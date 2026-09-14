"""Pydantic contracts.

These models are the contract between n8n, the AI agent and the CRM layer.
Validation happens at three points:

  1. Inbound lead  -> LeadIn          (rejects junk before we spend an LLM call)
  2. LLM output    -> LeadClassification (rejects malformed model output)
  3. Outbound API  -> LeadResult      (what n8n and the browser receive)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Priority = Literal["High", "Medium", "Low"]

Intent = Literal[
    "demo_request",
    "pricing_enquiry",
    "partnership",
    "support_request",
    "job_application",
    "vendor_pitch",
    "spam",
    "general_enquiry",
]

RecommendedOwner = Literal["sales_lead", "sdr", "support", "none"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------- input
class LeadIn(BaseModel):
    """A raw inbound lead, exactly the four fields the brief specifies."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    company: str = Field(default="", max_length=160)
    message: str = Field(min_length=1, max_length=8000)

    # Optional metadata; useful for attribution, ignored by the agent.
    source: str = Field(default="web_form", max_length=60)
    phone: str = Field(default="", max_length=40)

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v

    def display_company(self) -> str:
        return self.company.strip() or "Unknown"


# ----------------------------------------------------------- agent artefacts
class ToolCallRecord(BaseModel):
    """One tool invocation inside the agent loop. Persisted for traceability."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    duration_ms: int = 0


class LeadClassification(BaseModel):
    """The structured output the LLM is constrained to produce."""

    model_config = ConfigDict(str_strip_whitespace=True)

    priority: Priority
    reason: str = Field(min_length=1, max_length=600)
    follow_up_message: str = Field(min_length=1, max_length=2000)
    summary: str = Field(default="", max_length=600)
    intent: Intent = "general_enquiry"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)
    recommended_owner: RecommendedOwner = "sdr"

    @field_validator("signals")
    @classmethod
    def _cap_signals(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()][:8]


class AgentOutcome(BaseModel):
    """Classification plus the metadata needed to audit how it was produced."""

    classification: LeadClassification
    model: str
    degraded: bool = False
    degraded_reason: str = ""
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    latency_ms: int = 0
    attempts: int = 1


# ----------------------------------------------------------------- crm layer
class CrmContact(BaseModel):
    """Result of a CRM write.

    Note the field name `error_detail` rather than the obvious `error`. n8n's
    HTTP Request node inspects a JSON response body for a top-level `error`
    key and treats its presence as a node failure - even on HTTP 200, and even
    when the value is an empty string. With `retryOnFail` enabled that made the
    CRM node fire three identical writes per lead while still reporting
    success, which was only visible in the API access log. Renaming the field
    fixes it at the source; see docs/phases/phase-04-n8n-workflow.md.
    """

    provider: str
    contact_id: str
    created: bool = False
    opportunity_id: str = ""
    pipeline: str = ""
    stage: str = ""
    url: str = ""
    degraded: bool = False
    error_detail: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class CrmUpsertRequest(BaseModel):
    """Payload n8n sends to the CRM proxy endpoint."""

    lead: LeadIn
    classification: LeadClassification
    lead_id: str = ""


# -------------------------------------------------------------- notification
class NotificationResult(BaseModel):
    # `error_detail`, not `error`, for the same reason as CrmContact above.
    channel: str
    sent: bool
    skipped_reason: str = ""
    error_detail: str = ""


class NotifyRequest(BaseModel):
    lead: LeadIn
    classification: LeadClassification
    lead_id: str = ""
    crm: Optional[CrmContact] = None
    force: bool = False


# -------------------------------------------------------------------- output
class ClassifyResponse(BaseModel):
    lead_id: str
    received_at: datetime
    lead: LeadIn
    classification: LeadClassification
    agent: dict[str, Any]


class LeadResult(BaseModel):
    """Full pipeline result: AI + CRM + notification."""

    lead_id: str
    received_at: datetime
    lead: LeadIn
    classification: LeadClassification
    agent: dict[str, Any]
    crm: Optional[CrmContact] = None
    notification: Optional[NotificationResult] = None
    warnings: list[str] = Field(default_factory=list)


class RecordRequest(BaseModel):
    """Final outcome written back by the n8n workflow."""

    lead: LeadIn
    classification: LeadClassification
    lead_id: str = ""
    crm: Optional[CrmContact] = None
    notification: Optional[NotificationResult] = None
    agent: dict[str, Any] = Field(default_factory=dict)
    source: str = "n8n"


class ErrorResponse(BaseModel):
    error: str
    detail: str = ""
    lead_id: str = ""
    request_id: str = ""
