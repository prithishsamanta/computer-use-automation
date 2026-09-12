"""Application settings, read from the environment.

Kept intentionally small. Later phases add ANTHROPIC_API_KEY (discovery)
and DATABASE_URL (run/capability/intervention state). log_dir/evidence_dir
(Phase 7) are the file-backed roots for structured events
(JsonlEventSink) and failure evidence (FileEvidenceStore) respectively --
both plain directories on disk, no external logging/tracing/evidence
service. Nothing here is a secret that should ever be logged -- see
.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md on redaction discipline.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "cuas"
    environment: str = "dev"
    artifact_dir: str = "data/artifacts"
    log_dir: str = "data/logs"
    evidence_dir: str = "data/evidence"


def get_settings() -> Settings:
    return Settings()
