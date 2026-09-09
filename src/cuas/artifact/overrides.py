"""Base artifact + version override + tenant override, resolved at replay
time (.CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md, "Base Artifact + Overrides").

Deliberately not a general inheritance engine (.CLAUDE/05: "Do not
implement a very complex inheritance engine unless the demo needs it") --
an override is a small, reviewable patch that replaces a named step's
target and/or value, nothing more. Overrides apply in a fixed order
(version, then tenant), so a tenant override always wins over a version
override for the same step, matching the doc's worked example (base
"Member Search" -> version override "Member Lookup" -> tenant override
inside a frame).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, model_validator

from cuas.artifact.schema import Artifact
from cuas.domain import Target


class OverrideScope(str, Enum):
    VERSION = "version"
    TENANT = "tenant"


class StepOverride(BaseModel):
    step_id: str
    target: Target | None = None
    value: str | None = None


class ArtifactOverride(BaseModel):
    scope: OverrideScope
    version: str | None = None
    tenant_id: str | None = None
    step_overrides: list[StepOverride]

    @model_validator(mode="after")
    def _scope_key_is_present(self) -> "ArtifactOverride":
        if self.scope == OverrideScope.VERSION and not self.version:
            raise ValueError("scope=version overrides must set `version`")
        if self.scope == OverrideScope.TENANT and not self.tenant_id:
            raise ValueError("scope=tenant overrides must set `tenant_id`")
        return self


def resolve_artifact(
    base: Artifact,
    overrides: list[ArtifactOverride],
    *,
    version: str,
    tenant_id: str,
) -> Artifact:
    """Apply the version override matching `version` (if any), then the
    tenant override matching `tenant_id` (if any), to a copy of `base`.
    Unmatched overrides in the list are ignored -- callers pass every
    known override for a capability and let this pick the applicable ones.
    """

    resolved = base.model_copy(deep=True)
    steps_by_id = {step.id: step for step in resolved.steps}

    applicable = [o for o in overrides if o.scope == OverrideScope.VERSION and o.version == version]
    applicable += [o for o in overrides if o.scope == OverrideScope.TENANT and o.tenant_id == tenant_id]

    for override in applicable:
        for step_override in override.step_overrides:
            step = steps_by_id.get(step_override.step_id)
            if step is None:
                raise ValueError(
                    f"override references unknown step id {step_override.step_id!r} "
                    f"(scope={override.scope}, version={override.version}, tenant_id={override.tenant_id})"
                )
            if step_override.target is not None:
                step.target = step_override.target
            if step_override.value is not None:
                step.value = step_override.value

    return resolved
