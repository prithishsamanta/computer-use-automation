"""Hand-authored sample artifacts used across test modules (and, from Phase
5 on, by the ReplayEngine tests too -- .CLAUDE/07_IMPLEMENTATION_GUIDANCE.md:
"The replay core should exist before allowing the discovery loop to
generate artifacts for it," which means replay needs something real to run
before discovery exists to produce it).

get_savings_balance() encodes the demo capability from
.CLAUDE/07_IMPLEMENTATION_GUIDANCE.md ("Demo Application"): search member
-> open member details -> read savings balance. Locators here are the same
ones proven against the real demo_app in the Phase 3 integration tests
(tests/integration/test_playwright_surface_adapter.py), not invented ones.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cuas.artifact import (
    Artifact,
    ArtifactApplication,
    ArtifactSafety,
    BusinessOutcome,
    InputSpec,
    OutputSpec,
    OutputType,
    Provenance,
    RecoverableCondition,
    RecoveryAction,
    Step,
    SuccessCondition,
    SuccessConditionType,
)
from cuas.domain import ActionType, Locator, LocatorStrategy, RiskLevel, Target
from cuas.surface.adapter import WaitCondition, WaitConditionKind


def _role(role: str, name: str | None = None) -> Locator:
    params = {"role": role}
    if name is not None:
        params["name"] = name
    return Locator(strategy=LocatorStrategy.ROLE_NAME, params=params)


def _css(selector: str, frame: str | None = None) -> Locator:
    return Locator(strategy=LocatorStrategy.CSS, params={"selector": selector}, frame=frame)


def get_savings_balance(version: str = "1.0.0") -> Artifact:
    dismiss_ok = Target(primary=_role("button", "OK"))

    return Artifact(
        capability_id="get_savings_balance",
        name="Get Savings Balance",
        description="Find a member by ID and return their current savings balance.",
        version=version,
        application=ArtifactApplication(
            vendor="meridian-demo",
            application="credit-union-admin",
            supported_versions=["1.x"],
        ),
        tenant_scope="base",
        inputs={
            "member_id": InputSpec(
                type="string", required=True, description="Institution member identifier"
            )
        },
        outputs={
            "savings_balance": OutputSpec(
                type=OutputType.DECIMAL,
                source=Target(
                    primary=_css("#acct-row-2 td:nth-child(3)", frame="#accounts-frame"),
                    fallbacks=[_css("#tbl1 tr:nth-child(3) td:nth-child(3)", frame="#accounts-frame")],
                ),
                required=True,
            )
        },
        steps=[
            Step(
                id="fill_member_id",
                action_type=ActionType.FILL,
                intent="enter_member_id",
                target=Target(primary=_role("textbox")),
                value="{{member_id}}",
                risk=RiskLevel.SAFE,
            ),
            Step(
                id="submit_search",
                action_type=ActionType.CLICK,
                intent="submit_member_search",
                target=Target(primary=_role("button", "Search")),
                risk=RiskLevel.SAFE,
                checkpoint=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Accounts"),
            ),
        ],
        success_condition=SuccessCondition(type=SuccessConditionType.OUTPUT_VALID, output="savings_balance"),
        business_outcomes=[
            BusinessOutcome(
                code="MEMBER_NOT_FOUND",
                detect=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Member not found."),
            )
        ],
        recoverable_conditions=[
            RecoverableCondition(
                code="SESSION_NOTICE_POPUP",
                detect=WaitCondition(kind=WaitConditionKind.VISIBLE, target=dismiss_ok),
                recovery=RecoveryAction.DISMISS,
                dismiss_target=dismiss_ok,
            )
        ],
        safety=ArtifactSafety(intent="view_savings_balance", risk=RiskLevel.SAFE),
        provenance=Provenance(created_from_run="manual-authoring-phase4", created_at=datetime.now(timezone.utc)),
    )
