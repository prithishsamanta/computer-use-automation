"""Thin FastAPI entry point.

Phase 1: a health route only, to prove the skeleton is wired end to end.
Phase 11 adds the real /runs and intervention endpoints once
RunOrchestrator exists.
"""

from __future__ import annotations

from fastapi import FastAPI

from cuas.observability.config import get_settings

app = FastAPI(title="Computer-Use Automation System")


@app.get("/health")
def health() -> dict[str, str]:
    settings = get_settings()
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}
