"""InterventionRepository: the seam RunOrchestrator creates escalations
through (.CLAUDE/07_IMPLEMENTATION_GUIDANCE.md's suggested
`InterventionRepository` interface). Same one-ABC-one-real-implementation
shape as every other repository in this codebase.

Phase 11 shipped only `InMemoryInterventionRepository`, deliberately as a
placeholder ("Keep intervention creation behind an interface for now if
the full persistence implementation is Phase 12"). Phase 12 adds the real
one: `FileInterventionRepository`, plain JSON files on disk, one per
intervention id -- the same "small, mutable record that is expected to
change in place" rationale `FileCapabilityRepository` already uses
(status moves PENDING -> CLAIMED -> RESOLVED/CANCELLED over the record's
life; each transition is a `save()` that overwrites the same file, so a
git diff of one intervention's history reads the same way a capability's
does). `InMemoryInterventionRepository` stays -- it is still exactly
right for fast, in-process unit tests that don't care about surviving a
restart, the same reason `FakeCapabilityRepository` sits alongside
`FileCapabilityRepository` rather than replacing it.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import ValidationError

from cuas.domain import ArtifactInvalidError, InterventionStatus
from cuas.handoff.models import InterventionRequest

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_.\-]+")


def _safe_key(value: str) -> str:
    return _SAFE_KEY_RE.sub("_", value)


class InterventionRepository(ABC):
    @abstractmethod
    def save(self, request: InterventionRequest) -> None: ...

    @abstractmethod
    def get(self, intervention_id: str) -> InterventionRequest: ...

    @abstractmethod
    def list_pending(self) -> list[InterventionRequest]: ...

    @abstractmethod
    def list_all(self) -> list[InterventionRequest]: ...


class InMemoryInterventionRepository(InterventionRepository):
    def __init__(self) -> None:
        self._requests: dict[str, InterventionRequest] = {}

    def save(self, request: InterventionRequest) -> None:
        self._requests[request.id] = request

    def get(self, intervention_id: str) -> InterventionRequest:
        try:
            return self._requests[intervention_id]
        except KeyError as exc:
            raise KeyError(f"no intervention request with id {intervention_id!r}") from exc

    def list_pending(self) -> list[InterventionRequest]:
        return [r for r in self._requests.values() if r.status == InterventionStatus.PENDING]

    def list_all(self) -> list[InterventionRequest]:
        return list(self._requests.values())


class FileInterventionRepository(InterventionRepository):
    """Plain JSON files on disk, one per intervention id.

    Layout under `root`:
        <intervention_id>.json

    Unlike `FileArtifactRepository` (which refuses to overwrite a
    version -- a published Artifact is immutable) but exactly like
    `FileCapabilityRepository`, `save()` here is idempotent
    overwrite-on-save: an `InterventionRequest` is a single mutable
    record whose `status`/`claimed_by`/`resolved_at` are expected to
    change in place as an operator claims and resolves it, keyed by its
    own `id` (never re-keyed on any other field), so "save this id again"
    always means "this is the current state of that same intervention."
    """

    def __init__(self, root: str | Path):
        self._root = Path(root)

    def _path(self, intervention_id: str) -> Path:
        return self._root / f"{_safe_key(intervention_id)}.json"

    def save(self, request: InterventionRequest) -> None:
        path = self._path(request.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(request.model_dump_json(indent=2) + "\n")

    def get(self, intervention_id: str) -> InterventionRequest:
        path = self._path(intervention_id)
        if not path.exists():
            raise KeyError(f"no intervention request with id {intervention_id!r}")
        return self._load(path)

    def list_pending(self) -> list[InterventionRequest]:
        return [r for r in self.list_all() if r.status == InterventionStatus.PENDING]

    def list_all(self) -> list[InterventionRequest]:
        if not self._root.exists():
            return []
        return [self._load(path) for path in sorted(self._root.glob("*.json"))]

    def _load(self, path: Path) -> InterventionRequest:
        try:
            return InterventionRequest.model_validate_json(path.read_text())
        except ValidationError as exc:
            raise ArtifactInvalidError(f"{path} failed schema validation: {exc}") from exc
