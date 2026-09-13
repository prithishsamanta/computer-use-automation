"""End-to-end proof that `RunOrchestrator` itself -- not just `ReplayEngine`
in isolation -- works against a real Playwright surface and the real demo
app, from a genuinely fresh browser acquired the same way the real API
does (`orchestration/orchestrator.py`'s own `surface_factory()` call, not
a hand-navigated one).

This closes a real gap every earlier integration test left open:
`tests/integration/test_replay_engine_e2e.py` drives `ReplayEngine`
directly and always navigates the surface to `demo_app_base_url + "/"`
itself before calling `engine.run()`; `tests/unit/test_run_orchestrator.py`
drives the real `RunOrchestrator` but only ever with `FakeSurfaceAdapter`,
which has no notion of "what page am I on" at all. Neither ever proved
that `RunOrchestrator`'s own surface-acquisition path -- a brand-new
context/page via `launch_playwright_surface()`, with nothing in between
that navigates it anywhere -- actually lands `ReplayEngine` somewhere its
artifact's first step can act on.

It didn't, for `get_savings_balance()` specifically: that fixture's own
steps assume the surface is already on the demo app's search page (true
only because every caller navigated first, by hand). Registered as a real
capability and invoked through the real Docker container, every request
failed at the first FILL step with `TARGET_NOT_FOUND` (`about:blank` has
no textbox). This file reproduces exactly that failure mode against a
fresh `launch_playwright_surface()` surface and proves the fix: a small,
deployment-scoped artifact variant with its own leading NAVIGATE step
(the same shape `scripts/register_get_savings_balance_capability.py`
builds for the real container, pointed at this test's own ephemeral
`demo_app_base_url` instead of the Compose hostname) replays correctly
through the unmodified, real `RunOrchestrator`.

Marked `integration` (pyproject.toml): needs a real browser, never an
LLM/API key.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cuas.artifact import Artifact, FileArtifactRepository, Step
from cuas.capability import CapabilityRecord, CapabilityService, CapabilityStatus, FileCapabilityRepository
from cuas.discovery import FileDiscoveryTraceStore
from cuas.domain import ActionType, AppContext, RiskLevel
from cuas.handoff import InMemoryInterventionRepository
from cuas.orchestration import RunOrchestrator, RunOutcome
from cuas.safety import LayeredPolicyEngine
from cuas.surface.adapter import WaitCondition, WaitConditionKind
from cuas.surface.playwright_adapter import launch_playwright_surface
from tests.fixtures.sample_artifacts import get_savings_balance

pytestmark = pytest.mark.integration

VENDOR = "meridian-demo"
APPLICATION = "credit-union-admin"
CAPABILITY_ID = "get_savings_balance"
VERSION = "1.0.1"

CONTEXT = AppContext(vendor=VENDOR, application=APPLICATION, version="1.0.0", tenant_id="base")


def _deployed_artifact(base_url: str) -> Artifact:
    """Same construction as scripts/register_get_savings_balance_capability.py's
    build_deployed_artifact() -- the exact, unmodified get_savings_balance()
    fixture plus one leading NAVIGATE step -- parameterized by base_url
    instead of the Compose hostname, since this test's demo app runs on a
    fresh ephemeral port per session (tests/integration/conftest.py)."""

    base = get_savings_balance(version=VERSION)
    navigate_to_search_page = Step(
        id="open_search_page",
        action_type=ActionType.NAVIGATE,
        intent="search_member",  # ALLOW in DEFAULT_GLOBAL_INTENT_POLICY
        value=f"{base_url}/",
        risk=RiskLevel.SAFE,
        checkpoint=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Member ID or Last Name"),
    )
    data = base.model_dump(exclude={"steps"})
    return Artifact(**data, steps=[navigate_to_search_page, *base.steps])


def _orchestrator(tmp_path, demo_app_base_url: str) -> RunOrchestrator:
    artifact_repo = FileArtifactRepository(tmp_path / "artifacts")
    capability_repo = FileCapabilityRepository(tmp_path / "capabilities")
    capability_service = CapabilityService(capability_repo, artifact_repo)

    artifact_repo.save(_deployed_artifact(demo_app_base_url))
    capability_repo.save(
        CapabilityRecord(
            capability_id=CAPABILITY_ID,
            name="Get Savings Balance",
            vendor=VENDOR,
            application=APPLICATION,
            supported_versions=["1.x"],
            tenant_scope="base",
            artifact_version=VERSION,
            status=CapabilityStatus.ACTIVE,
        )
    )

    return RunOrchestrator(
        capability_service,
        artifact_repo,
        LayeredPolicyEngine(),
        launch_playwright_surface,  # the real factory -- a brand-new, unnavigated surface each call
        InMemoryInterventionRepository(),
        FileDiscoveryTraceStore(tmp_path / "discovery_traces"),
    )


@pytest.mark.asyncio
async def test_success_from_a_fresh_orchestrator_acquired_surface(demo_app_base_url: str, tmp_path) -> None:
    orchestrator = _orchestrator(tmp_path, demo_app_base_url)

    result = await orchestrator.run_capability(CAPABILITY_ID, {"member_id": "M1001"}, CONTEXT)

    assert result.outcome == RunOutcome.SUCCESS
    assert result.outputs["savings_balance"] == Decimal("18204.55")


@pytest.mark.asyncio
async def test_unknown_member_is_a_business_outcome_through_the_real_orchestrator(
    demo_app_base_url: str, tmp_path
) -> None:
    orchestrator = _orchestrator(tmp_path, demo_app_base_url)

    result = await orchestrator.run_capability(CAPABILITY_ID, {"member_id": "no-such-member"}, CONTEXT)

    assert result.outcome == RunOutcome.BUSINESS_OUTCOME
    assert result.business_outcome_code == "MEMBER_NOT_FOUND"


@pytest.mark.asyncio
async def test_ambiguous_match_pauses_for_intervention_through_the_real_orchestrator(
    demo_app_base_url: str, tmp_path
) -> None:
    """"Smith" matches two seeded members -- a genuinely unmodeled state,
    same as test_replay_engine_e2e.py's own ambiguous-match test -- except
    here it must also prove RunOrchestrator's own escalation path: a real,
    still-open browser session and a real intervention_id, not just a
    FAILED ReplayResult."""

    orchestrator = _orchestrator(tmp_path, demo_app_base_url)

    result = await orchestrator.run_capability(CAPABILITY_ID, {"member_id": "Smith"}, CONTEXT)

    assert result.outcome == RunOutcome.FAILED
    assert result.intervention_id is not None
    assert result.session_id is not None

    # Clean up the paused session/browser the same way a real operator
    # eventually would (cancel, not resume -- nothing to approve here),
    # rather than leaking a live Playwright process past this test.
    await orchestrator.cancel_intervention(result.intervention_id, operator_id="test-harness")
