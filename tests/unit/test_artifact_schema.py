"""Schema-level tests: the sample artifact is valid, round-trips through
JSON, and the cross-field validators actually catch invalid artifacts
rather than silently accepting them (.CLAUDE/02_ARTIFACT_SCHEMA.md:
artifacts must be "safe to execute without an LLM making runtime
decisions" -- that starts with rejecting malformed ones)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cuas.artifact import (
    Artifact,
    ArtifactApplication,
    ArtifactSafety,
    OutputSpec,
    OutputType,
    Provenance,
    RecoverableCondition,
    RecoveryAction,
    Step,
    SuccessCondition,
    SuccessConditionType,
)
from cuas.domain import ActionType, RiskLevel, Target
from cuas.surface.adapter import WaitCondition, WaitConditionKind
from datetime import datetime, timezone
from tests.fixtures.sample_artifacts import get_savings_balance


def _role_target(role: str, name: str | None = None) -> Target:
    from cuas.domain import Locator, LocatorStrategy

    params = {"role": role}
    if name is not None:
        params["name"] = name
    return Target(primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params=params))


def _minimal_kwargs(**overrides):
    kwargs = dict(
        capability_id="cap",
        name="Cap",
        description="d",
        version="1.0.0",
        application=ArtifactApplication(vendor="v", application="a", supported_versions=["1.x"]),
        steps=[
            Step(id="s1", action_type=ActionType.CLICK, intent="i", target=_role_target("button", "Go")),
        ],
        outputs={"out": OutputSpec(type=OutputType.STRING, source=_role_target("textbox"))},
        success_condition=SuccessCondition(type=SuccessConditionType.OUTPUT_VALID, output="out"),
        safety=ArtifactSafety(intent="i", risk=RiskLevel.SAFE),
        provenance=Provenance(created_from_run="run-1", created_at=datetime.now(timezone.utc)),
    )
    kwargs.update(overrides)
    return kwargs


def test_sample_artifact_is_valid() -> None:
    artifact = get_savings_balance()
    assert artifact.capability_id == "get_savings_balance"
    assert len(artifact.steps) == 2
    assert "savings_balance" in artifact.outputs


def test_artifact_round_trips_through_json() -> None:
    artifact = get_savings_balance()
    reloaded = Artifact.model_validate_json(artifact.model_dump_json())
    assert reloaded.model_dump() == artifact.model_dump()


def test_rejects_non_semver_version() -> None:
    with pytest.raises(ValidationError):
        Artifact(**_minimal_kwargs(version="v1"))


def test_rejects_duplicate_step_ids() -> None:
    target = _role_target("button", "Go")
    with pytest.raises(ValidationError):
        Artifact(
            **_minimal_kwargs(
                steps=[
                    Step(id="dup", action_type=ActionType.CLICK, intent="i", target=target),
                    Step(id="dup", action_type=ActionType.CLICK, intent="i", target=target),
                ]
            )
        )


def test_rejects_success_condition_referencing_undeclared_output() -> None:
    with pytest.raises(ValidationError):
        Artifact(
            **_minimal_kwargs(
                success_condition=SuccessCondition(
                    type=SuccessConditionType.OUTPUT_VALID, output="does_not_exist"
                )
            )
        )


def test_dismiss_recovery_requires_a_dismiss_target() -> None:
    with pytest.raises(ValidationError):
        RecoverableCondition(
            code="X",
            detect=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="oops"),
            recovery=RecoveryAction.DISMISS,
        )
