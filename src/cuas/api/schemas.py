"""HTTP-facing request/response shapes for the /runs and /interventions
endpoints.

Explicit Phase 11 instruction, reaffirmed in Phase 12: "Keep API models
separate from core domain models where appropriate so HTTP does not leak
into the service layer." `RunOrchestrator` never imports FastAPI or
anything from this module -- these types exist only to translate one HTTP
request into the call `RunOrchestrator.run_capability`/`claim_intervention`/
`mark_human_control_complete`/`resume_run` already accepts, and their
results back into a JSON response, so a later HTTP-only concern
(pagination, auth, a versioned response envelope) never needs to touch
`RunResult`/`InterventionRequest` themselves. `RunResponse`/
`InterventionResponse` are structurally identical to `RunResult`/
`InterventionRequest` today; each stays a distinct type on purpose rather
than aliasing, so that stays true even after they diverge.

Reuses a couple of small, already-typed value objects directly
(`WaitCondition`, `BusinessOutcome`) rather than re-declaring them --
those are plain data shapes with no service-layer behavior of their own,
not something the API boundary needs a second copy of.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from cuas.artifact.schema import BusinessOutcome
from cuas.domain import ErrorCode, InterventionStatus
from cuas.orchestration import RunOutcome
from cuas.surface.adapter import WaitCondition


class DiscoveryGoalRequest(BaseModel):
    """What a caller supplies to opt a request into on-demand discovery
    for a capability_id nothing currently resolves to (see
    RunOrchestrator.run_capability's docstring for why this can't be
    inferred from the capability_id alone). Omitted entirely means "do
    not attempt discovery for this request" -- a no-match result then
    just reports DISCOVERY_REQUIRED and stops."""

    description: str
    start_url: str
    sensitive_inputs: set[str] = Field(default_factory=set)
    success_checkpoint: WaitCondition | None = None
    known_business_outcomes: list[BusinessOutcome] = Field(default_factory=list)


class RunRequest(BaseModel):
    capability_id: str
    vendor: str
    application: str
    version: str
    tenant_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    discovery_goal: DiscoveryGoalRequest | None = None


class RunResponse(BaseModel):
    run_id: str
    capability_id: str
    outcome: RunOutcome
    outputs: dict[str, Any] = Field(default_factory=dict)
    business_outcome_code: str | None = None
    error_code: ErrorCode | None = None
    error_message: str | None = None
    reason: str | None = None
    intervention_id: str | None = None
    session_id: str | None = None
    artifact_version: str | None = None
    discovered_new_capability: bool = False


class InterventionResponse(BaseModel):
    """Mirrors `InterventionRequest` field-for-field (Phase 12) -- an
    operator-facing queue's view of one escalation. `session_id` being
    non-null is what tells an operator (or their tooling) a live browser
    is genuinely still waiting, per .CLAUDE/04's handoff model."""

    id: str
    run_id: str
    capability_id: str
    tenant_id: str
    current_step: str | None = None
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    status: InterventionStatus
    claimed_by: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class OperatorActionRequest(BaseModel):
    """Body for the claim/complete endpoints. `operator_id` is the whole
    of this take-home's authorization model -- an explicit, simple
    stand-in for real IAM (see RunOrchestrator.mark_human_control_complete's
    docstring and cuas.handoff.errors.InterventionOwnershipError), not a
    session token or an authenticated identity."""

    operator_id: str
