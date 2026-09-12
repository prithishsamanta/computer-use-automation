"""Live Anthropic API test for AnthropicLLMClient. Marked `live_llm`
(pyproject.toml): needs a real ANTHROPIC_API_KEY and makes a live call to
Anthropic's API. Excluded from the default/CI run (`pytest -m "not
live_llm"`), never executed automatically -- run it explicitly with
`pytest -m live_llm` once ANTHROPIC_API_KEY is set.

Every other discovery test (unit and integration) uses FakeLLMClient
instead, precisely so this file's absence from a normal run never blocks
development or CI (.CLAUDE/07_IMPLEMENTATION_GUIDANCE.md: "Use mocks/fakes
for CI and only pause when an actual live discovery run needs
ANTHROPIC_API_KEY").
"""

from __future__ import annotations

import os

import pytest

from cuas.discovery.anthropic_client import AnthropicLLMClient
from cuas.discovery.models import DiscoveryGoal
from cuas.domain import AppContext
from cuas.surface.adapter import Observation

pytestmark = pytest.mark.live_llm

CONTEXT = AppContext(vendor="meridian-demo", application="credit-union-admin", version="1.0.0", tenant_id="base")


@pytest.mark.asyncio
async def test_anthropic_client_proposes_a_structured_action_for_a_real_search_page() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        pytest.skip("ANTHROPIC_API_KEY not set -- this test makes a real call to the Anthropic API")

    client = AnthropicLLMClient(api_key)
    goal = DiscoveryGoal(
        capability_id="get_savings_balance",
        description="Find member M1001 and read their savings balance.",
        start_url="http://demo.invalid/",
        inputs={"member_id": "M1001"},
        sensitive_inputs={"member_id"},
    )
    observation = Observation(
        url="http://demo.invalid/",
        visible_text="Member Search\n[Member ID: ________]\n[Search]",
    )

    response = await client.propose_action(goal=goal, context=CONTEXT, observation=observation, history=[])

    assert response.parse_error is None
    assert response.proposal is not None
    assert "reasoning" in response.proposal
    assert response.input_tokens > 0
    assert response.output_tokens > 0
