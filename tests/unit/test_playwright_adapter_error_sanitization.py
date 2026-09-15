"""Unit tests for PlaywrightSurfaceAdapter._sanitize_underlying_error -- a
pure function needing no browser, so it runs in the default/CI suite.
The end-to-end proof that a real, invalid selector's real Playwright
error survives into TargetNotFoundError's own message lives in
tests/integration/test_playwright_surface_adapter.py
(test_target_not_found_message_includes_sanitized_underlying_cause),
since that needs a real browser to produce a real underlying exception.

Real run 7f8840f08ec34eecaa25e78c696ba134 (DECISIONS_LOG.md) showed this
underlying cause was previously captured as `last_error` and chained
(`raise TargetNotFoundError(...) from last_error`) but never included in
the exception's own message -- and discovery only ever sees `str(exc)`,
so the chained cause was silently discarded before it ever reached the
LLM.
"""

from __future__ import annotations

from cuas.surface.playwright_adapter import _MAX_UNDERLYING_ERROR_CHARS, _sanitize_underlying_error


def test_takes_only_the_first_line() -> None:
    """Playwright's own Error.__str__() is typically one useful line
    followed by a JS stack trace and/or a multi-line "Call log:" block --
    neither of the latter is root cause, and both can be unbounded."""

    exc = Exception(
        "Locator.wait_for: SyntaxError: ... is not a valid selector.\n"
        "    at query (<anonymous>:5120:41)\n"
        "Call log:\n"
        "  - waiting for locator(\"...\").first to be visible\n"
    )
    assert _sanitize_underlying_error(exc) == "Locator.wait_for: SyntaxError: ... is not a valid selector."


def test_truncates_a_long_first_line() -> None:
    exc = Exception("x" * 500)
    result = _sanitize_underlying_error(exc)
    assert len(result) == _MAX_UNDERLYING_ERROR_CHARS + 3  # + "..."
    assert result.endswith("...")


def test_empty_message_gets_a_deterministic_fallback() -> None:
    assert _sanitize_underlying_error(Exception("")) == "no further detail available"


def test_strips_surrounding_whitespace() -> None:
    exc = Exception("   Timeout 1500ms exceeded.   \nCall log:\n  - waiting\n")
    assert _sanitize_underlying_error(exc) == "Timeout 1500ms exceeded."
