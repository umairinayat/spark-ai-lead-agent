#!/usr/bin/env python3
"""Generate the n8n workflow export.

The workflow is authored here rather than hand-edited as JSON for three
reasons: node ids stay stable across regenerations, the embedded JavaScript
stays readable (no escaped newlines), and the node/typeVersion table sits in
one place where it can be checked against a real n8n install.

Run:  python scripts/build_workflow.py
Out:  n8n/workflow.lead-qualification.json

Validated against n8n 2.35.7 via `n8n import:workflow`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "n8n" / "workflow.lead-qualification.json"

# Node type versions verified against n8n 2.35.7. Each is at or below the
# installed default, so the workflow imports on older releases too.
TV = {
    "webhook": 2,
    "code": 2,
    "httpRequest": 4.2,
    "if": 2.2,
    "switch": 3.2,
    "respondToWebhook": 1.1,
}

NS = uuid.UUID("6f1c6b9e-1f7a-4a2a-9a3e-4f9c2b7d1e00")


def nid(name: str) -> str:
    """Deterministic node id, so regenerating produces no spurious diff."""
    return str(uuid.uuid5(NS, name))


def cond_id(name: str) -> str:
    return uuid.uuid5(NS, "cond:" + name).hex[:16]


# --------------------------------------------------------------- JS payloads
VALIDATE_JS = """\
// Validate and normalise the inbound lead BEFORE spending an LLM call.
// Anything that fails here throws, which routes to the error output and
// returns a 400 to the caller. Cheap rejection of junk is the first line of
// error handling in this pipeline.
const REQUIRED = ['name', 'email', 'message'];
const EMAIL_RE = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;

const out = [];
for (const item of $input.all()) {
  // The webhook node nests the POST body under `body`.
  const body = item.json.body ?? item.json;
  const errors = [];

  for (const field of REQUIRED) {
    if (body[field] === undefined || String(body[field]).trim() === '') {
      errors.push(field + ' is required');
    }
  }
  if (body.email && !EMAIL_RE.test(String(body.email).trim())) {
    errors.push('email is not a valid address');
  }
  if (body.message && String(body.message).length > 8000) {
    errors.push('message exceeds 8000 characters');
  }
  if (errors.length) {
    throw new Error('Lead validation failed: ' + errors.join('; '));
  }

  out.push({
    json: {
      receivedAt: new Date().toISOString(),
      lead: {
        name: String(body.name).trim().slice(0, 120),
        email: String(body.email).trim().toLowerCase(),
        company: String(body.company ?? '').trim().slice(0, 160),
        message: String(body.message).trim(),
        source: String(body.source ?? 'n8n-webhook').slice(0, 60),
        phone: String(body.phone ?? '').trim().slice(0, 40),
      },
    },
  });
}
return out;
"""

FALLBACK_JS = """\
// Reached only when the AI service failed every retry.
//
// Dropping the lead here would be the worst possible outcome: a 503 from a
// model provider must not cost a sale. So we apply a conservative keyword
// heuristic, mark the result degraded, and let the CRM write and the alert
// proceed. The degraded flag is stored, so these leads can be re-scored later.
const lead = $('Validate & Normalise Lead').first().json.lead;
const text = (lead.message + ' ' + lead.company).toLowerCase();

const POSITIVE = [
  /budget|pricing|quote|cost|\\$\\d/,
  /asap|urgent|deadline|this week|next week/,
  /founder|ceo|cto|head of|director|owner/,
  /demo|call|meeting|pilot|proposal/,
  /\\d+\\s*(users|seats|employees|leads)/,
];
const NEGATIVE = [
  /resume|cv|internship|applying for|job opening/,
  /we offer|our agency|seo services|backlinks/,
  /click here|crypto|bitcoin|casino|limited time offer/,
];

const hits = POSITIVE.filter((r) => r.test(text)).length;
const blocked = NEGATIVE.some((r) => r.test(text));

let priority = 'Medium';
if (blocked) priority = 'Low';
else if (hits >= 2) priority = 'High';
else if (hits === 0) priority = 'Low';

const firstName = (lead.name || 'there').split(' ')[0];
const err = $json.error?.message || $json.error || $json.message || 'AI service unavailable';

return [{
  json: {
    receivedAt: new Date().toISOString(),
    lead,
    classification: {
      priority,
      reason:
        'Fallback classification: the AI service was unreachable (' + String(err).slice(0, 160) +
        '). Matched ' + hits + ' positive keyword signal(s)' +
        (blocked ? ' and a disqualifying signal' : '') + '.',
      follow_up_message:
        'Hi ' + firstName + ', thanks for getting in touch. Your enquiry has reached us and ' +
        'a member of the team will come back to you shortly with a proper response.\\n\\n' +
        'The Spark AI team',
      summary: 'Scored by the n8n fallback heuristic because the AI service was unavailable.',
      intent: blocked ? 'spam' : 'general_enquiry',
      confidence: 0.3,
      signals: ['n8n-fallback-heuristic'],
      recommended_owner: priority === 'High' ? 'sales_lead' : 'sdr',
    },
    agent: {
      model: 'n8n-fallback-heuristic',
      degraded: true,
      degraded_reason: String(err).slice(0, 300),
      attempts: 0,
      latency_ms: 0,
      tool_calls: [],
    },
  },
}];
"""

PREPARE_CRM_JS = """\
// Join point for the two AI paths (service response and fallback). Everything
// downstream reads this shape, so the rest of the workflow does not need to
// know which path produced the classification.
const item = $input.first().json;

const STAGE_BY_PRIORITY = {
  High: 'Hot - Contact Today',
  Medium: 'Warm - Nurture',
  Low: 'Cold - Archive',
};
// Some intents should never enter the sales pipeline whatever they score.
const STAGE_BY_INTENT = {
  support_request: 'Routed to Support',
  job_application: 'Not a Sales Lead',
  vendor_pitch: 'Not a Sales Lead',
  spam: 'Not a Sales Lead',
};

const c = item.classification;
const stage = STAGE_BY_INTENT[c.intent] ?? STAGE_BY_PRIORITY[c.priority] ?? 'Warm - Nurture';

return [{
  json: {
    leadId: item.lead_id ?? '',
    receivedAt: item.receivedAt ?? new Date().toISOString(),
    lead: item.lead,
    classification: c,
    agent: item.agent ?? {},
    stage,
    shouldNotify: c.priority === 'High' && c.intent !== 'spam',
  },
}];
"""

ATTACH_CRM_JS = """\
// The HTTP node replaced the item with the CRM response, so re-attach the
// lead context the rest of the workflow needs.
const base = $('Prepare CRM Payload').first().json;
return [{ json: { ...base, crm: $json } }];
"""

CRM_ERROR_JS = """\
// CRM write failed after its retries. The lead is already qualified and that
// work is worth keeping, so we continue with a degraded CRM record rather
// than failing the run. The alert still fires and the outcome is still
// stored, with the error attached for replay.
const base = $('Prepare CRM Payload').first().json;
const err = $json.error?.message || $json.error || $json.message || 'CRM request failed';

return [{
  json: {
    ...base,
    crm: {
      provider: 'unavailable',
      contact_id: '',
      created: false,
      opportunity_id: '',
      pipeline: '',
      stage: base.stage,
      url: '',
      degraded: true,
      // `error_detail`, not `error`: n8n's HTTP Request node treats a
      // top-level `error` key in a JSON response as a node failure, which
      // silently triggered three retries of the CRM write. Keeping the same
      // field name here means the shape matches the API's.
      error_detail: String(err).slice(0, 300),
    },
  },
}];
"""

SLACK_ALERT_JS = """\
// Build a Slack Block Kit message. Built here rather than inline in the HTTP
// node so the layout is readable and testable.
const d = $json;
const c = d.classification;
const lead = d.lead;
const crm = d.crm ?? {};
const header = ':fire: High priority lead - ' + (lead.company || 'Unknown company');

const quoted = String(lead.message || '').slice(0, 600).replace(/\\n/g, '\\n>');

const payload = {
  text: header,
  blocks: [
    { type: 'header', text: { type: 'plain_text', text: header.slice(0, 150), emoji: true } },
    {
      type: 'section',
      fields: [
        { type: 'mrkdwn', text: '*Name*\\n' + lead.name },
        { type: 'mrkdwn', text: '*Email*\\n' + lead.email },
        { type: 'mrkdwn', text: '*Company*\\n' + (lead.company || 'Unknown') },
        { type: 'mrkdwn', text: '*Intent*\\n' + String(c.intent || '').replace(/_/g, ' ') },
        { type: 'mrkdwn', text: '*Confidence*\\n' + Math.round((c.confidence ?? 0) * 100) + '%' },
        { type: 'mrkdwn', text: '*Owner*\\n' + String(c.recommended_owner || '').replace(/_/g, ' ') },
      ],
    },
    { type: 'section', text: { type: 'mrkdwn', text: ('*Why this priority*\\n' + c.reason).slice(0, 2900) } },
    { type: 'section', text: { type: 'mrkdwn', text: ('*Their message*\\n>' + quoted).slice(0, 2900) } },
    {
      type: 'section',
      text: {
        type: 'mrkdwn',
        text: '*Suggested reply*\\n```' + String(c.follow_up_message || '').slice(0, 1200) + '```',
      },
    },
    {
      type: 'context',
      elements: [{
        type: 'mrkdwn',
        text: 'CRM: ' + (crm.provider || 'n/a') + ' ' + (crm.contact_id || '(not written)') +
              ' -> ' + (crm.stage || d.stage) + ' | delivered by n8n',
      }],
    },
  ],
};

return [{ json: { ...d, slackPayload: payload } }];
"""

MARK_NOTIFIED_JS = """\
// Both notification branches converge here so the record step sees one shape.
const base = $('Prepare CRM Payload').first().json;

let crm = null;
try {
  crm = $('Attach CRM Result').first().json.crm;
} catch (e) {
  try { crm = $('Handle CRM Failure').first().json.crm; } catch (e2) { crm = null; }
}

// Which branch actually ran? Referencing a node that did not execute throws,
// so this doubles as the branch test.
let viaSlack = false;
try {
  $('Post Slack Alert').first();
  viaSlack = true;
} catch (e) {
  viaSlack = false;
}

const resp = $json ?? {};
let notification;
if (resp.channel) {
  // Came back from the API notify fallback, which returns a NotificationResult.
  notification = {
    channel: resp.channel,
    sent: !!resp.sent,
    skipped_reason: resp.skipped_reason ?? '',
    error_detail: resp.error_detail ?? '',
  };
} else if (resp.error) {
  // The notification call failed but the node was set to continue, so the
  // lead still gets recorded. n8n puts the failure on `error` in that case.
  notification = {
    channel: viaSlack ? 'slack' : 'api-fallback',
    sent: false,
    skipped_reason: '',
    error_detail: String(resp.error.message ?? resp.error).slice(0, 300),
  };
} else {
  notification = { channel: 'slack', sent: true, skipped_reason: '', error_detail: '' };
}

return [{ json: { ...base, crm, notification } }];
"""

NO_ALERT_JS = """\
// Medium and Low leads are stored and stage-assigned, but sales is not paged.
// Alert fatigue is what stops people trusting the High alerts.
const base = $('Prepare CRM Payload').first().json;

let crm = null;
try {
  crm = $('Attach CRM Result').first().json.crm;
} catch (e) {
  try { crm = $('Handle CRM Failure').first().json.crm; } catch (e2) { crm = null; }
}

return [{
  json: {
    ...base,
    crm,
    notification: {
      channel: 'none',
      sent: false,
      skipped_reason: 'priority ' + base.classification.priority + ' does not page the sales team',
      error_detail: '',
    },
  },
}];
"""

BUILD_RESPONSE_JS = """\
// Shape the webhook response. The browser form renders this directly.
//
// Read from whichever notification branch actually ran, NOT from
// 'Prepare CRM Payload' - that node runs before the notification decision, so
// sourcing the response from it always reported notified:false even on High
// priority leads that had just paged the sales team.
let base = null;
try {
  base = $('Mark Notified').first().json;
} catch (e) {
  try { base = $('No Alert Needed').first().json; } catch (e2) { base = null; }
}
if (!base) base = $('Prepare CRM Payload').first().json;

const stored = $json ?? {};
const crm = base.crm ?? null;
const c = base.classification;
return [{
  json: {
    status: 'ok',
    lead_id: stored.lead_id || base.leadId || '',
    priority: c.priority,
    intent: c.intent,
    confidence: c.confidence,
    reason: c.reason,
    summary: c.summary,
    follow_up_message: c.follow_up_message,
    signals: c.signals ?? [],
    recommended_owner: c.recommended_owner,
    pipeline_stage: base.stage,
    crm: crm
      ? {
          provider: crm.provider,
          contact_id: crm.contact_id,
          created: !!crm.created,
          stage: crm.stage,
          degraded: !!crm.degraded,
          error_detail: crm.error_detail ?? '',
        }
      : null,
    notified: !!(base.notification && base.notification.sent),
    notification_channel: base.notification?.channel ?? 'none',
    notification_skipped_reason: base.notification?.skipped_reason ?? '',
    ai_degraded: !!(base.agent?.degraded),
    processed_by: 'n8n',
  },
}];
"""

ERROR_RESPONSE_JS = """\
// Validation rejected the payload. Return a 400 with something actionable
// rather than a bare 500.
const err = $json.error?.message || $json.error || $json.message || 'invalid lead payload';
// n8n appends "[line N]" to errors thrown inside a Code node. That is an
// internal detail of our workflow, not something the caller should see.
const detail = String(err)
  .replace(/^Lead validation failed: /, '')
  .replace(/\\s*\\[line \\d+\\]\\s*$/, '');

return [{
  json: {
    status: 'rejected',
    error: 'validation_failed',
    detail,
  },
}];
"""


# ------------------------------------------------------------------ builders
def node(
    name: str,
    ntype: str,
    position: list[int],
    parameters: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    n = {
        "parameters": parameters,
        "id": nid(name),
        "name": name,
        "type": f"n8n-nodes-base.{ntype}",
        "typeVersion": TV[ntype],
        "position": position,
    }
    n.update(extra)
    return n


def code_node(name: str, position: list[int], js: str, **extra: Any) -> dict[str, Any]:
    return node(name, "code", position, {"jsCode": js}, **extra)


def http_node(
    name: str,
    position: list[int],
    url: str,
    body_expr: str,
    *,
    timeout: int = 45000,
    headers: list[dict[str, str]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "method": "POST",
        "url": url,
        "sendBody": True,
        "specifyBody": "json",
        "jsonBody": body_expr,
        "options": {"timeout": timeout},
    }
    if headers:
        params["sendHeaders"] = True
        params["specifyHeaders"] = "keypair"
        params["headerParameters"] = {"parameters": headers}
    return node(name, "httpRequest", position, params, **extra)


API_HEADERS = [
    {"name": "Content-Type", "value": "application/json"},
    {"name": "X-API-Key", "value": "={{ $env.API_KEY || '' }}"},
]

API = "={{ $env.API_BASE_URL || 'http://api:8000' }}"


def build() -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [
        # ---------------------------------------------------------- intake
        node(
            "Lead Intake Webhook", "webhook", [-780, 300],
            {
                "httpMethod": "POST",
                "path": "lead-intake",
                "responseMode": "responseNode",
                "options": {},
            },
            webhookId=nid("webhook:lead-intake"),
        ),
        code_node(
            "Validate & Normalise Lead", [-560, 300], VALIDATE_JS,
            onError="continueErrorOutput",
        ),
        node(
            "Reject Invalid Lead", "code", [-340, 480],
            {"jsCode": ERROR_RESPONSE_JS},
        ),
        node(
            "Respond 400", "respondToWebhook", [-120, 480],
            {
                "respondWith": "json",
                "responseBody": "={{ JSON.stringify($json) }}",
                "options": {"responseCode": 400},
            },
        ),
        # ------------------------------------------------------------- AI
        http_node(
            "Qualify Lead (AI Agent)", [-340, 280],
            f"{API}/api/v1/classify",
            "={{ JSON.stringify($json.lead) }}",
            headers=API_HEADERS,
            onError="continueErrorOutput",
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=2000,
            alwaysOutputData=False,
        ),
        code_node("Fallback Classification", [-120, 460], FALLBACK_JS),
        code_node("Prepare CRM Payload", [100, 300], PREPARE_CRM_JS),
        # ------------------------------------------------------------ CRM
        http_node(
            "Sync Contact to CRM", [320, 300],
            f"{API}/api/v1/crm/upsert",
            "={{ JSON.stringify({ lead: $json.lead, classification: $json.classification, "
            "lead_id: $json.leadId || '' }) }}",
            timeout=30000,
            headers=API_HEADERS,
            onError="continueErrorOutput",
            retryOnFail=True,
            maxTries=3,
            waitBetweenTries=2000,
        ),
        code_node("Attach CRM Result", [540, 200], ATTACH_CRM_JS),
        code_node("Handle CRM Failure", [540, 420], CRM_ERROR_JS),
        # -------------------------------------------------------- routing
        node(
            "Route by Priority", "switch", [780, 300],
            {
                "rules": {
                    "values": [
                        _switch_rule("High"),
                        _switch_rule("Medium"),
                        _switch_rule("Low"),
                    ]
                },
                "options": {},
            },
        ),
        # ---------------------------------------------------- notification
        node(
            "Slack Webhook Configured?", "if", [1000, 140],
            {
                "conditions": {
                    "options": {
                        "caseSensitive": True,
                        "leftValue": "",
                        "typeValidation": "loose",
                        "version": 2,
                    },
                    "conditions": [
                        {
                            "id": cond_id("slack-configured"),
                            "leftValue": "={{ ($env.SLACK_WEBHOOK_URL || '').startsWith('https://hooks.slack.com') }}",
                            "rightValue": "",
                            "operator": {
                                "type": "boolean",
                                "operation": "true",
                                "singleValue": True,
                            },
                        }
                    ],
                    "combinator": "and",
                },
                "options": {},
            },
        ),
        code_node("Build Slack Alert", [1220, 40], SLACK_ALERT_JS),
        http_node(
            "Post Slack Alert", [1440, 40],
            "={{ $env.SLACK_WEBHOOK_URL }}",
            "={{ JSON.stringify($json.slackPayload) }}",
            timeout=15000,
            headers=[{"name": "Content-Type", "value": "application/json"}],
            onError="continueRegularOutput",
            retryOnFail=True,
            maxTries=2,
            waitBetweenTries=1000,
        ),
        http_node(
            "Notify via API (fallback)", [1220, 240],
            f"{API}/api/v1/notify",
            "={{ JSON.stringify({ lead: $json.lead, classification: $json.classification, "
            "lead_id: $json.leadId || '', crm: $json.crm || null, force: true }) }}",
            timeout=20000,
            headers=API_HEADERS,
            onError="continueRegularOutput",
        ),
        code_node("Mark Notified", [1660, 140], MARK_NOTIFIED_JS),
        code_node("No Alert Needed", [1000, 440], NO_ALERT_JS),
        # ------------------------------------------------------- persist
        http_node(
            "Record Outcome", [1880, 300],
            f"{API}/api/v1/leads/record",
            "={{ JSON.stringify({ lead: $json.lead, classification: $json.classification, "
            "lead_id: $json.leadId || '', crm: $json.crm || null, "
            "notification: $json.notification || null, agent: $json.agent || {}, "
            "source: 'n8n' }) }}",
            timeout=20000,
            headers=API_HEADERS,
            onError="continueRegularOutput",
            retryOnFail=True,
            maxTries=2,
            waitBetweenTries=1000,
        ),
        code_node("Build Response", [2100, 300], BUILD_RESPONSE_JS),
        node(
            "Respond 200", "respondToWebhook", [2320, 300],
            {
                "respondWith": "json",
                "responseBody": "={{ JSON.stringify($json) }}",
                "options": {"responseCode": 200},
            },
        ),
    ]

    connections: dict[str, Any] = {
        "Lead Intake Webhook": _main([["Validate & Normalise Lead"]]),
        # output 0 = valid, output 1 = validation error
        "Validate & Normalise Lead": _main(
            [["Qualify Lead (AI Agent)"], ["Reject Invalid Lead"]]
        ),
        "Reject Invalid Lead": _main([["Respond 400"]]),
        # output 0 = AI succeeded, output 1 = AI failed after retries
        "Qualify Lead (AI Agent)": _main(
            [["Prepare CRM Payload"], ["Fallback Classification"]]
        ),
        "Fallback Classification": _main([["Prepare CRM Payload"]]),
        "Prepare CRM Payload": _main([["Sync Contact to CRM"]]),
        "Sync Contact to CRM": _main([["Attach CRM Result"], ["Handle CRM Failure"]]),
        "Attach CRM Result": _main([["Route by Priority"]]),
        "Handle CRM Failure": _main([["Route by Priority"]]),
        # switch outputs: 0 High, 1 Medium, 2 Low
        "Route by Priority": _main(
            [
                ["Slack Webhook Configured?"],
                ["No Alert Needed"],
                ["No Alert Needed"],
            ]
        ),
        "Slack Webhook Configured?": _main(
            [["Build Slack Alert"], ["Notify via API (fallback)"]]
        ),
        "Build Slack Alert": _main([["Post Slack Alert"]]),
        "Post Slack Alert": _main([["Mark Notified"]]),
        "Notify via API (fallback)": _main([["Mark Notified"]]),
        "Mark Notified": _main([["Record Outcome"]]),
        "No Alert Needed": _main([["Record Outcome"]]),
        "Record Outcome": _main([["Build Response"]]),
        "Build Response": _main([["Respond 200"]]),
    }

    return {
        # `id` is optional for UI import but required by `n8n import:workflow`,
        # which writes straight into the DB. Keeping it fixed means re-importing
        # updates the same workflow instead of creating duplicates.
        "id": "sparkAiLeadQual01",
        "name": "Spark AI - Lead Qualification Agent",
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {
            "executionOrder": "v1",
            "saveManualExecutions": True,
            "saveExecutionProgress": True,
            "saveDataErrorExecution": "all",
            "saveDataSuccessExecution": "all",
        },
        "pinData": {},
        "tags": [],
        "meta": {
            "instanceId": "spark-ai-lead-agent",
            "description": (
                "Receives a lead on POST /webhook/lead-intake, qualifies it with "
                "the AI agent service, writes it to the configured CRM, alerts "
                "sales on High priority and stores the outcome. Every external "
                "call has retries and a degradation path."
            ),
        },
    }


def _switch_rule(priority: str) -> dict[str, Any]:
    return {
        "conditions": {
            "options": {
                "caseSensitive": True,
                "leftValue": "",
                "typeValidation": "strict",
                "version": 2,
            },
            "conditions": [
                {
                    "id": cond_id("priority-" + priority),
                    "leftValue": "={{ $json.classification.priority }}",
                    "rightValue": priority,
                    "operator": {"type": "string", "operation": "equals"},
                }
            ],
            "combinator": "and",
        },
        "renameOutput": True,
        "outputKey": priority,
    }


def _main(outputs: list[list[str]]) -> dict[str, Any]:
    return {
        "main": [
            [{"node": target, "type": "main", "index": 0} for target in group]
            for group in outputs
        ]
    }


if __name__ == "__main__":
    workflow = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(workflow['nodes'])} nodes)")
