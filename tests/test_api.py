"""API-level tests, including the n8n contract and the workflow export itself.

`test_workflow.py`-style checks live here too: the workflow JSON is a shipped
deliverable, so it gets the same treatment as code.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi.testclient import TestClient

WORKFLOW_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "n8n" / "workflow.lead-qualification.json"
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("CRM_PROVIDER", "mock")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("API_KEY", "")

    import app.config as config_module
    config_module.get_settings.cache_clear()
    import importlib
    import app.main as main_module
    importlib.reload(main_module)
    return TestClient(main_module.app)


HIGH_LEAD = {
    "name": "Sara Malik",
    "email": "sara@northwind-logistics.com",
    "company": "Northwind Logistics",
    "message": (
        "We are a 40-person freight brokerage and need to automate quote "
        "follow-ups from our CRM. Budget around $15k, live before end of Q4. "
        "Can we book a call this week?"
    ),
}


# ------------------------------------------------------------------- ops
def test_health_reports_active_configuration(client):
    r = client.get("/health")
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["crm_provider"] == "mock"
    assert cfg["llm_live"] is False


def test_config_exposes_the_stage_map(client):
    body = client.get("/api/v1/config").json()
    assert body["stages_by_priority"]["High"] == "Hot - Contact Today"
    assert body["notify_on"] == ["High"]


def test_health_leaks_no_secrets(client):
    body = json.dumps(client.get("/health").json())
    for secret in ("api_key", "token", "webhook_url", "sk-"):
        assert secret not in body.lower() or secret == "api_key"
    assert "openai_api_key" not in body


# --------------------------------------------------------------- classify
def test_classify_returns_structured_output(client):
    r = client.post("/api/v1/classify", json=HIGH_LEAD)
    assert r.status_code == 200
    body = r.json()
    c = body["classification"]
    assert c["priority"] in {"High", "Medium", "Low"}
    assert c["reason"] and c["follow_up_message"]
    assert body["agent"]["suggested_stage"]
    assert isinstance(body["agent"]["should_notify"], bool)


def test_classify_has_no_side_effects(client):
    """n8n retries this node, so it must not create records."""
    before = client.get("/api/v1/leads").json()["count"]
    client.post("/api/v1/classify", json=HIGH_LEAD)
    client.post("/api/v1/classify", json=HIGH_LEAD)
    assert client.get("/api/v1/leads").json()["count"] == before


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "A", "email": "not-an-email", "message": "hi there"},
        {"name": "", "email": "a@b.com", "message": "hi"},
        {"name": "A", "email": "a@b.com", "message": ""},
        {"email": "a@b.com", "message": "hi"},
    ],
)
def test_invalid_leads_are_rejected_with_422(client, payload):
    assert client.post("/api/v1/classify", json=payload).status_code == 422


# ------------------------------------------------------------ full chain
def test_full_pipeline_creates_lead_crm_record_and_notification(client):
    r = client.post("/api/v1/leads", json=HIGH_LEAD)
    assert r.status_code == 201
    body = r.json()

    assert body["classification"]["priority"] == "High"
    assert body["crm"]["provider"] == "mock"
    assert body["crm"]["created"] is True
    assert body["crm"]["stage"] == "Hot - Contact Today"
    assert body["notification"]["sent"] is True

    stored = client.get(f"/api/v1/leads/{body['lead_id']}").json()
    assert stored["lead"]["priority"] == "High"
    assert stored["agent_runs"], "the agent trace must be persisted"


def test_low_priority_lead_is_stored_but_not_announced(client):
    r = client.post("/api/v1/leads", json={
        "name": "Ahmed Hassan", "email": "ahmed@gmail.com", "company": "",
        "message": "I am a fresh graduate applying for an internship, CV attached.",
    })
    body = r.json()
    assert body["classification"]["priority"] == "Low"
    assert body["notification"]["sent"] is False
    assert body["crm"]["stage"] == "Not a Sales Lead"


def test_stats_aggregate_correctly(client):
    client.post("/api/v1/leads", json=HIGH_LEAD)
    stats = client.get("/api/v1/stats").json()
    assert stats["total_leads"] >= 1
    assert "High" in stats["by_priority"]


def test_lead_not_found_returns_404(client):
    assert client.get("/api/v1/leads/lead_doesnotexist").status_code == 404


# ------------------------------------------------------- n8n step contract
def test_n8n_step_endpoints_compose(client):
    """Walk the same four calls the workflow makes, in order."""
    classified = client.post("/api/v1/classify", json=HIGH_LEAD).json()
    classification = classified["classification"]

    crm = client.post("/api/v1/crm/upsert", json={
        "lead": HIGH_LEAD, "classification": classification,
    }).json()
    assert crm["contact_id"]

    notification = client.post("/api/v1/notify", json={
        "lead": HIGH_LEAD, "classification": classification, "crm": crm,
        "lead_id": "lead_test",
    }).json()
    assert notification["sent"] is True

    recorded = client.post("/api/v1/leads/record", json={
        "lead": HIGH_LEAD, "classification": classification, "lead_id": "lead_test",
        "crm": crm, "notification": notification, "agent": classified["agent"],
        "source": "n8n",
    }).json()
    assert recorded["stored"] is True

    stored = client.get("/api/v1/leads/lead_test").json()
    assert stored["lead"]["pipeline_source"] == "n8n"


def test_api_key_is_enforced_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setenv("API_KEY", "s3cret")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    import app.config as config_module
    config_module.get_settings.cache_clear()
    import importlib
    import app.main as main_module
    importlib.reload(main_module)
    c = TestClient(main_module.app)

    assert c.post("/api/v1/classify", json=HIGH_LEAD).status_code == 401
    assert c.post("/api/v1/classify", json=HIGH_LEAD,
                  headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.post("/api/v1/classify", json=HIGH_LEAD,
                  headers={"X-API-Key": "s3cret"}).status_code == 200

    config_module.get_settings.cache_clear()


# ------------------------------------------------------ workflow contract
def test_workflow_export_is_valid_json_and_complete():
    w = json.loads(WORKFLOW_PATH.read_text())

    assert w["id"] and w["name"] and w["nodes"] and w["connections"]
    names = {n["name"] for n in w["nodes"]}
    assert len(names) == len(w["nodes"]), "duplicate node names"

    for node in w["nodes"]:
        for key in ("id", "name", "type", "typeVersion", "position", "parameters"):
            assert key in node, f"{node.get('name')} is missing {key}"
        assert node["type"].startswith("n8n-nodes-base."), node["type"]


def test_workflow_has_no_dangling_connections():
    w = json.loads(WORKFLOW_PATH.read_text())
    names = {n["name"] for n in w["nodes"]}
    for source, conn in w["connections"].items():
        assert source in names, f"connection from unknown node {source}"
        for group in conn["main"]:
            for target in group:
                assert target["node"] in names, f"connection to unknown node {target['node']}"


def test_workflow_code_nodes_reference_real_nodes():
    """`$('Node Name')` against a renamed node fails only at runtime."""
    import re
    w = json.loads(WORKFLOW_PATH.read_text())
    names = {n["name"] for n in w["nodes"]}
    for node in w["nodes"]:
        js = node["parameters"].get("jsCode", "")
        for ref in re.findall(r"\$\('([^']+)'\)", js):
            assert ref in names, f"{node['name']} references missing node {ref}"


def test_workflow_external_calls_have_retries_and_error_handling():
    """Every HTTP node must survive an upstream failure."""
    w = json.loads(WORKFLOW_PATH.read_text())
    http_nodes = [n for n in w["nodes"] if n["type"].endswith(".httpRequest")]
    assert len(http_nodes) >= 4

    for node in http_nodes:
        assert node.get("onError") in {"continueErrorOutput", "continueRegularOutput"}, (
            f"{node['name']} would halt the workflow on failure"
        )
        assert node["parameters"]["options"].get("timeout"), f"{node['name']} has no timeout"

    for name in ("Qualify Lead (AI Agent)", "Sync Contact to CRM"):
        node = next(n for n in w["nodes"] if n["name"] == name)
        assert node.get("retryOnFail") is True, f"{name} does not retry"
        assert node.get("maxTries", 0) >= 2


def test_workflow_error_branches_are_wired():
    """Nodes set to continueErrorOutput must have their second output
    connected, or failures silently vanish."""
    w = json.loads(WORKFLOW_PATH.read_text())
    for node in w["nodes"]:
        if node.get("onError") != "continueErrorOutput":
            continue
        conns = w["connections"].get(node["name"], {}).get("main", [])
        assert len(conns) >= 2 and conns[1], (
            f"{node['name']} has an error output with nothing connected to it"
        )


def test_workflow_secrets_come_from_environment():
    """No credential may be baked into the export."""
    raw = WORKFLOW_PATH.read_text()
    for marker in ("sk-", "pat-", "hooks.slack.com/services/T", "Bearer ey"):
        assert marker not in raw, f"possible hard-coded secret: {marker}"
    assert "$env.SLACK_WEBHOOK_URL" in raw
    assert "$env.API_BASE_URL" in raw


def test_workflow_routes_all_three_priorities():
    w = json.loads(WORKFLOW_PATH.read_text())
    switch = next(n for n in w["nodes"] if n["type"].endswith(".switch"))
    keys = [r["outputKey"] for r in switch["parameters"]["rules"]["values"]]
    assert keys == ["High", "Medium", "Low"]
    assert len(w["connections"]["Route by Priority"]["main"]) == 3
