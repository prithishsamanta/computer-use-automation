"""FileDiscoveryTraceStore/NullDiscoveryTraceStore in isolation, the same
"simple file-backed layout keyed by run_id" discipline covered directly
for FileEvidenceStore in tests/unit/test_evidence_store.py --
DiscoveryEngine's own tests (test_discovery_engine.py) already prove the
store is wired up and receives correctly-redacted content; this file is
for the storage mechanics on their own.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cuas.discovery.trace import DiscoveryTrace, DiscoveryTraceStep, FileDiscoveryTraceStore, NullDiscoveryTraceStore
from cuas.safety import PolicyDecision


def _trace(run_id: str = "run-1") -> DiscoveryTrace:
    return DiscoveryTrace(
        run_id=run_id,
        capability_id="get_savings_balance",
        goal_description="Find member [REDACTED] and read their savings balance.",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        final_status="success",
        steps=[
            DiscoveryTraceStep(
                step_index=0,
                observation_url="http://demo.invalid/",
                observation_text_excerpt="Member Search",
                raw_model_output="{'action_type': 'fill'}",
                parsed_action={"action_type": "fill", "intent": "search_member"},
                policy_decision=PolicyDecision.ALLOW,
                outcome="executed",
            )
        ],
    )


def test_file_store_round_trips_a_trace(tmp_path) -> None:
    store = FileDiscoveryTraceStore(tmp_path)
    trace = _trace()

    path = store.save(trace)

    assert path == tmp_path / "run-1.json"
    assert path.exists()

    loaded = store.load("run-1")
    assert loaded.run_id == "run-1"
    assert loaded.final_status == "success"
    assert len(loaded.steps) == 1
    assert loaded.steps[0].policy_decision == PolicyDecision.ALLOW
    assert "[REDACTED]" in loaded.goal_description


def test_file_store_load_of_unknown_run_id_raises(tmp_path) -> None:
    store = FileDiscoveryTraceStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.load("no-such-run")


def test_null_store_persists_nothing_and_load_raises(tmp_path) -> None:
    store = NullDiscoveryTraceStore()
    store.save(_trace())  # must not raise, must not write anything

    assert list(tmp_path.iterdir()) == []
    with pytest.raises(FileNotFoundError):
        store.load("run-1")
