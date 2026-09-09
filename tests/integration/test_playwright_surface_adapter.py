"""Exercises PlaywrightSurfaceAdapter with a real headless Chromium against
the real demo_app (tests/integration/conftest.py starts it as a
subprocess). No mocking on either side -- this is what proves the
SurfaceAdapter abstraction and its locator fallback chain actually work
against the legacy-flavored markup, not just against a fake.

Marked `integration` (pyproject.toml) so it's excluded only when a caller
explicitly filters it out; it needs a browser but never an LLM/API key.
"""

from __future__ import annotations

import pytest

from cuas.domain import Locator, LocatorStrategy, Target, TargetNotFoundError
from cuas.surface.adapter import WaitCondition, WaitConditionKind
from cuas.surface.playwright_adapter import launch_playwright_surface

pytestmark = pytest.mark.integration


def _role(role: str, name: str | None = None) -> Locator:
    params = {"role": role}
    if name is not None:
        params["name"] = name
    return Locator(strategy=LocatorStrategy.ROLE_NAME, params=params)


def _css(selector: str, frame: str | None = None) -> Locator:
    return Locator(strategy=LocatorStrategy.CSS, params={"selector": selector}, frame=frame)


async def _dismiss_session_notice(surface) -> None:
    await surface.click(Target(primary=_role("button", "OK")))


@pytest.mark.asyncio
async def test_navigate_and_observe(demo_app_base_url: str) -> None:
    async with launch_playwright_surface() as surface:
        await surface.navigate(demo_app_base_url + "/")
        observation = await surface.observe()
        assert observation.url == demo_app_base_url + "/"
        assert "Member Services" in observation.visible_text


@pytest.mark.asyncio
async def test_full_capability_path_search_to_savings_balance(demo_app_base_url: str) -> None:
    async with launch_playwright_surface() as surface:
        await surface.navigate(demo_app_base_url + "/")
        await _dismiss_session_notice(surface)

        await surface.fill(Target(primary=_role("textbox")), "M1001")
        await surface.click(Target(primary=_role("button", "Search")))

        await surface.wait_for(
            WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Accounts", timeout_ms=5000)
        )

        balance = await surface.read(
            Target(primary=_css("#acct-row-2 td:nth-child(3)", frame="#accounts-frame"))
        )
        assert balance == "$18204.55"


@pytest.mark.asyncio
async def test_search_unknown_member_is_business_outcome_text(demo_app_base_url: str) -> None:
    async with launch_playwright_surface() as surface:
        await surface.navigate(demo_app_base_url + "/")
        await _dismiss_session_notice(surface)

        await surface.fill(Target(primary=_role("textbox")), "no-such-member")
        await surface.click(Target(primary=_role("button", "Search")))

        await surface.wait_for(
            WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Member not found.", timeout_ms=5000)
        )


@pytest.mark.asyncio
async def test_target_resolution_falls_back_past_a_failing_primary(demo_app_base_url: str) -> None:
    """The search input has no real <label>, so a LABEL-strategy primary
    must fail before the ROLE_NAME fallback resolves it -- proving the
    fallback chain is actually walked, not just declared."""

    async with launch_playwright_surface() as surface:
        await surface.navigate(demo_app_base_url + "/")
        await _dismiss_session_notice(surface)

        target = Target(
            primary=Locator(strategy=LocatorStrategy.LABEL, params={"label": "Member ID or Last Name"}),
            fallbacks=[_role("textbox")],
        )
        await surface.fill(target, "M1002")
        await surface.click(Target(primary=_role("button", "Search")))

        observation = await surface.observe()
        assert "M1002" in observation.url or "John" in observation.visible_text


@pytest.mark.asyncio
async def test_target_not_found_when_every_candidate_fails(demo_app_base_url: str) -> None:
    async with launch_playwright_surface() as surface:
        await surface.navigate(demo_app_base_url + "/")
        await _dismiss_session_notice(surface)

        target = Target(primary=_css("#this-selector-matches-nothing"))
        with pytest.raises(TargetNotFoundError):
            await surface.read(target)
