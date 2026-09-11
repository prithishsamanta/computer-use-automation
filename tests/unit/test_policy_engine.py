"""LayeredPolicyEngine: global -> vendor/application -> tenant policy
layering (.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md, "Layered Policy Model").

No surface, no artifact, no LLM here -- just Action + AppContext in,
PolicyDecision out. ReplayEngine's own dependency on the PolicyEngine
abstraction (not a concrete class) is covered separately in
tests/unit/test_replay_engine.py, which runs the real get_savings_balance
artifact through a LayeredPolicyEngine to prove it's a drop-in replacement
for RiskBasedPolicyEngine.
"""

from __future__ import annotations

import pytest

from cuas.domain import Action, ActionType, AppContext, RiskLevel
from cuas.safety import IntentPolicy, LayeredPolicyEngine, PolicyDecision

CONTEXT = AppContext(vendor="meridian-demo", application="credit-union-admin", version="1.0.0", tenant_id="base")


def _action(intent: str, risk: RiskLevel | None = None) -> Action:
    kwargs = {"id": "step", "action_type": ActionType.CLICK, "intent": intent}
    if risk is not None:
        kwargs["risk"] = risk
    return Action(**kwargs)


# -- global layer ------------------------------------------------------


def test_global_layer_allows_a_known_safe_intent() -> None:
    engine = LayeredPolicyEngine()
    decision = engine.evaluate(_action("search_member"), CONTEXT)
    assert decision == PolicyDecision.ALLOW


def test_global_layer_requires_approval_for_a_known_risky_intent_even_if_declared_safe() -> None:
    """The global classification of `close_account` must win over an
    author's (or an LLM's) own optimistic risk=SAFE claim -- intent-based
    classification overrides a self-reported risk, never the reverse."""

    engine = LayeredPolicyEngine()
    decision = engine.evaluate(_action("close_account", risk=RiskLevel.SAFE), CONTEXT)
    assert decision == PolicyDecision.REQUIRE_APPROVAL


def test_global_layer_blocks_a_known_prohibited_intent_even_if_declared_safe() -> None:
    engine = LayeredPolicyEngine()
    decision = engine.evaluate(_action("expose_credentials", risk=RiskLevel.SAFE), CONTEXT)
    assert decision == PolicyDecision.DENY


# -- app/vendor layer ----------------------------------------------------


def test_app_layer_can_tighten_an_intent_the_global_layer_has_no_opinion_on() -> None:
    app_policies = {
        ("meridian-demo", "credit-union-admin"): IntentPolicy({"export_report": PolicyDecision.REQUIRE_APPROVAL}),
    }
    engine = LayeredPolicyEngine(app_policies=app_policies)

    # Author claims SAFE; the app layer disagrees and wins (most restrictive).
    decision = engine.evaluate(_action("export_report", risk=RiskLevel.SAFE), CONTEXT)
    assert decision == PolicyDecision.REQUIRE_APPROVAL


def test_app_layer_is_scoped_to_its_own_vendor_and_application() -> None:
    app_policies = {
        ("other-vendor", "other-app"): IntentPolicy({"export_report": PolicyDecision.DENY}),
    }
    engine = LayeredPolicyEngine(app_policies=app_policies)

    # CONTEXT is meridian-demo/credit-union-admin -- the other vendor's app
    # policy must not leak across into it. No layer recognizes the intent
    # and risk was declared SAFE, so SAFE (the only opinion) stands.
    decision = engine.evaluate(_action("export_report", risk=RiskLevel.SAFE), CONTEXT)
    assert decision == PolicyDecision.ALLOW


# -- tenant layer ---------------------------------------------------------


def test_tenant_layer_can_tighten_beyond_the_app_layer() -> None:
    app_policies = {
        ("meridian-demo", "credit-union-admin"): IntentPolicy({"submit_transfer": PolicyDecision.REQUIRE_APPROVAL}),
    }
    tenant_policies = {
        ("meridian-demo", "credit-union-admin", "strict-cu"): IntentPolicy({"submit_transfer": PolicyDecision.DENY}),
    }
    engine = LayeredPolicyEngine(app_policies=app_policies, tenant_policies=tenant_policies)

    strict_context = CONTEXT.model_copy(update={"tenant_id": "strict-cu"})
    assert engine.evaluate(_action("submit_transfer"), strict_context) == PolicyDecision.DENY

    # A tenant with no override falls back to the app layer's decision.
    other_context = CONTEXT.model_copy(update={"tenant_id": "base"})
    assert engine.evaluate(_action("submit_transfer"), other_context) == PolicyDecision.REQUIRE_APPROVAL


def test_tenant_layer_cannot_loosen_a_broader_blocked_decision() -> None:
    """A tenant trying to declare a globally-prohibited intent ALLOW must
    not succeed -- layering can only add restriction, never remove it."""

    tenant_policies = {
        ("meridian-demo", "credit-union-admin", "lenient-cu"): IntentPolicy(
            {"expose_credentials": PolicyDecision.ALLOW}
        ),
    }
    engine = LayeredPolicyEngine(tenant_policies=tenant_policies)

    lenient_context = CONTEXT.model_copy(update={"tenant_id": "lenient-cu"})
    decision = engine.evaluate(_action("expose_credentials"), lenient_context)
    assert decision == PolicyDecision.DENY


def test_tenant_layer_cannot_loosen_a_broader_approval_required_decision() -> None:
    tenant_policies = {
        ("meridian-demo", "credit-union-admin", "lenient-cu"): IntentPolicy({"close_account": PolicyDecision.ALLOW}),
    }
    engine = LayeredPolicyEngine(tenant_policies=tenant_policies)

    lenient_context = CONTEXT.model_copy(update={"tenant_id": "lenient-cu"})
    decision = engine.evaluate(_action("close_account"), lenient_context)
    assert decision == PolicyDecision.REQUIRE_APPROVAL


# -- unknown / unclassified actions ---------------------------------------


def test_unclassified_intent_with_an_explicit_risk_is_trusted() -> None:
    """Nobody's intent table recognizes this, but a human (or a discovery
    proposal) explicitly declared a risk -- that explicit declaration is
    the only opinion available, so it stands."""

    engine = LayeredPolicyEngine()
    assert engine.evaluate(_action("totally_novel_intent", risk=RiskLevel.SAFE), CONTEXT) == PolicyDecision.ALLOW
    assert engine.evaluate(_action("totally_novel_intent", risk=RiskLevel.BLOCKED), CONTEXT) == PolicyDecision.DENY


def test_unclassified_intent_without_an_explicit_risk_fails_closed() -> None:
    """The trap this whole engine exists to close: Action.risk defaults to
    RiskLevel.SAFE when nobody sets it. An intent no layer recognizes,
    combined with a `risk` that was never actually declared, must not
    silently resolve to ALLOW just because that's the field's default
    value -- it must escalate instead."""

    action = _action("totally_novel_intent")  # risk omitted entirely
    assert "risk" not in action.model_fields_set
    assert action.risk == RiskLevel.SAFE  # confirms the trap is real, not hypothetical

    engine = LayeredPolicyEngine()
    decision = engine.evaluate(action, CONTEXT)
    assert decision == PolicyDecision.REQUIRE_APPROVAL


@pytest.mark.parametrize(
    "intent,risk,expected",
    [
        ("search_member", None, PolicyDecision.ALLOW),
        ("close_account", None, PolicyDecision.REQUIRE_APPROVAL),
        ("expose_credentials", None, PolicyDecision.DENY),
    ],
)
def test_global_defaults_hold_with_no_app_or_tenant_policy_configured(
    intent: str, risk: RiskLevel | None, expected: PolicyDecision
) -> None:
    engine = LayeredPolicyEngine()
    assert engine.evaluate(_action(intent, risk=risk), CONTEXT) == expected
