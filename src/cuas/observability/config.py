"""Application settings, read from the environment.

Kept intentionally small. DATABASE_URL (run/capability/intervention state)
is still to come. log_dir/evidence_dir (Phase 7) are the file-backed roots
for structured events (JsonlEventSink) and failure evidence
(FileEvidenceStore); discovery_trace_dir (Phase 8) is the file-backed root
for complete raw discovery traces (FileDiscoveryTraceStore); capability_dir
(Phase 10) is the file-backed root for registered CapabilityRecords
(FileCapabilityRepository) -- all plain directories on disk, no external
logging/tracing/evidence/database service.
anthropic_api_key is read from ANTHROPIC_API_KEY when present but is never
required for normal development or CI -- only AnthropicLLMClient
(discovery/anthropic_client.py) needs it, and only for an actual live run.
Nothing here is a secret that should ever be logged -- see
.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md on redaction discipline.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "cuas"
    environment: str = "dev"
    artifact_dir: str = "data/artifacts"
    capability_dir: str = "data/capabilities"
    log_dir: str = "data/logs"
    evidence_dir: str = "data/evidence"
    discovery_trace_dir: str = "data/discovery_traces"
    anthropic_api_key: str | None = None


def get_settings() -> Settings:
    return Settings()
