"""Deterministic policy enforcement (.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md,
"Policy Enforcement" / "Layered Policy Model"). No LLM anywhere in this
module: an LLM may *propose* an action and even attach its own guess at
`Action.risk`, but this is the seam that actually decides SAFE /
APPROVAL_REQUIRED / BLOCKED, and it is the same seam ReplayEngine already
calls before executing a step and that discovery will call before
executing a proposed LLM action -- one PolicyEngine, two callers.

Two implementations:

- RiskBasedPolicyEngine: the Phase 5 placeholder. A pure 1:1
  RiskLevel -> PolicyDecision mapping. Kept (not replaced) because it's
  still useful as the simplest possible PolicyEngine for tests, and
  because LayeredPolicyEngine's own risk-fallback re-uses its mapping.
- LayeredPolicyEngine: the real Phase 6 answer. Global safety defaults ->
  vendor/application policy -> tenant-specific overrides, exactly as
  .CLAUDE/04 describes, combined by always taking the single most
  restrictive opinion among every layer (and the action's own explicitly
  declared risk) that has one. See its docstring for why "most
  restrictive wins" is the whole combination rule -- no priority table,
  no rule engine, just one comparison.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from cuas.domain import Action, AppContext, RiskLevel


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


# Severity order for combining multiple layers' opinions: higher wins.
# Deliberately a plain dict + max(), not a rule engine -- there are only
# three decisions and "more restrictive wins" is the entire policy.
_SEVERITY: dict[PolicyDecision, int] = {
    PolicyDecision.ALLOW: 0,
    PolicyDecision.REQUIRE_APPROVAL: 1,
    PolicyDecision.DENY: 2,
}

# The one place RiskLevel (declared on an Action/Step) is translated into
# an enforcement PolicyDecision. RiskBasedPolicyEngine uses this as its
# entire policy; LayeredPolicyEngine uses it only as a fallback signal
# (see LayeredPolicyEngine.evaluate).
RISK_TO_DECISION: dict[RiskLevel, PolicyDecision] = {
    RiskLevel.SAFE: PolicyDecision.ALLOW,
    RiskLevel.APPROVAL_REQUIRED: PolicyDecision.REQUIRE_APPROVAL,
    RiskLevel.BLOCKED: PolicyDecision.DENY,
}


class PolicyEngine(ABC):
    @abstractmethod
    def evaluate(self, action: Action, context: AppContext) -> PolicyDecision: ...


class RiskBasedPolicyEngine(PolicyEngine):
    """The simplest possible PolicyEngine: trusts `action.risk` outright.
    Deliberately still here as a Phase 5 artifact / test fixture baseline,
    not the runtime policy for real replay/discovery going forward -- see
    LayeredPolicyEngine."""

    def evaluate(self, action: Action, context: AppContext) -> PolicyDecision:
        return RISK_TO_DECISION[action.risk]


class IntentPolicy:
    """One layer's curated intent -> decision table.

    A layer expresses an opinion only for the intents it explicitly lists.
    For anything else it returns None ("no opinion, ask the next layer")
    rather than inventing a default -- a layer that doesn't recognize an
    intent must never be the reason that intent gets treated as safe.
    """

    def __init__(self, rules: dict[str, PolicyDecision] | None = None) -> None:
        self._rules = dict(rules or {})

    def decision_for(self, intent: str) -> PolicyDecision | None:
        return self._rules.get(intent)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"IntentPolicy({self._rules!r})"


# .CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md, "Action Categories": the
# doc's own SAFE / APPROVAL_REQUIRED / BLOCKED examples, translated into
# normalized intents. This is the *global* layer -- safety defaults that
# hold for every vendor/application/tenant unless a more specific layer
# tightens them further (never loosens; see LayeredPolicyEngine).
DEFAULT_GLOBAL_INTENT_POLICY: dict[str, PolicyDecision] = {
    # SAFE
    "search_member": PolicyDecision.ALLOW,
    "view_account": PolicyDecision.ALLOW,
    "open_member_record": PolicyDecision.ALLOW,
    "dismiss_known_popup": PolicyDecision.ALLOW,
    # APPROVAL_REQUIRED
    "submit_transaction": PolicyDecision.REQUIRE_APPROVAL,
    "close_account": PolicyDecision.REQUIRE_APPROVAL,
    "modify_critical_account_data": PolicyDecision.REQUIRE_APPROVAL,
    "delete_important_record": PolicyDecision.REQUIRE_APPROVAL,
    # BLOCKED
    "navigate_unauthorized_domain": PolicyDecision.DENY,
    "expose_credentials": PolicyDecision.DENY,
    "persist_prohibited_information": PolicyDecision.DENY,
}


class LayeredPolicyEngine(PolicyEngine):
    """.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md, "Layered Policy Model":

        global safety defaults -> vendor/application policy -> tenant-specific overrides

    Each layer is just an IntentPolicy (an intent -> PolicyDecision lookup
    table), scoped by nothing (global), by (vendor, application) (app
    layer), or by (vendor, application, tenant_id) (tenant layer). Policy
    is evaluated from `action.intent` (normalized business intent) and
    `context`, never from the action's target/locator -- so two vendors'
    differently-labeled UI controls ("Close Account" vs "Terminate
    Membership") that share intent `close_account` get the same decision.

    Combination rule -- deliberately the entire policy, not a priority
    table: collect every opinion that applies (global's, the app layer's,
    the tenant layer's, and the action's own explicitly-declared `risk` as
    one more opinion), and return the SINGLE MOST RESTRICTIVE one. This
    means:

    - A more specific layer can tighten a broader layer's decision (a
      tenant can require approval for something global treats as
      approval-required-at-most, or ban something app policy only
      flags for approval) -- exactly .CLAUDE/04's "institution override:
      all transfers require approval" example.
    - A more specific layer can never LOOSEN a broader layer's decision.
      If global says a known-dangerous intent is BLOCKED, no app or
      tenant policy -- and no amount of the action's own `risk` claiming
      otherwise -- can turn that into ALLOW. This is what keeps "layered
      policy" from becoming a way to quietly relax safety per tenant.
    - The action's declared `risk` is only ever a FALLBACK opinion, not a
      trump card: it participates in the same most-restrictive comparison
      as every layer, so an intent-based classification can always
      override an author's (or an LLM's) optimistic self-assessment, but
      never the reverse.

    Unclassified actions -- .CLAUDE/04: "Unknown / unsafe states must stop
    and escalate rather than guess." An action is unclassified when its
    intent appears in NONE of the three layers' tables AND its `risk`
    field was left at its implicit default rather than explicitly set
    (Action.risk defaults to RiskLevel.SAFE, which is exactly the silent-
    permissive trap this must not fall into: `action.model_fields_set` is
    how we tell "explicitly declared safe" apart from "nobody classified
    this at all"). With no opinions at all, this returns
    PolicyDecision.REQUIRE_APPROVAL, never ALLOW -- an unrecognized action
    escalates to a human instead of executing.
    """

    def __init__(
        self,
        global_policy: IntentPolicy | None = None,
        app_policies: dict[tuple[str, str], IntentPolicy] | None = None,
        tenant_policies: dict[tuple[str, str, str], IntentPolicy] | None = None,
    ) -> None:
        self._global = global_policy if global_policy is not None else IntentPolicy(DEFAULT_GLOBAL_INTENT_POLICY)
        self._app_policies = dict(app_policies or {})
        self._tenant_policies = dict(tenant_policies or {})

    def evaluate(self, action: Action, context: AppContext) -> PolicyDecision:
        opinions: list[PolicyDecision] = []

        global_decision = self._global.decision_for(action.intent)
        if global_decision is not None:
            opinions.append(global_decision)

        app_policy = self._app_policies.get((context.vendor, context.application))
        if app_policy is not None:
            app_decision = app_policy.decision_for(action.intent)
            if app_decision is not None:
                opinions.append(app_decision)

        tenant_policy = self._tenant_policies.get((context.vendor, context.application, context.tenant_id))
        if tenant_policy is not None:
            tenant_decision = tenant_policy.decision_for(action.intent)
            if tenant_decision is not None:
                opinions.append(tenant_decision)

        if "risk" in action.model_fields_set:
            opinions.append(RISK_TO_DECISION[action.risk])

        if not opinions:
            return PolicyDecision.REQUIRE_APPROVAL

        return max(opinions, key=lambda decision: _SEVERITY[decision])
