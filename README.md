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

`docker-compose.yml` builds two services:

- `demo-app` -- the legacy credit-union admin UI (its own minimal image,
  no dependency on `automation`).
- `automation` -- the FastAPI orchestration API, Playwright/Chromium,
  and (Phase 13) Xvfb + x11vnc + noVNC, so a human operator can literally
  see and control the exact live browser session automation pauses on a
  handoff.

This is the canonical, single-command reproducible demo path:

```bash
docker compose build
docker compose up
```

Once both services report healthy:

- `http://localhost:8000` -- the automation API (`GET /health`,
  `POST /runs`, `GET/POST /interventions/*`).
- `http://localhost:8080` -- the demo legacy admin app directly (useful
  for sanity-checking the target system on its own).
- `http://localhost:6080/vnc.html?autoconnect=true&resize=scale` -- the
  noVNC web client. This shows the automation container's virtual X
  display, which is where its headed Chromium renders. There is only
  ever one browser session here: noVNC/x11vnc are a view/control
  transport on top of the same Playwright-driven Chromium window
  `RunOrchestrator`/`SessionRegistry` are already holding open during a
  paused run -- opening this page never starts a second browser.

Runtime configuration (`ANTHROPIC_API_KEY`, if you want to exercise a
real discovery run) comes from the repo-root `.env` file via Compose's
`env_file:`, injected into the `automation` container at `docker compose
up` time -- it is never copied into the image, so nothing sensitive ends
up baked into a built image layer. Everything under `./data` (artifacts,
capabilities, logs, evidence, discovery traces, interventions) is a bind
mount, so it persists across `docker compose up` / `down` cycles and is
inspectable directly on the host.

**Verification status: fully verified on the developer's real Mac,**
both the build/startup path and the noVNC same-session handoff itself.
`docker compose build` completed successfully, both services started,
`GET http://localhost:8000/health` returned 200, Xvfb/x11vnc/noVNC all
started correctly inside the `automation` container, and the Compose
network between `automation` and `demo-app` works. This repo's Docker
setup (Phase 13's Xvfb/x11vnc/noVNC additions included) is build- and
startup-verified, not just authored/reasoned about from a sandbox without
Docker CLI/socket access -- that sandbox limitation is still real (it's
why this verification could only happen on the developer's own machine,
not from the tool session that wrote the config), but it no longer means
"untested"; it was tested, on the actual target machine.

The noVNC same-session intervention/resume flow (steps 4-9 below) has
also now been manually run end to end, twice, against two different
escalation paths, both through the real Dockerized API:

- **Approval-required, then automation performs the approved action.**
  `close_member_account` on member `M1001` / account `A-5001` paused at
  the policy-gated `close_account` step
  (`run_id 92c86535dca14af0aa14f9e2d8f5942f`,
  `intervention_id cb2396bae1b74d6abab7b13095ba69cc`). The browser stayed
  open on the close-confirmation page, visible in noVNC; claim -> (the
  operator deliberately did *not* click the button themselves, since the
  resume mechanism re-executes the gated step) -> complete -> resume
  continued the *same* `run_id`, automation clicked "Confirm Close"
  itself, and the run finished `success` with
  `{"account_status": "Closed"}`.
- **Hard failure, human resolves the ambiguity via noVNC, automation
  re-validates.** `get_savings_balance` on `member_id="Smith"` (matches
  two seeded members) failed a checkpoint mid-replay
  (`run_id 720f35e5933645f3920ae893b3f1bb64`, `error_code
  CHECKPOINT_FAILED`, `intervention_id d40a68d8004d49b0a898b893f139a93f`)
  and paused with a real captured screenshot/DOM snapshot
  (`data/evidence/720f35e5933645f3920ae893b3f1bb64/`). The operator
  claimed it, used noVNC to drive the *same* live browser to the correct
  member (M1002) themselves -- a genuinely different resolution path than
  the approval case above, since here the human, not automation, resolves
  the ambiguous state -- then completed and resumed. The *same* `run_id`
  finished `success` with `savings_balance: 9900.00` (M1002's real seeded
  balance), read from wherever the human left the browser.

Both runs prove the same underlying guarantee from two different angles:
the original `run_id`/`session_id` are preserved end to end, the browser
session is never recreated, and `RunOrchestrator` correctly resumes
either by re-executing a specific approved step or (when no single step
is known-safe to retry, e.g. any `FAILED` outcome) by skipping straight to
output re-extraction against whatever state the operator left the page in.

### Manual verification checklist (Phase 13: live-session handoff via noVNC)

Run this after `docker compose up` on a machine with real Docker:

1. **Start Compose** and wait for both services to report healthy:
   ```bash
   docker compose up --build
   docker compose ps   # both services should show "healthy"
   ```
2. **Open the API health endpoint** -- `curl http://localhost:8000/health`
   (or a browser tab) should return `{"status": "ok", ...}`.
3. **Open noVNC** -- visit
   `http://localhost:6080/vnc.html?autoconnect=true&resize=scale` in a
   browser. You should see a plain desktop (no window manager) on the
   automation container's virtual display; no Chromium window yet since
   no run has started.
4. **Trigger a run that reaches intervention** -- call `POST
   http://localhost:8000/runs` with a `capability_id`/`discovery_goal`
   (or an existing artifact) known to hit an `APPROVAL_REQUIRED`/`FAILED`
   step against the demo app (e.g. an approval-required capability, or a
   discovery goal against `http://demo-app:8080/` that the model can't
   complete cleanly). The response's `outcome` should be
   `approval_required` or `failed`, with non-null `intervention_id` and
   `session_id`.
5. **Confirm the browser remains open** -- switch back to the noVNC tab
   (refresh if needed): a real Chromium window should now be visible,
   sitting on whatever page the automation attempt paused on. It should
   still be there with no further action -- nothing closes it just
   because the HTTP request finished.
6. **Claim the intervention** --
   `POST http://localhost:8000/interventions/{intervention_id}/claim`
   with `{"operator_id": "teller-1"}`. Response `status` should become
   `"claimed"`.
7. **Manipulate the same browser via noVNC** -- in the noVNC tab, click
   into the page and make whatever change resolves the paused step (e.g.
   dismiss a dialog, fix a field, click the button automation was
   stopped in front of). This is the same Chromium window/tab the whole
   time -- there is no second session to switch to.
8. **Request resume** -- first
   `POST http://localhost:8000/interventions/{intervention_id}/complete`
   with `{"operator_id": "teller-1"}` (response `status` becomes
   `"resolved"`), then
   `POST http://localhost:8000/interventions/{intervention_id}/resume`.
9. **Confirm automation continues in the same session/run** -- the
   `resume` response's `run_id` should match the original run's `run_id`
   from step 4, `outcome` should reflect what happened next
   (`success`/`business_outcome`, or another `approval_required`/`failed`
   with a fresh `intervention_id` if it paused again), and -- watching
   noVNC while the resume call is in flight -- the browser should
   visibly continue interacting with the page an operator was just
   looking at, not reload a fresh one.

If a run instead reaches a terminal outcome directly (`success`,
`business_outcome`, `blocked`) without ever pausing, switch back to noVNC
afterward and confirm the browser window is gone -- a terminal outcome
always closes its surface (see `RunOrchestrator._close_surface`).
