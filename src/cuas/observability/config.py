"""Application settings, read from the environment.

Kept intentionally small. DATABASE_URL (run state) is still to come.
log_dir/evidence_dir (Phase 7) are the file-backed roots for structured
events (JsonlEventSink) and failure evidence (FileEvidenceStore);
discovery_trace_dir (Phase 8) is the file-backed root for complete raw
discovery traces (FileDiscoveryTraceStore); capability_dir (Phase 10) is
the file-backed root for registered CapabilityRecords
(FileCapabilityRepository); intervention_dir (Phase 12) is the file-backed
root for persisted InterventionRequests (FileInterventionRepository) --
all plain directories on disk, no external logging/tracing/evidence/
database service.
anthropic_api_key is read from ANTHROPIC_API_KEY when present but is never
required for normal development or CI -- only AnthropicLLMClient
(discovery/anthropic_client.py) needs it, and only for an actual live run.
Nothing here is a secret that should ever be logged -- see
.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md on redaction discipline.

playwright_headless (Phase 13) is the one knob that turns the same
`launch_playwright_surface` call headed or headless -- it does not
change which code path runs, only an argument Playwright itself
receives. Defaults to `True` (local dev, unit tests, CI: no display
needed). The automation Docker image's Compose service overrides this to
`false` via a plain (non-secret) environment variable, so the exact
Chromium session `RunOrchestrator` is driving renders on the container's
Xvfb display, where x11vnc/noVNC make it visible/controllable to a human
operator during a handoff -- see the Dockerfile and docker-compose.yml
for the rest of that wiring.
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
    intervention_dir: str = "data/interventions"
    anthropic_api_key: str | None = None
    playwright_headless: bool = True


def get_settings() -> Settings:
    return Settings()
