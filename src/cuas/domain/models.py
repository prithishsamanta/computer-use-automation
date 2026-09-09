"""Shared domain vocabulary used across discovery, replay, artifact, safety,
and handoff. These are the small, stable types that many packages need to
agree on; package-specific detail (artifact steps, checkpoints, wait rules,
etc.) is layered on top of these in the packages that own them, not here.

See .CLAUDE/02_ARTIFACT_SCHEMA.md and .CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md
for the source design decisions these types encode.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AppContext(BaseModel):
    """Identifies which vendor application / version / institution a run,
    capability, or policy decision applies to.

    This is the metadata capability retrieval filters on *before* semantic
    similarity (.CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md), and the scope
    that policy layering (global -> vendor/app -> tenant) resolves against.
    """

    vendor: str
    application: str
    version: str
    tenant_id: str


class RiskLevel(str, Enum):
    """The three action categories from .CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md.

    This is informational metadata carried on an Action/Step. It is never
    the enforcement authority by itself -- the runtime PolicyEngine (safety
    package) makes the actual SAFE / APPROVAL_REQUIRED / BLOCKED decision,
    because policy can vary by tenant and can change after an artifact or
    a discovery proposal was created.
    """

    SAFE = "safe"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED = "blocked"


class ControlState(str, Enum):
    """Human handoff control state machine (.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md)."""

    RUNNING_AUTOMATION = "running_automation"
    PAUSED_WAITING_FOR_HUMAN = "paused_waiting_for_human"
    HUMAN_CONTROL = "human_control"
    RESUME_REQUESTED = "resume_requested"


class InterventionStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class LocatorStrategy(str, Enum):
    """Preferred-to-last-resort target resolution order from
    .CLAUDE/03_DISCOVERY_AND_REPLAY.md. A Target's fallback chain should be
    ordered by this preference, most stable first.
    """

    ROLE_NAME = "role_name"
    SEMANTIC_ATTRIBUTE = "semantic_attribute"
    LABEL = "label"
    TEXT_CONTEXT = "text_context"
    STRUCTURAL = "structural"
    CSS = "css"
    XPATH = "xpath"
    COORDINATES = "coordinates"


class Locator(BaseModel):
    """A single candidate way to find an element on the surface.

    `params` holds strategy-specific fields (e.g. role_name -> {"role":
    "textbox", "name": "Member ID"}; label -> {"label": "Savings Balance"};
    css -> {"selector": "..."}) rather than a large union of optional
    fields, since each strategy's shape is small and the set of strategies
    is fixed by LocatorStrategy. The PlaywrightSurfaceAdapter (Phase 3)
    interprets `params` according to `strategy`.
    """

    strategy: LocatorStrategy
    params: dict[str, Any] = Field(default_factory=dict)
    frame: str | None = Field(
        default=None,
        description="Optional frame/iframe name or selector this locator resolves within.",
    )


class Target(BaseModel):
    """A primary locator plus bounded, deterministic fallback candidates."""

    primary: Locator
    fallbacks: list[Locator] = Field(default_factory=list)


class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    READ = "read"
    WAIT_FOR = "wait_for"
    DISMISS = "dismiss"


class Action(BaseModel):
    """A single structured, executable step.

    This is the shape an LLM proposes during discovery (.CLAUDE/03) and
    that an artifact Step wraps with ordering/checkpoint/wait metadata
    (.CLAUDE/02_ARTIFACT_SCHEMA.md, built in Phase 4). Keeping Action here
    lets discovery, replay, and safety all depend on the same type without
    depending on each other.
    """

    id: str
    action_type: ActionType
    intent: str = Field(
        description="Normalized business intent, e.g. 'search_member', 'close_account'. "
        "Policy evaluates this plus AppContext, not raw UI labels."
    )
    target: Target | None = None
    value: str | None = None
    risk: RiskLevel = RiskLevel.SAFE
