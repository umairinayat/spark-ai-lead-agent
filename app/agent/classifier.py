"""The lead qualification agent.

Control flow, which is the thing worth being able to draw on a whiteboard:

    build prompt
        |
        v
    [ tool-calling loop ]  <-- model asks for evidence, we execute it
        |   (max AGENT_MAX_TOOL_ROUNDS rounds)
        v
    final call with response_format=json_schema (strict)
        |
        +-- valid JSON matching the schema? --> validate with Pydantic --> done
        |
        +-- HTTP error / timeout / bad JSON --> retry (exponential backoff)
                                                   |
                                                   +-- retries exhausted
                                                          |
                                                          v
                                              deterministic rules engine
                                              (degraded=True, never fails)

The guarantee this module offers its caller: `run()` always returns an
AgentOutcome. It never raises. A lead is never lost because a model provider
had a bad afternoon.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Optional

import httpx
from pydantic import ValidationError

from ..config import Settings, get_settings
from ..models import AgentOutcome, LeadClassification, LeadIn, ToolCallRecord
from .prompts import FINAL_TURN_NUDGE, SYSTEM_PROMPT, build_user_prompt
from .rules import classify_with_rules
from .schema import RESPONSE_FORMAT
from .tools import TOOL_IMPLS, TOOL_SPECS

logger = logging.getLogger(__name__)

# Type of the optional CRM/lead-history lookup injected by the caller.
HistoryLookup = Callable[[str], Awaitable[dict[str, Any]]]


class LeadAgent:
    """Runs the qualification agent for a single lead."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        history_lookup: Optional[HistoryLookup] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.history_lookup = history_lookup

    # ------------------------------------------------------------ public API
    async def run(self, lead: LeadIn) -> AgentOutcome:
        started = time.perf_counter()

        if not self.settings.llm_enabled:
            history = await self._safe_history(lead.email)
            outcome = self._fallback(
                lead,
                history,
                reason="no_llm_configured",
                latency_ms=self._elapsed(started),
            )
            logger.info(
                "lead.classified",
                extra={"mode": "rules", "priority": outcome.classification.priority},
            )
            return outcome

        last_error = ""
        attempts = 0
        for attempt in range(1, self.settings.llm_max_retries + 2):
            attempts = attempt
            try:
                classification, tool_calls = await self._run_llm(lead)
                latency = self._elapsed(started)
                logger.info(
                    "lead.classified",
                    extra={
                        "mode": "llm",
                        "priority": classification.priority,
                        "attempts": attempt,
                        "tool_calls": len(tool_calls),
                        "latency_ms": latency,
                    },
                )
                return AgentOutcome(
                    classification=classification,
                    model=self.settings.openai_model,
                    degraded=False,
                    tool_calls=tool_calls,
                    latency_ms=latency,
                    attempts=attempt,
                )
            except Exception as exc:  # noqa: BLE001 - deliberate catch-all
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "agent.attempt_failed",
                    extra={"attempt": attempt, "error": last_error},
                )
                if attempt <= self.settings.llm_max_retries:
                    await asyncio.sleep(min(2 ** (attempt - 1), 4))

        history = await self._safe_history(lead.email)
        outcome = self._fallback(
            lead,
            history,
            reason=f"llm_failed_after_{attempts}_attempts: {last_error}"[:400],
            latency_ms=self._elapsed(started),
            attempts=attempts,
        )
        logger.error(
            "agent.degraded",
            extra={"reason": outcome.degraded_reason, "priority": outcome.classification.priority},
        )
        return outcome

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _elapsed(started: float) -> int:
        return int((time.perf_counter() - started) * 1000)

    def _fallback(
        self,
        lead: LeadIn,
        history: dict[str, Any],
        *,
        reason: str,
        latency_ms: int,
        attempts: int = 1,
    ) -> AgentOutcome:
        return AgentOutcome(
            classification=classify_with_rules(lead, history),
            model="deterministic-rules-v1",
            degraded=True,
            degraded_reason=reason,
            tool_calls=[],
            latency_ms=latency_ms,
            attempts=attempts,
        )

    async def _safe_history(self, email: str) -> dict[str, Any]:
        if not self.history_lookup:
            return {"times_seen": 0, "known": False}
        try:
            return await self.history_lookup(email)
        except Exception as exc:  # noqa: BLE001
            logger.warning("history_lookup.failed", extra={"error": str(exc)})
            return {"times_seen": 0, "known": False, "error": str(exc)}

    async def _dispatch_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute one tool call. Tool errors are returned, never raised:
        a broken tool should degrade the evidence, not kill the run."""
        try:
            if name == "lookup_existing_contact":
                return await self._safe_history(args.get("email", ""))
            impl = TOOL_IMPLS.get(name)
            if impl is None:
                return {"error": f"unknown tool '{name}'"}
            return impl(**args)
        except Exception as exc:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------- llm path
    async def _run_llm(self, lead: LeadIn) -> tuple[LeadClassification, list[ToolCallRecord]]:
        s = self.settings
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(lead)},
        ]
        tool_records: list[ToolCallRecord] = []

        headers = {
            "Authorization": f"Bearer {s.openai_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{s.openai_base_url.rstrip('/')}/chat/completions"

        async with httpx.AsyncClient(timeout=s.llm_timeout_seconds) as client:
            # ---- evidence gathering rounds -----------------------------
            for _round in range(s.agent_max_tool_rounds):
                payload = {
                    "model": s.openai_model,
                    "messages": messages,
                    "tools": TOOL_SPECS,
                    "tool_choice": "auto",
                    "temperature": s.llm_temperature,
                }
                data = await self._post(client, url, headers, payload)
                message = data["choices"][0]["message"]
                calls = message.get("tool_calls") or []
                if not calls:
                    messages.append({"role": "assistant", "content": message.get("content") or ""})
                    break

                messages.append(message)
                for call in calls:
                    fn = call["function"]
                    name = fn["name"]
                    try:
                        args = json.loads(fn.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    t0 = time.perf_counter()
                    result = await self._dispatch_tool(name, args)
                    tool_records.append(
                        ToolCallRecord(
                            name=name,
                            arguments=args,
                            result=result,
                            duration_ms=int((time.perf_counter() - t0) * 1000),
                        )
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(result, default=str)[:6000],
                        }
                    )

            # ---- final structured answer -------------------------------
            messages.append({"role": "user", "content": FINAL_TURN_NUDGE})
            final_payload = {
                "model": s.openai_model,
                "messages": messages,
                "response_format": RESPONSE_FORMAT,
                "temperature": s.llm_temperature,
            }
            data = await self._post(client, url, headers, final_payload)

        choice = data["choices"][0]
        msg = choice["message"]

        # Structured Outputs surfaces safety refusals in a dedicated field.
        if msg.get("refusal"):
            raise RuntimeError(f"model refused: {msg['refusal']}")
        if choice.get("finish_reason") == "length":
            raise RuntimeError("model output truncated (finish_reason=length)")

        content = msg.get("content") or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"model returned non-JSON content: {exc}") from exc

        try:
            classification = LeadClassification.model_validate(parsed)
        except ValidationError as exc:
            raise RuntimeError(f"schema validation failed: {exc.errors()[:3]}") from exc

        return classification, tool_records

    async def _post(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        response = await client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            body = response.text[:500]
            raise RuntimeError(f"OpenAI HTTP {response.status_code}: {body}")
        return response.json()
