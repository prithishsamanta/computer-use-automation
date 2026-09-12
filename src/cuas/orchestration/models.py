"""RunOrchestrator's own result type -- the shape every one of the phase's
branches (`request -> capability resolution -> replay or discovery ->
structured outcome`) converges on, independent of whether the answer came
from a deterministic replay, a fresh discovery run, or capability
resolution refusing to guess.

Deliberately a `cuas.orchestration` type, not reused from `ReplayResult`/
`DiscoveryResult`/`CapabilityResolution`: those three each describe one
component's own outcome vocabulary (ReplayStatus, DiscoveryStatus,
CapabilityResolutionStatus overlap in meaning but not in shape or in
which fields apply), and RunOrchestrator's job is precisely to translate
whichever one actually happened into a single, stable, caller-facing
result -- exactly the composition the phase asks for, without leaking any
one component's internal status enum through the seam.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from cuas.domain import ErrorCode


class RunOutcome(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    DISCOVERY_REQUIRED = "discovery_required"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"
    FAILED = "failed"
    AMBIGUOUS_CAPABILITY = "ambiguous_capability"


class RunResult(BaseModel):
    """One request's complete, structured outcome. `run_id` is stable and
    propagated through capability resolution, discovery, replay, and any
    intervention created along the way (.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md,
    "Traceability": every question a reviewer might ask should be
    answerable from this one id's event stream) -- and, from Phase 12 on,
    across a pause/handoff/resume cycle too: a resumed run's `RunResult`
    carries the exact same `run_id` it started with.

    `session_id` (Phase 12) is set alongside `intervention_id` whenever an
    APPROVAL_REQUIRED/FAILED outcome left a live automation session open
    for an operator to take over (see `cuas.handoff.session`); it is
    `None` for every outcome that doesn't pause a session (SUCCESS,
    BUSINESS_OUTCOME, BLOCKED, DISCOVERY_REQUIRED, AMBIGUOUS_CAPABILITY).
    """

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
    discovered_new_capability: bool = Field(
        default=False,
        description="True when this run's answer came from a discovery attempt made just "
        "now (and a new capability/artifact was stored as a result), not a pre-existing one.",
    )
