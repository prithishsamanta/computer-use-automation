"""InterventionRequest: the persisted record of one human-handoff escalation
(.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md, "InterventionRequest" / "Handoff
Model"). Field-for-field, this is that doc's own suggested shape:

    id, run_id, capability_id, tenant_id, current_step, reason, evidence,
    session_id, status, claimed_by, created_at, resolved_at

`status` reuses the existing `InterventionStatus` enum from
`cuas.domain.models` (PENDING/CLAIMED/RESOLVED/CANCELLED) -- that
vocabulary was already scaffolded there in an earlier phase precisely for
this type to use, rather than inventing a second one here.

One field is deliberately left thin, not by oversight: `evidence` is a
small, optional freeform dict (e.g. a step id, an error code, a short
human-readable summary) -- not the actual screenshot/DOM snapshot bytes.
Those already live in EvidenceStore, addressable by this same
`run_id`/`current_step`; an operator queue looks them up there rather
than this record duplicating them.

`session_id` is no longer always `None` as of Phase 12: `RunOrchestrator`
now keeps the live surface open across an escalation (see
`cuas.handoff.session.SessionRegistry`) and sets this field to that
session's id whenever one was created, satisfying .CLAUDE/04's "the
operator takes control of the SAME live session" requirement that Phase
11 explicitly could not yet meet. It can still legitimately be `None` in
one case: `RunOrchestrator._materialize_capability` creates an
intervention when a *successful* discovery run's trace can't be loaded or
turned into an artifact -- at that point the discovery surface has
already been closed (there is nothing further automation could do with
it; the failure is in artifact construction, not the live page), so there
is no session left to hand off.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from cuas.domain import InterventionStatus


class InterventionRequest(BaseModel):
    id: str
    run_id: str
    capability_id: str
    tenant_id: str
    current_step: str | None = None
    reason: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    status: InterventionStatus = InterventionStatus.PENDING
    claimed_by: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
