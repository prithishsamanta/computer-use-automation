"""HTTP-facing request/response shapes for the /runs endpoints.

Explicit Phase 11 instruction: "Keep API models separate from core domain
models where appropriate so HTTP does not leak into the service layer."
`RunOrchestrator` never imports FastAPI or anything from this module --
these types exist only to translate one HTTP request into the call
`RunOrchestrator.run_capability` already accepts (`AppContext`,
`DiscoveryGoal`, plain `inputs`), and its `RunResult` back into a JSON
response, so a later HTTP-only concern (pagination, auth, a versioned
response envelope) never needs to touch `RunResult` itself. `RunResponse`
is structurally identical to `RunResult` today; it stays a distinct type
on purpose rather than aliasing it, so that stays true even after the two
diverge.

Reuses a couple of small, already-typed value objects directly
(`WaitCondition`, `BusinessOutcome`) rather than re-declaring them --
those are plain data shapes with no service-layer behavior of their own,
not something the API boundary needs a second copy of.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from cuas.artifact.schema import BusinessOutcome
from cuas.domain import ErrorCode
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
    artifact_version: str | None = None
    discovered_new_capability: bool = False
