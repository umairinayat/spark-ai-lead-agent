"""JSON Schema handed to OpenAI Structured Outputs.

OpenAI's `strict: true` mode has two hard requirements that are easy to get
wrong and produce a 400 at runtime:

  * every key in `properties` must also appear in `required`
  * `additionalProperties` must be false on every object

Because of that we hand-write the schema instead of deriving it from Pydantic
(`model_json_schema()` emits `$defs`/`anyOf` for enums and optional fields,
which strict mode rejects). The schema below and `models.LeadClassification`
are kept in sync by `tests/test_agent.py::test_schema_matches_model`.
"""

from __future__ import annotations

from typing import Any

PRIORITY_VALUES = ["High", "Medium", "Low"]

INTENT_VALUES = [
    "demo_request",
    "pricing_enquiry",
    "partnership",
    "support_request",
    "job_application",
    "vendor_pitch",
    "spam",
    "general_enquiry",
]

OWNER_VALUES = ["sales_lead", "sdr", "support", "none"]

CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "priority",
        "reason",
        "follow_up_message",
        "summary",
        "intent",
        "confidence",
        "signals",
        "recommended_owner",
    ],
    "properties": {
        "priority": {
            "type": "string",
            "enum": PRIORITY_VALUES,
            "description": "Sales priority of this lead.",
        },
        "reason": {
            "type": "string",
            "description": (
                "One or two sentences justifying the priority. Must cite "
                "concrete evidence from the enquiry, not generic praise."
            ),
        },
        "follow_up_message": {
            "type": "string",
            "description": (
                "A short personalised follow-up email body addressed to the "
                "lead by first name. 60-120 words, no subject line, no "
                "placeholder brackets."
            ),
        },
        "summary": {
            "type": "string",
            "description": "One-line internal summary of what the lead wants.",
        },
        "intent": {
            "type": "string",
            "enum": INTENT_VALUES,
            "description": "The primary intent behind the enquiry.",
        },
        "confidence": {
            "type": "number",
            "description": "Confidence in the priority call, between 0 and 1.",
        },
        "signals": {
            "type": "array",
            "description": "Short evidence phrases that drove the decision.",
            "items": {"type": "string"},
        },
        "recommended_owner": {
            "type": "string",
            "enum": OWNER_VALUES,
            "description": "Which team should pick this lead up.",
        },
    },
}

RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "lead_classification",
        "strict": True,
        "schema": CLASSIFICATION_SCHEMA,
    },
}
