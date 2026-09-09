"""The demo legacy credit-union admin UI that discovery/replay operate
against (.CLAUDE/07_IMPLEMENTATION_GUIDANCE.md, "Demo Application").

Deliberately legacy-flavored on purpose, not by accident:
  - member accounts render inside an <iframe> (structural/frame quirk)
  - the accounts table uses generic ids/classes, not full label associations
    (weak semantics -> the target resolver has to fall back past role/label)
  - the accounts page sleeps briefly before rendering (a recoverable "slow
    page" condition -- replay must condition-wait, not sleep(5))
  - a dismissible session notice overlays the search page on first visit
    (a known-safe popup automation is allowed to dismiss)
  - "Member not found" is a real, renderable business outcome, not an error
  - closing an account is two-step (confirm page -> POST) so the actual
    irreversible action is a single, distinct, policy-gate-able step
    (.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md, close_account example)
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from demo_app.data import find_account, find_by_id, find_by_last_name

app = FastAPI(title="Meridian Credit Union - Member Services (demo)")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Simulated latency for the accounts panel, so replay must wait on a
# condition (element visible) instead of assuming instant rendering.
ACCOUNTS_LOAD_DELAY_SECONDS = 0.8


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"query": "", "results": None, "not_found": False})


@app.get("/search")
def search(request: Request, query: str = ""):
    query = query.strip()
    if not query:
        return templates.TemplateResponse(request, "index.html", {"query": "", "results": None, "not_found": False})

    exact = find_by_id(query)
    if exact is not None:
        return RedirectResponse(url=f"/members/{exact.id}", status_code=303)

    matches = find_by_last_name(query)
    if len(matches) == 1:
        return RedirectResponse(url=f"/members/{matches[0].id}", status_code=303)
    if len(matches) > 1:
        return templates.TemplateResponse(
            request, "index.html", {"query": query, "results": matches, "not_found": False}
        )

    return templates.TemplateResponse(request, "index.html", {"query": query, "results": None, "not_found": True})


@app.get("/members/{member_id}")
def member_detail(request: Request, member_id: str):
    member = find_by_id(member_id)
    if member is None:
        return templates.TemplateResponse(
            request, "index.html", {"query": member_id, "results": None, "not_found": True}, status_code=404
        )
    return templates.TemplateResponse(request, "member.html", {"member": member})


@app.get("/members/{member_id}/accounts")
def member_accounts(request: Request, member_id: str):
    member = find_by_id(member_id)
    if member is None:
        return templates.TemplateResponse(request, "accounts.html", {"member": None}, status_code=404)
    time.sleep(ACCOUNTS_LOAD_DELAY_SECONDS)
    return templates.TemplateResponse(request, "accounts.html", {"member": member})


@app.get("/members/{member_id}/accounts/{account_id}/close-confirm")
def close_confirm(request: Request, member_id: str, account_id: str):
    member = find_by_id(member_id)
    account = find_account(member, account_id) if member else None
    return templates.TemplateResponse(
        request, "close_confirm.html", {"member": member, "account": account}
    )


@app.post("/members/{member_id}/accounts/{account_id}/close")
def close_account(member_id: str, account_id: str):
    member = find_by_id(member_id)
    account = find_account(member, account_id) if member else None
    if account is not None and account.status == "open":
        account.status = "closed"
    return RedirectResponse(url=f"/members/{member_id}/accounts", status_code=303)
