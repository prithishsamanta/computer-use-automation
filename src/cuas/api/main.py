"""Thin FastAPI entry point.

Phase 1 added a health route only. Phase 11 added the minimal HTTP surface
to start a run and read its outcome back (`POST /runs`, `GET /runs/{run_id}`).
Phase 12 adds the operator-facing side of the human-handoff seam: a
pending-intervention queue and the claim -> complete -> resume lifecycle
`.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md` describes. Phase 13 adds no new
routes here at all -- it wires this same, already-complete lifecycle to a
literal noVNC view/control surface at the Docker/Compose level (see
Dockerfile, docker/automation-entrypoint.sh, docker-compose.yml). The one
change in this file is `_surface_factory` below, which now threads
`Settings.playwright_headless` through so the automation container can
run headed under Xvfb while every other environment (local dev, unit
tests, CI) keeps running headless exactly as before.

This module is the composition root: every dependency `RunOrchestrator`
needs is constructed once here, from `Settings`, and reused across
requests. It holds no orchestration logic of its own -- only wiring --
which is exactly why `RunOrchestrator` itself never imports FastAPI.
"""

from __future__ import annotations

from functools import partial

from fastapi import FastAPI, HTTPException

from cuas.api.schemas import (
    DiscoveryGoalRequest,
    InterventionResponse,
    OperatorActionRequest,
    RunRequest,
    RunResponse,
)
from cuas.api.store import RunResultStore
from cuas.artifact import FileArtifactRepository
from cuas.capability import CapabilityService, FileCapabilityRepository
from cuas.discovery import DiscoveryGoal, FileDiscoveryTraceStore
from cuas.discovery.anthropic_client import AnthropicLLMClient
from cuas.domain import AppContext
from cuas.handoff import FileInterventionRepository, HandoffError, InterventionRepository, InterventionRequest
from cuas.observability.config import get_settings
from cuas.observability.event_sink import JsonlEventSink
from cuas.observability.evidence import FileEvidenceStore
from cuas.orchestration import RunOrchestrator
from cuas.safety import LayeredPolicyEngine
from cuas.surface.playwright_adapter import launch_playwright_surface

app = FastAPI(title="Computer-Use Automation System")

_settings = get_settings()

_capability_service = CapabilityService(
    FileCapabilityRepository(_settings.capability_dir), FileArtifactRepository(_settings.artifact_dir)
)
# Only constructed when a real key is configured -- discovery is simply
# unavailable (RunOrchestrator reports DISCOVERY_REQUIRED with a clear
# reason rather than crashing) otherwise, exactly the "not required for
# normal development or CI" property Settings.anthropic_api_key already
# documents.
_llm = AnthropicLLMClient(_settings.anthropic_api_key) if _settings.anthropic_api_key else None

# File-backed, not the in-memory placeholder Phase 11 used -- the "full
# persistence implementation" this phase's instructions ask for. Kept as
# its own name (`_interventions`) rather than only reachable through
# `_orchestrator` because the read-only queue endpoints below (list/get)
# have no session-registry side effects and so query it directly, while
# every *mutating* endpoint (claim/complete/resume) goes through
# `_orchestrator`, which is the only thing that also holds the
# SessionRegistry those transitions need to touch.
_interventions: InterventionRepository = FileInterventionRepository(_settings.intervention_dir)

# Phase 13: the only change from a plain `launch_playwright_surface`
# reference. `Settings.playwright_headless` defaults to True everywhere
# except the automation Compose service (which sets
# PLAYWRIGHT_HEADLESS=false, a plain non-secret env var, so it runs
# headed under Xvfb -- see docker-compose.yml/Dockerfile). Still a
# zero-arg callable returning an async context manager, so it satisfies
# `RunOrchestrator`'s `SurfaceFactory` type exactly as before.
_surface_factory = partial(launch_playwright_surface, headless=_settings.playwright_headless)

_orchestrator = RunOrchestrator(
    _capability_service,
    FileArtifactRepository(_settings.artifact_dir),
    LayeredPolicyEngine(),
    _surface_factory,
    _interventions,
    FileDiscoveryTraceStore(_settings.discovery_trace_dir),
    llm=_llm,
    event_sink=JsonlEventSink(_settings.log_dir),
    evidence_store=FileEvidenceStore(_settings.evidence_dir),
)
_run_store = RunResultStore()


def _intervention_response(intervention: InterventionRequest) -> InterventionResponse:
    return InterventionResponse(**intervention.model_dump())


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@app.post("/runs", response_model=RunResponse)
async def start_run(request: RunRequest) -> RunResponse:
    context = AppContext(
        vendor=request.vendor, application=request.application, version=request.version, tenant_id=request.tenant_id
    )

    goal: DiscoveryGoal | None = None
    if request.discovery_goal is not None:
        goal = DiscoveryGoal(
            capability_id=request.capability_id,
            description=request.discovery_goal.description,
            start_url=request.discovery_goal.start_url,
            inputs=request.inputs,
            sensitive_inputs=request.discovery_goal.sensitive_inputs,
            success_checkpoint=request.discovery_goal.success_checkpoint,
            known_business_outcomes=request.discovery_goal.known_business_outcomes,
        )

    result = await _orchestrator.run_capability(request.capability_id, request.inputs, context, discovery_goal=goal)
    _run_store.save(result)
    return RunResponse(**result.model_dump())


@app.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: str) -> RunResponse:
    result = _run_store.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"no run with id {run_id!r}")
    return RunResponse(**result.model_dump())


# -- human handoff (Phase 12) ------------------------------------------------


@app.get("/interventions", response_model=list[InterventionResponse])
def list_interventions(pending_only: bool = True) -> list[InterventionResponse]:
    """Defaults to the operator's actual queue (PENDING only); pass
    `?pending_only=false` to see the full history, claimed/resolved/
    cancelled included."""

    records = _interventions.list_pending() if pending_only else _interventions.list_all()
    return [_intervention_response(r) for r in records]


@app.get("/interventions/{intervention_id}", response_model=InterventionResponse)
def get_intervention(intervention_id: str) -> InterventionResponse:
    try:
        return _intervention_response(_interventions.get(intervention_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/interventions/{intervention_id}/claim", response_model=InterventionResponse)
def claim_intervention(intervention_id: str, request: OperatorActionRequest) -> InterventionResponse:
    try:
        updated = _orchestrator.claim_intervention(intervention_id, operator_id=request.operator_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HandoffError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _intervention_response(updated)


@app.post("/interventions/{intervention_id}/complete", response_model=InterventionResponse)
def complete_intervention(intervention_id: str, request: OperatorActionRequest) -> InterventionResponse:
    """"Mark human control complete / request resume" -- the operator's
    "I've resolved it, ready to hand back to automation" action. Does not
    itself resume anything; call `POST /interventions/{id}/resume`
    next."""

    try:
        updated = _orchestrator.mark_human_control_complete(intervention_id, operator_id=request.operator_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HandoffError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _intervention_response(updated)


@app.post("/interventions/{intervention_id}/resume", response_model=RunResponse)
async def resume_intervention(intervention_id: str) -> RunResponse:
    try:
        result = await _orchestrator.resume_run(intervention_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HandoffError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _run_store.save(result)
    return RunResponse(**result.model_dump())


@app.post("/interventions/{intervention_id}/cancel", response_model=InterventionResponse)
async def cancel_intervention(intervention_id: str, request: OperatorActionRequest) -> InterventionResponse:
    try:
        updated = await _orchestrator.cancel_intervention(intervention_id, operator_id=request.operator_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HandoffError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _intervention_response(updated)
