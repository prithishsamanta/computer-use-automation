"""Deterministic construction of a reusable Artifact from a *successful*
DiscoveryTrace (.CLAUDE/03_DISCOVERY_AND_REPLAY.md, "Discovery Mistakes":
"reusable artifact: cleaned successful workflow only... Do not replay
failed detours... Artifact construction should derive a validated minimal
successful path rather than simply serializing the entire model
conversation"; .CLAUDE/08 decision #6).

No LLM anywhere in this module. If a future caller wants a model's help
summarizing or naming a capability, that is a *proposal* -- e.g. a
suggested `name`/`description` string -- that must still pass through this
same deterministic construction and the unmodified `Artifact` pydantic
schema before it can be stored; nothing here lets a model emit artifact
JSON directly. `build()` accepts optional `name`/`description` overrides
for exactly that reason: a caller-supplied string (from a human, or a
model-assisted proposal a caller chose to trust) is still just a string
substituted into an otherwise fully deterministic build -- it cannot
affect steps, inputs, outputs, checkpoints, or safety metadata, all of
which come only from the trace and the goal.

Algorithm:

1. Reject anything that isn't a genuinely successful, well-formed trace
   (`_require_successful_trace`) -- a malformed/incomplete trace is never
   "cleaned up" into something plausible-looking; it's refused outright.
2. Keep only steps DiscoveryEngine itself recorded as cleanly executed
   (`outcome == "executed"`) -- this is what excludes every failed
   detour, malformed-output turn, and policy escalation from ever
   reaching the artifact. A trace's failed exploration stays in the trace
   only (`_select_successful_path`).
3. Collapse exact, adjacent duplicate actions conservatively -- only when
   the immediately preceding kept action has an identical signature
   (action_type/intent/target/value); never a broader "looks similar"
   heuristic, and never a non-adjacent repeat, which is real history, not
   redundancy (`_deduplicate_adjacent`).
4. Turn each declared `DiscoveryGoal.inputs` name into a typed `InputSpec`,
   and placeholder-ize any of its raw values still literally present in a
   kept action (`_build_inputs`, `_placeholderize`). Sensitive inputs
   already arrive placeholder-ized as `{{name}}` from DiscoveryEngine's
   own redaction (`cuas.observability.redaction.redact_named_values`);
   this catches any *non-sensitive* declared inputs too, since those are
   left as their raw literal value in the persisted trace on purpose (a
   trace should stay human-readable where nothing sensitive is at stake).
5. Any kept READ action becomes a declared, typed `OutputSpec` instead of
   a replay `Step` -- output extraction, per the existing Artifact
   convention (see `tests/fixtures/sample_artifacts.py`), happens once at
   the end of a replay via `OutputSpec.source`, not as an inline step. The
   type is inferred from what was actually read
   (`_build_outputs`/`_infer_output_type`), never guessed by an LLM.
6. `goal.success_checkpoint`, if declared, becomes the checkpoint on the
   last remaining (non-READ) step; `goal.known_business_outcomes` carry
   forward verbatim into `Artifact.business_outcomes`.
7. Every kept step's own `intent` carries forward unchanged, and `risk` is
   deliberately left unset on every constructed `Step` -- exactly as
   DiscoveryEngine leaves it unset on every proposed `Action` -- so
   `LayeredPolicyEngine`'s fail-closed behavior for an unclassified intent
   governs a replay of this artifact exactly the way it governed the
   original discovery run. `ArtifactSafety` summarizes the capability's
   overall intent/risk the same deterministic way (`_overall_intent`,
   `_overall_risk`), computed from *these* steps only, before the
   deterministic setup step below is added -- it is informational
   metadata only, never itself the enforcement authority
   (.CLAUDE/02_ARTIFACT_SCHEMA.md, "Safety Metadata").
8. Prepend one deterministic `NAVIGATE` step to `goal.start_url`
   (`_build_navigate_to_start_step`) -- not an LLM decision, and not part
   of the steps `_overall_intent`/`_overall_risk` summarize (point 7
   already ran). `DiscoveryEngine.run()` navigates to `goal.start_url`
   *before* recording a single trace step (engine.py: `await
   self._surface.navigate(goal.start_url)` precedes the step loop
   entirely), so the successful path this builder reconstructs from the
   trace is always missing that prerequisite -- without it, replaying
   this artifact from a fresh, unnavigated surface (exactly what
   `RunOrchestrator` hands every non-resumed run) would start on
   `about:blank`, the same defect class DECISIONS_LOG.md's
   `get_savings_balance` bugfix entry describes for a hand-authored
   artifact. `goal.success_checkpoint`, if declared, attaches to this
   step instead when it ends up the *only* step (a discovery whose
   entire successful path was a single READ has nothing else to attach
   it to).
9. Construct the `Artifact` pydantic model. Its own validators
   (`_step_ids_are_unique`, `_success_condition_references_a_declared_output`,
   `_version_is_semver`, every field's own type) are what actually gate
   whether this becomes storable -- this module does not duplicate that
   validation, it just makes sure it always runs: `Artifact(...)` raises
   `pydantic.ValidationError` immediately if anything is wrong (wrapped
   here as `ArtifactBuildError` for a single exception type this package's
   callers need to handle), and no partially-built or validation-bypassed
   artifact is ever possible.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError

from cuas.artifact.schema import (
    Artifact,
    ArtifactApplication,
    ArtifactSafety,
    InputSpec,
    OutputSpec,
    OutputType,
    Provenance,
    Step,
    SuccessCondition,
    SuccessConditionType,
)
from cuas.discovery.models import DiscoveryGoal
from cuas.discovery.trace import DiscoveryTrace, DiscoveryTraceStep
from cuas.domain import Action, ActionType, AppContext, RiskLevel
from cuas.observability.redaction import redact_named_values_json

_INTENT_PREFIXES = ("read_", "view_", "get_", "fetch_")


class ArtifactBuildError(Exception):
    """A trace cannot become an artifact. Distinct from
    `ArtifactInvalidError` (`domain/errors.py`), which is for an
    already-constructed artifact failing storage-time schema validation --
    this is raised earlier, for a trace that is structurally unable to
    even attempt construction (not successful, no executed steps, no
    output to hang a success condition on, or a kept action too malformed
    to reconstruct), and for the schema-validation failure this module
    wraps so callers only need to handle one exception type."""


class ArtifactBuilder:
    """Stateless and deterministic -- safe to reuse across builds, holds
    no per-run state."""

    def build(
        self,
        trace: DiscoveryTrace,
        goal: DiscoveryGoal,
        context: AppContext,
        *,
        capability_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        version: str = "1.0.0",
    ) -> Artifact:
        self._require_successful_trace(trace)

        kept_steps = self._select_successful_path(trace)
        if not kept_steps:
            raise ArtifactBuildError(
                f"trace {trace.run_id!r} has no executed actions; nothing to build an artifact from"
            )
        kept_steps = self._deduplicate_adjacent(kept_steps)

        inputs = self._build_inputs(goal)
        steps = self._build_steps(kept_steps, goal)
        outputs = self._build_outputs(kept_steps, goal)
        if not outputs:
            raise ArtifactBuildError(
                f"trace {trace.run_id!r} has no READ actions in its successful path; "
                "cannot derive a typed output or success condition"
            )

        success_condition = SuccessCondition(type=SuccessConditionType.OUTPUT_VALID, output=next(iter(outputs)))

        resolved_capability_id = capability_id or trace.capability_id
        # Computed from the model-driven steps only, before the
        # deterministic navigate-to-start step below is prepended -- see
        # module docstring point 8. That step was never an LLM decision;
        # it must not skew what this artifact's safety metadata says
        # about the capability's own intent/risk.
        safety = ArtifactSafety(intent=self._overall_intent(steps, outputs), risk=self._overall_risk())

        # Module docstring point 8 / _build_navigate_to_start_step's own
        # docstring: DiscoveryEngine navigates to goal.start_url before
        # recording any trace step, so the successful path reconstructed
        # above is always missing that prerequisite. `steps` is
        # guaranteed non-empty from here on, even when kept_steps' only
        # executed action was a READ (which _build_steps excludes
        # entirely -- see point 5).
        steps = [self._build_navigate_to_start_step(goal), *steps]
        if goal.success_checkpoint is not None:
            steps[-1] = steps[-1].model_copy(update={"checkpoint": goal.success_checkpoint})

        try:
            return Artifact(
                capability_id=resolved_capability_id,
                name=name or resolved_capability_id.replace("_", " ").title(),
                description=description or trace.goal_description,
                version=version,
                application=ArtifactApplication(
                    vendor=context.vendor, application=context.application, supported_versions=[context.version]
                ),
                inputs=inputs,
                outputs=outputs,
                steps=steps,
                success_condition=success_condition,
                business_outcomes=list(goal.known_business_outcomes),
                safety=safety,
                provenance=Provenance(created_from_run=trace.run_id, created_at=datetime.now(timezone.utc)),
            )
        except ValidationError as exc:
            raise ArtifactBuildError(f"constructed artifact failed schema validation: {exc}") from exc

    # -- trace validation -----------------------------------------------------

    def _require_successful_trace(self, trace: DiscoveryTrace) -> None:
        if trace.final_status != "success":
            raise ArtifactBuildError(
                f"trace {trace.run_id!r} did not end in success (final_status={trace.final_status!r}); "
                "only a successful discovery run can become an artifact"
            )
        if not trace.steps:
            raise ArtifactBuildError(f"trace {trace.run_id!r} has no recorded steps")

    # -- successful path selection ---------------------------------------------

    def _select_successful_path(self, trace: DiscoveryTrace) -> list[DiscoveryTraceStep]:
        """Only steps DiscoveryEngine itself recorded as cleanly executed
        survive -- everything else (a failed action fed back to the model,
        a step that only declared "done", a step that never got to
        execute because of a parse/policy problem) is exploration, not
        the reusable path."""

        return [step for step in trace.steps if step.outcome == "executed" and step.parsed_action is not None]

    def _deduplicate_adjacent(self, steps: list[DiscoveryTraceStep]) -> list[DiscoveryTraceStep]:
        """Conservative: only collapses a step that is an EXACT repeat of
        the immediately preceding kept step (same action_type/intent/
        target/value) -- never a fuzzy "looks similar" heuristic, and
        never a non-adjacent repeat (e.g. the same search performed twice
        with different results in between is real history, not
        redundancy)."""

        deduped: list[DiscoveryTraceStep] = []
        last_signature: tuple[Any, ...] | None = None
        for step in steps:
            signature = self._action_signature(step.parsed_action)
            if signature == last_signature:
                continue
            deduped.append(step)
            last_signature = signature
        return deduped

    def _action_signature(self, parsed_action: dict[str, Any]) -> tuple[Any, ...]:
        target = parsed_action.get("target")
        target_sig = None
        if target:
            primary = target.get("primary", {})
            target_sig = (
                primary.get("strategy"),
                tuple(sorted(primary.get("params", {}).items())),
                primary.get("frame"),
            )
        return (parsed_action.get("action_type"), parsed_action.get("intent"), target_sig, parsed_action.get("value"))

    # -- inputs / placeholders --------------------------------------------------

    def _build_inputs(self, goal: DiscoveryGoal) -> dict[str, InputSpec]:
        return {
            name: InputSpec(
                type="string",
                required=True,
                description=f"Input captured from the discovery goal that produced this artifact ({name!r}).",
                sensitive=name in goal.sensitive_inputs,
            )
            for name in goal.inputs
        }

    def _placeholderize(self, action_dict: dict[str, Any], goal: DiscoveryGoal) -> dict[str, Any]:
        return redact_named_values_json(action_dict, goal.inputs)

    def _to_action(self, action_dict: dict[str, Any], goal: DiscoveryGoal) -> Action:
        try:
            return Action.model_validate(self._placeholderize(action_dict, goal))
        except ValidationError as exc:
            raise ArtifactBuildError(f"a kept discovery step's action could not be reconstructed: {exc}") from exc

    # -- steps ------------------------------------------------------------------

    def _build_steps(self, kept_steps: list[DiscoveryTraceStep], goal: DiscoveryGoal) -> list[Step]:
        steps: list[Step] = []
        for index, trace_step in enumerate(kept_steps):
            if trace_step.parsed_action.get("action_type") == ActionType.READ.value:
                continue  # READ actions become OutputSpec.source, not a replay Step -- see _build_outputs
            action = self._to_action(trace_step.parsed_action, goal)
            steps.append(
                Step(
                    id=f"step-{index + 1}",
                    action_type=action.action_type,
                    intent=action.intent,
                    target=action.target,
                    value=action.value,
                    # `risk` deliberately left unset -- see module docstring, point 7.
                )
            )
        return steps

    def _build_navigate_to_start_step(self, goal: DiscoveryGoal) -> Step:
        """The one Step in a discovered artifact that is deterministic
        orchestration setup, not a recorded LLM decision -- see module
        docstring point 8 for why it must exist at all.

        `risk` is explicitly set to SAFE, unlike every other constructed
        Step (which deliberately leaves it unset -- point 7): this
        navigation was never itself a policy decision to begin with.
        DiscoveryEngine's own `await self._surface.navigate(goal.start_url)`
        runs before the step loop starts, completely unconditionally --
        no PolicyEngine.evaluate() call ever governed it during discovery
        (engine.py has none for it). Marking it SAFE here only makes
        replay trust it exactly as much as discovery already implicitly
        did, through LayeredPolicyEngine's ordinary risk-as-opinion path
        (safety/policy.py: an explicitly-set `risk` counts as one more
        opinion via `action.model_fields_set`) -- not a new bypass, and
        not a blanket ALLOW for some other, undeclared intent."""

        return Step(
            id="navigate_to_discovery_start",
            action_type=ActionType.NAVIGATE,
            intent="open_start_page",
            value=goal.start_url,
            risk=RiskLevel.SAFE,
        )

    # -- outputs ------------------------------------------------------------

    def _build_outputs(self, kept_steps: list[DiscoveryTraceStep], goal: DiscoveryGoal) -> dict[str, OutputSpec]:
        outputs: dict[str, OutputSpec] = {}
        for trace_step in kept_steps:
            if trace_step.parsed_action.get("action_type") != ActionType.READ.value:
                continue
            action = self._to_action(trace_step.parsed_action, goal)
            if action.target is None:
                continue
            name = self._output_name_from_intent(action.intent, existing=outputs)
            output_type = self._infer_output_type(trace_step.read_value or "")
            outputs[name] = OutputSpec(type=output_type, source=action.target, required=True)
        return outputs

    def _output_name_from_intent(self, intent: str, *, existing: dict[str, Any]) -> str:
        name = intent
        for prefix in _INTENT_PREFIXES:
            if name.startswith(prefix) and len(name) > len(prefix):
                name = name[len(prefix):]
                break
        base = name
        suffix = 2
        while name in existing:
            name = f"{base}_{suffix}"
            suffix += 1
        return name

    def _infer_output_type(self, raw: str) -> OutputType:
        text = raw.strip()
        if text.lower() in ("true", "false", "yes", "no"):
            return OutputType.BOOLEAN
        cleaned = text.lstrip("$").replace(",", "")
        try:
            Decimal(cleaned)
        except InvalidOperation:
            return OutputType.STRING
        return OutputType.INTEGER if "." not in cleaned else OutputType.DECIMAL

    # -- safety metadata -----------------------------------------------------

    def _overall_intent(self, steps: list[Step], outputs: dict[str, OutputSpec]) -> str:
        # The capability's own summarizing intent is the *last* action
        # taken -- the one that produces the capability's actual result --
        # whether that's the last replay Step or (when the very last kept
        # action was itself a READ, so it became an output instead of a
        # Step) that READ's own intent isn't tracked here, so fall back to
        # the last Step's intent; a capability with only READ actions and
        # no Steps at all falls back to a generic label rather than
        # guessing.
        if steps:
            return steps[-1].intent
        return next(reversed(outputs), "read_output")

    def _overall_risk(self) -> RiskLevel:
        # Every kept action already passed the real PolicyEngine during
        # discovery -- this module only ever sees the "executed" outcome,
        # never a denied/approval-required one -- so SAFE is the correct,
        # honest summary for a capability built entirely from actions that
        # were actually allowed to run unattended. This is still only
        # informational: the runtime PolicyEngine re-evaluates every step
        # again at replay time regardless of what this says
        # (.CLAUDE/02_ARTIFACT_SCHEMA.md, "Safety Metadata").
        return RiskLevel.SAFE
