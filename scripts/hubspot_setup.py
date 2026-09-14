#!/usr/bin/env python3
"""Create the custom contact properties the HubSpot adapter writes.

HubSpot rejects writes to properties that do not exist in the portal, so run
this once after setting HUBSPOT_TOKEN:

    python scripts/hubspot_setup.py

Idempotent: properties that already exist are reported and skipped.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("HUBSPOT_BASE_URL", "https://api.hubapi.com")
TOKEN = os.environ.get("HUBSPOT_TOKEN", "")

PROPERTIES = [
    ("ai_priority", "AI Priority", "High / Medium / Low as judged by the agent"),
    ("ai_reason", "AI Reason", "Why the agent assigned this priority"),
    ("ai_summary", "AI Summary", "One-line summary of the enquiry"),
    ("ai_intent", "AI Intent", "Detected intent behind the enquiry"),
    ("ai_confidence", "AI Confidence", "Agent confidence, 0 to 1"),
    ("ai_signals", "AI Signals", "Evidence that drove the classification"),
    ("ai_follow_up", "AI Follow-up Draft", "Suggested personalised reply"),
    ("ai_recommended_owner", "AI Recommended Owner", "Team that should pick this up"),
    ("ai_pipeline_stage", "AI Pipeline Stage", "Stage assigned by the automation"),
    ("ai_tags", "AI Tags", "Tags applied by the automation"),
]


def create(name: str, label: str, description: str) -> str:
    payload = {
        "name": name,
        "label": label,
        "description": description,
        "groupName": "contactinformation",
        "type": "string",
        "fieldType": "textarea" if name in {"ai_reason", "ai_follow_up"} else "text",
    }
    req = urllib.request.Request(
        f"{BASE}/crm/v3/properties/contacts",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return "created" if r.status < 300 else f"unexpected status {r.status}"
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()[:300]
        if exc.code == 409 or "already exists" in body.lower():
            return "already exists"
        return f"FAILED ({exc.code}): {body}"
    except Exception as exc:  # noqa: BLE001
        return f"FAILED: {exc}"


def main() -> int:
    if not TOKEN:
        print("HUBSPOT_TOKEN is not set. Export it or put it in .env first.",
              file=sys.stderr)
        return 1
    failures = 0
    for name, label, description in PROPERTIES:
        result = create(name, label, description)
        print(f"  {name:<24} {result}")
        failures += result.startswith("FAILED")
    print()
    print("done." if not failures else f"{failures} property/properties failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
