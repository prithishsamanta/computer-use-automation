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
7. ⬜ Structured logging / evidence capture (observability package).
8. ⬜ LLM discovery loop, built and tested against fakes first.
9. ⬜ Artifact builder (discovery run → cleaned, typed artifact).
10. ⬜ Capability service (context/version/tenant resolution at call time).
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
