# System Diagrams (Mermaid)

This file contains the complete visual diagrams for the **Spark AI Lead Qualification Agent & n8n Automation**. These diagrams render automatically in GitHub, VS Code Markdown Preview, and any Mermaid-compatible viewer.

---

## 1. Use Case Diagram

Shows the user personas, external actors, and key capabilities of the AI triage platform.

```mermaid
graph LR
    classDef actor fill:#1f242d,stroke:#58a6ff,stroke-width:2px,color:#ffffff;
    classDef usecase fill:#161b22,stroke:#30363d,stroke-width:1px,color:#e6edf3;

    Visitor["Inbound Lead - Visitor"]:::actor
    SalesRep["Sales Lead - Closer"]:::actor
    SDR["SDR Team"]:::actor
    SupportRep["Support Team"]:::actor

    subgraph System["Spark AI Lead Agent Platform"]
        UC1["Submit Web Enquiry"]:::usecase
        UC2["Validate & Reject Bad Input - 400"]:::usecase
        UC3["AI Evidence Gathering - Tool Calling"]:::usecase
        UC4["Classify Priority & Intent - Structured JSON"]:::usecase
        UC5["Upsert Contact in HubSpot / Mock CRM"]:::usecase
        UC6["Stage Assignment - Hot / Warm / Cold / Support"]:::usecase
        UC7["Page Hot Leads on Slack - Block Kit"]:::usecase
        UC8["Fallback on Upstream Outage - Degraded"]:::usecase
        UC9["Audit Traces & Historical Logging"]:::usecase
    end

    Visitor --> UC1
    UC1 --> UC2
    UC2 --> UC3
    UC3 --> UC4
    UC4 --> UC5
    UC4 --> UC6
    UC6 --> UC7
    UC3 -.-> UC8
    UC4 --> UC9

    UC7 --> SalesRep
    UC6 --> SDR
    UC6 --> SupportRep
```

---

## 2. Complete System Architecture & Data Flow

Illustrates the end-to-end cloud pipeline across the Web Form, Railway n8n Workflow, FastAPI Microservice, HubSpot CRM, and Slack.

```mermaid
flowchart TD
    classDef client fill:#1e293b,stroke:#38bdf8,stroke-width:2px,color:#f8fafc;
    classDef n8n fill:#2e1065,stroke:#a855f7,stroke-width:2px,color:#f8fafc;
    classDef api fill:#064e3b,stroke:#10b981,stroke-width:2px,color:#f8fafc;
    classDef crm fill:#7c2d12,stroke:#f97316,stroke-width:2px,color:#f8fafc;
    classDef slack fill:#831843,stroke:#ec4899,stroke-width:2px,color:#f8fafc;

    Form["Web Intake Form - index.html"]:::client
    
    subgraph n8n_Engine["n8n Orchestration Spine - Railway Cloud"]
        N1["Node 1: Webhook Listener - lead-intake"]:::n8n
        N2{"Node 2: Code Validation - Name, Email, Length"}:::n8n
        N4["Node 4: Respond HTTP 400 - Bad Request"]:::n8n
        N5["Node 5: HTTP Call: POST /api/v1/classify"]:::n8n
        N6["Node 6: Fallback JS Heuristic - Keyword Scorer"]:::n8n
        N7["Node 7: Prepare CRM Payload & Stages"]:::n8n
        N8["Node 8: HTTP Call: POST /api/v1/crm/upsert"]:::n8n
        N10["Node 10: Handle CRM Failure - Degraded Record"]:::n8n
        N11{"Node 11: Switch on Priority"}:::n8n
        N12{"Node 12: Slack Configured?"}:::n8n
        N14["Node 14: Post Slack Block Kit Alert"]:::n8n
        N15["Node 15: Fallback Terminal / DB Notify"]:::n8n
        N17["Node 17: No Alert Needed - Silence Low/Med"]:::n8n
        N18["Node 18: HTTP Call: POST /api/v1/leads/record"]:::n8n
        N20["Node 20: Respond HTTP 200 to Form"]:::n8n
    end

    subgraph FastAPI_Microservice["FastAPI AI Service - Stateless Microservice"]
        Agent["LeadAgent Loop - explicit Python"]:::api
        T1["Tool: extract_intent_signals - Regex Vocab"]:::api
        T2["Tool: analyse_email_domain - Domain Match"]:::api
        T3["Tool: lookup_existing_contact - SQLite History"]:::api
        RulesFallback["Deterministic Rules Engine - rules.py"]:::api
        OpenAI["OpenAI GPT-4o-mini - Strict JSON Schema"]:::api
        DB[("SQLite Leads & Run Traces")]:::api
    end

    subgraph ThirdParty["Third-Party Integrations"]
        HubSpot["HubSpot CRM v3 - Contacts & Custom AI Fields"]:::crm
        Slack["Slack Channel #sales-lead - Incoming Webhook"]:::slack
    end

    Form -->|POST Lead Payload| N1
    N1 --> N2
    N2 -->|Invalid| N4
    N2 -->|Valid| N5

    N5 -->|HTTP| Agent
    Agent --> T1
    Agent --> T2
    Agent --> T3
    Agent --> OpenAI
    OpenAI -.->|5xx / Timeout / Refusal| RulesFallback
    Agent -->|Structured Outcome| N5
    N5 -.->|FastAPI Offline| N6

    N5 --> N7
    N6 --> N7
    N7 --> N8
    N8 -->|Upsert Request| HubSpot
    N8 -.->|HubSpot Error| N10

    N8 --> N11
    N10 --> N11

    N11 -->|High Priority| N12
    N11 -->|Medium Priority| N17
    N11 -->|Low Priority| N17

    N12 -->|Configured| N14
    N12 -->|Unconfigured| N15
    N14 -->|HTTP Webhook| Slack

    N14 --> N18
    N15 --> N18
    N17 --> N18

    N18 -->|Persist Audit| DB
    N18 --> N20
    N20 -->|Display Result Card| Form
```

---

## 3. Chronological Sequence Diagram

Illustrates the exact request-response lifecycle when a high-priority lead is processed.

```mermaid
sequenceDiagram
    autonumber
    actor User as Lead (Website)
    participant Form as Web Form
    participant n8n as n8n (Railway)
    participant API as FastAPI Agent
    participant OpenAI as OpenAI GPT-4o
    participant CRM as HubSpot CRM
    participant Slack as Slack (#sales-lead)

    User->>Form: Submits inquiry (Name, Email, Message)
    Form->>n8n: POST /webhook/lead-intake
    Note over n8n: Node 2: Validate fields & email format
    
    n8n->>API: POST /api/v1/classify
    activate API
    API->>API: Run deterministic tools (Regex, Domain, History)
    API->>OpenAI: Chat completion with tool evidence & JSON schema
    OpenAI-->>API: Returns structured priority, intent & reply
    API-->>n8n: HTTP 200 (ClassifyResponse)
    deactivate API

    Note over n8n: Node 7: Map priority to pipeline stage
    n8n->>API: POST /api/v1/crm/upsert
    activate API
    API->>CRM: Upsert contact & custom AI fields
    CRM-->>API: Contact ID created
    API-->>n8n: HTTP 200 (CrmContact)
    deactivate API

    Note over n8n: Node 11: Switch on Priority (High)
    n8n->>Slack: POST Block Kit Alert (Incoming Webhook)
    Slack-->>n8n: HTTP 200 OK

    n8n->>API: POST /api/v1/leads/record
    API-->>n8n: Stored in SQLite audit log
    
    n8n-->>Form: HTTP 200 JSON (Result + Follow-up + CRM link)
    Form-->>User: Displays High badge, custom reply, and status
```
