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
11. ⬜ Run orchestrator + API.
12. ⬜ Intervention / human-handoff persistence + async resume.
13. ⬜ Full Docker setup for handoff: Xvfb + noVNC inside the automation
    container.
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
