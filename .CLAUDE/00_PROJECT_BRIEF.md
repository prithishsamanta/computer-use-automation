# Interface.ai Take-Home — Project Brief

## Purpose

Build a backend integration layer that gives AI agents the ability to operate legacy banking / credit-union back-office applications when no clean API is available.

The core lifecycle is:

1. A natural-language goal is given.
2. An LLM performs a live discovery run by observing the UI, deciding what to do, and acting through a UI-control layer.
3. A successful run is converted into a typed, versioned, structured automation artifact.
4. Future executions use the artifact deterministically, without an LLM in the execution loop.
5. If automation cannot safely continue, it pauses and escalates to an authorized human operator.

The central principle is:

> The model discovers. The artifact becomes a reusable capability. Deterministic replay is how the capability is invoked in production.

## Target Environment

The target applications are internal operational systems used by banks and credit unions, not ordinary customer-facing web applications.

Examples include systems used to:

- service member/customer accounts
- process transactions
- administer institution data
- perform account operations
- navigate vendor-provided legacy applications

These systems may be:

- modern-ish web applications
- ugly legacy web applications
- poorly structured DOMs
- frames / tables / weak semantic markup
- potentially desktop applications in a future implementation

The take-home only needs one real surface implementation, but the architecture should not prevent future support for additional surfaces.

## Core Requirements

The implementation should contain a thin but real end-to-end vertical slice with:

- an LLM-driven discovery run against a real UI surface
- a structured reusable automation artifact
- deterministic replay without the LLM
- typed inputs and outputs
- ordered actions / steps
- stable target identification
- explicit success checkpoints
- error classification and safe recovery
- safety policy enforcement
- human intervention / handoff
- structured logs and richer failure evidence
- versioned, reviewable artifacts

## Important Evaluation Priorities

Prioritize:

- system design
- correctness of the core automation loop
- deterministic replay
- error handling
- human escalation
- safety
- generalization
- code quality
- communication of trade-offs

Do not optimize for feature breadth.

Do not add large-scale infrastructure merely to appear production-grade.

Design clean abstraction boundaries that could scale later, but keep the take-home focused.

## Our Major Design Decisions

### Execution model

Normal execution is synchronously orchestrated:

Request
→ capability lookup
→ artifact validation
→ discovery if necessary
→ replay / UI actions
→ checkpoints
→ output

Async I/O may be used internally, but we are not introducing Celery / RabbitMQ / Kafka in the main execution path.

Human handoff is asynchronous and persisted.

### Human authority

Automation may make routine execution decisions.

Automation must NOT independently make consequential banking business decisions.

Examples of decisions that require an authorized institution operator:

- closing an account
- approving a transaction
- making a policy exception
- proceeding with a risky / irreversible operation when approval is required

Unknown or unsafe situations must stop and escalate rather than guess.

### Human operator assumption

For this take-home, assume the human operator is an authorized employee/operator of the bank or credit union using the system.

Do not assume a generic interface.ai employee has unrestricted access to a customer's internal banking system.

This is a design assumption made because the brief is intentionally under-specified.

## Scope Discipline

Do not overbuild:

- no distributed task infrastructure unless a concrete need appears
- no full multi-tenant control plane
- no full desktop implementation
- no production-scale operator platform
- no elaborate capability marketplace

The implementation should instead make the abstractions clear enough that those could be added later.
