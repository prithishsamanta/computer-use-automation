"""The typed, versioned artifact shape from .CLAUDE/02_ARTIFACT_SCHEMA.md.

Deliberately reuses domain.Target/Locator/Action wherever the doc's concepts
are the same operation in disguise: an output's "source" (.CLAUDE/02,
"label_relative" example) and a business-outcome/recoverable-condition
"detect" rule are both just "find something on the surface" -- the same
thing a Step's target is. One locator/condition vocabulary, not three.

This module is the artifact *shape*. Cross-artifact concerns (base +
version + tenant override resolution, storage/versioning) live in
overrides.py and repository.py respectively, per .CLAUDE/08's instruction
to keep the discovery trace/artifact and the multi-tenant model each in
their own place rather than one large module.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from cuas.domain import Action, RiskLevel, Target
from cuas.surface.adapter import WaitCondition

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


class ArtifactApplication(BaseModel):
    vendor: str
    application: str
    supported_versions: list[str] = Field(default_factory=list)


class InputSpec(BaseModel):
    type: str = "string"
    required: bool = True
    description: str = ""
    sensitive: bool = Field(
        default=False,
        description=(
            "If true, this input's raw value must not be persisted in structured logs "
            "or evidence (.CLAUDE/04, 'Sensitive Data'; .CLAUDE/06, 'Observability must "
            "not become a data-leak mechanism') -- e.g. a member ID. Enforced by "
            "cuas.observability.redaction.redact_inputs, called on ReplayEngine's "
            "run_started event."
        ),
    )


class OutputType(str, Enum):
    STRING = "string"
    DECIMAL = "decimal"
    INTEGER = "integer"
    BOOLEAN = "boolean"


class OutputSpec(BaseModel):
    """`source` is a Target: reading an output's raw text and reading an
    element's text for any other purpose are the same SurfaceAdapter.read()
    call (.CLAUDE/03_DISCOVERY_AND_REPLAY.md, "Output Extraction" -- resolve
    source, read value, parse/normalize, validate type)."""

    type: OutputType
    source: Target
    required: bool = True


class WaitSpec(BaseModel):
    """How long a step's own action (fill/click/...) is allowed to wait for
    its target to become actionable. Distinct from `checkpoint`, which
    asserts semantic progress *after* the action runs."""

    timeout_ms: int = 5000


class RecoveryAction(str, Enum):
    WAIT = "wait"
    DISMISS = "dismiss"
    RETRY = "retry"
    RE_RESOLVE_TARGET = "re_resolve_target"


class Step(Action):
    """An Action plus the ordering/execution metadata an artifact needs
    that a bare discovery proposal doesn't (.CLAUDE/02_ARTIFACT_SCHEMA.md,
    "Steps"). `value` may contain `{{input_name}}` placeholders resolved at
    replay time from the artifact's declared inputs."""

    wait: WaitSpec = Field(default_factory=WaitSpec)
    checkpoint: WaitCondition | None = None


class SuccessConditionType(str, Enum):
    OUTPUT_VALID = "output_valid"


class SuccessCondition(BaseModel):
    type: SuccessConditionType
    output: str


class BusinessOutcome(BaseModel):
    code: str
    detect: WaitCondition


class RecoverableCondition(BaseModel):
    code: str
    detect: WaitCondition
    recovery: RecoveryAction
    dismiss_target: Target | None = Field(
        default=None, description="Required when recovery == DISMISS."
    )

    @model_validator(mode="after")
    def _dismiss_needs_target(self) -> "RecoverableCondition":
        if self.recovery == RecoveryAction.DISMISS and self.dismiss_target is None:
            raise ValueError(f"recoverable condition {self.code!r}: DISMISS recovery requires dismiss_target")
        return self


class ArtifactSafety(BaseModel):
    """The capability's overall normalized intent/risk (.CLAUDE/02,
    "Safety Metadata"). Informational only, same as a Step's own risk field
    -- the runtime PolicyEngine (Phase 6) is still the enforcement
    authority for every individual step, at both discovery and replay
    time, because policy can vary by tenant and change after this artifact
    was created."""

    intent: str
    risk: RiskLevel


class Provenance(BaseModel):
    created_from_run: str
    created_at: datetime


class Artifact(BaseModel):
    capability_id: str
    name: str
    description: str
    version: str
    application: ArtifactApplication
    tenant_scope: str = "base"

    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    outputs: dict[str, OutputSpec] = Field(default_factory=dict)
    steps: list[Step]

    success_condition: SuccessCondition
    business_outcomes: list[BusinessOutcome] = Field(default_factory=list)
    recoverable_conditions: list[RecoverableCondition] = Field(default_factory=list)

    safety: ArtifactSafety
    provenance: Provenance

    @model_validator(mode="after")
    def _version_is_semver(self) -> "Artifact":
        if not _SEMVER_RE.match(self.version):
            raise ValueError(f"version {self.version!r} is not semver (expected X.Y.Z)")
        return self

    @model_validator(mode="after")
    def _step_ids_are_unique(self) -> "Artifact":
        ids = [step.id for step in self.steps]
        duplicates = {step_id for step_id in ids if ids.count(step_id) > 1}
        if duplicates:
            raise ValueError(f"duplicate step id(s): {sorted(duplicates)}")
        return self

    @model_validator(mode="after")
    def _success_condition_references_a_declared_output(self) -> "Artifact":
        if self.success_condition.output not in self.outputs:
            raise ValueError(
                f"success_condition references output {self.success_condition.output!r}, "
                f"which is not declared in outputs {sorted(self.outputs)}"
            )
        return self
