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
from cuas.domain import AppContext
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
                "'submit_member_search', 'dismiss_known_popup'. Never the raw UI label text.",
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
        "required": ["reasoning"],
    },
}


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
                lines.append(
                    f"- step {entry.step_index}: proposed {entry.action.action_type.value} "
                    f"(intent={entry.action.intent!r}) -> {entry.outcome}{detail}"
                )
        lines.append(f"\nCurrent page URL: {observation.url}")
        lines.append(f"Current visible page text:\n{observation.visible_text}")
        return "\n".join(lines)
