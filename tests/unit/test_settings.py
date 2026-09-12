"""`Settings.playwright_headless` (Phase 13): the one knob that lets the
automation Docker container run a headed Chromium under Xvfb (for
noVNC/x11vnc) while every other environment -- local dev, this very test
suite, CI -- keeps running headless with zero configuration. Nothing here
touches a real browser or Docker; it's a plain pydantic-settings field.
"""

from __future__ import annotations

from cuas.observability.config import Settings


def test_playwright_headless_defaults_to_true(monkeypatch) -> None:
    monkeypatch.delenv("PLAYWRIGHT_HEADLESS", raising=False)

    settings = Settings(_env_file=None)

    assert settings.playwright_headless is True


def test_playwright_headless_can_be_overridden_via_environment(monkeypatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "false")

    settings = Settings(_env_file=None)

    assert settings.playwright_headless is False


def test_playwright_headless_true_string_is_still_true(monkeypatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_HEADLESS", "true")

    settings = Settings(_env_file=None)

    assert settings.playwright_headless is True
