"""InterventionRequest: the persisted record of one human-handoff escalation
(.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md, "InterventionRequest" / "Handoff
Model"). Field-for-field, this is that doc's own suggested shape:

    id, run_id, capability_id, tenant_id, current_step, reason, evidence,
    session_id, status, claimed_by, created_at, resolved_at

`status` reuses the existing `InterventionStatus` enum from
`cuas.domain.models` (PENDING/CLAIMED/RESOLVED/CANCELLED) -- that
vocabulary was already scaffolded there in an earlier phase precisely for
this type to use, rather than inventing a second one here.

Two fields are deliberately left thin in this phase, not by oversight:

- `evidence` is a small, optional freeform dict (e.g. a step id, an error
  code, a short human-readable summary) -- not the actual screenshot/DOM
  snapshot bytes. Those already live in EvidenceStore, addressable by
  this same `run_id`/`current_step`; an operator queue (Phase 12) looks
  them up there rather than this record duplicating them.
- `session_id` is always None as constructed by RunOrchestrator (Phase
  11). `.CLAUDE/04` is explicit that a real handoff must NOT terminate
  the live browser session -- the operator takes control of the *same*
  session. Phase 11 does not yet keep a browser alive across the
  request/response boundary (RunOrchestrator's `surface_factory` context
  manager closes the surface before this record is even created) --
  there is no live session for `session_id` to name yet. Introducing a
  session registry that outlives one orchestrator call is exactly what
  Phase 12 ("Intervention / human-handoff persistence + async resume")
  is for; this field exists now so that later work is additive (setting
  a value), not a schema change.
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
