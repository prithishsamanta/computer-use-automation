"""FileArtifactRepository: versioned save/load, immutability of a published
version, corrupt-file handling, and the capability listing CapabilityService
(Phase 10) will filter on."""

from __future__ import annotations

import pytest

from cuas.artifact import ArtifactRepository, FileArtifactRepository
from cuas.domain import ArtifactInvalidError
from tests.fixtures.sample_artifacts import get_savings_balance


@pytest.fixture()
def repo(tmp_path) -> ArtifactRepository:
    return FileArtifactRepository(tmp_path / "artifacts")


def test_save_then_load_round_trips(repo: ArtifactRepository) -> None:
    artifact = get_savings_balance()
    repo.save(artifact)

    loaded = repo.load("meridian-demo", "credit-union-admin", "get_savings_balance", "1.0.0")
    assert loaded.model_dump() == artifact.model_dump()


def test_saving_same_version_twice_is_rejected(repo: ArtifactRepository) -> None:
    artifact = get_savings_balance()
    repo.save(artifact)
    with pytest.raises(FileExistsError):
        repo.save(artifact)


def test_list_versions_sorted(repo: ArtifactRepository) -> None:
    repo.save(get_savings_balance(version="1.0.0"))
    repo.save(get_savings_balance(version="1.10.0"))
    repo.save(get_savings_balance(version="1.2.0"))

    versions = repo.list_versions("meridian-demo", "credit-union-admin", "get_savings_balance")
    assert versions == ["1.0.0", "1.2.0", "1.10.0"]


def test_load_missing_version_raises_file_not_found(repo: ArtifactRepository) -> None:
    with pytest.raises(FileNotFoundError):
        repo.load("meridian-demo", "credit-union-admin", "get_savings_balance", "9.9.9")


def test_load_corrupt_artifact_raises_artifact_invalid(repo: ArtifactRepository, tmp_path) -> None:
    artifact = get_savings_balance()
    path = repo.save(artifact)
    path.write_text('{"not": "a valid artifact"}')

    with pytest.raises(ArtifactInvalidError):
        repo.load("meridian-demo", "credit-union-admin", "get_savings_balance", "1.0.0")


def test_list_capabilities(repo: ArtifactRepository) -> None:
    repo.save(get_savings_balance())
    assert repo.list_capabilities() == [("meridian-demo", "credit-union-admin", "get_savings_balance")]
