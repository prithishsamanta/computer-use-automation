"""Thin smoke tests for the /interventions HTTP surface (Phase 12) --
proves the composition root wires FileInterventionRepository and
RunOrchestrator's new claim/complete/resume methods into real routes.

Deliberately restricted to paths that don't need a real intervention to
exist yet (an empty queue, and 404s for an unknown id): creating a real
one means actually escalating a run, which needs either Playwright (a
real capability run through the API) or an LLM -- neither available in a
unit test. test_intervention_lifecycle.py already covers the full
claim -> complete -> resume lifecycle directly against RunOrchestrator
with fakes; this file only proves the HTTP plumbing on top of it doesn't
have a wiring bug, the same scope test_api_runs.py chose for /runs.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from cuas.api.main import app

_NONEXISTENT_INTERVENTION = "test_api_definitely_nonexistent_intervention_9f3c"


def test_list_interventions_defaults_to_the_pending_queue() -> None:
    client = TestClient(app)

    response = client.get("/interventions")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_unknown_intervention_is_404() -> None:
    client = TestClient(app)

    response = client.get(f"/interventions/{_NONEXISTENT_INTERVENTION}")

    assert response.status_code == 404


def test_claim_unknown_intervention_is_404() -> None:
    client = TestClient(app)

    response = client.post(
        f"/interventions/{_NONEXISTENT_INTERVENTION}/claim", json={"operator_id": "teller-1"}
    )

    assert response.status_code == 404


def test_complete_unknown_intervention_is_404() -> None:
    client = TestClient(app)

    response = client.post(
        f"/interventions/{_NONEXISTENT_INTERVENTION}/complete", json={"operator_id": "teller-1"}
    )

    assert response.status_code == 404


def test_resume_unknown_intervention_is_404() -> None:
    client = TestClient(app)

    response = client.post(f"/interventions/{_NONEXISTENT_INTERVENTION}/resume")

    assert response.status_code == 404
