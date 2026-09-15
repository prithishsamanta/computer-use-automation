"""Unit tests for AnthropicLLMClient's prompt-building helpers that render a
failed discovery action's target into history (`_describe_target`, `_quote`)
and for `_build_user_message`'s decision about when to include that
rendering. Pure string/dict logic -- no network, no API key, so (like
test_anthropic_tool_schema.py) this runs in every default/CI invocation.

Real run 7f8840f08ec34eecaa25e78c696ba134 (DECISIONS_LOG.md) showed a
failed step's target/selector was never included in the history sent back
to the model at all: a stateless next turn had no way to know which
locator its own `error_message` was even about, so it proposed the exact
same selector again under a new intent. These tests lock in the fix at the
prompt-building layer (the sibling fix to the underlying-cause rendering
in playwright_adapter.py, covered by
test_playwright_adapter_error_sanitization.py).
"""

from __future__ import annotations

from cuas.discovery.anthropic_client import (
    _MAX_TARGET_DETAIL_CHARS,
    AnthropicLLMClient,
    _describe_target,
    _quote,
)
from cuas.discovery.models import DiscoveryGoal, DiscoveryHistoryEntry
from cuas.domain import Action, ActionType, Locator, LocatorStrategy, Target
from cuas.surface.adapter import Observation


def _goal() -> DiscoveryGoal:
    return DiscoveryGoal(
        capability_id="read_savings_account_balance",
        description="Find member M1001 and read their savings balance.",
        start_url="https://example.test/",
    )


def _observation() -> Observation:
    return Observation(url="https://example.test/members/M1001", visible_text="")


def _action(
    *,
    action_type: ActionType = ActionType.READ,
    intent: str = "read_savings_account_balance",
    target: Target | None = None,
    value: str | None = None,
) -> Action:
    return Action(id="discovery-0e94b335", action_type=action_type, intent=intent, target=target, value=value)


def _css_target(selector: str) -> Target:
    return Target(primary=Locator(strategy=LocatorStrategy.CSS, params={"selector": selector}))


# --- _quote -----------------------------------------------------------------


def test_quote_wraps_in_double_quotes() -> None:
    assert _quote("Savings Balance") == '"Savings Balance"'


def test_quote_swaps_embedded_double_quotes_for_single_quotes() -> None:
    assert _quote('td:first-child:contains("Savings")') == "\"td:first-child:contains('Savings')\""


# --- _describe_target ---------------------------------------------------------


def test_describe_target_for_css_selector() -> None:
    selector = "tr:has(td:first-child:contains('Savings')) td:nth-child(3)"
    target = _css_target(selector)
    assert _describe_target(target) == f'css selector="{selector}"'


def test_describe_target_for_role_name_with_name() -> None:
    target = Target(
        primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params={"role": "textbox", "name": "Member ID"})
    )
    assert _describe_target(target) == 'role_name role="textbox" name="Member ID"'


def test_describe_target_for_role_name_without_name() -> None:
    target = Target(primary=Locator(strategy=LocatorStrategy.ROLE_NAME, params={"role": "button"}))
    assert _describe_target(target) == 'role_name role="button"'


def test_describe_target_for_label() -> None:
    target = Target(primary=Locator(strategy=LocatorStrategy.LABEL, params={"label": "Savings Balance"}))
    assert _describe_target(target) == 'label label="Savings Balance"'


def test_describe_target_for_xpath() -> None:
    target = Target(primary=Locator(strategy=LocatorStrategy.XPATH, params={"xpath": "//tr[3]/td[2]"}))
    assert _describe_target(target) == 'xpath xpath="//tr[3]/td[2]"'


def test_describe_target_for_coordinates_never_leaks_raw_coordinates() -> None:
    target = Target(primary=Locator(strategy=LocatorStrategy.COORDINATES, params={"x": 100, "y": 200}))
    assert _describe_target(target) == "coordinates coordinates"


def test_describe_target_truncates_a_long_selector() -> None:
    target = _css_target("x" * 500)
    result = _describe_target(target)
    assert len(result) == _MAX_TARGET_DETAIL_CHARS + 3  # + "..."
    assert result.endswith("...")


# --- _build_user_message: when target detail is/isn't included --------------


def test_execution_failed_entry_includes_the_attempted_target() -> None:
    selector = "tr:has(td:first-child:contains('Savings')) td:nth-child(3)"
    entry = DiscoveryHistoryEntry(
        step_index=1,
        action=_action(target=_css_target(selector)),
        outcome="execution_failed",
        error_message=(
            "Could not resolve target for read: tried 1 candidate(s); "
            "last candidate (css) failed: Locator.wait_for: SyntaxError: Failed to execute "
            "'querySelectorAll' on 'Element': 'td:first-child:contains(\"Savings\")' is not a valid selector."
        ),
    )
    message = AnthropicLLMClient._build_user_message(None, _goal(), _observation(), [entry])

    expected_line = (
        "- step 1: proposed read (intent='read_savings_account_balance', "
        f'target=css selector="{selector}") -> execution_failed '
        "(Could not resolve target for read: tried 1 candidate(s); "
        "last candidate (css) failed: Locator.wait_for: SyntaxError: Failed to execute "
        "'querySelectorAll' on 'Element': 'td:first-child:contains(\"Savings\")' is not a valid selector.)"
    )
    assert expected_line in message


def test_non_execution_failed_entries_never_get_target_detail() -> None:
    target = _css_target("#balance")
    for outcome in ("executed", "policy_denied", "approval_required"):
        entry = DiscoveryHistoryEntry(step_index=1, action=_action(target=target), outcome=outcome)
        message = AnthropicLLMClient._build_user_message(None, _goal(), _observation(), [entry])
        assert "target=" not in message, f"outcome={outcome!r} unexpectedly included target detail"


def test_execution_failed_with_no_target_does_not_crash_or_include_target() -> None:
    entry = DiscoveryHistoryEntry(
        step_index=1,
        action=_action(action_type=ActionType.NAVIGATE, intent="load_start_page", target=None),
        outcome="execution_failed",
        error_message="navigation timed out",
    )
    message = AnthropicLLMClient._build_user_message(None, _goal(), _observation(), [entry])
    assert "target=" not in message
    assert "navigation timed out" in message


def test_action_value_is_never_included_even_for_execution_failed() -> None:
    """The new target rendering must never surface `Action.value` -- what a
    FILL action actually typed (e.g. a member id) -- only where it targeted."""

    entry = DiscoveryHistoryEntry(
        step_index=1,
        action=_action(
            action_type=ActionType.FILL,
            intent="search_member",
            target=_css_target("#member-id-input"),
            value="M1001-SUPER-SECRET-LOOKING-VALUE",
        ),
        outcome="execution_failed",
        error_message="fill failed",
    )
    message = AnthropicLLMClient._build_user_message(None, _goal(), _observation(), [entry])
    assert "M1001-SUPER-SECRET-LOOKING-VALUE" not in message
    assert "target=" in message
