# 04 — Human handoff and resume (two real, distinct flows)

Both flows below were run for real, on the developer's actual Mac,
against the Dockerized stack with the headed-browser/Xvfb/noVNC path from
Phase 13 — an operator genuinely interacted with the *same live browser
session* the paused automation had been using, and the run resumed under
its original `run_id` and produced the reported final outcome. Nothing in
this folder is simulated or replayed from a script.

They demonstrate two **different** resume mechanisms that exist for two
different reasons, which is worth calling out plainly rather than
glossing over:

- `ReplayResult.escalation_step_id` is only ever set (to the paused
  step's id) when the escalation is `APPROVAL_REQUIRED`. For a hard
  `FAILED` escalation it is always `None` — there is no single "the step
  to re-run" concept for an unmodeled failure. `RunOrchestrator._run_replay`
  resolves `resume_step_id` from `escalation_step_id or
  ReplayEngine.RESUME_AFTER_ALL_STEPS`, so the two branches necessarily
  resume differently.

## A — `close_member_account`: APPROVAL_REQUIRED → approve → resume

- **run_id:** `92c86535dca14af0aa14f9e2d8f5942f`
- **intervention_id:** `cb2396bae1b74d6abab7b13095ba69cc`
- **session_id:** `c896ebe1b323405aae0f08539bb4b817`
- The `confirm_close` step (intent `close_account`, policy decision
  `require_approval`) pauses the run with `approval_required` and opens an
  intervention.
- Files: `close_member_account_approval_required/92c86535dca14af0aa14f9e2d8f5942f.jsonl`
  (full real log) and `.../intervention_cb2396bae1b74d6abab7b13095ba69cc.json`
  (the persisted intervention record, `status: resolved`,
  `claimed_by: teller-1`).
- **Resume mechanism:** because this is an `APPROVAL_REQUIRED` escalation,
  `escalation_step_id` is `confirm_close`, so resume re-enters the replay
  loop **at that exact step**. The log shows this precisely — on resume,
  `confirm_close` is `step_started` again, its policy check now returns
  `decision: skipped_on_resume` (approval was already granted; it is not
  re-asked), and the automation itself performs the actual click
  (`action_executed`), passes the checkpoint, and completes with
  `status: success`. The human approved; the automation still executes
  the approved action.

## B — `get_savings_balance`: hard FAILED → operator resolves in noVNC → resume

- **run_id:** `720f35e5933645f3920ae893b3f1bb64`
- **intervention_id:** `d40a68d8004d49b0a898b893f139a93f`
- **session_id:** `07e576a073e446fcad356a2a05a44f6f`
- Full failure detail, screenshot, and DOM snapshot for this run live in
  `../03_hard_failure_and_diagnostics/` (same run — that folder covers the
  initial `CHECKPOINT_FAILED` half; this section covers the claim →
  human-resolution → resume half of the same log file).
- **Resume mechanism:** because this is a `FAILED` escalation (an
  unmodeled ambiguous match, not an approval gate), `escalation_step_id`
  is `None`, so resume uses `ReplayEngine.RESUME_AFTER_ALL_STEPS` — there
  is no single safe step to re-execute automatically. The operator
  resolved the ambiguity themselves, live, in the same noVNC browser
  session (manually navigating from the ambiguous "Smith" search results
  to member `M1002`'s account view — synthetic demo data), then marked
  human control complete. The relevant tail of
  `../03_hard_failure_and_diagnostics/720f35e5933645f3920ae893b3f1bb64.jsonl`
  (`intervention_claimed` → `human_control_completed` → `run_resumed` →
  resumed `run_started` → `run_completed status: success`, with **no**
  intervening `step_started` events) is quoted below verbatim:

  ```json
  {"timestamp":"2026-09-13T21:42:30.900384Z","run_id":"720f35e5933645f3920ae893b3f1bb64","component":"run_orchestrator","event":"intervention_claimed","step_id":null,"status":"info","details":{"intervention_id":"d40a68d8004d49b0a898b893f139a93f","claimed_by":"teller-1"}}
  {"timestamp":"2026-09-13T21:45:56.370064Z","run_id":"720f35e5933645f3920ae893b3f1bb64","component":"run_orchestrator","event":"human_control_completed","step_id":null,"status":"info","details":{"intervention_id":"d40a68d8004d49b0a898b893f139a93f","operator_id":"teller-1"}}
  {"timestamp":"2026-09-13T21:46:54.531312Z","run_id":"720f35e5933645f3920ae893b3f1bb64","component":"run_orchestrator","event":"run_resumed","step_id":null,"status":"info","details":{"intervention_id":"d40a68d8004d49b0a898b893f139a93f","session_id":"07e576a073e446fcad356a2a05a44f6f","origin":"replay"}}
  {"timestamp":"2026-09-13T21:46:54.532356Z","run_id":"720f35e5933645f3920ae893b3f1bb64","component":"replay_engine","event":"run_started","step_id":null,"status":"info","details":{"capability_id":"get_savings_balance","version":"1.0.1","tenant_id":"base","resumed":true,"inputs":{"member_id":"[REDACTED]"}}}
  {"timestamp":"2026-09-13T21:46:54.583333Z","run_id":"720f35e5933645f3920ae893b3f1bb64","component":"replay_engine","event":"run_completed","step_id":null,"status":"success","details":{"status":"success"}}
  {"timestamp":"2026-09-13T21:46:54.643204Z","run_id":"720f35e5933645f3920ae893b3f1bb64","component":"run_orchestrator","event":"run_completed","step_id":null,"status":"success","details":{"outcome":"success","capability_id":"get_savings_balance","discovered_new_capability":false,"session_id":null}}
  ```

  The developer separately reported the final read-back value after this
  resume: `savings_balance = 9900.00` for member `M1002` — that figure is
  not itself present in the structured log (the log confirms the *event
  sequence and outcome*, not the extracted decimal value), so it is
  reported here as the developer's observation of the real result, not as
  something independently re-derived from the files in this repo.

## What together these demonstrate

The same underlying guarantee — a paused run's `run_id`/`session_id`
identity is preserved across a real human handoff, and resume continues
the *original* run rather than starting a new one — holds under two
structurally different resume paths: automation-re-executes-the-approved-
step (A), and human-resolves-then-automation-just-re-validates (B).
