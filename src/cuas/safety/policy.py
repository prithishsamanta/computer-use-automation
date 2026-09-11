"""The deterministic policy seam ReplayEngine (and, from Phase 8,
DiscoveryService) calls through for every single action
(.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md: "The LLM receives the safety
policy in its context, but it is not the final authority" -- and neither,
by the same principle, is a hardcoded artifact risk label taken at face
value forever; the runtime engine evaluates it fresh every time).

RiskBasedPolicyEngine is Phase 5's minimal implementation: it exists so
ReplayEngine has a real PolicyEngine to call through this phase, not a
TODO. It intentionally does NOT implement the full layered
global -> vendor/application -> tenant model over normalized intent that
.CLAUDE/04 describes -- that is Phase 6's job, built behind this same
PolicyEngine interface so ReplayEngine never has to change when it lands.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from cuas.domain import Action, AppContext, RiskLevel


class PolicyDecision(str, Enum):
    ALLOW = "allow"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


class PolicyEngine(ABC):
    @abstractmethod
    def evaluate(self, action: Action, context: AppContext) -> PolicyDecision: ...


class RiskBasedPolicyEngine(PolicyEngine):
    """Maps an action's own declared RiskLevel straight to a decision.
    Deliberately simple: see module docstring for why this is a
    placeholder, not the final policy engine.
    """

    _MAPPING: dict[RiskLevel, PolicyDecision] = {
        RiskLevel.SAFE: PolicyDecision.ALLOW,
        RiskLevel.APPROVAL_REQUIRED: PolicyDecision.REQUIRE_APPROVAL,
        RiskLevel.BLOCKED: PolicyDecision.DENY,
    }

    def evaluate(self, action: Action, context: AppContext) -> PolicyDecision:
        return self._MAPPING[action.risk]
