"""CapabilityService.resolve(): metadata-filter capability resolution
(.CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md, "Capability Retrieval";
.CLAUDE/08 decision #8) -- exact resolution, override selection/precedence
routed through the existing resolve_artifact(), incompatible-context
rejection, disabled/deprecated capabilities, ambiguous matches, and
no-match producing a structured NO_CAPABILITY_MATCH result rather than an
exception or a replay failure (.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md,
"Capability Routing Outcomes")."""

from __future__ import annotations

import pytest

from cuas.artifact import ArtifactOverride, ArtifactRepository, FileArtifactRepository, OverrideScope, StepOverride
from cuas.capability import (
    CapabilityRecord,
    CapabilityResolutionStatus,
    CapabilityService,
    CapabilityStatus,
)
from cuas.domain import AppContext, Locator, LocatorStrategy, Target
from tests.fixtures.fake_capability_repository import FakeCapabilityRepository
from tests.fixtures.sample_artifacts import get_savings_balance

VENDOR = "meridian-demo"
APPLICATION = "credit-union-admin"
CAPABILITY_ID = "get_savings_balance"


def _context(version: str = "1.0.0", tenant_id: str = "cu42") -> AppContext:
    return AppContext(vendor=VENDOR, application=APPLICATION, version=version, tenant_id=tenant_id)


def _record(**overrides) -> CapabilityRecord:
    defaults = dict(
        capability_id=CAPABILITY_ID,
        name="Get Savings Balance",
        description="Find a member by ID and return their current savings balance.",
        vendor=VENDOR,
        application=APPLICATION,
        supported_versions=["1.x"],
        tenant_scope="base",
        artifact_version="1.0.0",
        status=CapabilityStatus.ACTIVE,
    )
    defaults.update(overrides)
    return CapabilityRecord(**defaults)


def _role_target(name: str) -> Target:
    return Target(primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params={"role": "button", "name": name}))


def _frame_target(frame: str) -> Target:
    return Target(primary=Locator(strategy=LocatorStrategy.CSS, params={"selector": "button.search"}, frame=frame))


@pytest.fixture()
def artifact_repo(tmp_path) -> ArtifactRepository:
    repo = FileArtifactRepository(tmp_path / "artifacts")
    repo.save(get_savings_balance(version="1.0.0"))
    return repo


@pytest.fixture()
def capability_repo() -> FakeCapabilityRepository:
    return FakeCapabilityRepository()


@pytest.fixture()
def service(capability_repo: FakeCapabilityRepository, artifact_repo: ArtifactRepository) -> CapabilityService:
    return CapabilityService(capability_repo, artifact_repo)


def test_exact_capability_resolution(service: CapabilityService, capability_repo: FakeCapabilityRepository) -> None:
    capability_repo.save(_record())

    resolution = service.resolve(CAPABILITY_ID, _context())

    assert resolution.status == CapabilityResolutionStatus.RESOLVED
    assert resolution.artifact is not None
    assert resolution.artifact.capability_id == CAPABILITY_ID
    assert resolution.matched_record.artifact_version == "1.0.0"
    step = next(s for s in resolution.artifact.steps if s.id == "submit_search")
    assert step.target.primary.params["name"] == "Search"


def test_version_specific_override_is_selected(
    service: CapabilityService, capability_repo: FakeCapabilityRepository, artifact_repo: ArtifactRepository
) -> None:
    capability_repo.save(_record(supported_versions=["1.x"]))
    artifact_repo.save_override(
        VENDOR,
        APPLICATION,
        CAPABILITY_ID,
        ArtifactOverride(
            scope=OverrideScope.VERSION,
            version="1.0.0",
            step_overrides=[StepOverride(step_id="submit_search", target=_role_target("Member Lookup"))],
        ),
    )

    matching = service.resolve(CAPABILITY_ID, _context(version="1.0.0"))
    other = service.resolve(CAPABILITY_ID, _context(version="1.5.0"))

    matching_step = next(s for s in matching.artifact.steps if s.id == "submit_search")
    assert matching_step.target.primary.params["name"] == "Member Lookup"

    other_step = next(s for s in other.artifact.steps if s.id == "submit_search")
    assert other_step.target.primary.params["name"] == "Search"


def test_tenant_specific_override_is_selected(
    service: CapabilityService, capability_repo: FakeCapabilityRepository, artifact_repo: ArtifactRepository
) -> None:
    capability_repo.save(_record(tenant_scope="base"))
    artifact_repo.save_override(
        VENDOR,
        APPLICATION,
        CAPABILITY_ID,
        ArtifactOverride(
            scope=OverrideScope.TENANT,
            tenant_id="cu42",
            step_overrides=[StepOverride(step_id="submit_search", target=_frame_target("#customerFrame"))],
        ),
    )

    for_cu42 = service.resolve(CAPABILITY_ID, _context(tenant_id="cu42"))
    for_other = service.resolve(CAPABILITY_ID, _context(tenant_id="cu99"))

    step_cu42 = next(s for s in for_cu42.artifact.steps if s.id == "submit_search")
    assert step_cu42.target.primary.frame == "#customerFrame"

    step_other = next(s for s in for_other.artifact.steps if s.id == "submit_search")
    assert step_other.target.primary.frame is None


def test_tenant_override_takes_precedence_over_version_override_for_same_step(
    service: CapabilityService, capability_repo: FakeCapabilityRepository, artifact_repo: ArtifactRepository
) -> None:
    capability_repo.save(_record())
    artifact_repo.save_override(
        VENDOR,
        APPLICATION,
        CAPABILITY_ID,
        ArtifactOverride(
            scope=OverrideScope.VERSION,
            version="1.0.0",
            step_overrides=[StepOverride(step_id="submit_search", target=_role_target("Member Lookup"))],
        ),
    )
    artifact_repo.save_override(
        VENDOR,
        APPLICATION,
        CAPABILITY_ID,
        ArtifactOverride(
            scope=OverrideScope.TENANT,
            tenant_id="cu42",
            step_overrides=[StepOverride(step_id="submit_search", target=_frame_target("#customerFrame"))],
        ),
    )

    resolution = service.resolve(CAPABILITY_ID, _context(version="1.0.0", tenant_id="cu42"))

    step = next(s for s in resolution.artifact.steps if s.id == "submit_search")
    assert step.target.primary.frame == "#customerFrame"


def test_incompatible_vendor_or_application_yields_no_match(
    service: CapabilityService, capability_repo: FakeCapabilityRepository
) -> None:
    capability_repo.save(_record())

    resolution = service.resolve(
        CAPABILITY_ID, AppContext(vendor="other-vendor", application=APPLICATION, version="1.0.0", tenant_id="cu42")
    )

    assert resolution.status == CapabilityResolutionStatus.NO_CAPABILITY_MATCH
    assert "vendor" in resolution.reason.lower()


def test_incompatible_version_yields_no_match(
    service: CapabilityService, capability_repo: FakeCapabilityRepository
) -> None:
    capability_repo.save(_record(supported_versions=["1.x"]))

    resolution = service.resolve(CAPABILITY_ID, _context(version="2.0.0"))

    assert resolution.status == CapabilityResolutionStatus.NO_CAPABILITY_MATCH
    assert "version" in resolution.reason.lower()


def test_incompatible_tenant_yields_no_match(
    service: CapabilityService, capability_repo: FakeCapabilityRepository
) -> None:
    capability_repo.save(_record(tenant_scope="cu42"))

    resolution = service.resolve(CAPABILITY_ID, _context(tenant_id="cu99"))

    assert resolution.status == CapabilityResolutionStatus.NO_CAPABILITY_MATCH
    assert "tenant" in resolution.reason.lower()


@pytest.mark.parametrize("status", [CapabilityStatus.DISABLED, CapabilityStatus.DEPRECATED])
def test_disabled_or_deprecated_capability_yields_no_match(
    service: CapabilityService, capability_repo: FakeCapabilityRepository, status: CapabilityStatus
) -> None:
    capability_repo.save(_record(status=status))

    resolution = service.resolve(CAPABILITY_ID, _context())

    assert resolution.status == CapabilityResolutionStatus.NO_CAPABILITY_MATCH
    assert status.value in resolution.reason.lower()


def test_ambiguous_when_multiple_equally_specific_compatible_records(artifact_repo: ArtifactRepository) -> None:
    # Both are "base"-scoped (specificity 0), both active, both compatible
    # with the request -- CapabilityService must refuse to pick one rather
    # than guess. FakeCapabilityRepository is used here on purpose: see its
    # module docstring for why FileCapabilityRepository's own key scheme
    # cannot produce this scenario, and why that does not make the check
    # unnecessary.
    repo = FakeCapabilityRepository([_record(artifact_version="1.0.0"), _record(artifact_version="1.0.0")])
    service = CapabilityService(repo, artifact_repo)

    resolution = service.resolve(CAPABILITY_ID, _context())

    assert resolution.status == CapabilityResolutionStatus.AMBIGUOUS
    assert resolution.artifact is None
    assert "ambiguous" in resolution.reason.lower() or "equally-specific" in resolution.reason.lower()


def test_tenant_specific_record_takes_precedence_over_base_record(
    service: CapabilityService, capability_repo: FakeCapabilityRepository
) -> None:
    capability_repo.save(_record(tenant_scope="base"))
    capability_repo.save(_record(tenant_scope="cu42"))

    for_cu42 = service.resolve(CAPABILITY_ID, _context(tenant_id="cu42"))
    for_other = service.resolve(CAPABILITY_ID, _context(tenant_id="cu99"))

    assert for_cu42.status == CapabilityResolutionStatus.RESOLVED
    assert for_cu42.matched_record.tenant_scope == "cu42"

    assert for_other.status == CapabilityResolutionStatus.RESOLVED
    assert for_other.matched_record.tenant_scope == "base"


def test_no_registered_capability_yields_no_match_not_exception(service: CapabilityService) -> None:
    resolution = service.resolve("nonexistent_capability", _context())

    assert resolution.status == CapabilityResolutionStatus.NO_CAPABILITY_MATCH
    assert resolution.artifact is None
    assert "no capability record registered" in resolution.reason.lower()


def test_capability_record_pointing_at_missing_artifact_version_yields_no_match(
    service: CapabilityService, capability_repo: FakeCapabilityRepository
) -> None:
    capability_repo.save(_record(artifact_version="9.9.9"))

    resolution = service.resolve(CAPABILITY_ID, _context())

    assert resolution.status == CapabilityResolutionStatus.NO_CAPABILITY_MATCH
