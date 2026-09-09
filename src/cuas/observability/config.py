"""Application settings, read from the environment.

Kept intentionally small in Phase 1: only what the FastAPI skeleton needs.
Later phases add ANTHROPIC_API_KEY (discovery), DATABASE_URL (run/capability/
intervention state), ARTIFACT_DIR / EVIDENCE_DIR (storage), and
DEMO_APP_BASE_URL (surface). Nothing here is a secret that should ever be
logged -- see .CLAUDE/06_ERRORS_AND_OBSERVABILITY.md on redaction discipline.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "cuas"
    environment: str = "dev"
    artifact_dir: str = "data/artifacts"


def get_settings() -> Settings:
    return Settings()
