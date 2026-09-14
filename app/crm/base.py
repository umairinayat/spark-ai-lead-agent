"""CRM abstraction.

The brief names GoHighLevel, but GHL is a paid product and an assessor cannot
be assumed to have a licence. Rather than stub the CRM out entirely, this
project defines one interface with three implementations:

    mock         - local SQLite, zero accounts, always available
    gohighlevel  - real GHL v2 REST calls (the brief's first choice)
    hubspot      - real HubSpot CRM v3 calls (free developer tier)

Selected at runtime by CRM_PROVIDER. The n8n workflow and the API surface do
not change when you switch: the pipeline/stage vocabulary below is the shared
language, and each adapter maps it onto whatever that CRM calls things.
"""

from __future__ import annotations

import abc
from typing import Any

from ..models import CrmContact, LeadClassification, LeadIn

PIPELINE_NAME = "Inbound Leads"

# Priority -> stage. This is the only place the routing vocabulary is defined.
STAGE_BY_PRIORITY: dict[str, str] = {
    "High": "Hot - Contact Today",
    "Medium": "Warm - Nurture",
    "Low": "Cold - Archive",
}

# Intent overrides: some leads should never enter the sales pipeline at all,
# regardless of score.
STAGE_BY_INTENT: dict[str, str] = {
    "support_request": "Routed to Support",
    "job_application": "Not a Sales Lead",
    "vendor_pitch": "Not a Sales Lead",
    "spam": "Not a Sales Lead",
}


def stage_for(classification: LeadClassification) -> str:
    return STAGE_BY_INTENT.get(
        classification.intent, STAGE_BY_PRIORITY.get(classification.priority, "Warm - Nurture")
    )


def tags_for(classification: LeadClassification) -> list[str]:
    tags = [
        "ai-qualified",
        f"priority-{classification.priority.lower()}",
        f"intent-{classification.intent.replace('_', '-')}",
    ]
    if classification.confidence < 0.4:
        tags.append("low-confidence")
    return tags


def ai_fields(classification: LeadClassification) -> dict[str, Any]:
    """The AI output we want stored against the contact in any CRM."""
    return {
        "ai_priority": classification.priority,
        "ai_reason": classification.reason,
        "ai_summary": classification.summary,
        "ai_intent": classification.intent,
        "ai_confidence": round(classification.confidence, 2),
        "ai_signals": ", ".join(classification.signals),
        "ai_follow_up": classification.follow_up_message,
        "ai_recommended_owner": classification.recommended_owner,
    }


def split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


class CrmAdapter(abc.ABC):
    """Every adapter creates-or-updates a contact and returns a CrmContact.

    Adapters must not raise on remote failure. They return a CrmContact with
    `degraded=True` and an `error` string, so a CRM outage downgrades the
    result instead of losing the lead. The workflow decides what to do next.
    """

    name: str = "base"

    @abc.abstractmethod
    async def upsert_lead(
        self, lead: LeadIn, classification: LeadClassification
    ) -> CrmContact:
        ...

    @abc.abstractmethod
    async def health(self) -> dict[str, Any]:
        ...
