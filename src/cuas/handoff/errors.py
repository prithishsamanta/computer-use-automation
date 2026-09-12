"""Exceptions for invalid handoff-lifecycle transitions (Phase 12).

Deliberately separate from `cuas.domain.errors.AutomationError` and its
subclasses -- those model "the automation itself hit a hard failure while
driving a surface" (target not found, checkpoint failed, ...). These model
a different kind of problem: a caller (an operator, the API layer)
asking `RunOrchestrator` to do something the current intervention/session
state does not allow, e.g. claiming an already-claimed intervention, or
resuming one that has not been through `mark_human_control_complete` yet.
Keeping them as a separate hierarchy lets the API layer map each to a
distinct, meaningful HTTP status (409 for a state conflict, 403 for an
ownership mismatch) without conflating them with automation failures.
"""

from __future__ import annotations


class HandoffError(Exception):
    """Base for every handoff-lifecycle error this package raises."""


class InterventionStateError(HandoffError):
    """Raised when claim/complete/resume is attempted against an
    InterventionRequest (or its underlying AutomationSession) whose
    current status/control_state does not allow that transition --
    e.g. double-claiming a PENDING-only operation, or resuming before
    `mark_human_control_complete` has run."""


class InterventionOwnershipError(HandoffError):
    """Raised when an operator who did not claim an intervention tries to
    complete it. This is the explicit, simple stand-in this phase uses
    for real authorization -- see the module docstring on
    `RunOrchestrator`'s claim/complete methods: 'Keep authorization
    assumptions explicit rather than pretending to implement production
    IAM.' There is no session/token/identity system here, just an
    operator-supplied id checked against the id that claimed the
    intervention."""


class SessionNotFoundError(HandoffError):
    """Raised when an intervention's `session_id` no longer names a live
    `AutomationSession` in the (necessarily in-process, in-memory)
    SessionRegistry -- either it was already resumed to a terminal
    outcome, or (a real limitation, not a bug -- see
    cuas.handoff.session's module docstring) the process restarted while
    the intervention was pending and the persisted InterventionRequest
    survived but the live browser it pointed at did not."""
