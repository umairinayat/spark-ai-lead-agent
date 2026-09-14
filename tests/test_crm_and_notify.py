"""CRM adapter and notification tests."""

from __future__ import annotations

import httpx
import pytest

from app.agent.rules import classify_with_rules
from app.crm import build_crm_adapter, stage_for
from app.crm.gohighlevel import GoHighLevelAdapter
from app.crm.hubspot import HubSpotAdapter
from app.crm.mock import MockCrmAdapter
from app.models import LeadClassification, LeadIn, NotificationResult
from app.notify import SlackNotifier, build_blocks


@pytest.fixture
def classification(high_lead) -> LeadClassification:
    return classify_with_rules(high_lead)


# ------------------------------------------------------------------ factory
@pytest.mark.parametrize(
    "provider,expected",
    [("mock", MockCrmAdapter), ("gohighlevel", GoHighLevelAdapter),
     ("hubspot", HubSpotAdapter), ("nonsense-typo", MockCrmAdapter)],
)
def test_factory_selects_provider(settings, repo, provider, expected):
    settings.crm_provider = provider
    assert isinstance(build_crm_adapter(settings, repo), expected)


# --------------------------------------------------------------- mock CRM
@pytest.mark.asyncio
async def test_mock_crm_creates_then_updates(settings, repo, high_lead, classification):
    crm = MockCrmAdapter(repo)

    first = await crm.upsert_lead(high_lead, classification)
    assert first.created is True
    assert first.stage == "Hot - Contact Today"

    second = await crm.upsert_lead(high_lead, classification)
    assert second.created is False, "same email must update, not duplicate"
    assert second.contact_id == first.contact_id

    contacts = repo.list_mock_contacts()
    assert len(contacts) == 1
    assert contacts[0]["touch_count"] == 2
    assert "priority-high" in contacts[0]["tags"]
    assert contacts[0]["custom_fields"]["ai_priority"] == "High"


@pytest.mark.asyncio
async def test_mock_crm_stores_the_ai_analysis(settings, repo, high_lead, classification):
    crm = MockCrmAdapter(repo)
    await crm.upsert_lead(high_lead, classification)
    fields = repo.list_mock_contacts()[0]["custom_fields"]
    for key in ("ai_priority", "ai_reason", "ai_summary", "ai_intent",
                "ai_confidence", "ai_follow_up"):
        assert key in fields, f"{key} was not persisted to the CRM record"


# ------------------------------------------------------- stage assignment
@pytest.mark.parametrize(
    "priority,intent,expected",
    [
        ("High", "demo_request", "Hot - Contact Today"),
        ("Medium", "general_enquiry", "Warm - Nurture"),
        ("Low", "general_enquiry", "Cold - Archive"),
        ("High", "support_request", "Routed to Support"),
        ("High", "job_application", "Not a Sales Lead"),
        ("Medium", "spam", "Not a Sales Lead"),
    ],
)
def test_intent_overrides_priority_for_staging(priority, intent, expected):
    """A support ticket must never land in the sales pipeline, whatever the
    priority score says."""
    c = LeadClassification(
        priority=priority, reason="r", follow_up_message="f", intent=intent
    )
    assert stage_for(c) == expected


# ----------------------------------------------- unconfigured real CRMs
@pytest.mark.asyncio
async def test_ghl_without_credentials_degrades_loudly(settings, high_lead, classification):
    settings.crm_provider = "gohighlevel"
    result = await GoHighLevelAdapter(settings).upsert_lead(high_lead, classification)
    assert result.degraded is True
    assert "GHL_API_KEY" in result.error_detail
    assert result.contact_id == ""


@pytest.mark.asyncio
async def test_hubspot_without_credentials_degrades_loudly(settings, high_lead, classification):
    result = await HubSpotAdapter(settings).upsert_lead(high_lead, classification)
    assert result.degraded is True
    assert "HUBSPOT_TOKEN" in result.error_detail


@pytest.mark.asyncio
async def test_crm_network_failure_does_not_raise(settings, high_lead, classification, monkeypatch):
    settings.ghl_api_key = "test"
    settings.ghl_location_id = "loc123"
    settings.crm_max_retries = 0

    async def boom(self, *args, **kwargs):
        raise httpx.ConnectError("CRM unreachable")

    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    result = await GoHighLevelAdapter(settings).upsert_lead(high_lead, classification)
    assert result.degraded is True
    assert result.error_detail


def test_crm_result_has_no_top_level_error_key(settings, high_lead, classification):
    """Regression guard. n8n's HTTP Request node treats a top-level `error`
    key in a 200 response as a node failure and silently retries, which made
    the CRM node write the same contact three times per lead. The field is
    called `error_detail` for exactly this reason - do not rename it back."""
    from app.models import CrmContact

    payload = CrmContact(provider="mock", contact_id="x").model_dump()
    assert "error" not in payload
    assert "error_detail" in payload

    assert "error" not in NotificationResult(channel="none", sent=False).model_dump()


# ------------------------------------------------------------- notifying
@pytest.mark.asyncio
async def test_only_high_priority_pages_sales(settings, repo, high_lead, low_lead):
    notifier = SlackNotifier(settings, repo)

    high = classify_with_rules(high_lead)
    low = classify_with_rules(low_lead)

    assert notifier.should_notify(high) is True
    assert notifier.should_notify(low) is False

    result = await notifier.notify(low_lead, low, lead_id="lead_1")
    assert result.sent is False
    assert "not in" in result.skipped_reason


@pytest.mark.asyncio
async def test_spam_never_pages_sales(settings, repo):
    notifier = SlackNotifier(settings, repo)
    c = LeadClassification(
        priority="High", reason="r", follow_up_message="f", intent="spam"
    )
    assert notifier.should_notify(c) is False


@pytest.mark.asyncio
async def test_console_fallback_when_slack_is_unset(settings, repo, high_lead, capsys):
    notifier = SlackNotifier(settings, repo)
    c = classify_with_rules(high_lead)
    result = await notifier.notify(high_lead, c, lead_id="lead_1")

    assert result.sent is True
    assert result.channel == "console"
    assert "HIGH PRIORITY LEAD" in capsys.readouterr().out
    assert len(repo.list_notifications()) == 1, "notification must be auditable"


@pytest.mark.asyncio
async def test_slack_failure_is_recorded_not_raised(settings, repo, high_lead, monkeypatch):
    settings.slack_webhook_url = "https://hooks.slack.com/services/TEST"
    notifier = SlackNotifier(settings, repo)
    c = classify_with_rules(high_lead)

    async def boom(self, *args, **kwargs):
        raise httpx.ConnectError("slack unreachable")

    monkeypatch.setattr(httpx.AsyncClient, "post", boom)
    result = await notifier.notify(high_lead, c, lead_id="lead_1")

    assert result.sent is False
    assert result.error_detail
    stored = repo.list_notifications()
    assert len(stored) == 1 and stored[0]["sent"] is False


def test_slack_blocks_are_well_formed(high_lead):
    c = classify_with_rules(high_lead)
    payload = build_blocks(high_lead, c, None, "lead_1")

    assert payload["text"]
    assert payload["blocks"][0]["type"] == "header"
    assert len(payload["blocks"][0]["text"]["text"]) <= 150, "Slack truncates long headers"
    for block in payload["blocks"]:
        for field in block.get("fields", []):
            assert len(field["text"]) <= 2000
        text = block.get("text", {}).get("text")
        if text:
            assert len(text) <= 3000, "Slack rejects sections over 3000 chars"
