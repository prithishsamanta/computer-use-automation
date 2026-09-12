"""InterventionRepository: the seam RunOrchestrator creates escalations
through (.CLAUDE/07_IMPLEMENTATION_GUIDANCE.md's suggested
`InterventionRepository` interface). Same one-ABC-one-real-implementation
shape as every other repository in this codebase, but the "real"
implementation here is deliberately the simplest thing that can work,
per the user's explicit Phase 11 instruction: "Keep intervention creation
behind an interface for now if the full persistence implementation is
Phase 12."

`InMemoryInterventionRepository` is that placeholder: it satisfies the
interface and genuinely round-trips requests within one process lifetime
(so RunOrchestrator's escalation behavior is fully testable now), but it
does not survive a restart and has no operator-facing claim/resolve
workflow yet -- both are exactly what Phase 12's "full persistence
implementation" needs to add. Swapping in a durable implementation later
(a file- or database-backed one, following the same
save/get/list_pending/list_all shape FileArtifactRepository and
FileCapabilityRepository already established) changes nothing about
RunOrchestrator, which only ever depends on this ABC.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from cuas.domain import InterventionStatus
from cuas.handoff.models import InterventionRequest


class InterventionRepository(ABC):
    @abstractmethod
    def save(self, request: InterventionRequest) -> None: ...

    @abstractmethod
    def get(self, intervention_id: str) -> InterventionRequest: ...

    @abstractmethod
    def list_pending(self) -> list[InterventionRequest]: ...

    @abstractmethod
    def list_all(self) -> list[InterventionRequest]: ...


class InMemoryInterventionRepository(InterventionRepository):
    def __init__(self) -> None:
        self._requests: dict[str, InterventionRequest] = {}

    def save(self, request: InterventionRequest) -> None:
        self._requests[request.id] = request

    def get(self, intervention_id: str) -> InterventionRequest:
        try:
            return self._requests[intervention_id]
        except KeyError as exc:
            raise KeyError(f"no intervention request with id {intervention_id!r}") from exc

    def list_pending(self) -> list[InterventionRequest]:
        return [r for r in self._requests.values() if r.status == InterventionStatus.PENDING]

    def list_all(self) -> list[InterventionRequest]:
        return list(self._requests.values())
