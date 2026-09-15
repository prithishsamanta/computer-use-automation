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
from tests.fixtures.fake_llm_client import (
    FakeLLMClient,
    css_target,
    malformed,
    propose,
    propose_done,
    propose_missing_intent,
    role_target,
)
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


# ---------------------------------------------------------------------------
# Malformed model output -- bounded recovery, not immediate termination
# (DECISIONS_LOG.md: a real Anthropic run, e199e82bd3774cf6af2124d699ca5cfa,
# omitted `intent` and the run terminated on the very first malformed
# turn). Supersedes the old
# test_malformed_model_output_stops_immediately_without_retry, which
# asserted exactly the behavior this change replaces.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_model_output_is_recorded_but_does_not_terminate_the_run() -> None:
    """A single malformed proposal is recorded and the loop asks the model
    again instead of ending the run -- three scripted turns (malformed,
    a corrected READ, done) all get consumed, and the run succeeds."""

    fake = FakeSurfaceAdapter()
    sink = InMemoryEventSink()
    llm = FakeLLMClient(
        propose_missing_intent("read", target=role_target("text", "Savings Balance")),
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
        propose_done("recovered"),
    )

    result = await _engine(fake, llm, event_sink=sink).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert len(llm.calls) == 3
    assert len(sink.events_of(EventType.MALFORMED_MODEL_OUTPUT)) == 1
    assert len(sink.events_of(EventType.RUN_COMPLETED)) == 1
    # No fabricated Action/history entry for the malformed turn -- only
    # the genuinely executed READ is in history.
    assert len(result.history) == 1
    assert result.history[0].action.action_type == ActionType.READ


@pytest.mark.asyncio
async def test_missing_intent_produces_a_clean_deterministic_error_and_is_recorded_honestly(tmp_path) -> None:
    """Reproduces real run e199e82bd3774cf6af2124d699ca5cfa's exact step 1
    shape: a structurally-present proposal missing `intent`. The parser
    must raise a clean, deterministic message instead of leaking Python's
    bare KeyError formatting, and DiscoveryTrace must keep recording the
    malformed turn exactly as before: raw output, parsed_action=None, the
    parse error, outcome="malformed_model_output" -- nothing here may
    rewrite or fabricate the proposal."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose_missing_intent("read", target=css_target("table tr:has(td:first-child:contains('Savings')) td:nth-child(3)")),
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
        propose_done("recovered"),
    )
    trace_store = FileDiscoveryTraceStore(tmp_path)

    result = await _engine(fake, llm, trace_store=trace_store).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    loaded = trace_store.load(result.run_id)
    malformed_step = loaded.steps[0]
    assert malformed_step.outcome == "malformed_model_output"
    assert malformed_step.parsed_action is None
    assert malformed_step.parse_error == "malformed action proposal: missing required field: intent"
    assert "'intent'" not in malformed_step.parse_error  # no bare KeyError formatting leaked through
    assert malformed_step.raw_model_output is not None
    assert "contains" in malformed_step.raw_model_output  # the real, uncorrected proposal is preserved


@pytest.mark.asyncio
async def test_malformed_proposal_never_executes_or_reaches_policy(tmp_path) -> None:
    """Proposing an unclassified/consequential-looking intent while
    malformed must not somehow execute it or reach policy: if it did, this
    run would either see a real click against the surface or escalate to
    APPROVAL_REQUIRED instead of recovering and succeeding."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose_missing_intent("click", target=role_target("button", "Close Account")),
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
        propose_done("recovered"),
    )
    trace_store = FileDiscoveryTraceStore(tmp_path)

    result = await _engine(fake, llm, trace_store=trace_store).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert not any(call[0] == "click" for call in fake.calls)
    loaded = trace_store.load(result.run_id)
    assert loaded.steps[0].policy_decision is None


@pytest.mark.asyncio
async def test_malformed_feedback_reaches_only_the_next_model_call_not_the_trace(tmp_path) -> None:
    """The corrective parse-error text must appear in the very next call's
    observation only -- never the first call's, never a third call's once
    it has already been shown once -- and it must never leak into the
    persisted trace's observation excerpt, which stays an honest record of
    what the page actually showed (mirrors _PROGRESS_REMINDER_TEXT's own
    separation)."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose_missing_intent("read", target=role_target("text", "Savings Balance")),
        propose("read", "view_account", target=role_target("text", "Savings Balance")),
        propose_done("recovered"),
    )
    trace_store = FileDiscoveryTraceStore(tmp_path)

    result = await _engine(fake, llm, trace_store=trace_store).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.SUCCESS
    assert "could not be parsed" not in llm.calls[0][2].visible_text
    assert "could not be parsed" in llm.calls[1][2].visible_text
    assert "could not be parsed" not in llm.calls[2][2].visible_text

    loaded = trace_store.load(result.run_id)
    assert all("could not be parsed" not in step.observation_text_excerpt for step in loaded.steps)


@pytest.mark.asyncio
async def test_corrected_proposal_after_malformed_turn_still_requires_approval_when_unclassified() -> None:
    """Recovering from a malformed turn grants no special policy
    treatment to whatever the model proposes next -- an unclassified
    intent (close_account) still escalates exactly as it would from a
    completely clean run, and DiscoveryPendingApproval still captures the
    exact corrected action (3904afe semantics untouched)."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose_missing_intent("click", target=role_target("button", "Close Account")),
        propose("click", "close_account", target=role_target("button", "Close Account")),
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.APPROVAL_REQUIRED
    assert not any(call[0] == "click" for call in fake.calls)
    assert result.pending_approval is not None
    assert result.pending_approval.pending_action.intent == "close_account"


@pytest.mark.asyncio
async def test_persistent_malformed_output_is_bounded_by_max_steps_and_escalates() -> None:
    """No separate malformed-retry cap exists -- the existing max_steps
    budget alone bounds unrecovered malformed output, and exhaustion still
    escalates through the normal terminal path. The exhaustion reason
    reports how many of the consumed steps were malformed."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose_missing_intent("read", target=role_target("text", "A")),
        propose_missing_intent("read", target=role_target("text", "B")),
        propose_missing_intent("read", target=role_target("text", "C")),
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT, DiscoveryLimits(max_steps=3))

    assert result.status == DiscoveryStatus.MAX_STEPS_EXCEEDED
    assert result.steps_taken == 3
    assert len(llm.calls) == 3  # never asked for a 4th turn once the budget is spent
    assert result.error_message == "exceeded max_steps=3 (3 of them malformed model output)"


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
# ---------------------------------------------------------------------------
# Discovery pause/resume continuation (DiscoveryPendingApproval): approving
# an APPROVAL_REQUIRED intervention must authorize and execute the EXACT
# action that escalated, not discard it for a fresh LLM reasoning attempt.
# See DECISIONS_LOG.md for the full design rationale; test_intervention_
# lifecycle.py covers the same behavior through the real claim/complete/
# resume orchestrator API, these exercise DiscoveryEngine.run(resume=...)
# directly.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approval_escalation_captures_the_exact_pending_action() -> None:
    """The DiscoveryPendingApproval bundle attached to an APPROVAL_REQUIRED
    result carries the exact Action policy evaluated, plus the loop's own
    continuation state at the moment of escalation -- not a re-derived or
    re-serialized copy."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close Account")))

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.APPROVAL_REQUIRED
    assert result.pending_approval is not None
    pending = result.pending_approval
    assert pending.pending_action.intent == "close_account"
    assert pending.pending_action.action_type == ActionType.CLICK
    assert pending.step_index == 0
    assert pending.history[-1].outcome == "approval_required"
    assert pending.history[-1].action.intent == "close_account"
    assert pending.consecutive_repeats == 0
    assert pending.needs_progress_reminder is False


@pytest.mark.asyncio
async def test_denied_action_never_produces_a_pending_approval_bundle() -> None:
    """Only APPROVAL_REQUIRED is something an operator can authorize past
    -- a DENY is terminal (mirrors ReplayEngine's own "a policy DENY is
    not something an operator can approve past"), so no continuation
    bundle is ever created for it."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(propose("click", "navigate_unauthorized_domain", target=role_target("link", "External Site")))

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.BLOCKED
    assert result.pending_approval is None


@pytest.mark.asyncio
async def test_pending_approval_preserves_raw_history_while_result_history_stays_redacted() -> None:
    """`DiscoveryResult.history` is the safe, redacted copy every existing
    caller already gets; `pending_approval.history` is deliberately the
    RAW in-memory history (only ever consumed in-process by a resumed
    DiscoveryEngine.run() call, never persisted to InterventionRequest/
    evidence -- see DiscoveryPendingApproval's docstring)."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(
        propose("fill", "search_member", target=role_target("textbox"), value="M1001"),
        propose("click", "close_account", target=role_target("button", "Close Account")),
    )

    result = await _engine(fake, llm).run(_goal(), CONTEXT)

    assert result.status == DiscoveryStatus.APPROVAL_REQUIRED
    assert result.history[0].action.value == "{{member_id}}"
    assert "M1001" not in repr(result.history)
    assert result.pending_approval.history[0].action.value == "M1001"


@pytest.mark.asyncio
async def test_resume_executes_the_exact_approved_action_without_a_new_llm_call() -> None:
    """No new LLM call happens for the resumed turn at all -- the action
    was already fully proposed, parsed, and policy-evaluated before the
    pause; resuming only authorizes and executes it."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close Account")))
    first = await _engine(fake, llm).run(_goal(), CONTEXT)
    assert first.status == DiscoveryStatus.APPROVAL_REQUIRED
    pending = first.pending_approval

    # No further responses scripted at all -- if resuming asked the model
    # to re-propose the already-approved action, FakeLLMClient's "ran out
    # of scripted responses" AssertionError would fail this test.
    llm2 = FakeLLMClient()
    second = await _engine(fake, llm2).run(
        _goal(), CONTEXT, DiscoveryLimits(max_steps=1), run_id=first.run_id, resume=pending
    )

    assert llm2.calls == []
    assert any(call[0] == "click" for call in fake.calls)
    assert second.status == DiscoveryStatus.MAX_STEPS_EXCEEDED  # budget already spent, see below


@pytest.mark.asyncio
async def test_only_the_resumed_action_bypasses_policy_subsequent_proposals_are_checked_normally() -> None:
    """The policy bypass applies to exactly one action -- the one an
    operator just approved. A brand-new proposal made after resuming (even
    one that also requires approval) goes through LayeredPolicyEngine.
    evaluate() exactly like any normal turn, and can escalate again on its
    own merits."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close Account")))
    first = await _engine(fake, llm).run(_goal(), CONTEXT)
    pending = first.pending_approval

    llm2 = FakeLLMClient(propose("click", "submit_transaction", target=role_target("button", "Submit")))
    second = await _engine(fake, llm2).run(_goal(), CONTEXT, run_id=first.run_id, resume=pending)

    assert len(llm2.calls) == 1
    assert second.status == DiscoveryStatus.APPROVAL_REQUIRED
    assert second.pending_approval is not None
    assert second.pending_approval.pending_action.intent == "submit_transaction"


@pytest.mark.asyncio
async def test_approved_action_that_fails_becomes_execution_failed_and_the_model_can_recover() -> None:
    """Approval never implies execution success: if the exact approved
    action still fails against the live surface, it becomes an ordinary
    "execution_failed" history entry -- the same generic recoverable-
    failure path any other turn uses -- and the bounded loop simply
    continues, giving the model a chance to correct course (the same
    reasoning that made resuming the real invalid-:contains()-selector
    intervention safe without special-casing anything)."""

    from cuas.domain import Locator, LocatorStrategy, Target

    fake = FakeSurfaceAdapter()
    close_target = role_target("button", "Close Account")
    real_close_target = Target(
        primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params={"role": "button", "name": "Close Account"})
    )
    fake.script_click(real_close_target, TargetNotFoundError("no element matched role=button name='Close Account'"))

    llm = FakeLLMClient(propose("click", "close_account", target=close_target))
    first = await _engine(fake, llm).run(_goal(), CONTEXT)
    assert first.status == DiscoveryStatus.APPROVAL_REQUIRED
    pending = first.pending_approval

    balance_target = role_target("text", "Savings Balance")
    real_balance_target = Target(
        primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params={"role": "text", "name": "Savings Balance"})
    )
    fake.script_read(real_balance_target, "$0.00")
    llm2 = FakeLLMClient(
        propose("read", "view_account", target=balance_target),
        propose_done("account no longer active; balance recorded as zero"),
    )

    second = await _engine(fake, llm2).run(_goal(), CONTEXT, run_id=first.run_id, resume=pending)

    assert second.status == DiscoveryStatus.SUCCESS
    assert second.history[0].outcome == "approval_required"
    assert second.history[1].outcome == "execution_failed"
    assert second.history[1].action.intent == "close_account"
    assert "no element matched" in second.history[1].error_message
    assert second.history[2].outcome == "executed"
    assert second.history[2].action.action_type == ActionType.READ
    assert second.history[2].read_value == "$0.00"
    assert len(llm2.calls) == 2


@pytest.mark.asyncio
async def test_max_steps_budget_survives_the_pause_rather_than_resetting() -> None:
    """The step count already spent proposing/escalating the approved
    action counts against the same max_steps budget after resume -- it is
    not restored to a fresh max_steps allotment."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close Account")))
    limits = DiscoveryLimits(max_steps=2)
    first = await _engine(fake, llm).run(_goal(), CONTEXT, limits)
    assert first.status == DiscoveryStatus.APPROVAL_REQUIRED
    pending = first.pending_approval

    # Only one more turn's worth of budget remains (max_steps=2 total, one
    # step already spent on the escalated proposal). If resuming silently
    # reset the step counter, DiscoveryEngine would ask for a second
    # response here, which FakeLLMClient treats as a hard test failure.
    llm2 = FakeLLMClient(propose_done("nothing materialized"))
    second = await _engine(fake, llm2).run(_goal(), CONTEXT, limits, run_id=first.run_id, resume=pending)

    assert second.status == DiscoveryStatus.MAX_STEPS_EXCEEDED
    assert len(llm2.calls) == 1


@pytest.mark.asyncio
async def test_token_budget_survives_the_pause_rather_than_resetting() -> None:
    """total_tokens already spent before the pause counts against the
    same max_total_tokens budget after resume."""

    from cuas.discovery.llm_client import LLMResponse

    fake = FakeSurfaceAdapter()
    escalating = LLMResponse(
        raw_text="escalating",
        proposal={
            "reasoning": "test", "action_type": "click", "intent": "close_account",
            "target": role_target("button", "Close Account"),
        },
        input_tokens=900,
        output_tokens=50,
    )
    llm = FakeLLMClient(escalating)
    limits = DiscoveryLimits(max_total_tokens=1000)
    first = await _engine(fake, llm).run(_goal(), CONTEXT, limits)
    assert first.status == DiscoveryStatus.APPROVAL_REQUIRED
    pending = first.pending_approval
    assert pending.total_tokens == 950

    # If the pause had silently reset total_tokens to 0, this next
    # response's 60 tokens would fit comfortably under the 1000 budget
    # instead of pushing the (correctly-preserved) running total over it.
    next_response = LLMResponse(
        raw_text="next",
        proposal={
            "reasoning": "test", "action_type": "read", "intent": "view_account",
            "target": role_target("text", "Savings Balance"),
        },
        input_tokens=60,
        output_tokens=0,
    )
    llm2 = FakeLLMClient(next_response)
    second = await _engine(fake, llm2).run(_goal(), CONTEXT, limits, run_id=first.run_id, resume=pending)

    assert second.status == DiscoveryStatus.MAX_TOKENS_EXCEEDED


@pytest.mark.asyncio
async def test_repetition_tracking_survives_the_pause_rather_than_resetting() -> None:
    """consecutive_repeats/last_signature already accumulated before the
    pause continue accumulating after resume, rather than looking like a
    first occurrence again."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close Account")))
    first = await _engine(fake, llm).run(_goal(), CONTEXT)
    assert first.pending_approval.consecutive_repeats == 0
    assert first.pending_approval.last_signature is not None

    # The very next proposal after resume repeats the identical action
    # signature. Carried-forward repetition state recognizes this as a
    # repeat (consecutive_repeats == 1); a silent reset would look like a
    # first occurrence again (consecutive_repeats staying 0).
    llm2 = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close Account")))
    second = await _engine(fake, llm2).run(_goal(), CONTEXT, run_id=first.run_id, resume=first.pending_approval)

    assert second.status == DiscoveryStatus.APPROVAL_REQUIRED
    assert second.pending_approval.consecutive_repeats == 1


@pytest.mark.asyncio
async def test_resuming_appends_to_the_existing_trace_instead_of_overwriting_it(tmp_path) -> None:
    """FileDiscoveryTraceStore.save() unconditionally overwrites
    <run_id>.json -- resuming must load the pre-pause trace and append to
    it, not construct a brand-new DiscoveryTrace that discards everything
    already recorded before the escalation."""

    fake = FakeSurfaceAdapter()
    llm = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close Account")))
    trace_store = FileDiscoveryTraceStore(tmp_path)
    first = await _engine(fake, llm, trace_store=trace_store).run(_goal(), CONTEXT)
    assert first.status == DiscoveryStatus.APPROVAL_REQUIRED

    original_trace = trace_store.load(first.run_id)
    assert len(original_trace.steps) == 1
    assert original_trace.steps[0].outcome == "approval_required"

    llm2 = FakeLLMClient(propose_done("nothing else to record"))
    await _engine(fake, llm2, trace_store=trace_store).run(
        _goal(), CONTEXT, DiscoveryLimits(max_steps=2), run_id=first.run_id, resume=first.pending_approval
    )

    final_trace = trace_store.load(first.run_id)
    # The original pre-pause step is still there, not replaced...
    assert final_trace.steps[0].outcome == "approval_required"
    # ...and the resumed execution appended further steps on top of it.
    assert len(final_trace.steps) > 1
    assert any(step.outcome == "executed" for step in final_trace.steps)
