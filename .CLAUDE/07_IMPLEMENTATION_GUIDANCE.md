# Implementation Guidance for Claude Code

## Goal

Implement a focused, defensible vertical slice.

Prefer correctness and clarity over breadth.

## Recommended Stack

Current preferred implementation direction:

- Python
- FastAPI for a small API/control layer
- Playwright for web automation
- Pydantic for typed schemas
- an LLM provider behind an `LLMClient` interface
- JSON or a small relational store for artifacts / run state
- JSONL structured logs
- pytest for tests

The exact persistence mechanism can remain simple.

Do not introduce complex infrastructure without a concrete need.

## Demo Application

Build or use a small intentionally legacy-style banking / credit-union admin UI.

Recommended demo workflow:

```text
Search member
→ open member details
→ open accounts
→ find savings account
→ return savings balance
```

The app should include enough friction to exercise automation:

- table-based layout
- imperfect semantics
- maybe one frame/iframe or unusual structure
- explicit business-outcome state such as "Member not found"
- one recoverable runtime condition or popup if practical
- one risky action in the UI that demonstrates safety blocking / operator approval

Do not make the demo so hostile that it consumes the entire project.

## Minimum Working Vertical Slice

Must demonstrate:

1. natural-language request
2. capability lookup
3. no match → discovery
4. real LLM chooses UI actions against live surface
5. action passes through policy engine
6. Playwright executes action
7. discovery completes successfully
8. successful path becomes artifact
9. artifact is persisted
10. same capability is invoked again
11. deterministic replay runs without LLM decisions
12. output is extracted and validated
13. evidence is saved

Also demonstrate at least one:

- business outcome
- replay failure
- human intervention path

Prefer demonstrating all three if still focused.

## Component Boundaries

Suggested package shape:

```text
src/
  api/
  orchestration/
  discovery/
  replay/
  artifact/
  capability/
  safety/
  handoff/
  observability/
  surface/
  storage/
```

Possible interfaces:

```text
LLMClient
SurfaceAdapter
ArtifactRepository
CapabilityRepository
PolicyEngine
InterventionRepository
EvidenceStore
```

Avoid a giant god service.

## Important Implementation Constraints

### Discovery

- LLM proposes structured actions
- never execute arbitrary generated code
- every action must be represented in a known action schema
- every action must pass policy validation
- retain complete discovery trace
- build cleaned artifact separately

### Replay

- must not call LLM to choose the next ordinary step
- validate artifact before executing
- use bounded locator fallbacks
- use conditional waits, not arbitrary sleeps
- checkpoint meaningful transitions
- validate output types
- classify failures explicitly

### Safety

- policy is deterministic
- risky action requires operator approval
- unknown action does not execute
- secret / PII persistence is blocked or redacted

### Handoff

- asynchronous persisted request
- same live session remains paused
- operator claims session
- operator can return control
- actions / evidence are recorded

## Things That May Change During Coding

These are intentionally not frozen yet:

- exact artifact JSON field names
- exact similarity threshold
- exact locator fallback representation
- timeout values
- retry counts
- checkpoint granularity
- storage choice
- exact demo legacy quirks
- exact operator page implementation

Do not change the architectural principles merely for convenience without documenting the reason.

## Required Deliverables

Plan for:

```text
README.md
REPORT.md
evidence/
```

README should contain exact setup and demo commands.

REPORT should explain:

- Architecture
- Artifact schema
- Determinism & error handling
- Heterogeneity & multi-tenant design
- Escalation & handoff
- Safety
- Cuts / trade-offs

Evidence should show actual discovery and replay behavior.

## Implementation Order

Recommended order:

1. project skeleton and typed domain models
2. demo target application
3. Playwright surface adapter
4. artifact schema
5. deterministic replay engine
6. policy engine
7. structured logs/evidence
8. LLM discovery loop
9. artifact construction from successful discovery
10. capability storage/retrieval
11. human intervention persistence + minimal operator control
12. failure/business-outcome scenarios
13. tests
14. README / REPORT / evidence cleanup

The replay core should exist before allowing the discovery loop to generate artifacts for it.
