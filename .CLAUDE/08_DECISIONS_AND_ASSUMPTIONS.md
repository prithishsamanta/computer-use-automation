# Finalized Decisions and Explicit Assumptions

This file separates intentional design choices from assignment requirements.

## Finalized Decisions

### 1. Main execution model

Use direct synchronous orchestration for normal execution.

Async I/O may be used internally.

Do not use Celery / RabbitMQ / Kafka for the initial implementation.

### 2. Human handoff

Human handoff is asynchronous and persisted.

The same live UI session remains associated with the paused run.

### 3. Human authority

Automation performs routine actions.

Authorized institution staff make consequential / risky business decisions.

Automation must stop and escalate on unknown or unsafe situations.

### 4. Safety enforcement

The LLM is informed about policy but is not trusted as the enforcement boundary.

A deterministic policy engine validates every proposed discovery action and every replay action.

### 5. Surface abstraction

Replay depends on a `SurfaceAdapter`.

Use Strategy-style execution with Adapter implementations.

Playwright is the real initial implementation.

Desktop support is future/design-only.

### 6. Discovery vs artifact

The raw discovery trace is not the artifact.

The artifact is a cleaned, validated successful workflow.

Failed exploration remains only in the trace/evidence.

### 7. Deterministic replay

Replay follows the artifact without LLM planning.

It uses:

- deterministic target resolution
- waits/timeouts
- checkpoints
- bounded recovery
- typed output extraction

### 8. Capability retrieval

First scope by compatible application/vendor/version/tenant metadata.

Then use semantic similarity to identify likely capability meaning.

Similarity score alone is insufficient; validate metadata and schemas.

No acceptable match → `DISCOVERY_REQUIRED`.

### 9. Multi-tenant reuse

Prefer:

```text
base vendor/application artifact
+ version override
+ tenant override
```

over duplicating the entire capability per institution.

### 10. Artifact safety

Artifacts contain typed placeholders.

Do not persist real runtime secrets / PII in reusable artifacts.

## Explicit Assumptions

### Human operator

Assume the operator taking over the back-office UI is an authorized employee/operator of the bank or credit union.

This is our architecture assumption, not a claim about interface.ai's actual staffing model.

### Policy ownership

Assume policy can conceptually be layered:

```text
global defaults
→ application/vendor policy
→ institution overrides
```

The take-home only needs a minimal implementation of this idea.

### Application context

Assume a run knows enough tenant/application context to filter capability candidates before semantic similarity.

How that context is supplied may be simplified in the demo.

### Desktop support

Do not implement desktop automation unless unexpectedly easy and clearly valuable.

The abstraction seam is enough for the assignment.

## Non-Goals

Do not spend time on:

- distributed worker clusters
- production-scale broker topology
- full tenant administration
- enterprise auth architecture
- full policy-management UI
- general-purpose desktop automation
- broad capability catalogs
- sophisticated embedding infrastructure
- full visual computer-use fallback

These can be discussed as future extensions.
