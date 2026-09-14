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
from cuas.domain import ActionType, AppContext, TargetNotFoundError
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
    """Three SAFE-classified actions (search_member, view_account x2 --
    real entries in DEFAULT_GLOBAL_INTENT_POLICY) execute through the fake
    surface -- including a READ, without which "done" would no longer be
    accepted at all, see _has_materializable_progress -- then the model
    declares done with no declared success_checkpoint to verify against,
    so its claim is accepted."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose("fill", "search_member", target=role_target("textbox"), value="M1001"),
        propose("click", "view_account", target=role_target("button", "Search")),
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
        propose_done("the savings balance is now visible"),
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert result.steps_taken == 4
    assert [call[0] for call in fake.calls if call[0] in ("fill", "click", "read")] == ["fill", "click", "read"]
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
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
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
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
        propose_done("recovered and the balance is visible"),
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert result.steps_taken == 3
    assert result.history[0].outcome == "execution_failed"
    assert result.history[0].error_message is not None
    assert len(llm.calls) == 3
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
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
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
    assert len(loaded.steps) == 3
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
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
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



# ---------------------------------------------------------------------------
# "Materializable progress" -- a discovery run must not be allowed to
# report SUCCESS on a "done" claim until it has executed at least one
# READ, because that is the exact same thing ArtifactBuilder itself
# already requires to build a reusable artifact at all (see
# DiscoveryEngine._has_materializable_progress and DECISIONS_LOG.md for
# the real Anthropic-backed run that first exposed this: the model saw
# the answer already visible on the page and declared done without ever
# proposing a READ, producing a trace ArtifactBuilder correctly refused
# to build anything from).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_done_with_no_executed_action_does_not_succeed_immediately() -> None:
    """The exact shape of the real bug: the model declares done on its
    very first turn, having executed nothing at all (the answer was
    already visible in the observation text). This must not be accepted
    as SUCCESS -- with no further scripted response and max_steps=1, the
    run is bounded to a single rejected "done" and MAX_STEPS_EXCEEDED,
    never SUCCESS. "done" turns are never represented in `history`
    (DiscoveryHistoryEntry.action is required), so history stays empty."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(propose_done("the balance is already visible on the page"))

    result = await _engine(fake, llm).run(_goal(), CONTEXT, DiscoveryLimits(max_steps=1))

    assert result.status != DiscoveryStatus.SUCCESS
    assert result.status == DiscoveryStatus.MAX_STEPS_EXCEEDED
    assert result.history == []
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_repeatedly_declaring_done_without_progress_is_bounded_not_unbounded() -> None:
    """A model that never produces a materializable action gets exactly
    max_steps chances and no more -- the existing step limit is the only
    bound needed; there is no separate retry budget to exhaust."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(propose_done(), propose_done(), propose_done())

    result = await _engine(fake, llm).run(_goal(), CONTEXT, DiscoveryLimits(max_steps=3))

    assert result.status == DiscoveryStatus.MAX_STEPS_EXCEEDED
    assert result.steps_taken == 3
    assert len(llm.calls) == 3  # never asked for a 4th turn


@pytest.mark.asyncio
async def test_rejected_done_feeds_back_corrective_context_and_accepts_a_read() -> None:
    """After a "done" claim is rejected for producing no materializable
    progress, the *next* call to the model must carry a concrete
    corrective instruction in its observation (never in the persisted
    trace -- see engine.py's _PROGRESS_REMINDER_TEXT comment), and a
    subsequent READ the model proposes in response must actually execute
    and have its value recorded in history, exactly as any other executed
    action would."""

    from cuas.domain import Locator, LocatorStrategy, Target

    fake = FakeSurfaceAdapter()
    balance_target = Target(primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params={"role": "text", "name": "Savings Balance"}))
    fake.script_read(balance_target, "$18204.55")

    llm = FakeLLMClient(
        propose_done("the balance is already visible on the page"),
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT, DiscoveryLimits(max_steps=2))

    # The first call saw a clean observation; only the second (post-
    # rejection) call carries the corrective reminder.
    assert len(llm.calls) == 2
    assert "replayed automatically later" not in llm.calls[0][2].visible_text
    assert "replayed automatically later" in llm.calls[1][2].visible_text

    # The READ the model proposed in response actually executed and was
    # recorded -- bounded at max_steps=2, so the run still ends without a
    # further "done" turn, but the progress it made is real.
    assert result.steps_taken == 2
    assert len(result.history) == 1
    assert result.history[0].outcome == "executed"
    assert result.history[0].action.action_type == ActionType.READ
    assert result.history[0].read_value == "$18204.55"


@pytest.mark.asyncio
async def test_done_after_a_materializable_read_succeeds() -> None:
    """The full corrective loop: done (rejected, nothing executed yet) ->
    READ (executes, recorded) -> done again -- and this second "done" is
    now accepted, because materializable progress genuinely exists."""

    from cuas.domain import Locator, LocatorStrategy, Target

    fake = FakeSurfaceAdapter()
    balance_target = Target(primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params={"role": "text", "name": "Savings Balance"}))
    fake.script_read(balance_target, "$18204.55")

    llm = FakeLLMClient(
        propose_done("the balance is already visible on the page"),
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
        propose_done("now recorded a READ of the balance"),
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert result.steps_taken == 3
    assert len(llm.calls) == 3
    assert len(result.history) == 1
    assert result.history[0].action.action_type == ActionType.READ
    assert result.history[0].read_value == "$18204.55"


@pytest.mark.asyncio
async def test_flows_with_materializable_progress_are_unaffected_by_the_new_gate() -> None:
    """A flow that already executes a READ before its first "done" must
    behave exactly as it did before this fix: no extra corrective turn,
    no extra STEP_FAILED event, the model is asked exactly as many times
    as it was scripted for."""

    fake = FakeSurfaceAdapter()
    sink = InMemoryEventSink()
    llm = FakeLLMClient(
        propose("fill", "search_member", target=role_target("textbox"), value="M1001"),
        propose("click", "view_account", target=role_target("button", "Search")),
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
        propose_done("the savings balance is now visible"),
    )

    result = await _engine(fake, llm, event_sink=sink).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert result.steps_taken == 4
    assert len(llm.calls) == 4  # no corrective retry was ever needed
    assert sink.events_of(EventType.STEP_FAILED) == []


@pytest.mark.asyncio
async def test_successful_run_emits_run_completed_exactly_once() -> None:
    """Regression test for a duplicate discovery_engine run_completed
    emission found in the real run's JSONL log: the success/"done" branch
    used to emit RUN_COMPLETED itself, in addition to the trailing
    unconditional emit every terminal branch already gets -- two events,
    4.5ms apart, for one run. Only the redundant inline emit was removed;
    every terminal branch (this included) still gets exactly one, from
    the one place that already handled it correctly."""

    fake = FakeSurfaceAdapter()
    sink = InMemoryEventSink()
    llm = FakeLLMClient(
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
        propose_done("balance recorded"),
    )

    result = await _engine(fake, llm, event_sink=sink).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert len(sink.events_of(EventType.RUN_COMPLETED)) == 1
