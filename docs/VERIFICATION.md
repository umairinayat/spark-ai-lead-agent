# Verification log

Everything below was run against a real **n8n 2.35.7** instance with the
workflow imported from `n8n/workflow.lead-qualification.json`, and a live
FastAPI service. These are transcripts, not descriptions.

---

## 1. The workflow imports into real n8n

```
$ n8n import:workflow --input=n8n/workflow.lead-qualification.json
Importing 1 workflows...
Successfully imported 1 workflow.
```

First attempt failed with `SQLITE_CONSTRAINT: NOT NULL constraint failed:
workflow_entity.id` — the CLI importer writes straight to the database and
requires a workflow `id`, which the editor's own export makes optional. A
fixed `id` was added, which also means re-importing updates the same workflow
instead of creating duplicates.

## 2. Automated tests

```
$ python -m pytest -q
...................................................................      [100%]
67 passed in 2.54s
```

Coverage by area: agent schema and tools (14), rules engine (6), agent failure
paths (7), CRM adapters (12), notification (7), API contract (13), workflow
export structure (8).

## 3. Classification across the full sample corpus, through n8n

Ten leads posted to `POST http://localhost:5678/webhook/lead-intake`:

```
LEAD            EXPECT   GOT      INTENT            STAGE                  SALES
-----------------------------------------------------------------------------------
Sara Malik      High     High     demo_request      Hot - Contact Today    paged
Daniel Okafor   High     High     demo_request      Hot - Contact Today    paged
Priya Raman     Medium   Medium   general_enquiry   Warm - Nurture         -
Tom Bradley     Medium   Medium   general_enquiry   Warm - Nurture         -
Ahmed Hassan    Low      Low      job_application   Not a Sales Lead       -
Growth Team     Low      Low      vendor_pitch      Not a Sales Lead       -
Crypto Alerts   Low      Low      spam              Not a Sales Lead       -
Rachel Dunn     Low      Low      support_request   Routed to Support      -
J               Low      Low      general_enquiry   Cold - Archive         -
Lina Ferrer     Low      Low      general_enquiry   Cold - Archive         -

10/10 within the expected band
```

Note the two intent overrides doing real work: Rachel Dunn is an existing
customer with a broken workflow, routed to support rather than into the sales
pipeline; the job application and the agency pitch are filed as *Not a Sales
Lead* rather than merely "Low".

Resulting aggregate:

```json
{
  "total_leads": 10,
  "by_priority": {"High": 2, "Medium": 2, "Low": 6},
  "by_intent": {"demo_request": 2, "general_enquiry": 4, "job_application": 1,
                "spam": 1, "support_request": 1, "vendor_pitch": 1},
  "notifications_sent": 2
}
```

Exactly two notifications for two High leads. Ten CRM upsert calls for ten
leads (see §6 — this was three times higher before a bug was found).

## 4. Idempotency

Re-posting the same lead:

```
crm created: False | reason mentions history: True
```

The CRM updates rather than duplicating, and the agent's
`lookup_existing_contact` tool now reports the prior contact, which feeds back
into the score as a positive signal.

## 5. The real LLM code path

The agent speaks plain HTTP to OpenAI, so `scripts/fake_openai.py` — an
OpenAI-compatible stub — exercises the genuine code path with no key and no
spend. `OPENAI_BASE_URL` points at it.

Normal mode:

```
priority   : High
confidence : 0.88
degraded   : False
model      : gpt-4o-mini | attempts 1 | latency 110 ms
TOOL CALLS the agent made:
   - extract_intent_signals -> ['signals_found', 'evidence', ...] (0ms)
   - analyse_email_domain   -> ['domain', 'domain_type', ...]     (0ms)
```

The model requested both tools, the service executed them, fed the results
back, and the second call returned schema-valid structured output.

Failure modes, same live service:

```
--- FAKE_MODE=error ---     (HTTP 500 from the provider)
  degraded: True | attempts: 3 | model: deterministic-rules-v1
  reason  : llm_failed_after_3_attempts: OpenAI HTTP 500: simulated upstream failure
  priority still produced: High

--- FAKE_MODE=refusal ---   (safety refusal in the `refusal` field)
  degraded: True | attempts: 3 | model: deterministic-rules-v1
  reason  : llm_failed_after_3_attempts: model refused: I cannot assist with that request.
  priority still produced: High

--- FAKE_MODE=garbage ---   (prose instead of JSON)
  degraded: True | attempts: 3 | model: deterministic-rules-v1
  reason  : llm_failed_after_3_attempts: model returned non-JSON content
  priority still produced: High
```

In all three the lead was still classified, filed and routed. Nothing was
dropped, and the degradation is recorded rather than hidden.

## 6. The two bugs that only running it would have found

### Bug 1 — n8n silently tripled every CRM write

The mock CRM showed `touch_count: 3` after one lead. n8n's execution log
reported the node running **once, successfully**. The API access log told the
real story:

```
18:18:51.732 POST /api/v1/crm/upsert 200
18:18:53.749 POST /api/v1/crm/upsert 200     <- +2000ms
18:18:55.765 POST /api/v1/crm/upsert 200     <- +2000ms
```

2000 ms is the node's `waitBetweenTries`. The node's recorded `executionTime`
was 4059 ms for a 3 ms call.

**Cause:** n8n's HTTP Request node treats a top-level `error` key in a JSON
response body as a node failure — on HTTP 200, with an empty-string value.
`CrmContact` had a field called `error`. `retryOnFail` retries *inside* one
node execution and records only the final attempt, which is why the execution
log looked clean.

**Isolated** with a one-variable test: an endpoint returning the identical
body minus the `error` key was called once; the original was called three
times.

**Fixed** by renaming the field to `error_detail`, with a regression test so
it cannot be renamed back.

Against a paid, rate-limited GoHighLevel account this was three API calls per
lead.

### Bug 2 — `notified` always reported false

A High lead that had genuinely paged sales returned `"notified": false`. The
response builder read the notification from a node that runs *before* the
notification decision. Fixed to read from whichever notification branch
actually executed.

## 7. Error paths through the workflow

Invalid payloads are rejected before any LLM spend:

```
$ curl -X POST .../webhook/lead-intake -d '{"name":"No Email","message":"   "}'
HTTP 400
{"status":"rejected","error":"validation_failed","detail":"email is required; message is required"}

$ curl -X POST .../webhook/lead-intake -d '{"name":"Bad","email":"not-an-email","message":"hello"}'
HTTP 400
{"status":"rejected","error":"validation_failed","detail":"email is not a valid address"}
```

With the AI service **stopped entirely**, a lead still completes end to end in
9.3 s (three retries, then the workflow's own heuristic):

```json
{
  "status": "ok",
  "priority": "High",
  "reason": "Fallback classification: the AI service was unreachable
             (connect ECONNREFUSED 127.0.0.1:8000). Matched 3 positive keyword signal(s).",
  "pipeline_stage": "Hot - Contact Today",
  "crm": {"provider": "unavailable", "degraded": true,
          "error_detail": "connect ECONNREFUSED 127.0.0.1:8000"},
  "ai_degraded": true,
  "processed_by": "n8n"
}
```

The lead was classified, staged and answered with a follow-up message while
both the AI service and the CRM were unreachable.

## 8. What was *not* verified here

Stated plainly so nothing in this document is taken further than it should be:

- **The Docker image was not built.** The environment this was developed in
  had the Docker CLI but no daemon. `docker-compose.yml` parses and the
  Dockerfile is conventional (slim base, dependency layer first, non-root
  user, healthcheck), but neither has been run. Everything else in this
  document was verified against the API and n8n running natively.
  `requirements.txt` *was* verified: a clean virtualenv installs it and all
  67 tests pass against that install.
- **No live call to OpenAI, GoHighLevel, HubSpot or Slack.** All four are
  reached over plain HTTP through code paths exercised against local stubs
  and failure injection. The request shapes follow each vendor's current
  documentation, but a first live run may still need a credential or field-id
  adjustment — the adapters report exactly what is missing when misconfigured.

## 9. Classifier bugs found and fixed during development

Caught by running the corpus rather than by review:

1. **"We are a 40-person freight brokerage"** matched the `vendor_pitch`
   pattern (`we are a`) and forced a strong lead to Low. The pattern now
   requires the sender to be selling *to us*.
2. **"Would you like to partner with us?"** matched `decision_maker`, because
   the bare word `partner` sat in the job-title list — so every agency cold
   pitch scored as a decision maker. Titles only now.
3. **The Medium band was unreachable.** Genuine early-stage enquiries scored
   0 and fell to Low. Two softer signals (`describes_process`, `asks_question`)
   were added at weight 1 — enough to separate a real enquiry from "info pls",
   not enough to reach High alone.
4. **`velocitycommerce.io` did not match "Velocity Commerce"** because the
   check was a token-set intersection. It now also compares the squashed form.
5. **Disqualifiers were all-or-nothing.** A single loose negative match buried
   leads with real buying evidence. They are now split into hard (job
   application, spam, support ticket — decisive) and soft (vendor pitch —
   overridden by two or more hard positive signals).

Each has a regression test named after the symptom.
