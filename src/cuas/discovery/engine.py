"""LLM-driven discovery (.CLAUDE/03_DISCOVERY_AND_REPLAY.md, "Discovery
Loop"). No mechanical execution of model output: every proposed action is
parsed into the same typed `Action` replay uses, checked by the exact same
`PolicyEngine` replay checks, and executed through the same
`SurfaceAdapter` interface -- one policy seam, one surface abstraction,
two callers (ReplayEngine, DiscoveryEngine).

Loop, per the explicit instruction this phase implements:

    observe -> ask model for a structured action -> validate/parse ->
    run through the same PolicyEngine -> execute through SurfaceAdapter ->
    observe again -> repeat until success/business outcome/failure

Key differences from ReplayEngine, both deliberate:

- Policy DENY/REQUIRE_APPROVAL is terminal here, exactly as in replay --
  "Unknown, malformed, unsafe, or policy-blocked actions must stop or
  escalate rather than being 'fixed' by guessing." Discovery does not try
  a different action after a policy escalation.
- A genuine *execution* failure (e.g. TargetNotFoundError because the
  model guessed a control that isn't there) is NOT terminal. Unlike
  replay, discovery has a model in the loop that can reason about a
  failure and try something else next turn, so the failure is recorded
  and fed back as history instead of being mechanically retried against
  an artifact-declared recovery table (there is no artifact yet). This is
  the one place discovery is *more* lenient than replay, and it is
  bounded by the same max_steps/max_duration/loop-detection limits as
  everything else, so a model that can't recover still terminates.
- LLM-proposed actions never have `Action.risk` set explicitly (see
  `_parse_proposal`) -- this is what makes LayeredPolicyEngine's
  model_fields_set-based "unclassified intent -> REQUIRE_APPROVAL, never
  silently ALLOW" behavior (safety/policy.py) automatically govern every
  proposal an LLM makes, with no discovery-specific safety code needed.

Persistence: the complete step-by-step trace (including every failed
detour, parse error, and policy escalation) is written through
DiscoveryTraceStore -- separate from, and never used to construct, an
Artifact (.CLAUDE/08 decision #6). Redaction is applied to every
free-text field before it is written to the trace or emitted as a
RunEvent; the raw (unredacted) values live only in the in-memory
`history` used to keep prompting the model correctly, and are stripped
before that history is returned to a caller in a DiscoveryResult (see
`_redact_history`) -- never log prompts/responses containing raw
sensitive values without redaction.

Redaction uses *named* placeholders (`{{member_id}}`), not a flat,
anonymous "[REDACTED]" marker (`cuas.observability.redaction.
redact_named_values`/`redact_named_values_json`) -- a small amendment
made in Phase 9, not part of Phase 8's original delivery. The reason:
Phase 9's artifact construction (`cuas.artifact_builder`) needs to turn a
successful trace's actions into an Artifact.Step whose `value` already
uses exactly this `{{input_name}}` placeholder syntax
(.CLAUDE/02_ARTIFACT_SCHEMA.md); a generic "[REDACTED]" marker would have
made that unrecoverable once more than one sensitive input existed, since
nothing would say *which* input a given "[REDACTED]" came from. Naming
the placeholder is strictly as safe as the generic marker -- no raw value
survives either way -- so this was the smallest change that unblocks
Phase 9 without loosening Phase 8's "never persist a raw sensitive value"
guarantee.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

from cuas.discovery.llm_client import LLMClient, LLMResponse
from cuas.discovery.models import (
    DiscoveryGoal,
    DiscoveryHistoryEntry,
    DiscoveryLimits,
    DiscoveryPendingApproval,
    DiscoveryResult,
    DiscoveryStatus,
)
from cuas.discovery.trace import DiscoveryTrace, DiscoveryTraceStep, DiscoveryTraceStore, NullDiscoveryTraceStore
from cuas.domain import Action, ActionType, AppContext, AutomationError, Locator, LocatorStrategy, Target
from cuas.observability.event_sink import EventSink, NullEventSink
from cuas.observability.events import EventType, RunEvent
from cuas.observability.evidence import EvidenceStore, NullEvidenceStore
from cuas.observability.redaction import redact_dict, redact_named_values, redact_named_values_json
from cuas.safety import PolicyDecision, PolicyEngine
from cuas.surface.adapter import Observation, SurfaceAdapter

# How much of the current page text is kept in the persisted trace per
# step -- a trace is a debugging/artifact-construction aid, not a full
# page dump; unbounded page text would also make redaction slower for no
# benefit once the meaningful part of the page has been captured.
_OBSERVATION_EXCERPT_CHARS = 1000

# Short, fixed timeout for checking whether a known business outcome or a
# declared success_checkpoint is *currently* present -- this is "is this
# already true right now", not "wait for the app to catch up" (that is
# what the surface's own action timeouts are for).
_OUTCOME_CHECK_TIMEOUT_MS = 500

# Trailing history entries carried into failure evidence, same rationale
# and same size as ReplayEngine's _RECENT_ACTIONS_FOR_EVIDENCE.
_RECENT_ACTIONS_FOR_EVIDENCE = 5

_SUPPORTED_LOCATOR_STRATEGIES = {LocatorStrategy.ROLE_NAME, LocatorStrategy.CSS}

# Fed back as part of the *observation* text on the next turn only (never
# written into the persisted trace's own observation_text_excerpt, which
# stays an honest record of what the page actually showed) after a "done"
# claim was rejected for producing no materializable progress -- see
# DiscoveryEngine._has_materializable_progress. Deliberately a plain
# instruction to use the existing structured action schema, not something
# that hands the model a pre-built action: the model must still propose
# it itself, through the same tool call every other turn uses, so the
# trace accurately records what was actually executed (Phase 9's
# ArtifactBuilder must never receive a fabricated action).
_PROGRESS_REMINDER_TEXT = (
    "This workflow will be replayed automatically later, without you "
    "present, from a fresh page load. Declaring the goal done is not "
    "enough by itself: nothing has been recorded yet that a future "
    "replay could use to obtain the result. Propose one concrete action "
    "(via this tool's normal action_type/target fields) that reads the "
    "specific value the goal describes -- e.g. an explicit READ of it -- "
    "before declaring done again."
)


def _require_field(source: dict[str, Any], key: str) -> Any:
    """Same purpose as `source[key]`, except a missing key raises a
    deterministic ValueError message instead of a bare KeyError -- whose
    str() is just the key's repr (e.g. "'intent'"), which used to leak
    straight into "malformed action proposal: 'intent'" (DECISIONS_LOG.md).
    `KeyError` stays in `_parse_proposal`/`_parse_target`'s except clause
    regardless, as defense in depth for any dict access not routed through
    this helper."""
    if key not in source:
        raise ValueError(f"missing required field: {key}")
    return source[key]


# Fed back the same way as _PROGRESS_REMINDER_TEXT above (next turn's
# *observation* only, never the persisted trace/observation fields), but
# for a different corrective case: the model's previous response could not
# even be parsed into an Action at all (see DiscoveryEngine._parse_proposal).
# Unlike the progress reminder, this describes one specific past mistake,
# not a standing rule, so the loop clears it back to None immediately after
# building the one observation that carries it (see `run`'s per-iteration
# observation_for_model construction).
def _build_malformed_feedback_text(parse_error: str) -> str:
    return (
        "Your previous response could not be parsed as a valid action "
        f"proposal: {parse_error}. That turn was not executed and nothing "
        "was recorded as an action. Propose exactly one action again, using "
        "this tool's schema, with every field this action type requires."
    )


def _with_malformed_count(reason: str, malformed_count: int) -> str:
    """Appends how many of the consumed steps were malformed-model-output
    turns to a budget-exhaustion reason, when at least one occurred -- an
    escalation-evidence quality improvement only; it never changes whether
    or when exhaustion happens (DECISIONS_LOG.md)."""
    if malformed_count <= 0:
        return reason
    return f"{reason} ({malformed_count} of them malformed model output)"


class DiscoveryEngine:
    def __init__(
        self,
        surface: SurfaceAdapter,
        policy: PolicyEngine,
        llm: LLMClient,
        *,
        event_sink: EventSink | None = None,
        evidence_store: EvidenceStore | None = None,
        trace_store: DiscoveryTraceStore | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self._surface = surface
        self._policy = policy
        self._llm = llm
        self._event_sink = event_sink or NullEventSink()
        self._evidence_store = evidence_store or NullEvidenceStore()
        self._trace_store = trace_store or NullDiscoveryTraceStore()
        self._clock = clock

    async def run(
        self,
        goal: DiscoveryGoal,
        context: AppContext,
        limits: DiscoveryLimits | None = None,
        *,
        run_id: str | None = None,
        resume: DiscoveryPendingApproval | None = None,
    ) -> DiscoveryResult:
        """`resume`, when not None, continues a discovery attempt that
        previously paused with DiscoveryStatus.APPROVAL_REQUIRED: the
        exact `resume.pending_action` an operator has now authorized is
        executed directly (no new LLM proposal, no re-evaluation through
        policy -- see DiscoveryPendingApproval's docstring), the loop's
        own budgets/counters are restored rather than reset, and the
        existing discovery trace for this run_id is appended to instead
        of being overwritten. `None` (the default) is byte-for-byte the
        original behavior: a fresh attempt from goal.start_url.

        A single malformed model proposal (unparseable JSON, a missing
        required field, an invalid enum value, etc. -- see
        `_parse_proposal`) is not terminal by itself: it is recorded
        honestly in the trace, never executed, never sent to policy, and
        never turned into a DiscoveryHistoryEntry (there is no valid Action
        to put in one) -- but the loop continues, with a concise, redacted
        description of the parse error fed back on the very next turn's
        observation only (see `pending_malformed_feedback`/
        `_build_malformed_feedback_text`), consuming one of the existing
        max_steps/max_duration_seconds/max_total_tokens bounds exactly like
        any other turn. Persistent malformed output still terminates and
        escalates once those bounds are exhausted, exactly as before (see
        DECISIONS_LOG.md).
        """

        run_id = run_id or uuid.uuid4().hex
        limits = limits or DiscoveryLimits()
        named_values = goal.named_sensitive_values()

        self._emit(
            run_id,
            EventType.RUN_STARTED,
            details={
                "capability_id": goal.capability_id,
                "tenant_id": context.tenant_id,
                "inputs": redact_dict(goal.inputs, goal.sensitive_inputs),
                "resumed": resume is not None,
            },
        )

        if resume is not None:
            # Continue the SAME trace file instead of silently overwriting
            # it (FileDiscoveryTraceStore.save() unconditionally overwrites
            # <run_id>.json, and a fresh DiscoveryTrace() here would
            # destroy the pre-escalation steps). Falls back to a fresh
            # trace if none can be loaded (e.g. NullDiscoveryTraceStore) --
            # exactly as safe as the non-resume path, just with nothing to
            # append to. Deliberately narrow: only ever reached when
            # `resume` is not None, which only ever happens for an
            # APPROVAL_REQUIRED-originated continuation -- every other
            # discovery pause/resume path is unchanged.
            try:
                trace = self._trace_store.load(run_id)
            except FileNotFoundError:
                trace = DiscoveryTrace(
                    run_id=run_id,
                    capability_id=goal.capability_id,
                    goal_description=redact_named_values(goal.description, named_values),
                    started_at=datetime.now(timezone.utc),
                )
            history: list[DiscoveryHistoryEntry] = list(resume.history)
            last_signature: str | None = resume.last_signature
            consecutive_repeats = resume.consecutive_repeats
            total_tokens = resume.total_tokens
            needs_progress_reminder = resume.needs_progress_reminder
        else:
            trace = DiscoveryTrace(
                run_id=run_id,
                capability_id=goal.capability_id,
                # Redacted even though it's caller-authored, not model output --
                # a goal description commonly embeds the very input it's
                # describing (e.g. "Find member M1001..."), and this field is
                # persisted to disk like everything else in the trace.
                goal_description=redact_named_values(goal.description, named_values),
                started_at=datetime.now(timezone.utc),
            )
            history = []
            last_signature = None
            consecutive_repeats = 0
            total_tokens = 0
            # Set once a "done" claim is rejected for producing no
            # materializable progress (see _has_materializable_progress).
            # Never cleared back to False by itself -- but the reminder it
            # controls is only actually shown while _has_materializable_progress
            # is still False (see the observation-building gate below), so
            # once a READ genuinely executes, the reminder stops appearing
            # on its own without needing a second flag or an explicit
            # reset. Real run e4f7901c680c4f2690e3b9f127e785fd
            # (DECISIONS_LOG.md) showed the earlier version of this -- the
            # reminder shown unconditionally once set -- kept instructing
            # the model to "propose one concrete action... before
            # declaring done again" even on the turn immediately after it
            # already had, which is exactly backwards.
            needs_progress_reminder = False

        # Not part of DiscoveryPendingApproval (3904afe) by design -- a
        # malformed proposal can never be the pending_action of an approval
        # escalation (see _parse_proposal's contract: parse_error implies
        # action is None, so the loop below never reaches self._policy.
        # evaluate() for it), and this state is purely same-run
        # bookkeeping/feedback, not something an operator's approval needs
        # to carry across a pause. Always starts fresh, including on a
        # resumed run -- if a resumed run also produces malformed output, it
        # is bounded and reported exactly like a fresh run's would be.
        malformed_count = 0
        pending_malformed_feedback: str | None = None

        start_time = self._clock()
        result: DiscoveryResult | None = None

        if resume is not None:
            # Execute exactly the operator-approved action -- no LLM call
            # (nothing new was proposed) and no re-evaluation through
            # policy (that decision, REQUIRE_APPROVAL, was already made
            # and is now authorized by the operator; policy is a pure
            # function of (action, context), so re-checking could only
            # repeat the same decision -- exactly ReplayEngine's own
            # skip_policy_for_step_id reasoning). Shares
            # `_execute_and_record` with the normal loop below so
            # "approval does not imply execution success" holds by
            # construction: an execution failure here becomes the same
            # ordinary "execution_failed" history entry a normal turn
            # would produce, and the bounded loop below simply continues.
            action = resume.pending_action
            execution_step_index = resume.step_index
            observation = await self._surface.observe()
            self._emit(
                run_id, EventType.STEP_STARTED, step_id=str(execution_step_index),
                details={"url": redact_named_values(observation.url, named_values), "resumed": True},
            )
            self._emit(
                run_id, EventType.POLICY_CHECKED, step_id=str(execution_step_index),
                details={"decision": "skipped_on_resume", "intent": action.intent},
            )
            trace_step = DiscoveryTraceStep(
                step_index=execution_step_index,
                observation_url=redact_named_values(observation.url, named_values),
                observation_text_excerpt=redact_named_values(
                    observation.visible_text[:_OBSERVATION_EXCERPT_CHARS], named_values
                ),
                raw_model_output=None,
                policy_decision=PolicyDecision.REQUIRE_APPROVAL,
                parsed_action=redact_named_values_json(action.model_dump(mode="json"), named_values),
            )
            observation = await self._execute_and_record(
                run_id, execution_step_index, action, PolicyDecision.REQUIRE_APPROVAL,
                trace_step, trace, history, named_values,
            )
            start_step = execution_step_index + 1
        else:
            await self._surface.navigate(goal.start_url)
            observation = await self._surface.observe()
            start_step = 0

        for step_index in range(start_step, limits.max_steps):
            if self._clock() - start_time > limits.max_duration_seconds:
                result = self._finish(
                    run_id, DiscoveryStatus.MAX_DURATION_EXCEEDED, goal, step_index, history,
                    reason=_with_malformed_count(
                        f"exceeded max_duration_seconds={limits.max_duration_seconds}", malformed_count
                    ),
                )
                await self._capture_terminal_evidence(run_id, step_index, "max_duration_exceeded")
                break

            outcome_code = await self._match_known_business_outcome(goal)
            if outcome_code is not None:
                self._emit(run_id, EventType.BUSINESS_OUTCOME_DETECTED, step_id=str(step_index), details={"business_outcome_code": outcome_code})
                result = DiscoveryResult(
                    run_id=run_id, status=DiscoveryStatus.BUSINESS_OUTCOME, capability_id=goal.capability_id,
                    steps_taken=step_index, business_outcome_code=outcome_code,
                    history=self._redact_history(history, named_values),
                )
                break

            self._emit(run_id, EventType.STEP_STARTED, step_id=str(step_index), details={"url": redact_named_values(observation.url, named_values)})

            # `observation` itself (and everything derived from it below,
            # e.g. trace_step.observation_text_excerpt) stays exactly what
            # the surface actually showed -- only the copy sent to the
            # model gets the reminder appended, so the persisted trace
            # never mixes real page content with injected system text.
            observation_for_model = observation
            if needs_progress_reminder and not self._has_materializable_progress(history):
                observation_for_model = Observation(
                    url=observation_for_model.url,
                    visible_text=observation_for_model.visible_text + "\n\n" + _PROGRESS_REMINDER_TEXT,
                )
            if pending_malformed_feedback is not None:
                observation_for_model = Observation(
                    url=observation_for_model.url,
                    visible_text=observation_for_model.visible_text
                    + "\n\n"
                    + _build_malformed_feedback_text(pending_malformed_feedback),
                )
                # Consumed exactly once -- this describes one specific past
                # mistake, not a standing rule like _PROGRESS_REMINDER_TEXT,
                # so it must not keep reappearing on every later turn.
                pending_malformed_feedback = None

            llm_response = await self._llm.propose_action(
                goal=goal, context=context, observation=observation_for_model, history=history
            )
            total_tokens += llm_response.input_tokens + llm_response.output_tokens

            trace_step = DiscoveryTraceStep(
                step_index=step_index,
                observation_url=redact_named_values(observation.url, named_values),
                observation_text_excerpt=redact_named_values(observation.visible_text[:_OBSERVATION_EXCERPT_CHARS], named_values),
                raw_model_output=redact_named_values(llm_response.raw_text, named_values),
            )

            if limits.max_total_tokens is not None and total_tokens > limits.max_total_tokens:
                trace_step.outcome = "max_tokens_exceeded"
                trace.steps.append(trace_step)
                result = self._finish(
                    run_id, DiscoveryStatus.MAX_TOKENS_EXCEEDED, goal, step_index, history,
                    reason=_with_malformed_count(
                        f"exceeded max_total_tokens={limits.max_total_tokens}", malformed_count
                    ),
                )
                break

            action, done, parse_error = self._parse_proposal(llm_response)

            if parse_error is not None:
                trace_step.parse_error = redact_named_values(parse_error, named_values)
                trace_step.outcome = "malformed_model_output"
                trace.steps.append(trace_step)
                malformed_count += 1
                # Fed back on the *next* turn's observation only (see the
                # observation_for_model construction above) -- never a
                # DiscoveryHistoryEntry (action is required there and there
                # is no valid Action to put in it; see DECISIONS_LOG.md) and
                # never a rewrite of what was actually recorded above. This
                # turn genuinely never executed and never reached policy --
                # nothing here changes that.
                pending_malformed_feedback = trace_step.parse_error
                self._emit(
                    run_id, EventType.MALFORMED_MODEL_OUTPUT, step_id=str(step_index), status="failure",
                    details={"parse_error": trace_step.parse_error, "malformed_count": malformed_count},
                )
                # Bounded by the same max_steps/max_duration_seconds/
                # max_total_tokens limits as every other kind of turn --
                # deliberately no separate malformed-retry cap
                # (DECISIONS_LOG.md). If the model never recovers, the
                # budgets above/below still terminate and escalate this run
                # exactly as they already do for any other stuck loop.
                continue  # noqa: consecutive-repeat/signature tracking intentionally not updated -- no Action exists to sign

            if done:
                trace_step.outcome = "declared_done"
                trace.steps.append(trace_step)
                if not self._has_materializable_progress(history):
                    # The model is informed about policy but is never the
                    # final authority on outcomes either -- and "the page
                    # already shows the answer" is not the same claim as
                    # "a reusable artifact can be built from this run".
                    # ArtifactBuilder needs at least one *executed* READ
                    # to derive a typed output at all (see
                    # _has_materializable_progress); without one, "done"
                    # is not trusted, exactly like an unverified
                    # success_checkpoint below. Bounded by the same
                    # max_steps/max_duration/max_tokens limits as every
                    # other turn -- no separate retry budget.
                    needs_progress_reminder = True
                    self._emit(
                        run_id, EventType.STEP_FAILED, step_id=str(step_index), status="info",
                        details={
                            "reason": "model declared done before any action was executed; "
                            "cannot materialize a reusable artifact from this trace"
                        },
                    )
                    continue  # noqa: consecutive-repeat/signature tracking intentionally not updated for "done" turns
                if await self._verify_success_checkpoint(goal):
                    result = DiscoveryResult(
                        run_id=run_id, status=DiscoveryStatus.SUCCESS, capability_id=goal.capability_id,
                        steps_taken=step_index + 1, history=self._redact_history(history, named_values),
                    )
                    break
                # Same rationale as above: a declared success_checkpoint
                # that doesn't verify means this "done" claim is not
                # trusted either, and the loop simply continues (bounded
                # by the same limits as every other turn) rather than
                # ending the run on the model's word alone.
                self._emit(
                    run_id, EventType.STEP_FAILED, step_id=str(step_index), status="info",
                    details={"reason": "model declared done but success_checkpoint did not verify"},
                )
                continue  # noqa: consecutive-repeat/signature tracking intentionally not updated for "done" turns

            assert action is not None  # guaranteed by _parse_proposal's contract when parse_error is None and not done

            signature = self._action_signature(action)
            if signature == last_signature:
                consecutive_repeats += 1
            else:
                consecutive_repeats = 0
                last_signature = signature

            if consecutive_repeats >= limits.max_consecutive_repeats:
                trace_step.parsed_action = redact_named_values_json(action.model_dump(mode="json"), named_values)
                trace_step.outcome = "loop_detected"
                trace.steps.append(trace_step)
                self._emit(
                    run_id, EventType.STEP_FAILED, step_id=str(step_index), status="failure",
                    details={"reason": "repeated action loop detected", "signature": signature},
                )
                await self._capture_terminal_evidence(run_id, step_index, "loop_detected")
                result = self._finish(
                    run_id, DiscoveryStatus.LOOP_DETECTED, goal, step_index, history,
                    reason=f"action proposed {consecutive_repeats + 1} times in a row: {signature}",
                )
                break

            decision = self._policy.evaluate(action, context)
            trace_step.policy_decision = decision
            trace_step.parsed_action = redact_named_values_json(action.model_dump(mode="json"), named_values)
            self._emit(run_id, EventType.POLICY_CHECKED, step_id=str(step_index), details={"decision": decision.value, "intent": action.intent})

            if decision in (PolicyDecision.DENY, PolicyDecision.REQUIRE_APPROVAL):
                outcome = "policy_denied" if decision == PolicyDecision.DENY else "approval_required"
                trace_step.outcome = outcome
                trace.steps.append(trace_step)
                history.append(DiscoveryHistoryEntry(step_index=step_index, action=action, policy_decision=decision, outcome=outcome))
                status = DiscoveryStatus.BLOCKED if decision == PolicyDecision.DENY else DiscoveryStatus.APPROVAL_REQUIRED
                verb = "denied" if decision == PolicyDecision.DENY else "requires operator approval for"
                # Only APPROVAL_REQUIRED is something an operator can
                # authorize past -- a DENY is terminal (see module
                # docstring/DECISIONS_LOG.md), so no continuation bundle
                # is ever created for it, mirroring replay's own "a
                # policy DENY... is not something an operator can approve
                # past" rule.
                pending_approval = (
                    DiscoveryPendingApproval(
                        pending_action=action,
                        step_index=step_index,
                        history=list(history),
                        total_tokens=total_tokens,
                        needs_progress_reminder=needs_progress_reminder,
                        last_signature=last_signature,
                        consecutive_repeats=consecutive_repeats,
                    )
                    if status == DiscoveryStatus.APPROVAL_REQUIRED
                    else None
                )
                result = self._finish(
                    run_id, status, goal, step_index, history,
                    reason=f"policy {verb} proposed action (intent={action.intent!r})",
                    pending_approval=pending_approval,
                )
                break

            self._emit(
                run_id, EventType.LLM_ACTION_PROPOSED, step_id=str(step_index),
                details={
                    "action_type": action.action_type.value,
                    "intent": action.intent,
                    "reasoning": redact_named_values(llm_response.proposal.get("reasoning", "") if llm_response.proposal else "", named_values),
                },
            )

            observation = await self._execute_and_record(
                run_id, step_index, action, decision, trace_step, trace, history, named_values
            )
        else:
            result = self._finish(
                run_id, DiscoveryStatus.MAX_STEPS_EXCEEDED, goal, limits.max_steps, history,
                reason=_with_malformed_count(f"exceeded max_steps={limits.max_steps}", malformed_count),
            )
            await self._capture_terminal_evidence(run_id, limits.max_steps, "max_steps_exceeded")

        assert result is not None
        trace.finished_at = datetime.now(timezone.utc)
        trace.final_status = result.status.value
        self._trace_store.save(trace)

        self._emit(run_id, EventType.RUN_COMPLETED, status=result.status.value, details={"status": result.status.value})
        return result

    # -- proposal parsing ---------------------------------------------------

    def _parse_proposal(self, response: LLMResponse) -> tuple[Action | None, bool, str | None]:
        """Returns (action, done, parse_error). Exactly one of `action`/
        `done=True`/`parse_error` is meaningful for any given response:
        this is the single place a raw provider payload is validated into
        (or rejected from becoming) a real Action, shared by every
        LLMClient implementation."""

        if response.parse_error is not None or response.proposal is None:
            return None, False, response.parse_error or "no structured proposal returned"

        proposal = response.proposal
        if proposal.get("done"):
            return None, True, None

        try:
            action_type = ActionType(_require_field(proposal, "action_type"))
            intent = _require_field(proposal, "intent")
            if not isinstance(intent, str) or not intent.strip():
                raise ValueError("intent must be a non-empty string")

            target: Target | None = None
            target_dict = proposal.get("target")
            if target_dict:
                target = self._parse_target(target_dict)

            value = proposal.get("value")
            if value is not None and not isinstance(value, str):
                raise ValueError("value must be a string when present")

            action = Action(
                id=f"discovery-{uuid.uuid4().hex[:8]}",
                action_type=action_type,
                intent=intent,
                target=target,
                value=value,
                # Deliberately never set `risk` here: LayeredPolicyEngine's
                # model_fields_set check is what makes an unclassified
                # intent fail closed to REQUIRE_APPROVAL rather than
                # trusting a model's own self-assessment (see module
                # docstring and safety/policy.py).
            )
        except (KeyError, ValueError, TypeError) as exc:
            return None, False, f"malformed action proposal: {exc}"

        return action, False, None

    def _parse_target(self, target_dict: dict[str, Any]) -> Target:
        strategy = LocatorStrategy(target_dict.get("strategy", "css"))
        if strategy not in _SUPPORTED_LOCATOR_STRATEGIES:
            raise ValueError(f"unsupported locator strategy from model: {strategy.value}")

        if strategy == LocatorStrategy.ROLE_NAME:
            params: dict[str, Any] = {"role": _require_field(target_dict, "role")}
            if target_dict.get("name"):
                params["name"] = target_dict["name"]
        else:
            params = {"selector": _require_field(target_dict, "selector")}

        return Target(primary=Locator(strategy=strategy, params=params, frame=target_dict.get("frame")))

    # -- execution ------------------------------------------------------

    async def _execute(self, action: Action) -> str | None:
        """Returns the raw string a READ action read (None for every
        other action type) -- captured so a later, successful trace can
        tell artifact construction (Phase 9, cuas.artifact_builder) what
        was actually read, letting it infer a typed OutputSpec instead of
        guessing one."""

        if action.action_type == ActionType.FILL:
            if action.target is None or action.value is None:
                raise ValueError(f"FILL requires target and value (intent={action.intent!r})")
            await self._surface.fill(action.target, action.value)
            return None
        elif action.action_type in (ActionType.CLICK, ActionType.DISMISS):
            if action.target is None:
                raise ValueError(f"{action.action_type.value} requires target (intent={action.intent!r})")
            await self._surface.click(action.target)
            return None
        elif action.action_type == ActionType.NAVIGATE:
            if action.value is None:
                raise ValueError(f"NAVIGATE requires value, the URL (intent={action.intent!r})")
            await self._surface.navigate(action.value)
            return None
        elif action.action_type == ActionType.READ:
            if action.target is None:
                raise ValueError(f"READ requires target (intent={action.intent!r})")
            return await self._surface.read(action.target)
        else:
            raise ValueError(f"unsupported action_type for discovery: {action.action_type}")

    # -- shared execute-and-record turn --------------------------------------

    async def _execute_and_record(
        self,
        run_id: str,
        step_index: int,
        action: Action,
        decision: PolicyDecision,
        trace_step: DiscoveryTraceStep,
        trace: DiscoveryTrace,
        history: list[DiscoveryHistoryEntry],
        named_values: dict[str, str],
    ) -> Observation:
        """Executes one already policy-cleared action and records the
        outcome identically whether it came from a normal turn's fresh
        LLM proposal or from a resumed, operator-approved
        DiscoveryPendingApproval.pending_action -- the single execution
        path both share, so approval can never imply execution success by
        construction (a failure here becomes the same ordinary
        "execution_failed" trace_step/history entry either way, and the
        caller's bounded loop is what decides what happens next)."""

        error_message: str | None = None
        read_value: str | None = None
        try:
            read_value = await self._execute(action)
            trace_step.outcome = "executed"
        except (AutomationError, ValueError) as exc:
            error_message = str(exc)
            trace_step.outcome = "execution_failed"
            trace_step.execution_error = redact_named_values(error_message, named_values)
            await self._capture_step_failure_evidence(run_id, step_index, exc, history)

        trace_step.read_value = redact_named_values(read_value, named_values) if read_value is not None else None

        self._emit(
            run_id,
            EventType.ACTION_EXECUTED if error_message is None else EventType.STEP_FAILED,
            step_id=str(step_index),
            status="success" if error_message is None else "failure",
            details={
                "action_type": action.action_type.value,
                "intent": action.intent,
                **({"error": trace_step.execution_error} if trace_step.execution_error else {}),
            },
        )

        trace.steps.append(trace_step)
        observation = await self._surface.observe()
        history.append(
            DiscoveryHistoryEntry(
                step_index=step_index,
                action=action,
                policy_decision=decision,
                outcome=trace_step.outcome,
                error_message=error_message,
                observation_after=observation,
                read_value=read_value,
            )
        )
        return observation

    # -- outcome detection ------------------------------------------------

    async def _match_known_business_outcome(self, goal: DiscoveryGoal) -> str | None:
        for outcome in goal.known_business_outcomes:
            probe = outcome.detect.model_copy(update={"timeout_ms": _OUTCOME_CHECK_TIMEOUT_MS})
            try:
                await self._surface.wait_for(probe)
                return outcome.code
            except Exception:
                continue
        return None

    async def _verify_success_checkpoint(self, goal: DiscoveryGoal) -> bool:
        if goal.success_checkpoint is None:
            # Nothing declared to verify against -- the model's own
            # "done" claim is accepted as-is. Safe because "the goal was
            # accomplished" is not itself a safety decision; the
            # PolicyEngine (already checked on every action along the
            # way) is what keeps discovery from doing anything
            # consequential regardless of how this turns out.
            return True
        probe = goal.success_checkpoint.model_copy(update={"timeout_ms": _OUTCOME_CHECK_TIMEOUT_MS})
        try:
            await self._surface.wait_for(probe)
            return True
        except Exception:
            return False

    def _has_materializable_progress(self, history: list[DiscoveryHistoryEntry]) -> bool:
        """Whether this trace, so far, contains at least one executed
        action ArtifactBuilder could actually build a reusable artifact
        from -- the gate a "done" claim must clear before discovery is
        allowed to report success.

        Deliberately narrower than "any executed action at all": a
        random executed click/fill/dismiss unrelated to the goal must
        not count, or a workflow could satisfy this by doing something
        irrelevant. The specific bar chosen is "at least one executed
        READ", because that is not an arbitrary, discovery-only rule --
        it is the exact same requirement `ArtifactBuilder.build()`
        already enforces unconditionally downstream: `_build_outputs`
        turns only READ actions into a declared OutputSpec, and `build()`
        refuses to construct an artifact with no outputs at all (module
        docstring point 5; see also point 8's leading-navigate-step fix,
        which this complements -- that fix ensures a *replayable* path
        exists for a successful trace, this one ensures a trace is only
        ever reported successful once it actually has one). Checking it
        here, before discovery ever reports SUCCESS, means a model that
        spots the answer without an action to fetch it gets a chance to
        correct course, instead of the run finishing "successfully" and
        then failing anyway when RunOrchestrator tries to materialize it
        (see DECISIONS_LOG.md for the real run that first exposed this)."""

        return any(entry.outcome == "executed" and entry.action.action_type == ActionType.READ for entry in history)

    # -- loop/repetition detection -----------------------------------------

    def _action_signature(self, action: Action) -> str:
        target_sig = None
        if action.target is not None:
            p = action.target.primary
            target_sig = f"{p.strategy.value}:{sorted(p.params.items())}:{p.frame}"
        return f"{action.action_type.value}:{action.intent}:{target_sig}:{action.value}"

    # -- observability ------------------------------------------------------

    def _emit(
        self, run_id: str, event: EventType, *, step_id: str | None = None, status: str = "info",
        details: dict[str, Any] | None = None,
    ) -> None:
        self._event_sink.record(
            RunEvent(run_id=run_id, component="discovery_engine", event=event, step_id=step_id, status=status, details=details or {})
        )

    async def _capture_step_failure_evidence(
        self, run_id: str, step_index: int, exc: Exception, history: list[DiscoveryHistoryEntry]
    ) -> None:
        """Mirrors ReplayEngine._capture_failure_evidence: best-effort,
        never allowed to mask the real failure it's documenting. Called
        for a genuine per-step execution failure (a proposed action that
        didn't work against the live surface) -- not for policy
        escalations or a clean success/business-outcome ending, exactly
        the same "evidence only on genuine failures" discipline Phase 7
        established for replay."""

        try:
            evidence = await self._surface.capture_evidence()
        except Exception:  # noqa: BLE001 - evidence capture is never allowed to be fatal
            evidence = None

        recent_actions = [f"{entry.step_index}:{entry.outcome}" for entry in history[-_RECENT_ACTIONS_FOR_EVIDENCE:]]

        record = self._evidence_store.capture_failure_evidence(
            run_id=run_id, step_id=str(step_index), reason="action_execution_failed",
            error_code=None, error_message=str(exc), url=evidence.url if evidence is not None else None,
            screenshot_png=evidence.screenshot_png if evidence is not None else None,
            dom_snapshot=evidence.dom_snapshot if evidence is not None else None, recent_actions=recent_actions,
        )
        self._emit(
            run_id, EventType.EVIDENCE_CAPTURED, step_id=str(step_index),
            details={
                "reason": "action_execution_failed", "screenshot_path": record.screenshot_path,
                "dom_snapshot_path": record.dom_snapshot_path,
                "screenshot_is_unredacted_pii_risk": record.screenshot_is_unredacted_pii_risk,
            },
        )

    async def _capture_terminal_evidence(self, run_id: str, step_index: int, reason: str) -> None:
        """Same discipline, for the loop's own hard-stop reasons
        (malformed output, a detected loop, exhausting a bound) rather
        than a single step's execution failure -- these are also genuine
        failures of the run, just not tied to one action's outcome."""

        try:
            evidence = await self._surface.capture_evidence()
        except Exception:  # noqa: BLE001
            evidence = None

        record = self._evidence_store.capture_failure_evidence(
            run_id=run_id, step_id=str(step_index), reason=reason, error_code=None, error_message=reason,
            url=evidence.url if evidence is not None else None,
            screenshot_png=evidence.screenshot_png if evidence is not None else None,
            dom_snapshot=evidence.dom_snapshot if evidence is not None else None, recent_actions=[],
        )
        self._emit(
            run_id, EventType.EVIDENCE_CAPTURED, step_id=str(step_index),
            details={
                "reason": reason, "screenshot_path": record.screenshot_path,
                "dom_snapshot_path": record.dom_snapshot_path,
                "screenshot_is_unredacted_pii_risk": record.screenshot_is_unredacted_pii_risk,
            },
        )

    # -- result assembly ----------------------------------------------------

    def _finish(
        self, run_id: str, status: DiscoveryStatus, goal: DiscoveryGoal, steps_taken: int,
        history: list[DiscoveryHistoryEntry], *, reason: str,
        pending_approval: DiscoveryPendingApproval | None = None,
    ) -> DiscoveryResult:
        is_escalation = status in (DiscoveryStatus.BLOCKED, DiscoveryStatus.APPROVAL_REQUIRED)
        return DiscoveryResult(
            run_id=run_id, status=status, capability_id=goal.capability_id, steps_taken=steps_taken,
            escalation_step_index=steps_taken if is_escalation else None,
            escalation_reason=reason if is_escalation else None,
            error_message=None if is_escalation else reason,
            history=self._redact_history(history, goal.named_sensitive_values()),
            pending_approval=pending_approval,
        )

    def _redact_history(
        self, history: list[DiscoveryHistoryEntry], named_values: dict[str, str]
    ) -> list[DiscoveryHistoryEntry]:
        """The boundary where discovery's internal, necessarily-raw
        working history (AnthropicLLMClient needs real values to keep
        reasoning correctly turn to turn) becomes safe to hand back to a
        caller or persist -- never log prompts/responses containing raw
        sensitive values without redaction."""

        if not named_values:
            return list(history)

        redacted: list[DiscoveryHistoryEntry] = []
        for entry in history:
            redacted_action = entry.action.model_copy(
                update={"value": redact_named_values(entry.action.value, named_values) if entry.action.value else entry.action.value}
            )
            redacted_observation = None
            if entry.observation_after is not None:
                redacted_observation = entry.observation_after.model_copy(
                    update={
                        "url": redact_named_values(entry.observation_after.url, named_values),
                        "visible_text": redact_named_values(entry.observation_after.visible_text, named_values),
                    }
                )
            redacted.append(
                entry.model_copy(
                    update={
                        "action": redacted_action,
                        "error_message": redact_named_values(entry.error_message, named_values) if entry.error_message else entry.error_message,
                        "observation_after": redacted_observation,
                        "read_value": redact_named_values(entry.read_value, named_values) if entry.read_value else entry.read_value,
                    }
                )
            )
        return redacted
