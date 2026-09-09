# Architecture

## High-Level Components

```text
                    API / Entry Point
                          |
                          v
                    RunOrchestrator
                          |
          +---------------+----------------+
          |               |                |
          v               v                v
  CapabilityService  DiscoveryService   ReplayEngine
          |               |                |
          v               v                v
 ArtifactRepository    LLMClient      SurfaceAdapter
                                              |
                                              v
                                       PlaywrightSurface

                          |
                 +--------+--------+
                 |                 |
                 v                 v
            SafetyPolicy      EvidenceService

                          |
                          v
               InterventionService
                          |
                          v
              Persistent handoff state
```

## Core Principle

The orchestrator coordinates the workflow.

The major components remain separated behind interfaces so the implementation can evolve without coupling domain logic to a specific infrastructure choice.

## Main Flow

### Existing capability

```text
Incoming request
→ resolve tenant / application context
→ capability lookup
→ retrieve compatible artifact
→ validate artifact schema
→ validate runtime inputs
→ deterministic replay
→ extract outputs
→ verify final success condition
→ return structured result
```

### No existing capability

```text
Incoming request
→ capability lookup
→ no acceptable match
→ DISCOVERY_REQUIRED
→ start live LLM discovery
→ observe UI
→ propose action
→ policy validation
→ execute action
→ observe resulting UI
→ repeat until goal succeeds
→ clean successful path
→ build typed artifact
→ validate artifact
→ persist capability
```

## Synchronous vs Asynchronous

### Synchronous orchestration

Keep the main workflow logically synchronous:

- database reads
- capability lookup
- artifact validation
- LLM discovery loop
- Playwright actions
- checkpoints
- output extraction

Implementation may use `async` / `await` for I/O-bound calls, but the workflow remains one coordinated run.

### Asynchronous human handoff

Human intervention is intentionally asynchronous.

When automation cannot safely continue:

```text
RUNNING_AUTOMATION
→ PAUSED_WAITING_FOR_HUMAN
→ HUMAN_CONTROL
→ RESUME_REQUESTED
→ RUNNING_AUTOMATION
```

An `InterventionRequest` is persisted while the live browser session remains associated with the run.

## Why No Celery / Message Broker

Do not add Celery, RabbitMQ, Kafka, etc. in the initial take-home.

Reasons:

- the workflow is stateful
- Playwright actions belong to the same live session
- distributed step execution creates unnecessary complexity
- task correlation, duplicate delivery, browser ownership, ordering, retries, and serialization become new problems
- the assignment explicitly values a working core over premature infrastructure

However, component boundaries should allow a future deployment to move orchestration behind queues/workers.

## OOP / Design Principles

Use dependency inversion consistently:

- `ReplayEngine` depends on `SurfaceAdapter`, not Playwright directly
- `DiscoveryService` depends on `LLMClient`, not a vendor SDK directly
- domain services depend on repository interfaces, not storage implementation details
- `InterventionService` depends on an intervention repository abstraction
- policy enforcement is independent of the LLM

Use composition over large inheritance hierarchies.

Prefer small focused interfaces.
