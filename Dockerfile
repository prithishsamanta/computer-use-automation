# Automation service: FastAPI orchestration app.
#
# Phase 1: serves only the health route below, over uvicorn.
# Phase 3 adds Playwright + browsers; Phase 13 adds Xvfb/x11vnc/noVNC so the
# headed browser this container drives is viewable/controllable at
# localhost:6080 for the human handoff demo (see .CLAUDE/04, and the plan
# discussed with the user for how Docker Compose stays the single
# reproducible demo path).
FROM python:3.11-slim AS base

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir -e .
RUN python -m playwright install --with-deps chromium

EXPOSE 8000

CMD ["uvicorn", "cuas.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
