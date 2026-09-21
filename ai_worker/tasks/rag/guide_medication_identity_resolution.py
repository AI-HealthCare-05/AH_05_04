"""Read-only matched-medication identity boundary for final Guide composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from ai_worker.tasks.rag.guideline_card import MedicationIdentityRef

__all__ = [
    "MedicationIdentityRefResolution",
    "MedicationIdentityRefResolverPort",
]


@dataclass(frozen=True, slots=True)
class MedicationIdentityRefResolution:
    """One authoritative matched-identification observation.

    The two identifiers let the composition root prove that the returned product
    identity belongs to the carrier identification it is about to generate for.
    No name, strength, product search result, or current/latest lookup is accepted.
    """

    medication_identification_id: UUID
    prescription_version_medication_id: UUID
    medication_identity: MedicationIdentityRef


class MedicationIdentityRefResolverPort(Protocol):
    """Read the existing matched product identities for one frozen Guide carrier."""

    async def resolve_ordered(
        self,
        *,
        identifications: tuple[object, ...],
    ) -> tuple[MedicationIdentityRefResolution, ...] | None: ...
