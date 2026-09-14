"""Pipeline orchestration.

This module is the single place where "qualify -> CRM -> notify -> persist"
lives. Both entry points use it:

  * the n8n workflow, which calls the steps individually so each one is a
    visible node with its own retry and error branch
  * POST /api/v1/leads, which runs the whole chain in one call (used by the
    browser form and by anyone who does not want to stand up n8n)

Keeping the logic here rather than duplicating it in the workflow means the
two paths cannot drift apart.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .agent import LeadAgent
from .config import Settings
from .crm import CrmAdapter, stage_for
from .db import LeadRepository
from .models import (
    AgentOutcome,
    CrmContact,
    LeadClassification,
    LeadIn,
    LeadResult,
    NotificationResult,
    new_id,
    utc_now,
)
from .notify import SlackNotifier

logger = logging.getLogger(__name__)


class LeadPipeline:
    def __init__(
        self,
        settings: Settings,
        repo: LeadRepository,
        crm: CrmAdapter,
        notifier: SlackNotifier,
    ) -> None:
        self.settings = settings
        self.repo = repo
        self.crm = crm
        self.notifier = notifier

    # ------------------------------------------------------------------ step 1
    def build_agent(self) -> LeadAgent:
        async def history_lookup(email: str) -> dict[str, Any]:
            return self.repo.history_for_email(email)

        return LeadAgent(self.settings, history_lookup=history_lookup)

    async def classify(self, lead: LeadIn) -> AgentOutcome:
        return await self.build_agent().run(lead)

    # ------------------------------------------------------------------ step 2
    async def sync_crm(
        self, lead: LeadIn, classification: LeadClassification
    ) -> CrmContact:
        return await self.crm.upsert_lead(lead, classification)

    # ------------------------------------------------------------------ step 3
    async def notify(
        self,
        lead: LeadIn,
        classification: LeadClassification,
        crm: Optional[CrmContact] = None,
        lead_id: str = "",
        force: bool = False,
    ) -> NotificationResult:
        return await self.notifier.notify(lead, classification, crm, lead_id, force)

    # ------------------------------------------------------------------ step 4
    def persist(
        self,
        lead_id: str,
        lead: LeadIn,
        outcome: AgentOutcome | dict[str, Any],
        classification: LeadClassification,
        crm: Optional[CrmContact],
        notification: Optional[NotificationResult],
        pipeline_source: str,
    ) -> None:
        import json

        agent_meta: dict[str, Any]
        tool_calls: list[Any]
        if isinstance(outcome, AgentOutcome):
            agent_meta = {
                "model": outcome.model,
                "degraded": outcome.degraded,
                "degraded_reason": outcome.degraded_reason,
                "attempts": outcome.attempts,
                "latency_ms": outcome.latency_ms,
            }
            tool_calls = [tc.model_dump() for tc in outcome.tool_calls]
        else:
            agent_meta = dict(outcome or {})
            tool_calls = agent_meta.get("tool_calls", []) or []

        self.repo.save_lead({
            "id": lead_id,
            "received_at": utc_now().isoformat(),
            "name": lead.name,
            "email": str(lead.email),
            "company": lead.company,
            "message": lead.message,
            "source": lead.source,
            "priority": classification.priority,
            "intent": classification.intent,
            "confidence": classification.confidence,
            "reason": classification.reason,
            "summary": classification.summary,
            "follow_up_message": classification.follow_up_message,
            "signals": json.dumps(classification.signals),
            "recommended_owner": classification.recommended_owner,
            "crm_provider": crm.provider if crm else "",
            "crm_contact_id": crm.contact_id if crm else "",
            "crm_opportunity_id": crm.opportunity_id if crm else "",
            "crm_stage": crm.stage if crm else stage_for(classification),
            "notified": int(bool(notification and notification.sent)),
            "degraded": int(bool(agent_meta.get("degraded"))),
            "pipeline_source": pipeline_source,
        })

        self.repo.save_agent_run({
            "id": new_id("run"),
            "lead_id": lead_id,
            "created_at": utc_now().isoformat(),
            "model": agent_meta.get("model", ""),
            "degraded": int(bool(agent_meta.get("degraded"))),
            "degraded_reason": agent_meta.get("degraded_reason", ""),
            "attempts": agent_meta.get("attempts", 1),
            "latency_ms": agent_meta.get("latency_ms", 0),
            "tool_calls": tool_calls,
        })

    # -------------------------------------------------------------- full run
    async def run(self, lead: LeadIn, pipeline_source: str = "api") -> LeadResult:
        """Qualify, sync, notify and persist. Never raises on integration
        failure - each step degrades independently and reports why."""
        lead_id = new_id("lead")
        warnings: list[str] = []

        outcome = await self.classify(lead)
        if outcome.degraded:
            warnings.append(f"AI degraded: {outcome.degraded_reason}")

        crm = await self.sync_crm(lead, outcome.classification)
        if crm.degraded:
            warnings.append(f"CRM degraded: {crm.error_detail}")

        notification = await self.notify(
            lead, outcome.classification, crm, lead_id
        )
        if notification.error_detail:
            warnings.append(f"Notification failed: {notification.error_detail}")

        try:
            self.persist(
                lead_id, lead, outcome, outcome.classification, crm,
                notification, pipeline_source,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("persist.failed", extra={"error": str(exc)})
            warnings.append(f"Persistence failed: {exc}")

        return LeadResult(
            lead_id=lead_id,
            received_at=utc_now(),
            lead=lead,
            classification=outcome.classification,
            agent={
                "model": outcome.model,
                "degraded": outcome.degraded,
                "degraded_reason": outcome.degraded_reason,
                "attempts": outcome.attempts,
                "latency_ms": outcome.latency_ms,
                "tool_calls": [tc.model_dump() for tc in outcome.tool_calls],
            },
            crm=crm,
            notification=notification,
            warnings=warnings,
        )
