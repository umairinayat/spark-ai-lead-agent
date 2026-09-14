"""Sales-team notification.

Posts a Slack Block Kit message to an Incoming Webhook when a lead clears the
notify threshold (High by default, configurable via NOTIFY_ON_PRIORITIES).

If SLACK_WEBHOOK_URL is unset the notifier does not fail - it logs the same
payload and records it in the `notifications` table, so the demo still shows
the notification decision being made and the evidence is inspectable at
GET /api/v1/notifications. Slack Incoming Webhooks are free, so the live path
is the expected one; the fallback exists so CI and reviewers are not blocked.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from ..config import Settings
from ..db import LeadRepository
from ..models import (
    CrmContact,
    LeadClassification,
    LeadIn,
    NotificationResult,
    new_id,
    utc_now,
)

logger = logging.getLogger(__name__)

PRIORITY_EMOJI = {"High": ":fire:", "Medium": ":eyes:", "Low": ":snowflake:"}


def build_blocks(
    lead: LeadIn,
    classification: LeadClassification,
    crm: Optional[CrmContact] = None,
    lead_id: str = "",
) -> dict[str, Any]:
    emoji = PRIORITY_EMOJI.get(classification.priority, ":inbox_tray:")
    header = f"{emoji} {classification.priority} priority lead - {lead.display_company()}"

    fields = [
        {"type": "mrkdwn", "text": f"*Name*\n{lead.name}"},
        {"type": "mrkdwn", "text": f"*Email*\n{lead.email}"},
        {"type": "mrkdwn", "text": f"*Company*\n{lead.display_company()}"},
        {"type": "mrkdwn", "text": f"*Intent*\n{classification.intent.replace('_', ' ')}"},
        {"type": "mrkdwn", "text": f"*Confidence*\n{classification.confidence:.0%}"},
        {"type": "mrkdwn", "text": f"*Owner*\n{classification.recommended_owner.replace('_', ' ')}"},
    ]

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": header[:150], "emoji": True}},
        {"type": "section", "fields": fields},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Why this priority*\n{classification.reason}"[:2900]},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Their message*\n>{_quote(lead.message)}"[:2900],
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Suggested reply*\n```{classification.follow_up_message[:1500]}```",
            },
        },
    ]

    context_bits = []
    if classification.signals:
        context_bits.append("Signals: " + ", ".join(classification.signals[:6]))
    if crm and crm.contact_id:
        crm_line = f"CRM: {crm.provider} `{crm.contact_id}` -> {crm.stage}"
        if crm.url:
            crm_line = f"CRM: <{crm.url}|{crm.provider} contact> -> {crm.stage}"
        context_bits.append(crm_line)
    elif crm and crm.degraded:
        context_bits.append(f":warning: CRM write degraded: {crm.error_detail[:120]}")
    if lead_id:
        context_bits.append(f"Lead `{lead_id}`")

    if context_bits:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": " | ".join(context_bits)[:2900]}]}
        )

    return {"text": header, "blocks": blocks}


def _quote(message: str, limit: int = 600) -> str:
    text = message.strip().replace("\n", "\n>")
    return text[:limit] + ("..." if len(text) > limit else "")


class SlackNotifier:
    def __init__(self, settings: Settings, repo: LeadRepository) -> None:
        self.s = settings
        self.repo = repo

    def should_notify(self, classification: LeadClassification) -> bool:
        if classification.intent == "spam":
            return False
        return classification.priority in self.s.notify_priorities

    async def notify(
        self,
        lead: LeadIn,
        classification: LeadClassification,
        crm: Optional[CrmContact] = None,
        lead_id: str = "",
        force: bool = False,
    ) -> NotificationResult:
        if not force and not self.should_notify(classification):
            return NotificationResult(
                channel="none",
                sent=False,
                skipped_reason=(
                    f"priority {classification.priority} is not in "
                    f"{sorted(self.s.notify_priorities)}"
                    if classification.intent != "spam"
                    else "classified as spam"
                ),
            )

        payload = build_blocks(lead, classification, crm, lead_id)
        record = {
            "id": new_id("ntf"),
            "lead_id": lead_id,
            "created_at": utc_now().isoformat(),
            "payload": payload,
        }

        if not self.s.slack_enabled:
            logger.info(
                "notify.console_fallback",
                extra={"priority": classification.priority, "email": str(lead.email)},
            )
            print(_console_card(lead, classification, crm), flush=True)
            record.update({"channel": "console", "sent": True, "error": ""})
            self.repo.save_notification(record)
            return NotificationResult(
                channel="console",
                sent=True,
                skipped_reason="SLACK_WEBHOOK_URL not set; printed and stored instead",
            )

        try:
            async with httpx.AsyncClient(timeout=self.s.slack_timeout_seconds) as client:
                r = await client.post(self.s.slack_webhook_url, json=payload)
                if r.status_code >= 400:
                    raise RuntimeError(f"Slack HTTP {r.status_code}: {r.text[:200]}")
            record.update({"channel": "slack", "sent": True, "error": ""})
            self.repo.save_notification(record)
            return NotificationResult(channel="slack", sent=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("notify.slack_failed", extra={"error": str(exc)})
            print(_console_card(lead, classification, crm), flush=True)
            record.update({"channel": "slack", "sent": False, "error": str(exc)[:300]})
            self.repo.save_notification(record)
            return NotificationResult(
                channel="slack", sent=False, error_detail=str(exc)[:300],
                skipped_reason="Slack delivery failed; printed to stdout instead",
            )


def _console_card(
    lead: LeadIn, classification: LeadClassification, crm: Optional[CrmContact]
) -> str:
    line = "=" * 64
    crm_line = (
        f"CRM        : {crm.provider} {crm.contact_id} -> {crm.stage}"
        if crm and crm.contact_id
        else "CRM        : (not written)"
    )
    return "\n".join([
        line,
        f" {classification.priority.upper()} PRIORITY LEAD - notify sales",
        line,
        f"Name       : {lead.name}",
        f"Email      : {lead.email}",
        f"Company    : {lead.display_company()}",
        f"Intent     : {classification.intent}  (confidence {classification.confidence:.0%})",
        f"Reason     : {classification.reason}",
        crm_line,
        "-" * 64,
        "Suggested follow-up:",
        classification.follow_up_message,
        line,
    ])
