"""Structured event vocabulary (.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md,
"Structured Logging"). Every run gets a run_id; every meaningful thing
that happens during it -- in replay, policy, and recovery today, in
orchestration/intervention once those exist (Phases 11-12, same EventSink)
-- is one of these events, so a reviewer can answer every question in the
doc's "Traceability" section from the event stream alone, without reading
code.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    ARTIFACT_LOADED = "artifact_loaded"
    STEP_STARTED = "step_started"
    POLICY_CHECKED = "policy_checked"
    ACTION_EXECUTED = "action_executed"
    CHECKPOINT_PASSED = "checkpoint_passed"
    BUSINESS_OUTCOME_DETECTED = "business_outcome_detected"
    RECOVERY_ATTEMPTED = "recovery_attempted"
    STEP_FAILED = "step_failed"
    EVIDENCE_CAPTURED = "evidence_captured"
    RUN_COMPLETED = "run_completed"
    # Phase 8 (discovery) additions -- same vocabulary, a different
    # component ("discovery_engine") emits these. RUN_STARTED/
    # POLICY_CHECKED/ACTION_EXECUTED/BUSINESS_OUTCOME_DETECTED/STEP_FAILED/
    # EVIDENCE_CAPTURED/RUN_COMPLETED are all reused as-is; these two have
    # no replay analogue.
    LLM_ACTION_PROPOSED = "llm_action_proposed"
    MALFORMED_MODEL_OUTPUT = "malformed_model_output"


class RunEvent(BaseModel):
    """.CLAUDE/06's suggested event shape, field for field:

        {"timestamp": ..., "run_id": ..., "component": ...,
         "event": ..., "step_id": ..., "status": ..., "details": "..."}

    `details` is this implementation's home for whatever the doc's own
    example calls "details" (a free-text string there) -- here a small
    structured dict, since a JSONL sink can hold that just as easily and
    it's far more useful to grep/query.
    """

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: str
    component: str = "replay_engine"
    event: EventType
    step_id: str | None = None
    status: str = "info"  # coarse outcome of *this event*: info | success | failure
    details: dict[str, Any] = Field(default_factory=dict)
