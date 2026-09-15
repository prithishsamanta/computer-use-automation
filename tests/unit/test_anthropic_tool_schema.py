"""Schema-only checks for AnthropicLLMClient's tool schema (`_TOOL_SCHEMA`
in anthropic_client.py). Deliberately NOT marked `live_llm`: this reads a
plain module-level dict, never imports the `anthropic` package itself
(that import is lazy, inside `AnthropicLLMClient.__init__` only -- see
that module's docstring), needs no API key, and makes no network call --
so it runs in every default/CI invocation, unlike
test_anthropic_llm_client.py's module-wide `live_llm` mark.

A real call to Anthropic (run aacd3ecc913d48d8ae9bd88100b93aba,
DECISIONS_LOG.md) was rejected before the model was ever invoked: "input_
schema does not support oneOf, allOf, or anyOf at the top level". An
earlier fix (01f4fff) had added a top-level `anyOf` to try to require
`intent` for non-done proposals -- Anthropic's custom-tool input_schema
does not accept that construct at all, at any nesting depth described by
its own error. These tests verify the schema stays within Anthropic's
accepted subset (a flat `required` list, no top-level composition
keywords) and that the real, conditional rule -- intent is required
unless done is true -- is documented for the model to read rather than
schema-enforced (which Anthropic's schema subset cannot express), with
DiscoveryEngine._parse_proposal as the actual, authoritative enforcement
point (see test_discovery_engine.py's
test_missing_intent_produces_a_clean_deterministic_error_and_is_recorded_honestly
and test_malformed_model_output_is_recorded_but_does_not_terminate_the_run,
both unaffected by this fix, for the runtime-rejection + bounded-recovery
half of this contract)."""

from __future__ import annotations

from cuas.discovery.anthropic_client import _TOOL_SCHEMA

# The exact three keywords Anthropic's rejection named.
_COMPOSITION_KEYWORDS_ANTHROPIC_REJECTS_AT_TOP_LEVEL = ("oneOf", "allOf", "anyOf")


def test_schema_stays_within_anthropics_accepted_subset() -> None:
    """Regression test for the exact rejection this project hit. `required`
    stays a flat list (the only shape used here), and none of the three
    composition keywords Anthropic's error named appear at the top level
    of input_schema -- if one of these is ever reintroduced (e.g. a future
    attempt to make some other field conditionally required), this fails
    before a real API call ever could."""

    schema = _TOOL_SCHEMA["input_schema"]
    assert isinstance(schema["required"], list)
    assert schema["required"] == ["reasoning"]
    for keyword in _COMPOSITION_KEYWORDS_ANTHROPIC_REJECTS_AT_TOP_LEVEL:
        assert keyword not in schema, (
            f"{keyword!r} at the top level of input_schema is rejected by Anthropic's API "
            "('input_schema does not support oneOf, allOf, or anyOf at the top level')"
        )


def test_done_true_response_satisfies_the_schemas_own_required_list() -> None:
    """The legitimate 'goal already achieved' response -- reasoning plus
    done, nothing else -- must remain schema-valid: only `reasoning` is
    ever declared required, so a done=true payload with no action fields
    at all still satisfies the schema's own required list."""

    schema = _TOOL_SCHEMA["input_schema"]
    done_response = {"reasoning": "the balance is already visible", "done": True}
    assert set(schema["required"]).issubset(done_response.keys())


def test_action_missing_intent_is_not_rejected_by_the_schema_itself() -> None:
    """Documents the real, unavoidable limitation this schema now lives
    with: Anthropic's accepted subset cannot express "intent is required
    unless done is true," so an action-shaped payload missing `intent` is
    NOT caught by the schema's own `required` list either -- proving the
    gap is real, not just asserted in a comment. `intent`'s own
    description states the rule in free text for the model to read, and
    DiscoveryEngine._parse_proposal is what actually enforces it at
    runtime (defense in depth, not a schema substitute)."""

    schema = _TOOL_SCHEMA["input_schema"]
    action_missing_intent = {"reasoning": "clicking search", "action_type": "click"}
    assert set(schema["required"]).issubset(action_missing_intent.keys())

    description = schema["properties"]["intent"]["description"]
    assert "required" in description.lower()
