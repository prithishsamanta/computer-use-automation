"""Schema-only checks for AnthropicLLMClient's tool schema (`_TOOL_SCHEMA`
in anthropic_client.py). Deliberately NOT marked `live_llm`: this reads a
plain module-level dict, never imports the `anthropic` package itself
(that import is lazy, inside `AnthropicLLMClient.__init__` only -- see
that module's docstring), needs no API key, and makes no network call --
so it runs in every default/CI invocation, unlike
test_anthropic_llm_client.py's module-wide `live_llm` mark.
"""

from __future__ import annotations

from cuas.discovery.anthropic_client import _TOOL_SCHEMA


def test_intent_is_required_for_every_non_done_action_proposal() -> None:
    """Real run e199e82bd3774cf6af2124d699ca5cfa (DECISIONS_LOG.md) showed
    a model omitting `intent` was consistent with the schema as it used to
    be written: the flat `required` array only ever listed `reasoning`,
    and `intent`'s own description never said it was required anywhere.
    `intent` can't simply move into that flat array, though -- a
    done=true response legitimately omits it (see `done`'s own
    description) -- so the schema instead declares: reasoning is always
    required, and additionally either this is a done claim or intent is
    present."""

    schema = _TOOL_SCHEMA["input_schema"]
    assert schema["required"] == ["reasoning"]
    assert {"required": ["done"]} in schema["anyOf"]
    assert {"required": ["intent"]} in schema["anyOf"]


def test_intent_description_states_it_is_required() -> None:
    description = _TOOL_SCHEMA["input_schema"]["properties"]["intent"]["description"]
    assert "required" in description.lower()
