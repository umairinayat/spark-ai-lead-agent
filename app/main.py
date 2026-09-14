"""FastAPI application: the AI service the n8n workflow orchestrates.

Endpoint map, and who calls what:

  GET  /health                    liveness + active configuration
  GET  /api/v1/config             non-secret runtime config (UI banner)

  POST /api/v1/classify           n8n node 3   - AI only, no side effects
  POST /api/v1/crm/upsert         n8n node 5   - CRM write, provider-agnostic
  POST /api/v1/notify             n8n node 7   - Slack alert
  POST /api/v1/leads/record       n8n node 9   - persist the final outcome

  POST /api/v1/leads              browser form - runs all four in one call
  GET  /api/v1/leads              stored leads
  GET  /api/v1/leads/{id}         one lead plus its agent trace
  GET  /api/v1/stats              dashboard counters
  GET  /api/v1/crm/contacts       mock CRM inspection
  GET  /api/v1/notifications      notification audit log

The split matters: /classify has no side effects, so n8n can retry it safely.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .crm import PIPELINE_NAME, STAGE_BY_INTENT, STAGE_BY_PRIORITY, build_crm_adapter, stage_for
from .db import LeadRepository
from .logging_conf import configure_logging, request_id_var
from .models import (
    ClassifyResponse,
    CrmContact,
    CrmUpsertRequest,
    LeadIn,
    LeadResult,
    NotificationResult,
    NotifyRequest,
    RecordRequest,
    new_id,
    utc_now,
)
from .notify import SlackNotifier
from .security import rate_limit, require_api_key
from .service import LeadPipeline

logger = logging.getLogger(__name__)

settings = get_settings()
configure_logging(settings.log_level)

repo = LeadRepository(settings.database_path)
crm_adapter = build_crm_adapter(settings, repo)
notifier = SlackNotifier(settings, repo)
pipeline = LeadPipeline(settings, repo, crm_adapter, notifier)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("service.start", extra=settings.describe())
    if not settings.llm_enabled:
        logger.warning(
            "service.llm_disabled",
            extra={"detail": "OPENAI_API_KEY not set; using deterministic rules engine"},
        )
    yield
    logger.info("service.stop")


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    description=(
        "AI lead qualification service. Classifies inbound leads, writes them "
        "to a CRM and alerts sales on high-priority leads. Orchestrated by n8n."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-Id") or uuid.uuid4().hex[:12]
    request_id_var.set(rid)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request.unhandled", extra={"path": request.url.path})
        return JSONResponse(
            status_code=500,
            content={"error": "internal_error", "request_id": rid},
            headers={"X-Request-Id": rid},
        )
    duration = int((time.perf_counter() - started) * 1000)
    logger.info(
        "request.complete",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration,
        },
    )
    response.headers["X-Request-Id"] = rid
    return response


# ------------------------------------------------------------------- health
@app.get("/health", tags=["ops"])
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": "1.0.0",
        "time": utc_now().isoformat(),
        "config": settings.describe(),
    }


@app.get("/api/v1/config", tags=["ops"])
async def config() -> dict[str, Any]:
    return {
        **settings.describe(),
        "pipeline": PIPELINE_NAME,
        "stages_by_priority": STAGE_BY_PRIORITY,
        "stages_by_intent": STAGE_BY_INTENT,
        "notify_on": sorted(settings.notify_priorities),
    }


@app.get("/api/v1/crm/health", tags=["ops"])
async def crm_health() -> dict[str, Any]:
    return await crm_adapter.health()


# ------------------------------------------------------- step endpoints (n8n)
@app.post(
    "/api/v1/classify",
    response_model=ClassifyResponse,
    tags=["agent"],
    dependencies=[Depends(require_api_key)],
)
async def classify(lead: LeadIn) -> ClassifyResponse:
    """Run the agent only. Idempotent and side-effect free, so the n8n node
    that calls it can retry on failure without creating duplicates."""
    outcome = await pipeline.classify(lead)
    return ClassifyResponse(
        lead_id=new_id("lead"),
        received_at=utc_now(),
        lead=lead,
        classification=outcome.classification,
        agent={
            "model": outcome.model,
            "degraded": outcome.degraded,
            "degraded_reason": outcome.degraded_reason,
            "attempts": outcome.attempts,
            "latency_ms": outcome.latency_ms,
            "tool_calls": [tc.model_dump() for tc in outcome.tool_calls],
            "suggested_stage": stage_for(outcome.classification),
            "should_notify": notifier.should_notify(outcome.classification),
        },
    )


@app.post(
    "/api/v1/crm/upsert",
    response_model=CrmContact,
    tags=["crm"],
    dependencies=[Depends(require_api_key)],
)
async def crm_upsert(payload: CrmUpsertRequest) -> CrmContact:
    """Create or update the contact in whichever CRM is configured.

    Routing the CRM write through the service rather than calling the CRM
    directly from n8n keeps credentials in one place and makes the provider
    swappable without editing the workflow.
    """
    return await pipeline.sync_crm(payload.lead, payload.classification)


@app.post(
    "/api/v1/notify",
    response_model=NotificationResult,
    tags=["notify"],
    dependencies=[Depends(require_api_key)],
)
async def notify(payload: NotifyRequest) -> NotificationResult:
    return await pipeline.notify(
        payload.lead, payload.classification, payload.crm,
        payload.lead_id, payload.force,
    )


@app.post(
    "/api/v1/leads/record",
    tags=["leads"],
    dependencies=[Depends(require_api_key)],
)
async def record_lead(payload: RecordRequest) -> dict[str, Any]:
    """Persist the outcome assembled by the workflow."""
    lead_id = payload.lead_id or new_id("lead")
    pipeline.persist(
        lead_id, payload.lead, payload.agent, payload.classification,
        payload.crm, payload.notification, payload.source,
    )
    return {"stored": True, "lead_id": lead_id}


# ------------------------------------------------------------- full pipeline
@app.post(
    "/api/v1/leads",
    response_model=LeadResult,
    status_code=status.HTTP_201_CREATED,
    tags=["leads"],
    dependencies=[Depends(rate_limit)],
)
async def submit_lead(lead: LeadIn) -> LeadResult:
    """Qualify, sync to CRM, notify and store in a single call.

    This is the path the browser form uses and the fallback for anyone who
    does not want to run n8n. It is deliberately not API-key protected so the
    public form can post to it; it is rate limited instead.
    """
    return await pipeline.run(lead, pipeline_source="api")


# -------------------------------------------------------------------- reads
@app.get("/api/v1/leads", tags=["leads"])
async def list_leads(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    priority: str = Query(default=""),
) -> dict[str, Any]:
    rows = repo.list_leads(limit=limit, offset=offset, priority=priority)
    return {"count": len(rows), "leads": rows}


@app.get("/api/v1/leads/{lead_id}", tags=["leads"])
async def get_lead(lead_id: str) -> JSONResponse:
    lead = repo.get_lead(lead_id)
    if not lead:
        return JSONResponse(status_code=404, content={"error": "lead_not_found"})
    return JSONResponse(content={"lead": lead, "agent_runs": repo.agent_runs_for(lead_id)})


@app.get("/api/v1/stats", tags=["leads"])
async def stats() -> dict[str, Any]:
    return repo.stats()


@app.get("/api/v1/crm/contacts", tags=["crm"])
async def crm_contacts(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    if crm_adapter.name != "mock":
        return {
            "provider": crm_adapter.name,
            "detail": "inspect contacts in the provider's own UI",
            "contacts": [],
        }
    return {"provider": "mock", "contacts": repo.list_mock_contacts(limit)}


@app.get("/api/v1/notifications", tags=["notify"])
async def notifications(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    return {"notifications": repo.list_notifications(limit)}
