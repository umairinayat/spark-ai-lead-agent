from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.db import LeadRepository  # noqa: E402
from app.models import LeadIn  # noqa: E402

SAMPLES_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "sample_leads.json"


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings with no external credentials - the default review posture."""
    return Settings(
        database_path=str(tmp_path / "test.db"),
        openai_api_key="",
        crm_provider="mock",
        slack_webhook_url="",
        api_key="",
    )


@pytest.fixture
def repo(settings) -> LeadRepository:
    return LeadRepository(settings.database_path)


@pytest.fixture
def samples() -> list[dict]:
    return json.loads(SAMPLES_PATH.read_text())


@pytest.fixture
def high_lead() -> LeadIn:
    return LeadIn(
        name="Sara Malik",
        email="sara@northwind-logistics.com",
        company="Northwind Logistics",
        message=(
            "We are a 40-person freight brokerage and we need to automate quote "
            "follow-ups out of our GoHighLevel CRM. Budget is around $15k and we "
            "want to go live before the end of Q4. Can we book a call this week?"
        ),
    )


@pytest.fixture
def low_lead() -> LeadIn:
    return LeadIn(
        name="Ahmed Hassan",
        email="ahmed.hassan.dev@gmail.com",
        company="",
        message=(
            "Respected Sir/Madam, I am a fresh graduate applying for any available "
            "internship position. Please find my CV attached."
        ),
    )
