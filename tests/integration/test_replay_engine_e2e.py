"""End-to-end ReplayEngine tests: the exact ReplayEngine
(src/cuas/replay/engine.py) driving a real PlaywrightSurfaceAdapter against
the real demo_app (tests/integration/conftest.py starts it as a
subprocess), running the real get_savings_balance() artifact
(tests/fixtures/sample_artifacts.py) end to end -- no fakes, no mocks, and
no LLM anywhere in the path.

Unlike tests/integration/test_playwright_surface_adapter.py, this file
deliberately does NOT dismiss the session-notice popup by hand before
running the engine: leaving it up is what proves ReplayEngine's own
bounded-recovery path (engine.py's _attempt_recovery, triggered when
fill_member_id's fill() first fails because the popup overlay is blocking
the textbox) actually fires against a real browser, not just against
tests/unit/test_replay_engine.py's scripted FakeSurfaceAdapter.

Marked `integration` (pyproject.toml): needs a real browser, never an
LLM/API key.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cuas.domain import AppContext
from cuas.replay import ReplayEngine, ReplayStatus
from cuas.safety import RiskBasedPolicyEngine
from cuas.surface.playwright_adapter import launch_playwright_surface
from tests.fixtures.sample_artifacts import get_savings_balance

pytestmark = pytest.mark.integration

CONTEXT = AppContext(vendor="meridian-demo", application="credit-union-admin", version="1.0.0", tenant_id="base")


@pytest.mark.asyncio
async def test_success_path_dismisses_popup_and_returns_typed_balance(demo_app_base_url: str) -> None:
    """Fresh page load -> popup is up -> the engine's own recovery dismisses
    it mid-step (not a hand-authored test setup step) -> search proceeds ->
    a real, correctly-typed Decimal output is extracted from the iframe."""

    async with launch_playwright_surface() as surface:
        await surface.navigate(demo_app_base_url + "/")

        engine = ReplayEngine(surface, RiskBasedPolicyEngine())
        result = await engine.run(get_savings_balance(), {"member_id": "M1001"}, CONTEXT)

    assert result.status == ReplayStatus.SUCCESS
    assert result.outputs["savings_balance"] == Decimal("18204.55")
    assert isinstance(result.outputs["savings_balance"], Decimal)

    recovery_events = [e for e in result.step_log if e.event == "recoverable_condition_detected"]
    assert len(recovery_events) == 1
    assert recovery_events[0].detail == "SESSION_NOTICE_POPUP"


@pytest.mark.asyncio
async def test_unknown_member_is_reported_as_a_business_outcome_not_a_failure(demo_app_base_url: str) -> None:
    """No member matches -> the "Accounts" checkpoint never appears, but
    that is a known, modeled alternative state (MEMBER_NOT_FOUND), not an
    automation failure."""

    async with launch_playwright_surface() as surface:
        await surface.navigate(demo_app_base_url + "/")

        engine = ReplayEngine(surface, RiskBasedPolicyEngine())
        result = await engine.run(get_savings_balance(), {"member_id": "no-such-member"}, CONTEXT)

    assert result.status == ReplayStatus.BUSINESS_OUTCOME
    assert result.business_outcome_code == "MEMBER_NOT_FOUND"
    assert result.error_code is None
    assert result.outputs == {}


@pytest.mark.asyncio
async def test_ambiguous_match_with_no_known_outcome_is_a_hard_failure(demo_app_base_url: str) -> None:
    """"Smith" matches two seeded members (M1002, M1004): the search
    re-renders a results table instead of redirecting to a single member,
    so neither the "Accounts" checkpoint nor the "Member not found."
    business outcome ever appears. This is a genuinely unmodeled state --
    exactly the case that should surface as a structured FAILED result
    with a real error_code instead of the engine improvising or hanging."""

    async with launch_playwright_surface() as surface:
        await surface.navigate(demo_app_base_url + "/")

        engine = ReplayEngine(surface, RiskBasedPolicyEngine())
        result = await engine.run(get_savings_balance(), {"member_id": "Smith"}, CONTEXT)

    assert result.status == ReplayStatus.FAILED
    assert result.error_code is not None
    assert result.escalation_step_id is None  # FAILED carries error_code, not an approval/deny escalation
    assert any(e.event == "checkpoint_failed" for e in result.step_log)
