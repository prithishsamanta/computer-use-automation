# REPORT

interface.ai take-home — Computer-Use Automation System. This covers
architecture, the discovery/artifact/replay pipeline, safety, human
handoff, multi-tenancy, observability, testing, and known limitations.
Every claim below is grounded in code at the referenced path or in the
evidence at `evidence/`; nothing here describes unimplemented behavior as
if it existed.

## 1. Architecture

`RunOrchestrator` (`src/cuas/orchestration/orchestrator.py`) is the single
coordination point. For a request it resolves tenant/application context,
asks `CapabilityService` (`src/cuas/capability/service.py`) whether a
compatible artifact already exists, and then does exactly one of two
things: replay an existing artifact via `ReplayEngine`
(`src/cuas/replay/engine.py`), or, if none matches, run live discovery via
`DiscoveryEngine` (`src/cuas/discovery/engine.py`) against an
`AnthropicLLMClient` (`src/cuas/discovery/anthropic_client.py`) and, on
success, materialize a new artifact with `ArtifactBuilder`
(`src/cuas/artifact_builder/builder.py`) before replaying it once more to
verify it. Every dependency direction points inward toward interfaces, not
outward toward a vendor SDK: `ReplayEngine` and `DiscoveryEngine` both
depend on `SurfaceAdapter` (`src/cuas/surface/adapter.py`), never on
Playwright directly; `DiscoveryEngine` depends on `LLMClient`
(`src/cuas/discovery/llm_client.py`), never on the Anthropic SDK directly;
policy enforcement (`src/cuas/safety/policy.py`) is a separate module both
engines call through, not something either implements itself. The main
workflow is orchestrated synchronously per `.CLAUDE/01_ARCHITECTURE.md`'s
explicit decision against adding a task queue for this take-home (a live
Playwright session is single-owner and stateful; distributing its steps
across workers buys nothing here) — `async`/`await` is used for I/O, not
to distribute the run.

## 2. Discovery -> artifact -> deterministic replay

Discovery is a bounded observe/propose/execute loop: `DiscoveryEngine`
shows the model the current page (URL + accessibility-tree text excerpt +
prior action history), the model proposes exactly one structured action
via `AnthropicLLMClient`, that action passes through the same
`PolicyEngine` a replay would, and — if allowed — executes against a real
`PlaywrightSurfaceAdapter`. This repeats until the model reports
`done: true` or a bound (turn limit / repeated-action detection) is hit.
On success, `ArtifactBuilder` turns the *trace* into a typed, reusable
*artifact* — deliberately not the same thing: per
`.CLAUDE/08_DECISIONS_AND_ASSUMPTIONS.md` decision #6, "the raw discovery
trace is not the artifact... failed exploration remains only in the
trace/evidence." Concretely, the artifact keeps only the steps needed to
reach the target state (in the canonical live run,
`evidence/05_live_llm_discovery/`, that is a single `navigate` step) plus
the *successfully executed* reads as typed output extractors; the two
failed-locator attempts never appear in the artifact at all, only in the
discovery trace and log. `RunOrchestrator` then immediately replays the
new artifact once, within the same run, as a verification step before
reporting the run as done (visible as the `replay_engine` events appended
after `discovery_engine`'s own `run_completed` in
`evidence/05_live_llm_discovery/1ce8002332b94aeaa4397eff7e0fb1e0.jsonl`).
From then on, any request for that capability_id resolves straight to
`ReplayEngine` — no LLM, no per-step approval —
(`evidence/05_live_llm_discovery/060199268dd84a509d98f2c04a320511.jsonl`
is a completely separate later run proving exactly that: its only
components are `run_orchestrator` and `replay_engine`).

## 3. Artifact design and versioning

An `Artifact` (`src/cuas/artifact/schema.py`) is a typed, versioned
document: `capability_id`, semver `version`, the `vendor/application` +
`supported_versions` it targets, `tenant_scope`, typed `inputs`/`outputs`
(each output has a primary locator plus optional fallbacks), an ordered
list of `steps` (action_type, intent, target, risk, wait/checkpoint), a
`success_condition`, declared `business_outcomes`, and
`recoverable_conditions`. Multi-tenant reuse follows
`.CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md`'s base-artifact-plus-overrides
model rather than one artifact per institution: `src/cuas/artifact/overrides.py`
implements a small, reviewable `ArtifactOverride` (a named step's `target`
and/or `value`, nothing more) at `VERSION` or `TENANT` scope, resolved in
fixed order (tenant wins over version) at replay time — not a general
inheritance engine, deliberately, per that doc's own instruction not to
over-build this. `get_savings_balance` demonstrates real version
evolution in this repo: `1.0.0` and `1.0.1` both exist
(`data/artifacts/meridian-demo/credit-union-admin/get_savings_balance/`),
with the capability record's `supported_versions: ["1.x"]` wildcard
matching either. `CapabilityService.resolve()`
(`src/cuas/capability/service.py`) implements the deterministic half of
`.CLAUDE/05`'s retrieval flow — filter to compatible vendor/application/
version/tenant, then validate the candidate — honestly and explicitly as
only that half: the semantic-similarity/embedding layer over *unknown*
capability_ids that same doc describes is deliberately deferred (see
Limitations); this repo's retrieval always starts from a known
`capability_id`.

## 4. Error taxonomy and recovery

Outcomes are categorized per `.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md`,
not collapsed into a single failure/success bit: expected business
outcomes (e.g. `MEMBER_NOT_FOUND`, `evidence/02_business_outcome/`) are
reported as `business_outcome`, distinct from real system failures;
recoverable runtime conditions (a known popup, a slow page) are retried
within a bound; hard failures get one of six explicit `ErrorCode` values
(`src/cuas/domain/errors.py`: `ARTIFACT_INVALID`, `TARGET_NOT_FOUND`,
`CHECKPOINT_FAILED`, `SESSION_EXPIRED`, `PERMISSION_DENIED`,
`OUTPUT_EXTRACTION_FAILED`) and become an intervention rather than a bare
exception (`evidence/03_hard_failure_and_diagnostics/`,
`CHECKPOINT_FAILED` on an ambiguous "Smith" match); and "no capability
match" is routed to `DISCOVERY_REQUIRED`, explicitly not treated as a
failure. Within discovery specifically, an execution failure is fed back
to the model as a sanitized, bounded error string rather than silently
retried or silently dropped (`PlaywrightSurfaceAdapter._sanitize_underlying_error`,
`AnthropicLLMClient._build_user_message`) — this is exactly what let the
model in `evidence/05_live_llm_discovery/` recover from two consecutive
invalid-CSS attempts by seeing the real Playwright `SyntaxError` each
time, and what let it recognize, from its own history, that an already-
executed read had come back with a semantically wrong value (a header
string, then a wrong account's balance) and correct itself before
declaring done.

## 5. Safety and policy

The LLM is told the safety policy but is never the enforcement boundary
(`.CLAUDE/08` decision #4): every proposed action, in discovery *and* in
replay, passes through the same deterministic `PolicyEngine`
(`src/cuas/safety/policy.py`), which maps each `RiskLevel` —
`SAFE` / `APPROVAL_REQUIRED` / `BLOCKED` — to a `PolicyDecision` —
`ALLOW` / `REQUIRE_APPROVAL` / `DENY`. `LayeredPolicyEngine` implements
`.CLAUDE/04`'s conceptual global-defaults -> vendor/application ->
institution-override stack, so an institution can tighten (never loosen)
what a shared base artifact is allowed to do. In
`evidence/05_live_llm_discovery/`, every one of the five actions the
model proposed was policy-classified `require_approval` and paused for a
real operator (`intervention_requested` -> claim -> approve -> resume,
five full cycles) before execution — the model never executed a read
unattended.

## 6. Human handoff

Handoff is asynchronous and preserves the live session rather than
restarting it (`.CLAUDE/04`, `src/cuas/handoff/`): when automation cannot
safely continue, `RunOrchestrator` persists an `InterventionRequest`
(`SessionRegistry`/`AutomationSession` keep the live Playwright session
associated with the paused run in-process) and the run's HTTP response
comes back with a non-null `intervention_id`/`session_id` rather than
blocking. An operator claims it (`POST /interventions/{id}/claim`),
resolves it — either by approving a specific gated step so automation
performs it (`APPROVAL_REQUIRED`, `escalation_step_id` set, resume
re-enters the replay loop at that exact step) or, for an unmodeled hard
failure, by driving the *same* live browser themselves via noVNC
(`FAILED`, `escalation_step_id` is `None`, resume uses
`RESUME_AFTER_ALL_STEPS`) — then marks control complete and resumes
(`POST /interventions/{id}/complete` then `/resume`). Both real, distinct
flows are captured end to end in `evidence/04_human_handoff_and_resume/`,
and `evidence/05_live_llm_discovery/` shows the approval flow exercised
five times in a single discovery run.

## 7. Heterogeneous surfaces and multi-tenancy

`ReplayEngine` and `DiscoveryEngine` depend only on the `SurfaceAdapter`
interface (`observe`/`click`/`fill`/`read`/`wait_for`/`capture_evidence`,
`src/cuas/surface/adapter.py`); `PlaywrightSurfaceAdapter`
(`src/cuas/surface/playwright_adapter.py`) is the one real, tested
implementation, targeting the legacy-web `demo_app/`. Per
`.CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md` and `.CLAUDE/08` decision #5,
this take-home deliberately keeps that seam real and leaves
non-web surfaces (a native desktop adapter) design-only rather than
building a second untested implementation just to prove the interface
exists — the interface itself is what generalizes, not a second stub
behind it. Multi-tenant reuse is the base-artifact-plus-overrides model
from section 3, applied at replay time, not a separate artifact per
institution.

## 8. Observability and evidence

Every run gets a `run_id`; every component logs structured JSONL events
(`timestamp`, `run_id`, `component`, `event`, `step_id`, `status`,
`details`) to `data/logs/<run_id>.jsonl` via
`src/cuas/observability/event_sink.py`/`events.py`, sufficient to answer
every question `.CLAUDE/06`'s "Traceability" section lists (what request
started the run, what artifact/version was selected, what action was
attempted, what policy decision fired, what checkpoint failed, why a
human was requested, what the final result was) directly by reading the
log — no reconstruction needed for the log files themselves. Sensitive
values are handled deliberately, not accidentally omitted:
`src/cuas/observability/redaction.py` redacts sensitive inputs
(`[REDACTED]` in place of a raw member ID) before a `run_started` event is
logged, and failure evidence captures (screenshot + accessibility-tree
DOM snapshot + metadata, `src/cuas/observability/evidence.py`) are
explicitly flagged `screenshot_is_unredacted_pii_risk: true` in their own
metadata so a real deployment knows to treat them as sensitive — they are
committed in this repo's `evidence/` folder only because the underlying
data is `demo_app/data.py`'s fictional seeded members, a fact `evidence/README.md`
states plainly. `evidence/` (see its own `README.md` for the full index)
curates five real scenarios: deterministic success, an expected business
outcome, a hard failure with captured diagnostics, two distinct human-
handoff/resume flows, and — the one genuinely LLM-driven scenario in this
repo — a full discovery run with two locator-syntax recoveries, two
self-corrected semantic misreads, artifact creation, and two independent
deterministic replays of the resulting artifact.

## 9. Testing

223 deterministic tests (`pytest -m "not integration and not live_llm"`)
cover domain logic, policy, artifact schema/overrides, capability
resolution, the discovery engine's bounded-recovery/progress logic, the
Anthropic client's message-building, and `ArtifactBuilder`, all against
fakes/mocks — no network, no browser. 18 integration tests
(`pytest -m integration`) exercise `PlaywrightSurfaceAdapter`,
`ReplayEngine`, and `RunOrchestrator` against a real headless Chromium
browser and the real `demo_app`, for all three primary outcomes
(success, business outcome, hard failure/intervention) plus artifact
materialization end to end (`tests/integration/test_artifact_builder_e2e.py`).
A separate `live_llm`-marked test exists but is excluded from both of the
above and is not run as part of this submission's verification, since it
would require a real Anthropic API call. Both suites above were re-run in
full for this submission (241 tests total, all passing) from a clean
checkout of the exact commit being submitted, not just trusted from
earlier development.

## 10. Trade-offs and limitations

**The canonical live-discovery artifact (`data/artifacts/.../discover_savings_balance_demo/1.0.0.json`,
`evidence/05_live_llm_discovery/`) has two honest imperfections, preserved
exactly as produced rather than cleaned up for this submission.** First,
all three of the model's *executed* reads were kept as declared artifact
outputs — including the column-header string `"Balance"` and the
wrong-account value `"$2340.10"` — not only the final, correct
`"$18204.55"` read; `ArtifactBuilder`'s current design (deliberately,
per `.CLAUDE/08` decision #10's scope) cleans the *steps* down to what is
needed to reach the target state, but does not yet semantically prune
which *executed reads* should count as real outputs versus exploratory
detours the model itself already recognized as wrong. Second, this
specific artifact's `success_condition` points at the *first*-produced
output (`savings_account_balance`, the header text) rather than the
*last* (`savings_balance_from_savings_row`, the correct value) — because
`"Balance"` is still a non-empty string, `OUTPUT_VALID` passes on it, so
this artifact's own deterministic replay technically validates success
against the wrong output. Commit `1357e6f` (already in this repository)
fixes `ArtifactBuilder` so that *future* discovery runs anchor
`success_condition` to the last-produced output instead of the first —
but that fix was deliberately not applied retroactively to this
already-materialized artifact, since doing so would mean regenerating or
hand-editing genuine evidence rather than preserving it as actually
produced. Semantic pruning of exploratory-but-technically-successful
outputs, and synthesizing a more generalized locator than the exact
literal string `"$18204.55"` the model happened to target, are both real
future work, not something this submission claims to have solved.

**Capability retrieval implements only the deterministic metadata-filter
half of `.CLAUDE/05`'s design** (`CapabilityService.resolve()`, section
3) — filtering by vendor/application/version/tenant compatibility. The
semantic-similarity/embedding layer for matching a request to a
capability whose id isn't already known is deliberately out of scope for
this take-home (`.CLAUDE/08`'s non-goals list this explicitly); every
request in this repo already supplies the `capability_id` it wants.

**Other explicit non-goals**, matching `.CLAUDE/08_DECISIONS_AND_ASSUMPTIONS.md`:
no distributed task queue (a single live browser session is inherently
stateful and single-owner; see section 1), no real desktop-automation
surface (the `SurfaceAdapter` interface is designed to support one; none
is implemented or tested), no full policy-management UI or multi-layer
policy persistence beyond `LayeredPolicyEngine`'s in-code layering, and
no production auth/tenant-administration system. The human-operator model
assumes an already-authorized bank/credit-union employee, per `.CLAUDE/04`
— this repo does not implement operator authentication itself.
