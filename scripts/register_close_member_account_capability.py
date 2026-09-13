"""One-off registration script: writes the smallest real capability +
artifact needed to intentionally reach APPROVAL_REQUIRED against the real
demo app, for manually verifying Phase 13's noVNC same-session handoff
(.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md).

Run from the repo root with the repo's own venv:
    .venv/bin/python scripts/register_close_member_account_capability.py

Writes:
    data/artifacts/meridian-demo/credit-union-admin/close_member_account/1.0.0.json
    data/capabilities/close_member_account/meridian-demo__credit-union-admin__base.json

Both via the real repository classes (FileArtifactRepository /
FileCapabilityRepository), so the files are byte-for-byte what the
running system itself would have written if it had discovered/registered
this capability on its own. Both repositories read fresh from disk on
every call (no caching layer), so a `docker compose` deployment whose
./data is bind-mounted picks this registration up on its very next
request -- no restart needed. Safe to re-run: the artifact save is a
no-op (with a printed notice) if version 1.0.0 already exists (versioned
artifacts are immutable once published -- see
FileArtifactRepository.save); the capability record save is an idempotent
overwrite, by design (see FileCapabilityRepository's own docstring).

Uses only real, already-seeded demo data: member M1001 / account A-5001
(demo_app/data.py), the real two-step close-account flow
(demo_app/app.py's own module docstring says it exists for exactly this
policy-gate scenario), the real "close_account" intent already mapped to
REQUIRE_APPROVAL in DEFAULT_GLOBAL_INTENT_POLICY
(src/cuas/safety/policy.py), and the real vendor/application values
already used throughout the test suite for this same demo app
(tests/fixtures/sample_artifacts.py: vendor="meridian-demo",
application="credit-union-admin"). No invented values.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cuas.artifact import (
    Artifact,
    ArtifactApplication,
    ArtifactSafety,
    FileArtifactRepository,
    OutputSpec,
    OutputType,
    Provenance,
    Step,
    SuccessCondition,
    SuccessConditionType,
)
from cuas.capability import CapabilityRecord, CapabilityStatus, FileCapabilityRepository
from cuas.domain import ActionType, Locator, LocatorStrategy, RiskLevel, Target
from cuas.surface.adapter import WaitCondition, WaitConditionKind

VENDOR = "meridian-demo"
APPLICATION = "credit-union-admin"
CAPABILITY_ID = "close_member_account"
VERSION = "1.0.0"

# Real seeded data (demo_app/data.py): M1001 (Jane Doe) -> A-5001 (checking,
# $2340.10, status "open"), rendered at accounts.html's row 1
# (id="acct-row-1") since it's the first account in her list. The
# automation container reaches the demo-app service at this hostname over
# the Compose network (docker-compose.yml); nothing in Settings
# substitutes this automatically (DEMO_APP_BASE_URL is declared in
# docker-compose.yml but not read by any code today), so it is hardcoded
# into this artifact's NAVIGATE step, same as every other artifact's
# target/value fields are fixed at authoring time.
DEMO_APP_BASE_URL = "http://demo-app:8080"
MEMBER_ID = "M1001"
ACCOUNT_ID = "A-5001"


def _role(role: str, name: str | None = None) -> Locator:
    params = {"role": role}
    if name is not None:
        params["name"] = name
    return Locator(strategy=LocatorStrategy.ROLE_NAME, params=params)


def _css(selector: str) -> Locator:
    return Locator(strategy=LocatorStrategy.CSS, params={"selector": selector})


def build_artifact() -> Artifact:
    return Artifact(
        capability_id=CAPABILITY_ID,
        name="Close Member Account",
        description=(
            "Open the two-step close-account confirmation for a member's account and, "
            "after operator approval, close it. Deliberately built to pause with "
            "APPROVAL_REQUIRED at the irreversible step (.CLAUDE/04_SAFETY_AND_HUMAN_HANDOFF.md's "
            "own close_account example) so Phase 13's noVNC same-session handoff has a real, "
            "non-synthetic scenario to exercise."
        ),
        version=VERSION,
        application=ArtifactApplication(vendor=VENDOR, application=APPLICATION, supported_versions=["1.x"]),
        tenant_scope="base",
        inputs={},
        outputs={
            "account_status": OutputSpec(
                type=OutputType.STRING,
                # accounts.html row 1 (acct-row-1) is A-5001 for M1001; 4th
                # <td> is the Status column, rendered "Closed" once the
                # close POST has actually run (demo_app/templates/accounts.html).
                source=Target(primary=_css("#acct-row-1 td:nth-child(4)")),
                required=True,
            )
        },
        steps=[
            Step(
                id="open_close_confirmation",
                action_type=ActionType.NAVIGATE,
                # "view_account" is ALLOW in DEFAULT_GLOBAL_INTENT_POLICY --
                # this step must NOT itself pause, so the run reaches the
                # *intended* pause point (the close click below) cleanly.
                intent="view_account",
                value=f"{DEMO_APP_BASE_URL}/members/{MEMBER_ID}/accounts/{ACCOUNT_ID}/close-confirm",
                risk=RiskLevel.SAFE,
                checkpoint=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="This cannot be undone."),
            ),
            Step(
                id="confirm_close",
                action_type=ActionType.CLICK,
                # "close_account" is REQUIRE_APPROVAL in
                # DEFAULT_GLOBAL_INTENT_POLICY -- this is the real pause
                # point. Declaring risk=SAFE here changes nothing (most
                # restrictive opinion always wins -- see
                # LayeredPolicyEngine's docstring); left explicit only to
                # match this repo's existing sample_artifacts.py convention
                # of always setting risk on a Step.
                intent="close_account",
                target=Target(primary=_role("button", "Confirm Close")),
                risk=RiskLevel.SAFE,
                checkpoint=WaitCondition(kind=WaitConditionKind.TEXT_PRESENT, text="Closed"),
            ),
        ],
        success_condition=SuccessCondition(type=SuccessConditionType.OUTPUT_VALID, output="account_status"),
        safety=ArtifactSafety(intent="close_account", risk=RiskLevel.APPROVAL_REQUIRED),
        provenance=Provenance(
            created_from_run="manual-authoring-phase13-verification", created_at=datetime.now(timezone.utc)
        ),
    )


def build_capability_record() -> CapabilityRecord:
    return CapabilityRecord(
        capability_id=CAPABILITY_ID,
        name="Close Member Account",
        description="Close a member's account after operator approval.",
        vendor=VENDOR,
        application=APPLICATION,
        supported_versions=["1.x"],
        tenant_scope="base",
        artifact_version=VERSION,
        status=CapabilityStatus.ACTIVE,
    )


def main() -> None:
    artifact_repo = FileArtifactRepository("data/artifacts")
    capability_repo = FileCapabilityRepository("data/capabilities")

    artifact = build_artifact()
    record = build_capability_record()

    try:
        artifact_path = artifact_repo.save(artifact)
        print(f"wrote artifact: {artifact_path}")
    except FileExistsError as exc:
        print(f"artifact already exists, not overwritten: {exc}")

    capability_path = capability_repo.save(record)
    print(f"wrote capability record: {capability_path}")


if __name__ == "__main__":
    main()
