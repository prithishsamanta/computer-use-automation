# Computer-Use Automation System

interface.ai take-home: a backend integration layer that lets an LLM
discover how to operate a legacy banking/credit-union back-office UI, turns
a successful discovery run into a typed reusable artifact, and replays that
artifact deterministically (no LLM in the loop) with safety enforcement and
asynchronous human handoff.

The full design context this implementation follows lives in `.CLAUDE/`.

## Status

Under active development. See `REPORT.md` (once written) for the
architecture writeup.

## Local development (without Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium   # needed once, for the integration tests / real browser automation
python -m pytest -q -m "not live_llm"
```

Run the automation API (health check only so far):

```bash
uvicorn cuas.api.main:app --reload
```

Run the demo legacy credit-union admin app on its own port:

```bash
uvicorn demo_app.app:app --reload --port 8080
```

Then visit `http://localhost:8080/` and search for `M1001`, `M1002`,
`M1003`, or `M1004` (or a last name like `Smith`, which has two matches).

## Docker / Docker Compose

`docker-compose.yml` builds two services: `demo-app` (the legacy UI) and
`automation` (the orchestration API; still just a health route at this
point in the build). This is intended as the canonical, single-command
reproducible demo path once the vertical slice is complete:

```bash
docker compose build
docker compose up
```

**Verification status:** the Dockerfile/Compose config in this repo is
authored from an environment without Docker CLI/socket access, so it has
not been build-verified from there. Docker is installed and working on the
developer's own machine, and `docker compose build` / `up` are run and
verified from that real terminal, not from the sandboxed tool session.
Treat any given commit's Docker setup as unverified-by-build until that
check has actually been run.
