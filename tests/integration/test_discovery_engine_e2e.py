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
from cuas.discovery.trace import FileDiscoveryTraceStore
from cuas.domain import AppContext
from cuas.safety import LayeredPolicyEngine
from cuas.surface.adapter import WaitCondition, WaitConditionKind
from cuas.surface.playwright_adapter import launch_playwright_surface
from tests.fixtures.fake_llm_client import FakeLLMClient, css_target, propose, propose_done, role_target

pytestmark = pytest.mark.integration

CONTEXT = AppContext(vendor="meridian-demo", application="credit-union-admin", version="1.0.0", tenant_id="base")


@pytest.mark.asyncio
async def test_discovery_engine_finds_a_member_and_verifies_its_own_success_claim(demo_app_base_url: str) -> None:
    """Five scripted turns -- dismiss the real popup, fill the real
    textbox, click the real search button, READ the savings balance, then
    declare done -- driven through the exact
    observe/parse/policy/execute/observe loop against a real browser. The
    model's "done" is not just trusted twice over: a declared
    success_checkpoint is verified against the live page, and (see
    DiscoveryEngine._has_materializable_progress) the run must have
    actually executed a READ, before it is allowed to report SUCCESS."""

    llm = FakeLLMClient(
        propose("click", "dismiss_known_popup", target=role_target("button", "OK")),
        propose("fill", "search_member", target=role_target("textbox"), value="M1001"),
        propose("click", "view_account", target=role_target("button", "Search")),
        propose("read", "open_member_record", target=css_target("#acct-row-2 td:nth-child(3)", frame="#accounts-frame")),
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
    assert result.steps_taken == 5
    assert [entry.outcome for entry in result.history] == ["executed", "executed", "executed", "executed"]
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
@pytest.mark.asyncio
async def test_resuming_an_approved_action_recovers_from_an_invalid_selector(
    demo_app_base_url: str, tmp_path
) -> None:
    """Reproduces, end to end against the real demo app, the exact gap a
    real Anthropic-backed run exposed (see DECISIONS_LOG.md): the model
    proposed reading the savings balance with a jQuery-only ':contains()'
    selector -- valid jQuery/Sizzle, never valid CSS or Playwright -- for
    an intent ('read_savings_account_balance') LayeredPolicyEngine has no
    opinion on at all, so it fails closed to REQUIRE_APPROVAL rather than
    silently trusting the model's implicit "safe" default (see policy.py's
    model_fields_set-based fail-closed design).

    Approving that intervention must execute the EXACT approved (still
    invalid) selector, not a repaired one discovery invents on its own --
    Playwright's own selector engine rejects it exactly as it did in the
    real run, and that failure becomes an ordinary "execution_failed"
    history entry (TargetNotFoundError, the same generic recoverable-
    failure path any other execution failure uses -- no special-casing
    anywhere for invalid selectors). Only then, seeing that failure fed
    back as history, does the (fake) model correct course with a real,
    valid Playwright selector (`:text-is()`, a genuine Playwright
    extension) and the run completes successfully -- also verifying the
    resumed run appended to, rather than overwrote, the pre-pause trace.
    """

    invalid_selector = "tr:has(td:first-child:contains('Savings')) td:nth-child(3)"
    corrected_selector = "tr:has(td:first-child:text-is('Savings')) td:nth-child(3)"

    escalating_llm = FakeLLMClient(
        propose("click", "dismiss_known_popup", target=role_target("button", "OK")),
        propose("fill", "search_member", target=role_target("textbox"), value="M1001"),
        propose("click", "view_account", target=role_target("button", "Search")),
        propose(
            "read", "read_savings_account_balance",
            target=css_target(invalid_selector, frame="#accounts-frame"),
        ),
    )

    goal = DiscoveryGoal(
        capability_id="get_savings_balance",
        description="Find member M1001 and read their savings balance.",
        start_url=demo_app_base_url + "/",
        inputs={"member_id": "M1001"},
        sensitive_inputs={"member_id"},
    )

    trace_store = FileDiscoveryTraceStore(tmp_path)

    # `read_savings_account_balance` is deliberately NOT allowlisted in
    # policy.py (the scope for this fix explicitly excludes that) -- so
    # EVERY proposal carrying this intent fails closed to REQUIRE_APPROVAL,
    # including the corrected one. Reproducing this faithfully therefore
    # takes two separate approve-and-resume cycles: one for the invalid
    # selector (which then fails to execute), and a second for the
    # corrected selector the model proposes in response (which succeeds).
    async with launch_playwright_surface() as surface:
        first = await DiscoveryEngine(surface, LayeredPolicyEngine(), escalating_llm, trace_store=trace_store).run(
            goal, CONTEXT
        )

        assert first.status == DiscoveryStatus.APPROVAL_REQUIRED
        pending = first.pending_approval
        assert pending is not None
        assert pending.pending_action.intent == "read_savings_account_balance"

        original_trace = trace_store.load(first.run_id)
        assert original_trace.steps[-1].outcome == "approval_required"

        # First approval: resume executes the EXACT approved (still
        # invalid) selector against the real, live page -- it fails, and
        # the model's very next proposal (the same unclassified intent,
        # so it ALSO requires approval) is the corrected selector.
        proposing_corrected_selector = FakeLLMClient(
            propose(
                "read", "read_savings_account_balance",
                target=css_target(corrected_selector, frame="#accounts-frame"),
            ),
        )
        second = await DiscoveryEngine(
            surface, LayeredPolicyEngine(), proposing_corrected_selector, trace_store=trace_store
        ).run(goal, CONTEXT, run_id=first.run_id, resume=pending)

        assert second.status == DiscoveryStatus.APPROVAL_REQUIRED
        # history[-2] is the bypass execution of the invalid selector
        # (appended by _execute_and_record just now); history[-1] is the
        # brand-new escalation for the corrected selector's own proposal
        # (same unclassified intent, so it too requires approval). Earlier
        # entries are the already-executed dismiss/fill/click turns.
        assert second.history[-2].outcome == "execution_failed"
        assert second.history[-2].action.intent == "read_savings_account_balance"
        assert "Could not resolve target" in second.history[-2].error_message
        assert second.history[-1].outcome == "approval_required"
        pending_corrected = second.pending_approval
        assert pending_corrected is not None
        assert pending_corrected.pending_action.target.primary.params["selector"] == corrected_selector

        # Second approval: resume executes the CORRECTED selector for
        # real -- it succeeds, materializing a READ the model's next
        # "done" claim can now be trusted for.
        finishing_llm = FakeLLMClient(propose_done("the savings balance is now visible"))
        third = await DiscoveryEngine(
            surface, LayeredPolicyEngine(), finishing_llm, trace_store=trace_store
        ).run(goal, CONTEXT, run_id=first.run_id, resume=pending_corrected)

    assert third.status == DiscoveryStatus.SUCCESS
    executed_reads = [entry for entry in third.history if entry.outcome == "executed" and entry.action.action_type.value == "read"]
    assert len(executed_reads) == 1
    assert executed_reads[0].read_value == "$18204.55"
    assert len(finishing_llm.calls) == 1

    final_trace = trace_store.load(first.run_id)
    # The original pre-pause steps are still there, byte-for-byte -- not
    # discarded for a fresh trace...
    assert final_trace.steps[: len(original_trace.steps)] == original_trace.steps
    # ...and every subsequent resumed attempt appended further steps on
    # top of them (failed selector, re-escalation, corrected READ, "done").
    assert len(final_trace.steps) > len(original_trace.steps)
    assert any(step.outcome == "execution_failed" for step in final_trace.steps)
