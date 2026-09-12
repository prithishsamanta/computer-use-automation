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
    DiscoveryResult,
    DiscoveryStatus,
)
from cuas.discovery.trace import DiscoveryTrace, DiscoveryTraceStep, DiscoveryTraceStore, NullDiscoveryTraceStore
from cuas.domain import Action, ActionType, AppContext, AutomationError, Locator, LocatorStrategy, Target
from cuas.observability.event_sink import EventSink, NullEventSink
from cuas.observability.events import EventType, RunEvent
from cuas.observability.evidence import EvidenceStore, NullEvidenceStore
from cuas.observability.redaction import redact_dict, redact_text
from cuas.safety import PolicyDecision, PolicyEngine
from cuas.surface.adapter import SurfaceAdapter

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


def _redact_json(value: Any, sensitive_values: list[str]) -> Any:
    """Recursively applies redact_text to every string leaf of a
    dict/list structure. Used for the model's raw proposal dict and for a
    parsed Action's own JSON, since a sensitive value the model was given
    as an input can just as easily show up nested inside either (e.g.
    action.value == the raw member id it was told to type)."""

    if isinstance(value, str):
        return redact_text(value, sensitive_values)
    if isinstance(value, dict):
        return {key: _redact_json(item, sensitive_values) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item, sensitive_values) for item in value]
    return value


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
    ) -> DiscoveryResult:
        run_id = run_id or uuid.uuid4().hex
        limits = limits or DiscoveryLimits()
        sensitive_values = goal.sensitive_values()

        self._emit(
            run_id,
            EventType.RUN_STARTED,
            details={
                "capability_id": goal.capability_id,
                "tenant_id": context.tenant_id,
                "inputs": redact_dict(goal.inputs, goal.sensitive_inputs),
            },
        )

        trace = DiscoveryTrace(
            run_id=run_id,
            capability_id=goal.capability_id,
            # Redacted even though it's caller-authored, not model output --
            # a goal description commonly embeds the very input it's
            # describing (e.g. "Find member M1001..."), and this field is
            # persisted to disk like everything else in the trace.
            goal_description=redact_text(goal.description, sensitive_values),
            started_at=datetime.now(timezone.utc),
        )

        history: list[DiscoveryHistoryEntry] = []
        last_signature: str | None = None
        consecutive_repeats = 0
        total_tokens = 0
        start_time = self._clock()

        await self._surface.navigate(goal.start_url)
        observation = await self._surface.observe()

        result: DiscoveryResult | None = None

        for step_index in range(limits.max_steps):
            if self._clock() - start_time > limits.max_duration_seconds:
                result = self._finish(
                    run_id, DiscoveryStatus.MAX_DURATION_EXCEEDED, goal, step_index, history,
                    reason=f"exceeded max_duration_seconds={limits.max_duration_seconds}",
                )
                await self._capture_terminal_evidence(run_id, step_index, "max_duration_exceeded")
                break

            outcome_code = await self._match_known_business_outcome(goal)
            if outcome_code is not None:
                self._emit(run_id, EventType.BUSINESS_OUTCOME_DETECTED, step_id=str(step_index), details={"business_outcome_code": outcome_code})
                result = DiscoveryResult(
                    run_id=run_id, status=DiscoveryStatus.BUSINESS_OUTCOME, capability_id=goal.capability_id,
                    steps_taken=step_index, business_outcome_code=outcome_code,
                    history=self._redact_history(history, sensitive_values),
                )
                break

            self._emit(run_id, EventType.STEP_STARTED, step_id=str(step_index), details={"url": redact_text(observation.url, sensitive_values)})

            llm_response = await self._llm.propose_action(goal=goal, context=context, observation=observation, history=history)
            total_tokens += llm_response.input_tokens + llm_response.output_tokens

            trace_step = DiscoveryTraceStep(
                step_index=step_index,
                observation_url=redact_text(observation.url, sensitive_values),
                observation_text_excerpt=redact_text(observation.visible_text[:_OBSERVATION_EXCERPT_CHARS], sensitive_values),
                raw_model_output=redact_text(llm_response.raw_text, sensitive_values),
            )

            if limits.max_total_tokens is not None and total_tokens > limits.max_total_tokens:
                trace_step.outcome = "max_tokens_exceeded"
                trace.steps.append(trace_step)
                result = self._finish(
                    run_id, DiscoveryStatus.MAX_TOKENS_EXCEEDED, goal, step_index, history,
                    reason=f"exceeded max_total_tokens={limits.max_total_tokens}",
                )
                break

            action, done, parse_error = self._parse_proposal(llm_response)

            if parse_error is not None:
                trace_step.parse_error = redact_text(parse_error, sensitive_values)
                trace_step.outcome = "malformed_model_output"
                trace.steps.append(trace_step)
                self._emit(
                    run_id, EventType.MALFORMED_MODEL_OUTPUT, step_id=str(step_index), status="failure",
                    details={"parse_error": trace_step.parse_error},
                )
                await self._capture_terminal_evidence(run_id, step_index, "malformed_model_output")
                result = self._finish(
                    run_id, DiscoveryStatus.MALFORMED_MODEL_OUTPUT, goal, step_index, history, reason=parse_error
                )
                break

            if done:
                trace_step.outcome = "declared_done"
                trace.steps.append(trace_step)
                if await self._verify_success_checkpoint(goal):
                    self._emit(run_id, EventType.RUN_COMPLETED, status="success", details={"status": "success"})
                    result = DiscoveryResult(
                        run_id=run_id, status=DiscoveryStatus.SUCCESS, capability_id=goal.capability_id,
                        steps_taken=step_index + 1, history=self._redact_history(history, sensitive_values),
                    )
                    break
                # The model is informed about policy but is never the final
                # authority on outcomes either: a declared success_checkpoint
                # that doesn't verify means this "done" claim is not trusted,
                # and the loop simply continues (bounded by the same limits
                # as every other turn) rather than ending the run on the
                # model's word alone.
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
                trace_step.parsed_action = _redact_json(action.model_dump(mode="json"), sensitive_values)
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
            trace_step.parsed_action = _redact_json(action.model_dump(mode="json"), sensitive_values)
            self._emit(run_id, EventType.POLICY_CHECKED, step_id=str(step_index), details={"decision": decision.value, "intent": action.intent})

            if decision in (PolicyDecision.DENY, PolicyDecision.REQUIRE_APPROVAL):
                outcome = "policy_denied" if decision == PolicyDecision.DENY else "approval_required"
                trace_step.outcome = outcome
                trace.steps.append(trace_step)
                history.append(DiscoveryHistoryEntry(step_index=step_index, action=action, policy_decision=decision, outcome=outcome))
                status = DiscoveryStatus.BLOCKED if decision == PolicyDecision.DENY else DiscoveryStatus.APPROVAL_REQUIRED
                verb = "denied" if decision == PolicyDecision.DENY else "requires operator approval for"
                result = self._finish(
                    run_id, status, goal, step_index, history,
                    reason=f"policy {verb} proposed action (intent={action.intent!r})",
                )
                break

            self._emit(
                run_id, EventType.LLM_ACTION_PROPOSED, step_id=str(step_index),
                details={
                    "action_type": action.action_type.value,
                    "intent": action.intent,
                    "reasoning": redact_text(llm_response.proposal.get("reasoning", "") if llm_response.proposal else "", sensitive_values),
                },
            )

            error_message: str | None = None
            try:
                await self._execute(action)
                trace_step.outcome = "executed"
            except (AutomationError, ValueError) as exc:
                error_message = str(exc)
                trace_step.outcome = "execution_failed"
                trace_step.execution_error = redact_text(error_message, sensitive_values)
                await self._capture_step_failure_evidence(run_id, step_index, exc, history)

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
                )
            )
        else:
            result = self._finish(
                run_id, DiscoveryStatus.MAX_STEPS_EXCEEDED, goal, limits.max_steps, history,
                reason=f"exceeded max_steps={limits.max_steps}",
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
            action_type = ActionType(proposal["action_type"])
            intent = proposal["intent"]
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
            params: dict[str, Any] = {"role": target_dict["role"]}
            if target_dict.get("name"):
                params["name"] = target_dict["name"]
        else:
            params = {"selector": target_dict["selector"]}

        return Target(primary=Locator(strategy=strategy, params=params, frame=target_dict.get("frame")))

    # -- execution ------------------------------------------------------

    async def _execute(self, action: Action) -> None:
        if action.action_type == ActionType.FILL:
            if action.target is None or action.value is None:
                raise ValueError(f"FILL requires target and value (intent={action.intent!r})")
            await self._surface.fill(action.target, action.value)
        elif action.action_type in (ActionType.CLICK, ActionType.DISMISS):
            if action.target is None:
                raise ValueError(f"{action.action_type.value} requires target (intent={action.intent!r})")
            await self._surface.click(action.target)
        elif action.action_type == ActionType.NAVIGATE:
            if action.value is None:
                raise ValueError(f"NAVIGATE requires value, the URL (intent={action.intent!r})")
            await self._surface.navigate(action.value)
        elif action.action_type == ActionType.READ:
            if action.target is None:
                raise ValueError(f"READ requires target (intent={action.intent!r})")
            await self._surface.read(action.target)
        else:
            raise ValueError(f"unsupported action_type for discovery: {action.action_type}")

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
    ) -> DiscoveryResult:
        is_escalation = status in (DiscoveryStatus.BLOCKED, DiscoveryStatus.APPROVAL_REQUIRED)
        return DiscoveryResult(
            run_id=run_id, status=status, capability_id=goal.capability_id, steps_taken=steps_taken,
            escalation_step_index=steps_taken if is_escalation else None,
            escalation_reason=reason if is_escalation else None,
            error_message=None if is_escalation else reason,
            history=self._redact_history(history, goal.sensitive_values()),
        )

    def _redact_history(
        self, history: list[DiscoveryHistoryEntry], sensitive_values: list[str]
    ) -> list[DiscoveryHistoryEntry]:
        """The boundary where discovery's internal, necessarily-raw
        working history (AnthropicLLMClient needs real values to keep
        reasoning correctly turn to turn) becomes safe to hand back to a
        caller or persist -- never log prompts/responses containing raw
        sensitive values without redaction."""

        if not sensitive_values:
            return list(history)

        redacted: list[DiscoveryHistoryEntry] = []
        for entry in history:
            redacted_action = entry.action.model_copy(
                update={"value": redact_text(entry.action.value, sensitive_values) if entry.action.value else entry.action.value}
            )
            redacted_observation = None
            if entry.observation_after is not None:
                redacted_observation = entry.observation_after.model_copy(
                    update={
                        "url": redact_text(entry.observation_after.url, sensitive_values),
                        "visible_text": redact_text(entry.observation_after.visible_text, sensitive_values),
                    }
                )
            redacted.append(
                entry.model_copy(
                    update={
                        "action": redacted_action,
                        "error_message": redact_text(entry.error_message, sensitive_values) if entry.error_message else entry.error_message,
                        "observation_after": redacted_observation,
                    }
                )
            )
        return redacted
