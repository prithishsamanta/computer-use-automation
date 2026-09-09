"""SurfaceAdapter: the abstraction ReplayEngine and DiscoveryService depend
on instead of Playwright directly (.CLAUDE/01_ARCHITECTURE.md,
.CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md).

Only PlaywrightSurfaceAdapter is real for this take-home; the interface is
shaped so a LegacyWebSurfaceAdapter or DesktopSurfaceAdapter could implement
it later without changing ReplayEngine/DiscoveryService at all (Strategy /
Dependency Inversion, per .CLAUDE/05).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, ConfigDict

from cuas.domain import Target


class Observation(BaseModel):
    """What discovery reasons about between actions, and what checkpoints /
    business-outcome detection inspect during replay."""

    url: str
    visible_text: str


class WaitConditionKind(str, Enum):
    VISIBLE = "visible"
    TEXT_PRESENT = "text_present"


class WaitCondition(BaseModel):
    """A condition-based wait (.CLAUDE/03_DISCOVERY_AND_REPLAY.md: "avoid
    fixed sleeps ... prefer condition-based waits"). VISIBLE needs `target`;
    TEXT_PRESENT needs `text`.
    """

    kind: WaitConditionKind
    target: Target | None = None
    text: str | None = None
    timeout_ms: int = 5000


class Evidence(BaseModel):
    """Raw evidence captured directly from the surface. Persisting this to
    disk under a run/step id is EvidenceStore's job (observability package,
    Phase 7), not the adapter's."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    url: str
    screenshot_png: bytes
    visible_text_excerpt: str


class SurfaceAdapter(ABC):
    """Conceptual interface from .CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md:
    observe / click / fill / read / wait_for / capture_evidence, plus
    navigate (needed to start any session; classified SAFE by policy when
    within an allowed application, per .CLAUDE/04)."""

    @abstractmethod
    async def navigate(self, url: str) -> None: ...

    @abstractmethod
    async def observe(self) -> Observation: ...

    @abstractmethod
    async def click(self, target: Target) -> None: ...

    @abstractmethod
    async def fill(self, target: Target, value: str) -> None: ...

    @abstractmethod
    async def read(self, target: Target) -> str: ...

    @abstractmethod
    async def wait_for(self, condition: WaitCondition) -> None: ...

    @abstractmethod
    async def capture_evidence(self) -> Evidence: ...
