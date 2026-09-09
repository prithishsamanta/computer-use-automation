"""In-memory seed data for the demo legacy credit-union admin app.

Deliberately not a real database: this app exists only to give discovery
and replay something real to operate against (.CLAUDE/07_IMPLEMENTATION_GUIDANCE.md,
"Demo Application"). State is process-local and mutated in place by the
close-account flow, so tests call reset_state() to get a clean seed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Account:
    id: str
    type: str  # "checking" | "savings"
    balance: Decimal
    status: str = "open"  # "open" | "closed"


@dataclass
class Member:
    id: str
    first_name: str
    last_name: str
    accounts: list[Account] = field(default_factory=list)


def _seed() -> dict[str, Member]:
    return {
        "M1001": Member(
            id="M1001",
            first_name="Jane",
            last_name="Doe",
            accounts=[
                Account(id="A-5001", type="checking", balance=Decimal("2340.10")),
                Account(id="A-5002", type="savings", balance=Decimal("18204.55")),
            ],
        ),
        "M1002": Member(
            id="M1002",
            first_name="John",
            last_name="Smith",
            accounts=[
                Account(id="A-6001", type="checking", balance=Decimal("512.00")),
                Account(id="A-6002", type="savings", balance=Decimal("9900.00")),
            ],
        ),
        "M1003": Member(
            id="M1003",
            first_name="Alice",
            last_name="Nguyen",
            accounts=[
                Account(id="A-7001", type="checking", balance=Decimal("0.00")),
                Account(id="A-7002", type="savings", balance=Decimal("305.42")),
            ],
        ),
        "M1004": Member(
            id="M1004",
            first_name="Mark",
            last_name="Smith",
            accounts=[
                Account(id="A-8001", type="checking", balance=Decimal("77.20")),
                Account(id="A-8002", type="savings", balance=Decimal("1500.00")),
            ],
        ),
    }


MEMBERS: dict[str, Member] = _seed()


def reset_state() -> None:
    """Restore the original seed data. Used by tests between cases."""

    global MEMBERS
    MEMBERS = _seed()


def find_by_id(member_id: str) -> Member | None:
    return MEMBERS.get(member_id.strip().upper())


def find_by_last_name(last_name: str) -> list[Member]:
    needle = last_name.strip().lower()
    if not needle:
        return []
    return [m for m in MEMBERS.values() if needle in m.last_name.lower()]


def find_account(member: Member, account_id: str) -> Account | None:
    return next((a for a in member.accounts if a.id == account_id), None)
