"""ReplayEngine's own branching logic, against a scriptable fake surface --
fast and deterministic. tests/integration/test_replay_engine_e2e.py covers
the same engine against the real demo app through a real browser; this
file is for edge cases (policy branches, bounded recovery, error
classification) that are awkward to force reliably against a real UI.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cuas.domain import AppContext, ErrorCode, TargetNotFoundError
from cuas.replay import ReplayEngine, ReplayStatus
from cuas.safety import LayeredPolicyEngine, RiskBasedPolicyEngine
from tests.fixtures.fake_surface import FakeSurfaceAdapter
from tests.fixtures.sample_artifacts import approval_required_capability, blocked_capability, get_savings_balance

CONTEXT = AppContext(vendor="meridian-demo", application="credit-union-admin", version="1.0.0", tenant_id="base")


def _engine(fake: FakeSurfaceAdapter) -> ReplayEngine:
    return ReplayEngine(fake, RiskBasedPolicyEngine())


@pytest.mark.asyncio
async def test_layered_policy_engine_is_a_drop_in_replacement_for_risk_based_policy() -> None:
    """ReplayEngine depends on the PolicyEngine ABC, not a concrete class
    (.CLAUDE/01_ARCHITECTURE.md). Swapping in Phase 6's LayeredPolicyEngine
    -- with no app/tenant policy configured, so get_savings_balance's own
    intents ("enter_member_id", "submit_member_search") fall through to
    its risk-fallback -- must replay the real capability identically to
    Phase 5's RiskBasedPolicyEngine. This is the regression check for
    "preserve current replay behavior" while moving the runtime policy
    engine forward.
    """

    artifact = get_savings_balance()
    fake = FakeSurfaceAdapter()
    fake.script_read(artifact.outputs["savings_balance"].source, "$18204.55")

    engine = ReplayEngine(fake, LayeredPolicyEngine())
    result = await engine.run(artifact, {"member_id": "M1001"}, CONTEXT)

    assert result.status == ReplayStatus.SUCCESS
    assert result.outputs["savings_balance"] == Decimal("18204.55")


@pytest.mark.asyncio
async def test_success_path_extracts_and_types_the_output() -> None:
    artifact = get_savings_balance()
    fake = FakeSurfaceAdapter()
    fake.script_read(artifact.outputs["savings_balance"].source, "$18204.55")

    result = await _engine(fake).run(artifact, {"member_id": "M1001"}, CONTEXT)

    assert result.status == ReplayStatus.SUCCESS
    assert result.outputs["savings_balance"] == Decimal("18204.55")
    assert any(call[0] == "fill" for call in fake.calls)


@pytest.mark.asyncio
async def test_business_outcome_is_not_reported_as_a_failure() -> None:
    artifact = get_savings_balance()
    fake = FakeSurfaceAdapter()
    submit_step = next(s for s in artifact.steps if s.id == "submit_search")
    fake.script_wait(submit_step.checkpoint, Exception("Accounts panel never appeared"))
    # MEMBER_NOT_FOUND's own detect condition is left unscripted -> succeeds by default.

    result = await _engine(fake).run(artifact, {"member_id": "does-not-exist"}, CONTEXT)

    assert result.status == ReplayStatus.BUSINESS_OUTCOME
    assert result.business_outcome_code == "MEMBER_NOT_FOUND"
    assert result.error_code is None


@pytest.mark.asyncio
async def test_recoverable_condition_is_dismissed_and_the_action_is_retried() -> None:
    artifact = get_savings_balance()
    fake = FakeSurfaceAdapter()
    fill_target = artifact.steps[0].target
    assert artifact.steps[0].id == "fill_member_id"
    fake.script_fill(fill_target, TargetNotFoundError("covered by session-notice overlay"))
    fake.script_read(artifact.outputs["savings_balance"].source, "$9900.00")
    # Popup's own detect condition (VISIBLE on the OK button) is left
    # unscripted -> "the popup is present" by default, so recovery fires.

    result = await _engine(fake).run(artifact, {"member_id": "M1002"}, CONTEXT)

    assert result.status == ReplayStatus.SUCCESS
    fill_calls = [c for c in fake.calls if c[0] == "fill"]
    assert len(fill_calls) == 2, "expected exactly one bounded retry after recovery"
    dismiss_target = artifact.recoverable_conditions[0].dismiss_target
    from tests.fixtures.fake_surface import _target_key

    assert any(c[0] == "click" and c[1] == _target_key(dismiss_target) for c in fake.calls)


@pytest.mark.asyncio
async def test_recovery_is_bounded_to_one_retry_then_fails() -> None:
    artifact = get_savings_balance()
    fake = FakeSurfaceAdapter()
    fill_target = artifact.steps[0].target
    fake.script_fill(
        fill_target,
        TargetNotFoundError("covered by overlay (1st)"),
        TargetNotFoundError("covered by overlay (2nd, still failing after recovery)"),
    )

    result = await _engine(fake).run(artifact, {"member_id": "M1001"}, CONTEXT)

    assert result.status == ReplayStatus.FAILED
    assert result.error_code == ErrorCode.TARGET_NOT_FOUND
    fill_calls = [c for c in fake.calls if c[0] == "fill"]
    assert len(fill_calls) == 2, "must not retry more than once"


@pytest.mark.asyncio
async def test_failure_with_no_matching_recoverable_condition_is_not_retried() -> None:
    artifact = get_savings_balance().model_copy(update={"recoverable_conditions": []})
    fake = FakeSurfaceAdapter()
    fill_target = artifact.steps[0].target
    fake.script_fill(fill_target, TargetNotFoundError("no recovery defined for this"))

    result = await _engine(fake).run(artifact, {"member_id": "M1001"}, CONTEXT)

    assert result.status == ReplayStatus.FAILED
    fill_calls = [c for c in fake.calls if c[0] == "fill"]
    assert len(fill_calls) == 1, "no recoverable_condition matched, so no retry should have been attempted"


@pytest.mark.asyncio
async def test_output_extraction_failure_is_classified_not_silently_successful() -> None:
    artifact = get_savings_balance()
    fake = FakeSurfaceAdapter()
    fake.script_read(artifact.outputs["savings_balance"].source, "not-a-number")

    result = await _engine(fake).run(artifact, {"member_id": "M1001"}, CONTEXT)

    assert result.status == ReplayStatus.FAILED
    assert result.error_code == ErrorCode.OUTPUT_EXTRACTION_FAILED


@pytest.mark.asyncio
async def test_policy_blocks_a_blocked_risk_action_without_executing_it() -> None:
    artifact = blocked_capability()
    fake = FakeSurfaceAdapter()

    result = await _engine(fake).run(artifact, {}, CONTEXT)

    assert result.status == ReplayStatus.BLOCKED
    assert result.escalation_step_id == "only_step"
    assert fake.calls == [], "a BLOCKED action must never reach the surface"


@pytest.mark.asyncio
async def test_policy_pauses_an_approval_required_action_without_executing_it() -> None:
    artifact = approval_required_capability()
    fake = FakeSurfaceAdapter()

    result = await _engine(fake).run(artifact, {}, CONTEXT)

    assert result.status == ReplayStatus.APPROVAL_REQUIRED
    assert result.escalation_step_id == "only_step"
    assert fake.calls == [], "an APPROVAL_REQUIRED action must not execute before an operator approves it"


@pytest.mark.asyncio
async def test_missing_required_input_is_rejected_before_touching_the_surface() -> None:
    artifact = get_savings_balance()
    fake = FakeSurfaceAdapter()

    with pytest.raises(ValueError):
        await _engine(fake).run(artifact, {}, CONTEXT)

    assert fake.calls == []
