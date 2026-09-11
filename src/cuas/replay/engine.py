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
"""

from __future__ import annotations

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
from cuas.safety import PolicyDecision, PolicyEngine
from cuas.surface.adapter import SurfaceAdapter


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
    status: ReplayStatus
    outputs: dict[str, Any] = Field(default_factory=dict)
    business_outcome_code: str | None = None
    escalation_step_id: str | None = None
    escalation_reason: str | None = None
    error_code: ErrorCode | None = None
    error_message: str | None = None
    step_log: list[StepLogEntry] = Field(default_factory=list)


class ReplayEngine:
    def __init__(self, surface: SurfaceAdapter, policy: PolicyEngine, *, recovery_timeout_ms: int = 800):
        self._surface = surface
        self._policy = policy
        self._recovery_timeout_ms = recovery_timeout_ms

    async def run(self, artifact: Artifact, inputs: dict[str, Any], context: AppContext) -> ReplayResult:
        """Caller contract: `artifact` is already schema-valid (pydantic
        guarantees that by construction; ArtifactRepository.load raises
        ArtifactInvalidError before an invalid one ever reaches here) and
        `context` is already known compatible with it (CapabilityService's
        job, Phase 10). This method still validates *inputs* against the
        artifact's declared InputSpecs, since those are per-invocation and
        nothing upstream has checked them yet.
        """

        self._validate_inputs(artifact, inputs)
        log: list[StepLogEntry] = []

        for step in artifact.steps:
            decision = self._policy.evaluate(step, context)
            log.append(StepLogEntry(step_id=step.id, event="policy_check", detail=decision.value))

            if decision == PolicyDecision.DENY:
                return ReplayResult(
                    status=ReplayStatus.BLOCKED,
                    step_log=log,
                    escalation_step_id=step.id,
                    escalation_reason=f"policy denied step {step.id!r} (intent={step.intent!r})",
                )
            if decision == PolicyDecision.REQUIRE_APPROVAL:
                return ReplayResult(
                    status=ReplayStatus.APPROVAL_REQUIRED,
                    step_log=log,
                    escalation_step_id=step.id,
                    escalation_reason=f"step {step.id!r} (intent={step.intent!r}) requires operator approval",
                )

            try:
                await self._execute_action(artifact, step, inputs, log)
            except AutomationError as exc:
                log.append(StepLogEntry(step_id=step.id, event="step_failed", detail=str(exc)))
                return ReplayResult(status=ReplayStatus.FAILED, step_log=log, error_code=exc.code, error_message=str(exc))
            log.append(StepLogEntry(step_id=step.id, event="step_executed"))

            if step.checkpoint is not None:
                try:
                    outcome_code = await self._verify_checkpoint(artifact, step, log)
                except AutomationError as exc:
                    log.append(StepLogEntry(step_id=step.id, event="checkpoint_failed", detail=str(exc)))
                    return ReplayResult(status=ReplayStatus.FAILED, step_log=log, error_code=exc.code, error_message=str(exc))
                if outcome_code is not None:
                    log.append(StepLogEntry(step_id=step.id, event="business_outcome_detected", detail=outcome_code))
                    return ReplayResult(status=ReplayStatus.BUSINESS_OUTCOME, step_log=log, business_outcome_code=outcome_code)
                log.append(StepLogEntry(step_id=step.id, event="checkpoint_passed"))

        try:
            outputs = await self._extract_outputs(artifact)
        except AutomationError as exc:
            log.append(StepLogEntry(step_id=None, event="output_extraction_failed", detail=str(exc)))
            return ReplayResult(status=ReplayStatus.FAILED, step_log=log, error_code=exc.code, error_message=str(exc))

        if not self._verify_success_condition(artifact, outputs):
            message = f"success_condition not satisfied by outputs {outputs!r}"
            log.append(StepLogEntry(step_id=None, event="success_condition_failed", detail=message))
            return ReplayResult(
                status=ReplayStatus.FAILED,
                step_log=log,
                error_code=ErrorCode.OUTPUT_EXTRACTION_FAILED,
                error_message=message,
            )

        log.append(StepLogEntry(step_id=None, event="run_completed"))
        return ReplayResult(status=ReplayStatus.SUCCESS, outputs=outputs, step_log=log)

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
        self, artifact: Artifact, step: Step, inputs: dict[str, Any], log: list[StepLogEntry]
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
            recovered = await self._attempt_recovery(artifact, log)
            if not recovered:
                raise
            log.append(StepLogEntry(step_id=step.id, event="recovery_retry"))
            await attempt()  # bounded to exactly one retry; a second failure propagates

    async def _attempt_recovery(self, artifact: Artifact, log: list[StepLogEntry]) -> bool:
        """Checks each recoverable_condition once, in declared order, and
        acts on the first one currently present. Bounded: this makes at
        most one recovery attempt per call (.CLAUDE/03_DISCOVERY_AND_REPLAY.md,
        "Recovery should be bounded and safe" / "A repeated failure should
        not become an infinite loop")."""

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
            return True
        return False

    # -- checkpoints + business outcomes -----------------------------------

    async def _verify_checkpoint(self, artifact: Artifact, step: Step, log: list[StepLogEntry]) -> str | None:
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

        if await self._attempt_recovery(artifact, log):
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
