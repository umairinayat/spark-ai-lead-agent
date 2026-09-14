"""Local CRM implementation backed by SQLite.

Not a no-op stub: it implements the same upsert-by-email semantics as a real
CRM (second submission from the same address updates rather than duplicates,
and increments a touch count), assigns pipeline stages from the same mapping
table, and stores the AI fields. That is what makes the demo honest - the
behaviour you see with `CRM_PROVIDER=mock` is the behaviour you get with
`CRM_PROVIDER=gohighlevel`, minus the network.
"""

from __future__ import annotations

from typing import Any

from ..db import LeadRepository
from ..models import CrmContact, LeadClassification, LeadIn, new_id, utc_now
from .base import (
    PIPELINE_NAME,
    CrmAdapter,
    ai_fields,
    stage_for,
    tags_for,
)


class MockCrmAdapter(CrmAdapter):
    name = "mock"

    def __init__(self, repo: LeadRepository) -> None:
        self.repo = repo

    async def upsert_lead(
        self, lead: LeadIn, classification: LeadClassification
    ) -> CrmContact:
        now = utc_now().isoformat()
        stage = stage_for(classification)
        contact = {
            "id": new_id("mock"),
            "email": str(lead.email),
            "name": lead.name,
            "company": lead.company,
            "phone": lead.phone,
            "tags": tags_for(classification),
            "pipeline": PIPELINE_NAME,
            "stage": stage,
            "custom_fields": ai_fields(classification),
            "created_at": now,
            "updated_at": now,
        }
        contact_id, created = self.repo.upsert_mock_contact(contact)
        return CrmContact(
            provider=self.name,
            contact_id=contact_id,
            created=created,
            opportunity_id=f"opp_{contact_id.split('_', 1)[-1]}",
            pipeline=PIPELINE_NAME,
            stage=stage,
            url=f"/api/v1/crm/contacts#{contact_id}",
            raw={"tags": contact["tags"], "custom_fields": contact["custom_fields"]},
        )

    async def health(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "ok": True,
            "detail": "local SQLite store; no external credentials required",
            "contacts": len(self.repo.list_mock_contacts(limit=1000)),
        }
