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


@pytest.mark.asyncio
async def test_target_not_found_message_includes_sanitized_underlying_cause(demo_app_base_url: str) -> None:
    """Reproduces the real shape from run 7f8840f08ec34eecaa25e78c696ba134
    (DECISIONS_LOG.md): a syntactically invalid CSS selector (jQuery-only
    :contains(), never valid CSS or a valid Playwright selector) against
    the real demo app. TargetNotFoundError's own message must now carry a
    real, useful fragment of the underlying Playwright error -- not just a
    bare "tried N candidate(s)" -- proving the root cause survives
    PlaywrightSurfaceAdapter._resolve() into the exception's own message
    instead of only being reachable via __cause__ (which discovery never
    reads -- see _execute_and_record in engine.py, unmodified here)."""

    async with launch_playwright_surface() as surface:
        await surface.navigate(demo_app_base_url + "/")
        await _dismiss_session_notice(surface)

        target = Target(primary=_css("tr:has(td:first-child:contains('Savings')) td:nth-child(3)"))
        with pytest.raises(TargetNotFoundError) as excinfo:
            await surface.read(target)

        message = str(excinfo.value)
        assert "tried 1 candidate(s)" in message
        assert "last candidate (css) failed:" in message
        # ":contains()" is a jQuery-only pseudo-class -- never valid CSS and
        # never a Playwright selector extension -- so depending on the exact
        # Playwright/browser build this either raises a SyntaxError
        # immediately or simply never matches anything and times out; either
        # way, a real, non-empty fragment of Playwright's own message must
        # now survive into TargetNotFoundError's own message (previously it
        # stopped at the bare "tried 1 candidate(s)" above, discarding
        # whichever of these it actually was -- see DECISIONS_LOG.md for run
        # 7f8840f08ec34eecaa25e78c696ba134).
        underlying = message.split("last candidate (css) failed:", 1)[1].strip()
        assert underlying
        assert underlying.startswith("Locator.wait_for:")
        # Only the first line -- never Playwright's own multi-line "Call
        # log:" section that follows it (unbounded, and not root cause).
        assert "Call log:" not in message
        # Exception chaining is preserved alongside the new sanitized message
        # -- a real traceback/debugger still sees the full underlying cause.
        assert excinfo.value.__cause__ is not None
