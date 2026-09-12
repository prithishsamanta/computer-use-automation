"""Persisted raw discovery trace (.CLAUDE/03_DISCOVERY_AND_REPLAY.md:
"Persist: full discovery trace: everything tried"; .CLAUDE/08 decision #6:
"The raw discovery trace is not the artifact. The artifact is a cleaned,
validated successful workflow. Failed exploration remains only in the
trace/evidence.") This module is deliberately dumb: DiscoveryEngine
decides what happened and is responsible for redacting every free-text
field before constructing a DiscoveryTraceStep; this module only writes
the result down. No code here (or anywhere else in this phase) ever
builds an Artifact from a DiscoveryTrace -- that is a later, separate
artifact-construction step's job, so a trace can preserve every failed
detour without that unreliability leaking into what eventually gets
replayed.

Same shape as ArtifactRepository/EvidenceStore elsewhere in this codebase:
one ABC, one real (file-backed) implementation, one no-op default.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from cuas.safety import PolicyDecision


class DiscoveryTraceStep(BaseModel):
    """Everything about one discovery-loop iteration worth keeping for
    later inspection or artifact-construction. Every string field here is
    expected to already be redacted (DiscoveryEngine calls
    cuas.observability.redaction.redact_text/redact_dict on each of these
    before constructing this object -- see engine.py) -- this type does
    not redact anything itself, it only carries what it's given."""

    step_index: int
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    observation_url: str
    observation_text_excerpt: str
    raw_model_output: str | None = None
    parsed_action: dict[str, Any] | None = None
    parse_error: str | None = None
    policy_decision: PolicyDecision | None = None
    execution_error: str | None = None
    outcome: str = ""
    read_value: str | None = Field(
        default=None,
        description="The raw string read() returned for a READ action, redacted like every "
        "other free-text field here -- carried so artifact construction (Phase 9) can infer "
        "a typed output from what was actually read.",
    )


class DiscoveryTrace(BaseModel):
    run_id: str
    capability_id: str
    goal_description: str
    started_at: datetime
    finished_at: datetime | None = None
    final_status: str | None = None
    steps: list[DiscoveryTraceStep] = Field(default_factory=list)


class DiscoveryTraceStore(ABC):
    @abstractmethod
    def save(self, trace: DiscoveryTrace) -> Any: ...

    @abstractmethod
    def load(self, run_id: str) -> DiscoveryTrace: ...


class NullDiscoveryTraceStore(DiscoveryTraceStore):
    """DiscoveryEngine's default -- persistence is additive, exactly like
    NullEventSink/NullEvidenceStore. A caller that hasn't wired up a real
    store still gets a fully functional discovery run; the trace is
    simply not written anywhere."""

    def save(self, trace: DiscoveryTrace) -> None:
        return None

    def load(self, run_id: str) -> DiscoveryTrace:
        raise FileNotFoundError(f"NullDiscoveryTraceStore persists nothing; no trace for {run_id!r}")


class FileDiscoveryTraceStore(DiscoveryTraceStore):
    """Layout: <root>/<run_id>.json -- one file per run, the same "plain
    JSON on disk, keyed by run_id" discipline as FileEvidenceStore/
    JsonlEventSink. A single JSON document (not JSONL) because a trace is
    naturally one growing document per run, written once at the end of a
    run rather than appended event-by-event during it."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def save(self, trace: DiscoveryTrace) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{trace.run_id}.json"
        path.write_text(trace.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return path

    def load(self, run_id: str) -> DiscoveryTrace:
        path = self._root / f"{run_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"no discovery trace at {path}")
        return DiscoveryTrace.model_validate_json(path.read_text())
