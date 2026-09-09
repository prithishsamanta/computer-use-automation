"""Versioned, reviewable artifact storage (.CLAUDE/02_ARTIFACT_SCHEMA.md,
"Versioning"; .CLAUDE/08 decision #10). Plain JSON files on disk, one per
version, so a diff of two versions is a readable git diff -- not a
database blob. `ArtifactRepository` is the interface CapabilityService
(Phase 10) and ReplayEngine (Phase 5) depend on; `FileArtifactRepository`
is the only implementation this take-home needs.

Layout under `root`:
    <vendor>/<application>/<capability_id>/<version>.json
    <vendor>/<application>/<capability_id>/overrides/<scope>-<key>.json
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path

from pydantic import ValidationError

from cuas.artifact.overrides import ArtifactOverride, OverrideScope
from cuas.artifact.schema import Artifact
from cuas.domain import ArtifactInvalidError

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_.\-]+")


def _safe_key(value: str) -> str:
    return _SAFE_KEY_RE.sub("_", value)


class ArtifactRepository(ABC):
    @abstractmethod
    def save(self, artifact: Artifact) -> Path: ...

    @abstractmethod
    def load(self, vendor: str, application: str, capability_id: str, version: str) -> Artifact: ...

    @abstractmethod
    def list_versions(self, vendor: str, application: str, capability_id: str) -> list[str]: ...

    @abstractmethod
    def save_override(
        self, vendor: str, application: str, capability_id: str, override: ArtifactOverride
    ) -> Path: ...

    @abstractmethod
    def load_overrides(
        self, vendor: str, application: str, capability_id: str
    ) -> list[ArtifactOverride]: ...

    @abstractmethod
    def list_capabilities(self) -> list[tuple[str, str, str]]:
        """All (vendor, application, capability_id) triples with at least
        one saved version. Used by CapabilityService's metadata filter
        (.CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md, "Capability Retrieval")."""


class FileArtifactRepository(ArtifactRepository):
    def __init__(self, root: str | Path):
        self._root = Path(root)

    def _capability_dir(self, vendor: str, application: str, capability_id: str) -> Path:
        return self._root / _safe_key(vendor) / _safe_key(application) / _safe_key(capability_id)

    def save(self, artifact: Artifact) -> Path:
        capability_dir = self._capability_dir(
            artifact.application.vendor, artifact.application.application, artifact.capability_id
        )
        capability_dir.mkdir(parents=True, exist_ok=True)
        path = capability_dir / f"{_safe_key(artifact.version)}.json"
        if path.exists():
            raise FileExistsError(
                f"{artifact.capability_id} version {artifact.version} already exists at {path}; "
                "bump the version instead of overwriting a published artifact "
                "(.CLAUDE/02_ARTIFACT_SCHEMA.md, Versioning)"
            )
        path.write_text(artifact.model_dump_json(indent=2) + "\n")
        return path

    def load(self, vendor: str, application: str, capability_id: str, version: str) -> Artifact:
        path = self._capability_dir(vendor, application, capability_id) / f"{_safe_key(version)}.json"
        if not path.exists():
            raise FileNotFoundError(f"no artifact at {path}")
        try:
            return Artifact.model_validate_json(path.read_text())
        except ValidationError as exc:
            raise ArtifactInvalidError(f"{path} failed schema validation: {exc}") from exc

    def list_versions(self, vendor: str, application: str, capability_id: str) -> list[str]:
        capability_dir = self._capability_dir(vendor, application, capability_id)
        if not capability_dir.exists():
            return []
        versions = [p.stem for p in capability_dir.glob("*.json")]
        return sorted(versions, key=lambda v: tuple(int(part) for part in v.split(".")))

    def save_override(
        self, vendor: str, application: str, capability_id: str, override: ArtifactOverride
    ) -> Path:
        overrides_dir = self._capability_dir(vendor, application, capability_id) / "overrides"
        overrides_dir.mkdir(parents=True, exist_ok=True)
        key = override.version if override.scope == OverrideScope.VERSION else override.tenant_id
        path = overrides_dir / f"{override.scope.value}-{_safe_key(key)}.json"
        path.write_text(override.model_dump_json(indent=2) + "\n")
        return path

    def load_overrides(self, vendor: str, application: str, capability_id: str) -> list[ArtifactOverride]:
        overrides_dir = self._capability_dir(vendor, application, capability_id) / "overrides"
        if not overrides_dir.exists():
            return []
        overrides = []
        for path in sorted(overrides_dir.glob("*.json")):
            try:
                overrides.append(ArtifactOverride.model_validate_json(path.read_text()))
            except ValidationError as exc:
                raise ArtifactInvalidError(f"{path} failed schema validation: {exc}") from exc
        return overrides

    def list_capabilities(self) -> list[tuple[str, str, str]]:
        if not self._root.exists():
            return []
        triples = []
        for version_file in self._root.glob("*/*/*/*.json"):
            capability_dir = version_file.parent
            capability_id, application_dir, vendor_dir = (
                capability_dir.name,
                capability_dir.parent.name,
                capability_dir.parent.parent.name,
            )
            triples.append((vendor_dir, application_dir, capability_id))
        return sorted(set(triples))
