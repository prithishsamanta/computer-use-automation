"""RunOrchestrator's persistent human-handoff + live-session lifecycle
(Phase 12): an escalation keeps its live surface open (SessionRegistry)
instead of closing it, persists an InterventionRequest naming that
session, and an operator can claim -> mark human control complete ->
resume it -- continuing the *same* run_id on the *same* surface, never
acquiring a second one from the surface_factory.

All pure unit tests -- FakeSurfaceAdapter/FakeLLMClient stand in for the
browser and the model, exactly like test_run_orchestrator.py. The
`sequential_surface_factory` fixture below is scripted with exactly ONE
surface per test that escalates and resumes: if `resume_run` ever called
`surface_factory` again (i.e. accidentally opened a second browser for
the operator), the factory's own `assert remaining, "..."` would fail the
test outright -- so "do not create a new browser session for the
operator" is verified structurally, not just by inspecting the result.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import pytest

from cuas.artifact import ArtifactRepository, FileArtifactRepository
from cuas.capability import CapabilityRecord, CapabilityService, CapabilityStatus, FileCapabilityRepository
from cuas.discovery import DiscoveryGoal, DiscoveryTraceStore, FileDiscoveryTraceStore
from cuas.domain import AppContext, ControlState, InterventionStatus
from cuas.handoff import (
    FileInterventionRepository,
    InMemoryInterventionRepository,
    InterventionOwnershipError,
    InterventionRepository,
    InterventionStateError,
    SessionNotFoundError,
    SessionRegistry,
)
from cuas.orchestration import RunOrchestrator, RunOutcome
from cuas.safety import LayeredPolicyEngine
from cuas.surface.adapter import SurfaceAdapter
from tests.fixtures.fake_llm_client import FakeLLMClient, propose, propose_done, role_target
from tests.fixtures.fake_surface import FakeSurfaceAdapter
from tests.fixtures.sample_artifacts import approval_required_capability

VENDOR = "test-vendor"
APPLICATION = "test-app"
CAPABILITY_ID = "approval_required_capability"
DEMO_URL = "http://fake-demo.invalid/"


def _context(tenant_id: str = "cu42") -> AppContext:
    return AppContext(vendor=VENDOR, application=APPLICATION, version="1.0.0", tenant_id=tenant_id)


def sequential_surface_factory(*surfaces: SurfaceAdapter):
    """Identical to test_run_orchestrator.py's own helper: each call hands
    back the next scripted surface, wrapped as the async context manager
    RunOrchestrator expects. Calling it more times than surfaces were
    scripted for is a hard test failure -- see module docstring."""

    remaining = list(surfaces)

    @asynccontextmanager
    async def factory() -> AsyncIterator[SurfaceAdapter]:
        assert remaining, "surface_factory called more times than the test scripted surfaces for"
        yield remaining.pop(0)

    return factory


@pytest.fixture()
def artifact_repo(tmp_path) -> ArtifactRepository:
    repo = FileArtifactRepository(tmp_path / "artifacts")
    repo.save(approval_required_capability())
    return repo


@pytest.fixture()
def capability_service(tmp_path, artifact_repo: ArtifactRepository) -> CapabilityService:
    service = CapabilityService(FileCapabilityRepository(tmp_path / "capabilities"), artifact_repo)
    service.register(
        CapabilityRecord(
            capability_id=CAPABILITY_ID,
            name=CAPABILITY_ID,
            vendor=VENDOR,
            application=APPLICATION,
            supported_versions=["1.x"],
            tenant_scope="cu42",
            artifact_version="1.0.0",
            status=CapabilityStatus.ACTIVE,
        )
    )
    return service


@pytest.fixture()
def trace_store(tmp_path) -> DiscoveryTraceStore:
    return FileDiscoveryTraceStore(tmp_path / "traces")


def _make_orchestrator(
    capability_service: CapabilityService,
    artifact_repo: ArtifactRepository,
    trace_store: DiscoveryTraceStore,
    surface_factory,
    *,
    interventions: InterventionRepository | None = None,
    session_registry: SessionRegistry | None = None,
    llm=None,
) -> tuple[RunOrchestrator, InterventionRepository, SessionRegistry]:
    interventions = interventions if interventions is not None else InMemoryInterventionRepository()
    session_registry = session_registry if session_registry is not None else SessionRegistry()
    orchestrator = RunOrchestrator(
        capability_service,
        artifact_repo,
        LayeredPolicyEngine(),
        surface_factory,
        interventions,
        trace_store,
        llm=llm,
        session_registry=session_registry,
    )
    return orchestrator, interventions, session_registry


class TestEscalationRetainsTheSession:
    async def test_escalation_keeps_the_surface_open_and_registers_a_session(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        surface = FakeSurfaceAdapter()
        orchestrator, interventions, sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(surface)
        )

        result = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())

        assert result.outcome == RunOutcome.APPROVAL_REQUIRED
        assert result.session_id is not None

        # The browser genuinely never closed: the exact same
        # FakeSurfaceAdapter instance is still reachable through the
        # registry, and the intervention that names it is still PENDING
        # (nobody has claimed it yet) -- .CLAUDE/04's "Do not terminate
        # the browser session when intervention is created."
        session = sessions.get(result.session_id)
        assert session.surface is surface
        assert session.control_state == ControlState.PAUSED_WAITING_FOR_HUMAN
        assert interventions.get(result.intervention_id).status == InterventionStatus.PENDING

    async def test_intervention_is_persisted_with_a_non_null_session_id(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        orchestrator, interventions, _sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(FakeSurfaceAdapter())
        )

        result = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())

        pending = interventions.get(result.intervention_id)
        assert pending.session_id == result.session_id
        assert pending.session_id is not None


class TestClaim:
    async def test_claim_transitions_to_claimed_and_session_to_human_control(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        orchestrator, interventions, sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(FakeSurfaceAdapter())
        )
        result = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())

        updated = orchestrator.claim_intervention(result.intervention_id, operator_id="teller-1")

        assert updated.status == InterventionStatus.CLAIMED
        assert updated.claimed_by == "teller-1"
        assert sessions.get(result.session_id).control_state == ControlState.HUMAN_CONTROL
        assert interventions.get(result.intervention_id).status == InterventionStatus.CLAIMED

    async def test_double_claim_is_rejected(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        orchestrator, _interventions, _sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(FakeSurfaceAdapter())
        )
        result = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())
        orchestrator.claim_intervention(result.intervention_id, operator_id="teller-1")

        with pytest.raises(InterventionStateError):
            orchestrator.claim_intervention(result.intervention_id, operator_id="teller-2")

    async def test_claiming_an_unknown_intervention_raises(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        orchestrator, _interventions, _sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory()
        )

        with pytest.raises(KeyError):
            orchestrator.claim_intervention("no-such-intervention", operator_id="teller-1")


class TestHumanControlComplete:
    async def test_marks_resolved_and_requests_resume(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        orchestrator, interventions, sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(FakeSurfaceAdapter())
        )
        result = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())
        orchestrator.claim_intervention(result.intervention_id, operator_id="teller-1")

        updated = orchestrator.mark_human_control_complete(result.intervention_id, operator_id="teller-1")

        assert updated.status == InterventionStatus.RESOLVED
        assert updated.resolved_at is not None
        assert sessions.get(result.session_id).control_state == ControlState.RESUME_REQUESTED
        assert interventions.get(result.intervention_id).status == InterventionStatus.RESOLVED

    async def test_before_claim_is_rejected(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        orchestrator, _interventions, _sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(FakeSurfaceAdapter())
        )
        result = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())

        with pytest.raises(InterventionStateError):
            orchestrator.mark_human_control_complete(result.intervention_id, operator_id="teller-1")

    async def test_by_the_wrong_operator_is_rejected(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        orchestrator, _interventions, _sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(FakeSurfaceAdapter())
        )
        result = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())
        orchestrator.claim_intervention(result.intervention_id, operator_id="teller-1")

        with pytest.raises(InterventionOwnershipError):
            orchestrator.mark_human_control_complete(result.intervention_id, operator_id="teller-2")


class TestResume:
    async def test_resume_before_human_control_complete_is_rejected(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        orchestrator, _interventions, _sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(FakeSurfaceAdapter())
        )
        result = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())
        orchestrator.claim_intervention(result.intervention_id, operator_id="teller-1")

        with pytest.raises(InterventionStateError):
            await orchestrator.resume_run(result.intervention_id)

    async def test_resume_continues_the_same_run_on_the_same_session_and_cleans_up_after(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        # Exactly ONE surface is ever scripted for this whole scenario --
        # if resume_run acquired a second one, sequential_surface_factory
        # would raise on that call, failing the test.
        orchestrator, interventions, sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(FakeSurfaceAdapter())
        )

        first = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())
        assert first.outcome == RunOutcome.APPROVAL_REQUIRED
        original_run_id = first.run_id
        session_id = first.session_id
        assert session_id is not None

        orchestrator.claim_intervention(first.intervention_id, operator_id="teller-1")
        orchestrator.mark_human_control_complete(first.intervention_id, operator_id="teller-1")

        resumed = await orchestrator.resume_run(first.intervention_id)

        assert resumed.run_id == original_run_id
        assert resumed.outcome == RunOutcome.SUCCESS
        # A terminal outcome closes the surface and drops the session --
        # nothing left to resume a second time.
        assert session_id not in sessions
        with pytest.raises(SessionNotFoundError):
            sessions.get(session_id)
        # The InterventionRequest itself is untouched history, not
        # deleted -- an operator's audit trail still shows it RESOLVED.
        assert interventions.get(first.intervention_id).status == InterventionStatus.RESOLVED

    async def test_full_cycle_also_works_against_the_file_backed_intervention_repository(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore, tmp_path
    ) -> None:
        """The 'real', durable InterventionRepository (Phase 12's own
        instruction: 'Add a file-backed or similarly simple persistent
        InterventionRepository for the real path') drives the exact same
        claim -> complete -> resume lifecycle as the in-memory one used
        everywhere else in this file."""

        orchestrator, interventions, _sessions = _make_orchestrator(
            capability_service,
            artifact_repo,
            trace_store,
            sequential_surface_factory(FakeSurfaceAdapter()),
            interventions=FileInterventionRepository(tmp_path / "interventions"),
        )

        first = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())
        orchestrator.claim_intervention(first.intervention_id, operator_id="teller-1")
        orchestrator.mark_human_control_complete(first.intervention_id, operator_id="teller-1")
        resumed = await orchestrator.resume_run(first.intervention_id)

        assert resumed.outcome == RunOutcome.SUCCESS
        assert resumed.run_id == first.run_id
        # Round-tripped through disk, not just held in a dict.
        reloaded = FileInterventionRepository(tmp_path / "interventions").get(first.intervention_id)
        assert reloaded.status == InterventionStatus.RESOLVED


class TestCancel:
    async def test_cancel_marks_cancelled_and_closes_the_session(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        orchestrator, interventions, sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(FakeSurfaceAdapter())
        )
        result = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())
        session_id = result.session_id
        assert session_id is not None

        updated = await orchestrator.cancel_intervention(result.intervention_id, operator_id="teller-1")

        assert updated.status == InterventionStatus.CANCELLED
        assert updated.resolved_at is not None
        assert session_id not in sessions
        assert interventions.get(result.intervention_id).status == InterventionStatus.CANCELLED


class TestDiscoveryEscalationSession:
    async def test_discovery_escalation_also_retains_the_live_session(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        surface = FakeSurfaceAdapter()
        llm = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close")))
        orchestrator, interventions, sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(surface), llm=llm
        )
        goal = DiscoveryGoal(
            capability_id="close_account_capability",
            description="Close the member's account.",
            start_url=DEMO_URL,
        )

        result = await orchestrator.run_capability(
            "close_account_capability", {}, _context(), discovery_goal=goal
        )

        assert result.outcome == RunOutcome.APPROVAL_REQUIRED
        assert result.session_id is not None
        session = sessions.get(result.session_id)
        assert session.surface is surface
        assert session.origin == "discovery"
        assert interventions.get(result.intervention_id).session_id == result.session_id


class TestDiscoveryApprovedActionResume:
    """Approving a discovery-origin APPROVAL_REQUIRED intervention must
    authorize and execute the EXACT action DiscoveryEngine escalated on --
    not merely unblock a fresh LLM reasoning attempt on the same surface
    (see engine.py's DiscoveryPendingApproval and this module's own
    docstring). These exercise the same claim/complete/resume API
    TestClaim/TestHumanControlComplete/TestResume already cover for
    replay, now for a discovery-origin session carrying a pending action.
    """

    def _goal(self) -> DiscoveryGoal:
        return DiscoveryGoal(
            capability_id="close_account_capability",
            description="Close the member's account.",
            start_url=DEMO_URL,
        )

    async def test_claiming_alone_does_not_authorize_resume(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        surface = FakeSurfaceAdapter()
        llm = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close")))
        orchestrator, _interventions, sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(surface), llm=llm
        )

        result = await orchestrator.run_capability("close_account_capability", {}, _context(), discovery_goal=self._goal())
        assert result.outcome == RunOutcome.APPROVAL_REQUIRED
        session = sessions.get(result.session_id)
        assert session.pending_discovery_action is not None
        assert session.pending_discovery_action.pending_action.intent == "close_account"

        orchestrator.claim_intervention(result.intervention_id, operator_id="teller-1")

        # Merely claiming is explicitly non-authorizing (.CLAUDE/04:
        # claim just transfers control to the operator) -- resume_run
        # must still refuse, exactly as it would for replay.
        with pytest.raises(InterventionStateError):
            await orchestrator.resume_run(result.intervention_id)

    async def test_resume_before_human_control_complete_is_rejected(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        surface = FakeSurfaceAdapter()
        llm = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close")))
        orchestrator, _interventions, _sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(surface), llm=llm
        )

        result = await orchestrator.run_capability("close_account_capability", {}, _context(), discovery_goal=self._goal())
        assert result.outcome == RunOutcome.APPROVAL_REQUIRED

        with pytest.raises(InterventionStateError):
            await orchestrator.resume_run(result.intervention_id)

    async def test_complete_then_resume_executes_the_exact_approved_action_without_a_new_llm_proposal(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        """Only ONE surface is ever scripted -- resuming a discovery-
        origin session never calls surface_factory again (same structural
        guarantee TestResume relies on for replay). The second proposal
        (submit_transaction) is ALSO require_approval, so this also shows
        the exact approved action executing for real (the click reaches
        the surface) while the very next turn still goes through normal
        policy evaluation and escalates again, rather than reaching a
        full artifact-materializing SUCCESS -- which needs a second,
        unrelated verification-replay surface this test isn't about."""

        surface = FakeSurfaceAdapter()
        llm = FakeLLMClient(
            propose("click", "close_account", target=role_target("button", "Close")),
            propose("click", "submit_transaction", target=role_target("button", "Confirm")),
        )
        orchestrator, interventions, sessions = _make_orchestrator(
            capability_service, artifact_repo, trace_store, sequential_surface_factory(surface), llm=llm
        )

        first = await orchestrator.run_capability("close_account_capability", {}, _context(), discovery_goal=self._goal())
        assert first.outcome == RunOutcome.APPROVAL_REQUIRED
        assert len(llm.calls) == 1  # only the original escalating proposal so far

        orchestrator.claim_intervention(first.intervention_id, operator_id="teller-1")
        orchestrator.mark_human_control_complete(first.intervention_id, operator_id="teller-1")
        resumed = await orchestrator.resume_run(first.intervention_id)

        assert resumed.run_id == first.run_id
        # The close_account click genuinely executed against the surface
        # (approved, not merely re-proposed)...
        assert any(call[0] == "click" for call in surface.calls)
        # ...and exactly one more LLM call happened after resume (the new
        # submit_transaction proposal) -- never a second call re-proposing
        # close_account itself -- and that new proposal was checked by
        # policy normally, escalating again on its own merits.
        assert len(llm.calls) == 2
        assert resumed.outcome == RunOutcome.APPROVAL_REQUIRED
        assert resumed.session_id == first.session_id
        assert interventions.get(first.intervention_id).status == InterventionStatus.RESOLVED
        assert interventions.get(resumed.intervention_id).reason is not None and "submit_transaction" in interventions.get(resumed.intervention_id).reason
