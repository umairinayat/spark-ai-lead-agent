# Spark AI — Lead Qualification Agent

An AI agent that triages inbound leads, writes them to a CRM, and pages the
sales team when something is worth interrupting them for.

Submitted for the Agentic AI Engineer technical assessment.

**It runs with an empty `.env`.** No OpenAI key, no CRM account, no Slack
workspace. Every integration degrades to a working local equivalent, so the
whole pipeline can be reviewed end to end in about two minutes — then lit up
one credential at a time.

---

## What it does

A lead arrives (name, email, company, message). The agent:

1. gathers evidence by calling tools — keyword signals, email-domain analysis,
   whether this person has contacted us before
2. classifies the lead **High / Medium / Low** with a reason grounded in that
   evidence
3. drafts a personalised follow-up message
4. and the automation files it in the CRM at the right pipeline stage, then
   pages sales on Slack if it is High

```
Browser form ──POST──► n8n webhook
                          │
                          ├─ 1. validate & normalise       reject junk before spending an LLM call
                          ├─ 2. POST /api/v1/classify      agent: tools → structured output
                          │        └ service down? → heuristic fallback inside n8n
                          ├─ 3. POST /api/v1/crm/upsert    mock │ GoHighLevel │ HubSpot
                          │        └ CRM down? → degraded record, pipeline continues
                          ├─ 4. switch on priority
                          │        High   → Slack Block Kit alert
                          │        Medium → filed, no alert
                          │        Low    → filed, no alert
                          ├─ 5. POST /api/v1/leads/record  persist outcome + agent trace
                          └─ 6. respond 200
```

**n8n is the orchestrator; the FastAPI service is a stateless AI
microservice.** Keeping that boundary clean is what makes the CRM swappable
without touching the workflow, and makes `/api/v1/classify` safe for n8n to
retry.

---

## Quick start

### Docker (everything)

```bash
cp .env.example .env          # works as-is; add credentials later
docker compose up --build -d
make import-workflow          # loads the workflow into n8n
```

Open <http://localhost:5678>, open **Spark AI - Lead Qualification Agent** and
toggle it **Active**. Then open <http://localhost:3000> and submit a lead.

| | |
|---|---|
| Lead form | <http://localhost:3000> |
| n8n editor | <http://localhost:5678> |
| API reference | <http://localhost:8000/docs> |

### Without Docker

```bash
make install
cp .env.example .env
make run                      # API on :8000
open frontend/index.html      # switch "Submit via" to "API directly"
```

The form's **API directly** mode runs the same four steps in one call, so the
system is fully demonstrable without standing up n8n at all.

### See it work in one command

```bash
make demo                     # or: python scripts/demo.py --via n8n
```

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

---

## Switching on the real integrations

Everything is off by default. Turn on whichever you have.

### OpenAI

```bash
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

With no key the service uses its deterministic rules engine and marks every
result `degraded`. That is a supported mode, not a broken one — it is how the
tests run and how the project demos without spending anything.

To exercise the *real* LLM code path — tool-calling round trips, strict
structured output, refusal handling — without a key:

```bash
python scripts/fake_openai.py &
OPENAI_API_KEY=sk-fake OPENAI_BASE_URL=http://127.0.0.1:8099/v1 make run
```

### CRM — and the GoHighLevel question

The brief names GoHighLevel. **GoHighLevel is a paid product**, so building
only against it would make this deliverable unreviewable without a licence.
Stubbing the CRM out entirely would make "CRM integration" a claim rather than
a demonstration. So there is one interface with three implementations:

| `CRM_PROVIDER` | What it is | Cost |
|---|---|---|
| `mock` *(default)* | Local SQLite store implementing real upsert-by-email semantics, stage assignment and AI-field storage | free, no account |
| `gohighlevel` | GoHighLevel v2 REST API — `POST /contacts/upsert` and `POST /opportunities/` | paid account |
| `hubspot` | HubSpot CRM v3 — `POST /crm/v3/objects/contacts/batch/upsert` | **free** developer tier |

The mock is not a no-op: submit the same email twice and it updates rather
than duplicating, increments a touch count, and that history feeds back into
the agent's next classification. The behaviour you see locally is the
behaviour you get in production, minus the network.

**HubSpot is the one a reviewer can actually run**, which is why it is there.

<details>
<summary>GoHighLevel setup</summary>

```bash
CRM_PROVIDER=gohighlevel
GHL_API_KEY=...            # Settings → Private Integrations
                           # scopes: contacts.write, opportunities.write
GHL_LOCATION_ID=...        # from the dashboard URL /v2/location/<THIS>/
GHL_PIPELINE_ID=...        # GET /opportunities/pipelines
GHL_STAGE_HIGH=...
GHL_STAGE_MEDIUM=...
GHL_STAGE_LOW=...
```

Two caveats worth knowing before you switch this on. GHL custom fields are
addressed by per-location **id**, not by name, so writing typed AI fields
needs a bootstrap step per tenant; without those ids the adapter uses tags
plus the opportunity name, both visible in the UI. And `GHL_API_VERSION`
defaults to `2021-07-28`; newer docs show `v3` on some endpoints, so it is
configurable rather than hard-coded.
</details>

<details>
<summary>HubSpot setup (free)</summary>

```bash
CRM_PROVIDER=hubspot
HUBSPOT_TOKEN=pat-...      # private app, scopes crm.objects.contacts.read/write
python scripts/hubspot_setup.py   # creates the custom ai_* properties
```

If the custom properties do not exist, the adapter detects HubSpot's specific
400 and retries with standard properties only, so a first run against a fresh
portal still creates the contact.
</details>

### Slack

```bash
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
NOTIFY_ON_PRIORITIES=High
```

Incoming Webhooks are free. With none configured, the alert is printed to the
service log and stored — `GET /api/v1/notifications` shows every notification
decision, sent or not.

---

## How the agent works

`app/agent/classifier.py`. This is an agent, not a single prompt: the model
decides what evidence it needs, we execute it, and only then does it commit.

```
build prompt
    │
    ▼
tool-calling loop  ◄── model requests evidence, we run it, feed results back
    │  (max AGENT_MAX_TOOL_ROUNDS rounds)
    ▼
final call, response_format = json_schema (strict)
    │
    ├─ valid? → Pydantic validation → done
    └─ HTTP error / timeout / refusal / bad JSON / schema mismatch
             → retry with backoff → retries exhausted
                    → deterministic rules engine (degraded = true)
```

`LeadAgent.run()` **never raises**. A lead is never lost because a model
provider had a bad afternoon.

### The tools

| Tool | Returns |
|---|---|
| `extract_intent_signals` | Which buying and disqualifying signals are present, with the matched phrases as evidence |
| `analyse_email_domain` | corporate / freemail / disposable / academic / government, and whether it matches the stated company |
| `lookup_existing_contact` | Whether we have seen this email before, how often, and what it scored last time |

They are deterministic on purpose. The same lead yields the same evidence
every time, they are unit-testable offline, and **the fallback engine scores
the identical evidence** — so the two paths can disagree about judgement, but
never about facts.

### Structured output

```json
{
  "priority": "High",
  "reason": "Names a $15k budget and an end-of-Q4 deadline, and asks for a call this week.",
  "follow_up_message": "Hi Sara, thanks for laying out the timeline so clearly...",
  "summary": "Freight brokerage wants quote follow-up automation.",
  "intent": "demo_request",
  "confidence": 0.88,
  "signals": ["budget stated", "deadline stated", "corporate domain"],
  "recommended_owner": "sales_lead"
}
```

Enforced twice: OpenAI `strict: true` JSON Schema, then Pydantic validation of
the parsed result. A validation failure is a retryable error, not something
that reaches the CRM.

`intent` earns its place — it lets a support ticket bypass the sales pipeline
no matter how urgently it is worded, and stops spam paging anyone.

### Priority rubric

**High** — at least two of: a concrete project described, budget stated or
implied, a timeline, decision-maker language, a specific commercial ask
(demo / quote / pilot), or volume figures.

**Medium** — a genuine business enquiry that is early: interest without a
timeline or budget, research-stage questions.

**Low** — no commercial value to sales: job applications, agency cold pitches,
support tickets from existing customers, spam, or too vague to act on.

Stage assignment, with intent overriding priority:

| | Stage |
|---|---|
| High | Hot - Contact Today |
| Medium | Warm - Nurture |
| Low | Cold - Archive |
| *intent = support_request* | Routed to Support |
| *intent = job_application / vendor_pitch / spam* | Not a Sales Lead |

---

## Error handling

Two independent fallback layers at different levels, because they fail
differently:

| Failure | What happens |
|---|---|
| Invalid lead payload | Rejected at the workflow's first node → 400 with a readable reason. No LLM spend on junk |
| OpenAI 5xx / timeout / rate limit | Retry with exponential backoff, then the rules engine. Flagged `degraded` |
| Model refuses, or returns non-JSON, or breaks the schema | Treated as a retryable error, then the same fallback |
| **The whole AI service is down** | n8n's error output runs a JS heuristic; the lead still reaches the CRM and the alert |
| CRM unreachable | 3 attempts, then a degraded CRM record. The alert still fires |
| Slack unreachable | Recorded as not-sent; the lead is still stored |

Verified live with the AI service stopped: a lead still completed end to end
in 9.3 s, classified High, staged, and answered. See
[`docs/VERIFICATION.md`](docs/VERIFICATION.md) §7.

Degraded results are **flagged and stored**, never silently substituted. A
sales team that cannot tell a model judgement from a keyword guess will stop
trusting both.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness and the active configuration |
| `GET` | `/api/v1/config` | Stage map, notify thresholds |
| `POST` | `/api/v1/classify` | **Agent only, no side effects** — safe for n8n to retry |
| `POST` | `/api/v1/crm/upsert` | CRM write through the configured provider |
| `POST` | `/api/v1/notify` | Sales notification |
| `POST` | `/api/v1/leads/record` | Persist the outcome assembled by the workflow |
| `POST` | `/api/v1/leads` | All four steps in one call (browser form) |
| `GET` | `/api/v1/leads` `/{id}` | Stored leads; one lead plus its agent trace |
| `GET` | `/api/v1/stats` | Counters by priority and intent |
| `GET` | `/api/v1/crm/contacts` | Inspect the mock CRM |
| `GET` | `/api/v1/notifications` | Notification audit log |

Interactive reference at `/docs`.

```bash
curl -X POST localhost:8000/api/v1/classify -H 'Content-Type: application/json' -d '{
  "name": "Sara Malik",
  "email": "sara@northwind-logistics.com",
  "company": "Northwind Logistics",
  "message": "40-person freight brokerage, need quote follow-up automation. Budget ~$15k, live before end of Q4. Call this week?"
}'
```

---

## Tests

```bash
make test
# 67 passed
```

The failure-path tests are the ones worth reading. Anyone can test a happy
path; the real question is what happens when a model provider 500s halfway
through a sales day.

```
test_agent_falls_back_when_provider_errors
test_agent_rejects_output_that_breaks_the_schema
test_agent_handles_a_model_refusal
test_a_broken_tool_degrades_evidence_not_the_run
test_prompt_injection_cannot_change_the_output_shape
test_crm_network_failure_does_not_raise
test_slack_failure_is_recorded_not_raised
test_crm_result_has_no_top_level_error_key
test_workflow_error_branches_are_wired
test_workflow_secrets_come_from_environment
```

The workflow export is tested like code: no dangling connections, no
`$('Node')` references to renamed nodes, every HTTP node has a timeout and an
error path, every `continueErrorOutput` has its second output actually
connected, and no secrets are baked into the JSON.

---

## Two bugs that only running it would have found

Both were invisible to code review *and* to n8n's own execution log.

**n8n silently tripled every CRM write.** The mock CRM showed `touch_count: 3`
after one lead while n8n reported the node running once, successfully. The API
access log showed three calls exactly 2000 ms apart — the node's
`waitBetweenTries`. Cause: n8n's HTTP Request node treats a top-level `error`
key in a JSON response as a node failure, on HTTP 200, with an empty-string
value. `CrmContact` had a field called `error`. Isolated with a one-variable
test (identical body minus that key → one call instead of three) and fixed by
renaming it to `error_detail`, with a regression test so it cannot come back.
Against a rate-limited paid GoHighLevel account that was three API calls per
lead.

**`notified` always reported false**, because the response builder read the
notification from a node that runs *before* the notification decision.

Full write-up: [`docs/phases/phase-04-n8n-workflow.md`](docs/phases/phase-04-n8n-workflow.md).

---

## Design decisions

**Why n8n orchestrates instead of the API.** The alternative makes the API
call n8n and n8n call back into the API — circular, with no clean answer to
"who is in charge?". This way the workflow export is the actual system, and
`/classify` stays side-effect free so retrying it is safe.

**Why the CRM write goes through the API rather than direct from n8n.**
Credentials stay in one place and `CRM_PROVIDER` swaps GoHighLevel for HubSpot
or the mock without editing the workflow. Slack *is* called directly from n8n,
so a reviewer can see a real third-party integration in the workflow itself.

**Why no agent framework.** The loop is about 90 lines of explicit code.
LangChain or CrewAI would hide exactly the thing this assessment is testing.

**Why SQLite, not Postgres.** Four flat tables through the standard library.
Swapping to Postgres means changing `app/db.py` only. At this volume Postgres
would add a container, a migration story and another thing to explain, and buy
nothing.

**Why a single HTML file, not React.** No build step, no `node_modules` in the
deliverable, and editable live during the interview.

**Why the JSON Schema is hand-written.** Pydantic's `model_json_schema()`
emits `$defs`/`anyOf` for enums and optionals; OpenAI strict mode rejects
both. The drift risk is paid for by a test that asserts the two stay in sync.

**Scope.** The brief says *simple*, says *ASAP*, and asks the candidate to
make a small change during the interview. That last line is a design
constraint. React + Postgres + a nine-phase build would score nothing extra
against the stated requirements and would be harder to change on camera.

Full reasoning, including what I would do next and why it was cut:
[`docs/PLAN.md`](docs/PLAN.md).

---

## Known limitations

Stated rather than hidden:

- **The rate limiter is in-process**, so it is per-worker rather than global.
  Correct for one container; it needs Redis behind several.
- **No idempotency key on the webhook.** A client retry creates a second lead
  record. The CRM contact is safe because it upserts.
- **Ten hand-written samples is a smoke test, not an evaluation.** Real
  accuracy numbers need a few hundred labelled leads and a confusion matrix.
- **Degraded results are flagged and queryable but nothing re-scores them**
  once the provider recovers.
- **GoHighLevel custom fields** need a per-location id mapping to be written
  as typed fields; currently the adapter uses tags plus the opportunity name.

---

## Layout

```
app/
  main.py            FastAPI app, endpoints, middleware
  config.py          settings; every integration optional
  models.py          Pydantic contracts
  service.py         qualify → CRM → notify → persist
  db.py              SQLite repository
  security.py        API key, rate limiting
  agent/
    classifier.py    the agent loop, retries, fallback
    prompts.py       system prompt and rubric
    schema.py        strict JSON Schema for structured output
    tools.py         the three deterministic tools
    rules.py         deterministic fallback classifier
  crm/
    base.py          CrmAdapter interface, stage mapping
    mock.py  gohighlevel.py  hubspot.py
  notify/slack.py    Block Kit alert + console fallback
n8n/
  workflow.lead-qualification.json     20 nodes, import this
frontend/index.html                    lead portal, no build step
scripts/
  build_workflow.py  generates the workflow export
  demo.py            submits every sample lead
  fake_openai.py     OpenAI-compatible stub for offline testing
  hubspot_setup.py   creates HubSpot custom properties
  sample_leads.json  the labelled corpus
tests/               67 tests
docs/
  PLAN.md            the real implementation plan
  VERIFICATION.md    transcripts of every verification run
  phases/phase-04-n8n-workflow.md      node-by-node + the bug write-ups
DEMO_SCRIPT.md       2-3 minute video script with timings
```

---

## What has and has not been run

Verified end to end against a real n8n 2.35.7 instance and a live API: the
workflow import, all ten sample leads through the webhook, the validation and
degradation branches, the agent's tool-calling loop, and three distinct LLM
failure modes. Transcripts are in [`docs/VERIFICATION.md`](docs/VERIFICATION.md).

Not run: the Docker image was never built — the development environment had
the Docker CLI but no daemon — and there has been no live call to OpenAI,
GoHighLevel, HubSpot or Slack. Those paths were exercised against local stubs
and failure injection instead. `requirements.txt` was verified in a clean
virtualenv with the full test suite passing against it.
