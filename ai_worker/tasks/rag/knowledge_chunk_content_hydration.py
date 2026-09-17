"""KnowledgeChunk Content Hydration Read-Only Contract Kernel (#711 Prerequisite).

Read-only hydration seam between the #697/#703 AUTHENTICATED retrieval selection
and the authoritative persisted `knowledge_chunk` body that the production
#178 retrieval already selected.

Scope & Authority Boundaries:
- Read-Only Seam: this module performs no database writes, no migrations, no
  schema change, and issues no lock. The only dependency is a read-only
  `KnowledgeChunkContentReaderPort`.
- No New Authority: hydration re-derives no eligibility, freshness, assessment,
  approval, Source currentness, or ranking meaning. Those belong to the #178/#180
  gates. The single question owned here is whether the persisted chunk body still
  matches, exactly, the provenance recorded when production retrieval selected it.
- Lookup Identity: reads are addressed by (`knowledge_index_id`,
  `knowledge_chunk_id`) together, never by chunk id alone, so the row read is the
  same Knowledge Index membership the search observed. One chunk may belong to
  several Index versions, and this pair keeps that unambiguous.
- Provenance Definitive Type: the expected and observed provenance are both the
  existing `ProductionEvidenceProvenance`. No hydration-specific provenance DTO
  exists, and field meanings are unchanged from production search.
- Exact Equality: verification is whole-value equality of
  `observation.provenance == selection.hit.provenance`. No per-field tolerance,
  normalization, trimming, case folding, or fallback is defined. Any differing
  field - index identity, chunk identity, Source Snapshot or Member identity,
  Source code/version, canonical checksum, external document, chunk index,
  locator, content hash, canonicalization spec version, or normalization version
  - closes as the single reason PROVENANCE_MISMATCH.
- Content Verification: `sha256(chunk_text.encode("utf-8")).hexdigest()` must
  equal the expected `content_hash`. The body is never normalized or repaired to
  make a hash agree.
- Atomic Fail-Closed: hydration is all-or-nothing. A single failing selection
  rejects the whole outcome with `selections = ()`; no partial hydration is
  returned and remaining selections are not read.
- Reader Exception Boundary: only the explicit `KnowledgeChunkContentReaderError`
  is converted into CONTENT_READER_ERROR, mirroring the #672 reader seam.
  Programming errors and unexpected exceptions propagate unhandled.
- Sensitive Content: hydrated bodies are carried as `SensitiveText`, whose
  representation is redacted. This module never logs chunk content, rows, or
  reader payloads, and no failure reason carries the body.
- Excluded Material: assessment authenticity, eligibility receipts, verifier
  artifacts, and the Guide Evidence Handoff request are not part of this seam.
  `HydratedGuideRetrievalSelection` is deliberately the terminal output.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ai_worker.tasks.rag.evidence_retrieval import SensitiveText
from ai_worker.tasks.rag.evidence_search import ProductionEvidenceProvenance
from ai_worker.tasks.rag.guide_retrieval_composition import AuthenticatedGuideRetrievalSelection

__all__ = [
    "GuideContentHydrationDecision",
    "GuideContentHydrationOutcome",
    "GuideContentHydrationReason",
    "HydratedGuideRetrievalSelection",
    "KnowledgeChunkContentObservation",
    "KnowledgeChunkContentReaderError",
    "KnowledgeChunkContentReaderPort",
    "hydrate_guide_retrieval_content",
]


class GuideContentHydrationDecision(StrEnum):
    HYDRATED = "HYDRATED"
    REJECTED = "REJECTED"


class GuideContentHydrationReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    CONTENT_NOT_FOUND = "CONTENT_NOT_FOUND"
    PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
    CONTENT_HASH_MISMATCH = "CONTENT_HASH_MISMATCH"
    CONTENT_READER_ERROR = "CONTENT_READER_ERROR"


@dataclass(frozen=True, slots=True)
class KnowledgeChunkContentObservation:
    """Authoritative persisted chunk body with the provenance rebuilt from its row."""

    provenance: ProductionEvidenceProvenance
    content_text: SensitiveText


class KnowledgeChunkContentReaderError(Exception):
    """Explicit dependency failure when reading persisted KnowledgeChunk content.

    The hydration kernel converts only this exception into CONTENT_READER_ERROR.
    Programming errors and unexpected exceptions propagate unhandled. Adapters
    must not carry raw SQL, connection strings, or chunk content in the message.
    """


class KnowledgeChunkContentReaderPort(Protocol):
    async def read_content(
        self,
        *,
        knowledge_index_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> KnowledgeChunkContentObservation | None: ...


@dataclass(frozen=True, slots=True)
class HydratedGuideRetrievalSelection:
    """An authenticated selection preserved as-is, carrying its verified body.

    `hit` and `binding` are never copied out of `selection`; hydration only
    attaches content.
    """

    selection: AuthenticatedGuideRetrievalSelection
    content_text: SensitiveText


@dataclass(frozen=True, slots=True)
class GuideContentHydrationOutcome:
    decision: GuideContentHydrationDecision
    reasons: tuple[GuideContentHydrationReason, ...]
    selections: tuple[HydratedGuideRetrievalSelection, ...] = ()


def _rejected(reason: GuideContentHydrationReason) -> GuideContentHydrationOutcome:
    return GuideContentHydrationOutcome(
        decision=GuideContentHydrationDecision.REJECTED,
        reasons=(reason,),
        selections=(),
    )


def _validate_input(
    selections: tuple[AuthenticatedGuideRetrievalSelection, ...],
) -> GuideContentHydrationReason | None:
    """Reject anything that is not a non-empty tuple of authenticated selections.

    An empty tuple is REQUEST_INVALID rather than a vacuous success: a production
    Evidence Gate success always carries at least one selected hit, so nothing to
    hydrate means the caller did not supply a #703 AUTHENTICATED outcome.
    """
    if type(selections) is not tuple or not selections:
        return GuideContentHydrationReason.REQUEST_INVALID
    for selection in selections:
        if type(selection) is not AuthenticatedGuideRetrievalSelection:
            return GuideContentHydrationReason.REQUEST_INVALID
        if type(selection.hit.provenance) is not ProductionEvidenceProvenance:
            return GuideContentHydrationReason.REQUEST_INVALID
    return None


async def hydrate_guide_retrieval_content(
    selections: tuple[AuthenticatedGuideRetrievalSelection, ...],
    *,
    reader: KnowledgeChunkContentReaderPort,
) -> GuideContentHydrationOutcome:
    """Re-read and exact-verify the persisted body of every authenticated selection.

    Validation is phase-ordered and fail-fast, and a rejection returns exactly one
    typed reason with no partial selections:
    - Phase 1: input structure validation (REQUEST_INVALID)
    - Phase 2: read each selection in input order by
      (`knowledge_index_id`, `knowledge_chunk_id`) (CONTENT_NOT_FOUND,
      CONTENT_READER_ERROR)
    - Phase 3: observed provenance whole-value equality against the selected hit's
      provenance (PROVENANCE_MISMATCH)
    - Phase 4: UTF-8 SHA-256 of the persisted body against the expected
      `content_hash` (CONTENT_HASH_MISMATCH)
    - Phase 5: build `HydratedGuideRetrievalSelection`

    Because Phase 3 already binds the observed provenance to the expected one as a
    whole value, the Phase 4 comparison against the expected `content_hash`
    simultaneously verifies the persisted provenance's own `content_hash`.

    The production selection order is preserved; this seam applies no ordering
    policy of its own.
    """
    # --------------------------------------------------------------------------
    # Phase 1: Input Structure Validation
    # --------------------------------------------------------------------------
    request_error = _validate_input(selections)
    if request_error is not None:
        return _rejected(request_error)

    hydrated: list[HydratedGuideRetrievalSelection] = []

    for selection in selections:
        expected = selection.hit.provenance

        # ----------------------------------------------------------------------
        # Phase 2: Authoritative Read (index membership + chunk identity)
        # ----------------------------------------------------------------------
        try:
            observation = await reader.read_content(
                knowledge_index_id=expected.knowledge_index_id,
                knowledge_chunk_id=expected.knowledge_chunk_id,
            )
        except KnowledgeChunkContentReaderError:
            return _rejected(GuideContentHydrationReason.CONTENT_READER_ERROR)

        if observation is None:
            return _rejected(GuideContentHydrationReason.CONTENT_NOT_FOUND)
        if (
            type(observation) is not KnowledgeChunkContentObservation
            or type(observation.content_text) is not SensitiveText
        ):
            # A malformed reader payload is a dependency failure, not a content
            # verdict; it must never be treated as a successful observation.
            return _rejected(GuideContentHydrationReason.CONTENT_READER_ERROR)

        # ----------------------------------------------------------------------
        # Phase 3: Exact Provenance Equality
        # ----------------------------------------------------------------------
        if observation.provenance != expected:
            return _rejected(GuideContentHydrationReason.PROVENANCE_MISMATCH)

        # ----------------------------------------------------------------------
        # Phase 4: UTF-8 SHA-256 Content Verification
        # ----------------------------------------------------------------------
        computed_hash = hashlib.sha256(observation.content_text.reveal().encode("utf-8")).hexdigest()
        if computed_hash != expected.content_hash:
            return _rejected(GuideContentHydrationReason.CONTENT_HASH_MISMATCH)

        # ----------------------------------------------------------------------
        # Phase 5: Hydrated Selection
        # ----------------------------------------------------------------------
        hydrated.append(
            HydratedGuideRetrievalSelection(
                selection=selection,
                content_text=observation.content_text,
            )
        )

    return GuideContentHydrationOutcome(
        decision=GuideContentHydrationDecision.HYDRATED,
        reasons=(),
        selections=tuple(hydrated),
    )
