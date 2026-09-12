"""FileInterventionRepository storage mechanics: save/get round-trips,
overwrite-on-save for an existing intervention id (same "small, mutable
record expected to change in place" rationale as
FileCapabilityRepository -- see the module docstring in
handoff/repository.py), pending-vs-all listing, unknown-id handling, and
corrupt-file handling mirroring FileArtifactRepository/
FileCapabilityRepository's own.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cuas.domain import ArtifactInvalidError, InterventionStatus
from cuas.handoff import FileInterventionRepository, InterventionRepository, InterventionRequest


@pytest.fixture()
def repo(tmp_path) -> InterventionRepository:
    return FileInterventionRepository(tmp_path / "interventions")


def _request(**overrides) -> InterventionRequest:
    defaults = dict(
        id="intv-1",
        run_id="run-1",
        capability_id="get_savings_balance",
        tenant_id="cu42",
        reason="operator approval required",
        session_id="sess-1",
        status=InterventionStatus.PENDING,
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return InterventionRequest(**defaults)


def test_save_then_get_round_trips(repo: InterventionRepository) -> None:
    request = _request()
    repo.save(request)

    loaded = repo.get("intv-1")
    assert loaded.model_dump() == request.model_dump()


def test_saving_same_id_twice_overwrites_rather_than_duplicates(repo: InterventionRepository) -> None:
    repo.save(_request(status=InterventionStatus.PENDING, claimed_by=None))
    repo.save(_request(status=InterventionStatus.CLAIMED, claimed_by="teller-1"))

    loaded = repo.get("intv-1")
    assert loaded.status == InterventionStatus.CLAIMED
    assert loaded.claimed_by == "teller-1"
    assert len(repo.list_all()) == 1


def test_list_pending_only_returns_pending_status(repo: InterventionRepository) -> None:
    repo.save(_request(id="intv-1", status=InterventionStatus.PENDING))
    repo.save(_request(id="intv-2", status=InterventionStatus.CLAIMED, claimed_by="teller-1"))
    repo.save(_request(id="intv-3", status=InterventionStatus.RESOLVED, resolved_at=datetime.now(timezone.utc)))

    pending = repo.list_pending()
    assert [r.id for r in pending] == ["intv-1"]


def test_list_all_returns_every_intervention(repo: InterventionRepository) -> None:
    repo.save(_request(id="intv-1"))
    repo.save(_request(id="intv-2"))

    loaded = repo.list_all()
    assert {r.id for r in loaded} == {"intv-1", "intv-2"}


def test_get_unknown_id_raises_key_error(repo: InterventionRepository) -> None:
    with pytest.raises(KeyError):
        repo.get("no-such-intervention")


def test_corrupt_intervention_file_raises_artifact_invalid(repo: InterventionRepository, tmp_path) -> None:
    repo.save(_request())
    path = tmp_path / "interventions" / "intv-1.json"
    path.write_text('{"not": "a valid intervention request"}')

    with pytest.raises(ArtifactInvalidError):
        repo.get("intv-1")
