"""RunOrchestrator: request -> capability resolution -> deterministic
replay, or DISCOVERY_REQUIRED -> live discovery -> artifact construction/
storage -> replay, with policy escalations and unresolved failures routed
through the (Phase 11) intervention seam. Every scenario the user's
explicit Phase 11 instructions ask for; all pure unit tests -- a
FakeSurfaceAdapter/FakeLLMClient pair stands in for the browser and the
model, so nothing here needs a real browser or an API key (no
`integration`/`live_llm` marker, same as Phase 10's capability tests).

Fixtures build real collaborators wherever the real thing is cheap and
in-process (CapabilityService, FileArtifactRepository/
FileCapabilityRepository against tmp_path, LayeredPolicyEngine,
ArtifactBuilder via RunOrchestrator's own default, FileDiscoveryTraceStore,
InMemoryInterventionRepository) -- only the surface and the LLM are faked,
exactly the same seam every earlier phase's own tests fake at.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from typing import AsyncIterator

import pytest

from cuas.artifact import ArtifactRepository, FileArtifactRepository
from cuas.capability import CapabilityRecord, CapabilityService, CapabilityStatus, FileCapabilityRepository
from cuas.discovery import DiscoveryGoal, DiscoveryLimits, DiscoveryTraceStore, FileDiscoveryTraceStore
from cuas.domain import AppContext, Locator, LocatorStrategy, Target
from cuas.handoff import InMemoryInterventionRepository
from cuas.orchestration import RunOrchestrator, RunOutcome
from cuas.safety import LayeredPolicyEngine
from cuas.surface.adapter import SurfaceAdapter, WaitCondition, WaitConditionKind
from tests.fixtures.fake_capability_repository import FakeCapabilityRepository
from tests.fixtures.fake_llm_client import FakeLLMClient, css_target, malformed, propose, propose_done, role_target
from tests.fixtures.fake_surface import FakeSurfaceAdapter
from tests.fixtures.sample_artifacts import approval_required_capability, blocked_capability, get_savings_balance

VENDOR = "meridian-demo"
APPLICATION = "credit-union-admin"
CAPABILITY_ID = "get_savings_balance"
DEMO_URL = "http://fake-demo.invalid/"


def _context(tenant_id: str = "cu42") -> AppContext:
    return AppContext(vendor=VENDOR, application=APPLICATION, version="1.0.0", tenant_id=tenant_id)


def _context_for(vendor: str, application: str, *, tenant_id: str = "cu42") -> AppContext:
    return AppContext(vendor=vendor, application=application, version="1.0.0", tenant_id=tenant_id)


def _capability_record(
    *,
    capability_id: str = CAPABILITY_ID,
    tenant_scope: str = "base",
    artifact_version: str = "1.0.0",
    vendor: str = VENDOR,
    application: str = APPLICATION,
) -> CapabilityRecord:
    return CapabilityRecord(
        capability_id=capability_id,
        name=capability_id,
        vendor=vendor,
        application=application,
        supported_versions=["1.x"],
        tenant_scope=tenant_scope,
        artifact_version=artifact_version,
        status=CapabilityStatus.ACTIVE,
    )


def _target_for(target_dict: dict) -> Target:
    """Rebuilds the same domain Target DiscoveryEngine._parse_target would
    construct from one of fake_llm_client's raw proposal target dicts, so
    a test can script FakeSurfaceAdapter.read/click/etc. against exactly
    the target key the engine will actually resolve."""

    return Target(
        primary=Locator(
            strategy=LocatorStrategy.CSS, params={"selector": target_dict["selector"]}, frame=target_dict.get("frame")
        )
    )


def sequential_surface_factory(*surfaces: SurfaceAdapter):
    """Each call to the returned factory hands back the next surface in
    order, wrapped as the async context manager RunOrchestrator expects
    (the exact shape `cuas.surface.playwright_adapter.launch_playwright_surface`
    already has -- see orchestrator.py's `SurfaceFactory` docstring).
    RunOrchestrator calls this at most twice in one `run_capability` call
    (once for a plain replay/discovery attempt, and again only for the
    replay that immediately follows a *newly successful* discovery), so a
    test scripts exactly as many surfaces as its scenario needs."""

    remaining = list(surfaces)

    @asynccontextmanager
    async def factory() -> AsyncIterator[SurfaceAdapter]:
        assert remaining, "surface_factory called more times than the test scripted surfaces for"
        yield remaining.pop(0)

    return factory


def _replay_ready_surface() -> FakeSurfaceAdapter:
    """A FakeSurfaceAdapter pre-scripted to satisfy get_savings_balance()'s
    own checkpoint/output, the same way test_replay_engine.py's own
    fixtures do."""

    surface = FakeSurfaceAdapter()
    artifact = get_savings_balance()
    checkpoint = artifact.steps[1].checkpoint
    assert checkpoint is not None
    surface.script_wait(checkpoint, None)
    surface.script_read(artifact.outputs["savings_balance"].source, "9,900.00")
    return surface


def _discovery_goal(*, capability_id: str = CAPABILITY_ID, member_id: str = "M1001") -> DiscoveryGoal:
    return DiscoveryGoal(
        capability_id=capability_id,
        description=f"Find member {member_id} and read their savings balance.",
        start_url=DEMO_URL,
        inputs={"member_id": member_id},
        sensitive_inputs={"member_id"},
        success_checkpoint=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Accounts"),
        known_business_outcomes=[],
    )


@pytest.fixture()
def artifact_repo(tmp_path) -> ArtifactRepository:
    repo = FileArtifactRepository(tmp_path / "artifacts")
    repo.save(get_savings_balance(version="1.0.0"))
    return repo


@pytest.fixture()
def capability_service(tmp_path, artifact_repo: ArtifactRepository) -> CapabilityService:
    return CapabilityService(FileCapabilityRepository(tmp_path / "capabilities"), artifact_repo)


@pytest.fixture()
def trace_store(tmp_path) -> DiscoveryTraceStore:
    return FileDiscoveryTraceStore(tmp_path / "traces")


class TestKnownCapability:
    async def test_resolved_capability_replays_to_success(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        capability_service.register(_capability_record(tenant_scope="cu42"))
        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(_replay_ready_surface()),
            InMemoryInterventionRepository(),
            trace_store,
        )

        result = await orchestrator.run_capability(CAPABILITY_ID, {"member_id": "M1001"}, _context())

        assert result.outcome == RunOutcome.SUCCESS
        assert result.outputs["savings_balance"] == Decimal("9900.00")
        assert result.artifact_version == "1.0.0"
        assert result.discovered_new_capability is False
        assert result.run_id

    async def test_resolved_capability_hits_business_outcome(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        capability_service.register(_capability_record(tenant_scope="cu42"))
        artifact = get_savings_balance()
        surface = FakeSurfaceAdapter()
        checkpoint = artifact.steps[1].checkpoint
        assert checkpoint is not None
        not_found = artifact.business_outcomes[0].detect
        surface.script_wait(checkpoint, AssertionError("checkpoint should not appear"))
        surface.script_wait(not_found, None)

        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(surface),
            InMemoryInterventionRepository(),
            trace_store,
        )

        result = await orchestrator.run_capability(CAPABILITY_ID, {"member_id": "M9999"}, _context())

        assert result.outcome == RunOutcome.BUSINESS_OUTCOME
        assert result.business_outcome_code == "MEMBER_NOT_FOUND"

    async def test_resolved_capability_approval_required_creates_intervention(
        self, artifact_repo: ArtifactRepository, tmp_path, trace_store: DiscoveryTraceStore
    ) -> None:
        artifact_repo.save(approval_required_capability())
        capability_service = CapabilityService(FileCapabilityRepository(tmp_path / "capabilities"), artifact_repo)
        capability_service.register(
            _capability_record(
                capability_id="approval_required_capability",
                tenant_scope="cu42",
                artifact_version="1.0.0",
                vendor="test-vendor",
                application="test-app",
            )
        )
        interventions = InMemoryInterventionRepository()
        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(FakeSurfaceAdapter()),
            interventions,
            trace_store,
        )

        result = await orchestrator.run_capability(
            "approval_required_capability", {}, _context_for("test-vendor", "test-app")
        )

        assert result.outcome == RunOutcome.APPROVAL_REQUIRED
        assert result.intervention_id is not None
        pending = interventions.get(result.intervention_id)
        assert pending.run_id == result.run_id
        assert pending.capability_id == "approval_required_capability"

    async def test_resolved_capability_blocked_creates_no_intervention(
        self, artifact_repo: ArtifactRepository, tmp_path, trace_store: DiscoveryTraceStore
    ) -> None:
        artifact_repo.save(blocked_capability())
        capability_service = CapabilityService(FileCapabilityRepository(tmp_path / "capabilities"), artifact_repo)
        capability_service.register(
            _capability_record(
                capability_id="blocked_capability",
                tenant_scope="cu42",
                artifact_version="1.0.0",
                vendor="test-vendor",
                application="test-app",
            )
        )
        interventions = InMemoryInterventionRepository()
        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(FakeSurfaceAdapter()),
            interventions,
            trace_store,
        )

        result = await orchestrator.run_capability("blocked_capability", {}, _context_for("test-vendor", "test-app"))

        assert result.outcome == RunOutcome.BLOCKED
        assert interventions.list_all() == []

    async def test_resolved_capability_hard_failure_creates_intervention(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        capability_service.register(_capability_record(tenant_scope="cu42"))
        artifact = get_savings_balance()
        surface = FakeSurfaceAdapter()
        checkpoint = artifact.steps[1].checkpoint
        assert checkpoint is not None
        # Neither the checkpoint nor the known business outcome ever
        # appears, and there is nothing to recover from -- ReplayEngine
        # exhausts its one bounded recovery attempt and reports FAILED.
        surface.script_wait(checkpoint, AssertionError("never appears"), AssertionError("never appears"))
        surface.script_wait(artifact.business_outcomes[0].detect, AssertionError("never appears"))

        interventions = InMemoryInterventionRepository()
        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(surface),
            interventions,
            trace_store,
        )

        result = await orchestrator.run_capability(CAPABILITY_ID, {"member_id": "M1001"}, _context())

        assert result.outcome == RunOutcome.FAILED
        assert result.error_code is not None
        assert result.intervention_id is not None
        assert interventions.get(result.intervention_id).reason


class TestNoCapability:
    async def test_no_capability_and_no_goal_reports_discovery_required(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(),
            InMemoryInterventionRepository(),
            trace_store,
        )

        result = await orchestrator.run_capability("brand_new_capability", {}, _context())

        assert result.outcome == RunOutcome.DISCOVERY_REQUIRED
        assert result.reason is not None

    async def test_no_capability_with_goal_but_no_llm_reports_discovery_required(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(),
            InMemoryInterventionRepository(),
            trace_store,
            llm=None,
        )
        goal = DiscoveryGoal(capability_id="brand_new_capability", description="x", start_url=DEMO_URL)

        result = await orchestrator.run_capability("brand_new_capability", {}, _context(), discovery_goal=goal)

        assert result.outcome == RunOutcome.DISCOVERY_REQUIRED
        assert "llm" in result.reason.lower()

    async def test_ambiguous_capability_is_reported_without_attempting_discovery(
        self, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        # Two equally-specific ("base") compatible records for the same
        # capability_id -- constructed with the same FakeCapabilityRepository
        # Phase 10's own ambiguous-match test uses, since a real
        # FileCapabilityRepository's uniqueness key cannot produce this
        # situation (tests/fixtures/fake_capability_repository.py explains
        # why that does not make the defensive check unnecessary).
        fake_repo = FakeCapabilityRepository([_capability_record(tenant_scope="base"), _capability_record(tenant_scope="base")])
        ambiguous_service = CapabilityService(fake_repo, artifact_repo)
        orchestrator = RunOrchestrator(
            ambiguous_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(),
            InMemoryInterventionRepository(),
            trace_store,
        )

        result = await orchestrator.run_capability(CAPABILITY_ID, {}, _context())

        assert result.outcome == RunOutcome.AMBIGUOUS_CAPABILITY


class TestDiscoveryPath:
    async def test_successful_discovery_builds_stores_and_replays(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        read_target_dict = css_target("#acct-row-2 td:nth-child(3)", frame="#accounts-frame")
        llm = FakeLLMClient(
            propose("fill", "search_member", target=role_target("textbox"), value="M1001"),
            propose("click", "view_account", target=role_target("button", "Search")),
            propose("read", "open_member_record", target=read_target_dict),
            propose_done("balance read"),
        )
        discovery_surface = FakeSurfaceAdapter()
        discovery_surface.script_wait(WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Accounts"), None)
        discovery_surface.script_read(_target_for(read_target_dict), "9,900.00")
        replay_surface = _replay_ready_surface()

        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(discovery_surface, replay_surface),
            InMemoryInterventionRepository(),
            trace_store,
            llm=llm,
        )

        result = await orchestrator.run_capability(
            CAPABILITY_ID, {"member_id": "M1001"}, _context(), discovery_goal=_discovery_goal()
        )

        assert result.outcome == RunOutcome.SUCCESS
        assert result.discovered_new_capability is True

        # The output's declared name comes from the READ action's own
        # intent ("open_member_record" -- see ArtifactBuilder's
        # _output_name_from_intent), not from the hand-authored sample
        # artifact's "savings_balance" -- look it up from the artifact
        # actually built, exactly like the Phase 9 e2e test does, rather
        # than assuming a name.
        resolution = capability_service.resolve(CAPABILITY_ID, _context())
        assert resolution.status.value == "resolved"
        assert resolution.matched_record.tenant_scope == "cu42"
        output_name = resolution.artifact.success_condition.output
        assert result.outputs[output_name] == Decimal("9900.00")

    async def test_discovery_approval_required_creates_intervention(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        llm = FakeLLMClient(propose("click", "close_account", target=role_target("button", "Close")))
        interventions = InMemoryInterventionRepository()
        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(FakeSurfaceAdapter()),
            interventions,
            trace_store,
            llm=llm,
        )

        result = await orchestrator.run_capability(
            "close_account_capability",
            {},
            _context(),
            discovery_goal=_discovery_goal(capability_id="close_account_capability"),
        )

        assert result.outcome == RunOutcome.APPROVAL_REQUIRED
        assert result.intervention_id is not None
        assert interventions.get(result.intervention_id).run_id == result.run_id

    async def test_discovery_blocked_creates_no_intervention(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        llm = FakeLLMClient(propose("navigate", "navigate_unauthorized_domain", value="http://evil.invalid/"))
        interventions = InMemoryInterventionRepository()
        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(FakeSurfaceAdapter()),
            interventions,
            trace_store,
            llm=llm,
        )

        result = await orchestrator.run_capability(
            "sketchy_capability", {}, _context(), discovery_goal=_discovery_goal(capability_id="sketchy_capability")
        )

        assert result.outcome == RunOutcome.BLOCKED
        assert interventions.list_all() == []

    async def test_discovery_hard_failure_creates_intervention(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        """A malformed proposal alone no longer terminates discovery (see
        DECISIONS_LOG.md/test_discovery_engine.py) -- it takes a bounded
        run that never recovers to still hit a hard failure, so this
        scripts enough malformed turns to exhaust a small max_steps budget
        rather than a single one."""

        llm = FakeLLMClient(malformed(), malformed())
        interventions = InMemoryInterventionRepository()
        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(FakeSurfaceAdapter()),
            interventions,
            trace_store,
            llm=llm,
            discovery_limits=DiscoveryLimits(max_steps=2),
        )

        result = await orchestrator.run_capability(
            "flaky_capability", {}, _context(), discovery_goal=_discovery_goal(capability_id="flaky_capability")
        )

        assert result.outcome == RunOutcome.FAILED
        assert result.intervention_id is not None


class TestRunIdCorrelation:
    async def test_one_run_id_correlates_discovery_and_replay(
        self, capability_service: CapabilityService, artifact_repo: ArtifactRepository, trace_store: DiscoveryTraceStore
    ) -> None:
        read_target_dict = css_target("#acct-row-2 td:nth-child(3)", frame="#accounts-frame")
        llm = FakeLLMClient(
            propose("fill", "search_member", target=role_target("textbox"), value="M1001"),
            propose("click", "view_account", target=role_target("button", "Search")),
            propose("read", "open_member_record", target=read_target_dict),
            propose_done("balance read"),
        )
        discovery_surface = FakeSurfaceAdapter()
        discovery_surface.script_wait(WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Accounts"), None)
        discovery_surface.script_read(_target_for(read_target_dict), "9,900.00")
        replay_surface = _replay_ready_surface()

        orchestrator = RunOrchestrator(
            capability_service,
            artifact_repo,
            LayeredPolicyEngine(),
            sequential_surface_factory(discovery_surface, replay_surface),
            InMemoryInterventionRepository(),
            trace_store,
            llm=llm,
        )

        result = await orchestrator.run_capability(
            CAPABILITY_ID, {"member_id": "M1001"}, _context(), run_id="fixed-run-id", discovery_goal=_discovery_goal()
        )

        assert result.run_id == "fixed-run-id"
        # The trace this run's own artifact construction depended on was
        # persisted under the exact same id -- proof the id genuinely
        # threaded through DiscoveryEngine, not just echoed back by
        # RunOrchestrator.
        trace = trace_store.load("fixed-run-id")
        assert trace.run_id == "fixed-run-id"
        assert trace.final_status == "success"
