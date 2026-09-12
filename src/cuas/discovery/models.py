"""Discovery-loop data model (.CLAUDE/03_DISCOVERY_AND_REPLAY.md, "Discovery
Loop"; .CLAUDE/07_IMPLEMENTATION_GUIDANCE.md's LLMClient interface). These
types describe one attempt to discover how to perform a capability against
a live surface -- goal, bounds, per-step history, and the terminal result
-- independent of *how* that history gets persisted (trace.py) or *which*
model proposed each action (llm_client.py).

Reuses BusinessOutcome/WaitCondition from the artifact/surface packages
rather than inventing discovery-only equivalents: "find something on the
surface" is the same operation whether it's checked during replay or
during discovery. There is deliberately no discovery-only equivalent of
Artifact.recoverable_conditions -- discovery has an LLM available to
reason about a failed action (the failure is simply reported back to it
as history, see DiscoveryHistoryEntry), which is the whole reason
discovery doesn't need ReplayEngine's mechanical, artifact-declared
recovery table.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from cuas.artifact.schema import BusinessOutcome
from cuas.domain import Action
from cuas.safety import PolicyDecision
from cuas.surface.adapter import Observation, WaitCondition


class DiscoveryGoal(BaseModel):
    """What discovery is trying to accomplish, in natural language plus
    the structured inputs it's allowed to use.

    `success_checkpoint`, if declared, lets DiscoveryEngine deterministically
    verify a model's "done" claim instead of trusting it outright (the LLM
    is informed about, but is never the final authority on, whether the
    goal was actually achieved -- .CLAUDE/08 decision #4). When omitted,
    there is nothing to verify against yet (e.g. exploring a brand new
    capability), so the model's own done=true is accepted as-is; this is
    safe because "goal accomplished" is not itself a safety decision --
    the PolicyEngine, not this flag, is what keeps discovery from taking a
    consequential action.

    `known_business_outcomes` mirrors Artifact.business_outcomes: known,
    modeled alternative end states (e.g. "member not found") detected the
    same deterministic way replay detects them, independent of anything
    the model says.

    `sensitive_inputs` names which of `inputs` must never appear in raw
    form in a persisted prompt/response/observation (redaction.redact_text
    scrubs their *values*, looked up via `sensitive_values()`) --
    discovery has no artifact InputSpec table yet to carry this instead
    (.CLAUDE/08 decision #6: the artifact doesn't exist until a later,
    separate construction step).
    """

    capability_id: str
    description: str = Field(
        description="Natural-language goal, e.g. 'Find member M1001 and read their savings balance.'"
    )
    start_url: str
    inputs: dict[str, str] = Field(default_factory=dict)
    sensitive_inputs: set[str] = Field(default_factory=set)
    success_checkpoint: WaitCondition | None = None
    known_business_outcomes: list[BusinessOutcome] = Field(default_factory=list)

    def sensitive_values(self) -> list[str]:
        return [str(self.inputs[name]) for name in self.sensitive_inputs if name in self.inputs]


class DiscoveryLimits(BaseModel):
    """Hard bounds (explicit instruction: "Discovery must have hard
    bounds: maximum steps, loop/repetition detection, and time/token
    limits where practical"). Every bound is enforced by DiscoveryEngine
    itself -- never left to the model's judgment or to an artifact-driven
    recovery table, since none exists yet at discovery time."""

    max_steps: int = 20
    max_duration_seconds: float = 300.0
    max_total_tokens: int | None = 20000
    max_consecutive_repeats: int = 2


class DiscoveryStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"
    MALFORMED_MODEL_OUTPUT = "malformed_model_output"
    LOOP_DETECTED = "loop_detected"
    MAX_STEPS_EXCEEDED = "max_steps_exceeded"
    MAX_DURATION_EXCEEDED = "max_duration_exceeded"
    MAX_TOKENS_EXCEEDED = "max_tokens_exceeded"
    FAILED = "failed"


class DiscoveryHistoryEntry(BaseModel):
    """One step of the observe -> propose -> policy -> execute -> observe
    loop, fed back to the model as context on later turns. This carries
    RAW values (it is what AnthropicLLMClient legitimately needs to keep
    reasoning correctly, e.g. "you already tried filling this box with the
    member id and it failed") -- DiscoveryEngine redacts a copy of this
    history before it ever leaves the engine as part of a returned
    DiscoveryResult or a persisted DiscoveryTrace. See engine.py's
    `_redact_history`.
    """

    step_index: int
    action: Action
    policy_decision: PolicyDecision | None = None
    outcome: str  # "executed" | "execution_failed" | "policy_denied" | "approval_required"
    error_message: str | None = None
    observation_after: Observation | None = None


class DiscoveryResult(BaseModel):
    run_id: str
    status: DiscoveryStatus
    capability_id: str
    steps_taken: int
    business_outcome_code: str | None = None
    escalation_step_index: int | None = None
    escalation_reason: str | None = None
    error_message: str | None = None
    history: list[DiscoveryHistoryEntry] = Field(default_factory=list)
