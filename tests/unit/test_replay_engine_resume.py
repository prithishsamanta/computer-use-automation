"""ReplayEngine's `resume_from_step_id` parameter (Phase 12) in isolation
-- no orchestrator, no SessionRegistry, just proving the engine-level
mechanics: a resumed run skips already-executed steps, skips the resumed
step's own policy check (it was already approved/allowed the first time),
and the `RESUME_AFTER_ALL_STEPS` sentinel goes straight to output
extraction. `tests/unit/test_intervention_lifecycle.py` covers the same
concepts end to end, through `RunOrchestrator`'s pause/claim/complete/
resume lifecycle; this file isolates the one thing ReplayEngine itself is
responsible for getting right.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cuas.domain import AppContext
from cuas.replay import ReplayEngine, ReplayStatus
from cuas.safety import LayeredPolicyEngine
from tests.fixtures.fake_surface import FakeSurfaceAdapter
from tests.fixtures.sample_artifacts import approval_required_capability, get_savings_balance

CONTEXT = AppContext(vendor="meridian-demo", application="credit-union-admin", version="1.0.0", tenant_id="base")


async def test_resume_from_step_id_skips_earlier_steps_and_their_reexecution() -> None:
    artifact = get_savings_balance()
    fake = FakeSurfaceAdapter()
    checkpoint = artifact.steps[1].checkpoint
    assert checkpoint is not None
    fake.script_wait(checkpoint, None)
    fake.script_read(artifact.outputs["savings_balance"].source, "9,900.00")

    engine = ReplayEngine(fake, LayeredPolicyEngine())
    result = await engine.run(
        artifact, {"member_id": "M1001"}, CONTEXT, resume_from_step_id="submit_search"
    )

    assert result.status == ReplayStatus.SUCCESS
    assert result.outputs["savings_balance"] == Decimal("9900.00")
    # "fill_member_id" is the step *before* the resume point -- it must
    # not be re-executed against the (already-progressed) live surface.
    assert not any(call[0] == "fill" for call in fake.calls)
    assert any(call[0] == "click" for call in fake.calls)


async def test_resume_after_all_steps_sentinel_skips_the_step_loop_entirely() -> None:
    artifact = get_savings_balance()
    fake = FakeSurfaceAdapter()
    fake.script_read(artifact.outputs["savings_balance"].source, "1,234.56")

    engine = ReplayEngine(fake, LayeredPolicyEngine())
    result = await engine.run(
        artifact, {"member_id": "M1001"}, CONTEXT, resume_from_step_id=ReplayEngine.RESUME_AFTER_ALL_STEPS
    )

    assert result.status == ReplayStatus.SUCCESS
    assert result.outputs["savings_balance"] == Decimal("1234.56")
    assert not any(call[0] in ("fill", "click") for call in fake.calls)


async def test_resume_from_unknown_step_id_raises_value_error() -> None:
    artifact = get_savings_balance()
    engine = ReplayEngine(FakeSurfaceAdapter(), LayeredPolicyEngine())

    with pytest.raises(ValueError, match="no_such_step"):
        await engine.run(artifact, {"member_id": "M1001"}, CONTEXT, resume_from_step_id="no_such_step")


async def test_resuming_an_approval_required_step_executes_it_without_a_second_policy_check() -> None:
    artifact = approval_required_capability()
    fake = FakeSurfaceAdapter()
    engine = ReplayEngine(fake, LayeredPolicyEngine())

    # First attempt: policy pauses before executing anything.
    first = await engine.run(artifact, {}, CONTEXT)
    assert first.status == ReplayStatus.APPROVAL_REQUIRED
    assert not fake.calls

    # A human approved it -- resuming must execute the step (not ask
    # policy again, which would just re-pause forever) and complete.
    second = await engine.run(artifact, {}, CONTEXT, resume_from_step_id="only_step")
    assert second.status == ReplayStatus.SUCCESS
    assert any(call[0] == "click" for call in fake.calls)
