"""The one real SurfaceAdapter implementation for this take-home.

Locator resolution follows the fallback order from
.CLAUDE/03_DISCOVERY_AND_REPLAY.md: role+name, semantic attribute, label,
text+context, structural/CSS, xpath, coordinates -- tried in the order a
Target lists them (primary first, then fallbacks), each bounded by a short
per-candidate timeout so an exhausted fallback chain still fails in bounded
time rather than hanging (.CLAUDE/03: "Fallbacks should be deterministic
and bounded").
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator as PlaywrightLocator
from playwright.async_api import Page, async_playwright

from cuas.domain import Locator, LocatorStrategy, Target, TargetNotFoundError
from cuas.surface.adapter import Evidence, Observation, SurfaceAdapter, WaitCondition, WaitConditionKind

# Per-candidate timeout while walking a Target's fallback chain. Deliberately
# shorter than a checkpoint's own wait timeout: this is "is this candidate
# even present", not "wait for the app to catch up".
FALLBACK_CANDIDATE_TIMEOUT_MS = 1500


class PlaywrightSurfaceAdapter(SurfaceAdapter):
    def __init__(self, page: Page):
        self._page = page

    def _scope(self, frame: str | None):
        return self._page.frame_locator(frame) if frame else self._page

    def _playwright_locator(self, locator: Locator) -> PlaywrightLocator:
        scope = self._scope(locator.frame)
        params = locator.params

        if locator.strategy == LocatorStrategy.ROLE_NAME:
            return scope.get_by_role(params["role"], name=params.get("name"))
        if locator.strategy == LocatorStrategy.SEMANTIC_ATTRIBUTE:
            return scope.locator(f"[{params['attribute']}='{params['value']}']")
        if locator.strategy == LocatorStrategy.LABEL:
            return scope.get_by_label(params["label"])
        if locator.strategy == LocatorStrategy.TEXT_CONTEXT:
            return scope.get_by_text(params["text"], exact=params.get("exact", False))
        if locator.strategy in (LocatorStrategy.STRUCTURAL, LocatorStrategy.CSS):
            return scope.locator(params["selector"])
        if locator.strategy == LocatorStrategy.XPATH:
            return scope.locator(f"xpath={params['xpath']}")

        raise ValueError(
            f"{locator.strategy} has no element-locator form; coordinates are handled "
            "separately in _resolve/click, not through _playwright_locator."
        )

    async def _resolve(self, target: Target, action_desc: str) -> PlaywrightLocator | tuple[float, float]:
        """Try primary, then each fallback in order. Returns either a
        resolved Playwright locator, or an (x, y) tuple if resolution fell
        all the way through to a COORDINATES candidate (last resort per
        .CLAUDE/03_DISCOVERY_AND_REPLAY.md)."""

        candidates = [target.primary, *target.fallbacks]
        last_error: Exception | None = None

        for candidate in candidates:
            if candidate.strategy == LocatorStrategy.COORDINATES:
                return (candidate.params["x"], candidate.params["y"])
            try:
                locator = self._playwright_locator(candidate).first
                await locator.wait_for(state="visible", timeout=FALLBACK_CANDIDATE_TIMEOUT_MS)
                return locator
            except Exception as exc:  # noqa: BLE001 - deliberately broad: any candidate can fail differently
                last_error = exc
                continue

        raise TargetNotFoundError(
            f"Could not resolve target for {action_desc}: tried {len(candidates)} candidate(s)"
        ) from last_error

    async def navigate(self, url: str) -> None:
        await self._page.goto(url)

    async def observe(self) -> Observation:
        return Observation(url=self._page.url, visible_text=await self._page.inner_text("body"))

    async def click(self, target: Target, *, timeout_ms: int | None = None) -> None:
        resolved = await self._resolve(target, "click")
        if isinstance(resolved, tuple):
            x, y = resolved
            await self._page.mouse.click(x, y)
            return
        try:
            await resolved.click(timeout=timeout_ms or FALLBACK_CANDIDATE_TIMEOUT_MS)
        except PlaywrightError as exc:
            # Resolution succeeded (the element exists) but Playwright's own
            # actionability check failed -- e.g. something is covering it
            # (the demo app's session-notice overlay). From ReplayEngine's
            # perspective that is the same class of problem as not finding
            # the element at all: it could not safely interact with the
            # target, which is exactly the case recoverable_conditions and
            # bounded retry exist for (.CLAUDE/03_DISCOVERY_AND_REPLAY.md).
            raise TargetNotFoundError(f"click did not become actionable: {exc}") from exc

    async def fill(self, target: Target, value: str, *, timeout_ms: int | None = None) -> None:
        resolved = await self._resolve(target, "fill")
        if isinstance(resolved, tuple):
            raise TargetNotFoundError("Cannot fill a value at raw coordinates; no element resolved")
        try:
            await resolved.fill(value, timeout=timeout_ms or FALLBACK_CANDIDATE_TIMEOUT_MS)
        except PlaywrightError as exc:
            raise TargetNotFoundError(f"fill did not become actionable: {exc}") from exc

    async def read(self, target: Target) -> str:
        resolved = await self._resolve(target, "read")
        if isinstance(resolved, tuple):
            raise TargetNotFoundError("Cannot read text from raw coordinates; no element resolved")
        return (await resolved.inner_text()).strip()

    async def wait_for(self, condition: WaitCondition) -> None:
        if condition.kind == WaitConditionKind.VISIBLE:
            if condition.target is None:
                raise ValueError("WaitCondition.VISIBLE requires target")
            locator = self._playwright_locator(condition.target.primary).first
            await locator.wait_for(state="visible", timeout=condition.timeout_ms)
        elif condition.kind == WaitConditionKind.TEXT_PRESENT:
            if condition.text is None:
                raise ValueError("WaitCondition.TEXT_PRESENT requires text")
            await self._page.get_by_text(condition.text).first.wait_for(
                state="visible", timeout=condition.timeout_ms
            )

    async def capture_evidence(self) -> Evidence:
        screenshot = await self._page.screenshot()
        text = await self._page.inner_text("body")
        dom_snapshot = await self._capture_dom_snapshot()
        return Evidence(
            url=self._page.url,
            screenshot_png=screenshot,
            visible_text_excerpt=text[:2000],
            dom_snapshot=dom_snapshot,
        )

    # Accessibility-tree snapshot (.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md,
    # "Rich Failure Evidence": "DOM snapshot or accessibility snapshot").
    # Deliberately thin -- one Playwright call, truncated, best-effort.
    # This must never be allowed to fail evidence capture as a whole: a
    # missing snapshot is a much smaller loss than losing the screenshot
    # and URL over an unrelated accessibility-tree quirk.
    _MAX_DOM_SNAPSHOT_CHARS = 20_000

    async def _capture_dom_snapshot(self) -> str | None:
        try:
            tree = await self._page.accessibility.snapshot()
        except Exception:  # noqa: BLE001 - best-effort, never fatal to evidence capture
            return None
        if tree is None:
            return None
        try:
            snapshot = json.dumps(tree)
        except (TypeError, ValueError):
            return None
        if len(snapshot) <= self._MAX_DOM_SNAPSHOT_CHARS:
            return snapshot
        return snapshot[: self._MAX_DOM_SNAPSHOT_CHARS] + "...<truncated>"


@asynccontextmanager
async def launch_playwright_surface(headless: bool = True) -> AsyncIterator[PlaywrightSurfaceAdapter]:
    """Own the browser/context/page lifecycle for one PlaywrightSurfaceAdapter.

    `headless` defaults to True (CI, and this sandbox's own verification
    runs). Phase 13's Docker automation image runs headed under Xvfb, with
    the resulting display exposed via noVNC for the human-handoff demo --
    that only changes how the caller sets `headless`/DISPLAY, not this
    adapter's code.
    """

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        try:
            context = await browser.new_context()
            page = await context.new_page()
            yield PlaywrightSurfaceAdapter(page)
        finally:
            await browser.close()
