# Computer-Use Automation System

interface.ai take-home: a backend integration layer that lets an LLM
discover how to operate a legacy banking/credit-union back-office UI, turns
a successful discovery run into a typed reusable artifact, and replays that
artifact deterministically (no LLM in the loop) with safety enforcement and
asynchronous human handoff.

The full design context this implementation follows lives in `.CLAUDE/`.

## Status

Under active development. This README will gain exact setup and demo
commands (including `docker compose up`) as the vertical slice comes
together; see `REPORT.md` (once written) for the architecture writeup.

## Local development (without Docker)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q -m "not live_llm"
uvicorn cuas.api.main:app --reload
```

`GET /health` should return `{"status": "ok", ...}`.
