"""Unit tests for KnowledgeChunk Content Hydration Read-Only Kernel (#711)."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.evidence_search import (
    FractionReceipt,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    StableCoordinate,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    ObservedDecisionOutcome,
    RequestDecisionStage,
    RequestSourceMemberBinding,
)
from ai_worker.tasks.rag.guide_retrieval_composition import AuthenticatedGuideRetrievalSelection
from ai_worker.tasks.rag.knowledge_chunk_content_hydration import (
    GuideContentHydrationDecision,
    GuideContentHydrationReason,
    KnowledgeChunkContentObservation,
    KnowledgeChunkContentReaderError,
    hydrate_guide_retrieval_content,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberKind

_INDEX_ID = UUID("71100000-0000-4000-8000-000000000001")
_SNAPSHOT_ID = UUID("71100000-0000-4000-8000-000000000002")
_MEMBER_ID = UUID("71100000-0000-4000-8000-000000000003")

_RAW_CONTENT_SENTINEL = "SYNTHETIC_CHUNK_BODY_SENTINEL_711"


def _make_artifact(code: str, version: str = "v1") -> ImmutableArtifactRef:
    return ImmutableArtifactRef(artifact_code=code, version=version, content_sha256="a" * 64)


def _make_provenance(
    *,
    chunk_id: UUID,
    content: str,
    external_document_id: str = "DOC-1",
    chunk_index: int = 0,
) -> ProductionEvidenceProvenance:
    return ProductionEvidenceProvenance(
        knowledge_index_id=_INDEX_ID,
        index_code="GUIDELINE_INDEX",
        index_version="v1",
        index_configuration_hash="4" * 64,
        knowledge_chunk_id=chunk_id,
        source_snapshot_id=_SNAPSHOT_ID,
        source_snapshot_member_id=_MEMBER_ID,
        source_code="MFDS_LABEL",
        source_version="2026.1",
        canonical_checksum="c" * 64,
        external_document_id=external_document_id,
        chunk_index=chunk_index,
        locator=f"$.records[{chunk_index}]",
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        canonicalization_spec_version="canonical-v1",
        normalization_version="normalization-v1",
    )


def _make_selection(
    *,
    chunk_id: UUID | None = None,
    content: str = _RAW_CONTENT_SENTINEL,
    external_document_id: str = "DOC-1",
    chunk_index: int = 0,
    fusion_rank: int = 1,
) -> AuthenticatedGuideRetrievalSelection:
    provenance = _make_provenance(
        chunk_id=chunk_id or uuid4(),
        content=content,
        external_document_id=external_document_id,
        chunk_index=chunk_index,
    )
    hit = ProductionSearchHit(
        provenance=provenance,
        coordinate=StableCoordinate(
            source_code=provenance.source_code,
            source_version=provenance.source_version,
            external_document_id=external_document_id,
            chunk_index=chunk_index,
        ),
        exact_hit=False,
        observed_trigram_score=None,
        observed_fts_score=None,
        observed_dense_score=None,
        lexical_rank=None,
        dense_rank=None,
        fusion_rank=fusion_rank,
        fraction_receipt=FractionReceipt("1", "60"),
        is_eligible_for_future_reranker=True,
    )
    binding = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("request_guard"),
        request_operation_code="GUIDE_GENERATE",
        source_snapshot_id=_SNAPSHOT_ID,
        source_snapshot_member_id=_MEMBER_ID,
        source_code=provenance.source_code,
        source_version=provenance.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_make_artifact("request_source_decision"),
        request_member_decision_ref=_make_artifact("request_member_decision"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_decision_stage=RequestDecisionStage.REQUEST,
        endpoint_code="MFDS_LABEL_ENDPOINT",
        operation_code=None,
    )
    return AuthenticatedGuideRetrievalSelection(hit=hit, binding=binding)


class _FakeReader:
    """Read-only stub keyed by the (index id, chunk id) lookup identity."""

    def __init__(
        self,
        observations: dict[tuple[UUID, UUID], KnowledgeChunkContentObservation | None],
    ) -> None:
        self._observations = observations
        self.calls: list[tuple[UUID, UUID]] = []

    async def read_content(
        self,
        *,
        knowledge_index_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> KnowledgeChunkContentObservation | None:
        self.calls.append((knowledge_index_id, knowledge_chunk_id))
        return self._observations.get((knowledge_index_id, knowledge_chunk_id))


class _RaisingReader:
    def __init__(self, error: BaseException) -> None:
        self._error = error
        self.calls = 0

    async def read_content(
        self,
        *,
        knowledge_index_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> KnowledgeChunkContentObservation | None:
        self.calls += 1
        raise self._error


def _observation(
    selection: AuthenticatedGuideRetrievalSelection,
    *,
    content: str = _RAW_CONTENT_SENTINEL,
    provenance: ProductionEvidenceProvenance | None = None,
) -> KnowledgeChunkContentObservation:
    return KnowledgeChunkContentObservation(
        provenance=provenance if provenance is not None else selection.hit.provenance,
        content_text=SensitiveText(content),
    )


def _reader_for(
    *selections: AuthenticatedGuideRetrievalSelection,
    content: str = _RAW_CONTENT_SENTINEL,
) -> _FakeReader:
    return _FakeReader(
        {
            (s.hit.provenance.knowledge_index_id, s.hit.provenance.knowledge_chunk_id): _observation(s, content=content)
            for s in selections
        }
    )


# ---------------------------------------------------------------------------
# 1-3. Successful hydration
# ---------------------------------------------------------------------------


async def test_single_selection_hydrates_with_exact_content() -> None:
    selection = _make_selection()
    reader = _reader_for(selection)

    outcome = await hydrate_guide_retrieval_content((selection,), reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.HYDRATED
    assert outcome.reasons == ()
    assert len(outcome.selections) == 1
    hydrated = outcome.selections[0]
    assert hydrated.selection is selection
    assert hydrated.content_text.reveal() == _RAW_CONTENT_SENTINEL
    assert reader.calls == [(selection.hit.provenance.knowledge_index_id, selection.hit.provenance.knowledge_chunk_id)]


async def test_multiple_selections_preserve_production_input_order() -> None:
    first = _make_selection(external_document_id="DOC-1", chunk_index=0, fusion_rank=1)
    second = _make_selection(external_document_id="DOC-2", chunk_index=7, fusion_rank=2)
    third = _make_selection(external_document_id="DOC-3", chunk_index=3, fusion_rank=3)
    ordered = (third, first, second)
    reader = _reader_for(*ordered)

    outcome = await hydrate_guide_retrieval_content(ordered, reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.HYDRATED
    assert tuple(h.selection for h in outcome.selections) == ordered
    assert reader.calls == [(s.hit.provenance.knowledge_index_id, s.hit.provenance.knowledge_chunk_id) for s in ordered]


async def test_distinct_chunks_of_the_same_source_member_all_hydrate() -> None:
    first = _make_selection(external_document_id="DOC-1", chunk_index=0)
    second = _make_selection(external_document_id="DOC-1", chunk_index=1)
    assert first.hit.provenance.source_snapshot_member_id == second.hit.provenance.source_snapshot_member_id
    reader = _reader_for(first, second)

    outcome = await hydrate_guide_retrieval_content((first, second), reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.HYDRATED
    assert len(outcome.selections) == 2


# ---------------------------------------------------------------------------
# 4. Not found
# ---------------------------------------------------------------------------


async def test_missing_persisted_content_rejects_whole_outcome() -> None:
    selection = _make_selection()
    reader = _FakeReader({})

    outcome = await hydrate_guide_retrieval_content((selection,), reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.REJECTED
    assert outcome.reasons == (GuideContentHydrationReason.CONTENT_NOT_FOUND,)
    assert outcome.selections == ()


# ---------------------------------------------------------------------------
# 5-16. Every provenance field mismatch closes as PROVENANCE_MISMATCH
# ---------------------------------------------------------------------------

_PROVENANCE_FIELD_MUTATIONS: tuple[tuple[str, object], ...] = (
    ("knowledge_index_id", UUID("71100000-0000-4000-8000-0000000000ff")),
    ("knowledge_chunk_id", UUID("71100000-0000-4000-8000-0000000000fe")),
    ("index_code", "OTHER_INDEX"),
    ("index_version", "v2"),
    ("index_configuration_hash", "5" * 64),
    ("source_snapshot_id", UUID("71100000-0000-4000-8000-0000000000fd")),
    ("source_snapshot_member_id", UUID("71100000-0000-4000-8000-0000000000fc")),
    ("source_code", "OTHER_SOURCE"),
    ("source_version", "2026.2"),
    ("canonical_checksum", "d" * 64),
    ("external_document_id", "DOC-OTHER"),
    ("chunk_index", 99),
    ("locator", "$.records[99]"),
    ("canonicalization_spec_version", "canonical-v2"),
    ("normalization_version", "normalization-v2"),
)


@pytest.mark.parametrize(("field", "value"), _PROVENANCE_FIELD_MUTATIONS, ids=lambda v: str(v)[:40])
async def test_any_provenance_field_mismatch_rejects_fail_closed(field: str, value: object) -> None:
    selection = _make_selection()
    expected = selection.hit.provenance
    observed = replace(expected, **{field: value})
    assert observed != expected
    reader = _FakeReader(
        {
            (expected.knowledge_index_id, expected.knowledge_chunk_id): KnowledgeChunkContentObservation(
                provenance=observed,
                content_text=SensitiveText(_RAW_CONTENT_SENTINEL),
            )
        }
    )

    outcome = await hydrate_guide_retrieval_content((selection,), reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.REJECTED
    assert outcome.reasons == (GuideContentHydrationReason.PROVENANCE_MISMATCH,)
    assert outcome.selections == ()


async def test_persisted_provenance_content_hash_mismatch_is_provenance_mismatch() -> None:
    """A drifted persisted `content_hash` differs from the expected provenance first."""
    selection = _make_selection()
    expected = selection.hit.provenance
    observed = replace(expected, content_hash="e" * 64)
    reader = _FakeReader(
        {
            (expected.knowledge_index_id, expected.knowledge_chunk_id): KnowledgeChunkContentObservation(
                provenance=observed,
                content_text=SensitiveText(_RAW_CONTENT_SENTINEL),
            )
        }
    )

    outcome = await hydrate_guide_retrieval_content((selection,), reader=reader)

    assert outcome.reasons == (GuideContentHydrationReason.PROVENANCE_MISMATCH,)


# ---------------------------------------------------------------------------
# 17. Content hash verification
# ---------------------------------------------------------------------------


async def test_persisted_body_not_matching_expected_hash_rejects() -> None:
    selection = _make_selection()
    reader = _reader_for(selection, content="TAMPERED_" + _RAW_CONTENT_SENTINEL)

    outcome = await hydrate_guide_retrieval_content((selection,), reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.REJECTED
    assert outcome.reasons == (GuideContentHydrationReason.CONTENT_HASH_MISMATCH,)
    assert outcome.selections == ()


async def test_content_hash_is_computed_over_utf8_bytes() -> None:
    """A multi-byte body verifies only against its UTF-8 digest."""
    body = "아세트아미노펜 500mg"
    selection = _make_selection(content=body)
    assert selection.hit.provenance.content_hash == hashlib.sha256(body.encode("utf-8")).hexdigest()
    reader = _reader_for(selection, content=body)

    outcome = await hydrate_guide_retrieval_content((selection,), reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.HYDRATED
    assert outcome.selections[0].content_text.reveal() == body


async def test_whitespace_variant_body_is_not_silently_normalized() -> None:
    selection = _make_selection()
    reader = _reader_for(selection, content=f"  {_RAW_CONTENT_SENTINEL}  ")

    outcome = await hydrate_guide_retrieval_content((selection,), reader=reader)

    assert outcome.reasons == (GuideContentHydrationReason.CONTENT_HASH_MISMATCH,)


# ---------------------------------------------------------------------------
# 18. Atomicity
# ---------------------------------------------------------------------------


async def test_second_selection_failure_yields_no_partial_hydration() -> None:
    first = _make_selection(external_document_id="DOC-1", chunk_index=0)
    second = _make_selection(external_document_id="DOC-2", chunk_index=1)
    third = _make_selection(external_document_id="DOC-3", chunk_index=2)
    expected_second = second.hit.provenance
    reader = _FakeReader(
        {
            (first.hit.provenance.knowledge_index_id, first.hit.provenance.knowledge_chunk_id): _observation(first),
            (expected_second.knowledge_index_id, expected_second.knowledge_chunk_id): KnowledgeChunkContentObservation(
                provenance=replace(expected_second, locator="$.records[999]"),
                content_text=SensitiveText(_RAW_CONTENT_SENTINEL),
            ),
            (third.hit.provenance.knowledge_index_id, third.hit.provenance.knowledge_chunk_id): _observation(third),
        }
    )

    outcome = await hydrate_guide_retrieval_content((first, second, third), reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.REJECTED
    assert outcome.reasons == (GuideContentHydrationReason.PROVENANCE_MISMATCH,)
    assert outcome.selections == ()
    # The third selection is never read: rejection is fail-fast, not best-effort.
    assert len(reader.calls) == 2


# ---------------------------------------------------------------------------
# 19-20. Reader exception boundary
# ---------------------------------------------------------------------------


async def test_typed_reader_error_closes_as_content_reader_error() -> None:
    selection = _make_selection()
    reader = _RaisingReader(KnowledgeChunkContentReaderError("knowledge chunk content read failed"))

    outcome = await hydrate_guide_retrieval_content((selection,), reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.REJECTED
    assert outcome.reasons == (GuideContentHydrationReason.CONTENT_READER_ERROR,)
    assert outcome.selections == ()


async def test_unexpected_runtime_error_propagates_unhandled() -> None:
    selection = _make_selection()
    reader = _RaisingReader(RuntimeError("programming error"))

    with pytest.raises(RuntimeError):
        await hydrate_guide_retrieval_content((selection,), reader=reader)


async def test_malformed_reader_payload_closes_as_content_reader_error() -> None:
    selection = _make_selection()
    expected = selection.hit.provenance
    reader = _FakeReader(
        {
            (expected.knowledge_index_id, expected.knowledge_chunk_id): KnowledgeChunkContentObservation(
                provenance=expected,
                content_text=_RAW_CONTENT_SENTINEL,  # type: ignore[arg-type]
            )
        }
    )

    outcome = await hydrate_guide_retrieval_content((selection,), reader=reader)

    assert outcome.reasons == (GuideContentHydrationReason.CONTENT_READER_ERROR,)


# ---------------------------------------------------------------------------
# Input structure validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_input", [(), [], None, ("not-a-selection",)], ids=["empty", "list", "none", "wrong-type"])
async def test_invalid_input_structure_rejects(bad_input: object) -> None:
    reader = _FakeReader({})

    outcome = await hydrate_guide_retrieval_content(bad_input, reader=reader)  # type: ignore[arg-type]

    assert outcome.decision is GuideContentHydrationDecision.REJECTED
    assert outcome.reasons == (GuideContentHydrationReason.REQUEST_INVALID,)
    assert reader.calls == []


# ---------------------------------------------------------------------------
# 21. Sensitive content is never revealed
# ---------------------------------------------------------------------------


async def test_hydrated_content_is_redacted_in_representations_and_logs(caplog) -> None:
    selection = _make_selection()
    reader = _reader_for(selection)

    with caplog.at_level(logging.DEBUG):
        outcome = await hydrate_guide_retrieval_content((selection,), reader=reader)

    hydrated = outcome.selections[0]
    assert _RAW_CONTENT_SENTINEL not in repr(hydrated)
    assert _RAW_CONTENT_SENTINEL not in repr(outcome)
    assert _RAW_CONTENT_SENTINEL not in str(hydrated.content_text)
    assert _RAW_CONTENT_SENTINEL not in caplog.text
    assert hydrated.content_text.reveal() == _RAW_CONTENT_SENTINEL


async def test_rejection_reasons_never_carry_raw_content() -> None:
    selection = _make_selection()
    reader = _reader_for(selection, content="TAMPERED_" + _RAW_CONTENT_SENTINEL)

    outcome = await hydrate_guide_retrieval_content((selection,), reader=reader)

    assert "TAMPERED_" not in repr(outcome)
    assert outcome.reasons == (GuideContentHydrationReason.CONTENT_HASH_MISMATCH,)
