"""HubSpot CRM v3 adapter.

Included because HubSpot has a free developer tier, which means this is the
one *real* CRM path a reviewer can exercise end to end without paying for
anything. It is the practical substitute for GoHighLevel.

Endpoint used:
    POST {base}/crm/v3/objects/contacts/batch/upsert
        body: {"inputs": [{"id": <email>, "idProperty": "email",
                           "properties": {...}}]}

Auth is a private app token: `Authorization: Bearer pat-...`.

Note on properties: `ai_priority`, `ai_reason` and friends are custom contact
properties. If they do not exist in the portal HubSpot rejects the write with
a 400 naming the unknown property. The adapter detects that specific failure
and retries with standard properties only, so a first run against a fresh
portal still creates the contact rather than failing outright. Run
`scripts/hubspot_setup.py` to create the custom properties properly.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from ..config import Settings
from ..models import CrmContact, LeadClassification, LeadIn
from .base import (
    PIPELINE_NAME,
    CrmAdapter,
    ai_fields,
    split_name,
    stage_for,
    tags_for,
)

logger = logging.getLogger(__name__)

STANDARD_PROPERTIES = {
    "email", "firstname", "lastname", "company", "phone", "website",
    "hs_lead_status", "lifecyclestage",
}

LEAD_STATUS_BY_PRIORITY = {
    "High": "OPEN_DEAL",
    "Medium": "IN_PROGRESS",
    "Low": "UNQUALIFIED",
}


class HubSpotAdapter(CrmAdapter):
    name = "hubspot"

    def __init__(self, settings: Settings) -> None:
        self.s = settings

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.s.hubspot_token}",
            "Content-Type": "application/json",
        }

    def _properties(self, lead: LeadIn, classification: LeadClassification) -> dict[str, Any]:
        first, last = split_name(lead.name)
        props: dict[str, Any] = {
            "email": str(lead.email),
            "firstname": first,
            "lastname": last,
            "hs_lead_status": LEAD_STATUS_BY_PRIORITY.get(classification.priority, "NEW"),
        }
        if lead.company:
            props["company"] = lead.company
        if lead.phone:
            props["phone"] = lead.phone
        props.update({k: str(v) for k, v in ai_fields(classification).items()})
        props["ai_pipeline_stage"] = stage_for(classification)
        props["ai_tags"] = ", ".join(tags_for(classification))
        return props

    async def _upsert(
        self, client: httpx.AsyncClient, props: dict[str, Any], email: str
    ) -> dict[str, Any]:
        url = f"{self.s.hubspot_base_url.rstrip('/')}/crm/v3/objects/contacts/batch/upsert"
        body = {"inputs": [{"id": email, "idProperty": "email", "properties": props}]}
        r = await client.post(url, headers=self._headers, json=body)
        if r.status_code >= 400:
            raise _HubSpotError(r.status_code, r.text[:500])
        return r.json()

    async def upsert_lead(
        self, lead: LeadIn, classification: LeadClassification
    ) -> CrmContact:
        stage = stage_for(classification)
        if not self.s.hubspot_token:
            return CrmContact(
                provider=self.name, contact_id="", degraded=True,
                error_detail="HubSpot not configured (missing: HUBSPOT_TOKEN)",
                stage=stage, pipeline=PIPELINE_NAME,
            )

        email = str(lead.email)
        props = self._properties(lead, classification)
        try:
            async with httpx.AsyncClient(timeout=self.s.crm_timeout_seconds) as client:
                try:
                    data = await self._upsert(client, props, email)
                except _HubSpotError as exc:
                    unknown = _unknown_properties(exc.body)
                    if exc.status == 400 and unknown:
                        logger.warning(
                            "hubspot.unknown_properties",
                            extra={"properties": sorted(unknown)},
                        )
                        reduced = {
                            k: v for k, v in props.items() if k in STANDARD_PROPERTIES
                        }
                        data = await self._upsert(client, reduced, email)
                    else:
                        raise

            results = data.get("results") or []
            contact_id = results[0].get("id", "") if results else ""
            created = bool(results and results[0].get("new"))
            portal_url = (
                f"https://app.hubspot.com/contacts/contacts/{contact_id}"
                if contact_id else ""
            )
            return CrmContact(
                provider=self.name, contact_id=contact_id, created=created,
                pipeline=PIPELINE_NAME, stage=stage, url=portal_url,
                raw={"lead_status": props.get("hs_lead_status")},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("hubspot.upsert_failed", extra={"error": str(exc)})
            return CrmContact(
                provider=self.name, contact_id="", degraded=True,
                error_detail=str(exc)[:400], stage=stage, pipeline=PIPELINE_NAME,
            )

    async def health(self) -> dict[str, Any]:
        if not self.s.hubspot_token:
            return {
                "provider": self.name, "ok": False,
                "detail": "not configured (missing: HUBSPOT_TOKEN)",
            }
        return {
            "provider": self.name, "ok": True,
            "detail": "private app token present",
        }


class _HubSpotError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HubSpot HTTP {status}: {body}")
        self.status = status
        self.body = body


def _unknown_properties(body: str) -> set[str]:
    """HubSpot reports unknown properties inside the 400 body."""
    if "PROPERTY_DOESNT_EXIST" not in body and "does not exist" not in body:
        return set()
    return set(re.findall(r"Property \"?([a-zA-Z0-9_]+)\"? does not exist", body))
