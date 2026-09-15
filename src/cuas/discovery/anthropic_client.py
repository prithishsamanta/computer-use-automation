"""The one real LLMClient implementation (.CLAUDE/07_IMPLEMENTATION_GUIDANCE.md:
"Keep the real Anthropic implementation thin and provider-isolated"). This
is the only module in the codebase that imports `anthropic`, and it
imports it lazily inside __init__ rather than at module import time, so
the rest of the codebase -- including every test that doesn't exercise
this class -- never needs the package installed or an API key present.

Deliberately thin: this class's only job is turning
(goal, context, observation, history) into one Anthropic tool-forced
`messages.create` call, and turning that call's tool_use block back into
an LLMResponse.proposal dict. Validating that dict into a real Action (or
rejecting it as malformed) is DiscoveryEngine's job, not this class's --
see llm_client.py's docstring for why that split matters.

A note on redaction, since this is the one place raw sensitive values
(e.g. an actual member id) are deliberately put into text that leaves the
process: that is correct and necessary here -- the model needs the real
value to type it into a search box, and the prompt sent to Anthropic is
not "observability" in the .CLAUDE/06 sense, it's the system doing its
job. Redaction applies to what DiscoveryEngine *persists/logs*
afterwards (the DiscoveryTrace, RunEvents), not to what this class sends
to the model -- see engine.py's `_redact_history` / `_redact_json`.

Never exercised by the default/CI test run -- see
tests/unit/test_anthropic_llm_client.py, marked `live_llm`
(pyproject.toml), which needs a real ANTHROPIC_API_KEY and is excluded
from `pytest -m "not live_llm"`.
"""

from __future__ import annotations

import json
from typing import Any

from cuas.discovery.llm_client import LLMClient, LLMResponse
from cuas.discovery.models import DiscoveryGoal, DiscoveryHistoryEntry
from cuas.domain import AppContext, LocatorStrategy, Target
from cuas.surface.adapter import Observation

_TOOL_NAME = "propose_action"

# Deliberately narrower than the full ActionType enum: discovery never
# offers the model navigate-away-mid-flow or wait_for (there is no
# artifact-declared checkpoint condition for it to describe), so those
# simply are not options it can propose in the first place, rather than
# something DiscoveryEngine has to reject after the fact.
_TOOL_SCHEMA: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": (
        "Propose exactly one next UI action toward the stated goal, or declare the "
        "goal already satisfied by the current page. Never propose more than one "
        "action at a time -- you will be shown the result and asked again."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reasoning": {
                "type": "string",
                "description": "Brief explanation of why this action (or 'done') moves toward the goal.",
            },
            "done": {
                "type": "boolean",
                "description": "True if the current page already shows the goal has been achieved. "
                "When true, omit action_type/intent/target/value.",
            },
            "action_type": {
                "type": "string",
                "enum": ["navigate", "click", "fill", "read", "dismiss"],
            },
            "intent": {
                "type": "string",
                "description": "Normalized business intent for this action, e.g. 'enter_member_id', "
                "'submit_member_search', 'dismiss_known_popup'. Never the raw UI label text. "
                "Required for every action proposal, i.e. whenever done is not true.",
            },
            "target": {
                "type": "object",
                "description": "Required for click/fill/read/dismiss. Omit for navigate.",
                "properties": {
                    "strategy": {"type": "string", "enum": ["role_name", "css"]},
                    "role": {"type": "string", "description": "Accessibility role, for strategy=role_name."},
                    "name": {"type": "string", "description": "Accessible name, for strategy=role_name."},
                    "selector": {"type": "string", "description": "CSS selector, for strategy=css."},
                    "frame": {"type": "string", "description": "Optional iframe selector the target lives in."},
                },
            },
            "value": {
                "type": "string",
                "description": "Required for fill (the text to type) and navigate (the URL).",
            },
        },
        # `intent` cannot simply move into this flat `required` list -- a
        # done=true response legitimately omits it (see `done`'s own
        # description above), and Anthropic's custom-tool input_schema
        # rejects a conditional shape entirely: an earlier version of this
        # schema declared a top-level `anyOf: [{"required": ["done"]},
        # {"required": ["intent"]}]` to express "either a done claim, or
        # intent present," and Anthropic's API rejected the whole request
        # with `tools.0.custom.input_schema: input_schema does not support
        # oneOf, allOf, or anyOf at the top level" -- a real run,
        # aacd3ecc913d48d8ae9bd88100b93aba, hit exactly this before the
        # model was ever called (DECISIONS_LOG.md). Anthropic's accepted
        # schema subset has no way to express "field X is required only
        # when field Y is absent," so conditional requiredness for `intent`
        # cannot be declared here at all -- only documented (see `intent`'s
        # own description above, and this comment) and enforced at runtime.
        # `DiscoveryEngine._parse_proposal` is the actual, authoritative
        # validator for exactly this rule (action_type/intent required,
        # target/value required per action_type, done exempt from all of
        # it) and is never weakened by what this schema can or cannot
        # declare.
        "required": ["reasoning"],
    },
}


# Bound on a rendered target description folded into a failed history
# line (see _describe_target) -- keeps it a single short, predictable
# addition to the prompt regardless of how long a real selector/label
# gets.
_MAX_TARGET_DETAIL_CHARS = 120


def _quote(value: str) -> str:
    """Plain double-quoted rendering for prompt text -- not JSON/repr
    escaping (this is read by a model, not parsed as a literal): embedded
    double quotes are swapped for single quotes so the description stays
    readable on one line without backslash noise."""
    return '"' + value.replace('"', "'") + '"'


def _describe_target(target: Target) -> str:
    """A compact, bounded, typed rendering of a Target's primary locator --
    enough for the model to know *which* locator it tried (dispatches on
    LocatorStrategy the same way PlaywrightSurfaceAdapter._playwright_locator
    does), never the full Target/fallback structure and never
    `Action.value` (which can carry sensitive user input the model typed,
    e.g. a member id -- this only ever describes WHERE an action targeted,
    not what was typed there). Real run 7f8840f08ec34eecaa25e78c696ba134
    (DECISIONS_LOG.md) showed a failed selector was never included in this
    history at all, so a stateless next turn had no way to know which
    locator its own error message was about."""

    locator = target.primary
    strategy = locator.strategy
    params = locator.params

    if strategy == LocatorStrategy.ROLE_NAME:
        name = params.get("name")
        detail = f"role={_quote(str(params.get('role', '')))}"
        if name:
            detail += f" name={_quote(str(name))}"
    elif strategy == LocatorStrategy.SEMANTIC_ATTRIBUTE:
        detail = f"{params.get('attribute', '')}={_quote(str(params.get('value', '')))}"
    elif strategy == LocatorStrategy.LABEL:
        detail = f"label={_quote(str(params.get('label', '')))}"
    elif strategy == LocatorStrategy.TEXT_CONTEXT:
        detail = f"text={_quote(str(params.get('text', '')))}"
    elif strategy in (LocatorStrategy.STRUCTURAL, LocatorStrategy.CSS):
        detail = f"selector={_quote(str(params.get('selector', '')))}"
    elif strategy == LocatorStrategy.XPATH:
        detail = f"xpath={_quote(str(params.get('xpath', '')))}"
    elif strategy == LocatorStrategy.COORDINATES:
        detail = "coordinates"
    else:
        detail = "unknown strategy"

    description = f"{strategy.value} {detail}"
    if len(description) > _MAX_TARGET_DETAIL_CHARS:
        description = description[:_MAX_TARGET_DETAIL_CHARS] + "..."
    return description


class AnthropicLLMClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 1024,
    ) -> None:
        from anthropic import AsyncAnthropic  # lazy: see module docstring

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def propose_action(
        self,
        *,
        goal: DiscoveryGoal,
        context: AppContext,
        observation: Observation,
        history: list[DiscoveryHistoryEntry],
    ) -> LLMResponse:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=self._build_system_prompt(goal, context),
            messages=[{"role": "user", "content": self._build_user_message(goal, observation, history)}],
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
        )

        input_tokens = response.usage.input_tokens if response.usage is not None else 0
        output_tokens = response.usage.output_tokens if response.usage is not None else 0

        tool_use = next((block for block in response.content if block.type == "tool_use"), None)
        if tool_use is None:
            return LLMResponse(
                raw_text=str(response.content),
                proposal=None,
                parse_error="model response contained no tool_use block",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        return LLMResponse(
            raw_text=json.dumps(tool_use.input),
            proposal=tool_use.input,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _build_system_prompt(self, goal: DiscoveryGoal, context: AppContext) -> str:
        return (
            "You are operating a legacy back-office web UI on behalf of a financial "
            f"institution, one small action at a time, to discover how to perform a "
            f"capability called {goal.capability_id!r}. Application: {context.vendor}/"
            f"{context.application} (tenant {context.tenant_id}).\n\n"
            f"You propose exactly one structured action per turn using the {_TOOL_NAME} "
            "tool; you never execute anything directly. Every action you propose is "
            "independently checked by a deterministic policy engine before it runs -- "
            "some actions will be rejected as unsafe or as requiring human approval "
            "regardless of what you intend, and that ends this discovery attempt rather "
            "than letting you try another approach. So only propose actions that are "
            "clearly safe, read-only, or routine data lookups; never propose closing "
            "accounts, submitting transactions, or anything consequential.\n\n"
            "Use action_type=navigate only to load the starting page (it has already "
            "been loaded once for you). Prefer role_name locators (accessibility role "
            "plus visible name) over css selectors when a control's role and name are "
            "both apparent from the page text; fall back to a css selector only when "
            "you can reasonably infer one from context. Set done=true, with no other "
            "field except reasoning, once the current page already shows the "
            "information or result the goal describes."
        )

    def _build_user_message(
        self, goal: DiscoveryGoal, observation: Observation, history: list[DiscoveryHistoryEntry]
    ) -> str:
        lines = [f"Goal: {goal.description}"]
        if goal.inputs:
            lines.append(f"Inputs available to you: {json.dumps(goal.inputs)}")
        if history:
            lines.append("\nHistory so far:")
            for entry in history:
                detail = f" ({entry.error_message})" if entry.error_message else ""
                # Only for a genuine execution failure -- not policy
                # escalations/denials, not "done" turns -- and only when a
                # target actually exists (a NAVIGATE action has none). This
                # is deliberately the one extra thing execution_failed
                # entries carry: which locator the failed action tried,
                # never `entry.action.value` (see _describe_target).
                target_detail = ""
                if entry.outcome == "execution_failed" and entry.action.target is not None:
                    target_detail = f", target={_describe_target(entry.action.target)}"
                lines.append(
                    f"- step {entry.step_index}: proposed {entry.action.action_type.value} "
                    f"(intent={entry.action.intent!r}{target_detail}) -> {entry.outcome}{detail}"
                )
        lines.append(f"\nCurrent page URL: {observation.url}")
        lines.append(f"Current visible page text:\n{observation.visible_text}")
        return "\n".join(lines)
