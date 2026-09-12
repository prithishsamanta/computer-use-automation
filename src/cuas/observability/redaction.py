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


def redact_dict(data: dict[str, Any], sensitive_keys: set[str]) -> dict[str, Any]:
    """The same idea as redact_inputs, generalized to any dict of named
    values without requiring an Artifact's InputSpec table. Discovery
    (Phase 8) doesn't have artifact-declared inputs yet -- there is no
    artifact until a later, separate construction step -- so it names its
    own sensitive inputs directly (DiscoveryGoal.sensitive_inputs) and
    calls this instead of redact_inputs."""

    return {key: (REDACTED if key in sensitive_keys else value) for key, value in data.items()}


def redact_text(text: str, sensitive_values: list[str]) -> str:
    """Scrubs every exact occurrence of each given raw value out of
    freeform text before it can be logged or persisted.

    Discovery's prompts, model responses, and page observations are
    unstructured text, not a dict of named fields -- redact_inputs/
    redact_dict don't apply. This is the freeform equivalent: given the
    raw sensitive values themselves (e.g. the actual member ID string,
    not its field name), replace every occurrence wherever it appears.
    Deliberately simple substring replacement, not a regex or NLP
    approach -- a sensitive value discovery cares about is always an
    exact, known string (it came from DiscoveryGoal.inputs), never a
    pattern to be inferred from the text.
    """

    if not text:
        return text
    redacted = text
    for value in sensitive_values:
        if value:
            redacted = redacted.replace(value, REDACTED)
    return redacted
