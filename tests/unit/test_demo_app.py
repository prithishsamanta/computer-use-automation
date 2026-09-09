"""Fast, no-browser tests for the demo legacy app's own behavior: business
outcome rendering, the frame/accounts flow, and the two-step close-account
action. These don't touch Playwright or cuas -- Phase 3 adds the
PlaywrightSurfaceAdapter tests that drive this same app through a real
browser (tests/integration).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from demo_app.app import app
from demo_app.data import reset_state


@pytest.fixture(autouse=True)
def _reset_demo_data():
    reset_state()
    yield
    reset_state()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_search_by_exact_member_id_redirects_to_detail(client: TestClient) -> None:
    response = client.get("/search", params={"query": "M1001"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/members/M1001"


def test_search_unknown_member_is_a_business_outcome_not_an_error(client: TestClient) -> None:
    response = client.get("/search", params={"query": "does-not-exist"})
    assert response.status_code == 200
    assert "Member not found." in response.text


def test_search_by_last_name_with_multiple_matches_lists_results(client: TestClient) -> None:
    response = client.get("/search", params={"query": "Smith"})
    assert response.status_code == 200
    assert "M1002" in response.text
    assert "M1004" in response.text


def test_member_detail_embeds_accounts_in_an_iframe(client: TestClient) -> None:
    response = client.get("/members/M1001")
    assert response.status_code == 200
    assert 'src="/members/M1001/accounts"' in response.text


def test_accounts_page_renders_savings_balance(client: TestClient) -> None:
    response = client.get("/members/M1001/accounts")
    assert response.status_code == 200
    assert "18204.55" in response.text
    assert "Savings" in response.text


def test_close_account_requires_confirm_step_before_state_changes(client: TestClient) -> None:
    confirm = client.get("/members/M1001/accounts/A-5001/close-confirm")
    assert confirm.status_code == 200
    assert "Are you sure" in confirm.text

    # Visiting the confirm page alone must not have closed the account.
    still_open = client.get("/members/M1001/accounts")
    assert "Closed" not in still_open.text

    post_response = client.post("/members/M1001/accounts/A-5001/close", follow_redirects=False)
    assert post_response.status_code == 303

    after = client.get("/members/M1001/accounts")
    assert "Closed" in after.text
