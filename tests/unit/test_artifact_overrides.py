"""base + version override + tenant override resolution
(.CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md, "Base Artifact + Overrides"),
mirroring the doc's own worked example: base locator "Member Search" ->
version override "Member Lookup" -> tenant override scoped inside a frame.
A tenant override must win over a version override for the same step, and
an override for a different version/tenant must never apply."""

from __future__ import annotations

import pytest

from cuas.artifact import ArtifactOverride, ArtifactRepository, FileArtifactRepository, OverrideScope, StepOverride, resolve_artifact
from cuas.domain import Locator, LocatorStrategy, Target
from tests.fixtures.sample_artifacts import get_savings_balance


def _role_target(name: str) -> Target:
    return Target(primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params={"role": "button", "name": name}))


def _frame_target(frame: str) -> Target:
    return Target(primary=Locator(strategy=LocatorStrategy.CSS, params={"selector": "button.search"}, frame=frame))


@pytest.fixture()
def repo(tmp_path) -> ArtifactRepository:
    return FileArtifactRepository(tmp_path / "artifacts")


def test_version_override_changes_the_matching_version_only() -> None:
    base = get_savings_balance()
    version_override = ArtifactOverride(
        scope=OverrideScope.VERSION,
        version="1.0.0",
        step_overrides=[StepOverride(step_id="submit_search", target=_role_target("Member Lookup"))],
    )

    resolved_for_matching_version = resolve_artifact(
        base, [version_override], version="1.0.0", tenant_id="anyone"
    )
    resolved_for_other_version = resolve_artifact(
        base, [version_override], version="2.0.0", tenant_id="anyone"
    )

    step = next(s for s in resolved_for_matching_version.steps if s.id == "submit_search")
    assert step.target.primary.params["name"] == "Member Lookup"

    unaffected_step = next(s for s in resolved_for_other_version.steps if s.id == "submit_search")
    assert unaffected_step.target.primary.params["name"] == "Search"


def test_tenant_override_wins_over_version_override_for_same_step() -> None:
    base = get_savings_balance()
    version_override = ArtifactOverride(
        scope=OverrideScope.VERSION,
        version="1.0.0",
        step_overrides=[StepOverride(step_id="submit_search", target=_role_target("Member Lookup"))],
    )
    tenant_override = ArtifactOverride(
        scope=OverrideScope.TENANT,
        tenant_id="cu42",
        step_overrides=[StepOverride(step_id="submit_search", target=_frame_target("#customerFrame"))],
    )

    resolved = resolve_artifact(
        base, [version_override, tenant_override], version="1.0.0", tenant_id="cu42"
    )
    step = next(s for s in resolved.steps if s.id == "submit_search")
    assert step.target.primary.frame == "#customerFrame"


def test_tenant_override_for_a_different_tenant_does_not_apply() -> None:
    base = get_savings_balance()
    tenant_override = ArtifactOverride(
        scope=OverrideScope.TENANT,
        tenant_id="cu42",
        step_overrides=[StepOverride(step_id="submit_search", target=_frame_target("#customerFrame"))],
    )

    resolved = resolve_artifact(base, [tenant_override], version="1.0.0", tenant_id="some-other-cu")
    step = next(s for s in resolved.steps if s.id == "submit_search")
    assert step.target.primary.frame is None


def test_override_referencing_unknown_step_id_raises() -> None:
    base = get_savings_balance()
    bad_override = ArtifactOverride(
        scope=OverrideScope.TENANT,
        tenant_id="cu42",
        step_overrides=[StepOverride(step_id="no-such-step", target=_role_target("X"))],
    )
    with pytest.raises(ValueError):
        resolve_artifact(base, [bad_override], version="1.0.0", tenant_id="cu42")


def test_resolving_does_not_mutate_the_base_artifact() -> None:
    base = get_savings_balance()
    override = ArtifactOverride(
        scope=OverrideScope.TENANT,
        tenant_id="cu42",
        step_overrides=[StepOverride(step_id="submit_search", target=_role_target("Member Lookup"))],
    )
    resolve_artifact(base, [override], version="1.0.0", tenant_id="cu42")

    unchanged_step = next(s for s in base.steps if s.id == "submit_search")
    assert unchanged_step.target.primary.params["name"] == "Search"


def test_repository_round_trips_overrides(repo: ArtifactRepository) -> None:
    override = ArtifactOverride(
        scope=OverrideScope.TENANT,
        tenant_id="cu42",
        step_overrides=[StepOverride(step_id="submit_search", target=_role_target("Member Lookup"))],
    )
    repo.save_override("meridian-demo", "credit-union-admin", "get_savings_balance", override)

    loaded = repo.load_overrides("meridian-demo", "credit-union-admin", "get_savings_balance")
    assert len(loaded) == 1
    assert loaded[0].tenant_id == "cu42"
    assert loaded[0].step_overrides[0].step_id == "submit_search"
