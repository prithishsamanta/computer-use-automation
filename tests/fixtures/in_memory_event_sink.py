"""An in-memory EventSink test double. Complements JsonlEventSink (the
real file-backed implementation, covered directly by
tests/unit/test_event_sink.py) -- this is for asserting *what*
ReplayEngine emitted, in what order, and correlated by which run_id,
without parsing JSONL files back off disk for every assertion.
"""

from __future__ import annotations

from cuas.observability.event_sink import EventSink
from cuas.observability.events import EventType, RunEvent


class InMemoryEventSink(EventSink):
    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def record(self, event: RunEvent) -> None:
        self.events.append(event)

    def events_of(self, event_type: EventType) -> list[RunEvent]:
        return [e for e in self.events if e.event == event_type]
