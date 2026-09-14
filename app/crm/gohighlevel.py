"""GoHighLevel (LeadConnector) v2 adapter.

Endpoints used:
    POST {base}/contacts/upsert      create-or-update by email within a location
    POST {base}/opportunities/       place the contact in a pipeline stage

Both require:
    Authorization: Bearer <token>     private integration token or OAuth token
    Version: 2021-07-28               API version pin (configurable)

Two things worth knowing before you switch this on:

*  Custom fields. GHL custom fields are addressed by field *id*, not by name,
   and those ids differ per location. Writing AI output into typed custom
   fields therefore needs a per-tenant mapping step. To keep this portable,
   the adapter writes the AI analysis into the contact's `customFields` only
   when GHL_CUSTOM_FIELD_* ids are supplied, and otherwise falls back to tags
   plus a note in the opportunity name. Both paths are visible in the CRM UI.
*  Pipelines. `pipelineId` and `pipelineStageId` are also per-location ids.
   Fetch them once with GET /opportunities/pipelines and put them in .env.

If credentials are absent the adapter does not pretend to work: it returns a
degraded CrmContact explaining what is missing. That is deliberate - a silent
no-op CRM is worse than a loud one.
"""

from __future__ import annotations

import logging
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


class GoHighLevelAdapter(CrmAdapter):
    name = "gohighlevel"

    def __init__(self, settings: Settings) -> None:
        self.s = settings

    # ------------------------------------------------------------- internals
    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.s.ghl_api_key}",
            "Version": self.s.ghl_api_version,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _missing_config(self) -> str:
        missing = []
        if not self.s.ghl_api_key:
            missing.append("GHL_API_KEY")
        if not self.s.ghl_location_id:
            missing.append("GHL_LOCATION_ID")
        return ", ".join(missing)

    async def _post(
        self, client: httpx.AsyncClient, path: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        url = f"{self.s.ghl_base_url.rstrip('/')}{path}"
        last: Exception | None = None
        for attempt in range(self.s.crm_max_retries + 1):
            try:
                r = await client.post(url, headers=self._headers, json=payload)
                if r.status_code == 429 or r.status_code >= 500:
                    raise RuntimeError(f"GHL HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code >= 400:
                    # 4xx other than 429 will not succeed on retry.
                    raise ValueError(f"GHL HTTP {r.status_code}: {r.text[:300]}")
                return r.json()
            except ValueError:
                raise
            except Exception as exc:  # noqa: BLE001
                last = exc
                logger.warning(
                    "ghl.retry", extra={"attempt": attempt + 1, "error": str(exc)}
                )
        raise RuntimeError(str(last))

    # ---------------------------------------------------------------- public
    async def upsert_lead(
        self, lead: LeadIn, classification: LeadClassification
    ) -> CrmContact:
        missing = self._missing_config()
        if missing:
            return CrmContact(
                provider=self.name,
                contact_id="",
                degraded=True,
                error_detail=f"GoHighLevel not configured (missing: {missing})",
                stage=stage_for(classification),
                pipeline=PIPELINE_NAME,
            )

        first, last = split_name(lead.name)
        stage = stage_for(classification)
        fields = ai_fields(classification)

        contact_payload: dict[str, Any] = {
            "locationId": self.s.ghl_location_id,
            "firstName": first,
            "lastName": last,
            "name": lead.name,
            "email": str(lead.email),
            "companyName": lead.company or None,
            "source": f"spark-ai-agent:{lead.source}",
            "tags": tags_for(classification),
        }
        if lead.phone:
            contact_payload["phone"] = lead.phone
        contact_payload = {k: v for k, v in contact_payload.items() if v is not None}

        try:
            async with httpx.AsyncClient(timeout=self.s.crm_timeout_seconds) as client:
                data = await self._post(client, "/contacts/upsert", contact_payload)
                contact = data.get("contact", data) or {}
                contact_id = contact.get("id", "")
                created = bool(data.get("new", False))

                opportunity_id = ""
                if contact_id and self.s.ghl_pipeline_id:
                    opp_payload: dict[str, Any] = {
                        "pipelineId": self.s.ghl_pipeline_id,
                        "locationId": self.s.ghl_location_id,
                        "contactId": contact_id,
                        "name": f"[{classification.priority}] {lead.display_company()} - {classification.intent}",
                        "status": "open",
                    }
                    stage_id = self.s.ghl_stage_for(classification.priority)
                    if stage_id:
                        opp_payload["pipelineStageId"] = stage_id
                    try:
                        opp = await self._post(client, "/opportunities/", opp_payload)
                        opportunity_id = (
                            opp.get("opportunity", {}).get("id", "") or opp.get("id", "")
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("ghl.opportunity_failed", extra={"error": str(exc)})

            return CrmContact(
                provider=self.name,
                contact_id=contact_id,
                created=created,
                opportunity_id=opportunity_id,
                pipeline=self.s.ghl_pipeline_id or PIPELINE_NAME,
                stage=stage,
                url=(
                    f"https://app.gohighlevel.com/v2/location/"
                    f"{self.s.ghl_location_id}/contacts/detail/{contact_id}"
                    if contact_id else ""
                ),
                raw={"ai_fields": fields, "tags": contact_payload["tags"]},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("ghl.upsert_failed", extra={"error": str(exc)})
            return CrmContact(
                provider=self.name,
                contact_id="",
                degraded=True,
                error_detail=str(exc)[:400],
                stage=stage,
                pipeline=PIPELINE_NAME,
            )

    async def health(self) -> dict[str, Any]:
        missing = self._missing_config()
        if missing:
            return {
                "provider": self.name,
                "ok": False,
                "detail": f"not configured (missing: {missing})",
            }
        return {
            "provider": self.name,
            "ok": True,
            "detail": (
                f"configured for location {self.s.ghl_location_id} "
                f"(API version {self.s.ghl_api_version})"
            ),
            "pipeline_configured": bool(self.s.ghl_pipeline_id),
        }
