"""FileCapabilityRepository storage mechanics: save/list round-trips,
overwrite-on-save for an existing (capability_id, vendor, application,
tenant_scope) key (deliberately the opposite of FileArtifactRepository's
refuse-to-overwrite semantics -- see the module docstring in
capability/repository.py for why), listing scoped to one capability_id vs.
across all of them, and corrupt-file handling mirroring
FileArtifactRepository's own."""

from __future__ import annotations

import pytest

from cuas.capability import CapabilityRecord, CapabilityRepository, CapabilityStatus, FileCapabilityRepository
from cuas.domain import ArtifactInvalidError


@pytest.fixture()
def repo(tmp_path) -> CapabilityRepository:
    return FileCapabilityRepository(tmp_path / "capabilities")


def _record(**overrides) -> CapabilityRecord:
    defaults = dict(
        capability_id="get_savings_balance",
        name="Get Savings Balance",
        vendor="meridian-demo",
        application="credit-union-admin",
        supported_versions=["1.x"],
        tenant_scope="base",
        artifact_version="1.0.0",
        status=CapabilityStatus.ACTIVE,
    )
    defaults.update(overrides)
    return CapabilityRecord(**defaults)


def test_save_then_list_by_capability_id_round_trips(repo: CapabilityRepository) -> None:
    record = _record()
    repo.save(record)

    loaded = repo.list_by_capability_id("get_savings_balance")
    assert len(loaded) == 1
    assert loaded[0].model_dump() == record.model_dump()


def test_saving_same_key_twice_overwrites_rather_than_duplicates(repo: CapabilityRepository) -> None:
    repo.save(_record(status=CapabilityStatus.ACTIVE, artifact_version="1.0.0"))
    repo.save(_record(status=CapabilityStatus.DISABLED, artifact_version="1.1.0"))

    loaded = repo.list_by_capability_id("get_savings_balance")
    assert len(loaded) == 1
    assert loaded[0].status == CapabilityStatus.DISABLED
    assert loaded[0].artifact_version == "1.1.0"


def test_different_tenant_scopes_are_separate_records(repo: CapabilityRepository) -> None:
    repo.save(_record(tenant_scope="base"))
    repo.save(_record(tenant_scope="cu42"))

    loaded = repo.list_by_capability_id("get_savings_balance")
    assert {r.tenant_scope for r in loaded} == {"base", "cu42"}


def test_list_by_capability_id_only_returns_matching_capability(repo: CapabilityRepository) -> None:
    repo.save(_record(capability_id="get_savings_balance"))
    repo.save(_record(capability_id="open_member_record", name="Open Member Record"))

    loaded = repo.list_by_capability_id("get_savings_balance")
    assert [r.capability_id for r in loaded] == ["get_savings_balance"]


def test_list_all_returns_records_across_capabilities(repo: CapabilityRepository) -> None:
    repo.save(_record(capability_id="get_savings_balance"))
    repo.save(_record(capability_id="open_member_record", name="Open Member Record"))

    loaded = repo.list_all()
    assert {r.capability_id for r in loaded} == {"get_savings_balance", "open_member_record"}


def test_list_by_capability_id_for_unknown_id_returns_empty(repo: CapabilityRepository) -> None:
    assert repo.list_by_capability_id("nonexistent") == []


def test_corrupt_capability_file_raises_artifact_invalid(repo: CapabilityRepository) -> None:
    path = repo.save(_record())
    path.write_text('{"not": "a valid capability record"}')

    with pytest.raises(ArtifactInvalidError):
        repo.list_by_capability_id("get_savings_balance")
