"""Runtime capability lookup/resolution
(.CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md, "Capability Retrieval";
.CLAUDE/08_DECISIONS_AND_ASSUMPTIONS.md decision #8). This phase implements
only the deterministic metadata-filter half of that flow:

    request -> tenant/app context -> filter to compatible vendor/application/version
    -> [semantic similarity over compatible capabilities -- NOT this phase]
    -> validate candidate intent/input/output/risk compatibility
    -> select artifact or DISCOVERY_REQUIRED

There is exactly one registered capability_id per request here (the caller
already knows which capability it wants -- e.g. from an intent classifier
or a direct API call); semantic retrieval over *unknown* capability_ids is
the later stretch layer decision #8 anticipates, deliberately deferred.

`CapabilityService` does not itself decide to run discovery. Per
.CLAUDE/06_ERRORS_AND_OBSERVABILITY.md, "no semantic capability match is not
a replay failure" -- it is normal control flow -- but *what to do about it*
(kick off DiscoveryEngine, queue for a human, etc.) is the orchestrator's
call, not this service's. So `resolve()` returns a `CapabilityResolution`
whose status is one of RESOLVED / NO_CAPABILITY_MATCH / AMBIGUOUS; a caller
that wants "DISCOVERY_REQUIRED" as an action gets there by checking
`status != RESOLVED`, exactly the branch .CLAUDE/01_ARCHITECTURE.md's
"No existing capability" flow describes.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from cuas.artifact import Artifact, ArtifactRepository, resolve_artifact
from cuas.capability.models import BASE_TENANT_SCOPE, CapabilityRecord, CapabilityStatus
from cuas.capability.repository import CapabilityRepository
from cuas.domain import AppContext, ArtifactInvalidError


class CapabilityResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    NO_CAPABILITY_MATCH = "no_capability_match"
    AMBIGUOUS = "ambiguous"


class CapabilityResolution(BaseModel):
    """The outcome of one resolve() call. `artifact`/`matched_record` are
    set only when status == RESOLVED; `reason` is always a human-readable
    explanation, populated for every non-RESOLVED status (and left empty on
    RESOLVED, where the matched_record already says everything relevant)."""

    status: CapabilityResolutionStatus
    capability_id: str
    artifact: Artifact | None = None
    matched_record: CapabilityRecord | None = None
    reason: str = ""


def _version_matches(version: str, supported_versions: list[str]) -> bool:
    """Exact match ("1.2.0" == "1.2.0"), or a major-version wildcard
    ("1.x" matches any "1.*.*"). Anything else is a mismatch -- this
    service does not guess at semver ranges beyond what the artifact/
    capability schemas already express (ArtifactApplication.supported_versions
    uses the same "N.x" convention -- see tests/fixtures/sample_artifacts.py)."""

    for pattern in supported_versions:
        if pattern == version:
            return True
        if pattern.endswith(".x"):
            major = pattern[: -len(".x")]
            if version.split(".")[0] == major:
                return True
    return False


class CapabilityService:
    """Independent of FastAPI by design (constructor takes only the two
    repositories it needs) so an orchestrator -- HTTP-driven or not -- can
    call it directly, per the user's explicit Phase 10 instruction."""

    def __init__(self, capabilities: CapabilityRepository, artifacts: ArtifactRepository):
        self._capabilities = capabilities
        self._artifacts = artifacts

    def register(self, record: CapabilityRecord) -> None:
        self._capabilities.save(record)

    def resolve(self, capability_id: str, context: AppContext) -> CapabilityResolution:
        records = self._capabilities.list_by_capability_id(capability_id)
        if not records:
            return CapabilityResolution(
                status=CapabilityResolutionStatus.NO_CAPABILITY_MATCH,
                capability_id=capability_id,
                reason=f"no capability record registered for {capability_id!r}",
            )

        compatible: list[tuple[CapabilityRecord, int]] = []
        rejection_reasons: list[str] = []
        for record in records:
            is_compatible, reason, specificity = self._compatibility(record, context)
            if is_compatible:
                compatible.append((record, specificity))
            else:
                rejection_reasons.append(f"{record.vendor}/{record.application}@{record.artifact_version}: {reason}")

        if not compatible:
            return CapabilityResolution(
                status=CapabilityResolutionStatus.NO_CAPABILITY_MATCH,
                capability_id=capability_id,
                reason="no compatible capability record for "
                f"{context.vendor}/{context.application} {context.version} tenant={context.tenant_id}; "
                + "; ".join(rejection_reasons),
            )

        max_specificity = max(specificity for _, specificity in compatible)
        most_specific = [record for record, specificity in compatible if specificity == max_specificity]
        if len(most_specific) > 1:
            return CapabilityResolution(
                status=CapabilityResolutionStatus.AMBIGUOUS,
                capability_id=capability_id,
                reason=(
                    f"{len(most_specific)} equally-specific compatible capability records for "
                    f"{context.vendor}/{context.application} {context.version} tenant={context.tenant_id}; "
                    "refusing to guess rather than pick one arbitrarily "
                    f"(candidates: {[r.artifact_version for r in most_specific]})"
                ),
            )

        matched = most_specific[0]
        try:
            base = self._artifacts.load(
                matched.vendor, matched.application, matched.capability_id, matched.artifact_version
            )
        except FileNotFoundError:
            return CapabilityResolution(
                status=CapabilityResolutionStatus.NO_CAPABILITY_MATCH,
                capability_id=capability_id,
                reason=(
                    f"capability record for {capability_id!r} points at artifact version "
                    f"{matched.artifact_version!r}, which has no saved artifact file"
                ),
            )
        # ArtifactInvalidError is deliberately NOT caught here: a registered
        # capability pointing at a corrupted/schema-invalid artifact file is
        # a data-integrity bug, not a routine "nothing matched" outcome, and
        # should propagate loudly rather than be reported as NO_CAPABILITY_MATCH.

        overrides = self._artifacts.load_overrides(matched.vendor, matched.application, matched.capability_id)
        resolved_artifact = resolve_artifact(base, overrides, version=context.version, tenant_id=context.tenant_id)

        return CapabilityResolution(
            status=CapabilityResolutionStatus.RESOLVED,
            capability_id=capability_id,
            artifact=resolved_artifact,
            matched_record=matched,
        )

    def _compatibility(
        self, record: CapabilityRecord, context: AppContext
    ) -> tuple[bool, str | None, int]:
        """Returns (is_compatible, rejection_reason_if_not, specificity).

        Specificity is "most specific wins" the same way LayeredPolicyEngine
        prefers a more specific policy layer: a record scoped to this exact
        tenant (specificity 1) beats a tenant_scope="base" record usable by
        any tenant (specificity 0) when both are otherwise compatible. Two
        records tied at the same specificity for the same request is an
        AMBIGUOUS match, not a coin flip.
        """

        if record.vendor != context.vendor or record.application != context.application:
            return False, (
                f"vendor/application mismatch (record is for {record.vendor}/{record.application}, "
                f"request is for {context.vendor}/{context.application})"
            ), 0

        if record.status != CapabilityStatus.ACTIVE:
            return False, f"capability status is {record.status.value!r}, not active", 0

        if not _version_matches(context.version, record.supported_versions):
            return False, (
                f"version {context.version!r} not in supported_versions {record.supported_versions}"
            ), 0

        if record.tenant_scope == BASE_TENANT_SCOPE:
            return True, None, 0
        if record.tenant_scope == context.tenant_id:
            return True, None, 1
        return False, (
            f"tenant_scope {record.tenant_scope!r} does not match requesting tenant {context.tenant_id!r}"
        ), 0
