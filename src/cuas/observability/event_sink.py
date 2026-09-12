"""Where structured RunEvents go. No external logging stack, tracing
backend, or message broker (explicit constraint, .CLAUDE/09) -- just an
abstraction ReplayEngine depends on, and one small file-backed
implementation. Same shape as SurfaceAdapter/PolicyEngine/
ArtifactRepository elsewhere in this codebase: one ABC, one real
implementation, room for more later without touching callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from cuas.observability.events import RunEvent


class EventSink(ABC):
    @abstractmethod
    def record(self, event: RunEvent) -> None: ...


class NullEventSink(EventSink):
    """ReplayEngine's default when no sink is configured. Observability is
    additive: a caller that hasn't wired one up still gets a fully
    functional, unchanged replay -- events are simply not recorded
    anywhere, nothing crashes or behaves differently."""

    def record(self, event: RunEvent) -> None:
        return None


class JsonlEventSink(EventSink):
    """One JSON-Lines file per run: <root>/<run_id>.jsonl. Append-only,
    human-diffable, trivially greppable by run_id or event type -- the
    "simple file-backed layout keyed by run_id" this phase asks for,
    nothing heavier."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def record(self, event: RunEvent) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / f"{event.run_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")
