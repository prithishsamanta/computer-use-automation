# Claude Code Starting Prompt

I am starting the interface.ai Computer-Use Automation System take-home project.

Before writing any code, read all of the project context files in this repository in the following order:

1. `README.md`
2. `00_PROJECT_BRIEF.md`
3. `08_DECISIONS_AND_ASSUMPTIONS.md`
4. `01_ARCHITECTURE.md`
5. `02_ARTIFACT_SCHEMA.md`
6. `03_DISCOVERY_AND_REPLAY.md`
7. `04_SAFETY_AND_HUMAN_HANDOFF.md`
8. `05_SURFACES_AND_MULTI_TENANCY.md`
9. `06_ERRORS_AND_OBSERVABILITY.md`
10. `07_IMPLEMENTATION_GUIDANCE.md`

Treat these files as the current source of truth.

Important constraints:

- Do not silently redesign the architecture.
- Do not introduce Celery, RabbitMQ, Kafka, or other distributed infrastructure unless a concrete need makes it necessary.
- Normal execution should use direct synchronous orchestration, with async I/O where useful.
- Human handoff is asynchronous and persisted.
- The LLM is used for discovery, but deterministic replay must execute the saved artifact without using the LLM to choose the next action.
- The LLM is not the final safety authority; proposed actions must pass deterministic policy enforcement.
- Consequential or risky banking decisions must be made or approved by an authorized institution operator.
- The replay engine must depend on a surface abstraction rather than directly on Playwright.
- Preserve clear OOP boundaries and dependency inversion.
- Keep the raw discovery trace separate from the cleaned reusable artifact.

Development discipline:

- Build incrementally rather than implementing the whole system before testing.
- Add or update tests alongside each meaningful component.
- Run relevant tests after each meaningful change.
- Keep regression tests passing before moving to the next implementation phase.
- Add integration and end-to-end tests as component boundaries become real.
- Set up lightweight CI early, preferably GitHub Actions, to run automated tests on pushes / pull requests.
- Dockerize the project from the beginning.
- Docker / Docker Compose should be the canonical reproducible demo path.
- Keep the Docker setup simple and document exact setup/demo commands.
- Do not introduce Kubernetes or unnecessary deployment infrastructure.

Some implementation details are intentionally flexible and can be refined while coding, including locator representation, timeout/retry values, exact artifact field names, persistence details, and demo UI characteristics.

If implementation reveals that one of our architectural decisions creates a significant problem, explain the problem and proposed change before changing the architecture.

For now, do NOT start implementing the entire project immediately.

First:

1. Read all context files.
2. Summarize your understanding of the system.
3. Propose the concrete project structure and implementation phases.
4. Identify contradictions, missing decisions, or technical risks.
5. Clearly separate decisions already made, implementation details you recommend, and questions that genuinely require my decision.
6. Explain how you will keep the project continuously tested during development.
7. Explain your proposed Docker / Docker Compose setup.
8. Recommend what should be implemented first.

Do not write application code yet.

I want to review your understanding and implementation plan before authorizing implementation.
