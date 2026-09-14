#!/usr/bin/env python3
"""A minimal OpenAI-compatible stub for exercising the real LLM code path.

The agent talks to OpenAI over plain HTTP, so pointing `OPENAI_BASE_URL` at
this server exercises the genuine article - tool-calling round trips, the
`response_format: json_schema` request, refusal handling, HTTP error handling
- with no API key and no spend. It is how the LLM path was verified for the
submission, and it is useful for local development.

    python scripts/fake_openai.py &
    OPENAI_API_KEY=sk-fake OPENAI_BASE_URL=http://127.0.0.1:8099/v1 \
      uvicorn app.main:app --port 8000

Modes, via the FAKE_MODE environment variable:
    normal   (default) one tool-calling round, then a structured answer
    error    always returns HTTP 500, to exercise retry + fallback
    refusal  returns a safety refusal
    garbage  returns content that is not valid JSON
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

MODE = os.environ.get("FAKE_MODE", "normal")
PORT = int(os.environ.get("FAKE_PORT", "8099"))


def _classification(company: str) -> dict:
    return {
        "priority": "High",
        "reason": (
            "The enquiry names a budget and a go-live deadline, and the tool "
            "results confirm a corporate domain matching the stated company."
        ),
        "follow_up_message": (
            "Hi there, thanks for laying out the timeline and budget so "
            "clearly - that makes this easy to scope. We have built this exact "
            "flow before and I would rather show you than describe it. Are you "
            "free for twenty minutes later this week?\n\nThe Spark AI team"
        ),
        "summary": f"{company or 'Unknown'} wants quote follow-up automation.",
        "intent": "demo_request",
        "confidence": 0.88,
        "signals": ["budget stated", "deadline stated", "corporate domain"],
        "recommended_owner": "sales_lead",
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the console quiet
        pass

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")

        if MODE == "error":
            return self._send(500, {"error": {"message": "simulated upstream failure"}})

        messages = request.get("messages", [])
        already_used_tools = any(m.get("role") == "tool" for m in messages)
        wants_structured = "response_format" in request

        if not wants_structured and not already_used_tools:
            # First turn: ask for evidence, exactly as the real model does.
            user_text = next(
                (m.get("content", "") for m in messages if m.get("role") == "user"), ""
            )
            return self._send(200, {"choices": [{"message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_signals",
                        "type": "function",
                        "function": {
                            "name": "extract_intent_signals",
                            "arguments": json.dumps({"message": user_text, "company": ""}),
                        },
                    },
                    {
                        "id": "call_domain",
                        "type": "function",
                        "function": {
                            "name": "analyse_email_domain",
                            "arguments": json.dumps({"email": _email_from(user_text), "company": ""}),
                        },
                    },
                ],
            }}]})

        if MODE == "refusal":
            return self._send(200, {"choices": [{
                "finish_reason": "stop",
                "message": {"refusal": "I cannot assist with that request."},
            }]})

        if MODE == "garbage":
            return self._send(200, {"choices": [{
                "finish_reason": "stop",
                "message": {"content": "Sure! Here is the answer: High priority."},
            }]})

        return self._send(200, {"choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps(_classification(""))},
        }]})


def _email_from(text: str) -> str:
    import re
    m = re.search(r"[\w\.\-\+]+@[\w\.\-]+\.\w+", text)
    return m.group(0) if m else "unknown@example.com"


if __name__ == "__main__":
    print(f"fake OpenAI on http://127.0.0.1:{PORT}/v1  (mode={MODE})", flush=True)
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
