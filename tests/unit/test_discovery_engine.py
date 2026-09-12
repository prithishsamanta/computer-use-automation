"""DiscoveryEngine's own branching logic, against a scriptable fake
surface and a scriptable fake LLM -- fast, deterministic, no network, no
API key. tests/integration/test_discovery_engine_e2e.py covers the same
engine against the real demo app through a real browser (still with
FakeLLMClient -- no LLM/API key is ever needed for CI, per this phase's
explicit instruction).
"""

from __future__ import annotations

import pytest

from cuas.discovery.engine import DiscoveryEngine
from cuas.discovery.models import DiscoveryGoal, DiscoveryLimits, DiscoveryStatus
from cuas.discovery.trace import FileDiscoveryTraceStore
from cuas.domain import AppContext, TargetNotFoundError
from cuas.observability import EventType
from cuas.safety import LayeredPolicyEngine
from cuas.surface.adapter import Evidence, Observation, WaitCondition, WaitConditionKind
from cuas.artifact.schema import BusinessOutcome
from tests.fixtures.fake_llm_client import FakeLLMClient, css_target, malformed, propose, propose_done, role_target
from tests.fixtures.fake_surface import FakeSurfaceAdapter
from tests.fixtures.in_memory_event_sink import InMemoryEventSink

CONTEXT = AppContext(vendor="meridian-demo", application="credit-union-admin", version="1.0.0", tenant_id="base")


def _goal(**overrides) -> DiscoveryGoal:
    defaults = dict(
        capability_id="get_savings_balance",
        description="Find member M1001 and read their savings balance.",
        start_url="http://fake.invalid/",
        inputs={"member_id": "M1001"},
        sensitive_inputs={"member_id"},
    )
    defaults.update(overrides)
    return DiscoveryGoal(**defaults)


def _engine(fake: FakeSurfaceAdapter, llm: FakeLLMClient, **kwargs) -> DiscoveryEngine:
    return DiscoveryEngine(fake, LayeredPolicyEngine(), llm, **kwargs)


@pytest.mark.asyncio
async def test_normal_successful_discovery_executes_allowed_actions_and_stops_on_done() -> None:
    """Two SAFE-classified actions (search_member, view_account -- real
    entries in DEFAULT_GLOBAL_INTENT_POLICY) execute through the fake
    surface, then the model declares done with no declared
    success_checkpoint to verify against, so its claim is accepted."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose("fill", "search_member", target=role_target("textbox"), value="M1001"),
        propose("click", "view_account", target=role_target("button", "Search")),
        propose_done("the savings balance is now visible"),
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert result.steps_taken == 3
    assert [call[0] for call in fake.calls if call[0] in ("fill", "click")] == ["fill", "click"]
    # The raw member id was legitimately sent to "the model" (llm.calls
    # carries what FakeLLMClient was actually invoked with, unredacted --
    # that's correct, the model needs the real value)...
    assert llm.calls[0][2].url == "http://fake.invalid/"
    # ...but never comes back raw in what DiscoveryEngine hands to a caller.
    assert result.history[0].action.value == "{{member_id}}"
    assert "M1001" not in repr(result.history)


@pytest.mark.asyncio
async def test_malformed_model_output_stops_immediately_without_retry() -> None:
    """No repair attempt, no second call to the model -- exactly one
    scripted response is consumed."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(malformed("<garbage>"))
    sink = InMemoryEventSink()

    result = await _engine(fake, llm, event_sink=sink).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.MALFORMED_MODEL_OUTPUT
    assert result.steps_taken == 0
    assert result.error_message is not None
    assert len(sink.events_of(EventType.MALFORMED_MODEL_OUTPUT)) == 1
    assert len(sink.events_of(EventType.RUN_COMPLETED)) == 1


@pytest.mark.asyncio
async def test_action_type_specific_incompleteness_is_a_recoverable_execution_failure() -> None:
    """A fill proposal with no `value` is structurally valid JSON (action_type
    and intent both parse fine) but semantically incomplete for FILL --
    DiscoveryEngine does not treat this as "malformed model output" (that
    label is reserved for output the provider/parser itself could not
    make sense of at all); it surfaces as a ValueError from execution,
    handled exactly like any other execution failure: reported back to
    the model as history rather than guessed at or hard-aborted."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose("fill", "search_member", target=role_target("textbox")),  # no value
        propose_done("recovered"),
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert result.history[0].outcome == "execution_failed"
    assert "requires target and value" in result.history[0].error_message


@pytest.mark.asyncio
async def test_repeated_identical_proposals_are_detected_as_a_loop() -> None:
    """max_consecutive_repeats defaults to 2: two identical proposals are
    tolerated (they execute normally), the third aborts the run instead of
    letting the model spin forever."""

    fake = FakeSurfaceAdapter()
    same = lambda: propose("click", "search_member", target=role_target("button", "Search"))
    llm = FakeLLMClient(same(), same(), same())

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.LOOP_DETECTED
    assert result.steps_taken == 2  # aborted on the 3rd (index-2) turn, after 2 executed
    assert len(result.history) == 2


@pytest.mark.asyncio
async def test_policy_denied_action_stops_the_run_rather_than_trying_something_else() -> None:
    """navigate_unauthorized_domain is DENY in the global default policy
    table -- discovery must escalate/stop here, not quietly try a
    different action on its own."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose("click", "navigate_unauthorized_domain", target=role_target("link", "External Site")),
        propose_done(),  # must never be consumed -- BLOCKED is terminal
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.BLOCKED
    assert result.escalation_step_index == 0
    assert result.escalation_reason is not None
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_approval_required_action_escalates_instead_of_executing() -> None:
    """close_account is REQUIRE_APPROVAL in the global default policy
    table -- a consequential action an LLM must never be allowed to just
    execute during unattended discovery."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close Account")))

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.APPROVAL_REQUIRED
    assert result.escalation_step_index == 0
    assert not any(call[0] == "click" for call in fake.calls)


@pytest.mark.asyncio
async def test_model_proposing_a_nonexistent_target_is_a_recoverable_execution_failure() -> None:
    """Unlike a policy escalation, a TargetNotFoundError against the live
    surface is NOT terminal: discovery has a model in the loop that can
    react to it, so the failure is recorded and fed back as history
    instead of ending the run -- this is the one place discovery differs
    from ReplayEngine's mechanical, artifact-declared recovery."""

    fake = FakeSurfaceAdapter()
    bad_target = role_target("button", "DoesNotExist")

    # FakeSurfaceAdapter.script_click keys off a real Target, not a raw
    # dict -- build the same Target DiscoveryEngine will construct from
    # `bad_target` so the script actually matches at the seam we're
    # exercising (proposal dict -> Target -> click()).
    from cuas.domain import Locator, LocatorStrategy, Target

    real_target = Target(primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params={"role": "button", "name": "DoesNotExist"}))
    fake.script_click(real_target, TargetNotFoundError("no element matched role=button name='DoesNotExist'"))

    llm = FakeLLMClient(
        propose("click", "search_member", target=bad_target),
        propose_done("recovered and the balance is visible"),
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert result.steps_taken == 2
    assert result.history[0].outcome == "execution_failed"
    assert result.history[0].error_message is not None
    assert len(llm.calls) == 2
    # The second call to the model must have seen the first failure as
    # context -- proof the failure was fed back, not silently swallowed.
    assert llm.calls[1][3][0].outcome == "execution_failed"


@pytest.mark.asyncio
async def test_max_steps_exhaustion_terminates_the_run() -> None:
    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose("click", "search_member", target=role_target("button", "One")),
        propose("click", "view_account", target=role_target("button", "Two")),
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT, DiscoveryLimits(max_steps=2))

    assert result.status == DiscoveryStatus.MAX_STEPS_EXCEEDED
    assert result.steps_taken == 2
    assert len(llm.calls) == 2  # never asked for a 3rd turn


@pytest.mark.asyncio
async def test_business_outcome_is_detected_deterministically_without_asking_the_model() -> None:
    """A known business outcome is checked against the live surface at the
    top of every turn, before the model is ever consulted -- the model's
    opinion is not what determines this, exactly as replay's own
    business-outcome detection never asks anyone, it just checks."""

    fake = FakeSurfaceAdapter()  # unscripted wait_for succeeds immediately by default
    llm = FakeLLMClient()  # must never be called
    goal = _goal(
        known_business_outcomes=[
            BusinessOutcome(
                code="MEMBER_NOT_FOUND",
                detect=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Member not found."),
            )
        ]
    )

    result = await _engine(fake, llm).run(goal, CONTEXT)

    assert result.status == DiscoveryStatus.BUSINESS_OUTCOME
    assert result.business_outcome_code == "MEMBER_NOT_FOUND"
    assert result.steps_taken == 0
    assert llm.calls == []


@pytest.mark.asyncio
async def test_discovery_trace_is_persisted_separately_from_the_returned_result(tmp_path) -> None:
    """The full trace -- including redacted free text -- is written to
    disk keyed by run_id, independent of whatever the caller does with
    the returned DiscoveryResult."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose("fill", "search_member", target=role_target("textbox"), value="M1001"),
        propose_done("balance visible"),
    )
    trace_store = FileDiscoveryTraceStore(tmp_path)

    result = await _engine(fake, llm, trace_store=trace_store).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    loaded = trace_store.load(result.run_id)
    assert loaded.run_id == result.run_id
    assert loaded.capability_id == "get_savings_balance"
    assert loaded.final_status == "success"
    assert loaded.finished_at is not None
    assert len(loaded.steps) == 2
    assert (tmp_path / f"{result.run_id}.json").exists()
    # The raw sensitive input must never survive into the persisted trace.
    assert "M1001" not in loaded.model_dump_json()


@pytest.mark.asyncio
async def test_execution_failure_captures_evidence_but_policy_escalation_does_not() -> None:
    from cuas.domain import Locator, LocatorStrategy, Target

    fake = FakeSurfaceAdapter()
    fake.script_evidence(Evidence(url="http://fake.invalid/broken", screenshot_png=b"\x89PNG\r\n", visible_text_excerpt="oops"))
    real_target = Target(primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params={"role": "button", "name": "Ghost"}))
    fake.script_click(real_target, TargetNotFoundError("missing"))

    sink = InMemoryEventSink()
    llm = FakeLLMClient(
        propose("click", "search_member", target=role_target("button", "Ghost")),
        propose_done(),
    )

    result = await _engine(fake, llm, event_sink=sink).run(_goal(), CONTEXT)
    assert result.status == DiscoveryStatus.SUCCESS
    assert len(sink.events_of(EventType.EVIDENCE_CAPTURED)) == 1

    sink2 = InMemoryEventSink()
    llm2 = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close Account")))
    result2 = await _engine(FakeSurfaceAdapter(), llm2, event_sink=sink2).run(_goal(), CONTEXT)
    assert result2.status == DiscoveryStatus.APPROVAL_REQUIRED
    assert sink2.events_of(EventType.EVIDENCE_CAPTURED) == []
