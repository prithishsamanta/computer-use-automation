# 01 — Deterministic replay success

**Real run, real Docker container, real Playwright browser.** No FakeLLM,
no FakeSurfaceAdapter — this is the actual `get_savings_balance` capability
replayed through the real `RunOrchestrator` and demo app.

- **run_id:** `8781cbc67b0e42ad985f7750f4f09a84`
- **capability:** `get_savings_balance`, artifact version `1.0.1` (the
  fixed, deployment-scoped version — see `DECISIONS_LOG.md`'s "Bugfix
  (during Phase 14 evidence capture)" entry for why `1.0.1` exists and why
  `1.0.0` is left on disk unchanged)
- **input:** member ID `M1001` (synthetic demo data — Jane Doe,
  `demo_app/data.py`)
- **outcome:** `success`
- **output:** `savings_balance = 18204.55`

## Files

- `8781cbc67b0e42ad985f7750f4f09a84.jsonl` — the real, unmodified
  structured event log for this run, copied verbatim from
  `data/logs/`. Shows the full step sequence (navigate → fill → click),
  one automatic recovery from a seeded session-notice popup
  (`SESSION_NOTICE_POPUP`, a recoverable condition the artifact declares
  on purpose), and the terminal `run_completed` / `status: success` event.
  Note `inputs.member_id` is logged as `"[REDACTED]"` — the artifact marks
  `member_id` as a sensitive input, and the real logging layer redacts it
  before writing to disk; this is the system's own real redaction
  behavior, not something curated after the fact.
- `reconstructed_run_response.json` — see the `_note` field inside that
  file. The system does not persist the raw HTTP response body it once
  sent back from `POST /runs`, so this is not a captured copy of that
  response — it is rebuilt from this run's own persisted event log,
  mapped onto the real `RunResponse` schema (`src/cuas/api/schemas.py`)
  and `RunOrchestrator`'s own result-construction code. Every field value
  traces back to either the log above or the `savings_balance` value the
  developer reported after actually issuing this request through the real
  container.

## What this demonstrates

That a resolved capability replays deterministically end-to-end through
the real orchestration/API/browser stack — including transparent recovery
from a known transient UI condition — and produces the correct,
schema-declared output.
