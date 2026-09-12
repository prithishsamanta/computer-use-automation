"""FileEvidenceStore: simple file-backed failure evidence keyed by
run_id, and the explicit "screenshots are not redacted" acknowledgement
(screenshot_is_unredacted_pii_risk) this phase specifically asked to be
verified rather than assumed."""

from __future__ import annotations

import json

from cuas.observability import FileEvidenceStore, NullEvidenceStore


def test_capture_failure_evidence_writes_screenshot_dom_snapshot_and_meta(tmp_path) -> None:
    store = FileEvidenceStore(tmp_path)

    record = store.capture_failure_evidence(
        run_id="run-1",
        step_id="submit_search",
        reason="checkpoint_failed",
        error_code="CHECKPOINT_FAILED",
        error_message="Accounts panel never appeared",
        url="http://demo.invalid/members/M1001",
        screenshot_png=b"\x89PNG-fake-bytes",
        dom_snapshot='{"role": "WebArea"}',
        recent_actions=["fill_member_id:step_executed", "submit_search:policy_check"],
    )

    run_dir = tmp_path / "run-1"
    assert record.screenshot_path is not None
    assert record.dom_snapshot_path is not None
    assert (run_dir / record.screenshot_path).read_bytes() == b"\x89PNG-fake-bytes"
    assert (run_dir / record.dom_snapshot_path).read_text() == '{"role": "WebArea"}'

    meta_files = list(run_dir.glob("*.meta.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text())
    assert meta["run_id"] == "run-1"
    assert meta["step_id"] == "submit_search"
    assert meta["error_code"] == "CHECKPOINT_FAILED"
    assert meta["recent_actions"] == ["fill_member_id:step_executed", "submit_search:policy_check"]


def test_a_captured_screenshot_is_explicitly_flagged_as_an_unredacted_pii_risk(tmp_path) -> None:
    """The core requirement this phase called out specifically: redaction
    for text is straightforward, but a screenshot is a picture of
    whatever was on screen and cannot be automatically sanitized. This
    must be stated as an explicit flag on the record, not left implicit."""

    store = FileEvidenceStore(tmp_path)
    record = store.capture_failure_evidence(
        run_id="run-1", step_id="a", reason="step_failed", error_code="X", error_message="m",
        url=None, screenshot_png=b"real-pixels", dom_snapshot=None, recent_actions=[],
    )
    assert record.screenshot_is_unredacted_pii_risk is True


def test_no_screenshot_means_no_pii_risk_flag_and_no_extra_files(tmp_path) -> None:
    store = FileEvidenceStore(tmp_path)
    record = store.capture_failure_evidence(
        run_id="run-1", step_id=None, reason="output_extraction_failed", error_code="OUTPUT_EXTRACTION_FAILED",
        error_message="boom", url=None, screenshot_png=None, dom_snapshot=None, recent_actions=[],
    )

    assert record.screenshot_path is None
    assert record.dom_snapshot_path is None
    assert record.screenshot_is_unredacted_pii_risk is False

    run_dir = tmp_path / "run-1"
    files = list(run_dir.iterdir())
    assert len(files) == 1
    assert files[0].name.endswith(".meta.json")


def test_sequence_numbers_avoid_collisions_within_a_run(tmp_path) -> None:
    store = FileEvidenceStore(tmp_path)
    common = dict(
        run_id="run-1", step_id="a", reason="step_failed", error_code="X", error_message="m",
        url=None, screenshot_png=None, dom_snapshot=None, recent_actions=[],
    )
    store.capture_failure_evidence(**common)
    store.capture_failure_evidence(**common)

    meta_files = sorted(p.name for p in (tmp_path / "run-1").glob("*.meta.json"))
    assert len(meta_files) == 2
    assert meta_files[0] != meta_files[1]


def test_null_evidence_store_persists_nothing(tmp_path) -> None:
    store = NullEvidenceStore()
    record = store.capture_failure_evidence(
        run_id="run-1", step_id="a", reason="step_failed", error_code="X", error_message="m",
        url="http://x.invalid", screenshot_png=b"bytes", dom_snapshot="{}", recent_actions=["a:b"],
    )

    assert record.screenshot_path is None
    assert record.screenshot_is_unredacted_pii_risk is False
    assert list(tmp_path.iterdir()) == []
