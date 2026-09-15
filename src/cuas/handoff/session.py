"""SessionRegistry: the piece Phase 11 explicitly deferred -- something
that owns a live automation session across the pause the intervention
seam creates, so an escalation genuinely satisfies
.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md's "Do not terminate the browser
session when intervention is created. The requirement is control transfer
of the SAME live session."

Design in one sentence: instead of `RunOrchestrator` entering and exiting
`async with self._surface_factory() as surface: ...` around one replay/
discovery attempt (Phase 11), it now manually acquires the surface
(`cm = self._surface_factory(); surface = await cm.__aenter__()`) and,
on an escalating result, hands the still-open `(surface, cm)` pair to this
registry instead of exiting the context manager -- so the browser process
that context manager owns keeps running, untouched, for as long as the
session sits here. `RunOrchestrator.resume_run` later retrieves that exact
`AutomationSession` and calls back into `ReplayEngine`/`DiscoveryEngine`
against `session.surface` -- never a new one -- and only calls
`cm.__aexit__` (closing the browser) once a run reaches a truly terminal
outcome (SUCCESS, BUSINESS_OUTCOME, BLOCKED, or an operator explicitly
cancels). See orchestrator.py's `_close_surface`/`_run_replay`/
`_run_discovery` for exactly where each transition happens.

**Why this is a deliberate, documented exception to this codebase's
"one ABC + one real implementation" rule** (every other seam --
ArtifactRepository, CapabilityRepository, InterventionRepository,
PolicyEngine, LLMClient, SurfaceAdapter -- follows it): an
`AutomationSession` holds a live, in-process Python object (an open
Playwright browser/page, reachable only through the exact `SurfaceAdapter`
instance that wraps it) that cannot be serialized, handed to another
process, or meaningfully reconstructed from a second "alternative
backend" -- there is no file-backed or database-backed equivalent of "a
running browser," so an ABC here would gesture at a swappability that
doesn't actually exist. `SessionRegistry` is therefore a single plain
class, not an interface. Its unavoidable consequence -- and a real
limitation, not a shortcut -- is that a live session cannot outlive this
process: if the API process restarts while an intervention is PENDING,
the persisted `InterventionRequest` (Phase 12's `FileInterventionRepository`)
survives and still shows up in an operator's queue, but the browser its
`session_id` named does not, and `resume_run` raises `SessionNotFoundError`
rather than silently fabricating a new session. Surviving a process
restart would need a genuinely different mechanism (e.g. a long-running,
out-of-process browser server the API reconnects to over CDP) -- squarely
Phase 13+ territory (".CLAUDE says Phase 13 wires the headed
browser + Xvfb + noVNC Docker path"), not something this phase's session
registry pretends to solve.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Literal

from cuas.artifact import Artifact
from cuas.discovery import DiscoveryGoal, DiscoveryPendingApproval
from cuas.domain import AppContext, ControlState
from cuas.handoff.errors import SessionNotFoundError
from cuas.surface.adapter import SurfaceAdapter

# What kind of attempt this session was paused mid-way through, so
# `RunOrchestrator.resume_run` knows whether to call back into
# `ReplayEngine` (an artifact already exists) or `DiscoveryEngine` (no
# artifact yet -- discovery itself is what's paused).
SessionOrigin = Literal["replay", "discovery"]


@dataclass
class AutomationSession:
    """Everything `RunOrchestrator` needs to resume one paused run on its
    original, still-open surface. Plain dataclass, not pydantic: `surface`
    and `surface_cm` are live objects (a `SurfaceAdapter` and the async
    context manager that owns its teardown), not data -- there is nothing
    here to validate, serialize, or persist. Persisting the *fact* that a
    session exists and why is `InterventionRequest`'s job (it names this
    session by `session_id`); this object is the live handle that record
    points at while this process is running.
    """

    session_id: str
    run_id: str
    capability_id: str
    context: AppContext
    inputs: dict[str, Any]
    origin: SessionOrigin
    surface: SurfaceAdapter
    surface_cm: AbstractAsyncContextManager[SurfaceAdapter]
    control_state: ControlState = ControlState.PAUSED_WAITING_FOR_HUMAN
    discovered_new_capability: bool = False

    # origin == "replay": which artifact/step to resume with.
    artifact: Artifact | None = None
    resume_step_id: str | None = None

    # origin == "discovery": no artifact exists yet -- resuming means
    # giving the LLM another attempt at the same goal, on the same
    # surface (see orchestrator.py's `resume_run` docstring for why this
    # is a deliberately simpler resume model than replay's) -- UNLESS
    # `pending_discovery_action` is set, in which case resuming means
    # executing that exact operator-approved action first (see
    # DiscoveryPendingApproval's docstring and DiscoveryEngine.run's
    # `resume` parameter). Deliberately only ever held here, in-process --
    # never serialized into InterventionRequest/evidence, exactly like
    # every other field on this dataclass (see module docstring).
    discovery_goal: DiscoveryGoal | None = None
    pending_discovery_action: DiscoveryPendingApproval | None = None


class SessionRegistry:
    """In-memory bookkeeping of every currently-paused `AutomationSession`,
    keyed by `session_id`. See this module's docstring for why this is
    intentionally the only implementation rather than an ABC."""

    def __init__(self) -> None:
        self._sessions: dict[str, AutomationSession] = {}

    def register(self, session: AutomationSession) -> None:
        self._sessions[session.session_id] = session

    def get(self, session_id: str) -> AutomationSession:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise SessionNotFoundError(
                f"no live automation session with id {session_id!r} (already resumed to completion, "
                "or the process restarted while it was pending -- see SessionRegistry's module docstring)"
            ) from exc

    def discard(self, session_id: str) -> AutomationSession:
        try:
            return self._sessions.pop(session_id)
        except KeyError as exc:
            raise SessionNotFoundError(f"no live automation session with id {session_id!r} to discard") from exc

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._sessions

    def __len__(self) -> int:
        return len(self._sessions)
