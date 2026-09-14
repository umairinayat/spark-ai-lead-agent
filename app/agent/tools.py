"""Tools available to the agent.

These are what make this an *agent* rather than a single prompt: the model
decides which evidence it needs, calls these functions, reads the results, and
only then commits to a classification. Each tool is deterministic and cheap,
so the same lead produces the same evidence every time and the tools can be
unit tested without touching the network.

Adding a tool is a three-line change (a function here, an entry in
`TOOL_SPECS`, an entry in `TOOL_IMPLS`) - handy if you are asked to extend the
agent live.
"""

from __future__ import annotations

import re
from typing import Any, Callable

# Domains we treat as consumer mailboxes rather than company addresses.
FREEMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.uk", "hotmail.com",
    "outlook.com", "live.com", "msn.com", "aol.com", "icloud.com", "me.com",
    "proton.me", "protonmail.com", "gmx.com", "gmx.de", "mail.com",
    "yandex.com", "zoho.com", "rediffmail.com", "qq.com", "163.com",
}

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "trashmail.com", "yopmail.com", "sharklasers.com", "getnada.com",
    "throwawaymail.com", "temp-mail.org", "dispostable.com", "maildrop.cc",
}

ACADEMIC_SUFFIXES = (".edu", ".ac.uk", ".edu.au", ".ac.in", ".edu.pk", ".ac.jp")

# --- signal vocabularies -------------------------------------------------
# Each entry is (signal_name, regex). Kept as plain regex so a reviewer can
# read, audit and extend them without running the model.
SIGNAL_PATTERNS: list[tuple[str, str]] = [
    ("budget_mentioned",
     r"\b(budget|price|pricing|quote|cost|invest(?:ment)?|retainer|"
     r"\$\s?\d|usd\s?\d|aed\s?\d|eur\s?\d|£\s?\d|\d+\s?k\b|per month|monthly fee)\b"),
    ("timeline_urgency",
     r"\b(asap|urgent|immediately|this week|next week|this month|by \w+day|"
     r"deadline|q[1-4]\b|within \d+\s*(day|week|month)|end of (the )?(month|quarter|year)|"
     r"go[- ]live|launch(ing)? (in|by|on))\b"),
    # "partner" on its own used to live in this list and matched the verb in
    # "partner with us" - which appears in every agency cold pitch and let them
    # score as decision makers. Titles only, and only in title position.
    ("decision_maker",
     r"\b(founder|co-?founder|ceo|cto|coo|cfo|cmo|chief \w+ officer|"
     r"managing (director|partner)|business owner|head of \w+|vp of \w+|"
     r"vice president|director of \w+|board member|i own the|i run the)\b"),
    ("demo_or_call_request",
     r"\b(demo|walkthrough|trial|pilot|proof of concept|poc|call|meeting|"
     r"schedule|book a|hop on|catch up|discovery (call|session)|proposal|rfp|rfq)\b"),
    ("volume_or_scale",
     r"\b(\d[\d,\.]*\s*[- ]?\s*(users?|seats?|employees?|agents?|leads?|customers?|"
     r"contacts?|tickets?|calls?|records?|locations?|branches?|person|people|staff|"
     r"members?)|team of \d+|\d+\s*(people|staff)|"
     r"\d[\d,\.]*\s*(a|per|each)\s*(day|week|month|quarter|year))\b"),
    # A lead that describes its own operation or pain is engaging seriously,
    # even before it names a budget. Without this, every genuine early-stage
    # enquiry scored zero and fell into Low.
    ("describes_process",
     r"\b(we (run|handle|manage|receive|get|process|use|have|operate)|"
     r"our (team|process|workflow|customers?|clients?|agents?|enquir\w+|"
     r"inquir\w+|leads?|front desk|operations?|listings?|pipeline)|"
     r"at the moment we|currently we|right now we|manual\w*|by hand|"
     r"cannot keep up|can'?t keep up|struggl\w+ (with|to)|bottleneck)\b"),
    ("asks_question",
     r"(\?|\b(could|can|would) (you|someone|we)\b|\bhow (does|do|would|much|long)\b|"
     r"\bwhat (is|are|would|kind of)\b|\bwondering\b|\bwanted to (know|understand)\b|"
     r"\blike to understand\b)"),
    ("integration_scope",
     r"\b(integrat\w+|api|webhook|crm|gohighlevel|ghl|hubspot|salesforce|"
     r"zapier|make\.com|n8n|zendesk|shopify|stripe|whatsapp|twilio|sso|"
     r"migrat\w+|onboard\w+)\b"),
    ("job_seeking",
     r"\b(resume|cv|curriculum vitae|internship|intern position|job|vacancy|"
     r"opening|hiring|apply(ing)? for|candidate|portfolio|fresh graduate|"
     r"looking for (a )?(role|position|work|opportunit))\b"),
    # Deliberately narrow. An earlier version matched a bare "we are a ...",
    # which fired on "we are a 40-person brokerage" - a buyer describing
    # itself, not an agency pitching us. Every alternative below now requires
    # the sender to be selling something TO us.
    ("vendor_pitch",
     r"\b(we (offer|provide|deliver|build)\s+\w+(\s+\w+){0,3}\s+(services|solutions)\s+(to|for)\s+(you|your|companies|businesses|clients)|"
     r"our (agency|company|team|firm) (specialis|specializ)\w*\s+in|"
     r"we help (companies|businesses|brands|clients|you)|"
     r"(i|we) (would like to|wanted to|am writing to) (introduce|offer|propose)|"
     r"white[- ]label|guaranteed? (results|leads|rankings?|traffic)|"
     r"seo services|backlinks?|boost your (traffic|sales|ranking|revenue)|"
     r"increase your (traffic|sales|revenue|leads)|"
     r"(hire|outsource to) our (team|developers|agency)|"
     r"(dedicated|offshore) (developers?|team) (available|at)|"
     r"partner with us)\b"),
    ("spam_markers",
     r"\b(click here|limited time offer|act now|congratulations you|"
     r"crypto|bitcoin|forex|casino|viagra|make \$\d+|work from home|"
     r"earn money fast|100% free|risk[- ]free|unsubscribe)\b"),
    ("support_request",
     r"\b(not working|broken|bug|error|issue with my|can'?t log ?in|"
     r"password reset|refund|cancel my (subscription|account)|"
     r"existing (customer|account)|already a (customer|client))\b"),
]

COMPILED_SIGNALS = [(name, re.compile(rx, re.I)) for name, rx in SIGNAL_PATTERNS]

_COMPANY_STOPWORDS = {
    "inc", "llc", "ltd", "limited", "gmbh", "corp", "corporation", "co",
    "company", "group", "holdings", "technologies", "technology", "tech",
    "solutions", "systems", "labs", "studio", "agency", "consulting", "fz",
    "llp", "plc", "pvt", "private", "sa", "bv", "ag", "the", "and",
}


# ------------------------------------------------------------------ helpers
def _domain_of(email: str) -> str:
    return email.split("@")[-1].strip().lower() if "@" in email else ""


def _company_tokens(company: str) -> set[str]:
    tokens = re.split(r"[^a-z0-9]+", company.lower())
    return {t for t in tokens if t and t not in _COMPANY_STOPWORDS and len(t) > 2}


# -------------------------------------------------------------------- tools
def extract_intent_signals(message: str, company: str = "") -> dict[str, Any]:
    """Deterministic keyword/regex signal extraction over the enquiry text."""
    text = message or ""
    matched: dict[str, list[str]] = {}
    for name, pattern in COMPILED_SIGNALS:
        hits = pattern.findall(text)
        if hits:
            flat: list[str] = []
            for h in hits:
                value = " ".join(x for x in h if x) if isinstance(h, tuple) else h
                value = value.strip()
                if value and value.lower() not in [f.lower() for f in flat]:
                    flat.append(value)
            matched[name] = flat[:5]

    words = len(re.findall(r"\b\w+\b", text))
    positive = {
        "budget_mentioned", "timeline_urgency", "decision_maker",
        "demo_or_call_request", "volume_or_scale", "integration_scope",
        "describes_process", "asks_question",
    }
    negative = {"job_seeking", "vendor_pitch", "spam_markers", "support_request"}

    return {
        "signals_found": sorted(matched.keys()),
        "evidence": matched,
        "positive_signal_count": len(positive & matched.keys()),
        "negative_signal_count": len(negative & matched.keys()),
        "word_count": words,
        "is_very_short": words < 12,
        "mentions_company_name": bool(
            _company_tokens(company) & set(re.split(r"[^a-z0-9]+", text.lower()))
        ),
        "contains_url": bool(re.search(r"https?://|www\.", text, re.I)),
    }


def analyse_email_domain(email: str, company: str = "") -> dict[str, Any]:
    """Classify the sender's email domain and check it against the company."""
    domain = _domain_of(email)
    if not domain:
        return {"domain": "", "domain_type": "invalid", "matches_company": False}

    if domain in DISPOSABLE_DOMAINS:
        domain_type = "disposable"
    elif domain in FREEMAIL_DOMAINS:
        domain_type = "freemail"
    elif domain.endswith(ACADEMIC_SUFFIXES):
        domain_type = "academic"
    elif domain.endswith(".gov") or ".gov." in domain:
        domain_type = "government"
    else:
        domain_type = "corporate"

    root = domain.rsplit(".", 2)[0] if domain.count(".") > 1 else domain.split(".")[0]
    root_parts = {root, *re.split(r"[^a-z0-9]+", root)}
    tokens = _company_tokens(company)
    # Exact part match, or the company name concatenated into the domain
    # ("Velocity Commerce" -> velocitycommerce.io), which a set intersection
    # alone would miss.
    squashed = re.sub(r"[^a-z0-9]", "", company.lower())
    root_squashed = re.sub(r"[^a-z0-9]", "", root)
    matches = bool(tokens & root_parts) or (
        bool(squashed) and len(squashed) > 3
        and (squashed in root_squashed or root_squashed in squashed)
    )

    return {
        "domain": domain,
        "domain_type": domain_type,
        "matches_company": matches,
        "company_provided": bool(company.strip()),
        "note": {
            "corporate": "Company-controlled domain: mild positive signal.",
            "freemail": "Consumer mailbox: mild negative, not decisive.",
            "disposable": "Throwaway mailbox: strong negative signal.",
            "academic": "Academic address: often a student enquiry.",
            "government": "Government address: treat as corporate.",
            "invalid": "Malformed address.",
        }[domain_type],
    }


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "extract_intent_signals",
            "description": (
                "Run deterministic keyword and pattern analysis over the "
                "enquiry text. Returns which commercial buying signals and "
                "which disqualifying signals are present, with the matched "
                "phrases as evidence."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["message", "company"],
                "properties": {
                    "message": {"type": "string", "description": "The enquiry text."},
                    "company": {"type": "string", "description": "Stated company name, may be empty."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyse_email_domain",
            "description": (
                "Classify the sender's email domain as corporate, freemail, "
                "disposable, academic or government, and report whether it "
                "matches the company name they gave."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["email", "company"],
                "properties": {
                    "email": {"type": "string"},
                    "company": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_existing_contact",
            "description": (
                "Look this email up in the CRM and in our own lead store. "
                "Returns whether we have seen them before, how many times, "
                "and what priority they were given last time."
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "required": ["email"],
                "properties": {"email": {"type": "string"}},
            },
        },
    },
]

# Pure tools. `lookup_existing_contact` needs a repository handle, so it is
# bound per-request in classifier.py rather than registered here.
TOOL_IMPLS: dict[str, Callable[..., dict[str, Any]]] = {
    "extract_intent_signals": extract_intent_signals,
    "analyse_email_domain": analyse_email_domain,
}
