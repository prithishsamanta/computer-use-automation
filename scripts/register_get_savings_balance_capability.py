"""One-off registration script: registers get_savings_balance() -- the
existing, already-tested sample artifact (tests/fixtures/sample_artifacts.py,
exercised for real against a live browser by
tests/integration/test_replay_engine_e2e.py) -- as a real capability, so it
can be invoked through the actual running API/Docker container. This adds
no new behavior and does not redefine the artifact: it imports the exact
same function the test suite already calls and persists whatever it
returns, unmodified.

Run from the repo root with the repo's own venv:
    .venv/bin/python scripts/register_get_savings_balance_capability.py

Writes:
    data/artifacts/meridian-demo/credit-union-admin/get_savings_balance/1.0.0.json
    data/capabilities/get_savings_balance/meridian-demo__credit-union-admin__base.json

Same idempotent-rerun behavior as
scripts/register_close_member_account_capability.py: the artifact save is
a no-op (with a printed notice) if version 1.0.0 already exists; the
capability record save is an idempotent overwrite by design.
"""

from __future__ import annotations

import sys
from pathlib import Path

# tests/ is a plain package (tests/fixtures/__init__.py exists) but is only
# on sys.path automatically under pytest's own pythonpath config
# (pyproject.toml: pythonpath = ["src", "."]) -- add the repo root here too
# so this plain script can import the same fixture function pytest does.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cuas.artifact import FileArtifactRepository
from cuas.capability import CapabilityRecord, CapabilityStatus, FileCapabilityRepository
from tests.fixtures.sample_artifacts import get_savings_balance

VENDOR = "meridian-demo"
APPLICATION = "credit-union-admin"
CAPABILITY_ID = "get_savings_balance"
VERSION = "1.0.0"


def main() -> None:
    artifact_repo = FileArtifactRepository("data/artifacts")
    capability_repo = FileCapabilityRepository("data/capabilities")

    # The exact, unmodified fixture already proven against a real browser --
    # not reimplemented or redefined here.
    artifact = get_savings_balance(version=VERSION)
    assert artifact.capability_id == CAPABILITY_ID

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
    print(f"wrote capability record: {capability_path}")


if __name__ == "__main__":
    main()
