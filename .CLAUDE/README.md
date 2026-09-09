# Claude Code Context Pack

These files capture the design decisions for the interface.ai computer-use automation take-home.

Read them in this order:

1. `00_PROJECT_BRIEF.md`
2. `08_DECISIONS_AND_ASSUMPTIONS.md`
3. `01_ARCHITECTURE.md`
4. `02_ARTIFACT_SCHEMA.md`
5. `03_DISCOVERY_AND_REPLAY.md`
6. `04_SAFETY_AND_HUMAN_HANDOFF.md`
7. `05_SURFACES_AND_MULTI_TENANCY.md`
8. `06_ERRORS_AND_OBSERVABILITY.md`
9. `07_IMPLEMENTATION_GUIDANCE.md`

## Important Instruction to the Coding Agent

Treat the architecture decisions in these files as the current source of truth.

If implementation pressure suggests changing a major decision, do not silently change it.

Instead:

1. state the conflict
2. explain the trade-off
3. propose the smallest change
4. preserve the core assignment requirements

Exact implementation details may evolve, especially:

- artifact field names
- locator representation
- retry counts
- timeouts
- similarity threshold
- demo UI quirks
- persistence implementation

But the safety model, deterministic replay principle, asynchronous human handoff, and surface abstraction should remain intact unless explicitly revised.
