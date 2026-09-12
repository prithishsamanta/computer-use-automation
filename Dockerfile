# Automation service: FastAPI orchestration app.
#
# Phase 1: serves only the health route below, over uvicorn.
# Phase 3 adds Playwright + browsers. Phase 13 adds Xvfb/x11vnc/noVNC so
# the headed browser this container drives is viewable/controllable at
# http://localhost:6080 for the human-handoff demo (see .CLAUDE/04, and
# docker/automation-entrypoint.sh for exactly how that's wired up). None
# of this changes what the container runs -- it's still the same FastAPI
# app over uvicorn, launched by that entrypoint script instead of
# directly by CMD.
FROM python:3.11-slim AS base

WORKDIR /app

# xvfb: virtual X display so headed Chromium has somewhere to render with
#   no physical/GPU display in the container.
# x11vnc: exposes that X display over VNC (localhost-only by default; see
#   docker-compose.yml's port mapping and the entrypoint script's -nopw
#   note on why that's an accepted demo-scope trade-off).
# novnc + websockify: the web-based VNC client (novnc's static files) and
#   the websocket<->TCP bridge (websockify) that serves it, so an operator
#   only needs a browser tab, not a native VNC client.
RUN apt-get update && apt-get install -y --no-install-recommends \
        xvfb \
        x11vnc \
        novnc \
        websockify \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir -e .
RUN python -m playwright install --with-deps chromium

COPY docker/automation-entrypoint.sh /usr/local/bin/automation-entrypoint.sh
RUN chmod +x /usr/local/bin/automation-entrypoint.sh

# The display the entrypoint's Xvfb/x11vnc pair uses. Playwright reads
# $DISPLAY from its own process environment on Linux -- this is the only
# thing that makes headed Chromium (Settings.playwright_headless=false,
# set as a plain env var on the automation Compose service, not baked in
# here) render onto Xvfb instead of failing for lack of a display.
ENV DISPLAY=:99

# 8000: FastAPI. 6080: noVNC web UI (docker-compose.yml maps both to the
# host).
EXPOSE 8000 6080

ENTRYPOINT ["/usr/local/bin/automation-entrypoint.sh"]
