"""End-to-end proof that artifact construction is genuinely reusable, not
just a replay of the exact discovery run it came from: a real
DiscoveryEngine run against the real demo app (FakeLLMClient, no LLM key)
produces a real successful DiscoveryTrace; ArtifactBuilder turns that
trace into an Artifact; the resulting Artifact then replays successfully
through the real ReplayEngine against a *fresh* page load, with a
*different* member id than the one used during discovery.

Marked `integration` (pyproject.toml): needs a real browser, never an
LLM/API key.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cuas.artifact.schema import BusinessOutcome
from cuas.artifact_builder import ArtifactBuilder
from cuas.discovery.engine import DiscoveryEngine
from cuas.discovery.models import DiscoveryGoal, DiscoveryStatus
from cuas.discovery.trace import FileDiscoveryTraceStore
from cuas.domain import AppContext
from cuas.replay import ReplayEngine, ReplayStatus
from cuas.safety import LayeredPolicyEngine
from cuas.surface.adapter import WaitCondition, WaitConditionKind
from cuas.surface.playwright_adapter import launch_playwright_surface
from tests.fixtures.fake_llm_client import FakeLLMClient, css_target, propose, propose_done, role_target

pytestmark = pytest.mark.integration

CONTEXT = AppContext(vendor="meridian-demo", application="credit-union-admin", version="1.0.0", tenant_id="base")


@pytest.mark.asyncio
async def test_artifact_built_from_a_real_discovery_run_replays_with_a_different_input(demo_app_base_url: str, tmp_path) -> None:
    # Intents must be ones LayeredPolicyEngine's global default table
    # actually recognizes as SAFE (search_member/view_account/
    # open_member_record) -- an unrecognized intent with no explicitly-set
    # risk fails closed to REQUIRE_APPROVAL by design (safety/policy.py),
    # so this is not a free-form label, it's exercising the real policy
    # table discovery is actually governed by.
    llm = FakeLLMClient(
        propose("click", "dismiss_known_popup", target=role_target("button", "OK")),
        propose("fill", "search_member", target=role_target("textbox"), value="M1001"),
        propose("click", "view_account", target=role_target("button", "Search")),
        propose("read", "open_member_record", target=css_target("#acct-row-2 td:nth-child(3)", frame="#accounts-frame")),
        propose_done("the savings balance has been read"),
    )
    goal = DiscoveryGoal(
        capability_id="get_savings_balance",
        description="Find member M1001 and read their savings balance.",
        start_url=demo_app_base_url + "/",
        inputs={"member_id": "M1001"},
        sensitive_inputs={"member_id"},
        success_checkpoint=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Accounts"),
        known_business_outcomes=[
            BusinessOutcome(code="MEMBER_NOT_FOUND", detect=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Member not found."))
        ],
    )
    trace_store = FileDiscoveryTraceStore(tmp_path)

    async with launch_playwright_surface() as surface:
        engine = DiscoveryEngine(surface, LayeredPolicyEngine(), llm, trace_store=trace_store)
        discovery_result = await engine.run(goal, CONTEXT)

    assert discovery_result.status == DiscoveryStatus.SUCCESS
    trace = trace_store.load(discovery_result.run_id)

    artifact = ArtifactBuilder().build(trace, goal, CONTEXT)

    assert len(artifact.steps) == 3  # dismiss + fill + click; the READ became an output, not a Step
    assert [s.action_type.value for s in artifact.steps] == ["click", "fill", "click"]
    assert artifact.steps[1].value == "{{member_id}}"
    assert "M1001" not in artifact.model_dump_json()

    # The real proof of reuse: replay the *constructed* artifact, fresh
    # page load, with a member id that was never part of the discovery
    # run at all.
    async with launch_playwright_surface() as replay_surface:
        await replay_surface.navigate(demo_app_base_url + "/")
        replay_engine = ReplayEngine(replay_surface, LayeredPolicyEngine())
        replay_result = await replay_engine.run(artifact, {"member_id": "M1002"}, CONTEXT)

    assert replay_result.status == ReplayStatus.SUCCESS
    output_name = artifact.success_condition.output
    assert replay_result.outputs[output_name] == Decimal("9900.00")
