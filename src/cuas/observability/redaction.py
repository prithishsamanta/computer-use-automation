"""Input redaction for structured logs (.CLAUDE/06: "Observability must
not become a data-leak mechanism"; this phase's explicit instruction not
to persist raw sensitive input values -- e.g. a member ID -- in logs or
evidence).

Scope, deliberately narrow. This redacts *raw invocation inputs* (the
dict passed to ReplayEngine.run()) before they can reach a run_started
event. Nothing else in the replay path needs it: an action_executed event
logs a step's *value template* (e.g. "{{member_id}}"), never the
substituted value, so a resolved sensitive input is simply never
formatted into a log line to begin with -- there is nothing to strip
there because the leak never occurs by construction. See
ReplayEngine._execute_action / the ACTION_EXECUTED emission in
replay/engine.py.
"""

from __future__ import annotations

from typing import Any

from cuas.artifact.schema import Artifact

REDACTED = "[REDACTED]"


def redact_inputs(artifact: Artifact, inputs: dict[str, Any]) -> dict[str, Any]:
    """A copy of `inputs` with every value whose declared InputSpec has
    `sensitive=True` replaced by REDACTED. An input name absent from the
    artifact's own `inputs` schema is passed through unchanged -- schema
    validation is ReplayEngine's job, not this function's, and that
    shouldn't happen for an artifact-valid call anyway."""

    redacted: dict[str, Any] = {}
    for name, value in inputs.items():
        spec = artifact.inputs.get(name)
        redacted[name] = REDACTED if (spec is not None and spec.sensitive) else value
    return redacted
