# Complete Beginner's Setup & Operation Guide

An exhaustive, step-by-step walkthrough for setting up, configuring, deploying, and testing the **Spark AI Lead Qualification Agent & n8n Automation**.

---

## 1. The Big Picture: What Are We Actually Building?

Imagine a busy company where hundreds of people send messages saying: *"I want to buy your software!"*, *"Can I have a job?"*, or *"You won a free Bitcoin!"*.

If a human had to read every message, they would waste hours. We are setting up an **automated AI assembly line** that does this in under a second:

```
  [1. The Website Form]          Someone visits your website and types a message.
           │
           ▼
  [2. n8n (The Office Manager)]  Catches the message and moves the digital paperwork.
           │
           ▼
  [3. The AI Brain (FastAPI)]    Reads the message, detects buying signals, and decides:
                                 "Is this High, Medium, or Low priority?"
           │
           ▼
  [4. The CRM (Filing Cabinet)]  Saves their name & email into a contact list (HubSpot or Mock).
           │
           ▼
  [5. Slack (The Walkie-Talkie)] If it's a HOT lead, alerts the sales team on Slack!
```

---

## 2. Prerequisites: What You Need on Your Computer

Before touching any code, make sure you have these installed on your Windows PC:

1. **Python (Version 3.10, 3.11, or 3.12)**:
   - Download from [python.org](https://www.python.org/downloads/).
   - ⚠️ **CRITICAL STEP DURING INSTALL**: On the very first install screen, check the box that says **"Add python.exe to PATH"**. If you miss this, your computer won't recognize Python commands in PowerShell.
2. **Git**:
   - Download from [git-scm.com](https://git-scm.com/download/win). Just click "Next" on all default options.
3. **Docker Desktop (Recommended for the easiest 1-click launch)**:
   - Download from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/).
   - Install it, restart your computer if asked, and open the Docker Desktop app so the engine starts running in the background.

---

## 3. How to Get Your API Keys (Step-by-Step)

> [!NOTE]
> **You can run this entire project with ZERO API keys**! If you leave everything blank, the system automatically uses its local mock database, local rules engine, and console notifications. However, if you want real AI, real HubSpot, and real Slack messages, follow these steps to get your keys.

---

### Step 3.1: OpenAI API Key (The AI Brain)
This gives the agent access to GPT-4o-mini to read messages and draft personalized emails.

1. Go to [platform.openai.com](https://platform.openai.com/) and sign up or log in.
2. In the left sidebar, click on **API Keys** (or go directly to [platform.openai.com/api-keys](https://platform.openai.com/api-keys)).
3. Click the green button: **+ Create new secret key**.
4. Name it `spark-lead-agent` and click **Create secret key**.
5. Copy the key immediately (it looks like `sk-proj-abc123xyz...`). Store it in a temporary text file because OpenAI will never show it to you again.

---

### Step 3.2: Slack Webhook (The Sales Walkie-Talkie)
This lets our system send real Slack alerts into your company channel.

1. Go to [slack.com](https://slack.com/) and log into your workspace (you can create a free workspace in 2 minutes if you don't have one).
2. In Slack, create a channel named `#sales-leads`.
3. Open your web browser and go to [api.slack.com/apps](https://api.slack.com/apps).
4. Click the green button: **Create New App** ➔ Choose **From scratch**.
5. App Name: `Spark Lead Bot`. Select your workspace ➔ Click **Create App**.
6. On the left menu under *Features*, click **Incoming Webhooks**.
7. Toggle the switch to **Activate Incoming Webhooks** (it turns green/On).
8. Scroll down to the bottom and click **Add New Webhook to Workspace**.
9. Select your `#sales-leads` channel from the dropdown and click **Allow**.
10. You will now see a **Webhook URL** that looks like:
    `https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX`
11. Click **Copy**.

---

### Step 3.3: HubSpot CRM Token (The Free Contact Filing Cabinet)
HubSpot has a 100% free tier where you can see contacts and deals created live.

1. Go to [hubspot.com](https://www.hubspot.com/) and create a free account (choose CRM / Sales).
2. Inside HubSpot, click the **Settings gear icon (⚙️)** in the top navigation bar.
3. In the left-hand menu, scroll down to **Integrations** and click **Private Apps**.
4. Click **Create a private app**.
5. In the **Basic Info** tab: Name it `Spark AI Agent`.
6. Click the **Scopes** tab (this defines what the app is allowed to do):
   - Search for `contacts` in the search box.
   - Check the boxes for:
     - `crm.objects.contacts.read`
     - `crm.objects.contacts.write`
7. Click **Create app** in the top right, then click **Continue creating**.
8. A popup will show your token (it starts with `pat-na1-...`). Click **Show token**, then click **Copy**.

---

### Step 3.4: What about GoHighLevel (GHL)?
GoHighLevel is a paid software ($97+/month). 
- If you don't have a paid GHL account, **don't worry!** The project was specifically built with a **Mock CRM** and a **HubSpot adapter** so anyone can test it for free.
- If you *do* have GoHighLevel:
  - In GHL, go to **Settings** ➔ **Private Integrations** ➔ Create a token with `contacts.write` and `opportunities.write`.
  - Your `GHL_LOCATION_ID` is found in your browser URL (`/v2/location/<YOUR_LOCATION_ID>/`).

---

## 4. Setting Up Your Configuration File (`.env`)

Every app uses a special settings file named `.env` (short for "environment").

1. Open PowerShell and navigate to your project folder:
   ```powershell
   cd d:\spark-ai-lead-agent\spark-ai-lead-agent
   ```
2. Make a copy of the template file `.env.example` and name it `.env`:
   ```powershell
   cp .env.example .env
   ```
3. Open `.env` in VS Code or Notepad. Here is what each line means:

```ini
# --- System Settings ---
ENVIRONMENT=development
LOG_LEVEL=INFO
DATABASE_PATH=data/leads.db

# --- Security ---
# Keep empty for local testing so you don't have to enter passwords
API_KEY=
CORS_ALLOW_ORIGINS=*

# --- OpenAI Settings ---
LLM_PROVIDER=openai
# PASTE YOUR OPENAI KEY HERE (or leave blank to use the free offline rules engine):
OPENAI_API_KEY=sk-proj-your-actual-key-here
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=https://api.openai.com/v1

# --- CRM Provider Settings ---
# Choose ONE: "mock" (offline test), "hubspot" (free real CRM), or "gohighlevel"
CRM_PROVIDER=mock

# If you chose hubspot above, paste your HubSpot private app token here:
HUBSPOT_TOKEN=pat-na1-your-actual-token-here

# --- Slack Alerts ---
# PASTE YOUR SLACK WEBHOOK URL HERE (or leave blank for terminal output):
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T000/B000/XXXXX
NOTIFY_ON_PRIORITIES=High

# --- n8n Network Config ---
API_BASE_URL=http://api:8000
N8N_BLOCK_ENV_ACCESS_IN_NODE=false
GENERIC_TIMEZONE=Asia/Dubai
```

Save and close the file.

---

## 5. How to Launch Everything

### Method A: The 1-Click Docker Method (Recommended)

Make sure Docker Desktop is open and running on your computer.

1. In PowerShell, start the entire container stack:
   ```powershell
   docker compose up --build -d
   ```
   *Docker will automatically download and start Python FastAPI, n8n, and the web frontend.*

2. Load the workflow into n8n:
   ```powershell
   docker compose exec n8n n8n import:workflow --input=/workflows/workflow.lead-qualification.json
   ```

3. If you configured HubSpot (`CRM_PROVIDER=hubspot`), create the custom AI fields in HubSpot by running:
   ```powershell
   python scripts/hubspot_setup.py
   ```

You are ready! Your services are live at:
- **Lead Intake Web Form**: <http://localhost:3000>
- **n8n Automation Canvas**: <http://localhost:5678>
- **API Documentation & Swagger UI**: <http://localhost:8000/docs>

---

### Method B: The Native Python Method (Without Docker)

If you don't have Docker Desktop installed, you can run everything natively:

1. **Create and activate a Python virtual environment**:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
2. **Install the dependencies**:
   ```powershell
   pip install -r requirements.txt
   ```
3. **Start the FastAPI backend**:
   ```powershell
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```
4. **Open the web form**:
   - Simply open `frontend/index.html` in your browser (Google Chrome or Edge).
   - In the dropdown **"Submit via"**, choose **"API directly (no n8n needed)"**.

---

## 6. Complete n8n Walkthrough: Every Node Explained

Open your browser to <http://localhost:5678>.

### How to Activate the Workflow
1. When you open n8n for the first time, it might ask you to create an admin account (enter any username/password you want).
2. In the left menu, click **Workflows**.
3. You will see **Spark AI - Lead Qualification Agent**. Click to open it.
4. In the top right corner, click the toggle switch from **Inactive** to **Active** (turns orange/green).

---

### Node-by-Node Tour: What Each Node Does

The workflow has **20 nodes**. Here is the plain English breakdown of each one:

```
[1. Webhook] ──► [2. Validate Lead] ──(Valid)──► [5. Qualify Lead (AI)] ──► [7. Prepare CRM]
                        │                                  │
                    (Invalid)                           (Error)
                        ▼                                  ▼
                [3. Reject Lead]               [6. Fallback Heuristic]
                        ▼
                [4. Respond 400]
```

#### Intake & Validation Stage
1. **Node 1: Lead Intake Webhook**
   - *What it is*: A digital mailbox listening at `POST /webhook/lead-intake`.
   - *What it does*: When someone hits the "Submit" button on the website, this node receives the data (`name`, `email`, `company`, `message`).
2. **Node 2: Validate & Normalise Lead** (JavaScript Code Node)
   - *What it does*: Checks if the person actually gave a valid email and didn't leave the message empty or write an 8,000+ character spam block.
   - *Why it matters*: If someone submits junk, it immediately stops before wasting money on OpenAI calls.
3. **Node 3: Reject Invalid Lead** & **Node 4: Respond 400**
   - *What it does*: If Node 2 fails, these nodes send a clean error message back to the user's browser: `"email is not a valid address"`.

---

#### AI Qualification Stage
5. **Node 5: Qualify Lead (AI Agent)** (HTTP Request Node)
   - *What it calls*: `POST http://api:8000/api/v1/classify`.
   - *What it does*: Hands the lead to the FastAPI service. The AI model checks buying signals (budget, timeline, authority) and returns priority: `High`, `Medium`, or `Low`.
   - *Safety feature*: If the AI takes too long or fails, it tries 3 times with a 2-second wait between tries.
6. **Node 6: Fallback Classification** (JavaScript Code Node)
   - *What it does*: If the AI service is completely offline, this node runs emergency keyword matching inside n8n itself. A lead is **never** dropped!

---

#### CRM Sync Stage
7. **Node 7: Prepare CRM Payload** (JavaScript Code Node)
   - *What it does*: Decides what stage in the sales pipeline this person belongs to:
     - `High` ➔ **Hot - Contact Today**
     - `Medium` ➔ **Warm - Nurture**
     - `Low` ➔ **Cold - Archive**
     - Job seekers or spam ➔ **Not a Sales Lead**
8. **Node 8: Sync Contact to CRM** (HTTP Request Node)
   - *What it calls*: `POST http://api:8000/api/v1/crm/upsert`.
   - *What it does*: Saves the contact and AI notes into your CRM (Mock, HubSpot, or GHL).
9. **Node 9: Attach CRM Result** & **Node 10: Handle CRM Failure**
   - *What it does*: Keeps the pipeline moving forward even if the CRM is temporarily down.

---

#### Notification & Routing Stage
11. **Node 11: Route by Priority** (Switch Node)
    - *What it does*: Acts like a train track switch with 3 tracks:
      - **Track 1 (High)**: Goes toward the sales alert.
      - **Track 2 (Medium)**: Goes to Node 17 (No alert needed).
      - **Track 3 (Low)**: Goes to Node 17 (No alert needed).
12. **Node 12: Slack Webhook Configured?** (If Node)
    - *What it does*: Checks if you provided a real `SLACK_WEBHOOK_URL`.
13. **Node 13: Build Slack Alert** & **Node 14: Post Slack Alert**
    - *What it does*: Assembles a card with emojis (`🔥 High priority lead`), the lead's company, why the AI chose High, and the suggested email reply, then posts it to your Slack channel!
14. **Node 15: Notify via API (fallback)**
    - *What it does*: If you didn't configure Slack, it tells FastAPI to print a neat box to your terminal console instead.
15. **Node 16: Mark Notified** & **Node 17: No Alert Needed**
    - *What it does*: Records whether sales was paged or if the alert was skipped to prevent alert fatigue.

---

#### Persistence & Response Stage
18. **Node 18: Record Outcome** (HTTP Request Node)
    - *What it calls*: `POST http://api:8000/api/v1/leads/record`.
    - *What it does*: Saves the complete history, classification, and agent execution trace into SQLite for audit logs.
19. **Node 19: Build Response** & **Node 20: Respond 200**
    - *What it does*: Packages the final answer and sends it back to the user's browser in less than a second.

---

## 7. How to Test & Use the System

1. Open **<http://localhost:3000>** in your browser.
2. At the top under **"Submit via"**, make sure **"n8n webhook"** is selected.
3. You will see 4 quick-fill sample buttons:
   - Click **"High: budget + deadline"**:
     - *Lead*: Sara Malik, Northwind Logistics.
     - *Message*: *"40-person brokerage, budget $15k, need quote automation before end of Q4. Can we book a call?"*
4. Click the blue button: **Qualify this lead**.
5. **Watch what happens**:
   - The right side updates with a red **High priority** badge.
   - It explains **Why**: names a $15k budget, a Q4 timeline, and a call request.
   - It provides a custom drafted email addressed to Sara.
   - It shows the CRM stage: **Hot - Contact Today**.
   - Check your Slack channel: you will see a Block Kit alert appear!
6. Try clicking **"Low: job application"**:
   - Notice that it scores **Low**, stage becomes **Not a Sales Lead**, and **no Slack alert is sent**.

---

## 8. How to Test Failure Modes (The "Impressive Demo" Trick)

To verify that the system is bulletproof:

1. Open PowerShell and stop the AI backend container:
   ```powershell
   docker compose stop api
   ```
2. Go back to <http://localhost:3000> and click **"High: budget + deadline"** ➔ **Qualify this lead**.
3. Notice:
   - The form does **not** crash or give a generic error.
   - A yellow notice appears: **"Degraded result. The AI service was unavailable, so the deterministic fallback scored this lead. It was still filed and routed - nothing was dropped."**
4. Restart the API:
   ```powershell
   docker compose start api
   ```

---

## 9. Quick Troubleshooting Checklist

| Issue | What Happened | How to Fix It |
|---|---|---|
| Web form says "Could not reach endpoint" | The n8n webhook isn't receiving data | Make sure the workflow is toggled to **Active** inside n8n (<http://localhost:5678>). |
| Result says `degraded: True` | OpenAI key is missing or invalid | Check your `OPENAI_API_KEY` in `.env`. If running without a key, this is normal behavior. |
| Slack message doesn't appear | Webhook URL is wrong or empty | Check that `SLACK_WEBHOOK_URL` in `.env` starts with `https://hooks.slack.com/services/`. |
| HubSpot says 400 invalid property | Custom AI fields haven't been created yet | Run `python scripts/hubspot_setup.py` once to register the properties in your HubSpot portal. |
