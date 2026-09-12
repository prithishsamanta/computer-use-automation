"""End-to-end DiscoveryEngine tests: the exact DiscoveryEngine
(src/cuas/discovery/engine.py) driving a real PlaywrightSurfaceAdapter
against the real demo_app (tests/integration/conftest.py starts it as a
subprocess), with a FakeLLMClient standing in for an actual model -- no
ANTHROPIC_API_KEY is ever needed for this file, or for CI.

Unlike tests/integration/test_replay_engine_e2e.py, discovery has no
artifact-declared recoverable_conditions to mechanically dismiss the
session-notice popup that appears on a fresh page load -- it is the
model's own job to notice and dismiss it, so the very first scripted
proposal in these tests is dismissing that popup, not the member search
itself. This is the one behavior these tests exist specifically to prove
against a real browser rather than FakeSurfaceAdapter's scripted
responses (tests/unit/test_discovery_engine.py already covers
DiscoveryEngine's own branching logic in isolation).

Marked `integration` (pyproject.toml): needs a real browser, never an
LLM/API key.
"""

from __future__ import annotations

import pytest

from cuas.artifact.schema import BusinessOutcome
from cuas.discovery.engine import DiscoveryEngine
from cuas.discovery.models import DiscoveryGoal, DiscoveryStatus
from cuas.domain import AppContext
from cuas.safety import LayeredPolicyEngine
from cuas.surface.adapter import WaitCondition, WaitConditionKind
from cuas.surface.playwright_adapter import launch_playwright_surface
from tests.fixtures.fake_llm_client import FakeLLMClient, propose, propose_done, role_target

pytestmark = pytest.mark.integration

CONTEXT = AppContext(vendor="meridian-demo", application="credit-union-admin", version="1.0.0", tenant_id="base")


@pytest.mark.asyncio
async def test_discovery_engine_finds_a_member_and_verifies_its_own_success_claim(demo_app_base_url: str) -> None:
    """Four scripted turns -- dismiss the real popup, fill the real
    textbox, click the real search button, then declare done -- driven
    through the exact observe/parse/policy/execute/observe loop against a
    real browser. The model's "done" is not just trusted: a declared
    success_checkpoint is verified against the live page before the run
    is allowed to report SUCCESS."""

    llm = FakeLLMClient(
        propose("click", "dismiss_known_popup", target=role_target("button", "OK")),
        propose("fill", "search_member", target=role_target("textbox"), value="M1001"),
        propose("click", "view_account", target=role_target("button", "Search")),
        propose_done("the Accounts panel is now visible"),
    )

    goal = DiscoveryGoal(
        capability_id="get_savings_balance",
        description="Find member M1001 and open their accounts view.",
        start_url=demo_app_base_url + "/",
        inputs={"member_id": "M1001"},
        sensitive_inputs={"member_id"},
        success_checkpoint=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Accounts"),
    )

    async with launch_playwright_surface() as surface:
        engine = DiscoveryEngine(surface, LayeredPolicyEngine(), llm)
        result = await engine.run(goal, CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert result.steps_taken == 4
    assert [entry.outcome for entry in result.history] == ["executed", "executed", "executed"]
    # The raw member id was needed by (fake) "the model", but must not
    # survive into what DiscoveryEngine hands back.
    assert "M1001" not in repr(result.history)


@pytest.mark.asyncio
async def test_discovery_engine_detects_member_not_found_as_a_business_outcome(demo_app_base_url: str) -> None:
    """A search for a nonexistent member is a known, modeled alternative
    end state, detected deterministically against the real rendered page
    -- independent of anything the (fake) model would have said next; note
    the script below never gets a chance to run its would-be 4th ("done")
    turn because the outcome is caught first."""

    llm = FakeLLMClient(
        propose("click", "dismiss_known_popup", target=role_target("button", "OK")),
        propose("fill", "search_member", target=role_target("textbox"), value="no-such-member"),
        propose("click", "view_account", target=role_target("button", "Search")),
    )

    goal = DiscoveryGoal(
        capability_id="get_savings_balance",
        description="Find member no-such-member and open their accounts view.",
        start_url=demo_app_base_url + "/",
        inputs={"member_id": "no-such-member"},
        known_business_outcomes=[
            BusinessOutcome(
                code="MEMBER_NOT_FOUND",
                detect=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Member not found."),
            )
        ],
    )

    async with launch_playwright_surface() as surface:
        engine = DiscoveryEngine(surface, LayeredPolicyEngine(), llm)
        result = await engine.run(goal, CONTEXT)

    assert result.status == DiscoveryStatus.BUSINESS_OUTCOME
    assert result.business_outcome_code == "MEMBER_NOT_FOUND"
