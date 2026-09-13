"""One-off registration script: registers get_savings_balance() -- the
existing, already-tested sample artifact (tests/fixtures/sample_artifacts.py,
exercised for real against a live browser by
tests/integration/test_replay_engine_e2e.py) -- as a real capability, so it
can be invoked through the actual running API/Docker container.

Version 1.0.0 (registered earlier) reproduced a real bug: RunOrchestrator
acquires a brand-new, unnavigated Playwright surface for every
non-resumed run (`cm = self._surface_factory(); surface = await
cm.__aenter__()` in orchestration/orchestrator.py, then straight into
`ReplayEngine.run()` -- no navigate anywhere in between), so the surface
starts on about:blank. get_savings_balance()'s own steps have always
assumed the surface is already sitting on the demo app's search page
(fill_member_id's `role=textbox` locator matches exactly one thing: the
index page's search box) -- true in every existing test, which always
calls `surface.navigate(demo_app_base_url + "/")` itself before invoking
ReplayEngine directly, and true of close_member_account (which begins
with its own NAVIGATE step), but never true when this fixture is replayed
through the real orchestrator with no caller navigating first. Confirmed
against the real container: both M1001 and no-such-member failed at
fill_member_id with TARGET_NOT_FOUND ("Could not resolve target for fill:
tried 1 candidate(s)") -- exactly what an empty about:blank page produces
for a bare `role=textbox` locator.

The shared fixture in tests/fixtures/sample_artifacts.py is deliberately
NOT changed: it's reused across ~15 unit tests and 4 integration tests
with base URLs that differ per environment (pytest's demo_app_base_url
fixture uses a fresh ephemeral port every run; this real deployment uses
the fixed Compose hostname http://demo-app:8080), which is exactly why it
was authored with no navigation step of its own in the first place --
adding one directly to the shared fixture would hardcode one specific
environment's URL into every test that calls it. Instead, this script
builds a small deployment-specific variant: the exact, unmodified
fixture's steps/outputs/success_condition/business_outcomes/
recoverable_conditions, with one additional leading NAVIGATE step (its
own capability_id-scoped id, own intent, own checkpoint) pointing at this
deployment's real demo-app URL -- the same pattern close_member_account
already uses, constructed via the real Artifact() constructor (so it goes
through full pydantic validation, not a raw, unvalidated model_copy).

Registered as version 1.0.1: FileArtifactRepository.save() refuses to
overwrite an existing version on purpose (a published artifact is
immutable once versioned -- see its own docstring), and 1.0.0 was already
written and is part of the repo's history. 1.0.1 is the smallest
correction, using RunOrchestrator's own established convention for
"already-known capability needs a newer version" (_next_version's
monotonic patch bump). The capability record is repointed at 1.0.1 --
an ordinary, idempotent metadata update (FileCapabilityRepository.save()
is designed for exactly this: "flipping status... or repointing
artifact_version at a newly published version is ordinary metadata
maintenance").

Run from the repo root with the repo's own venv:
    .venv/bin/python scripts/register_get_savings_balance_capability.py

Writes:
    data/artifacts/meridian-demo/credit-union-admin/get_savings_balance/1.0.1.json
Updates (idempotent overwrite):
    data/capabilities/get_savings_balance/meridian-demo__credit-union-admin__base.json
        (artifact_version: "1.0.0" -> "1.0.1")
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/ is a plain package (tests/fixtures/__init__.py exists) but is only
# on sys.path automatically under pytest's own pythonpath config
# (pyproject.toml: pythonpath = ["src", "."]) -- add the repo root here too
# so this plain script can import the same fixture function pytest does.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cuas.artifact import Artifact, FileArtifactRepository, Step
from cuas.capability import CapabilityRecord, CapabilityStatus, FileCapabilityRepository
from cuas.domain import ActionType, RiskLevel
from cuas.surface.adapter import WaitCondition, WaitConditionKind
from tests.fixtures.sample_artifacts import get_savings_balance

VENDOR = "meridian-demo"
APPLICATION = "credit-union-admin"
CAPABILITY_ID = "get_savings_balance"
VERSION = "1.0.1"

# Same real-container hostname close_member_account's artifact already
# uses (docker-compose.yml's Compose service network -- not read from any
# config today, same as that artifact's own NAVIGATE step; see this repo's
# scripts/register_close_member_account_capability.py).
DEMO_APP_BASE_URL = "http://demo-app:8080"


def build_deployed_artifact() -> Artifact:
    # The exact, unmodified fixture already proven against a real browser --
    # not reimplemented or redefined here. Only its *steps* list is
    # extended (one new leading step); every other field (inputs, outputs,
    # success_condition, business_outcomes, recoverable_conditions,
    # safety, provenance shape) is inherited as-is.
    base = get_savings_balance(version=VERSION)

    navigate_to_search_page = Step(
        id="open_search_page",
        action_type=ActionType.NAVIGATE,
        # "search_member" is ALLOW in DEFAULT_GLOBAL_INTENT_POLICY --
        # matches this step's actual purpose (arriving at the member
        # search page) and must not itself pause approval.
        intent="search_member",
        value=f"{DEMO_APP_BASE_URL}/",
        risk=RiskLevel.SAFE,
        checkpoint=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Member ID or Last Name"),
    )

    # Re-run through the real Artifact() constructor (full pydantic
    # validation: unique step ids, semver, success_condition references a
    # declared output) rather than an unvalidated model_copy -- base's own
    # other fields are reused via model_dump() and re-validated the same
    # way any other Artifact construction is.
    data = base.model_dump(exclude={"steps"})
    return Artifact(**data, steps=[navigate_to_search_page, *base.steps])


def main() -> None:
    artifact_repo = FileArtifactRepository("data/artifacts")
    capability_repo = FileCapabilityRepository("data/capabilities")

    artifact = build_deployed_artifact()
    assert artifact.capability_id == CAPABILITY_ID
    assert len(artifact.steps) == 3, "expected: navigate, fill, submit"

    record = CapabilityRecord(
        capability_id=CAPABILITY_ID,
        name=artifact.name,
        description=artifact.description,
        vendor=VENDOR,
        application=APPLICATION,
        supported_versions=["1.x"],
        tenant_scope="base",
        artifact_version=VERSION,
        status=CapabilityStatus.ACTIVE,
    )

    try:
        artifact_path = artifact_repo.save(artifact)
        print(f"wrote artifact: {artifact_path}")
    except FileExistsError as exc:
        print(f"artifact already exists, not overwritten: {exc}")

    capability_path = capability_repo.save(record)
    print(f"wrote capability record (now points at {VERSION}): {capability_path}")


if __name__ == "__main__":
    main()
