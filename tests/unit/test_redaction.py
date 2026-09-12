"""redact_inputs: a sensitive-marked InputSpec's raw value must never
reach a log payload; a non-sensitive one is left alone so redaction
doesn't become "hide everything and call it safe" (.CLAUDE/06:
"Observability must not become a data-leak mechanism", but logging still
has to be useful)."""

from __future__ import annotations

from datetime import datetime, timezone

from cuas.artifact import (
    Artifact,
    ArtifactApplication,
    ArtifactSafety,
    InputSpec,
    OutputSpec,
    OutputType,
    Provenance,
    SuccessCondition,
    SuccessConditionType,
)
from cuas.domain import Locator, LocatorStrategy, RiskLevel, Target
from cuas.observability import REDACTED, redact_inputs
from tests.fixtures.sample_artifacts import get_savings_balance


def _artifact_with_mixed_inputs() -> Artifact:
    target = Target(primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params={"role": "button"}))
    return Artifact(
        capability_id="mixed_inputs_capability",
        name="Mixed Inputs",
        description="Test fixture with one sensitive and one non-sensitive input.",
        version="1.0.0",
        application=ArtifactApplication(vendor="test-vendor", application="test-app", supported_versions=["1.x"]),
        inputs={
            "member_id": InputSpec(type="string", required=True, sensitive=True),
            "search_scope": InputSpec(type="string", required=False, sensitive=False),
        },
        outputs={"result": OutputSpec(type=OutputType.STRING, source=target)},
        steps=[],
        success_condition=SuccessCondition(type=SuccessConditionType.OUTPUT_VALID, output="result"),
        safety=ArtifactSafety(intent="test", risk=RiskLevel.SAFE),
        provenance=Provenance(created_from_run="test-fixture", created_at=datetime.now(timezone.utc)),
    )


def test_sensitive_input_is_redacted_and_non_sensitive_input_is_not() -> None:
    artifact = _artifact_with_mixed_inputs()
    redacted = redact_inputs(artifact, {"member_id": "M1001", "search_scope": "active-only"})
    assert redacted == {"member_id": REDACTED, "search_scope": "active-only"}


def test_the_real_get_savings_balance_artifact_marks_member_id_sensitive() -> None:
    """Guards against the flag silently getting dropped from the real
    fixture the e2e/unit replay tests exercise."""

    artifact = get_savings_balance()
    redacted = redact_inputs(artifact, {"member_id": "M1001"})
    assert redacted == {"member_id": REDACTED}


def test_an_input_not_declared_on_the_artifact_passes_through_unchanged() -> None:
    """redact_inputs only ever *removes* information for inputs the
    artifact itself has flagged; it must not become a blanket filter that
    also swallows caller-supplied extras it doesn't recognize."""

    artifact = get_savings_balance()
    redacted = redact_inputs(artifact, {"member_id": "M1001", "trace_tag": "debug-session-42"})
    assert redacted == {"member_id": REDACTED, "trace_tag": "debug-session-42"}
