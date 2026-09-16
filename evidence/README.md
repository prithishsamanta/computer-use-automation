# Evidence index

Curated evidence for behavior that has actually been implemented and
verified against the real system — the real `RunOrchestrator`/API, a real
Playwright browser, and (for scenarios 03/04) the real Phase 13
headed-browser/Xvfb/noVNC Docker handoff path on the developer's actual
Mac. Nothing here is generated from a mock or fake, and nothing is
labeled as verified beyond what was actually run and observed.

Raw runtime data under `data/` (logs, evidence captures, intervention
records) is untouched by this folder — everything here is a **curated
copy** of a subset of it, kept for readability and to avoid committing
raw runtime directories wholesale. `data/logs/` currently also contains
several earlier reproduction runs from diagnosing and fixing the
`get_savings_balance` orchestrator-lifecycle bug (see
`DECISIONS_LOG.md`); those are intentionally not curated here since they
predate the fix and don't represent current, correct behavior.

`data/discovery_traces/` and `data/interventions/` are also gitignored
runtime output for the same reason `data/logs/` and `data/evidence/` are
-- `05_live_llm_discovery/` curates copies of the specific discovery
trace and intervention records that run produced, same treatment as the
logs/evidence curated elsewhere in this folder.

| Folder | Scenario | Run ID | Outcome |
|---|---|---|---|
| [`01_deterministic_replay/`](01_deterministic_replay/) | Successful deterministic replay | `8781cbc67b0e42ad985f7750f4f09a84` | `success`, savings_balance `18204.55` |
| [`02_business_outcome/`](02_business_outcome/) | Expected business outcome | `fab1522275624dfb850b47e9fe08b045` | `business_outcome` / `MEMBER_NOT_FOUND` |
| [`03_hard_failure_and_diagnostics/`](03_hard_failure_and_diagnostics/) | Hard replay failure + captured diagnostics | `720f35e5933645f3920ae893b3f1bb64` | `failed` / `CHECKPOINT_FAILED` → intervention |
| [`04_human_handoff_and_resume/`](04_human_handoff_and_resume/) | Same-session human handoff/resume (two real flows) | `92c86535dca14af0aa14f9e2d8f5942f` (A) and `720f35e5933645f3920ae893b3f1bb64` (B, same run as 03) | both resume to `success` |
| [`05_live_llm_discovery/`](05_live_llm_discovery/) | Genuine LLM-driven discovery, recovery, artifact creation, deterministic replay | `1ce8002332b94aeaa4397eff7e0fb1e0` (discovery) / `060199268dd84a509d98f2c04a320511` (replay) | both `success`; discovery `discovered_new_capability: true`, replay `false` |

All member/account data referenced anywhere in this evidence (`M1001`
Jane Doe, `M1002`/`M1004` John/Mark Smith, account `A-5001`, etc.) is
synthetic demo data seeded by `demo_app/data.py` for this take-home's
fictional "Meridian Credit Union" demo app — none of it is real.

## A note on API response bodies

Scenarios 01 and 02 include a `reconstructed_run_response.json`. The
running system does not persist raw HTTP response bytes anywhere on disk
(there is no `data/runs/` or similar store), so these are not captured
copies of what the API actually returned over the wire — each is
reconstructed by mapping that run's own real, persisted event log onto
the real `RunResponse` schema (`src/cuas/api/schemas.py`) and
`RunOrchestrator`'s own result-construction code, using only field values
already confirmed by that log or explicitly reported by the developer
after issuing the request for real. Each file says this plainly in its
own `_note` field. `05_live_llm_discovery/` also includes one
(`final_api_result.json`), reconstructed the same way, for the discovery
run's own final outcome. Scenarios 03/04 rely on the real event log and
intervention records directly rather than a reconstructed response,
since the interesting evidence there is the escalation/resume mechanics,
not a single terminal response.

## Supporting (secondary) evidence

`tests/integration/test_run_orchestrator_e2e.py` is the regression test
added while fixing the bug these three `get_savings_balance` runs exposed
and then verified against; it exercises the real `RunOrchestrator` with a
real Playwright surface against the demo app (not just `ReplayEngine` in
isolation, and not `FakeSurfaceAdapter`) for exactly the three outcomes
captured here (success, business outcome, hard-failure/intervention). It
is mentioned here as corroborating evidence, not as a substitute for the
real runtime evidence above — see `DECISIONS_LOG.md`'s "Bugfix (during
Phase 14 evidence capture)" entry for the full writeup and verification
results.
