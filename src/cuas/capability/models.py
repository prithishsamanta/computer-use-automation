"""Capability metadata (.CLAUDE/07_IMPLEMENTATION_GUIDANCE.md's suggested
`capability/` package + `CapabilityRepository` interface;
.CLAUDE/05_SURFACES_AND_MULTI_TENANCY.md, "Capability Retrieval": metadata
answers "can this capability run on this application / version /
tenant?").

Deliberately separate from `Artifact` (.CLAUDE/07: "register/store
capability metadata separately from artifact files"): a `CapabilityRecord`
is a small, mutable *pointer* -- which artifact version currently backs
this capability, for which vendor/application/tenant, and whether it's
even switched on -- while an `Artifact` (artifact/schema.py) is the large,
immutable, versioned thing it points to. Flipping a capability to
DISABLED, or repointing it at a newer `artifact_version`, is an ordinary
metadata update; publishing a new `Artifact` version is not
(`FileArtifactRepository.save` refuses to overwrite one, on purpose).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# A capability record's `tenant_scope` uses this same "base" sentinel as
# `Artifact.tenant_scope` -- meaning "compatible with any tenant of this
# vendor/application" -- vs. a literal tenant_id, meaning "only this one
# tenant." See CapabilityService._compatibility for how the two compare.
BASE_TENANT_SCOPE = "base"


class CapabilityStatus(str, Enum):
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"


class CapabilityRecord(BaseModel):
    """One registered capability, scoped to a vendor/application and
    (optionally) a specific tenant, pointing at one artifact version.

    More than one `CapabilityRecord` can share the same `capability_id`
    -- e.g. a `tenant_scope="base"` record usable by any tenant of that
    vendor/application, alongside a `tenant_scope=<tenant_id>` record for
    one institution that needs a genuinely different artifact rather than
    a step-level override (.CLAUDE/08 decision #9 prefers overrides over
    full duplication, but doesn't forbid this when a tenant's workflow has
    truly diverged). `CapabilityService.resolve` picks the most specific
    compatible record for a given context, and refuses to guess when more
    than one is equally specific.
    """

    capability_id: str
    name: str
    description: str = ""
    vendor: str
    application: str
    supported_versions: list[str] = Field(
        default_factory=lambda: ["1.x"],
        description="Application versions this capability is compatible with. An entry "
        "may be an exact version ('1.2.0') or a major-version wildcard ('1.x').",
    )
    tenant_scope: str = BASE_TENANT_SCOPE
    artifact_version: str = Field(
        description="The Artifact version (as stored in ArtifactRepository) this "
        "capability currently resolves to."
    )
    status: CapabilityStatus = CapabilityStatus.ACTIVE
