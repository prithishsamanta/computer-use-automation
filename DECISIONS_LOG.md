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
  `docker compose build`/`up` themselves.
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
- **Non-Docker verification, explicitly bounded:** this sandbox has no
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
  claim Docker verification that didn't happen.
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
