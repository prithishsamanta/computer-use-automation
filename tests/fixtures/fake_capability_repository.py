"""In-memory CapabilityRepository for unit-testing CapabilityService's
resolution logic in isolation from any one storage layer's own identity
rules. FileCapabilityRepository keys records by (capability_id, vendor,
application, tenant_scope), which -- by construction -- can never hold two
records that would tie on specificity for the same request (there is only
one "base" slot and only one slot per exact tenant_id per vendor/app). That
makes it a poor fixture for proving CapabilityService *itself* detects and
refuses an ambiguous match rather than picking one arbitrarily: the
defensive check deserves its own direct test, independent of whether
today's file layout happens to prevent the situation from arising in
practice (a different repository implementation, a migration, or a hand-
edited data file could all produce it). This fake's save() simply appends,
so tests can construct that scenario on purpose.
"""

from __future__ import annotations

from cuas.capability.models import CapabilityRecord
from cuas.capability.repository import CapabilityRepository


class FakeCapabilityRepository(CapabilityRepository):
    def __init__(self, records: list[CapabilityRecord] | None = None):
        self._records: list[CapabilityRecord] = list(records or [])

    def save(self, record: CapabilityRecord) -> None:
        self._records.append(record)

    def list_by_capability_id(self, capability_id: str) -> list[CapabilityRecord]:
        return [r for r in self._records if r.capability_id == capability_id]

    def list_all(self) -> list[CapabilityRecord]:
        return list(self._records)
