"""Capability metadata storage (.CLAUDE/07_IMPLEMENTATION_GUIDANCE.md's
suggested `CapabilityRepository` interface). Deliberately mirrors
`ArtifactRepository`'s one-ABC-one-real-implementation shape
(artifact/repository.py), but its `save()` semantics are the opposite on
purpose:

`FileArtifactRepository.save()` refuses to overwrite an existing version --
a published `Artifact` is immutable once versioned
(.CLAUDE/02_ARTIFACT_SCHEMA.md, "Versioning"). A `CapabilityRecord` is not a
versioned artifact; it is a small, mutable pointer that is EXPECTED to
change in place -- flipping `status` to DISABLED, or repointing
`artifact_version` at a newly published version, is ordinary metadata
maintenance, not a violation of anything. So `FileCapabilityRepository.save()`
is idempotent overwrite-on-save, keyed by the combination that uniquely
identifies one record: (capability_id, vendor, application, tenant_scope).
Two records may share a capability_id (a "base" record plus a
tenant-specific one -- see capability/models.py's CapabilityRecord
docstring), but never the same (capability_id, vendor, application,
tenant_scope) tuple; saving that combination again replaces the prior
record rather than erroring, since the caller's action ("register/update
this capability for this scope") did in fact mean the tuple to have that
one, current value.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import ValidationError

from cuas.capability.models import CapabilityRecord
from cuas.domain import ArtifactInvalidError

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_.\-]+")


def _safe_key(value: str) -> str:
    return _SAFE_KEY_RE.sub("_", value)


class CapabilityRepository(ABC):
    @abstractmethod
    def save(self, record: CapabilityRecord) -> Path: ...

    @abstractmethod
    def list_by_capability_id(self, capability_id: str) -> list[CapabilityRecord]:
        """Every registered record (any vendor/application/tenant_scope,
        any status) for this capability_id. CapabilityService.resolve is
        responsible for filtering/ranking these by compatibility -- this
        method does no filtering of its own."""

    @abstractmethod
    def list_all(self) -> list[CapabilityRecord]: ...


class FileCapabilityRepository(CapabilityRepository):
    """Plain JSON files on disk, one per (capability_id, vendor,
    application, tenant_scope), so a diff of a capability being
    disabled/repointed is a readable git diff -- consistent with
    FileArtifactRepository's own rationale.

    Layout under `root`:
        <capability_id>/<vendor>__<application>__<tenant_scope>.json
    """

    def __init__(self, root: str | Path):
        self._root = Path(root)

    def _path(self, record: CapabilityRecord) -> Path:
        capability_dir = self._root / _safe_key(record.capability_id)
        key = f"{_safe_key(record.vendor)}__{_safe_key(record.application)}__{_safe_key(record.tenant_scope)}"
        return capability_dir / f"{key}.json"

    def save(self, record: CapabilityRecord) -> Path:
        path = self._path(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(record.model_dump_json(indent=2) + "\n")
        return path

    def list_by_capability_id(self, capability_id: str) -> list[CapabilityRecord]:
        capability_dir = self._root / _safe_key(capability_id)
        if not capability_dir.exists():
            return []
        return self._load_all(sorted(capability_dir.glob("*.json")))

    def list_all(self) -> list[CapabilityRecord]:
        if not self._root.exists():
            return []
        return self._load_all(sorted(self._root.glob("*/*.json")))

    def _load_all(self, paths: list[Path]) -> list[CapabilityRecord]:
        records = []
        for path in paths:
            try:
                records.append(CapabilityRecord.model_validate_json(path.read_text()))
            except ValidationError as exc:
                raise ArtifactInvalidError(f"{path} failed schema validation: {exc}") from exc
        return records
