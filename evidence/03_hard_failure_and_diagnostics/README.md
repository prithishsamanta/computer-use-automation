# 03 — Hard replay failure with captured diagnostic evidence

**Real run, real Docker container, real Playwright browser.** Member ID
`Smith` matches two seeded members (`M1002` John Smith, `M1004` Mark
Smith — `demo_app/data.py`), a genuinely unmodeled ambiguous state the
artifact has no declared business outcome for. The checkpoint after
`submit_search` (waiting for "Accounts" to appear) never passes, even
after the same automatic `SESSION_NOTICE_POPUP` recovery seen in
scenarios 01/02 is attempted twice, so the step fails for real.

- **run_id:** `720f35e5933645f3920ae893b3f1bb64`
- **capability:** `get_savings_balance`, artifact version `1.0.1`
- **input:** member ID `Smith`
- **failed step:** `submit_search`
- **error_code:** `CHECKPOINT_FAILED`
- **resulting escalation:** `intervention_id`
  `d40a68d8004d49b0a898b893f139a93f`, `session_id`
  `07e576a073e446fcad356a2a05a44f6f`

This is the same run that scenario 04's "B" flow resumes — this folder
covers the failure and its captured diagnostics; **04** covers the
claim → human resolution → resume → success sequence for the same
`run_id`.

## Files

- `720f35e5933645f3920ae893b3f1bb64.jsonl` — the full, real, unmodified
  event log for this run, copied verbatim from `data/logs/`. Includes
  both halves: the initial failure (through `intervention_requested` /
  `run_completed status: failed`) covered by this scenario, and the later
  resume (`intervention_claimed` → `human_control_completed` →
  `run_resumed` → `run_completed status: success`) that scenario 04's "B"
  flow describes.
- `001_submit_search.png` — the real screenshot captured automatically at
  the moment of `checkpoint_failed`, by the system's own evidence-capture
  path (`replay_engine`'s `evidence_captured` event). **Synthetic demo
  data only** — this is the seeded fictional credit-union demo app
  (`demo_app/`), showing two fictional "Smith" search results (`M1002` /
  `M1004`), not any real institution or person. The underlying log entry
  flags this screenshot `"screenshot_is_unredacted_pii_risk": true`; that
  flag is the system correctly identifying it *would* be a PII risk for
  real member data, which is exactly why it is only being committed here
  because the data behind it is synthetic and fictional, per the seeded
  demo dataset.
- `001_submit_search.dom.json` — the accessibility-tree DOM snapshot
  captured at the same moment, same synthetic-data caveat.
- `001_submit_search.meta.json` — the real capture metadata (timestamp,
  reason, error, URL, recent action trail) written alongside the
  screenshot/DOM snapshot.
- `intervention_d40a68d8004d49b0a898b893f139a93f.json` — the real,
  persisted intervention record created by this failure, copied verbatim
  from `data/interventions/`. Contains no PII — only the run/session IDs,
  the failure reason, and (once resolved) `claimed_by: "teller-1"` and a
  `resolved_at` timestamp.

## What this demonstrates

That an automation failure the system cannot resolve on its own —
distinct from both a clean business outcome (scenario 02) and a policy
refusal — is (a) detected via its declared checkpoint rather than
silently misreported as success, (b) automatically captured as
screenshot + DOM + structured metadata *before* the run hands off, and
(c) turned into a durable, claimable intervention record rather than
simply erroring out.
