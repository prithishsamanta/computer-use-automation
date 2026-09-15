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
exists; `ReplayEngine` (Phase 5/7/12) deterministically executes one, and
can resume one already partway through; `DiscoveryEngine` (Phase 8) drives
a live LLM-guided attempt when none does; `ArtifactBuilder` (Phase 9)
turns a *successful* discovery run into a storable artifact;
`ArtifactRepository`/`CapabilityService.register` persist it;
`SessionRegistry` (Phase 12) keeps one paused run's live surface open
across the human-handoff boundary. This module does not reimplement any
of their algorithms -- it only decides, from each one's own structured
result, what to do next and how to translate the outcome into one stable
`RunResult` shape.

Deliberately NOT here: replay's step loop, policy's SAFE/APPROVAL/BLOCKED
combination logic, discovery's observe-propose-execute loop, capability's
compatibility/specificity resolution, or the human-handoff *state
machine*'s own transition rules (those live on `InterventionStatus`/
`ControlState` and are only ever set here, never re-derived). Each stays
exactly where it already lived; this module only calls into it.

No Celery/RabbitMQ/Kafka: every method here (`run_capability`,
`claim_intervention`, `mark_human_control_complete`, `resume_run`) is a
single direct call that runs synchronously to completion. The
*asynchronous* part Phase 12 introduces -- real-world time passing while
an operator is paged, looks at the queue, and eventually acts -- is
modeled entirely by persisted state (`InterventionRequest` rows an
operator's own client polls or is notified about some other way, outside
this system's scope) plus one in-process `SessionRegistry` holding the
paused browser open in the meantime; it is not a message broker, a
background worker, or a long-running task of any kind. Nothing sits
"running" between a pause and its eventual `resume_run` call except an
idle Playwright browser process.

**Phase 11's explicitly-flagged limitation, now closed:** Phase 11's
`async with self._surface_factory() as surface: ...` closed the surface
before an `InterventionRequest` could even be created, so
`session_id` was always `None`. Phase 12 replaces that pattern with a
manually-managed context manager (`cm = self._surface_factory(); surface
= await cm.__aenter__()`) that is *not* exited when a replay/discovery
attempt escalates -- instead the still-open `(surface, cm)` pair is
registered in `SessionRegistry` under a fresh `session_id`, control state
`PAUSED_WAITING_FOR_HUMAN`, and that id is what `InterventionRequest.
session_id` now actually names. The surface is only closed (`cm.
__aexit__`) once a run reaches a genuinely terminal outcome -- see
`_close_surface`, and the docstrings on `_run_replay`/`_run_discovery`/
`resume_run`/`cancel_intervention` for exactly where each transition
happens. `cuas.handoff.session`'s own module docstring explains the one
real limitation this still carries: a live session cannot survive this
process restarting, only a persisted `InterventionRequest` can.
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
    DiscoveryPendingApproval,
    DiscoveryStatus,
    DiscoveryTraceStore,
    LLMClient,
)
from cuas.domain import AppContext, ControlState, InterventionStatus
from cuas.handoff import (
    AutomationSession,
    InterventionOwnershipError,
    InterventionRepository,
    InterventionRequest,
    InterventionStateError,
    SessionRegistry,
)
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
# just passes that function in unchanged. RunOrchestrator calls this at
# most twice in one *unescalated* `run_capability` call (once for a plain
# replay/discovery attempt, and again only for the replay that immediately
# follows a *newly successful* discovery); an escalation followed by
# `resume_run` never calls it again for that run -- it reuses the surface
# already held open in `SessionRegistry` (see module docstring).
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
        session_registry: SessionRegistry | None = None,
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
        # Defaults to a fresh, empty registry -- fine for the common case
        # (one orchestrator instance, one process, e.g. api/main.py's
        # composition root). A caller only needs to pass its own instance
        # in when a test wants to inspect paused sessions directly (see
        # tests/unit/test_intervention_lifecycle.py) or, hypothetically,
        # share one registry across orchestrator instances.
        self._sessions = session_registry if session_registry is not None else SessionRegistry()

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
        resume_session_id: str | None = None,
    ) -> RunResult:
        """Runs (or resumes) one replay attempt.

        `resume_session_id` is `None` for a brand-new attempt (the common
        case: `run_capability` calling this directly, or
        `_materialize_capability`'s post-discovery verification replay) --
        a fresh surface is acquired from `surface_factory` and, on a
        terminal outcome, closed again before returning. When it names an
        existing `AutomationSession` (only `resume_run` passes this), the
        session's own already-open surface is reused instead -- `
        surface_factory` is not called at all, satisfying .CLAUDE/04's "do
        not terminate the browser session" / "do not create a new browser
        session for the operator" -- and `ReplayEngine.run` is told exactly
        where to resume via `session.resume_step_id`.

        Either way, an escalating result (APPROVAL_REQUIRED/FAILED) does
        NOT close the surface: it registers (or updates) an
        `AutomationSession` and creates a new `InterventionRequest`
        pointing at it, leaving the browser open for the next claim/
        resume cycle. Every other outcome (SUCCESS/BUSINESS_OUTCOME/
        BLOCKED) is terminal and always closes the surface.
        """

        if resume_session_id is not None:
            session = self._sessions.get(resume_session_id)
            surface = session.surface
            cm: AbstractAsyncContextManager[SurfaceAdapter] | None = None
            resume_from_step_id = session.resume_step_id
        else:
            cm = self._surface_factory()
            surface = await cm.__aenter__()
            resume_from_step_id = None

        engine = ReplayEngine(surface, self._policy, event_sink=self._event_sink, evidence_store=self._evidence_store)
        try:
            replay_result = await engine.run(
                artifact, inputs, context, run_id=run_id, resume_from_step_id=resume_from_step_id
            )
        except Exception:
            await self._close_surface(cm, resume_session_id)
            raise

        common = dict(
            run_id=run_id,
            capability_id=capability_id,
            artifact_version=artifact.version,
            discovered_new_capability=discovered_new_capability,
        )

        if replay_result.status == ReplayStatus.SUCCESS:
            await self._close_surface(cm, resume_session_id)
            return RunResult(**common, outcome=RunOutcome.SUCCESS, outputs=replay_result.outputs)

        if replay_result.status == ReplayStatus.BUSINESS_OUTCOME:
            await self._close_surface(cm, resume_session_id)
            return RunResult(
                **common, outcome=RunOutcome.BUSINESS_OUTCOME, business_outcome_code=replay_result.business_outcome_code
            )

        if replay_result.status == ReplayStatus.BLOCKED:
            # A policy DENY is a terminal refusal, not something an
            # operator can approve past -- .CLAUDE/04: "Do not execute.
            # Log the policy decision and terminate." Nothing for a human
            # to act on, so (deliberately, see module docstring) no
            # intervention is created for this branch, and the surface is
            # closed immediately -- there is nothing left to hand off.
            await self._close_surface(cm, resume_session_id)
            return RunResult(**common, outcome=RunOutcome.BLOCKED, reason=replay_result.escalation_reason)

        # APPROVAL_REQUIRED or FAILED: pause. Keep the surface open (in a
        # new AutomationSession, or updated in place if this attempt was
        # itself a resume that escalated again) and create a fresh
        # InterventionRequest naming it.
        session_id = resume_session_id or uuid.uuid4().hex
        is_approval = replay_result.status == ReplayStatus.APPROVAL_REQUIRED
        # FAILED with no escalation_step_id means the failure happened
        # after every step ran (output extraction / success-condition
        # verification) -- resume there via ReplayEngine's dedicated
        # sentinel rather than re-running the whole artifact.
        resume_step_id = replay_result.escalation_step_id if is_approval else (
            replay_result.escalation_step_id or ReplayEngine.RESUME_AFTER_ALL_STEPS
        )

        if cm is not None:
            self._sessions.register(
                AutomationSession(
                    session_id=session_id,
                    run_id=run_id,
                    capability_id=capability_id,
                    context=context,
                    inputs=inputs,
                    origin="replay",
                    surface=surface,
                    surface_cm=cm,
                    control_state=ControlState.PAUSED_WAITING_FOR_HUMAN,
                    discovered_new_capability=discovered_new_capability,
                    artifact=artifact,
                    resume_step_id=resume_step_id,
                )
            )
        else:
            session.control_state = ControlState.PAUSED_WAITING_FOR_HUMAN
            session.resume_step_id = resume_step_id
            session.discovered_new_capability = discovered_new_capability

        reason = replay_result.escalation_reason if is_approval else (replay_result.error_message or "replay failed")
        intervention_id = self._create_intervention(
            run_id=run_id,
            capability_id=capability_id,
            context=context,
            reason=reason,
            current_step=replay_result.escalation_step_id,
            session_id=session_id,
        )

        if is_approval:
            return RunResult(
                **common,
                outcome=RunOutcome.APPROVAL_REQUIRED,
                reason=replay_result.escalation_reason,
                intervention_id=intervention_id,
                session_id=session_id,
            )
        return RunResult(
            **common,
            outcome=RunOutcome.FAILED,
            error_code=replay_result.error_code,
            error_message=replay_result.error_message,
            intervention_id=intervention_id,
            session_id=session_id,
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
        resume_session_id: str | None = None,
        discovery_resume: DiscoveryPendingApproval | None = None,
    ) -> RunResult:
        """Runs (or resumes) one discovery attempt, mirroring
        `_run_replay`'s surface-acquisition/pause/resume shape.

        Resuming a paused discovery that has no pending approval
        (`discovery_resume is None` -- every DiscoveryStatus other than
        APPROVAL_REQUIRED) is a deliberately simpler model than resuming a
        paused *replay*: `DiscoveryEngine` has no notion of resuming an
        LLM conversation from a specific mid-loop point (that would mean
        serializing and replaying model context, a much larger feature
        this phase does not attempt). Instead, `resume_run` gives the LLM
        a fresh `DiscoveryEngine.run()` call -- a new reasoning attempt
        from scratch -- but on the exact same, still-open surface, so
        whatever the operator did while in HUMAN_CONTROL (dismissed a
        blocking dialog, navigated past a broken page, manually satisfied
        a captcha) is reflected in what the model observes next. This is
        an explicit simplification, not an oversight -- see
        DECISIONS_LOG.md's Phase 12 entry.

        When the pause WAS an APPROVAL_REQUIRED escalation, `resume_run`
        instead passes `session.pending_discovery_action` here as
        `discovery_resume`, and `DiscoveryEngine.run(resume=...)` executes
        that exact operator-approved action directly rather than asking
        the model to re-propose it -- see DiscoveryPendingApproval's
        docstring and DECISIONS_LOG.md's later entry closing this gap.
        """

        assert self._llm is not None  # caller (run_capability/resume_run) already checked
        self._emit(
            run_id, EventType.DISCOVERY_STARTED, details={"capability_id": capability_id, "resumed": resume_session_id is not None}
        )

        if resume_session_id is not None:
            session = self._sessions.get(resume_session_id)
            surface = session.surface
            cm: AbstractAsyncContextManager[SurfaceAdapter] | None = None
        else:
            cm = self._surface_factory()
            surface = await cm.__aenter__()

        engine = DiscoveryEngine(
            surface,
            self._policy,
            self._llm,
            event_sink=self._event_sink,
            evidence_store=self._evidence_store,
            trace_store=self._trace_store,
        )
        try:
            discovery_result = await engine.run(
                goal, context, self._discovery_limits, run_id=run_id, resume=discovery_resume
            )
        except Exception:
            await self._close_surface(cm, resume_session_id)
            raise

        if discovery_result.status == DiscoveryStatus.SUCCESS:
            return await self._materialize_capability(
                capability_id, goal, inputs, context, run_id=run_id, discovery_cm=cm, discovery_session_id=resume_session_id
            )

        if discovery_result.status == DiscoveryStatus.BUSINESS_OUTCOME:
            await self._close_surface(cm, resume_session_id)
            return RunResult(
                run_id=run_id,
                capability_id=capability_id,
                outcome=RunOutcome.BUSINESS_OUTCOME,
                business_outcome_code=discovery_result.business_outcome_code,
            )

        if discovery_result.status == DiscoveryStatus.BLOCKED:
            # Same rationale as the replay branch above: a terminal denial,
            # not an approval an operator can grant, so no intervention
            # and the surface closes immediately.
            await self._close_surface(cm, resume_session_id)
            return RunResult(
                run_id=run_id, capability_id=capability_id, outcome=RunOutcome.BLOCKED, reason=discovery_result.escalation_reason
            )

        # Every remaining terminal DiscoveryStatus (APPROVAL_REQUIRED,
        # MALFORMED_MODEL_OUTPUT, LOOP_DETECTED, MAX_STEPS_EXCEEDED,
        # MAX_DURATION_EXCEEDED, MAX_TOKENS_EXCEEDED, FAILED) pauses the
        # session for a human, exactly like the replay branch.
        session_id = resume_session_id or uuid.uuid4().hex
        if cm is not None:
            self._sessions.register(
                AutomationSession(
                    session_id=session_id,
                    run_id=run_id,
                    capability_id=capability_id,
                    context=context,
                    inputs=inputs,
                    origin="discovery",
                    surface=surface,
                    surface_cm=cm,
                    control_state=ControlState.PAUSED_WAITING_FOR_HUMAN,
                    discovery_goal=goal,
                    pending_discovery_action=discovery_result.pending_approval,
                )
            )
        else:
            session.control_state = ControlState.PAUSED_WAITING_FOR_HUMAN
            session.pending_discovery_action = discovery_result.pending_approval

        if discovery_result.status == DiscoveryStatus.APPROVAL_REQUIRED:
            intervention_id = self._create_intervention(
                run_id=run_id,
                capability_id=capability_id,
                context=context,
                reason=discovery_result.escalation_reason or "operator approval required during discovery",
                current_step=(
                    str(discovery_result.escalation_step_index) if discovery_result.escalation_step_index is not None else None
                ),
                session_id=session_id,
            )
            return RunResult(
                run_id=run_id,
                capability_id=capability_id,
                outcome=RunOutcome.APPROVAL_REQUIRED,
                reason=discovery_result.escalation_reason,
                intervention_id=intervention_id,
                session_id=session_id,
            )

        intervention_id = self._create_intervention(
            run_id=run_id,
            capability_id=capability_id,
            context=context,
            reason=discovery_result.error_message or f"discovery ended with status {discovery_result.status.value}",
            current_step=str(discovery_result.steps_taken),
            session_id=session_id,
        )
        return RunResult(
            run_id=run_id,
            capability_id=capability_id,
            outcome=RunOutcome.FAILED,
            error_message=discovery_result.error_message,
            intervention_id=intervention_id,
            session_id=session_id,
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
        discovery_cm: AbstractAsyncContextManager[SurfaceAdapter] | None,
        discovery_session_id: str | None,
    ) -> RunResult:
        """A successful `DiscoveryResult` alone is not enough to answer the
        original request: it proves the goal was accomplished, but neither
        it nor its redacted `history` carries typed, artifact-declared
        outputs (see discovery/models.py -- `read_value` is a raw string,
        and naming/typing an output is `ArtifactBuilder`'s job, not
        discovery's). Rather than duplicate that output-typing logic here
        (exactly what earlier phases' instructions say not to do), this
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

        `discovery_cm`/`discovery_session_id` name whatever surface
        discovery itself just finished with (a fresh one, or a resumed
        `AutomationSession`'s). Once the discovery trace is safely loaded
        from disk, that surface is done being useful -- everything from
        here on is artifact construction plus a *separate* verification
        replay against its own fresh surface -- so it is always closed
        before this method returns, on every path (including the two
        failure paths below), never left paused for an operator: neither
        of those failures is something a human can fix by touching the
        browser (they're about the trace file / artifact schema, not
        about the live page).
        """

        try:
            trace = self._trace_store.load(run_id)
        except FileNotFoundError as exc:
            await self._close_surface(discovery_cm, discovery_session_id)
            intervention_id = self._create_intervention(
                run_id=run_id,
                capability_id=capability_id,
                context=context,
                reason=f"discovery succeeded but its trace could not be loaded for artifact construction: {exc}",
                current_step=None,
                session_id=None,
            )
            return RunResult(
                run_id=run_id, capability_id=capability_id, outcome=RunOutcome.FAILED, error_message=str(exc), intervention_id=intervention_id
            )

        version = self._next_version(context.vendor, context.application, capability_id)

        try:
            artifact = self._artifact_builder.build(trace, goal, context, capability_id=capability_id, version=version)
        except ArtifactBuildError as exc:
            await self._close_surface(discovery_cm, discovery_session_id)
            intervention_id = self._create_intervention(
                run_id=run_id,
                capability_id=capability_id,
                context=context,
                reason=f"discovery succeeded but could not be turned into a reusable artifact: {exc}",
                current_step=None,
                session_id=None,
            )
            return RunResult(
                run_id=run_id, capability_id=capability_id, outcome=RunOutcome.FAILED, error_message=str(exc), intervention_id=intervention_id
            )

        await self._close_surface(discovery_cm, discovery_session_id)

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

    # -- surface lifecycle ---------------------------------------------------

    async def _close_surface(
        self, cm: AbstractAsyncContextManager[SurfaceAdapter] | None, session_id: str | None
    ) -> None:
        """The single place a surface's teardown actually happens, called
        from every terminal branch of `_run_replay`/`_run_discovery`/
        `_materialize_capability`, plus `cancel_intervention`.

        Exactly one of `cm`/`session_id` is meaningful for a given call:
        `cm` when this attempt acquired a brand-new surface itself (not a
        resume), `session_id` when it reused one already sitting in
        `SessionRegistry` (a resume) -- in which case the session is
        removed from the registry here, since a terminal outcome means
        there is nothing left to resume.
        """

        if cm is not None:
            await cm.__aexit__(None, None, None)
        elif session_id is not None:
            session = self._sessions.discard(session_id)
            await session.surface_cm.__aexit__(None, None, None)

    # -- human handoff lifecycle (Phase 12) ----------------------------------

    def claim_intervention(self, intervention_id: str, *, operator_id: str) -> InterventionRequest:
        """PENDING -> CLAIMED, and (if a live session is attached)
        PAUSED_WAITING_FOR_HUMAN -> HUMAN_CONTROL. .CLAUDE/04: "operator
        claims request -> operator takes control of SAME live session."
        This method only records that transition -- Phase 12 does not
        wire up an actual remote-control surface (VNC/CDP passthrough) for
        the operator to literally drive the browser; that is Phase 13's
        "headed browser + Xvfb + noVNC" job. What matters here is that the
        browser genuinely still exists, untouched, for whenever that
        wiring is added.

        Raises `InterventionStateError` on a double-claim (or any claim
        attempt against a non-PENDING intervention) rather than silently
        overwriting `claimed_by` -- exactly the "invalid/double claim"
        case the phase asks to be handled, not ignored.
        """

        intervention = self._interventions.get(intervention_id)
        if intervention.status != InterventionStatus.PENDING:
            raise InterventionStateError(
                f"intervention {intervention_id!r} cannot be claimed: status is "
                f"{intervention.status.value!r}, not 'pending' (already claimed or resolved)"
            )

        if intervention.session_id is not None:
            session = self._sessions.get(intervention.session_id)
            session.control_state = ControlState.HUMAN_CONTROL

        updated = intervention.model_copy(update={"status": InterventionStatus.CLAIMED, "claimed_by": operator_id})
        self._interventions.save(updated)
        self._emit(
            intervention.run_id,
            EventType.INTERVENTION_CLAIMED,
            step_id=intervention.current_step,
            details={"intervention_id": intervention_id, "claimed_by": operator_id},
        )
        return updated

    def mark_human_control_complete(self, intervention_id: str, *, operator_id: str) -> InterventionRequest:
        """CLAIMED -> RESOLVED, and (if a live session is attached)
        HUMAN_CONTROL -> RESUME_REQUESTED. .CLAUDE/04: "operator resolves
        issue -> operator hands control back." This marks the human's own
        part of the handoff done and the session as ready for
        `resume_run` to actually continue automation -- it does not
        itself resume anything, matching the phase's own split between
        "mark human control complete / request resume" and "resume run"
        as separate actions/endpoints.

        `operator_id` must match whoever claimed it
        (`InterventionOwnershipError` otherwise) -- the explicit, simple
        stand-in this phase uses instead of real IAM (see
        `cuas.handoff.errors.InterventionOwnershipError`'s docstring).
        """

        intervention = self._interventions.get(intervention_id)
        if intervention.status != InterventionStatus.CLAIMED:
            raise InterventionStateError(
                f"intervention {intervention_id!r} is not currently claimed (status={intervention.status.value!r})"
            )
        if intervention.claimed_by != operator_id:
            raise InterventionOwnershipError(
                f"intervention {intervention_id!r} was claimed by {intervention.claimed_by!r}, not {operator_id!r}"
            )

        if intervention.session_id is not None:
            session = self._sessions.get(intervention.session_id)
            session.control_state = ControlState.RESUME_REQUESTED

        updated = intervention.model_copy(
            update={"status": InterventionStatus.RESOLVED, "resolved_at": datetime.now(timezone.utc)}
        )
        self._interventions.save(updated)
        self._emit(
            intervention.run_id,
            EventType.HUMAN_CONTROL_COMPLETED,
            step_id=intervention.current_step,
            details={"intervention_id": intervention_id, "operator_id": operator_id},
        )
        return updated

    async def resume_run(self, intervention_id: str) -> RunResult:
        """RESUME_REQUESTED -> RUNNING_AUTOMATION, and actually continues
        the paused run on its original session/surface -- `.CLAUDE/04`'s
        "automation resumes" step. Requires
        `mark_human_control_complete` to have run first (an intervention
        must be RESOLVED with its session in RESUME_REQUESTED); calling
        this any earlier raises `InterventionStateError` rather than
        resuming a session a human hasn't actually finished with.

        Dispatches on `session.origin`: a paused replay resumes via
        `_run_replay(..., resume_session_id=...)` (continuing
        `ReplayEngine` from `session.resume_step_id`); a paused discovery
        resumes via `_run_discovery(..., resume_session_id=...)` (a fresh
        `DiscoveryEngine` attempt on the same surface -- see that method's
        docstring for why that's the right level of resume fidelity for
        discovery specifically). Either path may escalate again (a second
        APPROVAL_REQUIRED/FAILED) -- in which case the *same* `run_id` and
        `session_id` carry forward into a brand-new `InterventionRequest`,
        exactly the "preserve the original run_id across pause/handoff/
        resume" requirement, for as many pause/resume cycles as it takes.
        """

        intervention = self._interventions.get(intervention_id)
        if intervention.status != InterventionStatus.RESOLVED:
            raise InterventionStateError(
                f"intervention {intervention_id!r} is not ready to resume (status={intervention.status.value!r}); "
                "call mark_human_control_complete first"
            )
        if intervention.session_id is None:
            raise InterventionStateError(f"intervention {intervention_id!r} has no live session to resume")

        session = self._sessions.get(intervention.session_id)
        if session.control_state != ControlState.RESUME_REQUESTED:
            raise InterventionStateError(
                f"session {intervention.session_id!r} is not ready to resume "
                f"(control_state={session.control_state.value!r}); call mark_human_control_complete first"
            )

        session.control_state = ControlState.RUNNING_AUTOMATION
        self._emit(
            session.run_id,
            EventType.RUN_RESUMED,
            details={"intervention_id": intervention_id, "session_id": session.session_id, "origin": session.origin},
        )

        if session.origin == "replay":
            assert session.artifact is not None
            result = await self._run_replay(
                session.artifact,
                session.inputs,
                session.context,
                run_id=session.run_id,
                capability_id=session.capability_id,
                discovered_new_capability=session.discovered_new_capability,
                resume_session_id=session.session_id,
            )
        else:
            assert session.discovery_goal is not None
            result = await self._run_discovery(
                session.capability_id,
                session.discovery_goal,
                session.inputs,
                session.context,
                run_id=session.run_id,
                resume_session_id=session.session_id,
                discovery_resume=session.pending_discovery_action,
            )

        return self._finish(result)

    async def cancel_intervention(self, intervention_id: str, *, operator_id: str) -> InterventionRequest:
        """An operator's explicit "give up on this run" -- not named as a
        required endpoint by the phase instructions, but `InterventionStatus
        .CANCELLED` already existed in the domain vocabulary (Phase 4/11
        scaffolding) with nothing that ever set it. This is what does:
        PENDING or CLAIMED -> CANCELLED, and unconditionally closes
        whatever live session was still attached (there is no further
        resume for a cancelled intervention -- unlike the RESOLVED path,
        cancellation itself performs the session cleanup, rather than
        waiting for a `resume_run` call that will never come).
        """

        intervention = self._interventions.get(intervention_id)
        if intervention.status not in (InterventionStatus.PENDING, InterventionStatus.CLAIMED):
            raise InterventionStateError(
                f"intervention {intervention_id!r} cannot be cancelled (status={intervention.status.value!r})"
            )

        if intervention.session_id is not None and intervention.session_id in self._sessions:
            await self._close_surface(None, intervention.session_id)

        updated = intervention.model_copy(
            update={"status": InterventionStatus.CANCELLED, "resolved_at": datetime.now(timezone.utc)}
        )
        self._interventions.save(updated)
        self._emit(
            intervention.run_id,
            EventType.INTERVENTION_CANCELLED,
            step_id=intervention.current_step,
            details={"intervention_id": intervention_id, "operator_id": operator_id},
        )
        return updated

    # -- intervention creation ---------------------------------------------

    def _create_intervention(
        self,
        *,
        run_id: str,
        capability_id: str,
        context: AppContext,
        reason: str,
        current_step: str | None,
        session_id: str | None,
    ) -> str:
        intervention_id = uuid.uuid4().hex
        request = InterventionRequest(
            id=intervention_id,
            run_id=run_id,
            capability_id=capability_id,
            tenant_id=context.tenant_id,
            current_step=current_step,
            reason=reason,
            session_id=session_id,
            status=InterventionStatus.PENDING,
            created_at=datetime.now(timezone.utc),
        )
        self._interventions.save(request)
        self._emit(
            run_id,
            EventType.INTERVENTION_REQUESTED,
            step_id=current_step,
            status="escalation",
            details={"intervention_id": intervention_id, "reason": reason, "session_id": session_id},
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
                "session_id": result.session_id,
            },
        )
        return result
