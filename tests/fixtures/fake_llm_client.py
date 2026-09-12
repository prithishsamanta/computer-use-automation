"""A scriptable LLMClient test double -- tests/fixtures/fake_surface.py's
sibling for the model side of discovery. Lets DiscoveryEngine unit tests
script exactly what "the model" proposes turn by turn (a normal action, a
malformed payload, the same action repeatedly for loop detection, a
done=true claim, ...) with no network access, no API key, and no
dependency on AnthropicLLMClient at all.

Module-level helpers (`propose`, `propose_done`, `malformed`,
`role_target`, `css_target`) build the raw `proposal` dict shape
DiscoveryEngine._parse_proposal expects, mirroring the real
AnthropicLLMClient's tool-call payload shape closely enough that a test
exercising the fake is exercising the same parsing code a real response
would hit.
"""

from __future__ import annotations

from typing import Any

from cuas.discovery.llm_client import LLMClient, LLMResponse
from cuas.discovery.models import DiscoveryGoal, DiscoveryHistoryEntry
from cuas.domain import AppContext
from cuas.surface.adapter import Observation


class FakeLLMClient(LLMClient):
    def __init__(self, *responses: LLMResponse) -> None:
        self._responses: list[LLMResponse] = list(responses)
        self.calls: list[tuple[DiscoveryGoal, AppContext, Observation, list[DiscoveryHistoryEntry]]] = []

    def script(self, *responses: LLMResponse) -> None:
        """Append more responses to the queue -- lets a test build up a
        multi-turn script incrementally rather than all at construction
        time."""
        self._responses.extend(responses)

    async def propose_action(
        self,
        *,
        goal: DiscoveryGoal,
        context: AppContext,
        observation: Observation,
        history: list[DiscoveryHistoryEntry],
    ) -> LLMResponse:
        self.calls.append((goal, context, observation, list(history)))
        if not self._responses:
            raise AssertionError(
                "FakeLLMClient ran out of scripted responses -- the test scripted fewer "
                "turns than DiscoveryEngine actually took; script one more response per "
                "expected loop iteration."
            )
        return self._responses.pop(0)


def propose(
    action_type: str,
    intent: str,
    *,
    target: dict[str, Any] | None = None,
    value: str | None = None,
    reasoning: str = "test reasoning",
) -> LLMResponse:
    proposal: dict[str, Any] = {"reasoning": reasoning, "action_type": action_type, "intent": intent}
    if target is not None:
        proposal["target"] = target
    if value is not None:
        proposal["value"] = value
    return LLMResponse(raw_text=repr(proposal), proposal=proposal)


def propose_done(reasoning: str = "goal accomplished") -> LLMResponse:
    proposal = {"done": True, "reasoning": reasoning}
    return LLMResponse(raw_text=repr(proposal), proposal=proposal)


def malformed(raw_text: str = "<not structured output>", error: str | None = None) -> LLMResponse:
    return LLMResponse(raw_text=raw_text, proposal=None, parse_error=error or "model response contained no tool_use block")


def role_target(role: str, name: str | None = None) -> dict[str, Any]:
    target: dict[str, Any] = {"strategy": "role_name", "role": role}
    if name is not None:
        target["name"] = name
    return target


def css_target(selector: str, frame: str | None = None) -> dict[str, Any]:
    target: dict[str, Any] = {"strategy": "css", "selector": selector}
    if frame is not None:
        target["frame"] = frame
    return target
