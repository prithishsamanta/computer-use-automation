"""JsonlEventSink: one append-only JSON-Lines file per run_id, and
NullEventSink's guarantee that it does nothing at all."""

from __future__ import annotations

import json

from cuas.observability import EventType, JsonlEventSink, NullEventSink, RunEvent


def test_jsonl_sink_appends_one_line_per_event_in_the_docs_suggested_shape(tmp_path) -> None:
    sink = JsonlEventSink(tmp_path)
    sink.record(RunEvent(run_id="run-1", event=EventType.RUN_STARTED, details={"capability_id": "get_savings_balance"}))
    sink.record(RunEvent(run_id="run-1", event=EventType.RUN_COMPLETED, status="success", details={"status": "success"}))

    lines = (tmp_path / "run-1.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["run_id"] == "run-1"
    assert first["component"] == "replay_engine"
    assert first["event"] == "run_started"
    assert first["details"] == {"capability_id": "get_savings_balance"}
    assert "timestamp" in first and "step_id" in first and "status" in first

    second = json.loads(lines[1])
    assert second["event"] == "run_completed"
    assert second["status"] == "success"


def test_jsonl_sink_keys_separate_runs_into_separate_files(tmp_path) -> None:
    sink = JsonlEventSink(tmp_path)
    sink.record(RunEvent(run_id="run-a", event=EventType.RUN_STARTED))
    sink.record(RunEvent(run_id="run-b", event=EventType.RUN_STARTED))
    sink.record(RunEvent(run_id="run-a", event=EventType.RUN_COMPLETED))

    assert len((tmp_path / "run-a.jsonl").read_text().strip().splitlines()) == 2
    assert len((tmp_path / "run-b.jsonl").read_text().strip().splitlines()) == 1


def test_null_sink_persists_nothing(tmp_path) -> None:
    sink = NullEventSink()
    sink.record(RunEvent(run_id="run-1", event=EventType.RUN_STARTED))
    assert list(tmp_path.iterdir()) == []
