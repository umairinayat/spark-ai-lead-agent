# Spark AI — Agentic AI Engineer assessment (built 2026-09-14)

Deliverable: `spark-ai-lead-agent.zip`. AI lead qualification agent + n8n
automation. Built, run and verified end to end; not just written.

## Architecture decided

**n8n orchestrates; FastAPI is a stateless AI microservice.** The original
phase plan had `Frontend → API → n8n → AI Agent → CRM → Slack`, which is
circular (the API calls n8n, n8n calls back into the API's agent). Fixed to:

```
webhook → validate → POST /classify → POST /crm/upsert → switch on priority
        → Slack (High only) → POST /leads/record → respond
```

CRM write routed through the API (credentials in one place, provider swappable
without editing the workflow); Slack called direct from n8n so a real
third-party integration is visible in the workflow.

## Key decisions

- **Scope cut deliberately.** Brief says "simple", "ASAP", and "make a small
  change during the technical interview". Dropped React/TS/Tailwind, Postgres
  and the 9-phase build. Single HTML file (no build step, editable live),
  SQLite via stdlib.
- **GoHighLevel is paid** → adapter pattern: `mock` (SQLite, real
  upsert-by-email semantics) | `gohighlevel` (v2 API, fully written) |
  `hubspot` (free tier — the one a reviewer can actually run). One env var.
- **Genuine agent loop**, not a single prompt: model calls deterministic tools
  (signal extraction, domain analysis, CRM history lookup), then a strict
  JSON-Schema-constrained final answer. No framework — the loop is ~90 lines
  and hiding it would hide the thing being assessed.
- **Two independent fallback layers**: rules engine inside the service (LLM
  error/refusal/bad JSON/schema failure), and a JS heuristic inside n8n (whole
  service down). `LeadAgent.run()` never raises.
- **`intent` overrides `priority` for staging** so support tickets and job
  applications never enter the sales pipeline.

## Bugs found by running it (good interview material)

1. **n8n silently tripled every CRM write.** Its HTTP Request node treats a
   top-level `error` key in a JSON response as a node failure — on HTTP 200,
   with an empty-string value — so `retryOnFail` fired twice more. n8n's own
   execution log showed one clean run; only the API access log revealed it
   (three calls exactly 2000ms apart = `waitBetweenTries`). Isolated with a
   one-variable test. Fixed by renaming the field to `error_detail`, with a
   regression test. **Remember this — it will recur in any n8n project.**
2. `notified` always false — response built from a node upstream of the
   notification decision.
3. Classifier regex bugs: "we are a 40-person brokerage" matched vendor_pitch;
   bare "partner" in the decision-maker title list matched "partner with us";
   the Medium band was unreachable until softer engagement signals were added.

## Verified

n8n 2.35.7, real import + live runs. 10/10 sample leads correctly banded,
exactly 10 CRM calls for 10 leads, 2 alerts for 2 High leads, validation
returns 400, full pipeline completes in 9.3s with the AI service stopped.
67 tests pass from a clean unzip. Docker image NOT build-tested (no daemon in
the dev sandbox); no live calls to OpenAI/GHL/HubSpot/Slack.

## Still to do (user's side)

Record the 2–3 min video — script with timings is in `DEMO_SCRIPT.md`.

I read the whole plan. Before I build, the honest assessment:

Your phase plan is not actually a plan. All 38 phase documents are byte-identical boilerplate — same "Purpose / Objectives / Tasks / Deliverables / Verification Criteria" text with only the title changed. There are no node lists, no schemas, no prompts, no API contracts. The only real content is in the five root files (~90 lines total).

Two things in those root files I'd push back on before writing code:

1. The architecture flow is circular. Frontend → API → n8n → AI Agent → CRM → Slack has FastAPI calling n8n, then n8n calling back into the AI agent which lives inside FastAPI. An assessor will ask "who is the orchestrator?" and there's no clean answer. The defensible version: n8n is the spine (webhook → HTTP call to the AI service → CRM → Slack), FastAPI is a stateless AI microservice. That also makes the workflow export — a required deliverable — the actual centerpiece rather than a thin wrapper.

2. Scope is the biggest risk to your score, not your strength. The brief says "simple", "ASAP", and — the line most people miss — "make a small change during the technical interview." React + TypeScript + Tailwind + PostgreSQL + Docker + 10 phases is a lot of surface area to defend live under time pressure, and none of it earns points the rubric asks for. The rubric wants: LLM usage, structured output, webhook/API integration, CRM integration, automation logic, error handling. A tight build hits all six and leaves you able to change anything on camera in 60 seconds.

A few decisions that change what I write:

How should I scope the build?
Tight core + optional extras (Recommended)
Which LLM provider will you actually have a working API key for during the demo video?
OpenAI only
GoHighLevel is paid — how do you want CRM handled?
Adapter pattern: Mock + GHL + HubSpot (Recommended)
How should the High-priority sales notification work?
Slack webhook + console fallback (Recommended)
Ran 72 commands, created 42 files, edited 8 files · 33 notes
Loaded tools
Added task: Research n8n workflow JSON schema and CRM API specs
Added task: Rewrite the phase plan with real content
Added task: Build FastAPI AI agent service
Added task: Build CRM adapters and Slack notifier
Added task: Author importable n8n workflow JSON
Added task: Build lead capture form and Docker Compose
Added task: Write tests and run end-to-end verification
Added task: Write README, demo script and package submission zip
Started task
n8n workflow JSON export format nodes typeVersion connections webhook httpRequest 2026

7 results

https://github.com/nordeim/n8n-Workflow/blob/main/authoritative_n8n_workflow_json_guide-2.md

github.com

https://tokenmix.ai/blog/n8n-workflow-json-import-export-2026

tokenmix.ai

The first source's connections format is wrong — let me verify against a real exported workflow rather than a blog.

Fetch a real n8n template workflow JSON
https://docs.n8n.io/workflows/export-import/

docs.n8n.io

n8n workflow json "connections" "main" "node" "type": "main" "index": 0 example export

8 results

Check runtimes and start n8n install in background
https://raw.githubusercontent.com/nordeim/n8n-Workflow/main/authoritative_n8n_workflow_json_guide.md

raw.githubusercontent.com

https://highlevel.stoplight.io/docs/integrations/1c1e2d0b1bf4b-upsert-contact

highlevel.stoplight.io

GoHighLevel API v2 upsert contact endpoint services.leadconnectorhq.com Version header 2021-07-28 opportunities create

6 results

https://marketplace.gohighlevel.com/docs/ghl/contacts/upsert-contact/

marketplace.gohighlevel.com

https://rocketlauncher.ai/api/contacts-create-update-recipes

rocketlauncher.ai

GoHighLevel API v2 create opportunity POST /opportunities/ pipelineId pipelineStageId contactId locationId status open

10 results

https://platform.openai.com/docs/guides/structured-outputs

platform.openai.com

https://developers.openai.com/api/docs/guides/structured-outputs

developers.openai.com

https://marketplace.gohighlevel.com/docs/ghl/opportunities/create-opportunity/index.html

marketplace.gohighlevel.com

https://developers.hubspot.com/docs/reference/api/crm/objects/contacts

developers.hubspot.com

Check n8n install progress

Research done. Building now — starting with the service skeleton and dependencies.

Create project structure and install Python dependencies
Created config.py
Created models.py
Created schema.py
Created prompts.py
Created tools.py
Created rules.py
Created classifier.py
Created __init__.py
Created db.py
Created base.py
Created mock.py
Created gohighlevel.py
Created hubspot.py
Created __init__.py
Created slack.py
Created __init__.py
Created security.py
Created service.py

Now the FastAPI app itself.

Created logging_conf.py
Created main.py
Verify the FastAPI app imports and list routes
Check n8n install status

n8n 2.35.7 is installed locally — I can validate the workflow against the real thing. Let me check the actual node versions it ships.

Inspect installed n8n node type versions
Inspect versioned n8n node types

Now the n8n workflow — I'll generate it with a script so it's reproducible, then validate it by importing into the real n8n.

Created build_workflow.py
Generate workflow and check for dangling references

Now the real test — importing it into n8n itself.

Import workflow into n8n to validate it

Needs a workflow id for CLI import — adding it.

Edited build_workflow.py
Re-import workflow into n8n

Workflow imports cleanly. Now let me actually run it end to end — API + n8n live.

Start API and test classification

Found a real bug: "We are a 40-person freight brokerage" matched the vendor-pitch pattern and forced a strong lead to Low. Fixing the signal regexes and the disqualification logic.

Edited tools.py
Edited tools.py
Edited rules.py
Edited rules.py
Read rules.py
Edited rules.py
Edited rules.py
Edited rules.py

Now let me build a proper sample-lead corpus and test the classifier against all of it.

Created sample_leads.json
Test rules classifier against sample corpus

The Medium band is too narrow — genuine-but-early leads fall through to Low. Fixing the scoring.

Edited tools.py
Edited tools.py
Edited tools.py
Edited rules.py
Edited rules.py
Edited rules.py
Re-test rules classifier
Edited tools.py
Edited sample_leads.json
Re-test classifier after regex fixes

10/10 on the corpus. Now the full pipeline, then n8n.

Test full pipeline and CRM upsert semantics
Restart the API service
Check whether API process survived
Start API detached from shell
Test full pipeline with CRM upsert semantics

Now the real end-to-end test: n8n server driving the whole workflow.

Start n8n server
Activate workflow and post a lead to the webhook
Restart n8n so the workflow activates
Check n8n status after restart
Start n8n via detached script
Post a high-priority lead through the n8n workflow

The workflow runs end to end. Two defects to fix: notified reports false on a High lead, and raw signal names leak into the output.

Inspect CRM, notifications and stats after n8n run
Count CRM upsert calls in API log
Inspect n8n execution record for retry cause
Read n8n execution data for error messages
List n8n database tables and check logs
Locate the n8n database file
Read n8n execution data for the retry cause
Parse n8n execution run data per node
List all API requests during the n8n run
Inspect the CRM node run detail

Let me isolate this properly with a controlled re-run.

Re-run workflow and count CRM calls

Strong hypothesis: the CRM response is the only one with a top-level error key, and n8n's HTTP node treats that as a failure. Testing it.

Add a diagnostic endpoint without the error key
Confirm diagnostic endpoint and restart API
Point CRM node at diagnostic endpoint and restart n8n
Test whether removing the error key stops the retries
Check the failed response
Reactivate, restart and run the diagnostic test

Confirmed and reproduced. n8n's HTTP Request node treats a JSON response containing a top-level error key as a node failure — it silently retried the CRM write 3× and only recorded the last attempt as success. Fixing that plus the two other defects.

Remove the temporary diagnostic endpoint
Edited models.py
Edited models.py
Rename error field across the codebase
Edited slack.py
Edited slack.py

Now the workflow-side fixes: the error rename, the notified bug, and readable signal labels.

Edited build_workflow.py
Edited build_workflow.py
Edited build_workflow.py
Edited build_workflow.py
Edited build_workflow.py

Now the readable signal labels in the rules engine.

Edited rules.py
Rebuild workflow, restart API and n8n
Reset both services with the fixed build
Check service state after reset
Verify the fixes end to end through n8n

All three defects fixed. Now testing every branch including the failure paths.

Run all sample leads through n8n

Now the error paths — invalid input, and the AI service being down.

Test validation error branch
Edited build_workflow.py
Rebuild and reload the workflow
Test the AI-service-down fallback path

The full degradation chain works. One mislabel to fix, then I'll write the remaining pieces and do a final verification pass.

Edited build_workflow.py
Edited build_workflow.py

Now the tests, frontend, Docker setup and configuration.

Created conftest.py
Created test_agent.py
Created test_crm_and_notify.py
Created test_api.py
Run the test suite

67 tests pass. Now the lead portal, Docker setup and config.

Created index.html
Edited index.html
Syntax-check the frontend JavaScript

Now Docker, env config and the Makefile.

Created .env.example
Created Dockerfile
Created requirements.txt
Created docker-compose.yml
Created Makefile
Created demo.py
Created hubspot_setup.py

Now the rewritten phase plan — replacing the 38 identical boilerplate files with real content.

Created PLAN.md
Created phase-04-n8n-workflow.md

Now a full verification run to capture real evidence, then the README.

Rebuild workflow and restart the whole stack
Run the demo script through n8n
Verify counts, idempotency and persistence

One more: proving the real LLM tool-calling path works, using a local OpenAI-compatible stub.

Created fake_openai.py
Start the OpenAI stub and point the API at it
Check API with LLM stub configured
Verify the real agent tool-calling loop
Test error, refusal and malformed-output paths over real HTTP
Run failure-mode tests against the live service
Read failure-mode test output

All failure modes verified. Now the documentation.

Created VERIFICATION.md
Created README.md
Created DEMO_SCRIPT.md
Created .gitignore
Created README.md

Final verification pass, then packaging.

Final tests, compile check and repo listing
Final clean run of the API-only path
Run demo against the API-only path
Validate requirements.txt in a clean venv

Docker is available — let me build-test the image rather than ship an unverified Dockerfile.

Build the Docker image

No Docker daemon in this sandbox, so I'll validate what I can and say so honestly rather than claim it's build-tested.

Validate docker-compose YAML
Edited VERIFICATION.md
Add honest verification caveat to README

Now packaging the final submission.

Package and verify the submission zip
Shared files
Writing project file spark-ai-assessment-notes.md

Done. The zip is a complete, working assessment — not a scaffold.

On your phase plan: I rewrote it rather than implemented it. All 38 phase docs were byte-identical boilerplate, and the root architecture was circular (API → n8n → AI agent living inside the API). The fixed design makes n8n the orchestrator and FastAPI a stateless AI microservice, which is what makes the workflow export — a required deliverable — the actual centerpiece.

What's in it:

Real agent loop, not one prompt: the model calls three deterministic tools (signal extraction, email-domain analysis, CRM history lookup), then returns a strict JSON-Schema-constrained answer. No framework — the loop is ~90 lines you can explain line by line.
20-node n8n workflow, generated by a script so it's reproducible. Imported into a real n8n 2.35.7 and run end to end.
GoHighLevel made optional properly: one CrmAdapter interface, three implementations behind CRM_PROVIDER. The mock implements real upsert-by-email semantics against SQLite; GHL v2 is fully written; HubSpot's free tier is there because it's the one a reviewer can actually run without paying.
67 tests, weighted toward failure paths. Passes from a clean unzip.

Two bugs I only found by running it — and these are your best interview material:

n8n was silently writing every contact to the CRM three times. Its own execution log said the node ran once, successfully. Only the API access log gave it away: three calls exactly 2000ms apart. Cause: n8n's HTTP Request node treats a top-level error key in a JSON response as a node failure — on HTTP 200, with an empty-string value — and retryOnFail records only the final attempt. I isolated it with a one-variable test and fixed it by renaming the field. Against a paid GoHighLevel account that was 3× the API calls per lead.

Second, notified always reported false on High leads, because the response was built from a node upstream of the notification decision.

Two things I'd push back on before you submit:

The ten sample leads are a smoke test, not an evaluation. If anyone asks "how accurate is it?", the honest answer is "I don't know yet — that needs a few hundred labelled leads and a confusion matrix." Saying that beats quoting 10/10.

I could not build-test the Docker image (CLI present, no daemon in this sandbox), and there were no live calls to OpenAI, GHL, HubSpot or Slack. That's stated plainly in both the README and docs/VERIFICATION.md rather than glossed. Run docker compose up --build yourself before recording — it's the one untested path, and it's the first thing an assessor will try.

DEMO_SCRIPT.md has the video timed to 3:00, with the failure-injection segment at 1:30 — that's the part that separates a working demo from a considered one.s