"""Thin smoke test for the /runs HTTP surface (Phase 11) -- proves the
composition root in api/main.py (Settings -> real repositories ->
RunOrchestrator -> FastAPI routes) actually wires together and a request
round-trips through real Pydantic validation on both sides, without
touching Playwright or a real LLM.

Deliberately exercises only the one branch that is always safe to hit in
a unit test regardless of what's on disk: a capability_id no
FileCapabilityRepository could possibly have a record for produces
NO_CAPABILITY_MATCH -> DISCOVERY_REQUIRED before RunOrchestrator ever
calls its surface_factory (see orchestrator.py's run_capability), so this
never launches a browser. Every other branch (replay, discovery) is
covered against RunOrchestrator directly in test_run_orchestrator.py,
where fakes stand in for the surface/LLM -- re-proving that logic through
HTTP here would just be slower, not more thorough.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from cuas.api.main import app

_NONEXISTENT_CAPABILITY = "test_api_definitely_nonexistent_capability_9f3c"


def test_start_run_with_unknown_capability_reports_discovery_required() -> None:
    client = TestClient(app)

    response = client.post(
        "/runs",
        json={
            "capability_id": _NONEXISTENT_CAPABILITY,
            "vendor": "no-such-vendor",
            "application": "no-such-app",
            "version": "1.0.0",
            "tenant_id": "cu-test",
            "inputs": {},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "discovery_required"
    assert body["capability_id"] == _NONEXISTENT_CAPABILITY
    assert body["run_id"]


def test_get_run_retrieves_a_previously_started_run() -> None:
    client = TestClient(app)
    start = client.post(
        "/runs",
        json={
            "capability_id": _NONEXISTENT_CAPABILITY,
            "vendor": "no-such-vendor",
            "application": "no-such-app",
            "version": "1.0.0",
            "tenant_id": "cu-test",
            "inputs": {},
        },
    )
    run_id = start.json()["run_id"]

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    assert response.json()["run_id"] == run_id
    assert response.json()["outcome"] == "discovery_required"


def test_get_run_for_unknown_run_id_is_404() -> None:
    client = TestClient(app)

    response = client.get("/runs/no-such-run-id-at-all")

    assert response.status_code == 404
