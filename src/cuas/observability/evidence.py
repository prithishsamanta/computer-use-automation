"""Failure evidence: a richer signal than one log line when something
breaks (.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md, "Rich Failure Evidence").
A simple file-backed layout keyed by run_id -- no external evidence
store, blob service, or tracing backend.

IMPORTANT -- screenshots are NOT redacted, and this module does not
pretend otherwise. A structured log line can have a sensitive field
swapped for "[REDACTED]" (see redaction.py) because it is a value we
control the formatting of. A screenshot is a pixel-for-pixel picture of
whatever was actually on screen -- a member ID typed into a search box,
an account balance, a name -- and there is no general, reliable way to
black that out automatically without also destroying the evidence's own
purpose (showing a reviewer what the UI genuinely looked like at the
moment of failure). So:

- Every EvidenceRecord that includes a screenshot is stamped
  `screenshot_is_unredacted_pii_risk=True`. This is not a bug to fix
  later; it is the honest description of what a screenshot is. Treat the
  evidence directory with the same access-control discipline as raw PII
  (do not commit it, do not attach it to a public bug report, restrict
  who can read it) -- exactly like any other place this system
  temporarily has to hold real member data to do its job
  (.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md, "Sensitive Data").
- The *textual* metadata written alongside a screenshot (URL, step id,
  error classification, recent action history) is still subject to the
  same redaction discipline as any other log content -- it just can't
  rely on `redact_inputs` directly, since none of those fields are raw
  artifact inputs. In practice, none of ReplayEngine's own failure paths
  ever put a resolved input value into `error_message`/`recent_actions`
  (Playwright errors describe selectors and timeouts, not filled values),
  so there is nothing further to strip today -- but that is a property of
  what currently constructs those strings, not a guarantee this module
  can make about arbitrary future callers, and should be re-examined if a
  new failure path starts formatting raw field values into error text.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


class EvidenceRecord(BaseModel):
    run_id: str
    step_id: str | None
    captured_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str
    error_code: str | None = None
    error_message: str | None = None
    url: str | None = None
    recent_actions: list[str] = Field(default_factory=list)
    screenshot_path: str | None = None
    dom_snapshot_path: str | None = None
    screenshot_is_unredacted_pii_risk: bool = False


class EvidenceStore(ABC):
    @abstractmethod
    def capture_failure_evidence(
        self,
        *,
        run_id: str,
        step_id: str | None,
        reason: str,
        error_code: str | None,
        error_message: str | None,
        url: str | None,
        screenshot_png: bytes | None,
        dom_snapshot: str | None,
        recent_actions: list[str],
    ) -> EvidenceRecord: ...


class NullEvidenceStore(EvidenceStore):
    """ReplayEngine's default -- evidence capture is additive. A caller
    that hasn't configured a real store still gets a fully functional
    replay: nothing is persisted, nothing crashes."""

    def capture_failure_evidence(
        self,
        *,
        run_id: str,
        step_id: str | None,
        reason: str,
        error_code: str | None,
        error_message: str | None,
        url: str | None,
        screenshot_png: bytes | None,
        dom_snapshot: str | None,
        recent_actions: list[str],
    ) -> EvidenceRecord:
        return EvidenceRecord(
            run_id=run_id,
            step_id=step_id,
            reason=reason,
            error_code=error_code,
            error_message=error_message,
            url=url,
            recent_actions=recent_actions,
        )


class FileEvidenceStore(EvidenceStore):
    """Layout: <root>/<run_id>/<NNN>_<step_id-or-run>.meta.json, plus a
    sibling `.png` when a screenshot was captured and a sibling
    `.dom.json` when an accessibility snapshot was. NNN is a per-run,
    per-store monotonically increasing sequence so multiple failures in
    one run (a step failure, then later an output-extraction failure)
    don't collide."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._sequence: dict[str, int] = {}

    def _next_sequence(self, run_id: str) -> int:
        n = self._sequence.get(run_id, 0) + 1
        self._sequence[run_id] = n
        return n

    def capture_failure_evidence(
        self,
        *,
        run_id: str,
        step_id: str | None,
        reason: str,
        error_code: str | None,
        error_message: str | None,
        url: str | None,
        screenshot_png: bytes | None,
        dom_snapshot: str | None,
        recent_actions: list[str],
    ) -> EvidenceRecord:
        run_dir = self._root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        stem = f"{self._next_sequence(run_id):03d}_{step_id or 'run'}"

        screenshot_path: str | None = None
        if screenshot_png:
            screenshot_path = f"{stem}.png"
            (run_dir / screenshot_path).write_bytes(screenshot_png)

        dom_snapshot_path: str | None = None
        if dom_snapshot:
            dom_snapshot_path = f"{stem}.dom.json"
            (run_dir / dom_snapshot_path).write_text(dom_snapshot, encoding="utf-8")

        record = EvidenceRecord(
            run_id=run_id,
            step_id=step_id,
            reason=reason,
            error_code=error_code,
            error_message=error_message,
            url=url,
            recent_actions=recent_actions,
            screenshot_path=screenshot_path,
            dom_snapshot_path=dom_snapshot_path,
            screenshot_is_unredacted_pii_risk=screenshot_path is not None,
        )
        (run_dir / f"{stem}.meta.json").write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return record
