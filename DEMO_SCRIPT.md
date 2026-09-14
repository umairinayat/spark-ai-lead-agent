# Demo video script — 2 to 3 minutes

The brief asks for 2–3 minutes. That is not enough time to tour the code, so
this script spends it on the three things an assessor is actually scoring:
**it works**, **it does not fall over**, and **you understand why you built it
this way**.

---

## Before you hit record

```bash
cp .env.example .env
docker compose up --build -d
make import-workflow
# open http://localhost:5678, open the workflow, toggle it Active
curl localhost:8000/health        # confirm it responds
rm -f data/leads.db*              # start from an empty table
```

Have these open in tabs, in this order:

1. The lead form — <http://localhost:3000>
2. n8n, workflow canvas — <http://localhost:5678>
3. A terminal, in the project directory
4. Slack, if you have `SLACK_WEBHOOK_URL` set (worth the two minutes to set up
   — a real Slack message lands much better than a console print)

Test your microphone. Speak slower than feels natural.

---

## 0:00–0:20 — What it is

> "This is an AI lead qualification agent wired into an n8n automation. A lead
> comes in through a webhook, an agent classifies it High, Medium or Low with
> a reason, drafts a personalised follow-up, files it in the CRM at the right
> pipeline stage, and pages sales on Slack if it is High."

Show the canvas in n8n while you say it. Trace the path with the cursor:
webhook → AI → CRM → the priority switch → Slack.

> "n8n is the orchestrator. The AI service behind it is a stateless FastAPI
> microservice, so the classify step has no side effects and n8n can retry it
> safely."

---

## 0:20–1:00 — A High-priority lead, live

Switch to the form. Click the **"High: budget + deadline"** sample button
rather than typing — typing burns fifteen seconds of a three-minute video.

Submit. While it runs:

> "That posts to the n8n webhook. The workflow validates it, calls the agent,
> writes to the CRM, and decides whether this is worth interrupting the sales
> team for."

When the result appears, point at things in this order:

> "High priority — and here is **why**: it names a budget, a Q4 deadline, and
> asks for a call. The reason cites the actual evidence, it is not generic.
>
> Here is the follow-up message it drafted — addressed to Sara by name and
> referencing her timeline, not a template.
>
> It went into the CRM as a new contact at the *Hot — Contact Today* stage.
> And sales was paged."

Cut to Slack and show the alert. If you have no Slack webhook, show the
notification in the terminal instead and say it falls back to the log.

---

## 1:00–1:30 — It says no as well as yes

Back to the form. Click the **"Low: job application"** sample. Submit.

> "Same pipeline, different call. This is a job application, so it is Low, and
> the intent classification routes it to *Not a Sales Lead* rather than just
> parking it at the bottom of the pipeline. No Slack alert — nobody gets
> interrupted."

If you have time, add the agency-spam sample too:

> "Same for cold agency pitches. Anything that would train the sales team to
> ignore their alerts gets filed silently."

> "That is the part that actually matters. A classifier that says High to
> everything is worse than no classifier, because people stop trusting it."

---

## 1:30–2:10 — What happens when it breaks

This is the segment that separates a working demo from a considered one.
**Do not skip it.**

In the terminal:

```bash
docker compose stop api
```

> "I have just stopped the AI service completely."

Submit the High-priority sample again from the form.

> "n8n retries three times, then takes its error branch and falls back to a
> keyword heuristic that runs inside the workflow itself. The lead is still
> classified, still staged, still answered — and it is flagged as degraded so
> nobody mistakes a keyword guess for a model judgement."

Point at the degraded banner in the result panel.

> "There are two independent fallback layers here. Inside the service, if
> OpenAI errors or returns output that fails schema validation, it retries and
> then uses a deterministic scorer. Inside the workflow, if the whole service
> is unreachable, this happens. A lead lost to a 503 is a lost deal."

```bash
docker compose start api
```

---

## 2:10–2:40 — The agent, and one thing you found

Open `app/agent/tools.py` or the classifier.

> "It is a real agent loop, not one prompt. The model calls tools first —
> keyword signal extraction, email domain analysis, and a CRM lookup for
> whether this person has contacted us before — then commits to an answer
> constrained by a strict JSON schema. The tools are deterministic, so the
> fallback scorer reasons over exactly the same evidence the model sees."

Then, briefly — this is the strongest thirty seconds in the video:

> "One thing worth mentioning. When I ran this end to end I found the CRM was
> being written three times per lead, while n8n's own execution log said the
> node ran once. It turns out n8n's HTTP node treats a top-level `error` key
> in a JSON response as a failure — on a 200, with an empty string — so
> `retryOnFail` fired twice more. I only caught it in the API access log.
> Renaming the field fixed it, and there is a regression test so it cannot
> come back. Against a paid GoHighLevel account that was three API calls per
> lead."

---

## 2:40–3:00 — GoHighLevel, and close

> "On GoHighLevel: it is a paid product, so rather than build something that
> cannot be reviewed without a licence, the CRM sits behind one interface with
> three implementations — a local mock that implements real upsert semantics,
> the GoHighLevel v2 adapter, and HubSpot, whose free tier means you can
> actually run it. It is one environment variable to switch, and the workflow
> does not change."

```bash
make test
```

> "Sixty-seven tests, including the failure paths and the workflow export
> itself. The README has the full design reasoning and the limitations I would
> fix next. Thanks for watching."

---

## If you have four minutes rather than three

Add, after the Low-priority segment:

- Re-submit the same lead and show `created: false` plus "Previously contacted
  us" appearing in the reason — the agent's CRM-history tool closing the loop.
- `make demo` — all ten labelled samples through the pipeline in one table.

## Recording notes

- **Do not read this script aloud.** Know the five beats, speak normally.
- Zoom the browser to ~125%. Text that is unreadable at 720p wastes the take.
- One take is fine. A small stumble matters far less than running long.
- If something genuinely breaks on camera, say what you think happened and
  move on — that reads as competence, not failure.
- Keep it under 3:00. Going long reads as not knowing what matters.
