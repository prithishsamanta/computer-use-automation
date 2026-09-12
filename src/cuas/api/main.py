"""Thin FastAPI entry point.

Phase 1 added a health route only. Phase 11 adds the minimal HTTP surface
this take-home needs to demonstrate the vertical slice end to end:
`POST /runs` starts a run (capability resolution -> deterministic replay,
or live discovery when nothing matches) and returns its structured
result; `GET /runs/{run_id}` retrieves that same result again later. This
is deliberately not the operator UI (Phase 12/13's job) -- there is no
intervention claim/resolve endpoint here yet, just enough to start a run
and read its outcome back.

This module is the composition root: every dependency `RunOrchestrator`
needs is constructed once here, from `Settings`, and reused across
requests. It holds no orchestration logic of its own -- only wiring --
which is exactly why `RunOrchestrator` itself never imports FastAPI.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from cuas.api.schemas import RunRequest, RunResponse
from cuas.api.store import RunResultStore
from cuas.artifact import FileArtifactRepository
from cuas.capability import CapabilityService, FileCapabilityRepository
from cuas.discovery import DiscoveryGoal, FileDiscoveryTraceStore
from cuas.discovery.anthropic_client import AnthropicLLMClient
from cuas.domain import AppContext
from cuas.handoff import InMemoryInterventionRepository
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

_orchestrator = RunOrchestrator(
    _capability_service,
    FileArtifactRepository(_settings.artifact_dir),
    LayeredPolicyEngine(),
    launch_playwright_surface,
    InMemoryInterventionRepository(),
    FileDiscoveryTraceStore(_settings.discovery_trace_dir),
    llm=_llm,
    event_sink=JsonlEventSink(_settings.log_dir),
    evidence_store=FileEvidenceStore(_settings.evidence_dir),
)
_run_store = RunResultStore()


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
