"""Non-Docker config sanity checks for Phase 13's Xvfb/x11vnc/noVNC
handoff path.

These CANNOT and do not claim to verify that the Docker build/runtime
actually works -- this sandbox has no Docker CLI/socket access (see
docker-compose.yml's own top-of-file note), so `docker compose build` /
`up` have never been run against any of this from here. What these tests
do instead is pin the plain-text contents of the Dockerfile, compose
file, and entrypoint script against the specific pieces Phase 13 depends
on, so an accidental future edit that silently drops the noVNC port, the
headed-mode env var, or a step in the entrypoint script fails a fast unit
test instead of only being discovered the next time someone actually runs
`docker compose up`.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    path = _REPO_ROOT / relative_path
    assert path.exists(), f"expected {relative_path!r} to exist at the repo root"
    return path.read_text()


def test_automation_dockerfile_installs_the_headed_browser_transport_stack() -> None:
    text = _read("Dockerfile")

    for package in ("xvfb", "x11vnc", "novnc", "websockify"):
        assert package in text, f"Dockerfile no longer installs {package!r}"
    assert "ENV DISPLAY=" in text
    assert "EXPOSE 8000 6080" in text
    assert "automation-entrypoint.sh" in text


def test_entrypoint_script_starts_the_full_display_vnc_novnc_chain_then_execs_uvicorn() -> None:
    text = _read("docker/automation-entrypoint.sh")

    assert "Xvfb" in text
    assert "x11vnc" in text
    assert "websockify" in text
    # Must be the LAST thing that happens, and via `exec` (so uvicorn
    # becomes the container's actual foreground/signal-receiving
    # process) -- not just present anywhere in the script.
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert lines[-1].startswith("exec uvicorn"), f"expected the script's last statement to exec uvicorn, got {lines[-1]!r}"


def test_docker_compose_exposes_novnc_and_runs_automation_headed() -> None:
    text = _read("docker-compose.yml")

    assert "6080:6080" in text
    assert "8000:8000" in text
    assert "PLAYWRIGHT_HEADLESS=false" in text


def test_docker_compose_persists_data_and_injects_env_at_runtime_not_build_time() -> None:
    text = _read("docker-compose.yml")

    assert "./data:/app/data" in text
    assert "env_file:" in text
    assert ".env" in text
    # The image build itself must never COPY .env -- runtime injection
    # only. (Secrets never baked into the image.)
    dockerfile_text = _read("Dockerfile")
    assert ".env" not in dockerfile_text


def test_docker_compose_orders_startup_with_a_health_gate() -> None:
    text = _read("docker-compose.yml")

    assert "healthcheck:" in text
    assert "condition: service_healthy" in text
