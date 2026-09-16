# 05 — Genuine LLM-driven discovery, end to end

**Real Anthropic model, real Docker container, real Playwright browser,
real human-in-the-loop approvals.** No step below is scripted, mocked, or
replayed from a fixture. This is the one scenario in this repo where an
LLM is actually deciding what action to try next against a live, unknown
page — everything in scenarios 01-04 replays a pre-built artifact with no
model in the loop at all.

- **discovery run_id:** `1ce8002332b94aeaa4397eff7e0fb1e0`
- **session_id:** `9a6f732008eb4b2dbdd1a48496d95031`
- **capability discovered:** `discover_savings_balance_demo` (no
  capability record existed for it before this run — see the log's own
  `discovery_required` event, `reason: "no capability record registered
  for 'discover_savings_balance_demo'"`)
- **goal given to the model:** "Read the member's savings account
  balance from the accounts table shown on this page (the row where the
  account type is Savings) and report its value."
- **target page:** `http://demo-app:8080/members/M1001/accounts`
  (synthetic demo data — Meridian Credit Union, member M1001, accounts
  A-5001/Checking/$2340.10 and A-5002/Savings/$18204.55)
- **outcome:** `success`, `discovered_new_capability: true`,
  `artifact_version: "1.0.0"`
- **standalone deterministic replay run_id:**
  `060199268dd84a509d98f2c04a320511` — a separate, later run against the
  same artifact, `outcome: success`, `discovered_new_capability: false`,
  zero discovery/Anthropic events

## What actually happened, step by step

Every claim below is quoted directly from
`discovery_trace_1ce8002332b94aeaa4397eff7e0fb1e0.json` (the model's own
reasoning at each turn) and cross-checked against the matching events in
`1ce8002332b94aeaa4397eff7e0fb1e0.jsonl` (the structured run log). Five
of the six proposed actions were `safe`-risk reads still gated by policy
as `require_approval` — see `.CLAUDE`'s policy model — so each one paused
for a real operator approval before execution; those are the
`intervention_*` files below.

1. **Step 0 — invalid locator, approved, fails at execution.** The model
   proposed `tr:has(td:first-child:contains('Savings')) td:nth-child(3)`
   (reasoning: "The Savings row shows a balance of $18204.55 in the
   third column"). `:contains()` is a jQuery extension, not valid
   Playwright/browser CSS. Operator `teller-1` approved the intent via
   `intervention_1e6ac22f351142c7a00c0e36dd8f8599` and resumed the run;
   execution then failed for real:
   `Locator.wait_for: SyntaxError: ... 'td:first-child:contains("Savings")' is not a valid selector.`
   Screenshot + DOM snapshot captured automatically at the failure:
   `001_failed_read_attempt_1.{png,dom.json,meta.json}`.
2. **Step 1 — retries the same mistake with a different selector, fails
   the same way.** New selector
   `tr:has(td:contains("Savings")) td:nth-child(3)` — still uses
   `:contains()`. Approved via `intervention_92427483b9a44290875fb0c452aa3eed`,
   fails identically at execution
   (`002_failed_read_attempt_2.{png,dom.json,meta.json}`). This is the
   locator-recovery evidence: two genuinely different invalid-CSS
   attempts, both surfaced back to the model as real execution errors
   (via the sanitized-error feedback path from commit `cfefe84`), not
   swallowed or retried automatically.
3. **Step 2 — switches to a valid selector, executes, but reads the
   wrong (header) text.** The model abandons `:contains()` entirely for
   `tr:has(td:nth-child(1)) td:nth-child(3)`. Approved via
   `intervention_4c353fc5f3d04227a633f6c7eaa15829`, this one **executes
   successfully** — but the selector matches the table's header row, so
   `read_value` is the literal string `"Balance"` (the column header),
   not a balance figure.
4. **Step 3 — the model catches its own first mistake, but its fix is
   still wrong.** Before proposing the next action, the model's own
   reasoning says: *"Although I previously proposed a READ that executed
   with read_value=\"Balance\" (which was likely the column header), I
   now need to read the actual savings account balance value."* This is
   the model recognizing, unprompted, that a technically-successful read
   was semantically wrong. Its fix, `tr:nth-child(2) td:nth-child(3)`,
   is approved via `intervention_b32876c297d94b9eaf4fc37a215b6853` and
   executes — but `tr:nth-child(2)` is the **Checking** row, not Savings,
   so `read_value` comes back `"$2340.10"`: a real number, still the
   wrong account.
5. **Step 4 — the model catches its second mistake and gets it right.**
   Reasoning: *"the Savings account (A-5002) actually has a balance of
   $18204.55, not $2340.10. The $2340.10 value appears to be from the
   Checking account row."* This time it abandons row-position CSS
   entirely and targets the exact rendered cell text via an
   accessibility-role locator (`role_name`, role `cell`, name
   `"$18204.55"`). Approved via
   `intervention_73c89f150a1d4070889802364922f639`, executes, `read_value
   = "$18204.55"` — the correct savings balance.
6. **Step 5 — the model declares done.** Reasoning: *"Since the READ has
   already been executed and the correct value has been captured, I
   should mark the goal as done."* No further action is proposed; the
   discovery engine reports `run_completed status: success`.

That is two rounds of locator-syntax recovery (steps 0-1 → 2) followed by
two rounds of semantic recovery the model performed entirely on its own
reasoning, unprompted by any test harness (step 2's header value caught
before step 3; step 3's wrong-row value caught before step 4) — ending in
a correct, verified read.

## Artifact creation and same-run verification replay

Immediately after `discovery_engine`'s `run_completed status: success`,
`run_orchestrator` logs `capability_match_found`
(`newly_discovered: true, artifact_version: "1.0.0"`) — the new artifact
at
`data/artifacts/meridian-demo/credit-union-admin/discover_savings_balance_demo/1.0.0.json`
and its capability record at
`data/capabilities/discover_savings_balance_demo/meridian-demo__credit-union-admin__base.json`
(both already tracked in this repo at those paths; not duplicated here
since they're reviewable in place, same as `data/artifacts/` generally).
`RunOrchestrator` then runs its own internal verification replay against
a fresh surface, in the **same** `run_id`'s log: `replay_engine`
`run_started` → `artifact_loaded` → the single declared step
(`navigate_to_discovery_start`) → `policy_checked decision: allow` →
`action_executed` → `run_completed status: success`. This is still part
of `1ce8002332b94aeaa4397eff7e0fb1e0.jsonl` — see its final ten lines.

## Deterministic replay, a separate run, zero LLM involvement

`060199268dd84a509d98f2c04a320511.jsonl` is a **different, later run**
against the now-registered artifact — not the same-run verification
replay above. Its complete event sequence is `capability_search_started`
→ `capability_match_found` (no `newly_discovered` field at all, since
this capability already existed) → `replay_engine` (`run_started`,
`artifact_loaded`, `step_started`/`policy_checked decision: allow`/
`action_executed` for `navigate_to_discovery_start`, `run_completed
status: success`) → `run_orchestrator run_completed` (`outcome: success`,
`discovered_new_capability: false`). There is no `discovery_engine`
component anywhere in this file, no Anthropic call, and no per-step
human approval — this is exactly the artifact-replay path scenarios
01-04 already demonstrate, now shown for the artifact this scenario's
own discovery produced.

## Files

- `1ce8002332b94aeaa4397eff7e0fb1e0.jsonl` — the full, real, unmodified
  discovery run log, copied verbatim from `data/logs/`. Covers
  everything above: all five approval cycles, both execution failures,
  both evidence captures, the successful discovery completion, and the
  embedded same-run verification replay.
- `060199268dd84a509d98f2c04a320511.jsonl` — the full, real, unmodified
  standalone replay run log, copied verbatim from `data/logs/`. Proves
  replay of a discovered artifact needs no LLM/discovery involvement at
  all.
- `discovery_trace_1ce8002332b94aeaa4397eff7e0fb1e0.json` — the real,
  unmodified discovery trace, copied verbatim from
  `data/discovery_traces/` (gitignored at that path since it's raw
  runtime output, same treatment as `data/logs/`). This is the only file
  that contains the model's actual reasoning text and raw proposed
  actions turn-by-turn — the direct evidence for the recovery narrative
  above.
- `interventions/step{0..4}_<id>.json` — the five real, persisted
  intervention records this run created, copied verbatim from
  `data/interventions/`, one per proposed action. All five are
  `status: resolved`, `claimed_by: "teller-1"`. (These are distinct from
  — and this run's records do not touch — the small number of unrelated
  intervention records elsewhere in `data/interventions/` that are still
  genuinely `pending` from earlier, separate investigation runs; those
  are left exactly as they are, everywhere in this repo, including here.)
- `001_failed_read_attempt_1.{png,dom.json,meta.json}` and
  `002_failed_read_attempt_2.{png,dom.json,meta.json}` — the real
  screenshot, accessibility-tree DOM snapshot, and capture metadata
  automatically recorded at each of the two execution failures (steps 0
  and 1), copied verbatim from `data/evidence/1ce8002332b94aeaa4397eff7e0fb1e0/`.
  **Synthetic demo data only** — the underlying log flags these
  `"screenshot_is_unredacted_pii_risk": true`, which is the system
  correctly identifying what *would* be a PII risk for real member data;
  it is safe to commit here only because `demo_app/data.py`'s seeded
  members (M1001 "Jane Doe", account A-5001/A-5002) are entirely
  fictional, same disclaimer as `evidence/03`.
- `final_api_result.json` — the API-facing result of the discovery run,
  reconstructed the same way (and with the same honesty caveat) as
  scenarios 01/02's `reconstructed_run_response.json` — see its own
  `_note` field.

The already-tracked, non-duplicated files this scenario also depends on:
`data/artifacts/meridian-demo/credit-union-admin/discover_savings_balance_demo/1.0.0.json`
(the materialized artifact — genuinely produced by this run's
`ArtifactBuilder` call and preserved exactly as produced; **not**
regenerated or edited for this submission, including its known
imperfections, see "Known limitation" below) and
`data/capabilities/discover_savings_balance_demo/meridian-demo__credit-union-admin__base.json`
(its capability record).

## What this demonstrates

- **Genuine Anthropic-driven discovery against a live UI.** Every
  proposed action in `discovery_trace_...json` carries real
  `raw_model_output` text with the model's own free-form reasoning —
  this is not a scripted sequence.
- **Recovery from a failed locator.** Steps 0 and 1 both fail with a
  real, distinct Playwright execution error (invalid `:contains()` CSS),
  fed back to the model, which changes its selector strategy each time.
- **Semantic recovery from a wrong-but-executing read.** Step 2 executes
  cleanly but reads `"Balance"` (a header, not a value); the model
  identifies this itself before proposing step 3.
- **Semantic recovery from a wrong-account read.** Step 3 executes and
  reads a real number, `"$2340.10"`, from the wrong (Checking) row; the
  model identifies this itself before proposing step 4.
- **A successful, correct final read.** Step 4 reads `"$18204.55"`, the
  actual Savings balance, via a locator strategy (`role_name` on the
  literal cell text) distinct from every prior attempt.
- **Artifact creation and registration.** `capability_match_found` with
  `newly_discovered: true` and a real, inspectable artifact + capability
  record now exist on disk (see the paths above).
- **Deterministic replay of that exact artifact.** Both the embedded
  same-run verification replay and the fully separate
  `060199268dd84a509d98f2c04a320511` run replay the artifact this
  discovery produced.
- **Replay without any LLM/discovery involvement.** Neither replay
  contains a `discovery_engine` event, an Anthropic call, or a per-step
  human approval — only `replay_engine` deterministically executing the
  artifact's declared step and output extractors.

## Known limitation in this specific artifact

This artifact (`1.0.0`, `provenance.created_from_run:
"1ce8002332b94aeaa4397eff7e0fb1e0"`) is preserved exactly as this run
produced it — **it is not regenerated or cleaned up for this
submission.** Two honest imperfections are visible directly in
`data/artifacts/.../discover_savings_balance_demo/1.0.0.json`:

- All three executed reads (steps 2-4) were retained as declared
  `outputs` — including `savings_account_balance` (`"Balance"`, the
  header text) and `savings_account_balance_value` (`"$2340.10"`, the
  wrong account) alongside the correct
  `savings_balance_from_savings_row` (`"$18204.55"`). The exploratory
  wrong turns are outputs of the artifact, not just steps in the trace
  the model recovered from.
- `success_condition` points at `savings_account_balance` — the
  **first**-produced output (the header text), not the correct,
  last-produced one. Because `"Balance"` is still a non-empty string,
  `OUTPUT_VALID` passes on it, so replay of this artifact reports
  success without the last output ever being what success was checked
  against.

Commit `1357e6f` (already in this repository, see `git log` /
`DECISIONS_LOG.md`) fixed `ArtifactBuilder` to anchor
`success_condition` at the **last** produced output for any future
discovery run — but, deliberately, that fix was not applied
retroactively to this already-materialized artifact, since doing so
would mean regenerating or hand-editing genuine evidence rather than
preserving it as produced. `REPORT.md`'s Limitations section covers this
in full, along with why semantic pruning of the wrong exploratory
outputs and generalized locator synthesis remain future work rather
than something this fix (or this submission) attempts.
