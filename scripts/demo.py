#!/usr/bin/env python3
"""Submit every sample lead and print a results table.

Useful for the demo video: one command shows the whole classification range,
the stage routing and the notification decision.

    python scripts/demo.py                      # against the API directly
    python scripts/demo.py --via n8n            # through the n8n webhook
    python scripts/demo.py --api http://host:8000
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "scripts" / "sample_leads.json"

RESET = "\033[0m"
COLOURS = {"High": "\033[31m", "Medium": "\033[33m", "Low": "\033[90m"}


def post(url: str, payload: dict, api_key: str = "") -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except Exception:
            return exc.code, {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": str(exc)}


def flatten(body: dict) -> dict:
    """Both entry points return different envelopes; normalise them."""
    if "classification" in body:
        c = body["classification"]
        crm = body.get("crm") or {}
        note = body.get("notification") or {}
        return {
            "priority": c["priority"], "intent": c["intent"],
            "stage": crm.get("stage", ""), "contact": crm.get("contact_id", ""),
            "notified": bool(note.get("sent")),
            "degraded": bool((body.get("agent") or {}).get("degraded")),
            "reason": c["reason"],
        }
    crm = body.get("crm") or {}
    return {
        "priority": body.get("priority", "?"), "intent": body.get("intent", ""),
        "stage": body.get("pipeline_stage", ""), "contact": crm.get("contact_id", ""),
        "notified": bool(body.get("notified")),
        "degraded": bool(body.get("ai_degraded")),
        "reason": body.get("reason", ""),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--n8n", default="http://localhost:5678/webhook/lead-intake")
    ap.add_argument("--via", choices=["api", "n8n"], default="api")
    ap.add_argument("--api-key", default="")
    ap.add_argument("--no-colour", action="store_true")
    args = ap.parse_args()

    url = f"{args.api}/api/v1/leads" if args.via == "api" else args.n8n
    samples = json.loads(SAMPLES.read_text())

    try:
        with urllib.request.urlopen(f"{args.api}/health", timeout=10) as r:
            cfg = json.loads(r.read())["config"]
        print(f"model: {cfg['llm_model']}   crm: {cfg['crm_provider']}   "
              f"slack: {'live' if cfg['slack_enabled'] else 'console fallback'}")
    except Exception as exc:  # noqa: BLE001
        print(f"warning: could not reach {args.api}/health ({exc})", file=sys.stderr)

    print(f"submitting {len(samples)} sample leads via {args.via} -> {url}\n")
    header = f"{'LEAD':<16}{'EXPECT':<9}{'GOT':<9}{'INTENT':<18}{'STAGE':<23}{'SALES':<8}"
    print(header)
    print("-" * len(header))

    mismatches = 0
    for row in samples:
        lead = row["lead"]
        status, body = post(url, lead, args.api_key)
        if status not in (200, 201):
            print(f"{lead['name'][:15]:<16}HTTP {status}: {str(body)[:70]}")
            mismatches += 1
            continue

        r = flatten(body)
        acceptable = row.get("acceptable", [row["expected_priority"]])
        ok = r["priority"] in acceptable
        mismatches += (not ok)

        colour = "" if args.no_colour else COLOURS.get(r["priority"], "")
        end = "" if args.no_colour else RESET
        flag = "" if ok else "  <-- outside expected band"
        print(
            f"{lead['name'][:15]:<16}{row['expected_priority']:<9}"
            f"{colour}{r['priority']:<9}{end}{r['intent'][:17]:<18}"
            f"{r['stage'][:22]:<23}{('paged' if r['notified'] else '-'):<8}{flag}"
        )

    print()
    print(f"{len(samples) - mismatches}/{len(samples)} within the expected band")
    print(f"inspect: {args.api}/api/v1/leads   {args.api}/api/v1/crm/contacts   "
          f"{args.api}/api/v1/notifications")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
