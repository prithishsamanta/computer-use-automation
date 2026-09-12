"""Deterministic replay (.CLAUDE/03_DISCOVERY_AND_REPLAY.md, "Deterministic
Replay"). No LLM anywhere in this module -- it follows the artifact.

Per-step flow: policy check -> execute action (bounded recovery on
failure) -> if the step declares a checkpoint, verify it, checking for a
known business outcome before giving up and attempting recovery before
declaring a hard failure. After every step has run: extract typed outputs,
verify the success condition. Every exit is a structured ReplayResult, not
an improvised decision -- unresolved/unsafe states surface as FAILED,
APPROVAL_REQUIRED, or BLOCKED, and it is the caller's (eventually
RunOrchestrator's, Phase 11) job to act on that, e.g. by opening a real
InterventionRequest (Phase 12).

Phase 7 adds structured observability on top of that unchanged algorithm:
every run gets a run_id, and every event in
.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md's "Structured Logging" list is
emitted through an injected EventSink, with richer evidence captured
through an injected EvidenceStore on the FAILED path specifically. Both
default to no-op implementations, so a caller that doesn't wire either one
up (every Phase 5/6 test, unchanged) gets byte-for-byte the same replay
behavior as before -- observability is additive, never a precondition.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from cuas.artifact.schema import Artifact, OutputType, Step, SuccessConditionType
from cuas.domain import (
    ActionType,
    AppContext,
    AutomationError,
    CheckpointFailedError,
    ErrorCode,
    OutputExtractionFailedError,
)
from cuas.observability.event_sink import EventSink, NullEventSink
from cuas.observability.events import EventType, RunEvent
from cuas.observability.evidence import EvidenceStore, NullEvidenceStore
from cuas.observability.redaction import redact_inputs
from cuas.safety import PolicyDecision, PolicyEngine
from cuas.surface.adapter import SurfaceAdapter

# How many recent step_log entries ride along with failure evidence as
# "recent action history" (.CLAUDE/06, "Rich Failure Evidence"). Small and
# fixed on purpose -- this is context for a human glancing at one failure,
# not a full run transcript (the JsonlEventSink already has that).
_RECENT_ACTIONS_FOR_EVIDENCE = 5


class ReplayStatus(str, Enum):
    SUCCESS = "success"
    BUSINESS_OUTCOME = "business_outcome"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"
    FAILED = "failed"


class StepLogEntry(BaseModel):
    step_id: str | None
    event: str
    detail: str = ""


class ReplayResult(BaseModel):
    run_id: str
    status: ReplayStatus
    outputs: dict[str, Any] = Field(default_factory=dict)
    business_outcome_code: str | None = None
    escalation_step_id: str | None = None
    escalation_reason: str | None = None
    error_code: ErrorCode | None = None
    error_message: str | None = None
    step_log: list[StepLogEntry] = Field(default_factory=list)


class ReplayEngine:
    def __init__(
        self,
        surface: SurfaceAdapter,
        policy: PolicyEngine,
        *,
        recovery_timeout_ms: int = 800,
        event_sink: EventSink | None = None,
        evidence_store: EvidenceStore | None = None,
    ):
        self._surface = surface
        self._policy = policy
        self._recovery_timeout_ms = recovery_timeout_ms
        self._event_sink = event_sink or NullEventSink()
        self._evidence_store = evidence_store or NullEvidenceStore()

    async def run(
        self,
        artifact: Artifact,
        inputs: dict[str, Any],
        context: AppContext,
        *,
        run_id: str | None = None,
    ) -> ReplayResult:
        """Caller contract: `artifact` is already schema-valid (pydantic
        guarantees that by construction; ArtifactRepository.load raises
        ArtifactInvalidError before an invalid one ever reaches here) and
        `context` is already known compatible with it (CapabilityService's
        job, Phase 10). This method still validates *inputs* against the
        artifact's declared InputSpecs, since those are per-invocation and
        nothing upstream has checked them yet.

        `run_id` is normally left for this call to generate (a fresh uuid4
        per invocation); a caller that already has one -- e.g. a future
        RunOrchestrator correlating replay with its own orchestration-level
        events -- can pass it in instead so every component logs under the
        same id.
        """

        run_id = run_id or uuid.uuid4().hex

        self._emit(
            run_id,
            EventType.RUN_STARTED,
            details={
                "capability_id": artifact.capability_id,
                "version": artifact.version,
                "tenant_id": context.tenant_id,
                # Sensitive inputs (e.g. a member ID) must never land in a
                # log line -- see observability/redaction.py.
                "inputs": redact_inputs(artifact, inputs),
            },
        )

        self._validate_inputs(artifact, inputs)

        self._emit(
            run_id,
            EventType.ARTIFACT_LOADED,
            details={"capability_id": artifact.capability_id, "version": artifact.version},
        )

        log: list[StepLogEntry] = []

        for step in artifact.steps:
            self._emit(
                run_id,
                EventType.STEP_STARTED,
                step_id=step.id,
                details={"action_type": step.action_type.value, "intent": step.intent},
            )

            decision = self._policy.evaluate(step, context)
            log.append(StepLogEntry(step_id=step.id, event="policy_check", detail=decision.value))
            self._emit(
                run_id,
                EventType.POLICY_CHECKED,
                step_id=step.id,
                details={"decision": decision.value, "intent": step.intent},
            )

            if decision == PolicyDecision.DENY:
                self._emit(run_id, EventType.RUN_COMPLETED, status="blocked", details={"status": "blocked"})
                return ReplayResult(
                    run_id=run_id,
                    status=ReplayStatus.BLOCKED,
                    step_log=log,
                    escalation_step_id=step.id,
                    escalation_reason=f"policy denied step {step.id!r} (intent={step.intent!r})",
                )
            if decision == PolicyDecision.REQUIRE_APPROVAL:
                self._emit(
                    run_id, EventType.RUN_COMPLETED, status="approval_required", details={"status": "approval_required"}
                )
                return ReplayResult(
                    run_id=run_id,
                    status=ReplayStatus.APPROVAL_REQUIRED,
                    step_log=log,
                    escalation_step_id=step.id,
                    escalation_reason=f"step {step.id!r} (intent={step.intent!r}) requires operator approval",
                )

            try:
                await self._execute_action(artifact, step, inputs, log, run_id=run_id)
            except AutomationError as exc:
                log.append(StepLogEntry(step_id=step.id, event="step_failed", detail=str(exc)))
                await self._capture_failure_evidence(run_id, step.id, "step_failed", exc, log)
                self._emit(
                    run_id,
                    EventType.STEP_FAILED,
                    step_id=step.id,
                    status="failure",
                    details={"error_code": exc.code.value, "error_message": str(exc)},
                )
                self._emit(
                    run_id, EventType.RUN_COMPLETED, status="failed", details={"status": "failed", "error_code": exc.code.value}
                )
                return ReplayResult(run_id=run_id, status=ReplayStatus.FAILED, step_log=log, error_code=exc.code, error_message=str(exc))

            self._emit(
                run_id,
                EventType.ACTION_EXECUTED,
                step_id=step.id,
                status="success",
                # The step's *template* value (e.g. "{{member_id}}"), never
                # the substituted one -- see redaction.py's module docstring
                # for why that alone is what keeps this event leak-free.
                details={"action_type": step.action_type.value, "value_template": step.value},
            )
            log.append(StepLogEntry(step_id=step.id, event="step_executed"))

            if step.checkpoint is not None:
                try:
                    outcome_code = await self._verify_checkpoint(artifact, step, log, run_id=run_id)
                except AutomationError as exc:
                    log.append(StepLogEntry(step_id=step.id, event="checkpoint_failed", detail=str(exc)))
                    await self._capture_failure_evidence(run_id, step.id, "checkpoint_failed", exc, log)
                    self._emit(
                        run_id,
                        EventType.STEP_FAILED,
                        step_id=step.id,
                        status="failure",
                        details={"error_code": exc.code.value, "error_message": str(exc)},
                    )
                    self._emit(
                        run_id,
                        EventType.RUN_COMPLETED,
                        status="failed",
                        details={"status": "failed", "error_code": exc.code.value},
                    )
                    return ReplayResult(run_id=run_id, status=ReplayStatus.FAILED, step_log=log, error_code=exc.code, error_message=str(exc))
                if outcome_code is not None:
                    log.append(StepLogEntry(step_id=step.id, event="business_outcome_detected", detail=outcome_code))
                    self._emit(
                        run_id,
                        EventType.BUSINESS_OUTCOME_DETECTED,
                        step_id=step.id,
                        details={"business_outcome_code": outcome_code},
                    )
                    self._emit(
                        run_id,
                        EventType.RUN_COMPLETED,
                        status="business_outcome",
                        details={"status": "business_outcome", "business_outcome_code": outcome_code},
                    )
                    return ReplayResult(run_id=run_id, status=ReplayStatus.BUSINESS_OUTCOME, step_log=log, business_outcome_code=outcome_code)
                log.append(StepLogEntry(step_id=step.id, event="checkpoint_passed"))
                self._emit(run_id, EventType.CHECKPOINT_PASSED, step_id=step.id, status="success")

        try:
            outputs = await self._extract_outputs(artifact)
        except AutomationError as exc:
            log.append(StepLogEntry(step_id=None, event="output_extraction_failed", detail=str(exc)))
            await self._capture_failure_evidence(run_id, None, "output_extraction_failed", exc, log)
            self._emit(
                run_id, EventType.STEP_FAILED, status="failure", details={"error_code": exc.code.value, "error_message": str(exc)}
            )
            self._emit(run_id, EventType.RUN_COMPLETED, status="failed", details={"status": "failed", "error_code": exc.code.value})
            return ReplayResult(run_id=run_id, status=ReplayStatus.FAILED, step_log=log, error_code=exc.code, error_message=str(exc))

        if not self._verify_success_condition(artifact, outputs):
            message = f"success_condition not satisfied by outputs {outputs!r}"
            log.append(StepLogEntry(step_id=None, event="success_condition_failed", detail=message))
            exc = OutputExtractionFailedError(message)
            await self._capture_failure_evidence(run_id, None, "success_condition_failed", exc, log)
            self._emit(
                run_id,
                EventType.STEP_FAILED,
                status="failure",
                details={"error_code": exc.code.value, "error_message": message},
            )
            self._emit(run_id, EventType.RUN_COMPLETED, status="failed", details={"status": "failed", "error_code": exc.code.value})
            return ReplayResult(
                run_id=run_id,
                status=ReplayStatus.FAILED,
                step_log=log,
                error_code=ErrorCode.OUTPUT_EXTRACTION_FAILED,
                error_message=message,
            )

        log.append(StepLogEntry(step_id=None, event="run_completed"))
        self._emit(run_id, EventType.RUN_COMPLETED, status="success", details={"status": "success"})
        return ReplayResult(run_id=run_id, status=ReplayStatus.SUCCESS, outputs=outputs, step_log=log)

    # -- observability ------------------------------------------------------

    def _emit(
        self,
        run_id: str,
        event: EventType,
        *,
        step_id: str | None = None,
        status: str = "info",
        details: dict[str, Any] | None = None,
    ) -> None:
        self._event_sink.record(
            RunEvent(run_id=run_id, event=event, step_id=step_id, status=status, details=details or {})
        )

    async def _capture_failure_evidence(
        self,
        run_id: str,
        step_id: str | None,
        reason: str,
        exc: AutomationError,
        log: list[StepLogEntry],
    ) -> None:
        """Only called on the FAILED path -- a normal success (or a
        business outcome, or an approval/deny escalation, none of which
        are failures) never reaches here, so successful runs never pay for
        a screenshot/AX snapshot they don't need.

        Best-effort: a problem capturing evidence must never mask or
        replace the real failure this run already has. If
        surface.capture_evidence() itself raises, evidence is simply
        skipped for this failure rather than the run blowing up a second
        time on top of its first, real error.
        """

        try:
            evidence = await self._surface.capture_evidence()
        except Exception:  # noqa: BLE001 - evidence capture is never allowed to be fatal
            evidence = None

        recent_actions = [f"{entry.step_id}:{entry.event}" for entry in log[-_RECENT_ACTIONS_FOR_EVIDENCE:]]

        record = self._evidence_store.capture_failure_evidence(
            run_id=run_id,
            step_id=step_id,
            reason=reason,
            error_code=exc.code.value,
            error_message=str(exc),
            url=evidence.url if evidence is not None else None,
            screenshot_png=evidence.screenshot_png if evidence is not None else None,
            dom_snapshot=evidence.dom_snapshot if evidence is not None else None,
            recent_actions=recent_actions,
        )
        self._emit(
            run_id,
            EventType.EVIDENCE_CAPTURED,
            step_id=step_id,
            details={
                "reason": reason,
                "screenshot_path": record.screenshot_path,
                "dom_snapshot_path": record.dom_snapshot_path,
                # Explicit, not implied: a stored screenshot is never
                # treated as sanitized just because textual fields are
                # redacted elsewhere -- see observability/evidence.py.
                "screenshot_is_unredacted_pii_risk": record.screenshot_is_unredacted_pii_risk,
            },
        )

    # -- inputs -----------------------------------------------------------

    def _validate_inputs(self, artifact: Artifact, inputs: dict[str, Any]) -> None:
        missing = [name for name, spec in artifact.inputs.items() if spec.required and name not in inputs]
        if missing:
            raise ValueError(f"missing required input(s) for {artifact.capability_id!r}: {missing}")

    def _substitute(self, value: str | None, inputs: dict[str, Any]) -> str | None:
        if value is None:
            return None
        result = value
        for name, replacement in inputs.items():
            result = result.replace(f"{{{{{name}}}}}", str(replacement))
        return result

    # -- step execution + bounded recovery --------------------------------

    async def _execute_action(
        self,
        artifact: Artifact,
        step: Step,
        inputs: dict[str, Any],
        log: list[StepLogEntry],
        *,
        run_id: str,
    ) -> None:
        async def attempt() -> None:
            value = self._substitute(step.value, inputs)
            if step.action_type == ActionType.FILL:
                if step.target is None or value is None:
                    raise ValueError(f"step {step.id!r}: FILL requires target and value")
                await self._surface.fill(step.target, value, timeout_ms=step.wait.timeout_ms)
            elif step.action_type in (ActionType.CLICK, ActionType.DISMISS):
                if step.target is None:
                    raise ValueError(f"step {step.id!r}: {step.action_type} requires target")
                await self._surface.click(step.target, timeout_ms=step.wait.timeout_ms)
            elif step.action_type == ActionType.NAVIGATE:
                if value is None:
                    raise ValueError(f"step {step.id!r}: NAVIGATE requires value (the URL)")
                await self._surface.navigate(value)
            elif step.action_type == ActionType.READ:
                if step.target is None:
                    raise ValueError(f"step {step.id!r}: READ requires target")
                await self._surface.read(step.target)
            elif step.action_type == ActionType.WAIT_FOR:
                if step.checkpoint is None:
                    raise ValueError(f"step {step.id!r}: WAIT_FOR requires a checkpoint condition")
                await self._surface.wait_for(step.checkpoint)
            else:
                raise ValueError(f"step {step.id!r}: unsupported action_type {step.action_type}")

        try:
            await attempt()
        except AutomationError:
            recovered = await self._attempt_recovery(artifact, log, run_id=run_id, step_id=step.id)
            if not recovered:
                raise
            log.append(StepLogEntry(step_id=step.id, event="recovery_retry"))
            await attempt()  # bounded to exactly one retry; a second failure propagates

    async def _attempt_recovery(
        self, artifact: Artifact, log: list[StepLogEntry], *, run_id: str, step_id: str | None
    ) -> bool:
        """Checks each recoverable_condition once, in declared order, and
        acts on the first one currently present. Bounded: this makes at
        most one recovery attempt per call (.CLAUDE/03_DISCOVERY_AND_REPLAY.md,
        "Recovery should be bounded and safe" / "A repeated failure should
        not become an infinite loop"). Emits exactly one RECOVERY_ATTEMPTED
        event per call regardless of outcome -- "we looked and found
        nothing to recover from" is itself meaningful traceability, not
        just a successful dismissal."""

        recovered_condition: str | None = None
        for condition in artifact.recoverable_conditions:
            probe = condition.detect.model_copy(update={"timeout_ms": self._recovery_timeout_ms})
            try:
                await self._surface.wait_for(probe)
            except Exception:
                continue

            log.append(StepLogEntry(step_id=None, event="recoverable_condition_detected", detail=condition.code))
            if condition.recovery.value == "dismiss":
                assert condition.dismiss_target is not None  # enforced by RecoverableCondition's own validator
                await self._surface.click(condition.dismiss_target)
            # "wait" / "retry" / "re_resolve_target": no extra action here --
            # the caller's retry of the original operation IS the recovery
            # (a fresh wait_for/target resolution, run after a short pause
            # for "wait").
            recovered_condition = condition.code
            break

        self._emit(
            run_id,
            EventType.RECOVERY_ATTEMPTED,
            step_id=step_id,
            details={"recovered": recovered_condition is not None, "condition_code": recovered_condition},
        )
        return recovered_condition is not None

    # -- checkpoints + business outcomes -----------------------------------

    async def _verify_checkpoint(
        self, artifact: Artifact, step: Step, log: list[StepLogEntry], *, run_id: str
    ) -> str | None:
        """Returns a business-outcome code if the checkpoint didn't appear
        because a known alternative state did instead; returns None if the
        checkpoint passed (possibly after one bounded recovery attempt);
        raises CheckpointFailedError otherwise."""

        assert step.checkpoint is not None
        try:
            await self._surface.wait_for(step.checkpoint)
            return None
        except Exception as first_exc:
            pass

        outcome_code = await self._match_business_outcome(artifact)
        if outcome_code is not None:
            return outcome_code

        if await self._attempt_recovery(artifact, log, run_id=run_id, step_id=step.id):
            try:
                await self._surface.wait_for(step.checkpoint)
                return None
            except Exception as exc:
                raise CheckpointFailedError(f"checkpoint for step {step.id!r} failed after recovery: {exc}") from exc

        raise CheckpointFailedError(f"checkpoint for step {step.id!r} failed: {first_exc}") from first_exc

    async def _match_business_outcome(self, artifact: Artifact) -> str | None:
        for outcome in artifact.business_outcomes:
            probe = outcome.detect.model_copy(update={"timeout_ms": self._recovery_timeout_ms})
            try:
                await self._surface.wait_for(probe)
                return outcome.code
            except Exception:
                continue
        return None

    # -- output extraction --------------------------------------------------

    async def _extract_outputs(self, artifact: Artifact) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        for name, spec in artifact.outputs.items():
            try:
                raw = await self._surface.read(spec.source)
            except AutomationError as exc:
                raise OutputExtractionFailedError(f"output {name!r}: could not read source: {exc}") from exc

            try:
                value = self._parse_output(spec.type, raw)
            except Exception as exc:
                raise OutputExtractionFailedError(
                    f"output {name!r}: could not parse {raw!r} as {spec.type.value}: {exc}"
                ) from exc

            if spec.required and value is None:
                raise OutputExtractionFailedError(f"required output {name!r} was empty")
            outputs[name] = value
        return outputs

    def _parse_output(self, output_type: OutputType, raw: str) -> Any:
        text = raw.strip()
        if output_type == OutputType.STRING:
            return text
        if output_type == OutputType.DECIMAL:
            return Decimal(text.lstrip("$").replace(",", ""))
        if output_type == OutputType.INTEGER:
            return int(text.replace(",", ""))
        if output_type == OutputType.BOOLEAN:
            return text.strip().lower() in ("true", "yes", "1")
        raise ValueError(f"unsupported output type {output_type}")

    def _verify_success_condition(self, artifact: Artifact, outputs: dict[str, Any]) -> bool:
        condition = artifact.success_condition
        if condition.type == SuccessConditionType.OUTPUT_VALID:
            return outputs.get(condition.output) is not None
        return False
