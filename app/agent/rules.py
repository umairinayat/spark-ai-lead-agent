"""Deterministic fallback classifier.

This exists for two reasons, and both matter:

  1. Error handling. If OpenAI is down, rate limits us, or returns something
     that fails schema validation twice, the pipeline must still produce a
     usable answer rather than dropping a sales lead on the floor. A lead lost
     to a 429 is a lost deal.
  2. Reviewability. The whole system can be demonstrated and CI-tested with no
     API key and no spend, because this engine reuses the exact same tool
     outputs the LLM sees.

It scores the signals produced by `tools.extract_intent_signals` and
`tools.analyse_email_domain`, so the LLM path and the fallback path reason
over identical evidence. Results are marked `degraded=True` so nobody mistakes
a fallback for a model judgement.
"""

from __future__ import annotations

from ..models import LeadClassification, LeadIn
from .tools import analyse_email_domain, extract_intent_signals

POSITIVE_WEIGHTS = {
    # Hard commercial evidence.
    "budget_mentioned": 3,
    "timeline_urgency": 2,
    "decision_maker": 2,
    "demo_or_call_request": 2,
    "volume_or_scale": 2,
    "integration_scope": 1,
    # Softer engagement evidence: enough to separate a real early-stage
    # enquiry from "info pls", not enough on its own to reach High.
    "describes_process": 1,
    "asks_question": 1,
}

NEGATIVE_WEIGHTS = {
    "job_seeking": -6,
    "vendor_pitch": -5,
    "spam_markers": -8,
    "support_request": -4,
}

DOMAIN_WEIGHTS = {
    "corporate": 1,
    "government": 1,
    "freemail": -1,
    "academic": -2,
    "disposable": -5,
    "invalid": -3,
}

HIGH_THRESHOLD = 5
MEDIUM_THRESHOLD = 2

# Decisive on their own. No amount of buying language makes a CV a sales lead,
# and the support_request pattern only fires on explicit failure language
# ("broken", "error", "can't log in", "refund"), which is a ticket, not a deal.
HARD_DISQUALIFIERS = {"job_seeking", "spam_markers", "support_request"}
# Usually disqualifying, but overridden by strong positive evidence - an agency
# cold-pitch phrase can appear in a genuine enquiry.
SOFT_DISQUALIFIERS = {"vendor_pitch"}
SOFT_DISQUALIFIERS_INTENTS = {"vendor_pitch"}

# Only hard commercial evidence can override a soft disqualifier. Engagement
# signals (asking a question, describing a process) are things a cold pitch
# does too, so they must not be able to rescue one.
HARD_POSITIVES = {
    "budget_mentioned", "timeline_urgency", "decision_maker",
    "demo_or_call_request", "volume_or_scale",
}

_SIGNAL_LABELS = {
    "budget_mentioned": "budget or pricing discussed",
    "timeline_urgency": "explicit timeline",
    "decision_maker": "decision-maker language",
    "demo_or_call_request": "asked for a demo or call",
    "volume_or_scale": "volume or team size given",
    "integration_scope": "named specific systems to integrate",
    "describes_process": "described their own operation or pain point",
    "asks_question": "asked a direct question",
    "job_seeking": "job application",
    "vendor_pitch": "vendor selling to us",
    "spam_markers": "spam markers",
    "support_request": "existing-customer support request",
}


def _first_name(full_name: str) -> str:
    part = (full_name or "").strip().split()
    return part[0] if part else "there"


def _intent_for(signals: set[str], score: int) -> str:
    if "spam_markers" in signals:
        return "spam"
    if "job_seeking" in signals:
        return "job_application"
    if "vendor_pitch" in signals:
        return "vendor_pitch"
    if "support_request" in signals:
        return "support_request"
    if "demo_or_call_request" in signals:
        return "demo_request"
    if "budget_mentioned" in signals:
        return "pricing_enquiry"
    return "general_enquiry"


def _follow_up(lead: LeadIn, priority: str, intent: str, signals: set[str]) -> str:
    first = _first_name(lead.name)
    company = lead.display_company()
    company_clause = f" at {company}" if company != "Unknown" else ""

    if intent == "spam":
        return (
            f"Hi {first}, thanks for the note. This does not look like a fit "
            "for what we do, so we will not be taking it further.\n\n"
            "The Spark AI team"
        )
    if intent == "job_application":
        return (
            f"Hi {first}, thank you for your interest in joining us. We have "
            "passed your details to our hiring team and they will reach out "
            "directly if a suitable role opens up. We are not able to review "
            "applications through this channel, so please do keep an eye on "
            "our careers page for live vacancies.\n\nThe Spark AI team"
        )
    if intent == "support_request":
        return (
            f"Hi {first}, thanks for flagging this. Support questions are "
            "handled by our customer team rather than sales, so we have "
            "routed your message to them and someone will follow up shortly "
            "with next steps.\n\nThe Spark AI team"
        )
    if priority == "High":
        detail = "the timeline you mentioned" if "timeline_urgency" in signals else "what you described"
        return (
            f"Hi {first}, thanks for reaching out about your automation "
            f"project{company_clause}. Based on {detail}, this is something "
            "we have built before and I would rather show you than describe "
            "it. Are you free for a 20 minute call in the next couple of "
            "days? I will walk you through a comparable build and give you a "
            "straight answer on scope and cost before you commit to "
            "anything.\n\nThe Spark AI team"
        )
    if priority == "Medium":
        return (
            f"Hi {first}, thanks for getting in touch{company_clause}. Happy "
            "to help you work out whether this is worth doing at all before "
            "we talk about building anything. If you can tell me a bit about "
            "the process you are trying to automate and roughly what volume "
            "it runs at, I will come back with an honest view on the "
            "approach and the effort involved.\n\nThe Spark AI team"
        )
    return (
        f"Hi {first}, thanks for your message. We have logged it and will be "
        "in touch if there is a fit on our side. If your enquiry is time "
        "sensitive, replying with a bit more detail about what you need will "
        "help us point you in the right direction.\n\nThe Spark AI team"
    )


def classify_with_rules(lead: LeadIn, history: dict | None = None) -> LeadClassification:
    """Score a lead using only deterministic signals."""
    signals_result = extract_intent_signals(lead.message, lead.company)
    domain_result = analyse_email_domain(lead.email, lead.company)
    found = set(signals_result["signals_found"])

    score = 0
    for name in found:
        score += POSITIVE_WEIGHTS.get(name, 0) + NEGATIVE_WEIGHTS.get(name, 0)
    score += DOMAIN_WEIGHTS.get(domain_result["domain_type"], 0)

    if domain_result["matches_company"]:
        score += 1
    if signals_result["is_very_short"]:
        score -= 2
    if not lead.company.strip():
        score -= 1
    if history and history.get("times_seen", 0) > 0:
        score += 2

    intent = _intent_for(found, score)

    # Not all negative signals are equally decisive. A job application or
    # obvious spam is Low whatever else the message contains. A vendor pitch or
    # a support question is usually Low, but a single loose keyword match
    # should not bury a lead that otherwise shows real buying intent - so those
    # only force Low when the positive evidence is thin.
    if found & HARD_DISQUALIFIERS:
        priority = "Low"
    elif (found & SOFT_DISQUALIFIERS) and len(found & HARD_POSITIVES) < 2:
        priority = "Low"
    elif score >= HIGH_THRESHOLD:
        priority = "High"
    elif score >= MEDIUM_THRESHOLD:
        priority = "Medium"
    else:
        priority = "Low"

    # If a soft negative was overridden, the intent should not still claim the
    # lead is a vendor pitch or a support ticket.
    if priority != "Low" and intent in SOFT_DISQUALIFIERS_INTENTS:
        intent = _intent_for(found - SOFT_DISQUALIFIERS, score)

    positives = sorted(found & POSITIVE_WEIGHTS.keys())
    negatives = sorted(found & NEGATIVE_WEIGHTS.keys())
    readable = [_SIGNAL_LABELS.get(s, s) for s in positives + negatives]

    if negatives and priority == "Low":
        reason = (
            f"Scored {score}. Disqualifying signal detected: "
            f"{', '.join(_SIGNAL_LABELS.get(n, n) for n in negatives)}. "
            "Routed away from the sales pipeline."
        )
    elif negatives:
        reason = (
            f"Scored {score} on deterministic signals: "
            f"{', '.join(_SIGNAL_LABELS.get(p, p) for p in positives)}. "
            f"A weaker negative signal ({', '.join(_SIGNAL_LABELS.get(n, n) for n in negatives)}) "
            "was outweighed by the buying evidence."
        )
    elif positives:
        reason = (
            f"Scored {score} on deterministic signals: {', '.join(readable)}; "
            f"sender domain is {domain_result['domain_type']}."
        )
    else:
        reason = (
            f"Scored {score}. No concrete buying signals found in a "
            f"{signals_result['word_count']}-word enquiry from a "
            f"{domain_result['domain_type']} address."
        )

    if history and history.get("times_seen", 0) > 0:
        reason += f" Previously contacted us {history['times_seen']} time(s)."

    confidence = 0.55 if priority == "Medium" else 0.7
    if negatives and priority == "Low":
        confidence = 0.8
    elif negatives:
        confidence = 0.5  # conflicting evidence, say so rather than bluff

    owner = {"High": "sales_lead", "Medium": "sdr", "Low": "none"}[priority]
    if intent == "support_request":
        owner = "support"

    return LeadClassification(
        priority=priority,
        reason=reason,
        follow_up_message=_follow_up(lead, priority, intent, found),
        summary=(
            f"{lead.display_company()} enquiry classified as {priority} by the "
            f"deterministic fallback engine (score {score})."
        ),
        intent=intent,
        confidence=confidence,
        signals=readable[:8] or ["no signals detected"],
        recommended_owner=owner,
    )
