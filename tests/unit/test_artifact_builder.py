"""ArtifactBuilder's own construction logic, against hand-crafted
DiscoveryTrace/DiscoveryGoal objects -- fast, deterministic, no engine run
needed to exercise every branch. tests/integration/test_artifact_builder_e2e.py
covers the same builder fed by a real DiscoveryEngine run, with the
resulting Artifact then actually replayed against the real demo app.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from cuas.artifact import FileArtifactRepository
from cuas.artifact.schema import BusinessOutcome, OutputType
from cuas.artifact_builder import ArtifactBuildError, ArtifactBuilder
from cuas.discovery.models import DiscoveryGoal
from cuas.discovery.trace import DiscoveryTrace, DiscoveryTraceStep
from cuas.domain import ActionType, AppContext, RiskLevel
from cuas.safety import PolicyDecision
from cuas.surface.adapter import WaitCondition, WaitConditionKind

CONTEXT = AppContext(vendor="meridian-demo", application="credit-union-admin", version="1.0.0", tenant_id="base")


def _target_dict(strategy: str, params: dict, frame: str | None = None) -> dict:
    return {"primary": {"strategy": strategy, "params": params, "frame": frame}, "fallbacks": []}


def _role(role: str, name: str | None = None) -> dict:
    params = {"role": role}
    if name is not None:
        params["name"] = name
    return _target_dict("role_name", params)


def _css(selector: str, frame: str | None = None) -> dict:
    return _target_dict("css", {"selector": selector}, frame=frame)


def _action_dict(action_type: str, intent: str, *, target=None, value=None, action_id: str = "a1") -> dict:
    return {"id": action_id, "action_type": action_type, "intent": intent, "target": target, "value": value, "risk": "safe"}


def _step(index: int, action_type: str, intent: str, *, target=None, value=None, read_value=None, outcome: str = "executed", parsed_action=None) -> DiscoveryTraceStep:
    return DiscoveryTraceStep(
        step_index=index,
        observation_url="http://fake.invalid/",
        observation_text_excerpt="",
        parsed_action=parsed_action if parsed_action is not None else _action_dict(action_type, intent, target=target, value=value, action_id=f"discovery-{index}"),
        policy_decision=PolicyDecision.ALLOW,
        outcome=outcome,
        read_value=read_value,
    )


def _trace(steps, *, final_status: str = "success", run_id: str = "run-1", capability_id: str = "get_savings_balance", goal_description: str = "Find member {{member_id}} and read their savings balance.") -> DiscoveryTrace:
    return DiscoveryTrace(
        run_id=run_id,
        capability_id=capability_id,
        goal_description=goal_description,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        final_status=final_status,
        steps=steps,
    )


def _goal(**overrides) -> DiscoveryGoal:
    defaults = dict(
        capability_id="get_savings_balance",
        description="Find member M1001 and read their savings balance.",
        start_url="http://fake.invalid/",
        inputs={"member_id": "M1001"},
        sensitive_inputs={"member_id"},
    )
    defaults.update(overrides)
    return DiscoveryGoal(**defaults)


def _happy_path_steps() -> list[DiscoveryTraceStep]:
    return [
        _step(0, "fill", "enter_member_id", target=_role("textbox"), value="{{member_id}}"),
        _step(1, "click", "submit_member_search", target=_role("button", "Search")),
        _step(2, "read", "read_savings_balance", target=_css("#acct-row-2 td:nth-child(3)", frame="#accounts-frame"), read_value="$18204.55"),
    ]


def test_clean_successful_trace_becomes_an_artifact() -> None:
    goal = _goal(
        success_checkpoint=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Accounts"),
        known_business_outcomes=[
            BusinessOutcome(code="MEMBER_NOT_FOUND", detect=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Member not found."))
        ],
    )
    trace = _trace(_happy_path_steps(), run_id="run-happy")

    artifact = ArtifactBuilder().build(trace, goal, CONTEXT)

    assert [s.action_type for s in artifact.steps] == [ActionType.NAVIGATE, ActionType.FILL, ActionType.CLICK]
    # steps[0] is the deterministic navigate-to-start step ArtifactBuilder
    # itself prepends (never model-driven) -- see build()'s own docstring
    # point 8. The model's first *recorded* action, the FILL, is steps[1].
    assert artifact.steps[0].id == "navigate_to_discovery_start"
    assert artifact.steps[0].value == goal.start_url
    assert artifact.steps[1].value == "{{member_id}}"
    assert artifact.steps[-1].checkpoint == goal.success_checkpoint  # carried onto the last non-READ step
    assert list(artifact.outputs) == ["savings_balance"]  # "read_" prefix stripped from the READ's intent
    assert artifact.outputs["savings_balance"].type == OutputType.DECIMAL
    assert artifact.success_condition.output == "savings_balance"
    assert artifact.business_outcomes == goal.known_business_outcomes
    assert artifact.inputs["member_id"].sensitive is True
    assert artifact.inputs["member_id"].required is True
    assert artifact.safety.risk == RiskLevel.SAFE
    assert artifact.provenance.created_from_run == "run-happy"
    assert artifact.version == "1.0.0"
    # Every *model-driven* constructed Step leaves risk unset, exactly as
    # discovery left Action.risk unset -- LayeredPolicyEngine's
    # fail-closed behavior for an unrecognized intent must govern a
    # replay of this artifact too.
    assert "risk" not in artifact.steps[1].model_fields_set
    # The one deliberate exception is the prepended navigate step, which
    # explicitly declares risk=SAFE since it was never itself a policy
    # decision discovery made -- see _build_navigate_to_start_step.
    assert artifact.steps[0].risk == RiskLevel.SAFE
    assert "risk" in artifact.steps[0].model_fields_set


def test_trace_with_a_wrong_turn_detour_produces_an_artifact_without_it() -> None:
    steps = [
        _step(0, "fill", "enter_member_id", target=_role("textbox"), value="{{member_id}}"),
        _step(1, "click", "submit_member_search", target=_role("button", "WrongButton"), outcome="execution_failed"),
        _step(2, "click", "submit_member_search", target=_role("button", "Search")),
        _step(3, "read", "read_savings_balance", target=_css("#acct-row-2 td:nth-child(3)"), read_value="$18204.55"),
    ]
    artifact = ArtifactBuilder().build(_trace(steps), _goal(), CONTEXT)

    assert len(artifact.steps) == 3  # navigate-to-start, fill, click (the wrong-button click never counted)
    assert all(step.target.primary.params.get("name") != "WrongButton" for step in artifact.steps if step.target)


def test_sensitive_literal_values_become_typed_input_placeholders() -> None:
    """Even a raw literal ArtifactBuilder finds sitting in a kept action's
    value (not yet placeholder-ized) is substituted -- this is the
    builder's own responsibility, independent of whatever redaction
    DiscoveryEngine already did upstream."""

    steps = [
        _step(0, "fill", "enter_member_id", target=_role("textbox"), value="M1001"),  # raw literal
        _step(1, "read", "read_savings_balance", target=_css("#balance"), read_value="$100.00"),
    ]
    artifact = ArtifactBuilder().build(_trace(steps), _goal(inputs={"member_id": "M1001"}, sensitive_inputs={"member_id"}), CONTEXT)

    assert artifact.steps[0].action_type == ActionType.NAVIGATE  # the prepended deterministic setup step
    assert artifact.steps[1].value == "{{member_id}}"
    assert "M1001" not in artifact.model_dump_json()


class TestMalformedOrIncompleteTracesAreRejected:
    def test_trace_that_did_not_end_in_success_is_rejected(self) -> None:
        trace = _trace(_happy_path_steps(), final_status="failed")
        with pytest.raises(ArtifactBuildError, match="did not end in success"):
            ArtifactBuilder().build(trace, _goal(), CONTEXT)

    def test_trace_with_no_steps_at_all_is_rejected(self) -> None:
        trace = _trace([])
        with pytest.raises(ArtifactBuildError, match="no recorded steps"):
            ArtifactBuilder().build(trace, _goal(), CONTEXT)

    def test_trace_with_no_executed_steps_is_rejected(self) -> None:
        steps = [_step(0, "click", "submit_member_search", target=_role("button", "Search"), outcome="execution_failed")]
        trace = _trace(steps)
        with pytest.raises(ArtifactBuildError, match="nothing to build an artifact from"):
            ArtifactBuilder().build(trace, _goal(), CONTEXT)

    def test_trace_with_no_read_action_is_rejected(self) -> None:
        steps = [_step(0, "fill", "enter_member_id", target=_role("textbox"), value="{{member_id}}")]
        trace = _trace(steps)
        with pytest.raises(ArtifactBuildError, match="no READ actions"):
            ArtifactBuilder().build(trace, _goal(), CONTEXT)

    def test_an_executed_step_with_a_corrupted_action_is_rejected(self) -> None:
        broken = _step(0, "click", "submit_member_search", parsed_action={"id": "a1", "intent": "submit_member_search"})  # no action_type at all
        steps = [broken, _step(1, "read", "read_savings_balance", target=_css("#balance"), read_value="1")]
        trace = _trace(steps)
        with pytest.raises(ArtifactBuildError):
            ArtifactBuilder().build(trace, _goal(), CONTEXT)


def test_adjacent_duplicate_actions_are_collapsed_conservatively() -> None:
    steps = [
        _step(0, "fill", "enter_member_id", target=_role("textbox"), value="{{member_id}}"),
        _step(1, "fill", "enter_member_id", target=_role("textbox"), value="{{member_id}}"),  # exact adjacent repeat
        _step(2, "click", "submit_member_search", target=_role("button", "Search")),
        _step(3, "read", "read_savings_balance", target=_css("#balance"), read_value="1"),
    ]
    artifact = ArtifactBuilder().build(_trace(steps), _goal(), CONTEXT)

    assert len(artifact.steps) == 3  # navigate-to-start + the duplicate fill collapsed to one + click


def test_non_adjacent_repeats_are_preserved_not_deduplicated() -> None:
    """The same action repeated with something else in between is real
    history (e.g. correcting course and coming back), not redundancy --
    conservative means only an *exact, adjacent* repeat is ever removed."""

    steps = [
        _step(0, "fill", "enter_member_id", target=_role("textbox"), value="{{member_id}}"),
        _step(1, "click", "submit_member_search", target=_role("button", "Search")),
        _step(2, "fill", "enter_member_id", target=_role("textbox"), value="{{member_id}}"),  # non-adjacent repeat
        _step(3, "read", "read_savings_balance", target=_css("#balance"), read_value="1"),
    ]
    artifact = ArtifactBuilder().build(_trace(steps), _goal(), CONTEXT)

    assert len(artifact.steps) == 4  # navigate-to-start + nothing else collapsed


def test_discovered_artifact_gets_a_leading_navigate_to_start_step() -> None:
    """Regression test for the ArtifactBuilder navigate-step fix
    (DECISIONS_LOG.md): DiscoveryEngine.run() navigates to
    `goal.start_url` *before* recording a single trace step (engine.py's
    `await self._surface.navigate(goal.start_url)` precedes the step
    loop entirely), so a naively reconstructed artifact was always
    missing that prerequisite -- replaying it from a fresh, unnavigated
    surface (exactly what RunOrchestrator hands every non-resumed run)
    would start on about:blank, the same defect class already found and
    fixed for the hand-authored get_savings_balance artifact.

    Proves: (1) a leading NAVIGATE step to goal.start_url is present, and
    (2) the trace's own successful actions still follow it in the exact
    order DiscoveryEngine recorded them."""

    goal = _goal(start_url="http://demo-app.invalid/search")
    artifact = ArtifactBuilder().build(_trace(_happy_path_steps()), goal, CONTEXT)

    assert artifact.steps[0].action_type == ActionType.NAVIGATE
    assert artifact.steps[0].id == "navigate_to_discovery_start"
    assert artifact.steps[0].value == "http://demo-app.invalid/search"
    # The model-driven steps (fill, then click) are unchanged in content
    # and still follow the prepended navigate step in their original order.
    assert [s.action_type for s in artifact.steps] == [ActionType.NAVIGATE, ActionType.FILL, ActionType.CLICK]
    assert artifact.steps[1].value == "{{member_id}}"
    assert artifact.steps[2].target.primary.params.get("name") == "Search"


def test_read_only_discovery_still_gets_a_leading_navigate_step() -> None:
    """A discovery whose only useful model action was a single READ (no
    fill/click at all -- e.g. the goal's start_url already puts the
    surface where the value lives) is the sharpest version of the bug:
    _build_steps excludes READ actions entirely (they become an
    OutputSpec, not a Step -- see build()'s point 5), so without this fix
    such an artifact would have had *zero* steps and nothing at all to
    reach its own output with on replay."""

    steps = [_step(0, "read", "read_savings_balance", target=_css("#balance"), read_value="$100.00")]
    goal = _goal(start_url="http://demo-app.invalid/members/M1001/accounts")

    artifact = ArtifactBuilder().build(_trace(steps), goal, CONTEXT)

    assert len(artifact.steps) == 1
    assert artifact.steps[0].action_type == ActionType.NAVIGATE
    assert artifact.steps[0].value == "http://demo-app.invalid/members/M1001/accounts"
    assert artifact.steps[0].risk == RiskLevel.SAFE
    assert list(artifact.outputs) == ["savings_balance"]
    # Safety metadata is computed from the model-driven steps only (none,
    # here) -- the prepended navigate step must not make ArtifactBuilder
    # claim "open_start_page" as this capability's overall intent; the
    # pre-existing output-name fallback still applies.
    assert artifact.safety.intent == "savings_balance"


def test_provenance_and_version_metadata() -> None:
    trace = _trace(_happy_path_steps(), run_id="run-provenance-check")

    artifact = ArtifactBuilder().build(trace, _goal(), CONTEXT, version="2.3.1")

    assert artifact.provenance.created_from_run == "run-provenance-check"
    assert isinstance(artifact.provenance.created_at, datetime)
    assert artifact.version == "2.3.1"


def test_an_invalid_version_still_fails_schema_validation_before_storage() -> None:
    """Confirms the constructed artifact really does pass through
    Artifact's own pydantic validators (not just this module's own
    checks) -- a non-semver version is something only the schema itself
    rejects."""

    with pytest.raises(ArtifactBuildError):
        ArtifactBuilder().build(_trace(_happy_path_steps()), _goal(), CONTEXT, version="not-a-version")


def test_round_trip_save_and_load_through_artifact_repository(tmp_path) -> None:
    artifact = ArtifactBuilder().build(_trace(_happy_path_steps()), _goal(), CONTEXT)
    repo = FileArtifactRepository(tmp_path)

    repo.save(artifact)
    loaded = repo.load(
        artifact.application.vendor, artifact.application.application, artifact.capability_id, artifact.version
    )

    assert loaded == artifact
