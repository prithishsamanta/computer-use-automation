"""LLMClient: the abstraction DiscoveryEngine depends on instead of the
Anthropic SDK directly (.CLAUDE/07_IMPLEMENTATION_GUIDANCE.md's package
shape; same Strategy / Dependency-Inversion pattern as SurfaceAdapter/
PolicyEngine/ArtifactRepository elsewhere in this codebase). Two
implementations: AnthropicLLMClient (real, thin, lazy-imports the SDK,
anthropic_client.py) and tests/fixtures/fake_llm_client.py's
FakeLLMClient (a scripted double every unit test in this phase uses so
none of them need network access or an API key).

`propose_action` returns a raw `LLMResponse`, not an `Action` -- turning
that raw proposal into a validated `Action` (or rejecting it as
malformed) is `DiscoveryEngine`'s job, not any given client's
(DiscoveryEngine._parse_proposal), so a fake client and the real
Anthropic client are validated by exactly the same code path
(.CLAUDE/03_DISCOVERY_AND_REPLAY.md: "The model proposes actions; it does
not directly execute browser operations"; "Every proposed action must
pass deterministic policy before execution").
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from cuas.discovery.models import DiscoveryGoal, DiscoveryHistoryEntry
from cuas.domain import AppContext
from cuas.surface.adapter import Observation


class LLMResponse(BaseModel):
    """One discovery turn's raw provider output.

    `proposal`, when not None, is the model's raw structured payload --
    untyped and unvalidated on purpose; DiscoveryEngine._parse_proposal is
    the single place that turns it into a real Action or rejects it as
    malformed. `parse_error` set (with `proposal` None) means the provider
    itself could not produce any structured output at all (e.g. no
    tool_use block in an Anthropic response) -- a distinct, provider-level
    failure from a structurally invalid `proposal` dict, but both count as
    "malformed model output" to DiscoveryEngine, which must stop or
    escalate rather than guess at a repair either way.
    """

    raw_text: str
    proposal: dict[str, Any] | None = None
    parse_error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(ABC):
    @abstractmethod
    async def propose_action(
        self,
        *,
        goal: DiscoveryGoal,
        context: AppContext,
        observation: Observation,
        history: list[DiscoveryHistoryEntry],
    ) -> LLMResponse: ...
