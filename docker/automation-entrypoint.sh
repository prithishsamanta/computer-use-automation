#!/usr/bin/env bash
# Automation container entrypoint (Phase 13).
#
# Brings up a virtual X display, a VNC server pointed at it, and noVNC's
# websocket bridge on top of that -- so an authorized operator can open a
# browser tab to this container's noVNC port and see/control the EXACT
# Chromium window Playwright is already driving, before finally exec-ing
# uvicorn as the container's foreground process. Nothing here touches
# session ownership: SessionRegistry/InterventionRequest (Phase 12) are
# completely unaware this script exists. Xvfb/x11vnc/noVNC are a
# transport/view/control layer on top of one already-live browser
# session, not a second one -- see playwright_adapter.py's
# launch_playwright_surface docstring and DECISIONS_LOG.md's Phase 13
# entry for the full reasoning.
#
# Deliberately no process supervisor (supervisord/s6/runit/etc): this is
# a take-home demo container, not a production one. Xvfb/x11vnc/
# websockify are started once, in the background, and this script does
# not attempt to detect or restart a crashed one -- only `docker compose
# restart automation` (or a container-level healthcheck failure driving a
# restart policy) recovers from that. Accepted, documented simplification
# for this phase's scope.
set -euo pipefail

DISPLAY_NUM="${DISPLAY#:}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
export DISPLAY=":${DISPLAY_NUM}"
SCREEN_GEOMETRY="${SCREEN_GEOMETRY:-1280x800x24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"

echo "[entrypoint] starting Xvfb on ${DISPLAY} (${SCREEN_GEOMETRY})"
Xvfb "${DISPLAY}" -screen 0 "${SCREEN_GEOMETRY}" -nolisten tcp &

echo "[entrypoint] waiting for the X socket to come up"
for _ in $(seq 1 50); do
    if [ -e "/tmp/.X11-unix/X${DISPLAY_NUM}" ]; then
        break
    fi
    sleep 0.1
done

echo "[entrypoint] starting x11vnc on port ${VNC_PORT} (display ${DISPLAY})"
# -nopw: no VNC password. A deliberate, explicitly-documented demo-scope
# trade-off -- see README.md's Docker section and DECISIONS_LOG.md's
# Phase 13 entry. The reference docker-compose.yml only publishes
# 5900/6080 to the host's own loopback-reachable port mapping; do not
# expose either port on a non-localhost interface without adding real
# VNC/noVNC authentication first.
x11vnc -display "${DISPLAY}" -forever -shared -rfbport "${VNC_PORT}" -nopw -quiet &

echo "[entrypoint] starting noVNC (websockify) on port ${NOVNC_PORT} -> 127.0.0.1:${VNC_PORT}"
websockify --web=/usr/share/novnc/ "${NOVNC_PORT}" "127.0.0.1:${VNC_PORT}" &

echo "[entrypoint] starting FastAPI (uvicorn)"
exec uvicorn cuas.api.main:app --host 0.0.0.0 --port 8000
