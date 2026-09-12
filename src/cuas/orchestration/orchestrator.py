"""RunOrchestrator: the control-flow layer .CLAUDE/01_ARCHITECTURE.md's two
top-level flows describe --

    Existing capability:
        request -> resolve tenant/application context -> capability lookup
        -> retrieve compatible artifact -> deterministic replay
        -> extract outputs -> verify success condition -> structured result

    No existing capability:
        request -> capability lookup -> no acceptable match
        -> DISCOVERY_REQUIRED -> live LLM discovery -> ... -> persist capability

-- composed from the pieces every earlier phase already built:
`CapabilityService` (Phase 10) decides whether a compatible artifact
exists; `ReplayEngine` (Phase 5/7) deterministically executes one;
`DiscoveryEngine` (Phase 8) drives a live LLM-guided attempt when none
does; `ArtifactBuilder` (Phase 9) turns a *successful* discovery run into
a storable artifact; `ArtifactRepository`/`CapabilityService.register`
persist it. This module does not reimplement any of their algorithms --
it only decides, from each one's own structured result, what to do next
and how to translate the outcome into one stable `RunResult` shape.

Deliberately NOT here: replay's step loop, policy's SAFE/APPROVAL/BLOCKED
combination logic, discovery's observe-propose-execute loop, or
capability's compatibility/specificity resolution. Each stays exactly
where it already lived; this module only calls into it.

No Celery/RabbitMQ/Kafka: `run_capability` is a single direct async call
that runs synchronously to completion (its own await chain -- capability
lookup, then either one replay or one discovery-then-replay pass -- is
already fully async I/O, which is all "direct/synchronous orchestration"
ever meant here). A caller wanting a background job queue on top of this
is free to add one outside this module; nothing about this design
requires it.

**Known, explicitly-flagged limitation carried over from this phase's own
scope, not a silent gap:** `.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md` is
explicit that creating an `InterventionRequest` must NOT terminate the
live browser session -- "Do not terminate the browser session when
intervention is created... control transfer of the SAME live session."
This module's `surface_factory` context manager closes its surface
(`async with self._surface_factory() as surface: ...`) before an
intervention record is even created, because Phase 11 has no session
registry to hand a still-open surface off to across the request/response
boundary in the first place. `InterventionRequest.session_id` is
therefore always `None` here (see handoff/models.py's docstring). Keeping
a live session paused and resumable is precisely what Phase 12
("Intervention / human-handoff persistence + async resume") exists to
add; this phase creates the correct escalation *record* (run_id,
capability_id, tenant_id, current_step, reason, status=PENDING) through
the real interface, deliberately deferring only the session-preservation
mechanism itself.
"""

from __future__ import annotations

import uuid
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from typing import Any, Callable

from cuas.artifact import Artifact, ArtifactRepository
from cuas.artifact_builder import ArtifactBuildError, ArtifactBuilder
from cuas.capability import CapabilityRecord, CapabilityResolutionStatus, CapabilityService, CapabilityStatus
from cuas.discovery import (
    DiscoveryEngine,
    DiscoveryGoal,
    DiscoveryLimits,
    DiscoveryStatus,
    DiscoveryTraceStore,
    LLMClient,
)
from cuas.domain import AppContext, InterventionStatus
from cuas.handoff import InterventionRepository, InterventionRequest
from cuas.observability.event_sink import EventSink, NullEventSink
from cuas.observability.events import EventType, RunEvent
from cuas.observability.evidence import EvidenceStore, NullEvidenceStore
from cuas.orchestration.models import RunOutcome, RunResult
from cuas.replay import ReplayEngine, ReplayStatus
from cuas.safety import PolicyEngine
from cuas.surface.adapter import SurfaceAdapter

# A zero-arg callable returning an async context manager that yields one
# ready-to-use SurfaceAdapter and tears it down on exit -- exactly the
# shape `cuas.surface.playwright_adapter.launch_playwright_surface`
# already has (see tests/integration/test_artifact_builder_e2e.py's
# `async with launch_playwright_surface() as surface:`), so a real caller
# just passes that function in unchanged. RunOrchestrator calls this once
# per fresh surface it needs -- never more than twice in one
# `run_capability` call (once for a plain replay or a discovery attempt;
# a second time only for the replay that immediately follows a *newly
# successful* discovery -- see `_materialize_capability`).
SurfaceFactory = Callable[[], AbstractAsyncContextManager[SurfaceAdapter]]


class RunOrchestrator:
    def __init__(
        self,
        capability_service: CapabilityService,
        artifact_repository: ArtifactRepository,
        policy: PolicyEngine,
        surface_factory: SurfaceFactory,
        intervention_repository: InterventionRepository,
        trace_store: DiscoveryTraceStore,
        *,
        llm: LLMClient | None = None,
        artifact_builder: ArtifactBuilder | None = None,
        discovery_limits: DiscoveryLimits | None = None,
        event_sink: EventSink | None = None,
        evidence_store: EvidenceStore | None = None,
    ) -> None:
        self._capabilities = capability_service
        self._artifacts = artifact_repository
        self._policy = policy
        self._surface_factory = surface_factory
        self._interventions = intervention_repository
        self._trace_store = trace_store
        # LLMClient is optional: an orchestrator wired up without one (no
        # ANTHROPIC_API_KEY configured, say) can still serve every request
        # a known capability already covers -- it just cannot attempt
        # discovery for a genuinely new one, and says so plainly instead
        # of crashing (see `run_capability`).
        self._llm = llm
        self._artifact_builder = artifact_builder or ArtifactBuilder()
        self._discovery_limits = discovery_limits
        self._event_sink = event_sink or NullEventSink()
        self._evidence_store = evidence_store or NullEvidenceStore()

    async def run_capability(
        self,
        capability_id: str,
        inputs: dict[str, Any],
        context: AppContext,
        *,
        discovery_goal: DiscoveryGoal | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        """`discovery_goal` is how a caller opts a request into on-demand
        discovery when no compatible capability exists yet: this
        orchestrator has no way to invent a goal's `start_url`/
        `description`/`success_checkpoint` on its own, so a request with
        no compatible capability and no supplied goal can only report
        `DISCOVERY_REQUIRED` and stop -- it is the caller's decision
        whether/how to supply one (e.g. from an admin-configured capability
        definition, or a human operator describing a brand-new workflow),
        not something this orchestrator guesses at.
        """

        run_id = run_id or uuid.uuid4().hex

        self._emit(run_id, EventType.CAPABILITY_SEARCH_STARTED, details={"capability_id": capability_id})
        resolution = self._capabilities.resolve(capability_id, context)

        if resolution.status == CapabilityResolutionStatus.RESOLVED:
            assert resolution.artifact is not None and resolution.matched_record is not None
            self._emit(
                run_id,
                EventType.CAPABILITY_MATCH_FOUND,
                details={"artifact_version": resolution.artifact.version, "tenant_scope": resolution.matched_record.tenant_scope},
            )
            result = await self._run_replay(resolution.artifact, inputs, context, run_id=run_id, capability_id=capability_id)
            return self._finish(result)

        if resolution.status == CapabilityResolutionStatus.AMBIGUOUS:
            result = RunResult(
                run_id=run_id, capability_id=capability_id, outcome=RunOutcome.AMBIGUOUS_CAPABILITY, reason=resolution.reason
            )
            return self._finish(result)

        # NO_CAPABILITY_MATCH -- .CLAUDE/06's own "Capability Routing
        # Outcomes": not a replay failure, normal control flow.
        self._emit(run_id, EventType.DISCOVERY_REQUIRED, details={"reason": resolution.reason})

        if discovery_goal is None:
            result = RunResult(
                run_id=run_id, capability_id=capability_id, outcome=RunOutcome.DISCOVERY_REQUIRED, reason=resolution.reason
            )
            return self._finish(result)

        if self._llm is None:
            result = RunResult(
                run_id=run_id,
                capability_id=capability_id,
                outcome=RunOutcome.DISCOVERY_REQUIRED,
                reason="no compatible capability, and this orchestrator has no LLM client configured "
                "to attempt discovery",
            )
            return self._finish(result)

        result = await self._run_discovery(capability_id, discovery_goal, inputs, context, run_id=run_id)
        return self._finish(result)

    # -- capability found: deterministic replay --------------------------

    async def _run_replay(
        self,
        artifact: Artifact,
        inputs: dict[str, Any],
        context: AppContext,
        *,
        run_id: str,
        capability_id: str,
        discovered_new_capability: bool = False,
    ) -> RunResult:
        async with self._surface_factory() as surface:
            engine = ReplayEngine(surface, self._policy, event_sink=self._event_sink, evidence_store=self._evidence_store)
            replay_result = await engine.run(artifact, inputs, context, run_id=run_id)

        common = dict(
            run_id=run_id,
            capability_id=capability_id,
            artifact_version=artifact.version,
            discovered_new_capability=discovered_new_capability,
        )

        if replay_result.status == ReplayStatus.SUCCESS:
            return RunResult(**common, outcome=RunOutcome.SUCCESS, outputs=replay_result.outputs)

        if replay_result.status == ReplayStatus.BUSINESS_OUTCOME:
            return RunResult(
                **common, outcome=RunOutcome.BUSINESS_OUTCOME, business_outcome_code=replay_result.business_outcome_code
            )

        if replay_result.status == ReplayStatus.BLOCKED:
            # A policy DENY is a terminal refusal, not something an
            # operator can approve past -- .CLAUDE/04: "Do not execute.
            # Log the policy decision and terminate." Nothing for a human
            # to act on, so (deliberately, see module docstring) no
            # intervention is created for this branch; APPROVAL_REQUIRED
            # and FAILED below are the two escalation triggers this
            # phase's instructions actually name ("policy approval
            # requirement / unresolved failure").
            return RunResult(**common, outcome=RunOutcome.BLOCKED, reason=replay_result.escalation_reason)

        if replay_result.status == ReplayStatus.APPROVAL_REQUIRED:
            intervention_id = self._create_intervention(
                run_id=run_id,
                capability_id=capability_id,
                context=context,
                reason=replay_result.escalation_reason or "operator approval required",
                current_step=replay_result.escalation_step_id,
            )
            return RunResult(
                **common, outcome=RunOutcome.APPROVAL_REQUIRED, reason=replay_result.escalation_reason, intervention_id=intervention_id
            )

        # ReplayStatus.FAILED -- an unresolved hard failure.
        intervention_id = self._create_intervention(
            run_id=run_id,
            capability_id=capability_id,
            context=context,
            reason=replay_result.error_message or "replay failed",
            current_step=None,
        )
        return RunResult(
            **common,
            outcome=RunOutcome.FAILED,
            error_code=replay_result.error_code,
            error_message=replay_result.error_message,
            intervention_id=intervention_id,
        )

    # -- no capability found: live discovery ------------------------------

    async def _run_discovery(
        self,
        capability_id: str,
        goal: DiscoveryGoal,
        inputs: dict[str, Any],
        context: AppContext,
        *,
        run_id: str,
    ) -> RunResult:
        assert self._llm is not None  # caller (run_capability) already checked
        self._emit(run_id, EventType.DISCOVERY_STARTED, details={"capability_id": capability_id})

        async with self._surface_factory() as surface:
            engine = DiscoveryEngine(
                surface,
                self._policy,
                self._llm,
                event_sink=self._event_sink,
                evidence_store=self._evidence_store,
                trace_store=self._trace_store,
            )
            discovery_result = await engine.run(goal, context, self._discovery_limits, run_id=run_id)

        if discovery_result.status == DiscoveryStatus.SUCCESS:
            return await self._materialize_capability(capability_id, goal, inputs, context, run_id=run_id)

        if discovery_result.status == DiscoveryStatus.BUSINESS_OUTCOME:
            return RunResult(
                run_id=run_id,
                capability_id=capability_id,
                outcome=RunOutcome.BUSINESS_OUTCOME,
                business_outcome_code=discovery_result.business_outcome_code,
            )

        if discovery_result.status == DiscoveryStatus.BLOCKED:
            # Same rationale as the replay branch above: a terminal denial,
            # not an approval an operator can grant, so no intervention.
            return RunResult(
                run_id=run_id, capability_id=capability_id, outcome=RunOutcome.BLOCKED, reason=discovery_result.escalation_reason
            )

        if discovery_result.status == DiscoveryStatus.APPROVAL_REQUIRED:
            intervention_id = self._create_intervention(
                run_id=run_id,
                capability_id=capability_id,
                context=context,
                reason=discovery_result.escalation_reason or "operator approval required during discovery",
                current_step=(
                    str(discovery_result.escalation_step_index) if discovery_result.escalation_step_index is not None else None
                ),
            )
            return RunResult(
                run_id=run_id,
                capability_id=capability_id,
                outcome=RunOutcome.APPROVAL_REQUIRED,
                reason=discovery_result.escalation_reason,
                intervention_id=intervention_id,
            )

        # Every remaining terminal DiscoveryStatus (MALFORMED_MODEL_OUTPUT,
        # LOOP_DETECTED, MAX_STEPS_EXCEEDED, MAX_DURATION_EXCEEDED,
        # MAX_TOKENS_EXCEEDED, FAILED) is an unresolved failure of the
        # discovery attempt itself -- exactly .CLAUDE/04's "the discovery
        # model loops / becomes stuck" and "bounded safe recovery is
        # exhausted" escalation triggers.
        intervention_id = self._create_intervention(
            run_id=run_id,
            capability_id=capability_id,
            context=context,
            reason=discovery_result.error_message or f"discovery ended with status {discovery_result.status.value}",
            current_step=str(discovery_result.steps_taken),
        )
        return RunResult(
            run_id=run_id,
            capability_id=capability_id,
            outcome=RunOutcome.FAILED,
            error_message=discovery_result.error_message,
            intervention_id=intervention_id,
        )

    # -- successful discovery: build, store, register, then fulfil the request --

    async def _materialize_capability(
        self,
        capability_id: str,
        goal: DiscoveryGoal,
        inputs: dict[str, Any],
        context: AppContext,
        *,
        run_id: str,
    ) -> RunResult:
        """A successful `DiscoveryResult` alone is not enough to answer the
        original request: it proves the goal was accomplished, but neither
        it nor its redacted `history` carries typed, artifact-declared
        outputs (see discovery/models.py -- `read_value` is a raw string,
        and naming/typing an output is `ArtifactBuilder`'s job, not
        discovery's). Rather than duplicate that output-typing logic here
        (exactly what this phase's instructions say not to do), this
        method builds and stores the real artifact, registers the
        capability so every *future* request skips discovery entirely,
        and then fulfils *this* request the same way a future one would:
        by handing the freshly-resolved artifact to `_run_replay` against
        a second fresh surface. The one deliberate cost of this choice is
        a second live session for a newly-discovered capability's first
        invocation only; the benefit is that this request's answer and
        every subsequent one are produced by the exact same, already-
        proven replay code path, with no separate output-extraction logic
        to keep in sync.
        """

        try:
            trace = self._trace_store.load(run_id)
        except FileNotFoundError as exc:
            intervention_id = self._create_intervention(
                run_id=run_id,
                capability_id=capability_id,
                context=context,
                reason=f"discovery succeeded but its trace could not be loaded for artifact construction: {exc}",
                current_step=None,
            )
            return RunResult(
                run_id=run_id, capability_id=capability_id, outcome=RunOutcome.FAILED, error_message=str(exc), intervention_id=intervention_id
            )

        version = self._next_version(context.vendor, context.application, capability_id)

        try:
            artifact = self._artifact_builder.build(trace, goal, context, capability_id=capability_id, version=version)
        except ArtifactBuildError as exc:
            intervention_id = self._create_intervention(
                run_id=run_id,
                capability_id=capability_id,
                context=context,
                reason=f"discovery succeeded but could not be turned into a reusable artifact: {exc}",
                current_step=None,
            )
            return RunResult(
                run_id=run_id, capability_id=capability_id, outcome=RunOutcome.FAILED, error_message=str(exc), intervention_id=intervention_id
            )

        self._artifacts.save(artifact)
        # Registered scoped to exactly this tenant (not the "base" sentinel
        # that would make it eligible for every tenant of this vendor/
        # application): discovery only ever demonstrated the workflow
        # against this one tenant's context. Broadening a newly-learned
        # capability to every tenant is a separate, deliberate operator
        # decision (.CLAUDE/08 decision #9 prefers overrides/narrow scope
        # over assuming reuse), not something a single successful
        # discovery run should assume on its own.
        self._capabilities.register(
            CapabilityRecord(
                capability_id=capability_id,
                name=artifact.name,
                description=artifact.description,
                vendor=context.vendor,
                application=context.application,
                supported_versions=[context.version],
                tenant_scope=context.tenant_id,
                artifact_version=version,
                status=CapabilityStatus.ACTIVE,
            )
        )
        self._emit(
            run_id,
            EventType.CAPABILITY_MATCH_FOUND,
            details={"newly_discovered": True, "artifact_version": version, "tenant_scope": context.tenant_id},
        )

        return await self._run_replay(
            artifact, inputs, context, run_id=run_id, capability_id=capability_id, discovered_new_capability=True
        )

    def _next_version(self, vendor: str, application: str, capability_id: str) -> str:
        """Monotonic patch bump so re-discovering an already-known
        capability (e.g. after it was disabled, or the demo app changed
        slightly) never collides with `FileArtifactRepository.save`'s
        refuse-to-overwrite-a-version guarantee. `list_versions` returns
        versions sorted ascending (artifact/repository.py), so the last
        entry is always the highest."""

        existing = self._artifacts.list_versions(vendor, application, capability_id)
        if not existing:
            return "1.0.0"
        major, minor, patch = (int(part) for part in existing[-1].split("."))
        return f"{major}.{minor}.{patch + 1}"

    # -- intervention creation ---------------------------------------------

    def _create_intervention(
        self,
        *,
        run_id: str,
        capability_id: str,
        context: AppContext,
        reason: str,
        current_step: str | None,
    ) -> str:
        intervention_id = uuid.uuid4().hex
        request = InterventionRequest(
            id=intervention_id,
            run_id=run_id,
            capability_id=capability_id,
            tenant_id=context.tenant_id,
            current_step=current_step,
            reason=reason,
            status=InterventionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        self._interventions.save(request)
        self._emit(
            run_id,
            EventType.INTERVENTION_REQUESTED,
            step_id=current_step,
            status="escalation",
            details={"intervention_id": intervention_id, "reason": reason},
        )
        return intervention_id

    # -- observability -------------------------------------------------------

    def _emit(
        self, run_id: str, event: EventType, *, step_id: str | None = None, status: str = "info", details: dict[str, Any] | None = None
    ) -> None:
        self._event_sink.record(
            RunEvent(run_id=run_id, component="run_orchestrator", event=event, step_id=step_id, status=status, details=details or {})
        )

    def _finish(self, result: RunResult) -> RunResult:
        self._emit(
            result.run_id,
            EventType.RUN_COMPLETED,
            status=result.outcome.value,
            details={
                "outcome": result.outcome.value,
                "capability_id": result.capability_id,
                "discovered_new_capability": result.discovered_new_capability,
            },
        )
        return result
