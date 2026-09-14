# 02 — Expected business outcome (member not found)

**Real run, real Docker container, real Playwright browser**, same
capability and artifact version as scenario 01, with an input chosen to
hit the artifact's declared `MEMBER_NOT_FOUND` business outcome rather
than an error.

- **run_id:** `fab1522275624dfb850b47e9fe08b045`
- **capability:** `get_savings_balance`, artifact version `1.0.1`
- **input:** member ID `no-such-member` (deliberately absent from the
  seeded demo data, `demo_app/data.py`)
- **outcome:** `business_outcome`
- **business_outcome_code:** `MEMBER_NOT_FOUND`

## Files

- `fab1522275624dfb850b47e9fe08b045.jsonl` — real, unmodified event log
  copied from `data/logs/`. Shows the same navigate/fill/click sequence
  and the same automatic `SESSION_NOTICE_POPUP` recovery as scenario 01,
  followed by `business_outcome_detected` (`MEMBER_NOT_FOUND`) and a
  terminal `run_completed` / `status: business_outcome` event — this is
  the artifact's own declared `business_outcomes[0]` (detected via a
  `TEXT_PRESENT` checkpoint for "Member not found." in
  `tests/fixtures/sample_artifacts.py`), not an error path.
- `reconstructed_run_response.json` — same caveat as scenario 01: not a
  captured HTTP response body (the system persists none), but a
  reconstruction of the real `RunResponse` shape from this run's own log
  and `RunOrchestrator`'s BUSINESS_OUTCOME branch — see the file's own
  `_note` field.

## What this demonstrates

That a well-modeled, expected negative business result (as opposed to a
system/automation failure) is classified and reported distinctly — the
run completes cleanly with `outcome: business_outcome`, not `failed` —
which is the behavior `.CLAUDE`'s business-outcome modeling exists for.
