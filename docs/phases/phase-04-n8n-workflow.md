# Phase 4 — The n8n workflow

File: `n8n/workflow.lead-qualification.json` (20 nodes)
Generator: `scripts/build_workflow.py`

The workflow is generated from a Python script rather than hand-edited as
JSON. Three reasons: node ids stay stable so regenerating produces no spurious
diff, the embedded JavaScript stays readable instead of becoming a wall of
`\n`-escaped string, and the node/typeVersion table lives in one place where
it can be checked against a real n8n install.

`typeVersion` values were read out of an installed **n8n 2.35.7** rather than
guessed, and each is pinned at or below that version's default so the export
also imports on older releases.

---

## Node-by-node

| # | Node | Type | What it does |
|---|---|---|---|
| 1 | Lead Intake Webhook | webhook v2 | `POST /webhook/lead-intake`, `responseMode: responseNode` so the workflow controls the reply |
| 2 | Validate & Normalise Lead | code v2 | Required fields, email format, 8000-char cap. Throws → error output |
| 3 | Reject Invalid Lead | code v2 | Builds a clean error body, strips n8n's internal `[line N]` suffix |
| 4 | Respond 400 | respondToWebhook | Returns the validation failure |
| 5 | Qualify Lead (AI Agent) | httpRequest v4.2 | `POST /api/v1/classify`, 3 tries, 2s backoff, 45s timeout |
| 6 | Fallback Classification | code v2 | Keyword heuristic when the AI service is unreachable |
| 7 | Prepare CRM Payload | code v2 | Join point for both AI paths; assigns the pipeline stage |
| 8 | Sync Contact to CRM | httpRequest v4.2 | `POST /api/v1/crm/upsert`, 3 tries, 30s timeout |
| 9 | Attach CRM Result | code v2 | Re-attaches lead context after the HTTP node replaced the item |
| 10 | Handle CRM Failure | code v2 | Degraded CRM record so the run continues |
| 11 | Route by Priority | switch v3.2 | Three named outputs: High / Medium / Low |
| 12 | Slack Webhook Configured? | if v2.2 | Checks `$env.SLACK_WEBHOOK_URL` starts with the Slack host |
| 13 | Build Slack Alert | code v2 | Block Kit payload |
| 14 | Post Slack Alert | httpRequest v4.2 | Posts to the Incoming Webhook, continues on error |
| 15 | Notify via API (fallback) | httpRequest v4.2 | `POST /api/v1/notify` when Slack is not configured |
| 16 | Mark Notified | code v2 | Merges both notification branches into one shape |
| 17 | No Alert Needed | code v2 | Medium/Low path: records why no alert was sent |
| 18 | Record Outcome | httpRequest v4.2 | `POST /api/v1/leads/record` |
| 19 | Build Response | code v2 | Assembles the webhook response body |
| 20 | Respond 200 | respondToWebhook | Returns the full result |

## Error handling design

Every external call can fail, and every one of them has a defined path:

| Failure | Behaviour |
|---|---|
| Invalid lead payload | Node 2 throws → 400 with a readable reason. No LLM spend on junk |
| AI service slow | 45s timeout, 3 attempts, 2s backoff |
| AI service down | Error output → node 6 heuristic → pipeline continues, flagged degraded |
| CRM down | 3 attempts, then node 10 → degraded record → alert still fires |
| Slack down | `continueRegularOutput` → recorded as not-sent, lead still stored |
| Record call fails | `continueRegularOutput` → caller still gets its classification |

The principle throughout: **an integration outage must never cost a sales
lead.** Every degradation is flagged in the response and stored, so degraded
leads can be found and re-scored later.

---

## Two bugs that only running it would have found

Both were invisible to code review and to the workflow's own execution log.
They were caught by importing the export into a real n8n 2.35.7, running
leads through it, and reading the API's access log.

### 1. n8n silently retried every CRM write three times

**Symptom.** The mock CRM showed `touch_count: 3` after a single lead, and the
CRM node reported `created: false` on a contact that had just been created.
n8n's execution log said the node ran **once**, successfully.

**Evidence.** The API access log showed three `POST /api/v1/crm/upsert` calls
exactly 2000 ms apart — the node's `waitBetweenTries` — all returning HTTP 200
in 3-5 ms. The node's recorded `executionTime` was 4059 ms for a 3 ms call,
which is two retry waits.

**Cause.** The n8n HTTP Request node inspects a JSON response body for a
top-level `error` key and treats its presence as a node failure — even on
HTTP 200, and even when the value is an empty string. `CrmContact` had a field
called `error`. With `retryOnFail` enabled, every CRM write fired three times.
`retryOnFail` retries *within* one node execution and records only the final
attempt, which is why the execution log showed a single clean run.

**Isolation.** A diagnostic endpoint returning the identical body minus the
`error` key was called **once**; the original endpoint was called **three
times**. Same node config, same lead — one-variable difference.

**Fix.** The field is now `error_detail` in `CrmContact` and
`NotificationResult`. `tests/test_crm_and_notify.py::test_crm_result_has_no_top_level_error_key`
guards it, because renaming it back would silently reintroduce triple writes.

**Why it mattered.** Against GoHighLevel this is three API calls per lead
against a rate-limited paid product, and any non-idempotent CRM operation
would have been applied three times.

### 2. `notified` always reported false

**Symptom.** A High-priority lead that had genuinely paged the sales team came
back with `"notified": false`.

**Cause.** The `Build Response` node read the notification from
`$('Prepare CRM Payload')` — node 7, which runs *before* the notification
decision at nodes 12-17. The field it was reading did not exist yet.

**Fix.** `Build Response` now reads from whichever notification branch
actually ran (`Mark Notified` or `No Alert Needed`), using the fact that
referencing a node that did not execute throws — which doubles as the branch
test.

---

## Importing

**Docker (recommended):**

```bash
docker compose up -d
make import-workflow        # or: docker compose exec n8n \
                            #   n8n import:workflow --input=/workflows/workflow.lead-qualification.json
```

Then open <http://localhost:5678>, open the workflow and toggle it **Active**.

**Editor UI:** Workflows → ⋯ → *Import from File*.

**CLI:** `n8n import:workflow --input=n8n/workflow.lead-qualification.json`.
Note that the CLI writes straight to the database, so n8n must be restarted
for an import or an activation change to take effect. The export carries a
fixed `id` (`sparkAiLeadQual01`) so re-importing updates the same workflow
rather than creating duplicates.

## Environment the workflow expects

| Variable | Purpose | Default in the workflow |
|---|---|---|
| `API_BASE_URL` | Where the AI service lives | `http://api:8000` |
| `API_KEY` | Sent as `X-API-Key`; omit if auth is disabled | empty |
| `SLACK_WEBHOOK_URL` | Incoming webhook; empty routes to the API fallback | empty |

`N8N_BLOCK_ENV_ACCESS_IN_NODE=false` must be set, or `$env` is unreadable
inside expressions and Code nodes. It is already set in `docker-compose.yml`.

No credential is baked into the export — a test asserts this.

## Changing it during the interview

Likely asks and where they land:

- **Add a priority tier** → `STAGE_BY_PRIORITY` in `app/crm/base.py`, a rule
  in the Switch node, one enum value in `app/agent/schema.py`.
- **Notify on Medium too** → `NOTIFY_ON_PRIORITIES=High,Medium`, and connect
  the Switch's Medium output to `Slack Webhook Configured?`. No code change.
- **Add a scoring signal** → one `(name, regex)` tuple in
  `SIGNAL_PATTERNS` and one weight in `POSITIVE_WEIGHTS`.
- **Swap the CRM** → `CRM_PROVIDER=hubspot`. No workflow change at all.
- **Add an agent tool** → a function in `tools.py`, an entry in `TOOL_SPECS`,
  an entry in `TOOL_IMPLS`.
