"""In-memory store of completed RunResults, keyed by run_id, so a client
can retrieve a run's result/status after the fact (`GET /runs/{run_id}`)
without a database yet -- the same "simple, file/memory-backed for this
take-home" persistence choice every other repository in this codebase
makes.

Deliberately NOT part of `RunOrchestrator`: the orchestrator always
returns a `RunResult` directly from `run_capability`, synchronously, and
knows nothing about retrieval-after-the-fact. This store is purely the
thin API layer's own bookkeeping, so a caller that only wants the
synchronous request/response cycle (e.g. the test suite exercising
`RunOrchestrator` directly) never has to know it exists.
"""

from __future__ import annotations

from cuas.orchestration import RunResult


class RunResultStore:
    def __init__(self) -> None:
        self._results: dict[str, RunResult] = {}

    def save(self, result: RunResult) -> None:
        self._results[result.run_id] = result

    def get(self, run_id: str) -> RunResult | None:
        return self._results.get(run_id)
