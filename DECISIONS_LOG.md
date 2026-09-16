# Development Decisions Log — Computer-Use Automation System

This file is the running source of truth for *how* this project is being
built (as opposed to `.CLAUDE/*.md`, which is the *design spec* / source of
truth for *what* it should be). It exists so that context can be
compressed/summarized at any point without losing decisions, constraints,
or workflow rules. **Update this file at the end of every phase** — append
to the Phase Log, and edit the sections above it only if a standing
decision actually changes.

Repo: https://github.com/prithishsamanta/Computer-Use-Automation-System.git
Default branch: `main`.

---

## 1. Authoritative sources

- `.CLAUDE/01` through `.CLAUDE/09_CLAUDE_START_PROMPT.md` (11 files) are
  the **authoritative design spec**. Do not silently redesign the
  architecture described there. If something in the spec needs to change,
  state the conflict/trade-off explicitly and propose the smallest change
  — don't just diverge quietly.
- Explicitly flexible (spec leaves these to implementation judgment):
  locator representation, exact timeouts/retry counts, exact artifact
  field names, persistence details, demo UI specifics.
- This file (`DECISIONS_LOG.md`) is *not* spec — it's a log of choices
  made while implementing the spec, and of process/workflow rules the user
  has given directly.

## 2. Hard constraints (standing, do not violate without being asked)

- **No distributed infra** (Celery, RabbitMQ, Kafka, etc.) unless a
  concrete need arises. Normal execution is direct synchronous
  orchestration with async I/O where useful.
- **Human handoff is asynchronous and persisted**, not an in-memory queue.
- **LLM is discovery-only.** Deterministic replay never lets an LLM choose
  the next action — it executes the saved artifact.
- **LLM is never the final safety authority.** Every proposed action
  passes through deterministic policy enforcement regardless of what the
  LLM proposed.
- **Consequential/risky banking actions require an authorized institution
  operator** (human-in-the-loop approval).
- **`ReplayEngine` depends only on a surface abstraction** (`SurfaceAdapter`
  ABC), never directly on Playwright.
- **Preserve OOP boundaries / dependency inversion** throughout — strategy
  pattern for `SurfaceAdapter`, `PolicyEngine`, `ArtifactRepository`, each
  with exactly one real implementation so far and room for more.
- **Keep the raw discovery trace separate from the cleaned artifact** —
  two distinct concepts, not one blurred one.
- **Build incrementally.** Add/update tests alongside each component, run
  the relevant tests after each change, keep regressions green before
  starting the next phase, add integration/e2e tests as component
  boundaries become real (not all up front).
- **Lightweight GitHub Actions CI**, set up early, not bolted on at the end.
- **Dockerize from the beginning.** Docker/Docker Compose is the canonical
  reproducible demo path. Keep Docker simple. No Kubernetes.

## 3. Process / workflow rules (from the user directly)

- **Never push to GitHub automatically.** Only the user pushes, from their
  own terminal. I commit locally with a clear message and report the
  commit hash; I do not run `git push` myself unless explicitly told to in
  that exact turn.
- **All real file editing happens in the user's connected folder**
  (`$HOME/mnt/Computer_Use_Automation_System` via
  `mcp__remote-devices__device_bash`), not duplicated in the cloud
  sandbox. The connected folder is the single source of truth. The cloud
  sandbox is used only as scratch space for things the device can't do
  (see §5).
- **Don't stop at the discovery/LLM phase for lack of `ANTHROPIC_API_KEY`.**
  Build the `LLMClient` abstraction and discovery logic against
  mocks/fakes first; only pause when a *live* discovery run actually needs
  the real key.
- **Docker is installed and working on the user's real Mac.** The
  connected-folder sandbox does not expose the Docker CLI/socket — never
  treat Docker as "unavailable" because of that. Write real
  Dockerfile/Compose content, mark container execution as "unverified from
  this sandbox" in comments, and let the user run
  `docker compose build`/`up` themselves. (Confirmed working as of Phase
  13: the user ran `docker compose build`/`up` for real and reported
  success — see that phase's Phase Log entry. The rule itself still
  stands for every future Docker change: write it, flag it as
  sandbox-unverified, let the user run and confirm it, then update the
  relevant comments/README once they do — don't leave a stale
  "unverified" claim sitting in the repo once it's actually been
  verified.)
- **Per-phase loop**: implement → write/extend tests → run tests → (for
  anything needing a real browser) verify via the cloud-sandbox round-trip
  (§5) → commit locally with a detailed message → report results and wait
  for the user to say "continue" before starting the next phase.

## 4. Key architecture decisions made while implementing

- `Action`/`Target`/`Locator`/`AppContext`/`RiskLevel` live in
  `domain/models.py` as the shared vocabulary discovery, artifacts, and
  replay all reuse — deliberately not three separate concepts. `Step`
  (artifact schema) *extends* `Action` rather than duplicating its fields.
- `OutputSpec.source`, `BusinessOutcome.detect`, and
  `RecoverableCondition.detect` all reuse `Target`/`WaitCondition` rather
  than inventing parallel "how do I find something on the surface"
  concepts.
- Artifact override resolution (base + version + tenant) is a small,
  explicit patch mechanism — not a general-purpose inheritance engine.
  Lives in `artifact/overrides.py`, separate from the schema
  (`artifact/schema.py`) and storage (`artifact/repository.py`).
- Error taxonomy: `AutomationError` subclasses tagged with an `ErrorCode`
  enum (`domain/errors.py`) — `ARTIFACT_INVALID`, `TARGET_NOT_FOUND`,
  `CHECKPOINT_FAILED`, `SESSION_EXPIRED`, `PERMISSION_DENIED`,
  `OUTPUT_EXTRACTION_FAILED`. `ReplayEngine` never lets one of these
  escape uncaught — every expected automation-domain failure becomes a
  structured `ReplayResult`, not a raised exception the caller has to
  guess about.
- Policy: `RiskLevel` (SAFE / APPROVAL_REQUIRED / BLOCKED) on each step,
  mapped 1:1 by `RiskBasedPolicyEngine` to `PolicyDecision` (ALLOW /
  REQUIRE_APPROVAL / DENY). This mapping is a **deliberate Phase 5
  placeholder** — Phase 6 is expected to make this a fuller layered engine
  (e.g. tenant/context-aware rules), without changing `ReplayEngine`'s
  dependency on the `PolicyEngine` ABC.
- `ReplayEngine.run()` per-step order is: policy check → execute action
  (bounded to exactly one recovery attempt on failure) → if a checkpoint is
  declared, verify it (checking known `business_outcomes` *before*
  attempting recovery, only raising `CheckpointFailedError` if neither the
  checkpoint nor a business outcome nor one recovery attempt resolves it).
  This ordering (business outcome before recovery before hard failure) was
  not fully specified by the spec — decided and documented in code
  comments, since a business outcome is a valid non-error state and
  shouldn't be masked by a recovery attempt that isn't relevant to it.
- Recovery is bounded **per call**, not per run: `_attempt_recovery` makes
  at most one recovery attempt each time it's invoked, but it can be
  invoked more than once in a single run (once from a failed action, once
  more from a failed checkpoint) — this is intentional, not a loophole; it
  still can never spin, because each individual call is capped at one
  attempt and one retry.
- The demo app's dismissible session-notice popup is modeled as a
  `RecoverableCondition` (`SESSION_NOTICE_POPUP`, recovery=DISMISS), *not*
  as a hardcoded step in the artifact — per the principle that an artifact
  should encode only the clean deterministic happy path, and recovery
  should be reactive/general rather than baked into every capability that
  happens to hit it.
- Test-double pattern: `FakeSurfaceAdapter`
  (`tests/fixtures/fake_surface.py`) is a scriptable in-memory
  `SurfaceAdapter` for fast unit tests of `ReplayEngine`'s own branching
  logic (policy branches, bounded recovery, error classification) that
  would be awkward to force reliably against a real UI every time. Real
  Playwright-driven integration tests exist alongside it for what a fake
  can't prove (real actionability failures, real popups, real timing).
- pytest markers (`pyproject.toml`): `integration` (needs a real browser,
  never an LLM — runs in CI) and `live_llm` (needs a real
  `ANTHROPIC_API_KEY` — excluded from CI and from default local runs).

## 5. Sandbox/tooling workarounds (why things are done the way they are)

- **Cross-sandbox Playwright verification.** The device's connected-folder
  shell can't install a Playwright browser (`sudo` blocked in that VM, and
  `cdn.playwright.dev` is blocked by its network allowlist either way). The
  cloud sandbox *does* have a pre-installed Chromium and full network
  access, so the verification workflow for anything needing a real browser
  is: tar the repo on-device (excluding `.venv`/`.git`/`__pycache__`/`data`)
  → stage the tarball into the cloud sandbox via
  `device_stage_files`/`device_commit_files` (placing it briefly inside the
  connected folder, since only paths under a connected folder can be
  staged) → extract and run pytest for real in the cloud sandbox → report
  results → delete the temporary tarball from both sides. Authoring never
  happens in the cloud sandbox, only verification.
- **Playwright version pin.** The cloud sandbox's pre-installed Chromium is
  revision 1194. `pip`'s newer `playwright` releases expect a newer
  revision and try to download it, which is also blocked by the network
  allowlist. Bisected pip-downloadable `playwright` versions against their
  bundled `browsers.json` to find the exact version matching revision
  1194: **`playwright==1.56.0`**, pinned in `pyproject.toml`. Do not bump
  this casually — bumping it means re-doing the bisection or finding
  another way to get a matching browser revision.
- **`device_bash` can't delete files by default.** Deleting inside the
  connected folder (e.g. git's `.lock` files, or a temporary staged
  tarball) requires `device_request_delete_permission` once per session;
  already granted for this project's folder.
- **Git identity is local, not global**, on the device
  (`git config user.name/email`, no `--global`).
- **`class X(str, Enum)`, not `StrEnum`** — the device venv is Python 3.10;
  `enum.StrEnum` is 3.11+ only.

## 6. 15-phase plan and status

1. ✅ Project skeleton: domain models, FastAPI health route, Docker/CI
   scaffolding — commit `188fcf3`.
2. ✅ Demo legacy credit-union admin app + its Docker/Compose service —
   commit `bdc1bdb`.
3. ✅ `SurfaceAdapter` abstraction + real `PlaywrightSurfaceAdapter` —
   commit `d3ed433`.
4. ✅ Artifact schema + base/version/tenant override resolution +
   file-backed `ArtifactRepository` — commit `304be22`.
5. ✅ Deterministic `ReplayEngine` (safety-policy seam, bounded recovery,
   checkpoints, business outcomes, typed output extraction) — commit
   `ec85f4b`.
6. ✅ Fuller layered `PolicyEngine` (global -> app/vendor -> tenant),
   replacing Phase 5's 1:1 `RiskBasedPolicyEngine` placeholder as the real
   runtime policy (see Phase Log).
7. ✅ Structured logging / evidence capture (observability package).
8. ✅ LLM discovery loop, built and tested against fakes first (see Phase Log).
9. ✅ Artifact builder (discovery run → cleaned, typed artifact; see Phase Log).
10. ✅ Capability service (context/version/tenant resolution at call time; see Phase Log).
11. ✅ Run orchestrator + API (RunOrchestrator control-flow layer + thin FastAPI /runs route; see Phase Log).
12. ✅ Intervention / human-handoff persistence + async resume (`SessionRegistry` keeps the live surface open across escalation; see Phase Log).
13. ✅ Full Docker setup for handoff: Xvfb + x11vnc + noVNC inside the
    automation container (see Phase Log).
14. ⬜ Scenario capture / demo recordings.
15. ⬜ README + final report.

## 7. Known pitfalls already hit once (avoid repeating)

- Writing/committing in the cloud sandbox instead of the connected folder
  — rejected by the user; the connected folder is the only place real work
  happens.
- `from enum import StrEnum` — ImportError on the device's Python 3.10.
- `playwright install --with-deps chromium` / `playwright install
  chromium` — both fail on-device (`sudo` blocked; network allowlist blocks
  `cdn.playwright.dev`). Use the cloud-sandbox round-trip instead of
  fighting this on-device.
- Unpinned/loosely-pinned `playwright` in `pyproject.toml` pulls a version
  expecting a browser revision the cloud sandbox doesn't have
  pre-installed, and it can't download the right one either. Keep the
  `playwright==1.56.0` pin unless a new matching revision is confirmed.
- `git commit` failing with "unable to unlink '.git/index.lock':
  Operation not permitted" — needs
  `mcp__remote-devices__device_request_delete_permission` granted once.

---

## Phase Log

### Phase 5 — Deterministic ReplayEngine (commit `ec85f4b`)

- Built `src/cuas/replay/engine.py` (`ReplayEngine`, `ReplayResult`,
  `ReplayStatus`, `StepLogEntry`) exactly per §4 above.
- Built `src/cuas/safety/policy.py` (`PolicyEngine` ABC,
  `RiskBasedPolicyEngine`, `PolicyDecision`) as the Phase 5 placeholder
  policy seam.
- Extended `SurfaceAdapter.click`/`fill` with `timeout_ms`;
  `PlaywrightSurfaceAdapter` now converts a resolved-but-not-actionable
  target (e.g. covered by the demo app's session-notice overlay) into
  `TargetNotFoundError` instead of letting a raw Playwright timeout escape.
- Added `CheckpointFailedError`, `OutputExtractionFailedError` to the error
  taxonomy.
- Added `tests/fixtures/fake_surface.py` (`FakeSurfaceAdapter`) and 9 unit
  tests in `tests/unit/test_replay_engine.py` covering policy branches,
  bounded recovery, business-outcome precedence, and error classification.
- Added `tests/integration/test_replay_engine_e2e.py`: 3 real end-to-end
  tests running the actual `get_savings_balance` artifact through a real
  `PlaywrightSurfaceAdapter` against the real demo app — success with the
  engine's own live popup recovery (not hand-dismissed), the
  `MEMBER_NOT_FOUND` business outcome, and an unmodeled ambiguous-match
  state ("Smith" matches two seeded members) correctly surfacing as a
  structured `FAILED` result.
- Verified via the cloud-sandbox round-trip: 42 tests pass total (34 unit
  + 8 integration), no regressions. On-device unit suite reconfirmed green
  (34 passed) before committing.
- Committed locally; user has since pushed it themselves (`main` shows "up
  to date with origin/main" as of this log entry).


### Phase 6 — Layered PolicyEngine

- Built `LayeredPolicyEngine` in `src/cuas/safety/policy.py`
  (`.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md`, "Layered Policy Model"):
  global safety defaults -> vendor/application policy -> tenant-specific
  overrides. Each layer is an `IntentPolicy` — a plain
  `intent -> PolicyDecision` lookup table, nothing more. `DEFAULT_GLOBAL_INTENT_POLICY`
  encodes the doc's own SAFE/APPROVAL_REQUIRED/BLOCKED intent examples.
- **Combination rule (the entire policy, deliberately not a rules
  engine):** collect every opinion that applies — global's, the
  (vendor, application)-scoped app layer's, the
  (vendor, application, tenant_id)-scoped tenant layer's, and the action's
  own explicitly-declared `risk` as one more opinion — and return the
  single MOST RESTRICTIVE one. Consequences, all deliberate and all
  covered by tests: a more specific layer can tighten a broader layer's
  decision, but can never loosen one (a tenant can't turn a globally
  BLOCKED or APPROVAL_REQUIRED intent into ALLOW, even by declaring it
  ALLOW itself); an intent-based classification always overrides a
  self-reported `risk`, never the reverse.
- **Closed the "silently permissive default" trap explicitly**, since it's
  a real one: `Action.risk: RiskLevel = RiskLevel.SAFE` has a default, so
  an `Action` built without setting `risk` (e.g. a future discovery
  proposal that forgot to classify itself) is indistinguishable from one
  explicitly marked SAFE *unless* something checks
  `action.model_fields_set`. `LayeredPolicyEngine` does exactly that: an
  action whose intent matches no layer AND whose `risk` was never
  explicitly set contributes **no opinions at all**, and with no opinions
  the engine returns `REQUIRE_APPROVAL`, never `ALLOW` — it fails closed
  instead of guessing. An action with a genuinely explicit `risk` (however
  set) is still trusted as a fallback opinion when no layer recognizes its
  intent, so this doesn't regress hand-authored artifacts.
- `RiskBasedPolicyEngine` (Phase 5) is kept as-is — still a valid, simpler
  `PolicyEngine`, and `LayeredPolicyEngine`'s own risk-fallback reuses its
  `RiskLevel -> PolicyDecision` mapping (now a shared module constant,
  `RISK_TO_DECISION`).
- `ReplayEngine` required **zero changes** — it already depended only on
  the `PolicyEngine` ABC. Added
  `test_layered_policy_engine_is_a_drop_in_replacement_for_risk_based_policy`
  in `tests/unit/test_replay_engine.py`, which replays the real
  `get_savings_balance` artifact through a `LayeredPolicyEngine` (with no
  app/tenant policy configured, so its own step intents fall through to
  the risk-fallback) and asserts identical `SUCCESS` behavior to Phase 5 —
  the concrete regression check for "preserve current replay behavior."
- `tests/unit/test_policy_engine.py`: 13 tests covering global-layer
  SAFE/APPROVAL_REQUIRED/BLOCKED classification, an app layer tightening
  an intent the global layer has no opinion on (and staying scoped to its
  own vendor/app), a tenant layer tightening beyond the app layer (and
  falling back correctly for tenants with no override), a tenant unable to
  loosen a broader BLOCKED or APPROVAL_REQUIRED decision, an unclassified
  intent with an explicit risk being trusted, and the core
  unclassified-without-explicit-risk case failing closed to
  `REQUIRE_APPROVAL` (asserted against the actual pydantic mechanism —
  `model_fields_set` — that makes the trap real).
- Verified via the cloud-sandbox round-trip: 56 tests pass total (48 unit
  + 8 integration), no regressions from touching `safety/policy.py` alone
  (no `SurfaceAdapter`/Playwright changes this phase). On-device unit
  suite reconfirmed green (48 passed) before committing.
- Discovery (Phase 8) is expected to call this same `LayeredPolicyEngine`
  before executing any proposed action — no interface change needed for
  that; it's already the shared `PolicyEngine` ABC both callers will use.


### Phase 7 — Structured logging and evidence capture

- New `cuas.observability` modules: `events.py` (`EventType`, `RunEvent`
  in `.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md`'s own suggested shape --
  timestamp/run_id/component/event/step_id/status/details), `event_sink.py`
  (`EventSink` ABC + `NullEventSink` + `JsonlEventSink`, one JSONL file per
  run_id under `Settings.log_dir`), `evidence.py` (`EvidenceStore` ABC +
  `NullEvidenceStore` + `FileEvidenceStore`, one directory per run_id under
  `Settings.evidence_dir`), `redaction.py` (`redact_inputs`). Same
  one-ABC-one-real-implementation shape as `SurfaceAdapter`/`PolicyEngine`/
  `ArtifactRepository`.
- `ReplayEngine` now takes optional `event_sink`/`evidence_store`
  constructor args (both default to no-op implementations) and an optional
  `run_id` on `run()` (default: a fresh `uuid4`). Emits all 11 requested
  events (`run_started`, `artifact_loaded`, `step_started`,
  `policy_checked`, `action_executed`, `checkpoint_passed`,
  `business_outcome_detected`, `recovery_attempted`, `step_failed`,
  `evidence_captured`, `run_completed`) at the appropriate points in the
  existing, *unchanged* algorithm. `ReplayResult` gained a `run_id` field
  (always set; no test ever constructed a `ReplayResult` by hand, so this
  was a safe additive change). Zero changes to control flow -- every
  Phase 5/6 test still passes unmodified, because a caller that doesn't
  pass `event_sink`/`evidence_store` gets byte-for-byte the same behavior
  as before.
- **`artifact_loaded` placement, a deliberate stand-in:** `ReplayEngine`
  doesn't itself load artifacts (it receives one already loaded), so this
  event fires right after input validation succeeds, not from
  `ArtifactRepository`. Documented in code as provisional -- Phase 10's
  `CapabilityService`/`ArtifactRepository` integration is the real home for
  this event once retrieval-by-context exists; wiring it there later needs
  no interface change, just a second `EventSink.record` call site.
- **Evidence capture is scoped strictly to the FAILED path** (step
  execution failure, checkpoint failure after exhausted recovery, output
  extraction failure, success-condition failure) -- never on
  `BUSINESS_OUTCOME` (a valid state, not a failure) or on
  `APPROVAL_REQUIRED`/`BLOCKED` (a policy decision made before the surface
  was ever touched -- nothing to capture). This is what "normal success
  paths do not generate unnecessary heavy evidence" meant in practice, and
  it's covered by explicit tests for all three non-failure paths.
- **Redaction, precisely scoped:** `InputSpec` gained `sensitive: bool =
  False`; `redact_inputs(artifact, inputs)` swaps a sensitive input's raw
  value for `[REDACTED]` before it can reach the `run_started` event
  (the only place raw invocation inputs are ever formatted into a log
  line). `get_savings_balance`'s `member_id` is now marked `sensitive=True`
  as the concrete example. Realized while implementing that
  `action_executed` needed no redaction logic of its own: it logs a step's
  *value template* (e.g. `"{{member_id}}"`), never the substituted value,
  so a resolved sensitive input is never formatted into a log line in the
  first place -- there's nothing to strip because the leak can't occur by
  construction. This is documented explicitly in `redaction.py`'s
  docstring so a future contributor doesn't "fix" `action_executed` by
  bolting on redaction it doesn't need (or, worse, skip redacting a
  genuinely new leak path because they assume this one already covers it).
- **Screenshots are explicitly NOT treated as redacted**, per the user's
  specific instruction to state this plainly rather than let "redaction"
  cover for it: `EvidenceRecord.screenshot_is_unredacted_pii_risk` is set
  `True` on every record that includes a screenshot. `evidence.py`'s
  module docstring spells out why (a screenshot is a pixel-for-pixel
  picture of whatever was on screen -- a member ID, a balance -- and there
  is no general, reliable way to black that out without destroying the
  evidence's own purpose) and states plainly that the evidence directory
  needs the same access-control discipline as raw PII: excluded from
  version control (`data/evidence/` and `data/logs/` added to
  `.gitignore` this phase, alongside the pre-existing `data/*.db`), not
  attached to bug reports, access-restricted in any real deployment. Only
  the *textual* metadata stored alongside a screenshot is subject to the
  same redaction discipline as any other log content.
- **Thin accessibility/DOM snapshot**, "if practical" per the instructions:
  `Evidence` gained an optional `dom_snapshot` field; `PlaywrightSurfaceAdapter.
  capture_evidence()` calls Playwright's `page.accessibility.snapshot()`,
  serializes it to JSON, and truncates at 20,000 characters. Wrapped in a
  bare `except Exception: return None` -- a missing snapshot must never
  block capturing the screenshot/URL/text that evidence capture exists
  for. Verified for real (not just import-checked) against the live demo
  app in the cloud-sandbox round-trip: a real run there produces a real,
  non-empty accessibility-tree JSON file alongside a real PNG screenshot.
- Evidence capture is itself best-effort: if `surface.capture_evidence()`
  raises, `ReplayEngine._capture_failure_evidence` catches it and proceeds
  with `evidence=None` (so `EvidenceStore` still gets a call recording the
  reason/error/recent-actions with no screenshot) -- a problem capturing
  evidence must never mask or replace the run's real, primary failure.
- `tests/unit/test_event_sink.py` (3), `test_evidence_store.py` (5, incl.
  the explicit `screenshot_is_unredacted_pii_risk=True` assertion and a
  sequence-numbering-avoids-collisions case), `test_redaction.py` (3, incl.
  a hand-built artifact with one sensitive and one non-sensitive input to
  prove redaction doesn't become "hide everything"), and 8 new tests
  appended to `test_replay_engine.py` (event sequence + run_id correlation
  for a success run, explicit `run_id` threading, redaction of
  `run_started`'s inputs, `action_executed`'s value-template-not-value
  behavior, and the three "no evidence on a non-failure path" cases plus
  one real hard-failure-captures-evidence case using a real
  `FileEvidenceStore` against `tmp_path`). Plus one new real-browser
  integration test verifying actual PNG bytes and a real non-empty
  accessibility-tree snapshot land on disk from one real failed run.
- Verified via the cloud-sandbox round-trip: 76 tests pass total (67 unit
  + 9 integration), no regressions. On-device unit suite reconfirmed green
  (67 passed) before committing.


### Phase 8 — LLM discovery loop

- New `cuas.discovery` package: `models.py` (`DiscoveryGoal`,
  `DiscoveryLimits`, `DiscoveryStatus`, `DiscoveryHistoryEntry`,
  `DiscoveryResult`), `trace.py` (`DiscoveryTrace`, `DiscoveryTraceStep`,
  `DiscoveryTraceStore` ABC + `NullDiscoveryTraceStore` +
  `FileDiscoveryTraceStore`), `llm_client.py` (`LLMClient` ABC,
  `LLMResponse`), `anthropic_client.py` (`AnthropicLLMClient`, the one real
  implementation), `engine.py` (`DiscoveryEngine`). Same
  one-ABC-one-real-implementation shape as every other seam in this
  codebase.
- **Loop, exactly as specified:** observe -> ask the model for a
  structured action -> validate/parse -> run through the same
  `PolicyEngine` `ReplayEngine` uses -> execute through the same
  `SurfaceAdapter` -> observe again -> repeat until success / a known
  business outcome / a hard stop. `DiscoveryEngine` depends on
  `SurfaceAdapter`/`PolicyEngine` exactly like `ReplayEngine` does --
  neither abstraction needed to change at all for a second caller to show
  up, which is the whole point of having depended on interfaces from
  Phase 3/6 onward.
- **The model never executes anything.** `LLMClient.propose_action`
  returns a raw, untyped `LLMResponse.proposal` dict; turning that into a
  real, schema-valid `Action` (or rejecting it) is entirely
  `DiscoveryEngine._parse_proposal`'s job, not any given client's. This is
  deliberate: it means `FakeLLMClient` (tests) and the real
  `AnthropicLLMClient` are validated by the exact same parsing code, so a
  test proving "malformed output stops the run" is proving something true
  of the real integration too, not just of the fake.
- **LLM-proposed actions never get `Action.risk` set explicitly.** This
  was Phase 6's payoff arriving on schedule, not new code:
  `LayeredPolicyEngine` already treats an intent that matches no policy
  layer AND has no explicitly-set `risk` (via `model_fields_set`) as
  contributing zero opinions, which resolves to `REQUIRE_APPROVAL`. By
  simply never setting `risk` when constructing an `Action` from a parsed
  model proposal, every LLM proposal with an intent the policy tables
  don't specifically recognize automatically fails closed to
  human-approval-required -- no discovery-specific safety code needed,
  and no way for a model to talk its way into `SAFE` by self-declaring
  its own risk.
- **Policy DENY/REQUIRE_APPROVAL is terminal, exactly like replay.** "Stop
  or escalate rather than being 'fixed' by guessing" is implemented
  literally: a policy-denied or approval-required proposal ends the run
  (`DiscoveryStatus.BLOCKED` / `APPROVAL_REQUIRED`) immediately. Discovery
  never tries a different action after a policy escalation.
- **A genuine execution failure is NOT terminal, unlike replay** -- the one
  place discovery is deliberately more lenient than `ReplayEngine`.
  `TargetNotFoundError` (the model guessed a control that isn't there) or
  a plain `ValueError` (a structurally valid but semantically incomplete
  proposal, e.g. a `fill` with no `value`) is caught, recorded, and fed
  back to the model as history for the next turn, instead of being
  mechanically retried against an artifact-declared recovery table --
  there is no artifact yet for one to exist in, and unlike `ReplayEngine`,
  discovery has a reasoning model in the loop that can react to a
  failure. This is bounded by the same `max_steps`/`max_duration_seconds`/
  loop-detection limits as everything else, so a model that never recovers
  still terminates.
- **Hard bounds, all enforced by `DiscoveryEngine` itself, never left to
  model judgment:** `DiscoveryLimits.max_steps` (loop iteration cap),
  `max_duration_seconds` (wall-clock cap, checked every turn),
  `max_total_tokens` (running sum of `LLMResponse.input_tokens +
  output_tokens`, checked every turn), `max_consecutive_repeats` (default
  2 -- two identical proposals in a row are tolerated and execute
  normally, a third identical one in a row aborts the run with
  `LOOP_DETECTED` rather than letting the model spin). "Identical" is a
  signature over action_type/intent/target/value, deliberately excluding
  the randomly-generated `Action.id`.
- **Business-outcome detection is deterministic, not model-reported.**
  `DiscoveryGoal.known_business_outcomes` reuses `BusinessOutcome` from
  `artifact/schema.py` unchanged (the same "find something on the
  surface" concept `ReplayEngine` already uses) and is checked against
  the live surface at the top of every turn, before the model is ever
  consulted -- exactly mirroring how replay's own business-outcome
  detection never asks anyone, it just checks.
- **The model's "done" claim is not trusted outright either.**
  `DiscoveryGoal.success_checkpoint` (optional; reuses `WaitCondition`) is
  verified against the live surface when the model declares `done=true`.
  If it's declared and doesn't verify, the claim is rejected (logged,
  loop continues, still bounded by the same limits) rather than ending the
  run on the model's word. If no `success_checkpoint` is declared at all
  (a goal with nothing yet to check against), `done=true` is accepted as
  given -- safe, because "the goal was accomplished" isn't itself a safety
  decision; the `PolicyEngine`, already checked on every action along the
  way, is what actually keeps discovery from doing anything consequential.
- **Trace vs. artifact, kept strictly separate (`.CLAUDE/08` decision
  #6).** `DiscoveryTraceStore` persists a `DiscoveryTrace` --
  `step_index`, redacted observation/model-output/parsed-action, policy
  decision, execution error, outcome, for *every* turn including failed
  detours, malformed output, and loop/limit aborts. Nothing in this phase
  (or planned for it) ever constructs an `Artifact` from a
  `DiscoveryTrace` -- that's explicitly Phase 9's job, working from a
  successful trace after the fact.
- **Redaction, at every boundary a raw sensitive value could otherwise
  leak through.** `DiscoveryGoal.sensitive_inputs`/`sensitive_values()`
  name which of `inputs`' raw values must never be persisted or logged
  (discovery has no artifact `InputSpec.sensitive` table yet to carry this
  instead). Two new general-purpose redaction helpers in
  `observability/redaction.py`: `redact_dict` (a dict of named values,
  generalizing `redact_inputs` without requiring an `Artifact`) and
  `redact_text` (freeform substring replacement for prompts/responses/
  observations, which are unstructured text, not named fields).
  `DiscoveryEngine` applies one of these (or the engine-local
  `_redact_json`, a recursive version for nested dict/list structures like
  a parsed `Action`'s own JSON) to every free-text field before it reaches
  a `DiscoveryTraceStep`, a `RunEvent`, or a `DiscoveryResult` returned to
  a caller -- including the goal's own `description` (which commonly
  embeds the very input it describes, e.g. "Find member M1001...") and
  both the `url` and `visible_text` of any persisted `Observation`, since
  the demo app's real URLs embed the member ID verbatim after a redirect
  (`/members/M1001/accounts`) -- a genuine, non-hypothetical requirement,
  not just a hypothetical one. Crucially, this redaction is a boundary,
  not a blanket rule: the *raw* values legitimately reach
  `AnthropicLLMClient` (the model needs the real member ID to type it into
  a search box -- that is the system doing its job, not a leak) via an
  internal, never-persisted `history` list; `DiscoveryEngine._redact_history`
  is the one place that list is redacted before being handed back in a
  `DiscoveryResult`.
- **`AnthropicLLMClient` is thin and provider-isolated.** It is the only
  module in the codebase importing `anthropic`, imported lazily inside
  `__init__` rather than at module scope, so nothing else -- including
  every non-`live_llm` test -- needs the package installed or a key
  present. Its tool schema deliberately offers a narrower action surface
  than the full `ActionType` enum (`navigate`/`click`/`fill`/`read`/
  `dismiss`, and two locator strategies, `role_name`/`css`) -- the model
  is never offered `wait_for` (no artifact-declared checkpoint for it to
  describe) as an option to propose in the first place, rather than
  something `DiscoveryEngine` has to reject after the fact. Verified
  structurally accurate against the real, installed `anthropic==1.5.0`
  SDK (via `inspect.signature`/`model_fields` introspection -- confirmed a
  genuine, newer-than-training-data version of the official package, not
  a mock) without making any live API call.
- `tests/fixtures/fake_llm_client.py` (`FakeLLMClient` + `propose`/
  `propose_done`/`malformed`/`role_target`/`css_target` helpers) is
  `fake_surface.py`'s sibling on the model side -- a scripted queue of
  `LLMResponse`s, no network, no key. `fake_surface.py` gained
  `script_observation` (a queued sequence of `Observation`s) since,
  unlike `ReplayEngine`, `DiscoveryEngine` calls `observe()` repeatedly
  between actions and tests need to control what a multi-turn scripted
  run "sees" at each step.
- `tests/unit/test_discovery_engine.py`: 11 tests covering every scenario
  requested -- normal successful discovery (plus proving redaction of the
  returned history), malformed model output (both "no proposal at all"
  and "a proposal missing a field its own action_type requires" --
  the latter surfaces as a *recoverable* execution failure, not
  `MALFORMED_MODEL_OUTPUT`, since the JSON itself was structurally valid;
  documented in the test), repeated/looping actions, policy `BLOCKED`,
  `APPROVAL_REQUIRED`, a model-proposed nonexistent target (proving the
  failure is fed back as history and the *next* model call actually sees
  it), maximum-step exhaustion, business-outcome detection without ever
  invoking the (fake) model, evidence captured on a genuine execution
  failure but never on a policy escalation, and discovery-trace
  persistence with a redaction assertion against the full serialized
  trace. `tests/unit/test_discovery_trace_store.py`: 3 tests for
  `FileDiscoveryTraceStore`/`NullDiscoveryTraceStore` storage mechanics in
  isolation, mirroring `test_evidence_store.py`'s pattern.
- `tests/integration/test_discovery_engine_e2e.py`: 2 real end-to-end
  tests running the exact `DiscoveryEngine` through a real
  `PlaywrightSurfaceAdapter` against the real demo app, with
  `FakeLLMClient` standing in for an actual model (no `ANTHROPIC_API_KEY`
  needed). Unlike `test_replay_engine_e2e.py`, discovery has no
  artifact-declared `recoverable_conditions` to mechanically dismiss the
  real session-notice popup -- the first scripted proposal in both tests
  is dismissing that popup, proving it's genuinely the model's
  responsibility here, not the engine's. Covers the real success path
  (with `success_checkpoint` verification against the real "Accounts"
  panel) and the real `MEMBER_NOT_FOUND` business outcome.
- `tests/unit/test_anthropic_llm_client.py`: one `live_llm`-marked test,
  skips cleanly with no `ANTHROPIC_API_KEY` set (never executed this
  session, per the standing instruction to only pause on discovery work
  when a *live* run genuinely needs the key -- this phase never did).
- `data/discovery_traces/` added to `.gitignore` alongside
  `data/evidence/`/`data/logs/` -- a discovery trace is redacted but is
  still real runtime output of real runs, not a reviewable/versioned
  artifact like `data/artifacts/`.
- Verified via the cloud-sandbox round-trip (`pytest -m "not live_llm"`):
  92 tests pass total (81 unit, incl. the 14 new discovery/trace-store
  tests, + 11 integration, incl. the 2 new discovery e2e tests), no
  regressions. On-device unit suite reconfirmed green separately (81
  passed, 12 deselected for `integration`/`live_llm`) before committing;
  the new `live_llm`-marked Anthropic test skips cleanly with no key set.


### Phase 9 — Artifact construction from a successful discovery trace

- **Small, necessary Phase 8 amendment, made first and called out
  explicitly (per the standing instruction to state conflicts/trade-offs
  and propose the smallest change rather than silently diverging):**
  discovery's redaction switched from a flat, anonymous `"[REDACTED]"`
  marker to *named* placeholders (`"{{member_id}}"`) --
  `redact_named_values`/`redact_named_values_json`
  (`observability/redaction.py`), replacing `redact_text`/`_redact_json`
  everywhere in `discovery/engine.py`; `DiscoveryGoal.sensitive_values()`
  became `named_sensitive_values() -> dict[str, str]`. Reason: Phase 9
  needs a persisted trace's kept actions to already carry the exact
  `{{input_name}}` placeholder syntax an `Artifact.Step.value` uses
  (`.CLAUDE/02_ARTIFACT_SCHEMA.md`); a generic `"[REDACTED]"` marker made
  that unrecoverable the moment more than one sensitive input existed,
  since nothing said *which* input a given `"[REDACTED]"` came from.
  Naming the placeholder is strictly as safe as the generic marker -- no
  raw value survives either way -- so this was the smallest change that
  unblocks Phase 9 without loosening Phase 8's "never persist a raw
  sensitive value" guarantee. `DiscoveryHistoryEntry`/`DiscoveryTraceStep`
  also gained a `read_value: str | None` field (redacted like every other
  free-text field) -- Phase 8 never captured what a READ action actually
  read (it let the model see effects only via the next page observation),
  but Phase 9 needs to know what was read to infer a typed `OutputSpec`.
  `DiscoveryEngine._execute` now returns that value instead of discarding
  it. All 14 Phase 8 tests still pass after this change (one assertion
  updated: `"[REDACTED]"` -> `"{{member_id}}"`); no other behavior
  changed.
- New `cuas.artifact_builder` package (`builder.py`: `ArtifactBuilder`,
  `ArtifactBuildError`) -- stateless, deterministic, no LLM anywhere in
  it. Depends on both `cuas.discovery` (reads a `DiscoveryTrace`/
  `DiscoveryGoal`) and `cuas.artifact` (produces an `Artifact`) -- the
  first package in this codebase that legitimately sits "above" two
  existing ones rather than being a third parallel seam, since
  artifact-from-trace construction is inherently a bridge between them.
- **No LLM-authored artifact JSON, ever.** `build()` takes optional
  `name`/`description` string overrides for exactly the case where a
  human (or a model-assisted proposal a caller separately chose to trust)
  wants better labels than the trace's own `capability_id`/
  `goal_description` -- but those are just strings substituted into an
  otherwise fully deterministic build; they cannot affect steps, inputs,
  outputs, checkpoints, or safety metadata. Every constructed `Artifact`
  still passes through `Artifact`'s own unmodified pydantic validators
  before it can be returned or stored (`ValidationError` wrapped as
  `ArtifactBuildError`) -- construction never bypasses schema validation
  via `model_construct` or similar.
- **Algorithm** (`ArtifactBuilder.build`): reject anything that isn't a
  genuinely successful, well-formed trace first
  (`_require_successful_trace`) -> keep only steps `DiscoveryEngine`
  itself recorded as `outcome == "executed"` (`_select_successful_path`)
  -- this alone is what excludes every failed detour, malformed-output
  turn, and policy escalation, since none of those ever carry that
  outcome -> collapse only an *exact, adjacent* duplicate action
  conservatively (`_deduplicate_adjacent`; a non-adjacent repeat, e.g.
  correcting course and coming back to the same field, is real history,
  not redundancy, and is never touched) -> declare a typed `InputSpec`
  per `DiscoveryGoal.inputs` name and placeholder-ize any of its raw
  values still literally present in a kept action (`_build_inputs`/
  `_placeholderize`; sensitive inputs already arrive placeholder-ized
  from the Phase 8 amendment above -- this catches non-sensitive
  declared inputs too, which a trace leaves as their literal value on
  purpose for human readability) -> any kept `READ` action becomes a
  typed `OutputSpec` instead of a replay `Step` (`_build_outputs`;
  `OutputSpec.source` is what `ReplayEngine._extract_outputs` reads at
  the end of a run, exactly matching the existing hand-authored
  `get_savings_balance` convention -- a `READ` is never *also* replayed
  as an inline step, which would read it twice) -> the output's
  `OutputType` is inferred from what was actually read
  (`_infer_output_type`: boolean-like text, then a numeric parse
  distinguishing integer from decimal by the presence of a `.`, else
  string) -> `goal.success_checkpoint`, if declared, becomes the
  checkpoint on the last remaining (non-`READ`) step; `goal.
  known_business_outcomes` carry forward verbatim into
  `Artifact.business_outcomes` -> every constructed `Step` deliberately
  leaves `risk` unset, exactly as `DiscoveryEngine` left `Action.risk`
  unset on every proposal, so `LayeredPolicyEngine`'s fail-closed
  behavior for an unrecognized intent governs a replay of this artifact
  exactly the way it governed the original discovery run --
  `ArtifactSafety` (informational only, same as always) summarizes
  overall intent (the last step's) and risk (`SAFE`, since every kept
  action already passed the real `PolicyEngine` during discovery -- this
  module only ever sees the "executed" outcome).
- **"No output" is a rejection, not a limitation quietly worked around.**
  The existing `Artifact` schema's `SuccessCondition` only supports
  `OUTPUT_VALID` (`.CLAUDE/02`); a successful discovery run with no `READ`
  action anywhere in its kept path has nothing to hang a success
  condition on, so `ArtifactBuilder` raises `ArtifactBuildError` rather
  than inventing one. This doubles as one of the required "malformed/
  incomplete successful traces are rejected" test cases -- it wasn't
  designed as a test-passing trick, it's a genuine, honestly-reported
  boundary of what this phase's construction can do.
- `tests/unit/test_artifact_builder.py`: 13 tests against hand-crafted
  `DiscoveryTrace`/`DiscoveryGoal` objects (no engine run needed) --
  a clean successful trace becoming a correct artifact (steps, inputs,
  outputs, output type inference, checkpoint carried onto the last step,
  business outcomes carried forward, `risk` left unset, safety metadata),
  a wrong-turn detour (an `execution_failed` step) excluded from the
  result, a raw literal value becoming a `{{name}}` placeholder
  independent of upstream redaction, five "malformed/incomplete" rejection
  cases (not-success, no steps, no executed steps, no `READ`/output, a
  corrupted kept action) grouped in one test class, adjacent-duplicate
  collapse, non-adjacent-repeat preservation, provenance/version
  metadata (including a non-semver version failing schema validation, not
  just this module's own checks), and a round-trip save/load through the
  real `FileArtifactRepository`.
- `tests/integration/test_artifact_builder_e2e.py`: one real end-to-end
  test -- a real `DiscoveryEngine` run (`FakeLLMClient`, no LLM key)
  against the real demo app produces a real successful `DiscoveryTrace`;
  `ArtifactBuilder` turns it into an `Artifact`; that artifact then
  replays successfully through the real `ReplayEngine` against a *fresh*
  page load using member `M1002` -- a member id that was never part of
  the discovery run at all (discovery used `M1001`) -- and gets back the
  correct, independently-seeded balance (`9900.00`). This is the concrete
  proof that construction produces a genuinely reusable capability, not
  just a replay of the exact run it came from. (First attempt at this
  test used discovery-goal intents like `enter_member_id`/
  `read_savings_balance` that aren't in `DEFAULT_GLOBAL_INTENT_POLICY`,
  which correctly made every action `REQUIRE_APPROVAL` under
  `LayeredPolicyEngine` -- fixed by using recognized SAFE intents
  (`search_member`/`view_account`/`open_member_record`), which is the
  policy engine working as designed, not a bug.)
- Verified via the cloud-sandbox round-trip (`pytest -m "not live_llm"`):
  106 tests pass total (94 unit, incl. the 13 new `ArtifactBuilder` tests,
  + 12 integration, incl. the 1 new artifact-builder e2e test), no
  regressions. On-device unit suite reconfirmed green separately (94
  passed, 13 deselected) before committing.

### Phase 10 — Capability service and artifact resolution

- New `cuas.capability` package (`models.py`, `repository.py`,
  `service.py`) implementing only the deterministic metadata-filter half
  of `.CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md`'s "Capability Retrieval"
  flow (`request -> tenant/app context -> filter to compatible
  vendor/application/version -> validate candidate compatibility -> select
  artifact or DISCOVERY_REQUIRED`). No embeddings, no semantic similarity,
  no vector store -- per the user's explicit instruction, that step is
  deliberately left as a later stretch layer on top of this one. The
  service is a plain Python class with two constructor dependencies
  (`CapabilityRepository`, `ArtifactRepository`); it imports nothing
  FastAPI-related, so an orchestrator can call it directly.
- **`CapabilityRecord` is deliberately a separate, small, mutable
  pointer, not the artifact itself** (`.CLAUDE/07`: "register/store
  capability metadata separately from artifact files"). It carries
  `capability_id`, `name`, `description`, `vendor`/`application`,
  `supported_versions` (exact version or `"N.x"` major-version wildcard,
  the same convention `ArtifactApplication.supported_versions` already
  uses), `tenant_scope` (`"base"` sentinel or an exact `tenant_id`),
  `artifact_version` (which published `Artifact` version this capability
  currently resolves to), and `status` (`active`/`deprecated`/
  `disabled`). Flipping `status` or repointing `artifact_version` is
  ordinary metadata maintenance; it never touches a published `Artifact`
  file, which stays immutable once versioned exactly as Phase 4 designed
  it.
- **Why `CapabilityRecord.tenant_scope` exists at all when
  `ArtifactOverride` already has tenant-scoped overrides:** these are two
  different concerns, not a duplicate mechanism. `ArtifactOverride`/
  `resolve_artifact` (unchanged from Phase 4/5, reused as-is here) is a
  *step-level patch* -- customizing specific step targets/values within
  *one* artifact version, once a capability has already been matched.
  `CapabilityRecord.tenant_scope` is an *eligibility filter* answering
  "can this capability record even be considered for this tenant at
  all" -- and, unlike a patch, it can legitimately point a specific
  tenant at a genuinely different `artifact_version` when step-level
  overrides alone aren't enough (`.CLAUDE/08` decision #9 prefers
  overrides over full duplication but does not forbid this when a
  tenant's workflow has truly diverged). A tenant-exact record is treated
  as strictly *more specific* than a `"base"` record and wins when both
  are compatible -- the same "most specific wins" rule
  `LayeredPolicyEngine` already uses for policy layering, applied here to
  capability selection instead.
- **`CapabilityService.resolve(capability_id, context)` algorithm:** load
  every `CapabilityRecord` for `capability_id`; none registered at all ->
  `NO_CAPABILITY_MATCH` (reason: "no capability record registered").
  Otherwise check each record's compatibility against the `AppContext`
  (vendor/application exact match; `status == ACTIVE`; version compatible
  per `supported_versions`; `tenant_scope` either `"base"` or an exact
  match) and score its specificity (tenant-exact = 1, base = 0); zero
  compatible records -> `NO_CAPABILITY_MATCH` (reason includes every
  rejected record's specific incompatibility, e.g. "status is
  'disabled'", "version '2.0.0' not in supported_versions ['1.x']",
  "tenant_scope 'cu42' does not match requesting tenant 'cu99'"); more
  than one record tied at the highest specificity -> `AMBIGUOUS` (refuses
  to guess, exactly as instructed) rather than picking one arbitrarily;
  otherwise load the matched record's pinned `artifact_version` via the
  existing `ArtifactRepository.load`, load its overrides via
  `load_overrides`, and call the **existing, unmodified**
  `resolve_artifact(base, overrides, version=context.version,
  tenant_id=context.tenant_id)` -> `RESOLVED` with the fully resolved
  `Artifact`. A capability record whose pinned `artifact_version` has no
  saved file (`FileNotFoundError`) is reported as `NO_CAPABILITY_MATCH`,
  not a crash -- but a *schema-invalid* saved artifact
  (`ArtifactInvalidError`) is deliberately left to propagate, since a
  registered capability pointing at corrupted data is a genuine
  data-integrity bug, not a routine "nothing matched" outcome.
- **`CapabilityResolution.status` is `RESOLVED` / `NO_CAPABILITY_MATCH` /
  `AMBIGUOUS` -- `DISCOVERY_REQUIRED` is deliberately NOT one of them.**
  `.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md` describes `NO_CAPABILITY_MATCH
  -> DISCOVERY_REQUIRED` as normal control flow, but *deciding* to start
  discovery (or queue a human, or something else) is the orchestrator's
  job (Phase 11), not this service's -- `CapabilityService` only ever
  reports what it found. A caller gets to "discovery required" by
  branching on `status != RESOLVED`.
- **`FileCapabilityRepository.save()` is deliberately the opposite of
  `FileArtifactRepository.save()`.** The artifact repository refuses to
  overwrite an existing version (a published `Artifact` is immutable
  once versioned). A `CapabilityRecord` is a mutable pointer that is
  *expected* to change in place (status flips, repointing to a newer
  artifact version), so `FileCapabilityRepository.save()` is idempotent
  overwrite-on-save, keyed by `(capability_id, vendor, application,
  tenant_scope)` -- the tuple that uniquely identifies one record. Two
  records may legitimately share a `capability_id` (a `"base"` record
  plus a tenant-specific one), but never the same full key.
- `tests/fixtures/fake_capability_repository.py`: a small in-memory
  `CapabilityRepository` used only by `CapabilityService`'s own test
  suite, so ambiguous-match scenarios can be constructed directly rather
  than being accidentally impossible to express through
  `FileCapabilityRepository`'s own uniqueness key (which, by
  construction, can never hold two records tied on specificity for one
  request). This mirrors the codebase's existing `fake_llm_client.py`/
  `fake_surface.py` pattern of isolating logic tests from a real storage
  or I/O implementation.
- `tests/unit/test_capability_service.py` (14 tests) and
  `tests/unit/test_capability_repository.py` (7 tests), all pure unit
  tests against `tmp_path` and in-memory fakes -- no browser, no LLM.
  Covers every scenario the user asked for: exact resolution;
  version-specific override selection (and that a different, still
  version-compatible request does *not* pick it up); tenant-specific
  override selection (and that a different tenant does not); tenant
  override winning over a version override for the same step, routed
  through the service end-to-end; incompatible vendor/application,
  version, and tenant each producing `NO_CAPABILITY_MATCH` with a
  reason naming the specific mismatch; disabled and deprecated
  capabilities (parametrized) producing `NO_CAPABILITY_MATCH`; ambiguous
  equally-specific matches producing `AMBIGUOUS` rather than a guess; no
  registered capability at all, and a capability pointing at a missing
  artifact version, both producing `NO_CAPABILITY_MATCH` as a normal
  structured result rather than an exception; a bonus test proving a
  tenant-specific record's precedence over a base record end-to-end; and
  `FileCapabilityRepository`'s own storage mechanics (round-trip,
  overwrite-on-save for the same key, separate records for distinct
  tenant scopes, per-capability vs. across-all listing, corrupt-file
  handling).
- On-device unit suite: 114 passed, 13 deselected (`not integration and
  not live_llm`) -- no regressions. No cloud-sandbox round-trip needed
  for this phase: nothing here touches Playwright or a real browser.

### Phase 11 — `RunOrchestrator` + API integration

- New `cuas.orchestration` package (`models.py`, `orchestrator.py`)
  implementing the control-flow layer named in `.CLAUDE/07`'s suggested
  package shape. `RunOrchestrator.run_capability(capability_id, inputs,
  context, *, discovery_goal=None, run_id=None) -> RunResult` is the
  entire public surface: `request -> CapabilityService.resolve -> RESOLVED
  branches to ReplayEngine.run, NO_CAPABILITY_MATCH branches to
  DiscoveryEngine.run when a `discovery_goal` was supplied (else reports
  `DISCOVERY_REQUIRED` immediately), AMBIGUOUS is reported as its own
  outcome`. It never reimplements replay, policy, capability-resolution,
  or discovery logic -- every step is a direct call into the existing
  Phase 5/6/8/9/10 components, with only their `Status` enums translated
  into `RunOutcome` and, on escalation, an `InterventionRequest` built
  from their result fields. This was checked explicitly against the
  phase instruction ("do not move replay logic, policy logic, artifact
  resolution, or discovery internals into the orchestrator") before
  committing.
- **`SurfaceFactory = Callable[[], AbstractAsyncContextManager[SurfaceAdapter]]`**
  is the one new seam `RunOrchestrator` depends on beyond the existing
  services. It matches `launch_playwright_surface`'s exact shape (an
  `@asynccontextmanager`-decorated function) precisely so the real API
  composition root can pass that function directly, while tests pass a
  fake factory that hands out scripted `FakeSurfaceAdapter`s -- the
  orchestrator asks for a fresh surface per replay/discovery attempt and
  never owns surface lifecycle itself, matching every existing
  integration test's "one `launch_playwright_surface()` call per attempt"
  pattern (see `test_artifact_builder_e2e.py`).
- **Run ID propagation:** `run_capability` generates `run_id =
  uuid.uuid4().hex` when the caller doesn't supply one, then threads that
  exact value through every downstream call that accepts one --
  `ReplayEngine.run(..., run_id=run_id)`, `DiscoveryEngine.run(...,
  run_id=run_id)`, every `_emit(run_id, ...)` structured-log event, and
  the `InterventionRequest.run_id` field on escalation -- so a single
  `run_id` correlates logging, the discovery trace file, replay evidence,
  and (once Phase 12 persists them) intervention records end to end.
  Covered explicitly by `TestRunIdCorrelation`.
- **Replay-after-discovery, not output-extraction-in-the-orchestrator:**
  `DiscoveryResult` (Phase 8) carries step history and a `capability_id`,
  not typed/named outputs -- turning a trace into named, typed outputs is
  `ArtifactBuilder`'s job applied to a trace (Phase 9), and *re-reading*
  those outputs deterministically from a stored artifact is
  `ReplayEngine`'s job (Phase 5). Rather than duplicate
  `ReplayEngine._parse_output`'s type-conversion logic inside the
  orchestrator to derive a `SUCCESS` result's outputs straight from the
  discovery trace, `RunOrchestrator` on a successful discovery: (1) loads
  the just-written `DiscoveryTrace` via the injected `DiscoveryTraceStore`,
  (2) calls `ArtifactBuilder().build(...)` and saves the artifact via the
  injected `ArtifactRepository`, (3) registers a new `CapabilityRecord`
  pointing at it via `CapabilityService`, then (4) immediately calls
  `ReplayEngine.run` again -- against a **second**, freshly obtained
  surface -- with the original request's `inputs`, and returns *that*
  result to the caller. This means a capability's first-ever invocation
  costs one extra live browser session (discovery, then a verification
  replay) versus every subsequent call (resolved capability, one replay
  only), which was accepted as the right trade-off specifically to avoid
  violating the "do not duplicate replay logic" constraint. Documented at
  length in `orchestrator.py`'s `_materialize_capability` docstring.
- **Newly-discovered capabilities register with `tenant_scope =
  context.tenant_id`, not the `"base"` sentinel.** Discovery only ever
  demonstrates a workflow against one tenant's live application instance;
  broadening a freshly-learned capability to every tenant of that
  vendor/application is a deliberate, separate operator decision (in the
  spirit of `.CLAUDE/08` decision #9's "prefer overrides over full
  duplication" -- which argues against *needless* duplication, not
  against tenant-scoping something that hasn't been proven to generalize
  yet). An operator can widen an existing record's `tenant_scope` to
  `"base"` later; `RunOrchestrator` never does this automatically.
- **Escalation is created for `APPROVAL_REQUIRED` and hard `FAILED`
  outcomes, deliberately NOT for `BLOCKED`.** The phase instructions name
  exactly two triggers for the intervention seam: "policy approval
  requirement" and "unresolved failure." A policy `DENY` (`BLOCKED`) is a
  *terminal refusal* -- there is nothing for a human operator to approve,
  since the system has already decided the action must not happen -- so
  `RunOrchestrator` reports `RunOutcome.BLOCKED` directly with no
  `InterventionRequest`. `APPROVAL_REQUIRED` (a policy asking for sign-off
  before proceeding) and `FAILED` (replay/discovery gave up after bounded
  recovery) both create an `InterventionRequest` via the injected
  `InterventionRepository`, since both describe a state a human can
  actually act on. This is an interpretation choice, not something the
  phase instructions spelled out explicitly, and is called out as such in
  code comments per the standing instruction to flag judgment calls
  rather than let them pass silently.
- **Known, explicitly-flagged limitation carried into Phase 12:**
  `.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md` requires that creating an
  intervention must NOT terminate the live browser session, so a human
  operator can resume the in-progress workflow exactly where it stopped.
  Phase 11 does **not** satisfy this. `RunOrchestrator` obtains each
  surface via `async with self._surface_factory() as surface:`, and that
  `async with` block -- and therefore the browser session -- closes
  before control ever returns far enough up the stack to construct an
  `InterventionRequest`. There is no session registry yet capable of
  holding a live `SurfaceAdapter` open across the request/response
  boundary while a human is paged. Rather than build a partial, likely
  wrong version of that infrastructure under this phase's scope, this was
  left as an explicit, documented gap: `InterventionRequest.session_id`
  is hard-coded to `None` for the whole of Phase 11 (see the extensive
  docstring on that field in `handoff/models.py`), and Phase 12
  ("Intervention / human-handoff persistence + async resume") is exactly
  where the session-registry work belongs per the user's own 15-phase
  plan. **This is the single most important trade-off from this phase to
  keep in view going into Phase 12.**
- **`cuas.handoff` package** (`models.py`, `repository.py`): first cut of
  the intervention seam named in `.CLAUDE/07`'s suggested interfaces.
  `InterventionRequest` (id, run_id, capability_id, tenant_id,
  current_step, reason, evidence, session_id, status, claimed_by,
  created_at, resolved_at) is intentionally thin -- `evidence` is a small
  freeform dict of pointers/summary fields, not embedded screenshot
  bytes, which continue to live in `EvidenceStore` (Phase 7) and are
  referenced, not duplicated. `InterventionRepository` (ABC) +
  `InMemoryInterventionRepository` (real, minimal implementation) follow
  the same one-ABC-one-implementation pattern as every other seam in this
  codebase; per the phase instruction, the *full* persistence
  implementation (a file- or DB-backed repository, claim/resolve
  workflows, the operator-facing read side) is deferred to Phase 12 --
  Phase 11 only needs interventions to be *creatable* and *listable*
  behind an interface.
- **API models kept structurally separate from orchestration models, per
  explicit instruction, even though they currently look similar.**
  `cuas.api.schemas` (`RunRequest`, `RunResponse`, `DiscoveryGoalRequest`)
  are plain Pydantic models owned by the HTTP layer; `cuas.orchestration
  .models.RunResult` is the domain-level return type `RunOrchestrator`
  actually produces. `api/main.py` translates between them at the
  boundary (`RunResponse(**result.model_dump())`) rather than
  `RunOrchestrator` importing or returning an API type, so the service
  layer has zero FastAPI/HTTP awareness -- `RunOrchestrator` could be
  called from a CLI or a queue worker with no changes.
- **Thin FastAPI layer** (`api/main.py`, `api/schemas.py`, `api/store.py`):
  `api/main.py` is a pure composition root -- every dependency
  `RunOrchestrator` needs (`CapabilityService`, `FileArtifactRepository`,
  `LayeredPolicyEngine`, `launch_playwright_surface`,
  `InMemoryInterventionRepository`, `FileDiscoveryTraceStore`, the
  optional `AnthropicLLMClient`, `JsonlEventSink`, `FileEvidenceStore`) is
  constructed once from `Settings` and reused across requests. `POST
  /runs` builds an `AppContext` and optional `DiscoveryGoal` from the
  request body and calls `run_capability`; `GET /runs/{run_id}` reads
  back a previously saved `RunResult` from `RunResultStore`, an in-memory
  dict that is deliberately *not* part of `RunOrchestrator` itself --
  it's the API layer's own bookkeeping for "retrieve a result later,"
  which the orchestrator's own synchronous request/response contract
  doesn't need. No intervention claim/resolve endpoints yet -- this is
  explicitly not the operator UI (Phase 12/13's job).
- `Settings.capability_dir` and five new `EventType` members
  (`capability_search_started`, `capability_match_found`,
  `discovery_required`, `discovery_started`, `intervention_requested`)
  were added to the existing `observability` package to match
  `.CLAUDE/06`'s structured-logging event vocabulary and give
  `RunOrchestrator` a real, file-backed `CapabilityRepository` path in
  the API composition root.
- `tests/unit/test_run_orchestrator.py` (13 tests, pure unit tests against
  `FakeSurfaceAdapter`/`FakeLLMClient`, no browser, no live LLM): known
  capability -> replay success, business outcome, approval-required
  (creates intervention), blocked (creates no intervention), hard failure
  (creates intervention); no capability + no goal -> `DISCOVERY_REQUIRED`,
  no capability + goal but no LLM configured -> `DISCOVERY_REQUIRED`,
  ambiguous match -> `AMBIGUOUS_CAPABILITY` without attempting discovery;
  successful discovery -> artifact built/stored/registered and replayed
  for the actual result, discovery approval-required / blocked / hard
  failure (approval and failure create interventions, blocked does not);
  and one test proving a single `run_id` correlates the discovery trace
  file and the follow-up replay. `tests/unit/test_api_runs.py` (3 tests)
  is a thin smoke test of the real FastAPI composition root via
  `TestClient`, deliberately restricted to the one branch that's always
  safe to hit without Playwright (an unregistrable capability_id with no
  discovery goal resolves to `DISCOVERY_REQUIRED` before
  `run_capability` ever touches `surface_factory`) -- every other branch
  is already covered against `RunOrchestrator` directly, so re-proving it
  through HTTP would only be slower, not more thorough.
- On-device unit suite: 130 passed, 13 deselected (`not integration and
  not live_llm`) -- zero regressions from Phase 10's 114. No
  cloud-sandbox round-trip needed for this phase: `test_run_orchestrator
  .py` and `test_api_runs.py` are both pure unit tests against fakes.

### Phase 12 — Persistent human handoff + live-session ownership/resume

- **Closes Phase 11's explicitly-flagged gap.** Phase 11's `async with
  self._surface_factory() as surface: ...` closed the browser before an
  `InterventionRequest` could even be created, so `session_id` was always
  `None` and there was nothing for an operator to take control of.
  `RunOrchestrator` now manually acquires the surface's async context
  manager (`cm = self._surface_factory(); surface = await
  cm.__aenter__()`) instead of using `async with`, and simply does not
  call `cm.__aexit__` when a replay/discovery attempt escalates -- the
  still-open `(surface, cm)` pair is registered in a new `SessionRegistry`
  under a fresh `session_id` instead. The browser is only closed, from one
  centralized `_close_surface` helper, once a run reaches a genuinely
  terminal outcome (`SUCCESS`, `BUSINESS_OUTCOME`, `BLOCKED`, or an
  operator explicitly cancels). Every existing Phase 11 call site and test
  is unaffected -- `session_registry` is an optional constructor
  parameter defaulting to a fresh, empty registry.
- **New `cuas.handoff.session` module (`AutomationSession`,
  `SessionRegistry`) is a deliberate, documented exception to this
  codebase's "one ABC + one real implementation" rule** (every other seam
  -- `ArtifactRepository`, `CapabilityRepository`, `InterventionRepository`,
  `PolicyEngine`, `LLMClient`, `SurfaceAdapter` -- follows it). An
  `AutomationSession` holds a live, in-process Python object (an open
  Playwright browser/page reachable only through the exact `SurfaceAdapter`
  instance wrapping it) that cannot be serialized, handed to another
  process, or meaningfully reconstructed from an "alternative backend" --
  there is no file-backed or database-backed equivalent of "a running
  browser." An ABC here would gesture at a swappability that doesn't
  exist, so `SessionRegistry` is a single plain class. Its unavoidable,
  explicitly-documented consequence: a live session cannot survive this
  process restarting. If the API process restarts while an intervention is
  `PENDING`, the persisted `InterventionRequest` (below) survives and
  still shows up in an operator's queue, but the browser its `session_id`
  named does not -- `resume_run` then raises `SessionNotFoundError` rather
  than silently fabricating a new session. Surviving a process restart
  would need a genuinely different mechanism (e.g. a long-running,
  out-of-process browser server the API reconnects to over CDP) --
  squarely Phase 13+ territory, not something this phase's registry
  pretends to solve.
- **`ReplayEngine.run(..., resume_from_step_id=...)`** (new optional
  parameter, `None` by default -- every existing call site behaves
  byte-for-byte as before). Given a step id, `run()` skips every step
  *before* it (already executed against this exact, still-open surface --
  re-running them would repeat real-world actions, e.g. re-submitting a
  form) and treats that step's own policy check as already satisfied
  (either a human just explicitly approved it, or policy already returned
  `ALLOW` for it the first time -- policy decisions are a pure function of
  `(step, context)`, so re-evaluating could only repeat the same
  decision). Every step *after* the resumed one still gets a full, real
  policy check. A dedicated sentinel, `ReplayEngine.RESUME_AFTER_ALL_STEPS`,
  skips the step loop entirely and goes straight to output extraction --
  for the case where the original attempt executed every step but failed
  during output extraction or success-condition verification (no step id
  to resume at). **Explicitly-flagged simplification:** resuming a step
  that failed mid-action re-runs the *entire* step (its action, then its
  checkpoint) rather than the exact sub-phase that failed. This is safe
  for this system's actions (fill/click/navigate/read all idempotent
  enough to repeat once) and keeps the resume mechanism to one parameter
  instead of a second, finer-grained "resume phase" concept.
- **Discovery-path resume is deliberately simpler than replay-path
  resume.** `DiscoveryEngine` has no notion of resuming an LLM
  conversation from a specific mid-loop point -- that would mean
  serializing and replaying model context, a much larger feature this
  phase does not attempt. Instead, resuming a paused discovery gives the
  LLM a fresh `DiscoveryEngine.run()` call (a new reasoning attempt from
  scratch) on the exact same, still-open surface, so whatever the operator
  did while in `HUMAN_CONTROL` (dismissed a blocking dialog, navigated
  past a broken page, manually satisfied a captcha) is reflected in what
  the model observes next. An explicit simplification, not an oversight.
- **`FileInterventionRepository`** (new, alongside the Phase 11
  `InMemoryInterventionRepository`, which stays for fast unit tests):
  plain JSON files on disk, one per intervention id, using the same
  "small, mutable record expected to change in place" overwrite-on-save
  idiom `FileCapabilityRepository` already established -- status moves
  `PENDING -> CLAIMED -> RESOLVED/CANCELLED` over the record's life, and
  each transition is a `save()` that overwrites the same file. Wired into
  `api/main.py`'s composition root as the real path, per the phase
  instruction ("Add a file-backed or similarly simple persistent
  InterventionRepository for the real path").
- **New `cuas.handoff.errors` module** (`HandoffError` base,
  `InterventionStateError`, `InterventionOwnershipError`,
  `SessionNotFoundError`) -- deliberately separate from
  `cuas.domain.errors.AutomationError` and its subclasses, which model
  "the automation itself hit a hard failure while driving a surface."
  These model a different kind of problem: a caller (an operator, the API
  layer) asking `RunOrchestrator` to do something the current
  intervention/session state does not allow (double-claiming, resuming
  before human control is marked complete, an ownership mismatch). Keeping
  them a separate hierarchy lets the API layer map each to a distinct,
  meaningful HTTP status (409 for a state conflict) without conflating
  them with automation failures.
- **`RunOrchestrator`'s new handoff-lifecycle methods** implement
  `.CLAUDE/04`'s control state machine directly:
  `claim_intervention(intervention_id, operator_id)` -- `PENDING ->
  CLAIMED`, and (if a live session is attached) `PAUSED_WAITING_FOR_HUMAN
  -> HUMAN_CONTROL`; raises `InterventionStateError` on a double-claim
  rather than silently overwriting `claimed_by`.
  `mark_human_control_complete(intervention_id, operator_id)` -- `CLAIMED
  -> RESOLVED`, and `HUMAN_CONTROL -> RESUME_REQUESTED`; requires
  `operator_id` to match whoever claimed it (`InterventionOwnershipError`
  otherwise) -- the explicit, simple stand-in this phase uses instead of
  real IAM, exactly as instructed ("Keep authorization assumptions
  explicit rather than pretending to implement production IAM").
  `resume_run(intervention_id)` -- requires the intervention to be
  `RESOLVED` with its session in `RESUME_REQUESTED` (raises
  `InterventionStateError` otherwise), then dispatches on
  `session.origin` to `_run_replay`/`_run_discovery` with
  `resume_session_id` set, reusing `session.surface` and never calling
  `surface_factory` again -- verified structurally in every lifecycle test
  via `sequential_surface_factory`'s own `assert remaining` guard.
  `cancel_intervention(intervention_id, operator_id)` -- not named as a
  required endpoint by the phase instructions, but `InterventionStatus
  .CANCELLED` already existed in the domain vocabulary (Phase 4/11
  scaffolding) with nothing that ever set it; this closes that gap and
  unconditionally cleans up whatever live session was still attached. The
  original `run_id` is preserved across every pause/claim/complete/resume
  cycle, including a second escalation on resume, exactly as instructed.
- **New API endpoints** on the existing thin FastAPI layer: `GET
  /interventions` (defaults to the pending queue; `?pending_only=false`
  for full history), `GET /interventions/{id}`, `POST
  /interventions/{id}/claim`, `POST /interventions/{id}/complete`, `POST
  /interventions/{id}/resume`, `POST /interventions/{id}/cancel`. Each
  maps `KeyError -> 404` and `HandoffError -> 409`. `RunResponse` and the
  new `InterventionResponse` gained a `session_id` field so an operator
  client can tell whether a live browser is genuinely still waiting.
- **New `EventType` members** (`intervention_claimed`,
  `human_control_completed`, `intervention_cancelled`, `run_resumed`)
  cover the rest of `.CLAUDE/04`'s control state machine in the structured
  event stream, so the full pause -> claim -> human control ->
  resume-requested -> resumed lifecycle of one intervention is
  reconstructable from the event stream alone, the same "Traceability" bar
  Phase 7 set.
- **Tests** (28 new, all pure unit tests against `FakeSurfaceAdapter`/
  `FakeLLMClient` -- no browser, no live LLM, no cloud-sandbox round-trip
  needed): `tests/unit/test_replay_engine_resume.py` (4 tests) isolates
  `ReplayEngine.resume_from_step_id`/`RESUME_AFTER_ALL_STEPS` mechanics
  directly. `tests/unit/test_intervention_lifecycle.py` (13 tests) is the
  main orchestrator-level suite: escalation retains the same live session
  (`TestEscalationRetainsTheSession`, including the discovery path in
  `TestDiscoveryEscalationSession`); intervention persisted with a
  non-null `session_id`; claim transitions and double-claim/unknown-id
  rejection (`TestClaim`); human-control-complete transitions, rejected
  before claim, and rejected for the wrong operator
  (`TestHumanControlComplete`); resume rejected before human control is
  complete, resume continues the same `run_id` on the same session and
  cleans it up on a terminal outcome, and a full cycle against the real
  `FileInterventionRepository` (`TestResume`); cancel marks `CANCELLED`
  and closes the session (`TestCancel`). `tests/unit
  /test_intervention_repository.py` (7 tests) covers
  `FileInterventionRepository` storage mechanics (round-trip,
  overwrite-on-save, pending/all listing, unknown id, corrupt file) the
  same way `test_capability_repository.py` covers its file-backed
  counterpart. `tests/unit/test_api_interventions.py` (5 thin smoke
  tests) proves the HTTP plumbing for the endpoints above without a
  wiring bug, restricted to the empty-queue/404 paths that don't need
  Playwright or an LLM -- the same scope `test_api_runs.py` chose for
  `/runs`.
- **Not built this phase, by explicit instruction:** no noVNC/remote-control
  wiring for an operator to literally *touch* the paused browser --
  `claim_intervention` only records the state transition; the browser
  genuinely still exists, untouched, for whenever Phase 13's "headed
  browser + Xvfb + noVNC Docker path" adds that wiring.
- On-device unit suite: 158 passed, 13 deselected (`not integration and
  not live_llm`) -- zero regressions from Phase 11's 130 (130 + 28 new =
  158). No cloud-sandbox round-trip needed for this phase: every new test
  is a pure unit test against fakes.

### Phase 13 — Headed browser + Xvfb + noVNC Docker handoff path

- **Goal, and what was deliberately NOT touched:** make Phase 12's
  already-complete live-session handoff (`SessionRegistry` +
  `InterventionRequest`) *observable and controllable* through the
  canonical `docker compose` demo path, without redesigning any part of
  that session lifecycle. No orchestration, `SessionRegistry`, or
  `InterventionRequest` code changed this phase. `RunOrchestrator`/
  `ReplayEngine`/`DiscoveryEngine` remain completely unaware that a human
  might be watching over VNC -- as far as they're concerned, they're
  driving one `SurfaceAdapter`, exactly as before.
- **`Settings.playwright_headless: bool = True`** (new field) is the
  entire code-level change needed. `api/main.py`'s composition root now
  builds `_surface_factory = partial(launch_playwright_surface,
  headless=_settings.playwright_headless)` instead of passing
  `launch_playwright_surface` directly -- still a zero-arg callable
  returning an async context manager, so `RunOrchestrator`'s
  `SurfaceFactory` type and every existing call site are unaffected.
  Local dev, the unit suite, and CI all keep the default `True`
  (headless, no display needed); only the automation Compose service
  overrides it to `false` via a plain (non-secret) environment variable.
- **`launch_playwright_surface` (Phase 3, `surface/playwright_adapter.py`)
  gained one conditional branch:** when `headless=False`, it launches
  Chromium with `--start-maximized` and opens its context with
  `no_viewport=True`, so the page fills whatever screen size Xvfb reports
  instead of floating inside a fixed default viewport. Purely cosmetic
  for the demo; the `headless=True` branch every existing test exercises
  is byte-for-byte unchanged (`args=[]`, plain `new_context()`).
- **Why noVNC is genuinely "the same session," not a second one:**
  Playwright launches one headed Chromium process, which needs
  somewhere to render -- `Xvfb` (Phase 13, new) provides that virtual X
  display inside the container. `x11vnc` (new) serves that exact display
  over VNC. `websockify`, using Debian/Ubuntu's `novnc` package's static
  web client (both new), bridges that VNC stream to a browser tab over
  WebSocket. None of these three is a browser, a CDP client, or anything
  that could open its own Chromium -- they only relay pixels out and
  mouse/keyboard input back in, to and from the one X display Chromium is
  already attached to. An operator opening the noVNC tab is looking at,
  and clicking into, the literal same window/CDP session
  `RunOrchestrator`/`SessionRegistry` paused; there is no second surface
  to create or reconcile.
- **`docker/automation-entrypoint.sh`** (new) replaces the Dockerfile's
  old plain `CMD ["uvicorn", ...]`. In order: starts `Xvfb` on `$DISPLAY`
  (default `:99`) at a configurable `SCREEN_GEOMETRY` (default
  `1280x800x24`); polls for the X11 socket to exist before continuing;
  starts `x11vnc` against that display on `$VNC_PORT` (default `5900`);
  starts `websockify` serving `novnc`'s static files and proxying
  `$NOVNC_PORT` (default `6080`) to that x11vnc port; finally `exec`s
  uvicorn as the container's actual foreground/signal-receiving process.
  **Explicitly-flagged simplifications:** (1) `x11vnc -nopw` -- no VNC
  password. Acceptable for a take-home demo whose ports are only
  published to the host's own port mapping, not for anything exposed
  beyond localhost without adding real VNC/noVNC auth first -- called out
  in both the script and README.md. (2) No process supervisor
  (supervisord/s6/etc): Xvfb/x11vnc/websockify are plain backgrounded
  processes with no crash-restart logic; only a full `docker compose
  restart automation` recovers if one dies. Both are documented,
  deliberate, demo-scope trade-offs, not oversights.
- **Dockerfile** (automation service): added `xvfb`, `x11vnc`, `novnc`,
  `websockify` to the existing `apt-get install` step (same layer as
  before, no new build stage), copies and chmods the new entrypoint
  script into `/usr/local/bin/`, sets `ENV DISPLAY=:99` (so Playwright's
  own `$DISPLAY` env lookup on Linux finds the right display without any
  code change), exposes `6080` alongside the existing `8000`, and
  replaces `CMD` with `ENTRYPOINT ["/usr/local/bin/automation-entrypoint.sh"]`.
- **docker-compose.yml:** `automation` now also publishes `6080:6080`
  (noVNC) and sets `PLAYWRIGHT_HEADLESS=false` directly in
  `environment:` (not a secret, so no need to route it through `.env`).
  Added `env_file: [.env]` to `automation` specifically so
  `ANTHROPIC_API_KEY` (and any future secret) is injected into the
  container's environment at `docker compose up` time from the
  gitignored, host-side `.env` file -- never copied into the image by
  either Dockerfile. `./data:/app/data` (unchanged from Phase 2) already
  covers every `Settings.*_dir` (all `data/...`), so artifacts,
  capabilities, logs, evidence, discovery traces, and interventions all
  persist across `up`/`down` with no new mounts needed. Added a
  `healthcheck` to both services (a plain stdlib `urllib` request against
  each service's own health-ish endpoint -- deliberately not `curl`, to
  avoid adding a package to either image just for this) and made
  `automation` `depends_on: demo-app: condition: service_healthy`, so
  Compose won't start automation racing an unready demo-app.
- **Non-Docker verification, explicitly bounded (as originally written --
  see the "Update" bullet below for what happened next):** this sandbox has no
  Docker CLI/socket access, so `docker compose build`/`up` were not run
  against the actual images -- that remains the user's own
  `docker compose build && docker compose up` on their Mac, exactly as
  every prior phase's Docker work has been. What *was* verified directly,
  against a real Playwright/Chromium install outside Docker (same
  cloud-sandbox round-trip pattern used for every prior phase's
  Playwright-touching work): (1) the exact new `headless=False` launch
  code (`--start-maximized` + `no_viewport=True`) actually launches and
  navigates successfully under a real `Xvfb`; (2) the full `Xvfb -> x11vnc
  -> websockify/noVNC` chain, using the identical commands and flags the
  entrypoint script runs, actually comes up (all three processes attach
  correctly, `GET /vnc.html` returns 200), and a real headed Chromium
  launched against that same display renders correctly while the chain is
  live. This is meaningfully more confidence than "the packages exist and
  the shell script looks right," but it is still not a substitute for an
  actual `docker compose build && up` -- flagged as such in README.md and
  both Docker files' own comments, per the standing instruction never to
  claim Docker verification that didn't happen. (Superseded below: the
  user has since run the actual `docker compose build && up` and
  confirmed it.)
- **Tests** (8 new, all pure non-Docker unit tests -- no browser, no
  Docker daemon, no Xvfb needed to run them):
  `tests/unit/test_settings.py` (3 tests) covers
  `Settings.playwright_headless`'s default and environment-variable
  override. `tests/unit/test_docker_setup_config.py` (5 tests) pins the
  plain-text contents of `Dockerfile`, `docker-compose.yml`, and
  `docker/automation-entrypoint.sh` against the specific pieces this
  phase depends on (the four new apt packages, the exposed ports, the
  headed-mode env var, the entrypoint script's Xvfb/x11vnc/websockify
  calls and its `exec uvicorn` as the literal last statement, the
  `env_file`/no-`.env`-in-the-image split, and the compose healthcheck/
  `depends_on` gate) -- explicitly framed in that file's own docstring as
  config-drift protection, not Docker verification.
- On-device unit suite: 166 passed, 13 deselected (`not integration and
  not live_llm`) -- zero regressions from Phase 12's 158 (158 + 8 new =
  166). No cloud-sandbox round-trip needed to run the on-device suite
  itself (every new test is a pure unit test against fakes/plain text);
  the cloud-sandbox round-trip described above was used only for the
  extra, above-and-beyond manual verification of the headed-launch and
  Xvfb/x11vnc/noVNC mechanics, not for running `pytest`.
- **Not built this phase, by explicit instruction:** no Kubernetes, no
  browser farm, no external queue, no remote browser service. Still a
  single Compose file, two services, direct synchronous orchestration.
- **Update -- real Docker verification, on the user's actual Mac:**
  `docker compose build` completed successfully, both services started,
  `GET http://localhost:8000/health` returned 200, Xvfb/x11vnc/noVNC all
  started correctly inside the `automation` container, and the Compose
  network between `automation` and `demo-app` works. This phase's Docker
  setup is no longer unverified-by-build -- README.md's Docker section
  and docker-compose.yml's own top comment have been updated to say so
  instead of the earlier "not run from here" framing (which remains true
  of the sandbox itself, just no longer true of this build's actual
  status). Still pending, and explicitly not yet claimed as done: the
  noVNC same-session intervention/resume flow specifically (README's
  checklist steps 4-9 -- trigger a run to intervention, watch/claim/
  manipulate the paused browser over noVNC, resume, confirm it continues
  the same run/session). The user is running that check next; Phase 14
  does not start until they confirm it. (Superseded below: the user has
  since run this flow for real, twice.)

- **Update -- the noVNC same-session handoff flow itself is now
  confirmed, on the user's actual Mac, via the real Dockerized API path
  (not this tool's own out-of-Docker verification round-trips above):**
  two independent escalation scenarios, both preserving the original
  `run_id`/`session_id` end to end.

  1. **Approval-required.** `close_member_account` (M1001/A-5001) paused
     at the policy-gated `close_account` click
     (`run_id 92c86535dca14af0aa14f9e2d8f5942f`,
     `intervention_id cb2396bae1b74d6abab7b13095ba69cc`,
     `session_id c896ebe1b323405aae0f08539bb4b817`). The browser stayed
     open on the close-confirmation page, visible over noVNC; the
     operator deliberately did not click the gated button themselves
     (doing so would leave nothing for the resumed step to click --
     ReplayEngine re-executes the exact step it paused at for an
     `APPROVAL_REQUIRED` escalation). Claim -> complete -> resume: the
     same `run_id` finished `success`, and *automation itself* performed
     the click, producing `{"account_status": "Closed"}`.
  2. **Hard failure, human resolves via noVNC.** `get_savings_balance`
     with `member_id="Smith"` (ambiguous -- matches two seeded members)
     failed the `submit_search` checkpoint
     (`run_id 720f35e5933645f3920ae893b3f1bb64`, `error_code
     CHECKPOINT_FAILED`, `intervention_id
     d40a68d8004d49b0a898b893f139a93f`, `session_id
     07e576a073e446fcad356a2a05a44f6f`), and a real screenshot + DOM
     snapshot were captured to
     `data/evidence/720f35e5933645f3920ae893b3f1bb64/`. Here the operator
     *did* act in the browser via noVNC -- navigating the same live
     session to the correct member (M1002) themselves, since a
     checkpoint failure (unlike an approval gate) has no single "the
     human just approved this" step for automation to safely re-execute.
     Claim -> complete -> resume: `ReplayEngine.RESUME_AFTER_ALL_STEPS`
     (every `FAILED` result resumes this way, regardless of which step
     failed -- see `replay/engine.py`'s own docstring) skipped straight
     to output re-extraction against wherever the operator left the
     page, and the same `run_id` finished `success` with
     `savings_balance: 9900.00` -- M1002's real seeded savings balance,
     confirming the read came from the human-navigated page, not a
     coincidence.

  Together these two runs exercise both of `RunOrchestrator`'s resume
  branches (`resume_from_step_id` pointing at one specific step vs.
  `RESUME_AFTER_ALL_STEPS`) against the real API, real Docker, and real
  noVNC control -- not a test double anywhere. README.md and
  docker-compose.yml's top comment have been updated to state this as
  verified, not pending. Phase 14 is unblocked.

### Bugfix (during Phase 14 evidence capture) — `get_savings_balance` failed through the real orchestrator/API

**Symptom, reported by the user from the real Docker container:** `POST
/runs` for `get_savings_balance` with `member_id="M1001"` and with
`member_id="no-such-member"` both returned `FAILED` /
`TARGET_NOT_FOUND`, `"Could not resolve target for fill: tried 1
candidate(s)"` — both should have been `SUCCESS` / `BUSINESS_OUTCOME`
respectively.

**Root cause.** `RunOrchestrator._run_replay` acquires a brand-new
Playwright surface for every non-resumed run (`cm =
self._surface_factory(); surface = await cm.__aenter__()`, then straight
into `ReplayEngine.run()` — see orchestration/orchestrator.py) with
nothing in between that navigates it anywhere, so the surface starts on
`about:blank`. `get_savings_balance()` (tests/fixtures/sample_artifacts.py)
has no `NAVIGATE` step of its own — its first step (`fill_member_id`)
assumes the surface is already sitting on the demo app's search page, a
bare `role=textbox` locator with no fallback. That assumption was true in
every place this fixture had ever been exercised before: every
`ReplayEngine`-direct test (`tests/integration/test_replay_engine_e2e.py`)
navigates the surface itself before calling `engine.run()`, and every
`RunOrchestrator` unit test (`tests/unit/test_run_orchestrator.py`) uses
`FakeSurfaceAdapter`, which has no notion of page state at all — so
nothing had ever exercised this fixture through `RunOrchestrator`'s real,
freshly-acquired surface. `close_member_account` (Phase 13's registered
capability) never hit this because it was authored *with* its own leading
`NAVIGATE` step from the start.

This is not a locator bug (`role=textbox` correctly matches the demo
app's one search box, once the surface is actually on that page) and not
an orchestrator defect in the sense of "should navigate but doesn't" —
`RunOrchestrator`/`RunRequest` have no field anywhere carrying a generic
per-capability "start URL" for an already-resolved capability (that
concept exists only on `DiscoveryGoal.start_url`, for *new* capabilities
being discovered), so there is no other place in the current architecture
a start point could come from except the artifact's own steps.

**Fix.** The shared, already-tested fixture in
`tests/fixtures/sample_artifacts.py` is untouched — it's reused by ~15
unit tests and 4 integration tests with differing base URLs per
environment (pytest's `demo_app_base_url` uses a fresh ephemeral port
every run; the real container uses the fixed Compose hostname
`http://demo-app:8080`), which is exactly why it never had its own
navigation step. `scripts/register_get_savings_balance_capability.py` now
builds a small deployment-scoped variant instead: the fixture's exact,
unmodified steps/outputs/success_condition/business_outcomes/
recoverable_conditions, with one additional leading `NAVIGATE` step
(`open_search_page`, intent `search_member` — `ALLOW` in
`DEFAULT_GLOBAL_INTENT_POLICY`, own checkpoint) pointing at
`http://demo-app:8080/` — the same self-contained-navigation pattern
`close_member_account` already used, built through the real `Artifact()`
constructor (full pydantic validation) rather than an unvalidated
`model_copy`. Registered as version `1.0.1` (`1.0.0` — the broken
registration — stays on disk untouched; `FileArtifactRepository.save()`
deliberately refuses to overwrite a published version, so this is the
same monotonic-patch-bump convention `RunOrchestrator._next_version` uses
elsewhere); the capability record was repointed at `1.0.1` (an ordinary,
idempotent metadata update, by design).

**Regression test added:** `tests/integration/test_run_orchestrator_e2e.py`
— three `@pytest.mark.integration` tests, the first of their kind to
exercise the real `RunOrchestrator` + real `launch_playwright_surface` +
real demo app together (everything else either fakes the surface or
bypasses `RunOrchestrator`): `M1001` → `SUCCESS`, `savings_balance ==
Decimal("18204.55")`; `no-such-member` → `BUSINESS_OUTCOME`,
`MEMBER_NOT_FOUND`; `Smith` (ambiguous — matches two seeded members) →
`FAILED` with a real `intervention_id`/`session_id`, then
`cancel_intervention` to clean up the paused session. Each test registers
its own deployment-scoped artifact variant (same construction as the
registration script, pointed at the test's own ephemeral
`demo_app_base_url` instead of the Compose hostname) into `tmp_path`-backed
repositories — no shared/global state, no change to `data/`.

**Verified:** all 3 new tests pass against a real browser (cloud-sandbox
round-trip, §5 — no Docker in that sandbox, so this is the same
extra-diligence pattern used before, not equivalent to the user's own
Docker verification). Full suite re-run `-m "not live_llm"`: 181 passed
(178 previously-passing + 3 new), 1 deselected (`live_llm`), zero
failures, zero regressions. `close_member_account` was not touched —
its own real run (`run_id` `92c86535dca14af0aa14f9e2d8f5942f`) already
proved it correct, and this bug never applied to it.

**Not changed:** the artifact schema, `ReplayEngine`, `RunOrchestrator`,
`LayeredPolicyEngine`, any locator, or `tests/fixtures/sample_artifacts.py`
itself.


### Bugfix (pre-live-LLM-run prep) — discovered artifacts had no leading NAVIGATE step

**Symptom (found by read-only inspection, before any live Anthropic call
was made):** `DiscoveryEngine.run()` navigates to `DiscoveryGoal.start_url`
*before* the step loop begins and before a single trace step is recorded
(`await self._surface.navigate(goal.start_url)` precedes the loop
entirely, engine.py). `ArtifactBuilder.build()` reconstructs an artifact's
`steps` only from what the trace actually recorded, so it never carried a
NAVIGATE step for that initial, pre-loop navigation — for a discovery
whose only useful model action was a single READ (e.g. the goal's
`start_url` already puts the surface where the value lives), the built
artifact would have had **zero** `Step`s at all (`_build_steps` excludes
READ actions entirely; they become an `OutputSpec` instead). Any
subsequent replay of such an artifact — including `RunOrchestrator`'s own
*mandatory* verification replay immediately after a successful discovery,
which always runs on a brand-new, unnavigated surface exactly like every
other non-resumed run — would try to extract its output from
`about:blank`. This is the same defect class as the `get_savings_balance`
bugfix above, except structural to the discovery→artifact pipeline itself
rather than one hand-authored fixture: *every* discovered capability was
affected, regardless of which goal produced it.

**Root cause:** `ArtifactBuilder` never referenced `goal.start_url` at
all (confirmed: zero occurrences of `start_url`/`NAVIGATE` anywhere in
`artifact_builder/builder.py` before this fix) — the deterministic setup
navigation discovery itself depends on was simply never carried forward
into the reusable artifact.

**Fix (`src/cuas/artifact_builder/builder.py` only):** `build()` now
prepends one deterministic `NAVIGATE` step to `goal.start_url`
(`_build_navigate_to_start_step`), added *after* `_overall_intent`/
`_overall_risk` are computed from the real, model-driven steps (so the
synthetic step can't skew what the artifact's safety metadata says about
its own intent/risk) and before the artifact is constructed. This step's
`risk` is explicitly set to `SAFE` — the one deliberate exception to "every
constructed Step leaves risk unset" — because this navigation was never
itself a policy decision `DiscoveryEngine` made in the first place (it
runs unconditionally, before any `PolicyEngine.evaluate()` call exists for
it); marking it `SAFE` here only makes replay trust it exactly as much as
discovery already implicitly did, via `LayeredPolicyEngine`'s ordinary
risk-as-opinion mechanism (`action.model_fields_set`), not a new bypass.
`DiscoveryGoal.start_url` was already a plain field on the `goal` object
`build()` already receives as a parameter — no interface change was
needed to access it.

**Deliberately not done:** no change to `DiscoveryEngine` (the trace
continues to represent only what the model actually did — this navigation
was never an LLM decision, so it does not belong in the trace as if it
were one); no capability-specific registration workaround (unlike the
`get_savings_balance` fix above, which predates this one and was
necessarily a one-off script since that artifact was hand-authored, not
discovered); no change to any existing, published artifact
(`get_savings_balance` `1.0.0`/`1.0.1`, `close_member_account` `1.0.0` —
none of these were built by `ArtifactBuilder` and none were touched); no
new replay special-casing (the prepended step is an ordinary `Step` using
the existing `Artifact`/`Action` schema and the normal replay path, same
as every hand-authored NAVIGATE step already in this repo).

**Regression tests added:**
- `tests/unit/test_artifact_builder.py`: two new tests —
  `test_discovered_artifact_gets_a_leading_navigate_to_start_step` (a
  normal fill/click/read trace gets a leading NAVIGATE to `start_url`,
  followed by the original actions in their original order) and
  `test_read_only_discovery_still_gets_a_leading_navigate_step` (the
  sharpest case: a trace whose only executed action was a READ still
  produces a one-step artifact — the NAVIGATE — instead of zero, and
  `safety.intent` still falls back to the output-name convention rather
  than being skewed by the synthetic step). Five pre-existing tests in
  the same file had hardcoded step counts/indices from before the fix
  (`test_clean_successful_trace_becomes_an_artifact`,
  `test_trace_with_a_wrong_turn_detour_produces_an_artifact_without_it`,
  `test_sensitive_literal_values_become_typed_input_placeholders`,
  `test_adjacent_duplicate_actions_are_collapsed_conservatively`,
  `test_non_adjacent_repeats_are_preserved_not_deduplicated`) and were
  updated to account for the new leading step, not reverted.
- `tests/integration/test_artifact_builder_e2e.py`: the existing
  real-discovery-to-real-replay test used to call
  `await replay_surface.navigate(...)` itself before `ReplayEngine.run()`
  — exactly the test-harness pattern that had masked this bug (and the
  earlier `get_savings_balance` one) from ever being caught. That manual
  navigate call was removed; the test now proves the artifact's *own*
  leading NAVIGATE step is what gets a genuinely fresh, unnavigated
  surface (`about:blank`) to the right page, matching what
  `RunOrchestrator` actually hands every non-resumed run in production.

**Verified:** `tests/unit/test_artifact_builder.py` (15 tests, all
passing) + full deterministic suite `-m "not integration and not
live_llm"` (168 passed, 16 deselected, zero regressions) run directly on
the developer's machine. `tests/integration/test_artifact_builder_e2e.py`
and `tests/integration/test_run_orchestrator_e2e.py` (4 tests total) were
additionally run for real against a live browser via the cloud-sandbox
round-trip (§5) — all 4 pass, including the now-unmasked
fresh-surface-replay assertion. No Anthropic call was made; this entire
fix and its verification used `FakeLLMClient` only, per instruction —
this is a pipeline-correctness fix, not evidence of live LLM usage.


### Bugfix (first genuine Anthropic-backed discovery run) — "done" could be accepted with nothing materializable behind it

**Symptom (real Anthropic call, run `d17d9a2fd9a8483c9ca278976f1cf520`,
capability `discover_savings_balance_demo`):** the model correctly
observed the Savings account (`A-5002`, balance `$18204.55`) already
rendered on the page, and its first and only response was
`{"reasoning": "...the task is complete.", "done": true}` -- zero
proposed actions. `DiscoveryEngine` accepted this immediately (no
`success_checkpoint` was declared for this goal, so
`_verify_success_checkpoint` trivially returned `True`) and reported
`DiscoveryStatus.SUCCESS` after a single, entirely inert step.
`ArtifactBuilder.build()` then correctly refused to build anything from
it: `"trace 'd17d9a2fd9a8483c9ca278976f1cf520' has no executed actions;
nothing to build an artifact from"` -- an entirely predictable failure,
just one that should never have been allowed to reach `ArtifactBuilder`
in the first place. `RunOrchestrator` escalated to a human intervention,
and the run ultimately reported `failed`. This real run (its log, trace,
and intervention record) is preserved exactly as captured and was not
modified for this fix -- it is the evidence that exposed the gap.

Separately, this same run's JSONL log showed **two** identical
`discovery_engine: run_completed status=success` events 4.5ms apart.

**Root cause:** `DiscoveryEngine` had a `success_checkpoint`-verification
gate on a "done" claim, but no gate at all on whether discovery had
produced anything an artifact could actually be built from. Seeing the
answer already present in `Observation.visible_text` was treated as
equivalent to having *obtained* it through a replayable action, which it
is not -- `Observation.visible_text` is a debugging/model-context aid
(and, per Phase 8, is not even guaranteed to see inside iframes the way
`playwright_adapter`'s `page.inner_text("body")` does not), never itself
part of what gets replayed. The duplicate `run_completed` emission was a
second, unrelated bug in the same code path: the success/"done" branch had
its own inline `self._emit(..., EventType.RUN_COMPLETED, ...)` call, in
addition to the trailing, unconditional `self._emit(..., RUN_COMPLETED,
...)` every terminal branch already receives at the bottom of `run()`
(confirmed by reading `_finish()` and every other terminal branch --
`BUSINESS_OUTCOME`, `MALFORMED_MODEL_OUTPUT`, `LOOP_DETECTED`,
`MAX_STEPS_EXCEEDED`, `MAX_DURATION_EXCEEDED`, `MAX_TOKENS_EXCEEDED` --
none of which had this redundant second emit). Genuine duplicate
emission, not intentional layering.

**The invariant chosen:** `DiscoveryEngine._has_materializable_progress`
-- a "done" claim is only trusted once `history` contains at least one
entry with `outcome == "executed"` and `action.action_type ==
ActionType.READ`. This is the exact same requirement
`ArtifactBuilder.build()` already enforces completely independently and
unconditionally downstream: `_build_outputs` turns only executed READ
actions into a declared `OutputSpec`, and `build()` raises
`ArtifactBuildError` if `outputs` ends up empty, for *any* trace,
regardless of what the goal was for (confirmed: `build()` also requires
`trace.final_status == "success"` before it will even look at a trace at
all, and `DiscoveryStatus.SUCCESS` is assigned in exactly one place in
`engine.py`, gated by this same check -- there is no second path into
"success" that could bypass it, and no path from discovery's "success"
into `ArtifactBuilder` that doesn't first require that same status).
Deliberately narrower than "any executed action at all": an executed
click or fill unrelated to the goal must not count as progress just
because *something* happened, or a workflow could satisfy this
accidentally. READ is not an arbitrary, discovery-only bar picked to make
this one demo pass -- it is the identical, pre-existing downstream
requirement, checked earlier so the model gets a chance to correct course
instead of the whole run ending in a doomed, wasted intervention.

**How a premature `done=true` is handled:** rejected, not treated as a
terminal failure. The "done" turn's `trace_step` is still appended to
`trace.steps` (outcome `"declared_done"`, exactly as an unverified
`success_checkpoint` claim already was) -- the trace keeps recording
what actually happened -- but no `DiscoveryHistoryEntry` is created for
it (`DiscoveryHistoryEntry.action` is a required field; a "done" turn
proposes no action, so it was never representable in `history` before
this fix either, and still isn't after it), and the loop `continue`s to
the next turn instead of returning `DiscoveryStatus.SUCCESS`.

**How the corrective feedback is bounded:** by the existing
`DiscoveryLimits`, with zero new state beyond one boolean
(`needs_progress_reminder`). Once a "done" is rejected, every subsequent
call to `propose_action` has `_PROGRESS_REMINDER_TEXT` appended to an
*ephemeral* `Observation` built just for that call
(`observation_for_model`); the real `observation` variable, and
everything derived from it that gets persisted (`trace_step.
observation_url`/`observation_text_excerpt`), is completely untouched --
the trace never mixes real page content with injected system text, and
`ArtifactBuilder` never receives a fabricated action: the model must
still propose the READ itself, through the same structured
`action_type`/`target` schema every other turn uses. A model that never
produces a materializable READ simply keeps consuming turns from the
same `for step_index in range(limits.max_steps)` loop every other turn
already draws from, and falls through to the existing
`DiscoveryStatus.MAX_STEPS_EXCEEDED` branch once they run out -- no
separate retry budget, no new limit, no new `DiscoveryStatus` value
(`DiscoveryStatus.FAILED` remains declared but unused; it was not needed
here either).

**Duplicate `run_completed` fix:** removed the one redundant inline
`self._emit(run_id, EventType.RUN_COMPLETED, status="success", ...)`
call from the success branch. The trailing unconditional emit at the end
of `run()` already covers this branch like every other; nothing else
changed.

**Deliberately not done:** no change to the demo goal/capability
definition (the fix is structural, in `DiscoveryEngine`, not a prompt
tweak to make this one goal say "use a READ action"); no fabrication of
an action from the model's reasoning text anywhere (`ArtifactBuilder` was
not touched by this fix at all -- it was already correct, per the
previous bugfix entry above); no change to `Observation`,
`DiscoveryHistoryEntry`, or the `LLMClient` interface (the corrective
text rides on a call-scoped `Observation` copy, never a schema change);
no modification to the preserved real run's log, trace, or intervention
record (`d17d9a2fd9a8483c9ca278976f1cf520`) -- it remains exactly as
captured, as the evidence that exposed this gap.

**Regression tests added (`tests/unit/test_discovery_engine.py`):**
`test_done_with_no_executed_action_does_not_succeed_immediately`,
`test_repeatedly_declaring_done_without_progress_is_bounded_not_unbounded`,
`test_rejected_done_feeds_back_corrective_context_and_accepts_a_read`,
`test_done_after_a_materializable_read_succeeds`,
`test_flows_with_materializable_progress_are_unaffected_by_the_new_gate`,
`test_successful_run_emits_run_completed_exactly_once`. Five pre-existing
tests in the same file had scripted "done" immediately after only
fill/click actions, with no READ at all
(`test_normal_successful_discovery_executes_allowed_actions_and_stops_on_done`,
`test_action_type_specific_incompleteness_is_a_recoverable_execution_failure`,
`test_model_proposing_a_nonexistent_target_is_a_recoverable_execution_failure`,
`test_discovery_trace_is_persisted_separately_from_the_returned_result`,
`test_execution_failure_captures_evidence_but_policy_escalation_does_not`)
-- exactly the shape of trace this fix now correctly refuses to call
successful -- and were updated to execute a READ before their final
"done", not reverted; their actual subject (loop/policy/error-recovery
behavior) is otherwise unchanged. One more of the same shape was found in
`tests/integration/test_discovery_engine_e2e.py`
(`test_discovery_engine_finds_a_member_and_verifies_its_own_success_claim`)
and fixed the same way, against the real demo app. A new integration test,
`tests/integration/test_artifact_builder_e2e.py::
test_premature_done_is_corrected_then_artifact_replays_with_a_different_input`,
proves the full corrective loop end to end against a real browser: a
premature `done=true` on turn 0 is rejected, the model dismisses the
popup/searches/READs the balance, a second `done=true` is accepted, the
resulting artifact is identical in shape to the uncorrected flow's own
artifact, and it replays successfully with a *different* member id than
discovery ever used.

**Verified:** `tests/unit/test_discovery_engine.py` (17 tests, all
passing) + full deterministic suite `-m "not integration and not
live_llm"` (174 passed, 17 deselected, zero regressions elsewhere) run
directly against the repo. All integration tests (16, including the two
touched/added by this fix) were additionally run for real against a live
browser via the cloud-sandbox round-trip (§5) -- all pass. Combined
`-m "not live_llm"` run: 190 passed, 1 deselected (the `live_llm` smoke
test, which needs a real API key and is out of scope here). No Anthropic
call was made for any of this verification -- `FakeLLMClient` only, per
instruction. The real run that exposed this
(`d17d9a2fd9a8483c9ca278976f1cf520`) was not rerun.


### Bugfix (post-live-LLM-run investigation) — discovery approval/resume did not authorize or execute the escalated action

**Symptom (real Anthropic call, run `a4dac702e5cd4244ac2c22680834178a`,
capability `discover_savings_balance_demo`):** step 0 was a rejected
premature "done" (the materializable-progress gate above working exactly
as intended); step 1 proposed `{"action_type": "read", "intent":
"read_savings_account_balance", "target": {"strategy": "css", "selector":
"tr:has(td:first-child:contains('Savings')) td:nth-child(3)"}}`.
`read_savings_account_balance` appears in none of `LayeredPolicyEngine`'s
layers, and `_parse_proposal` deliberately never sets `Action.risk` (see
Phase 8's own docstring: this is what makes `model_fields_set`-based
fail-closed behavior govern every LLM proposal), so `evaluate()` had zero
opinions and correctly returned `REQUIRE_APPROVAL` -- confirmed, by
reading `policy.py` end to end, to be intentional fail-closed behavior,
not a bug: an unclassified intent must escalate, never silently execute.
`RunOrchestrator` created intervention `ea0efacbd2f2445fb3b01b7345fdd63d`
and kept the surface open in `AutomationSession`, exactly as designed.
Separately, verified empirically (an isolated Playwright script, no repo
files touched) that the proposed selector's `:contains()` pseudo-class is
genuine jQuery/Sizzle syntax, never valid CSS or Playwright -- Playwright
supports `:has()` (used correctly here) but throws a `SyntaxError` on
`:contains()`. This real run (its log, trace, and intervention record)
is preserved exactly as captured and was not modified for this fix, and
the intervention itself was never approved/resumed/claimed as part of
this work -- it remains `pending`.

**Root cause (the actual gap, found on inspection rather than by
reproducing a crash):** approving this intervention would not have done
what "approve → resume" implies. `_run_discovery`'s resume path called
`DiscoveryEngine.run(goal, context, limits, run_id=run_id)` again with no
resume-related parameter at all -- `DiscoveryEngine.run()` had no notion
of "the specific action that was escalated," so resuming meant a brand
new LLM reasoning attempt from an empty `history`/`step_index=0`, on the
same still-open surface. Concretely: (1) the exact policy-evaluated
`Action` that triggered `REQUIRE_APPROVAL` was discarded, never
authorized or executed -- an operator's approval had no mechanical effect
beyond unblocking a fresh guess; (2) a fresh `DiscoveryTrace()` was always
constructed at the top of `run()`, and `FileDiscoveryTraceStore.save()`
unconditionally overwrites `<run_id>.json`, so resuming *any* paused
discovery run (not just an approval) silently destroyed everything
recorded before the pause -- a second, independent bug found by reading
`trace.py`/`engine.py` together, not exercised by the real run above
(which was never resumed). Compared directly against `ReplayEngine.run
(resume_from_step_id=...)`, the already-correct analogous mechanism:
replay re-slices `artifact.steps` to the resume point and skips policy
for exactly that one step ("policy decisions here are pure functions of
(step, context), so re-evaluating could only repeat a decision already
made" -- replay's own comment); discovery had no equivalent at all.
Also confirmed by reading `claim_intervention`/`mark_human_control_complete`:
claiming an intervention only transfers control to an operator
(`PENDING → CLAIMED`, session → `HUMAN_CONTROL`) and is explicitly
non-authorizing; `mark_human_control_complete` (`CLAIMED → RESOLVED`,
session → `RESUME_REQUESTED`) is the actual approval step -- this
distinction was already correctly enforced by the existing state machine
and needed no change, only a real resume mechanism to make "approve"
mean something for discovery.

**The design chosen — `DiscoveryPendingApproval`:** a new typed bundle
(`discovery/models.py`) carrying exactly the in-process continuation
state needed to resume faithfully: `pending_action` (the exact,
already-parsed-and-evaluated `Action`), `step_index`, `history` (raw,
unredacted, exactly as the loop held it), and the loop's own
`total_tokens`/`needs_progress_reminder`/`last_signature`/
`consecutive_repeats`. `DiscoveryResult.pending_approval` is set only
when `status == DiscoveryStatus.APPROVAL_REQUIRED` -- never for `BLOCKED`
(a policy DENY is terminal, exactly like replay's own "a policy DENY is
not something an operator can approve past"; no continuation bundle is
ever constructed for it) and never for any other terminal status. It is
carried on `AutomationSession.pending_discovery_action` (a new field,
`handoff/session.py`) precisely like every other field on that
deliberately-plain, in-process-only dataclass -- never serialized into
`InterventionRequest.evidence`, `reason`, or anywhere else that crosses a
process/persistence boundary (confirmed: `RunResult`, the only shape the
API layer ever returns, has no `pending_approval`/`history`/`action`
field at all, so this cannot leak through that seam even by accident).

`DiscoveryEngine.run()` gained one new parameter, `resume:
DiscoveryPendingApproval | None`. When set: the existing trace is loaded
via `trace_store.load(run_id)` (falling back to a fresh one only if none
is found, e.g. `NullDiscoveryTraceStore`) instead of always constructing
a new `DiscoveryTrace` -- fixing the overwrite bug above, but
*deliberately scoped to only this branch*: every other discovery pause/
resume path (`MALFORMED_MODEL_OUTPUT`, `LOOP_DETECTED`,
`MAX_STEPS_EXCEEDED`, etc., resumed with no pending action) still
constructs a fresh trace exactly as before, per the explicit instruction
to limit this continuation behavior to `APPROVAL_REQUIRED` alone. The
loop's counters/history are restored from `resume` instead of reset to
empty/zero. Before the step loop begins, the exact `resume.pending_action`
is executed once, directly -- no call to `propose_action` (nothing new
was proposed), and no call to `self._policy.evaluate()` (that decision
was already made and is now authorized; re-evaluating a pure function of
`(action, context)` could only repeat it, mirroring `ReplayEngine`'s own
`skip_policy_for_step_id` reasoning exactly). This is the ONLY action
that ever bypasses a live policy check -- every subsequent proposal,
starting the very next loop iteration, goes through `self._policy.
evaluate()` completely normally and can escalate again on its own merits
(verified by a test where the very next proposal is also
`REQUIRE_APPROVAL` and does escalate again, rather than being silently
allowed through). The bypass execution and every normal turn's execution
now share one extracted method, `_execute_and_record` -- by construction,
there is no way for the resumed path's error handling to diverge from a
normal turn's: a `TargetNotFoundError`/`ValueError` becomes the same
"execution_failed" `DiscoveryTraceStep`/`DiscoveryHistoryEntry` either
way, and the bounded loop simply continues, exactly as it already does
for any other execution failure. "Approval" therefore means "this
specific action is authorized to run," never "this action will succeed."
`_finish()` gained a `pending_approval` passthrough parameter so the
escalation branch (the only call site that ever passes a non-`None`
value) can attach the freshly-built bundle to the returned
`DiscoveryResult`.

`RunOrchestrator._run_discovery` gained a `discovery_resume` parameter,
forwarded to `engine.run(resume=...)`; the `AutomationSession` created
(or updated, on a second escalation reusing the same session) now also
carries `pending_discovery_action = discovery_result.pending_approval`;
`resume_run`'s discovery dispatch passes `discovery_resume=session.
pending_discovery_action`. No change was needed to `claim_intervention`/
`mark_human_control_complete`/the `InterventionStatus`/`ControlState`
state machines themselves -- claim vs. approve semantics were already
correct; the gap was entirely in what a subsequent `resume_run` for
discovery actually *did*.

**Deliberately not done (explicit scope, per instruction):** no
`read_savings_account_balance` policy allowlist entry was added --
proposals carrying this intent still fail closed to `REQUIRE_APPROVAL`
every time, including a corrected retry with a fixed selector (see the
new integration test below, which needs two separate approve-and-resume
cycles for exactly this reason). No repair/sanitization of the invalid
`:contains()` selector anywhere -- the exact approved selector is
executed as-is and fails exactly as it did in the real run; the model
recovers with a different, valid selector on its next turn, through the
same generic "execution failure feeds back as history" path that already
existed and needed no changes (`_execute_and_record` is a pure extraction
of that existing code, not new failure-handling logic). No Anthropic call
was made anywhere in this work. No modification to
`a4dac702e5cd4244ac2c22680834178a`'s log/trace/intervention, or to the
earlier-preserved `d17d9a2fd9a8483c9ca278976f1cf520` evidence set. The
trace-overwrite bug is fixed only on the new `resume is not None` branch;
every other discovery-resume path still constructs a fresh trace on
resume exactly as before (a real, separate latent bug, left untouched
because a correctness dependency did not require fixing it here and the
instruction explicitly scoped this fix to `APPROVAL_REQUIRED`).

**Tests added:** `tests/unit/test_discovery_engine.py` --
`test_approval_escalation_captures_the_exact_pending_action`,
`test_denied_action_never_produces_a_pending_approval_bundle`,
`test_pending_approval_preserves_raw_history_while_result_history_stays_redacted`,
`test_resume_executes_the_exact_approved_action_without_a_new_llm_call`,
`test_only_the_resumed_action_bypasses_policy_subsequent_proposals_are_checked_normally`,
`test_approved_action_that_fails_becomes_execution_failed_and_the_model_can_recover`,
`test_max_steps_budget_survives_the_pause_rather_than_resetting`,
`test_token_budget_survives_the_pause_rather_than_resetting`,
`test_repetition_tracking_survives_the_pause_rather_than_resetting`,
`test_resuming_appends_to_the_existing_trace_instead_of_overwriting_it`.
`tests/unit/test_intervention_lifecycle.py` (new
`TestDiscoveryApprovedActionResume` class) --
`test_claiming_alone_does_not_authorize_resume`,
`test_resume_before_human_control_complete_is_rejected`,
`test_complete_then_resume_executes_the_exact_approved_action_without_a_new_llm_proposal`
(this last one structurally proves no second surface is ever acquired --
`sequential_surface_factory` is scripted with exactly one). A new
end-to-end integration test,
`tests/integration/test_discovery_engine_e2e.py::
test_resuming_an_approved_action_recovers_from_an_invalid_selector`,
reproduces the real scenario against the real demo app with `FakeLLMClient`
only: dismiss the popup, search M1001, propose the exact invalid
`:contains()` selector → `APPROVAL_REQUIRED` → resume executes it for
real and it fails with a real Playwright `TargetNotFoundError` → the
model's corrected-selector retry (same unclassified intent) escalates
*again* → a second resume executes the corrected selector for real,
reading the real page's actual savings balance (`$18204.55`) → a
materializable-progress-satisfying `done` succeeds -- while also
asserting the persisted trace file's pre-pause steps survive unchanged
and further steps are appended after them.

**Verified:** `tests/unit/test_discovery_engine.py` (27 tests, all
passing) and `tests/unit/test_intervention_lifecycle.py` (16 tests, all
passing) run directly against the repo. Full deterministic suite `-m
"not integration and not live_llm"`: 187 passed, 18 deselected, zero
regressions against the 174-test baseline before this change. Full
integration suite (17 tests, including the one new test above) run for
real against a live headless browser via the cloud-sandbox tar/stage/
extract/venv round-trip (§5), byte-for-byte the same source files
verified there as committed here (checksummed): all pass. No Anthropic
call was made for any of this verification. Diff reviewed end to end:
exactly the 8 intended files changed (`discovery/models.py`,
`discovery/engine.py`, `discovery/__init__.py`, `handoff/session.py`,
`orchestration/orchestrator.py`, plus the three test files); confirmed
`pending_approval`/`pending_discovery_action`/raw `history` never reach
`InterventionRequest`, `EvidenceStore`, any `RunEvent.details`, or
`RunResult` -- only the in-process `AutomationSession`, exactly like
every other live-session field. The real run
(`a4dac702e5cd4244ac2c22680834178a`) and its intervention
(`ea0efacbd2f2445fb3b01b7345fdd63d`, still `pending`) were not touched.
### Robustness fix (post-live-LLM-run investigation) -- malformed model output was needlessly terminal and leaked a raw KeyError message

**Symptom (real Anthropic call, run `e199e82bd3774cf6af2124d699ca5cfa`,
capability `discover_savings_balance_demo`):** step 0 was a correctly
rejected premature `done=true` (the materializable-progress gate above
working exactly as intended). Step 1 proposed `{"action_type": "read",
"reasoning": "...", "target": {"strategy": "css", "selector": "table
tr:has(td:first-child:contains('Savings')) td:nth-child(3)"}}` -- a real,
structurally valid tool_use block that simply omitted `intent`.
`_parse_proposal`'s `intent = proposal["intent"]` raised a bare
`KeyError('intent')`, whose `str()` is just `'intent'`, producing
`"malformed action proposal: 'intent'"` and terminating the run
immediately with `DiscoveryStatus.MALFORMED_MODEL_OUTPUT` -- one
uninspected turn, no retry, an intervention created
(`0f0b938ecdf04e1181a0564c3fa44e4e`) for something a corrected next turn
could very plausibly have resolved on its own. This real run (its log,
trace, and intervention record) was inspected read-only and is preserved
exactly as captured; the intervention was never approved/resumed/claimed
and remains `pending`.

**Root cause, found on inspection (read-only investigation, reported
before any code changed):** two independent, compounding causes. (1) The
schema contract sent to Anthropic (`AnthropicLLMClient._TOOL_SCHEMA`) never
actually required `intent`: `input_schema["required"]` was `["reasoning"]`
only, and `intent`'s own free-text description never said "required"
anywhere (unlike `target`'s and `value`'s descriptions, which do) -- the
model omitting it was consistent with the schema as written, not a
violation of it. (2) Separately, `_parse_proposal`'s validation treated
*any* malformed turn -- one bad field among many possible causes (invalid
JSON, a missing field, an invalid enum value, a bad locator strategy) -- as
instantly terminal, with no chance for the model to see its own mistake
and correct it, which sits in tension with this project's own documented
philosophy (`.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md`: "Do not escalate on
the first transient failure... Escalate when: bounded safe recovery is
exhausted..."; `.CLAUDE/03_DISCOVERY_AND_REPLAY.md`'s Replay Recovery
pattern: classify -> attempt only safe, bounded recovery -> retry ->
re-check -> escalate only if still unresolved). Neither doc ever mandated
immediate termination for discovery specifically -- it was a deliberate,
explicitly tested Phase 8 choice
(`test_malformed_model_output_stops_immediately_without_retry`), not an
oversight, just one this real run showed to be worth revisiting.

**The fix -- narrowly scoped, per instruction:**

*Contract:* `_TOOL_SCHEMA["input_schema"]` now declares `"anyOf":
[{"required": ["done"]}, {"required": ["intent"]}]` alongside the existing
`"required": ["reasoning"]` -- `intent` couldn't simply move into the flat
`required` array because a `done=true` response legitimately omits it;
this says "reasoning always required, and additionally either a done claim
or intent present," the smallest way to make intent required for exactly
the case `_parse_proposal` already treats as required. `intent`'s
description also now says so explicitly. Anthropic tool-use does not
hard-enforce this schema server-side the way strict/structured outputs
do, so `_parse_proposal`'s own runtime validation is kept unchanged as
defense in depth -- schema enforcement was added, not substituted.

*Bounded recovery:* the malformed branch in `DiscoveryEngine.run()`'s loop
no longer calls `_capture_terminal_evidence`/`_finish`/`break`. It still
does everything it did before (redact and append the `DiscoveryTraceStep`
with `parse_error`, `parsed_action=None`, `outcome="malformed_model_output"`,
emit `EventType.MALFORMED_MODEL_OUTPUT`), then increments a new loop-local
`malformed_count` and sets a new loop-local `pending_malformed_feedback`
to the already-redacted parse error, then `continue`s the same `for
step_index in range(start_step, limits.max_steps)` loop -- so a malformed
turn consumes one step of the existing budget exactly like a rejected
"done" claim already does, with no new retry cap (none was added, per
instruction). On the very next iteration, `observation_for_model` gets a
new corrective paragraph appended (`_build_malformed_feedback_text`,
mirroring the existing `_PROGRESS_REMINDER_TEXT` ephemeral-injection
pattern exactly: appended to the *copy* of the observation sent to the
model only, never to `observation` itself or anything derived into the
persisted trace), and `pending_malformed_feedback` is cleared back to
`None` immediately after -- it describes one specific past mistake, not a
standing rule, so it must not keep reappearing on later turns the way the
progress reminder deliberately does. The malformed turn is never turned
into a `DiscoveryHistoryEntry` (that type's `action: Action` field stays
required, unchanged, per instruction -- there is no valid `Action` to put
in one, and nothing was fabricated to make one fit) and never reaches
`self._policy.evaluate()` -- both exactly as before. A corrected next
proposal goes through the completely normal path: parsed, checked for
loop/repetition, evaluated by `LayeredPolicyEngine` with no special
treatment (verified by a new test: an unclassified intent proposed right
after a malformed turn still escalates to `APPROVAL_REQUIRED` and still
produces a normal `DiscoveryPendingApproval`, exactly as a completely
clean run would). If malformed output never recovers, the existing
`max_steps`/`max_duration_seconds`/`max_total_tokens` bounds terminate and
escalate the run exactly as they already do for any other stuck loop --
`MAX_STEPS_EXCEEDED`/`MAX_DURATION_EXCEEDED`/`MAX_TOKENS_EXCEEDED`, still
via `_capture_terminal_evidence` and `RunOrchestrator`'s existing generic
intervention-creation fallback, unchanged. Each of those three exhaustion
reasons now runs through a new `_with_malformed_count` helper that appends
`" (N of them malformed model output)"` when `malformed_count > 0`, purely
for escalation-evidence quality -- it never changes whether or when
exhaustion happens.

One deliberate, narrow side effect: `DiscoveryStatus.MALFORMED_MODEL_OUTPUT`
(the terminal status) is no longer ever produced by `DiscoveryEngine.run()`
-- persistent malformed output now always surfaces as one of the three
budget-exhaustion statuses instead. The enum value itself was left in
place (removing it was not asked for and is a larger, unrelated cleanup);
`EventType.MALFORMED_MODEL_OUTPUT` is unaffected and still emitted once
per malformed occurrence, terminal or not.

*Interaction with `3904afe`'s `DiscoveryPendingApproval`:* confirmed
unchanged and untouched -- `malformed_count`/`pending_malformed_feedback`
are plain loop-local variables, never added to `DiscoveryPendingApproval`
or `AutomationSession`, and always start fresh (including on a `resume`d
run) since a malformed proposal can never be the `pending_action` of an
approval escalation in the first place (`_parse_proposal` returns
`action=None` whenever `parse_error` is set, so the loop never reaches
`self._policy.evaluate()` -- the only place a `DiscoveryPendingApproval` is
built -- for a malformed turn). Every existing approval/resume test from
`3904afe` passes unchanged.

*Error-message quality:* a new `_require_field(source, key)` helper
replaces the four unchecked `dict["key"]` accesses that used to rely on
Python's bare `KeyError` formatting (`proposal["action_type"]`,
`proposal["intent"]` in `_parse_proposal`; `target_dict["role"]`,
`target_dict["selector"]` in `_parse_target`), raising
`ValueError(f"missing required field: {key}")` instead. `KeyError` stays
in both methods' `except` clauses regardless, as defense in depth for any
access not routed through the helper. This changes only the resulting
string (`"malformed action proposal: 'intent'"` ->
`"malformed action proposal: missing required field: intent"`), never
which cases are caught or when the branch fires.

**Deliberately not done (explicit scope, per instruction):** no separate
malformed-retry cap or counter-based bound was added -- the existing
`max_steps`/`max_duration_seconds`/`max_total_tokens` limits are relied on
as-is. No repair/sanitization of the model's invalid `:contains()`
selector (still genuinely invalid CSS/Playwright syntax; a corrected
retry still has to come from the model itself). No `read_savings_account_balance`-style
intent allowlist was added -- an unclassified intent proposed after
recovering from a malformed turn still fails closed to
`REQUIRE_APPROVAL`. No inference or fabrication of `intent` or any other
missing field anywhere. `DiscoveryHistoryEntry.action` was not made
optional. No change to `claim_intervention`/`mark_human_control_complete`/
`resume_run` or any `InterventionStatus`/`ControlState` state machine. No
Anthropic call was made anywhere in this work. No modification to
`e199e82bd3774cf6af2124d699ca5cfa`'s log/trace, or to intervention
`0f0b938ecdf04e1181a0564c3fa44e4e` (confirmed still `pending`,
byte-identical to before this work), or to any earlier-preserved evidence
set (`d17d9a2fd9a8483c9ca278976f1cf520`,
`a4dac702e5cd4244ac2c22680834178a`/`ea0efacbd2f2445fb3b01b7345fdd63d`).

**Tests added/updated:** `tests/fixtures/fake_llm_client.py` gained
`propose_missing_intent(...)`, a scriptable helper producing the exact
real shape (a structurally valid tool_use payload missing `intent`),
distinct from the pre-existing `malformed()` helper (which models the
*other* malformed case: no structured proposal at all). New
`tests/unit/test_anthropic_tool_schema.py` (schema-only, no `live_llm`
mark, no API key/network needed --
`test_intent_is_required_for_every_non_done_action_proposal`,
`test_intent_description_states_it_is_required`).
`tests/unit/test_discovery_engine.py`: replaced
`test_malformed_model_output_stops_immediately_without_retry` (asserted
exactly the old terminal-on-first-occurrence contract) with
`test_malformed_model_output_is_recorded_but_does_not_terminate_the_run`,
`test_missing_intent_produces_a_clean_deterministic_error_and_is_recorded_honestly`,
`test_malformed_proposal_never_executes_or_reaches_policy`,
`test_malformed_feedback_reaches_only_the_next_model_call_not_the_trace`,
`test_corrected_proposal_after_malformed_turn_still_requires_approval_when_unclassified`,
`test_persistent_malformed_output_is_bounded_by_max_steps_and_escalates`.
`tests/unit/test_run_orchestrator.py::test_discovery_hard_failure_creates_intervention`
updated (a single malformed proposal is no longer terminal, so it now
scripts two malformed turns against a `DiscoveryLimits(max_steps=2)`
orchestrator to still exhaust the bound and still assert a hard failure
creates an intervention). All pre-existing "done"-rejection tests
(`test_rejected_done_feeds_back_corrective_context_and_accepts_a_read`,
`test_done_after_a_materializable_read_succeeds`, etc.) and all
`3904afe` approval/resume tests were run unchanged and still pass.

**Verified:** `tests/unit/test_discovery_engine.py` +
`tests/unit/test_anthropic_tool_schema.py` (34 tests, all passing);
`tests/unit/test_run_orchestrator.py` + `tests/unit/test_intervention_lifecycle.py`
(29 tests, all passing) run directly against the repo. Full deterministic
suite `-m "not integration and not live_llm"`: 194 passed, 18 deselected --
+7 over the 187-test baseline before this change (net +5 in
test_discovery_engine.py: -1 superseded, +6 new; +2 in the new schema
test file), zero regressions. Full integration suite (17 tests, unchanged
from baseline -- this fix is unit-level parsing/loop behavior, not
surface/browser behavior, so no new integration test was added) run for
real against a live headless browser via the cloud-sandbox tar/stage/
extract/venv round-trip (checksummed byte-for-byte identical to the
device's committed source): all pass. No Anthropic call was made for any
of this verification. Diff reviewed end to end: exactly the 5 intended
source/test files changed (`discovery/anthropic_client.py`,
`discovery/engine.py`, `tests/fixtures/fake_llm_client.py`,
`tests/unit/test_discovery_engine.py`, `tests/unit/test_run_orchestrator.py`)
plus one new test file (`tests/unit/test_anthropic_tool_schema.py`);
confirmed `pending_malformed_feedback` is always assigned from the
already-redacted `trace_step.parse_error`, never the raw `parse_error`,
so no new raw-sensitive-value exposure path was introduced. The real run
(`e199e82bd3774cf6af2124d699ca5cfa`) and its intervention
(`0f0b938ecdf04e1181a0564c3fa44e4e`, still `pending`) were not touched.
### Correction to 01f4fff -- Anthropic rejects top-level `anyOf`/`oneOf`/`allOf` in a custom tool's `input_schema`

**Symptom (real Anthropic call attempt, run `aacd3ecc913d48d8ae9bd88100b93aba`,
capability `discover_savings_balance_demo`):** the request was rejected by
Anthropic's API before the model was ever invoked --
`tools.0.custom.input_schema: input_schema does not support oneOf, allOf,
or anyOf at the top level`. Confirmed by reading the run's log read-only:
it stops right after `step_started` at step 0 -- no
`llm_action_proposed`, no `malformed_model_output`, no `run_completed` --
because the exception from `self._client.messages.create(...)` (inside
`AnthropicLLMClient.propose_action`) was raised before `DiscoveryEngine`
ever got a response to parse. No trace file and no intervention exist for
this run_id, consistent with an unhandled exception escaping the loop
entirely rather than a normal discovery-engine terminal status. The
offending construct was `01f4fff`'s own fix: a top-level `"anyOf":
[{"required": ["done"]}, {"required": ["intent"]}]` added to
`_TOOL_SCHEMA["input_schema"]` to express "intent required unless done is
true." That schema was never actually sent to Anthropic in `01f4fff`'s
own verification (all tests use `FakeLLMClient`, and no Anthropic call was
made per that work's explicit constraint) -- this incompatibility only
surfaced on this real call.

**The correction:** the top-level `anyOf` is removed entirely.
`_TOOL_SCHEMA["input_schema"]["required"]` is back to the flat
`["reasoning"]` it was before `01f4fff` -- `intent` is NOT added to it
(doing so would make the legitimate `{"reasoning": "...", "done": true}`
response schema-invalid, which the instruction for this correction
explicitly ruled out). `intent`'s description (added in `01f4fff`,
unchanged here: "...Required for every action proposal, i.e. whenever
done is not true.") stays as the only place this rule is stated to the
model. A long comment now documents, in place of the old `anyOf`, exactly
why: Anthropic's accepted custom-tool schema subset has no way to express
"field X is required only when field Y is absent" -- there is no
conditional-requiredness construct available at all, not just a
differently-shaped one -- so this is a genuine, permanent limitation of
what can be declared here, not a bug to work around with cleverer JSON
Schema. `DiscoveryEngine._parse_proposal` was already, and remains, the
actual authoritative validator for the real rule (action_type/intent
required for a non-done proposal, target/value required per action_type,
done exempt from all of it) -- nothing about its validation logic changed
in this correction; `01f4fff`'s bounded bounded-malformed-output-recovery
loop, `_require_field`'s deterministic error messages, and
`DiscoveryPendingApproval`/`3904afe`'s non-interaction are all untouched
(confirmed: `git diff --stat -- src/cuas/discovery/engine.py` is empty
for this correction -- only `anthropic_client.py` and one test file
changed).

**Tests:** `tests/unit/test_anthropic_tool_schema.py` rewritten --
removed the two tests asserting the now-incompatible `anyOf` shape, added
`test_schema_stays_within_anthropics_accepted_subset` (a regression test:
`required` stays a flat list, and none of `oneOf`/`allOf`/`anyOf` appear
at the top level of `input_schema` -- fails immediately, before any real
API call could, if this construct is ever reintroduced),
`test_done_true_response_satisfies_the_schemas_own_required_list`
(demonstrates a `{"reasoning": ..., "done": true}` payload still
satisfies the schema's own `required` list), and
`test_action_missing_intent_is_not_rejected_by_the_schema_itself`
(demonstrates the limitation is real, not just asserted in a comment: an
action-shaped payload missing `intent` is NOT caught by the schema's own
`required` list either, and separately checks `intent`'s description
states the rule). The third requested demonstration -- the runtime parser
actually rejecting a missing-`intent` proposal and bounded recovery
handling it -- is already exercised by
`test_missing_intent_produces_a_clean_deterministic_error_and_is_recorded_honestly`
and `test_malformed_model_output_is_recorded_but_does_not_terminate_the_run`
in `tests/unit/test_discovery_engine.py` (added in `01f4fff`, unaffected
by this correction, re-run and still passing) -- not duplicated here, to
keep this a genuinely minimal correction.

**Verified:** `tests/unit/test_anthropic_tool_schema.py` +
`tests/unit/test_discovery_engine.py` (35 tests, all passing) run
directly against the repo. Full deterministic suite `-m "not integration
and not live_llm"`: 195 passed, 18 deselected (net +1 over `01f4fff`'s
194: -2 superseded schema tests, +3 new ones), zero regressions. Full
integration suite (17 tests, unchanged) run for real against a live
headless browser via the cloud-sandbox tar/stage/extract/venv round-trip,
checksummed byte-for-byte identical to the device's committed source:
all pass. No Anthropic call was made for any of this verification or the
correction itself. Diff reviewed end to end: exactly 2 files changed
(`discovery/anthropic_client.py`, `tests/unit/test_anthropic_tool_schema.py`)
-- `discovery/engine.py` has zero diff versus `01f4fff`. The real run
(`aacd3ecc913d48d8ae9bd88100b93aba`, no trace/intervention exists for it)
and every previously-preserved real run's log/trace/intervention
(`d17d9a2fd9a8483c9ca278976f1cf520`,
`a4dac702e5cd4244ac2c22680834178a`/`ea0efacbd2f2445fb3b01b7345fdd63d`,
`e199e82bd3774cf6af2124d699ca5cfa`/`0f0b938ecdf04e1181a0564c3fa44e4e`)
were not touched.

**Separately flagged, not implemented (per explicit instruction): an
unhandled Anthropic API error currently reaches FastAPI as a bare 500.**
`AnthropicLLMClient.propose_action`'s `self._client.messages.create(...)`
call has no try/except around it in `DiscoveryEngine.run()`'s loop, and
`POST /runs` (`src/cuas/api/main.py`) has no try/except around
`_orchestrator.run_capability(...)` either -- unlike every other endpoint
in that file, which maps `KeyError`/`HandoffError` to 404/409. The only
existing safety net is `RunOrchestrator._run_discovery`'s
`except Exception: await self._close_surface(...); raise` -- it closes
the browser surface so nothing leaks, then re-raises unchanged, so the
exception still reaches FastAPI's default handler as an unstructured 500
with no `RunResult` ever saved to `_run_store` and no intervention ever
created. This is a real, verifiable gap against this project's own
documented error model (`.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md`'s
"Hard / System Failures" category: these are supposed to surface as
structured results with a code, e.g. alongside "unrecoverable session
expiration" or "permission denied" -- an LLM provider rejecting a
request outright is the same category of thing, just not one the doc
happens to list by name) -- but it is a different bug class from
everything fixed in `01f4fff` and this correction (a provider/transport
failure, not malformed *content* the model returned), and fixing it
properly means classifying provider errors, deciding what `RunOutcome`/
HTTP status they map to, and likely creating an intervention/evidence
path analogous to the malformed-output one -- a distinct, non-trivial
feature, not a small extension of this fix. Recommendation given to the
user: worth doing for a production-grade version of this system, but
implementing it now, unrequested, would be scope creep for this
correction and arguably for the take-home's remaining scope more broadly;
left unimplemented pending an explicit decision, per instruction.

### Robustness fix (post-live-LLM-run investigation) -- a real execution failure's root cause and attempted target were discarded before the next stateless LLM turn ever saw them

A read-only inspection of live discovery run `7f8840f08ec34eecaa25e78c696ba134`
(`data/logs/`, `data/discovery_traces/`) found a genuine execution failure
handled architecture-consistently but uninformatively: an approved action
(`discovery-0e94b335`, `read_savings_account_balance`, a CSS selector
using jQuery's `:contains()` -- never valid CSS or a Playwright selector)
resumed and executed exactly as approved (confirming, again, all four
`3904afe` continuation requirements), failed for real, and was correctly
fed back into `history` for the next turn -- but the model proposed the
*identical* selector again one step later under a new intent, because
the feedback it received carried neither which locator had failed nor
any real detail about why.

Two separate, compounding information-loss points, both upstream of
`DiscoveryEngine`/`_execute_and_record` (`engine.py`, unmodified by this
fix -- `git diff --stat -- src/cuas/discovery/engine.py` is empty):

1. **`PlaywrightSurfaceAdapter._resolve()`** (`playwright_adapter.py`)
   already caught each candidate's real exception as `last_error` and
   chained it (`raise TargetNotFoundError(...) from last_error`), but
   `_execute_and_record`'s `except (AutomationError, ValueError) as exc:
   error_message = str(exc)` only ever sees `TargetNotFoundError`'s own
   message, never `__cause__` -- so the chained cause, though preserved
   for a debugger, was silently discarded before it ever reached
   discovery history at all.
2. **`AnthropicLLMClient._build_user_message`** rendered each history
   entry from `action_type`, `intent`, `outcome`, and `error_message`
   only -- never the actual target/selector the action attempted -- so
   even a maximally detailed `error_message` would have left the model
   unable to tell which of its own locators the message was even about.

**Fix, scoped to exactly this feedback boundary, nothing else:**

`playwright_adapter.py` adds `_sanitize_underlying_error(exc)`: a bounded
(`_MAX_UNDERLYING_ERROR_CHARS = 200`), single-line rendering of a failed
candidate's own exception message -- first line only (Playwright's
`Error.__str__()` is one useful line followed by a JS stack trace and/or
a multi-line, unbounded "Call log:" retry block; neither is root cause,
neither is safe to assume never echoes page-adjacent text), truncated,
with a deterministic `"no further detail available"` fallback for an
empty message. `_resolve()`'s final raise now folds this in alongside the
failed candidate's strategy name -- `TargetNotFoundError` stays the same
public/domain type, `from last_error` chaining is preserved unchanged for
a real traceback/debugger, and no traceback, secret, or arbitrary page
content is ever persisted -- only Playwright's own short diagnostic
sentence.

`anthropic_client.py` adds `_describe_target(target)`: a compact,
bounded (`_MAX_TARGET_DETAIL_CHARS = 120`) rendering of `Target.primary`
that dispatches on `LocatorStrategy` (mirroring
`PlaywrightSurfaceAdapter._playwright_locator`'s own dispatch) rather
than a blind `json.dumps`/`model_dump()` of the full `Target` (which
would include the unbounded fallback chain and `frame`). `_quote()`
renders values for prompt readability, not JSON/repr escaping.
`_build_user_message`'s history loop now appends `target=<description>`
only when `entry.outcome == "execution_failed"` and
`entry.action.target is not None` (a `NAVIGATE` action has no target and
must not crash this) -- deliberately reading only `entry.action.target`,
never `entry.action.value` (what a `FILL`/similar action actually typed,
e.g. a raw member id -- unrelated to *where* an action targeted, and the
one thing that must never ride along here just because target/error
feedback was added). This is consistent with, not a new gap in,
`DiscoveryHistoryEntry`'s existing design: its own docstring already
establishes that `AnthropicLLMClient` needs raw (unredacted) history to
reason correctly, with redaction applied only at the
`_redact_history`/persistence boundary in `engine.py` -- unchanged here.

Nothing here is `:contains()`-specific, Savings-specific, demo-app-
specific, or teaches the model any CSS/Playwright syntax -- both new
functions operate purely on `Exception`/`Target`/`LocatorStrategy`, the
same generic types every other action/locator in the system already
uses.

**Example -- the existing failed step, as the next stateless LLM turn
would now see it** (reconstructed from the real values in
`data/discovery_traces/7f8840f08ec34eecaa25e78c696ba134.json`; that
trace/log file itself was not modified by this fix -- read-only
throughout):

```
- step 1: proposed read (intent='read_savings_account_balance', target=css selector="tr:has(td:first-child:contains('Savings')) td:nth-child(3)") -> execution_failed (Could not resolve target for read: tried 1 candidate(s); last candidate (css) failed: Locator.wait_for: <Playwright's own real diagnostic -- see Verified below for which shape this environment actually produced for this exact selector>)
```

Previously this line stopped at `-> execution_failed (Could not resolve
target for read: tried 1 candidate(s))` -- no target, no underlying
cause at all.

**Explicitly out of scope for this fix (per instruction), all
unchanged:** `max_duration_seconds` resume behavior; `_action_signature`/
repeat detection; discovery budgets; an intent allowlist; policy
behavior; the currently-pending intervention
(`76737908f0564b31b3b123712ca74960`, re-verified `status: "pending"`,
`claimed_by: None`, `resolved_at: None` both before and after this fix)
was not manually repaired or replaced; prompts were not changed to teach
the model CSS/Playwright syntax; provider-error handling (the separately
flagged, still-unimplemented Anthropic-500 gap noted in the correction
above) was not touched.

**Tests:** `tests/unit/test_playwright_adapter_error_sanitization.py`
(new, 4 tests) -- `_sanitize_underlying_error` takes only the first line
of a multi-line message, truncates a long first line to
`_MAX_UNDERLYING_ERROR_CHARS` with a `...` suffix, falls back
deterministically on an empty message, and strips surrounding
whitespace. `tests/unit/test_anthropic_user_message.py` (new, 13 tests)
-- `_quote`'s double-quote-to-single-quote swap; `_describe_target` for
every `LocatorStrategy` variant (`css`, `role_name` with/without a
`name`, `label`, `xpath`, `coordinates` -- confirmed it never leaks raw
coordinate values) and its truncation; `_build_user_message` includes
`target=...` only for `execution_failed` entries and never for
`executed`/`policy_denied`/`approval_required`; a `NAVIGATE` action with
`target=None` produces no `target=` and does not crash; and -- the
sensitive-value proof the instruction specifically asked for -- a `FILL`
action's `Action.value` (a stand-in raw, secret-looking string) never
appears in the rendered message even when that same entry is
`execution_failed` and does get a `target=` rendering. One test locks in
the exact real history line for run `7f8840f08ec34eecaa25e78c696ba134`'s
step 1 byte-for-byte. `tests/integration/test_playwright_surface_adapter.py`
gains `test_target_not_found_message_includes_sanitized_underlying_cause`
(new) -- reproduces the real run's exact selector against a real browser
and real demo app and asserts the sanitized message is non-empty, always
begins with `Locator.wait_for:`, and never contains Playwright's own
multi-line `Call log:` block; deliberately does *not* assert a specific
underlying error *type* (see Verified below for why).

**Verified:** Full deterministic suite (`-m "not integration and not
live_llm"`): 212 passed, 19 deselected (net +17 over `68c952d`'s 195: the
17 new unit tests above), zero regressions -- run both directly against
the device repo and, byte-for-byte checksum-verified identical, via the
cloud-sandbox tar/stage/extract/venv round-trip. `tests/unit/
test_discovery_engine.py` (all 32 tests, covering both `01f4fff`'s
malformed-output recovery and `3904afe`'s approval/resume continuation)
re-run explicitly and confirmed passing unchanged, since neither behavior
was touched. Full integration suite (18 tests -- the pre-existing 17 plus
the 1 new one above) run for real against a live headless browser via
that same checksummed round-trip: all pass.

One real finding from running the new integration test for real, worth
recording rather than silently working around: the exact real Playwright
error this environment's browser build produces for the live run's exact
selector turned out to be `Locator.wait_for: Timeout 1500ms exceeded.` --
a plain timeout, not the `SyntaxError: ... is not a valid selector.` an
earlier isolated (non-integration, non-demo-app) sandbox check had
suggested. `:contains()` is a jQuery-only pseudo-class -- never valid CSS
and never a Playwright selector extension -- so whether a given
Playwright/browser build rejects it outright or simply never matches
anything and times out is a real, build-dependent difference, not a bug
in this fix; the original live run's own actual underlying exception type
was never recorded by the old code either (that's precisely the bug this
fix closes), so it cannot be recovered now. The integration test and this
log entry were both written to assert only what is actually guaranteed
regardless of that difference -- a real, non-empty, single-line fragment
of Playwright's own diagnostic survives, bounded, with the retry-log
noise excluded -- rather than assuming one specific exception shape.

No Anthropic call was made for any part of this investigation,
implementation, or verification. `data/interventions/
76737908f0564b31b3b123712ca74960.json`, `data/logs/
7f8840f08ec34eecaa25e78c696ba134.jsonl`, `data/discovery_traces/
7f8840f08ec34eecaa25e78c696ba134.json`, and every other previously-
preserved real run's evidence/trace/intervention/log were read-only
throughout and were not modified. Diff reviewed end to end: exactly 5
files touched -- 2 source (`src/cuas/surface/playwright_adapter.py`,
`src/cuas/discovery/anthropic_client.py`), 1 modified test
(`tests/integration/test_playwright_surface_adapter.py`), 2 new test
files (`tests/unit/test_playwright_adapter_error_sanitization.py`,
`tests/unit/test_anthropic_user_message.py`) -- no unintended changes,
no secrets, no page content, no full tracebacks persisted anywhere.

### Robustness fix (post-live-LLM-run investigation) -- a successful READ's result and completion-readiness were never fed back to the model, so it repeated the READ instead of declaring done

Read-only inspection of live run `e4f7901c680c4f2690e3b9f127e785fd`
found: step 3 executed a READ and recorded `read_value="$18204.55"`; the
very next Anthropic turn was told only `-> executed`, with no result and
no signal that this already satisfied `_has_materializable_progress` (the
model was still receiving `_PROGRESS_REMINDER_TEXT`, latched on by an
earlier rejected `done=true` and never turned off), and the system
prompt's own completion rule ("done once the page already shows the
result") never distinguished that from "a READ has actually captured it
for replay." The model reasonably proposed the identical READ again
instead of finishing.

Three narrow, symmetric fixes, all upstream of `engine.py`'s actual
gating logic (`_has_materializable_progress`/`_verify_success_checkpoint`
themselves untouched -- they were already correct):

1. `anthropic_client.py`'s `_build_user_message` now renders a
   successful READ's `read_value` into its history line (`_describe_read_value`:
   collapsed to one line, bounded to 200 chars, mirroring
   `_describe_target`/`_sanitize_underlying_error`'s existing pattern),
   gated on `outcome == "executed" and entry.read_value is not None` --
   which, per `DiscoveryHistoryEntry`'s own docstring, can only ever be a
   READ, so this can never attach to a FILL's typed value or any other
   action.
2. `engine.py`'s reminder gate changed from `if needs_progress_reminder:`
   to `if needs_progress_reminder and not
   self._has_materializable_progress(history):` -- deriving suppression
   from the same check `done` is already gated on, rather than adding a
   second state variable. The flag itself is still never reset (as
   before); its effect now naturally stops once real progress exists.
3. The system prompt's single done=true sentence was expanded (still
   fully generic -- no run, selector, or app specifics) to state the
   actual two-part rule: an executed READ is required before completion,
   and once one has produced the result, declare done rather than
   repeating it.

**Tests:** `tests/unit/test_anthropic_user_message.py` (+8) --
`_describe_read_value`'s single-line collapsing/truncation; a successful
READ's `read_value` appears in its history line (exact-string match); a
non-READ or valueless `executed` entry gets no `read_value=`; every
non-`executed` outcome never gets one even if `read_value` were
(incorrectly) set; `Action.value` still never leaks for a successful
entry either. `tests/unit/test_discovery_engine.py` (+1) --
`test_progress_reminder_stops_once_a_read_has_executed` reproduces the
real sequence end-to-end (rejected done -> READ executes -> reminder
gone -> done accepted -> SUCCESS), asserting the reminder text is present
before the READ and absent after, even though the flag was never reset.
All 32 pre-existing tests in that file (malformed-output recovery,
`3904afe` approval/resume, repeat detection) re-verified passing
unchanged.

**Verified:** Full deterministic suite: 221 passed (was 212), zero
regressions, checksum-verified identical between the device repo and the
cloud-sandbox round-trip. Full integration suite: 18 passed (unchanged
count -- no browser-facing code touched). No Anthropic call was made.
Interventions `76737908f0564b31b3b123712ca74960` and
`ff2fc8361e614b0c97a9955b6018245c`, and every trace/log/evidence file
from any real run, were read-only throughout and remain untouched
(`ff2fc836...` confirmed still `pending` after this fix). Docker was not
restarted for this commit.

**Explicitly out of scope, per instruction, all untouched:** the
`role_name(cell, "$18204.55")` value-as-locator generalization concern;
locator scoring/redesign; `_action_signature`/repeat detection; provider-
error handling; session persistence; duration accounting; policy;
`ArtifactBuilder`. Verifying this fix against a real Anthropic turn
still requires a fresh discovery run after a container restart (same
constraint as `cfefe84` -- no hot-reload path exists), which is deferred
to when Docker is actually restarted.

### Bugfix (post-live-run investigation) -- ArtifactBuilder's generated success condition pointed at the first executed READ instead of the last

Live run `1ce8002332b94aeaa4397eff7e0fb1e0` produced a genuinely
successful capability with three distinct, all-`executed` READs before
"done": two technically-successful but semantically wrong intermediate
reads (a table header read as `"Balance"`, then the Checking account's
`"$2340.10"` read under a different intent), corrected by a third
(`role_name(cell, "$18204.55")`) once the model noticed the mismatch.
None of the three are an exact adjacent repeat of the one before it, so
`_deduplicate_adjacent` correctly keeps all three, and all three
correctly become outputs -- that part of `ArtifactBuilder` is unchanged
and out of scope here. The bug: `build()` set
`success_condition = SuccessCondition(type=OUTPUT_VALID,
output=next(iter(outputs)))` -- the *first* output built, i.e. whichever
READ happened to execute first. For this run that was
`savings_account_balance` (the meaningless header-text one), even though
`_overall_intent`, a few lines later in the same method, already treats
the *last* action as "the one that produces the capability's actual
result." The two pieces of reasoning disagreed inside one `build()` call.

**Fix:** one line, `next(iter(outputs))` -> `next(reversed(outputs))`.
`outputs` is a plain dict built by iterating `kept_steps` in trace order,
so insertion order already is trace order -- this is the same
deterministic ordering `_overall_intent` already relies on, not a new
heuristic, no semantic scoring, no LLM involvement anywhere in
`ArtifactBuilder` (unchanged: it has none).

**Explicitly unchanged, per instruction:** which executed READs become
outputs (still all of them -- the two wrong intermediate reads for this
capability are still present in `artifact.outputs`, just no longer the
success-condition target); locator generation; the
`role_name(cell, "$18204.55")` value-bound locator; discovery engine;
`AnthropicLLMClient`; replay engine; policy; capability service; session
persistence.

**Tests:** `tests/unit/test_artifact_builder.py` (+2) --
`test_a_single_read_output_is_still_the_success_condition_output`
(one-output case is unaffected, first and last coincide) and
`test_multiple_executed_reads_use_the_last_one_as_the_success_condition_output`
(reproduces the real run's exact three-READ shape; asserts all three
still become outputs in trace order, the leading synthetic NAVIGATE is
still the only step, and the success condition now points at the last,
correct output rather than the first). All 15 pre-existing tests in that
file re-verified passing unchanged.

**Verified:** Full deterministic suite: 223 passed (was 221), zero
regressions, checksum-verified identical between the device repo and the
cloud-sandbox round-trip. Full integration suite: 18 passed, including
`test_artifact_builder_e2e.py` (a real engine run through the real
builder, replayed for real). No Anthropic call was made; Docker was not
restarted. The live artifact
(`data/artifacts/meridian-demo/credit-union-admin/discover_savings_balance_demo/1.0.0.json`),
its capability record, the discovery trace it was built from, and both
still-pending interventions
(`76737908f0564b31b3b123712ca74960`, `ff2fc8361e614b0c97a9955b6018245c`)
were read-only throughout and remain exactly as they were -- this fix
only changes what a *future* `ArtifactBuilder.build()` call produces.
Confirmed directly: the already-stored `1.0.0.json` still reads
`success_condition.output: "savings_account_balance"` after this commit,
since nothing rematerializes an existing artifact automatically.
