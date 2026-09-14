"""CRM adapter factory."""

from __future__ import annotations

from ..config import Settings
from ..db import LeadRepository
from .base import (
    PIPELINE_NAME,
    STAGE_BY_INTENT,
    STAGE_BY_PRIORITY,
    CrmAdapter,
    stage_for,
    tags_for,
)
from .gohighlevel import GoHighLevelAdapter
from .hubspot import HubSpotAdapter
from .mock import MockCrmAdapter

__all__ = [
    "CrmAdapter", "MockCrmAdapter", "GoHighLevelAdapter", "HubSpotAdapter",
    "build_crm_adapter", "stage_for", "tags_for", "PIPELINE_NAME",
    "STAGE_BY_PRIORITY", "STAGE_BY_INTENT",
]


def build_crm_adapter(settings: Settings, repo: LeadRepository) -> CrmAdapter:
    """Pick the adapter named by CRM_PROVIDER.

    Falls back to the mock adapter for an unrecognised value rather than
    crashing at startup: an unqualified lead is worse than a mis-typed env var.
    """
    provider = (settings.crm_provider or "mock").lower()
    if provider == "gohighlevel":
        return GoHighLevelAdapter(settings)
    if provider == "hubspot":
        return HubSpotAdapter(settings)
    return MockCrmAdapter(repo)
