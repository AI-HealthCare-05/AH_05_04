"""Unit tests for Guide Evidence Handoff Contract Kernel (#180 Prerequisite)."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from ai_worker.tasks.rag.claim_citation_validator import SourceMemberKind
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, QueryFingerprint, SensitiveText
from ai_worker.tasks.rag.evidence_search import (
    FractionReceipt,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    StableCoordinate,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    GuideEvidenceHandoffBuildDecision,
    GuideEvidenceHandoffReason,
    GuideEvidenceHandoffRequest,
    GuideEvidenceHandoffVerificationDecision,
    GuideEvidenceSelectionRequest,
    ObservedDecisionOutcome,
    RequestDecisionStage,
    RequestSourceMemberBinding,
    VerifiedGuideEvidenceHandoff,
    VerifiedGuideEvidenceSelection,
    build_guide_evidence_handoff,
    canonical_jcs_bytes,
    compute_guide_evidence_handoff_hash,
    verify_guide_evidence_handoff,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    ProductionSearchReceipt,
    RetrievalExecutionStatus,
    compute_production_search_receipt,
    compute_selection_manifest_hash,
)


def _make_artifact(code: str, version: str = "v1", digest: str | None = None) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(
        artifact_code=code,
        version=version,
        content_sha256=digest or ("a" * 64),
    )


# ==============================================================================
# Rule 1: Canonical JCS Serialization & Golden Vector Tests
# ==============================================================================


def test_canonical_jcs_utf16_key_ordering() -> None:
    # In UTF-16, \U00010000 encodes to (0xD800, 0xDC00), which sorts before \uE000
    # In Python code point sorting, '\U00010000' > '\uE000'
    obj = {"\ue000": 1, "\U00010000": 2, "a": 3}
    encoded = canonical_jcs_bytes(obj)
    # RFC 8785 UTF-16 code unit order: "a" (0x61), "\U00010000" (0xD800), "\uE000" (0xE000)
    expected = b'{"a":3,"\xf0\x90\x80\x80":2,"\xee\x80\x80":1}'
    assert encoded == expected


def test_canonical_jcs_explicit_null_and_empty_list() -> None:
    obj: dict[str, object] = {"b_null": None, "a_empty": []}
    encoded = canonical_jcs_bytes(obj)
    assert encoded == b'{"a_empty":[],"b_null":null}'


def test_canonical_jcs_safe_integer_range() -> None:
    max_safe = (2**53) - 1
    min_safe = -(2**53) + 1
    obj = {"max": max_safe, "min": min_safe}
    encoded = canonical_jcs_bytes(obj)
    assert f'"max":{max_safe}'.encode() in encoded
    assert f'"min":{min_safe}'.encode() in encoded

    with pytest.raises(ValueError, match="JSON_NUMBER_INVALID"):
        canonical_jcs_bytes({"overflow": 2**53})

    with pytest.raises(ValueError, match="JSON_NUMBER_INVALID"):
        canonical_jcs_bytes({"underflow": -(2**53)})


def test_canonical_jcs_rejects_float() -> None:
    with pytest.raises(ValueError, match="JSON_NUMBER_INVALID"):
        canonical_jcs_bytes({"score": 1.23})


def test_canonical_jcs_rejects_lone_surrogates() -> None:
    with pytest.raises(ValueError, match="JSON_UNICODE_INVALID"):
        canonical_jcs_bytes({"lone": "\ud800"})

    with pytest.raises(ValueError, match="JSON_UNICODE_INVALID"):
        canonical_jcs_bytes({"\udfff": "bad_key"})


# ==============================================================================
# Rule 2: Shape & Malformed Request Validation
# ==============================================================================


def test_rejects_malformed_request_not_instance() -> None:
    outcome = build_guide_evidence_handoff("not_a_request")  # type: ignore[arg-type]
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert outcome.reasons == (GuideEvidenceHandoffReason.REQUEST_INVALID,)
    assert outcome.handoff is None


def test_rejects_empty_selections() -> None:
    receipt = ProductionSearchReceipt(
        artifact_ref=_make_artifact("receipt"),
        variant="RET-H",
        retrieval_execution_status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code="OK",
        query_fingerprint=QueryFingerprint("sha256", "v1", "0" * 64),
        filter_snapshot_ref=_make_artifact("filter"),
        evidence_index_ref=_make_artifact("index"),
        retrieval_config_ref=_make_artifact("config"),
        adapter_artifact_ref=_make_artifact("adapter"),
        query_embedding_sha256="d" * 64,
        signal_manifest_sha256="1" * 64,
        hit_manifest_sha256="2" * 64,
        selection_manifest_sha256="3" * 64,
    )
    request = GuideEvidenceHandoffRequest(
        retrieval_receipt=receipt,
        selections=(),
        evaluated_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
    )
    outcome = build_guide_evidence_handoff(request)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.REQUEST_INVALID in outcome.reasons
    assert outcome.handoff is None


# ==============================================================================
# Fixtures for Multi-selection Tests
# ==============================================================================


def _make_search_hit(
    *,
    rank: int,
    source_code: str = "GUIDELINE_SOURCE",
    source_version: str = "2026.1",
    chunk_id: UUID | None = None,
    member_id: UUID | None = None,
    snapshot_id: UUID | None = None,
    external_doc_id: str = "DOC-1",
    chunk_index: int = 0,
    content_text: str = "sample text",
) -> tuple[ProductionSearchHit, SensitiveText]:
    cid = chunk_id or uuid4()
    mid = member_id or uuid4()
    sid = snapshot_id or uuid4()
    content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
    provenance = ProductionEvidenceProvenance(
        knowledge_index_id=uuid4(),
        index_code="GUIDELINE_INDEX",
        index_version="v1",
        index_configuration_hash="4" * 64,
        knowledge_chunk_id=cid,
        source_snapshot_id=sid,
        source_snapshot_member_id=mid,
        source_code=source_code,
        source_version=source_version,
        canonical_checksum="c" * 64,
        external_document_id=external_doc_id,
        chunk_index=chunk_index,
        locator=f"doc:{external_doc_id}#p1",
        content_hash=content_hash,
        canonicalization_spec_version="v1",
        normalization_version="v1",
    )
    coordinate = StableCoordinate(
        source_code=source_code,
        source_version=source_version,
        external_document_id=external_doc_id,
        chunk_index=chunk_index,
    )
    hit = ProductionSearchHit(
        provenance=provenance,
        coordinate=coordinate,
        exact_hit=False,
        observed_trigram_score=None,
        observed_fts_score=None,
        observed_dense_score=None,
        lexical_rank=None,
        dense_rank=None,
        fusion_rank=rank,
        fraction_receipt=FractionReceipt("1", "60"),
        is_eligible_for_future_reranker=True,
    )
    return hit, SensitiveText(content_text)


def _make_valid_handoff_components() -> tuple[GuideEvidenceHandoffRequest, ProductionSearchReceipt]:
    hit1, text1 = _make_search_hit(rank=1, external_doc_id="DOC-1", chunk_index=0, content_text="Evidence 1")
    hit2, text2 = _make_search_hit(rank=2, external_doc_id="DOC-2", chunk_index=1, content_text="Evidence 2")

    manifest_sha = compute_selection_manifest_hash([hit1, hit2])
    receipt = compute_production_search_receipt(
        variant="RET-H",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code="OK",
        query_fingerprint=QueryFingerprint("sha256", "v1", "0" * 64),
        filter_snapshot_hash="f" * 64,
        evidence_index_config_hash="e" * 64,
        retrieval_config_hash="3" * 64,
        adapter_artifact_ref=_make_artifact("adapter"),
        query_embedding_sha256="d" * 64,
        signal_manifest_sha256="1" * 64,
        hit_manifest_sha256="2" * 64,
        selection_manifest_sha256=manifest_sha,
    )
    receipt_ref = receipt.artifact_ref

    valid_from = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
    valid_until = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    evaluated_at = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)

    binding1 = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard-1"),
        source_snapshot_id=hit1.provenance.source_snapshot_id,
        source_snapshot_member_id=hit1.provenance.source_snapshot_member_id,
        source_code=hit1.provenance.source_code,
        source_version=hit1.provenance.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_make_artifact("src-dec-1"),
        request_member_decision_ref=_make_artifact("mem-dec-1"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        endpoint_code="guide_api",
        operation_code="get_guide",
    )
    sel1 = GuideEvidenceSelectionRequest(
        hit=hit1,
        binding=binding1,
        evidence_key="evidence:1",
        content_text=text1,
        retrieval_receipt_ref=receipt_ref,
        eligibility_receipt_ref=_make_artifact("elig-1"),
        assessment_artifact_ref=_make_artifact("assess-1"),
        verifier_artifact_ref=_make_artifact("verif-1"),
        assessment_valid_from=valid_from,
        assessment_valid_until=valid_until,
    )

    binding2 = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard-1"),
        source_snapshot_id=hit2.provenance.source_snapshot_id,
        source_snapshot_member_id=hit2.provenance.source_snapshot_member_id,
        source_code=hit2.provenance.source_code,
        source_version=hit2.provenance.source_version,
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        request_source_decision_ref=_make_artifact("src-dec-2"),
        request_member_decision_ref=_make_artifact("mem-dec-2"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        artifact_code="guide_doc",
        artifact_version="v1",
    )
    sel2 = GuideEvidenceSelectionRequest(
        hit=hit2,
        binding=binding2,
        evidence_key="evidence:2",
        content_text=text2,
        retrieval_receipt_ref=receipt_ref,
        eligibility_receipt_ref=_make_artifact("elig-2"),
        assessment_artifact_ref=_make_artifact("assess-2"),
        verifier_artifact_ref=_make_artifact("verif-2"),
        assessment_valid_from=valid_from,
        assessment_valid_until=valid_until,
    )

    req = GuideEvidenceHandoffRequest(
        retrieval_receipt=receipt,
        selections=(sel1, sel2),
        evaluated_at=evaluated_at,
    )
    return req, receipt


# ==============================================================================
# Rule 3: Selection Order & Duplicates
# ==============================================================================


def test_rejects_non_canonical_selection_order() -> None:
    req, _ = _make_valid_handoff_components()
    # Reverse the order so rank 2 comes before rank 1
    reversed_req = GuideEvidenceHandoffRequest(
        retrieval_receipt=req.retrieval_receipt,
        selections=(req.selections[1], req.selections[0]),
        evaluated_at=req.evaluated_at,
    )
    outcome = build_guide_evidence_handoff(reversed_req)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.SELECTION_ORDER_INVALID in outcome.reasons


def test_rejects_duplicate_evidence_key() -> None:
    req, _ = _make_valid_handoff_components()
    # Make sel2 have same evidence_key as sel1
    dup_sel2 = GuideEvidenceSelectionRequest(
        hit=req.selections[1].hit,
        binding=req.selections[1].binding,
        evidence_key=req.selections[0].evidence_key,
        content_text=req.selections[1].content_text,
        retrieval_receipt_ref=req.selections[1].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[1].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[1].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[1].verifier_artifact_ref,
        assessment_valid_from=req.selections[1].assessment_valid_from,
        assessment_valid_until=req.selections[1].assessment_valid_until,
    )
    dup_req = GuideEvidenceHandoffRequest(
        retrieval_receipt=req.retrieval_receipt,
        selections=(req.selections[0], dup_sel2),
        evaluated_at=req.evaluated_at,
    )
    outcome = build_guide_evidence_handoff(dup_req)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.DUPLICATE_EVIDENCE_KEY in outcome.reasons


def test_rejects_duplicate_stable_coordinate_or_chunk() -> None:
    req, _ = _make_valid_handoff_components()
    # Create sel2 with duplicate coordinate and chunk id but rank 2
    dup_hit, dup_text = _make_search_hit(
        rank=2,
        external_doc_id=req.selections[0].hit.coordinate.external_document_id,
        chunk_index=req.selections[0].hit.coordinate.chunk_index,
        chunk_id=req.selections[0].hit.provenance.knowledge_chunk_id,
        member_id=req.selections[0].hit.provenance.source_snapshot_member_id,
        snapshot_id=req.selections[0].hit.provenance.source_snapshot_id,
        content_text="duplicate content",
    )
    dup_sel2 = GuideEvidenceSelectionRequest(
        hit=dup_hit,
        binding=req.selections[0].binding,
        evidence_key="evidence:dup",
        content_text=dup_text,
        retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    dup_req = GuideEvidenceHandoffRequest(
        retrieval_receipt=req.retrieval_receipt,
        selections=(req.selections[0], dup_sel2),
        evaluated_at=req.evaluated_at,
    )
    outcome = build_guide_evidence_handoff(dup_req)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert (
        GuideEvidenceHandoffReason.DUPLICATE_STABLE_COORDINATE in outcome.reasons
        or GuideEvidenceHandoffReason.DUPLICATE_KNOWLEDGE_CHUNK_ID in outcome.reasons
    )


def test_allows_multiple_chunks_under_same_source_snapshot_member() -> None:
    common_member_id = uuid4()
    common_snapshot_id = uuid4()
    hit1, text1 = _make_search_hit(
        rank=1,
        external_doc_id="DOC-1",
        chunk_index=0,
        member_id=common_member_id,
        snapshot_id=common_snapshot_id,
        content_text="Chunk 0 from member",
    )
    hit2, text2 = _make_search_hit(
        rank=2,
        external_doc_id="DOC-1",
        chunk_index=1,
        member_id=common_member_id,
        snapshot_id=common_snapshot_id,
        content_text="Chunk 1 from same member",
    )
    manifest_sha = compute_selection_manifest_hash([hit1, hit2])
    receipt = compute_production_search_receipt(
        variant="RET-H",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code="OK",
        query_fingerprint=QueryFingerprint("sha256", "v1", "0" * 64),
        filter_snapshot_hash="f" * 64,
        evidence_index_config_hash="e" * 64,
        retrieval_config_hash="3" * 64,
        adapter_artifact_ref=_make_artifact("adapter"),
        query_embedding_sha256="d" * 64,
        signal_manifest_sha256="1" * 64,
        hit_manifest_sha256="2" * 64,
        selection_manifest_sha256=manifest_sha,
    )
    binding = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard-1"),
        request_operation_code="get_guide",
        source_snapshot_id=common_snapshot_id,
        source_snapshot_member_id=common_member_id,
        source_code=hit1.provenance.source_code,
        source_version=hit1.provenance.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_make_artifact("src-dec"),
        request_member_decision_ref=_make_artifact("mem-dec"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        endpoint_code="guide_api",
        operation_code="get_guide",
    )
    sel1 = GuideEvidenceSelectionRequest(
        hit=hit1,
        binding=binding,
        evidence_key="evidence:chunk0",
        content_text=text1,
        retrieval_receipt_ref=receipt.artifact_ref,
        eligibility_receipt_ref=_make_artifact("elig-1"),
        assessment_artifact_ref=_make_artifact("assess-1"),
        verifier_artifact_ref=_make_artifact("verif-1"),
        assessment_valid_from=datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
        assessment_valid_until=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
    )
    sel2 = GuideEvidenceSelectionRequest(
        hit=hit2,
        binding=binding,
        evidence_key="evidence:chunk1",
        content_text=text2,
        retrieval_receipt_ref=receipt.artifact_ref,
        eligibility_receipt_ref=_make_artifact("elig-2"),
        assessment_artifact_ref=_make_artifact("assess-2"),
        verifier_artifact_ref=_make_artifact("verif-2"),
        assessment_valid_from=datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
        assessment_valid_until=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
    )
    req = GuideEvidenceHandoffRequest(
        retrieval_receipt=receipt,
        selections=(sel1, sel2),
        evaluated_at=datetime(2026, 9, 15, 12, 0, tzinfo=UTC),
    )
    outcome = build_guide_evidence_handoff(req)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.BUILT
    assert outcome.handoff is not None


def test_rejects_rank_out_of_range() -> None:
    for bad_rank in [0, 6]:
        hit, text = _make_search_hit(rank=bad_rank)
        manifest_sha = compute_selection_manifest_hash([hit])
        receipt = compute_production_search_receipt(
            variant="RET-H",
            status=RetrievalExecutionStatus.SUCCEEDED,
            diagnostic_code="OK",
            query_fingerprint=QueryFingerprint("sha256", "v1", "0" * 64),
            filter_snapshot_hash="f" * 64,
            evidence_index_config_hash="e" * 64,
            retrieval_config_hash="3" * 64,
            adapter_artifact_ref=_make_artifact("adapter"),
            query_embedding_sha256="d" * 64,
            signal_manifest_sha256="1" * 64,
            hit_manifest_sha256="2" * 64,
            selection_manifest_sha256=manifest_sha,
        )
        binding = RequestSourceMemberBinding(
            request_guard_ref=_make_artifact("guard-1"),
            request_operation_code="get_guide",
            source_snapshot_id=hit.provenance.source_snapshot_id,
            source_snapshot_member_id=hit.provenance.source_snapshot_member_id,
            source_code=hit.provenance.source_code,
            source_version=hit.provenance.source_version,
            member_kind=SourceMemberKind.ENDPOINT_OPERATION,
            request_source_decision_ref=_make_artifact("src-dec"),
            request_member_decision_ref=_make_artifact("mem-dec"),
            observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
            observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
            endpoint_code="guide_api",
            operation_code="get_guide",
        )
        sel = GuideEvidenceSelectionRequest(
            hit=hit,
            binding=binding,
            evidence_key="evidence:bad_rank",
            content_text=text,
            retrieval_receipt_ref=receipt.artifact_ref,
            eligibility_receipt_ref=_make_artifact("elig"),
            assessment_artifact_ref=_make_artifact("assess"),
            verifier_artifact_ref=_make_artifact("verif"),
            assessment_valid_from=datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
            assessment_valid_until=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
        )
        outcome = build_guide_evidence_handoff(
            GuideEvidenceHandoffRequest(receipt, (sel,), datetime(2026, 9, 15, 12, 0, tzinfo=UTC))
        )
        assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
        assert GuideEvidenceHandoffReason.SELECTION_ORDER_INVALID in outcome.reasons


def test_allows_rank_gaps_in_strictly_increasing_order() -> None:
    hit1, text1 = _make_search_hit(rank=1, external_doc_id="DOC-1", chunk_index=0)
    hit2, text2 = _make_search_hit(rank=4, external_doc_id="DOC-2", chunk_index=1)
    manifest_sha = compute_selection_manifest_hash([hit1, hit2])
    receipt = compute_production_search_receipt(
        variant="RET-H",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code="OK",
        query_fingerprint=QueryFingerprint("sha256", "v1", "0" * 64),
        filter_snapshot_hash="f" * 64,
        evidence_index_config_hash="e" * 64,
        retrieval_config_hash="3" * 64,
        adapter_artifact_ref=_make_artifact("adapter"),
        query_embedding_sha256="d" * 64,
        signal_manifest_sha256="1" * 64,
        hit_manifest_sha256="2" * 64,
        selection_manifest_sha256=manifest_sha,
    )
    b1 = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard-1"),
        request_operation_code="get_guide",
        source_snapshot_id=hit1.provenance.source_snapshot_id,
        source_snapshot_member_id=hit1.provenance.source_snapshot_member_id,
        source_code=hit1.provenance.source_code,
        source_version=hit1.provenance.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_make_artifact("src-1"),
        request_member_decision_ref=_make_artifact("mem-1"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        endpoint_code="e",
        operation_code="op",
    )
    b2 = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard-1"),
        request_operation_code="get_guide",
        source_snapshot_id=hit2.provenance.source_snapshot_id,
        source_snapshot_member_id=hit2.provenance.source_snapshot_member_id,
        source_code=hit2.provenance.source_code,
        source_version=hit2.provenance.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_make_artifact("src-2"),
        request_member_decision_ref=_make_artifact("mem-2"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        endpoint_code="e",
        operation_code="op",
    )
    sel1 = GuideEvidenceSelectionRequest(
        hit=hit1,
        binding=b1,
        evidence_key="k1",
        content_text=text1,
        retrieval_receipt_ref=receipt.artifact_ref,
        eligibility_receipt_ref=_make_artifact("e1"),
        assessment_artifact_ref=_make_artifact("a1"),
        verifier_artifact_ref=_make_artifact("v1"),
        assessment_valid_from=datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
        assessment_valid_until=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
    )
    sel2 = GuideEvidenceSelectionRequest(
        hit=hit2,
        binding=b2,
        evidence_key="k2",
        content_text=text2,
        retrieval_receipt_ref=receipt.artifact_ref,
        eligibility_receipt_ref=_make_artifact("e2"),
        assessment_artifact_ref=_make_artifact("a2"),
        verifier_artifact_ref=_make_artifact("v2"),
        assessment_valid_from=datetime(2026, 9, 1, 0, 0, tzinfo=UTC),
        assessment_valid_until=datetime(2026, 10, 1, 0, 0, tzinfo=UTC),
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(receipt, (sel1, sel2), datetime(2026, 9, 15, 12, 0, tzinfo=UTC))
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.BUILT
    assert outcome.handoff is not None


def test_rejects_coordinate_provenance_mismatch() -> None:
    req, _ = _make_valid_handoff_components()
    bad_coord = StableCoordinate(
        source_code=req.selections[0].hit.provenance.source_code,
        source_version=req.selections[0].hit.provenance.source_version,
        external_document_id=req.selections[0].hit.provenance.external_document_id,
        chunk_index=999,  # prov has 0!
    )
    bad_hit = ProductionSearchHit(
        provenance=req.selections[0].hit.provenance,
        coordinate=bad_coord,
        exact_hit=False,
        observed_trigram_score=None,
        observed_fts_score=None,
        observed_dense_score=None,
        lexical_rank=None,
        dense_rank=None,
        fusion_rank=req.selections[0].hit.fusion_rank,
        fraction_receipt=req.selections[0].hit.fraction_receipt,
        is_eligible_for_future_reranker=True,
    )
    bad_sel = GuideEvidenceSelectionRequest(
        hit=bad_hit,
        binding=req.selections[0].binding,
        evidence_key=req.selections[0].evidence_key,
        content_text=req.selections[0].content_text,
        retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (bad_sel, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.COORDINATE_PROVENANCE_MISMATCH in outcome.reasons


# ==============================================================================
# Rule 4: Selection Manifest & Retrieval Receipt Verification
# ==============================================================================


def test_rejects_retrieval_receipt_mismatch() -> None:
    req, _ = _make_valid_handoff_components()
    # Point selection's retrieval_receipt_ref to another receipt
    mismatched_sel = GuideEvidenceSelectionRequest(
        hit=req.selections[0].hit,
        binding=req.selections[0].binding,
        evidence_key=req.selections[0].evidence_key,
        content_text=req.selections[0].content_text,
        retrieval_receipt_ref=_make_artifact("other_receipt"),
        eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    mismatched_req = GuideEvidenceHandoffRequest(
        retrieval_receipt=req.retrieval_receipt,
        selections=(mismatched_sel, req.selections[1]),
        evaluated_at=req.evaluated_at,
    )
    outcome = build_guide_evidence_handoff(mismatched_req)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH in outcome.reasons


def test_rejects_selection_manifest_mismatch() -> None:
    req, receipt = _make_valid_handoff_components()
    # Tamper with the receipt's selection_manifest_sha256
    tampered_receipt = ProductionSearchReceipt(
        artifact_ref=receipt.artifact_ref,
        variant=receipt.variant,
        retrieval_execution_status=receipt.retrieval_execution_status,
        diagnostic_code=receipt.diagnostic_code,
        query_fingerprint=receipt.query_fingerprint,
        filter_snapshot_ref=receipt.filter_snapshot_ref,
        evidence_index_ref=receipt.evidence_index_ref,
        retrieval_config_ref=receipt.retrieval_config_ref,
        adapter_artifact_ref=receipt.adapter_artifact_ref,
        query_embedding_sha256=receipt.query_embedding_sha256,
        signal_manifest_sha256=receipt.signal_manifest_sha256,
        hit_manifest_sha256=receipt.hit_manifest_sha256,
        selection_manifest_sha256="0" * 64,  # forged / mismatched
    )
    tampered_req = GuideEvidenceHandoffRequest(
        retrieval_receipt=tampered_receipt,
        selections=req.selections,
        evaluated_at=req.evaluated_at,
    )
    outcome = build_guide_evidence_handoff(tampered_req)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.SELECTION_MANIFEST_MISMATCH in outcome.reasons


def test_rejects_retrieval_receipt_invalid_variant() -> None:
    req, receipt = _make_valid_handoff_components()
    tampered_receipt = compute_production_search_receipt(
        variant="RET-D",  # not RET-H!
        status=receipt.retrieval_execution_status,
        diagnostic_code=receipt.diagnostic_code,
        query_fingerprint=receipt.query_fingerprint,
        filter_snapshot_hash=receipt.filter_snapshot_ref.content_sha256,
        evidence_index_config_hash=receipt.evidence_index_ref.content_sha256,
        retrieval_config_hash=receipt.retrieval_config_ref.content_sha256,
        adapter_artifact_ref=receipt.adapter_artifact_ref,
        query_embedding_sha256=receipt.query_embedding_sha256,
        signal_manifest_sha256=receipt.signal_manifest_sha256,
        hit_manifest_sha256=receipt.hit_manifest_sha256,
        selection_manifest_sha256=receipt.selection_manifest_sha256,
    )
    selections = tuple(
        GuideEvidenceSelectionRequest(
            hit=s.hit,
            binding=s.binding,
            evidence_key=s.evidence_key,
            content_text=s.content_text,
            retrieval_receipt_ref=tampered_receipt.artifact_ref,
            eligibility_receipt_ref=s.eligibility_receipt_ref,
            assessment_artifact_ref=s.assessment_artifact_ref,
            verifier_artifact_ref=s.verifier_artifact_ref,
            assessment_valid_from=s.assessment_valid_from,
            assessment_valid_until=s.assessment_valid_until,
        )
        for s in req.selections
    )
    outcome = build_guide_evidence_handoff(GuideEvidenceHandoffRequest(tampered_receipt, selections, req.evaluated_at))
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH in outcome.reasons


def test_rejects_retrieval_receipt_failed_execution_status() -> None:
    req, receipt = _make_valid_handoff_components()
    tampered_receipt = compute_production_search_receipt(
        variant="RET-H",
        status=RetrievalExecutionStatus.DEPENDENCY_ERROR,  # not SUCCEEDED!
        diagnostic_code="DEPENDENCY_FAILED",
        query_fingerprint=receipt.query_fingerprint,
        filter_snapshot_hash=receipt.filter_snapshot_ref.content_sha256,
        evidence_index_config_hash=receipt.evidence_index_ref.content_sha256,
        retrieval_config_hash=receipt.retrieval_config_ref.content_sha256,
        adapter_artifact_ref=receipt.adapter_artifact_ref,
        query_embedding_sha256=receipt.query_embedding_sha256,
        signal_manifest_sha256=receipt.signal_manifest_sha256,
        hit_manifest_sha256=receipt.hit_manifest_sha256,
        selection_manifest_sha256=receipt.selection_manifest_sha256,
    )
    selections = tuple(
        GuideEvidenceSelectionRequest(
            hit=s.hit,
            binding=s.binding,
            evidence_key=s.evidence_key,
            content_text=s.content_text,
            retrieval_receipt_ref=tampered_receipt.artifact_ref,
            eligibility_receipt_ref=s.eligibility_receipt_ref,
            assessment_artifact_ref=s.assessment_artifact_ref,
            verifier_artifact_ref=s.verifier_artifact_ref,
            assessment_valid_from=s.assessment_valid_from,
            assessment_valid_until=s.assessment_valid_until,
        )
        for s in req.selections
    )
    outcome = build_guide_evidence_handoff(GuideEvidenceHandoffRequest(tampered_receipt, selections, req.evaluated_at))
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH in outcome.reasons


def test_rejects_retrieval_receipt_tampered_fields_mismatching_artifact_ref() -> None:
    req, receipt = _make_valid_handoff_components()
    # Tamper with diagnostic_code without recalculating artifact_ref
    tampered_receipt = ProductionSearchReceipt(
        artifact_ref=receipt.artifact_ref,
        variant=receipt.variant,
        retrieval_execution_status=receipt.retrieval_execution_status,
        diagnostic_code="TAMPERED_CODE",
        query_fingerprint=receipt.query_fingerprint,
        filter_snapshot_ref=receipt.filter_snapshot_ref,
        evidence_index_ref=receipt.evidence_index_ref,
        retrieval_config_ref=receipt.retrieval_config_ref,
        adapter_artifact_ref=receipt.adapter_artifact_ref,
        query_embedding_sha256=receipt.query_embedding_sha256,
        signal_manifest_sha256=receipt.signal_manifest_sha256,
        hit_manifest_sha256=receipt.hit_manifest_sha256,
        selection_manifest_sha256=receipt.selection_manifest_sha256,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(tampered_receipt, req.selections, req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH in outcome.reasons


def test_rejects_missing_or_invalid_evidence_approval_refs() -> None:
    req, _ = _make_valid_handoff_components()
    bad_ref = ImmutableArtifactRef("", "v1", "a" * 64)
    invalid_sel = GuideEvidenceSelectionRequest(
        hit=req.selections[0].hit,
        binding=req.selections[0].binding,
        evidence_key=req.selections[0].evidence_key,
        content_text=req.selections[0].content_text,
        retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
        eligibility_receipt_ref=bad_ref,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    invalid_req = GuideEvidenceHandoffRequest(
        retrieval_receipt=req.retrieval_receipt,
        selections=(invalid_sel, req.selections[1]),
        evaluated_at=req.evaluated_at,
    )
    outcome = build_guide_evidence_handoff(invalid_req)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.OBSERVED_PROVENANCE_REF_REQUIRED in outcome.reasons


# ==============================================================================
# Rule 5: Source & Member Identity Exact-Match (#174 binding vs #178 hit)
# ==============================================================================


def test_rejects_source_or_snapshot_or_member_mismatch() -> None:
    req, _ = _make_valid_handoff_components()
    # Tamper with binding source_code
    b_bad_source = RequestSourceMemberBinding(
        request_guard_ref=req.selections[0].binding.request_guard_ref,
        source_snapshot_id=req.selections[0].binding.source_snapshot_id,
        source_snapshot_member_id=req.selections[0].binding.source_snapshot_member_id,
        source_code="MISMATCHED_SOURCE",
        source_version=req.selections[0].binding.source_version,
        member_kind=req.selections[0].binding.member_kind,
        request_source_decision_ref=req.selections[0].binding.request_source_decision_ref,
        request_member_decision_ref=req.selections[0].binding.request_member_decision_ref,
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        endpoint_code=req.selections[0].binding.endpoint_code,
        operation_code=req.selections[0].binding.operation_code,
    )
    sel_bad_source = GuideEvidenceSelectionRequest(
        hit=req.selections[0].hit,
        binding=b_bad_source,
        evidence_key=req.selections[0].evidence_key,
        content_text=req.selections[0].content_text,
        retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (sel_bad_source, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.SOURCE_MISMATCH in outcome.reasons

    # Tamper with binding source_snapshot_id
    b_bad_snap = RequestSourceMemberBinding(
        request_guard_ref=req.selections[0].binding.request_guard_ref,
        source_snapshot_id=uuid4(),
        source_snapshot_member_id=req.selections[0].binding.source_snapshot_member_id,
        source_code=req.selections[0].binding.source_code,
        source_version=req.selections[0].binding.source_version,
        member_kind=req.selections[0].binding.member_kind,
        request_source_decision_ref=req.selections[0].binding.request_source_decision_ref,
        request_member_decision_ref=req.selections[0].binding.request_member_decision_ref,
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        endpoint_code=req.selections[0].binding.endpoint_code,
        operation_code=req.selections[0].binding.operation_code,
    )
    outcome_snap = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(
            req.retrieval_receipt,
            (
                GuideEvidenceSelectionRequest(
                    hit=req.selections[0].hit,
                    binding=b_bad_snap,
                    evidence_key=req.selections[0].evidence_key,
                    content_text=req.selections[0].content_text,
                    retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
                    eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
                    assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
                    verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
                    assessment_valid_from=req.selections[0].assessment_valid_from,
                    assessment_valid_until=req.selections[0].assessment_valid_until,
                ),
                req.selections[1],
            ),
            req.evaluated_at,
        )
    )
    assert outcome_snap.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.SOURCE_SNAPSHOT_MISMATCH in outcome_snap.reasons


def test_rejects_member_identity_invalid_for_kind() -> None:
    req, _ = _make_valid_handoff_components()
    # ENDPOINT_OPERATION but missing operation_code
    bad_endpoint_binding = RequestSourceMemberBinding(
        request_guard_ref=req.selections[0].binding.request_guard_ref,
        source_snapshot_id=req.selections[0].binding.source_snapshot_id,
        source_snapshot_member_id=req.selections[0].binding.source_snapshot_member_id,
        source_code=req.selections[0].binding.source_code,
        source_version=req.selections[0].binding.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=req.selections[0].binding.request_source_decision_ref,
        request_member_decision_ref=req.selections[0].binding.request_member_decision_ref,
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        endpoint_code=None,  # missing endpoint_code!
        operation_code="get_guide",
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(
            req.retrieval_receipt,
            (
                GuideEvidenceSelectionRequest(
                    hit=req.selections[0].hit,
                    binding=bad_endpoint_binding,
                    evidence_key=req.selections[0].evidence_key,
                    content_text=req.selections[0].content_text,
                    retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
                    eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
                    assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
                    verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
                    assessment_valid_from=req.selections[0].assessment_valid_from,
                    assessment_valid_until=req.selections[0].assessment_valid_until,
                ),
                req.selections[1],
            ),
            req.evaluated_at,
        )
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.MEMBER_IDENTITY_INVALID in outcome.reasons


def test_rejects_observed_decision_not_pass() -> None:
    req, _ = _make_valid_handoff_components()
    fail_decision_binding = RequestSourceMemberBinding(
        request_guard_ref=req.selections[0].binding.request_guard_ref,
        source_snapshot_id=req.selections[0].binding.source_snapshot_id,
        source_snapshot_member_id=req.selections[0].binding.source_snapshot_member_id,
        source_code=req.selections[0].binding.source_code,
        source_version=req.selections[0].binding.source_version,
        member_kind=req.selections[0].binding.member_kind,
        request_source_decision_ref=req.selections[0].binding.request_source_decision_ref,
        request_member_decision_ref=req.selections[0].binding.request_member_decision_ref,
        observed_source_decision_outcome=ObservedDecisionOutcome.FAIL,  # not PASS!
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        endpoint_code=req.selections[0].binding.endpoint_code,
        operation_code=req.selections[0].binding.operation_code,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(
            req.retrieval_receipt,
            (
                GuideEvidenceSelectionRequest(
                    hit=req.selections[0].hit,
                    binding=fail_decision_binding,
                    evidence_key=req.selections[0].evidence_key,
                    content_text=req.selections[0].content_text,
                    retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
                    eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
                    assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
                    verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
                    assessment_valid_from=req.selections[0].assessment_valid_from,
                    assessment_valid_until=req.selections[0].assessment_valid_until,
                ),
                req.selections[1],
            ),
            req.evaluated_at,
        )
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.OBSERVED_DECISION_NOT_PASS in outcome.reasons


# ==============================================================================
# Rule 6: Content Hash Verification & SensitiveText Boundary
# ==============================================================================


def test_rejects_content_hash_mismatch() -> None:
    req, _ = _make_valid_handoff_components()
    # Provide text whose hash doesn't match hit provenance content_hash
    tampered_sel = GuideEvidenceSelectionRequest(
        hit=req.selections[0].hit,
        binding=req.selections[0].binding,
        evidence_key=req.selections[0].evidence_key,
        content_text=SensitiveText("tampered and mismatched text"),
        retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (tampered_sel, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.CONTENT_HASH_MISMATCH in outcome.reasons


def test_sensitivetext_never_leaked_in_repr_or_reasons() -> None:
    sentinel = "PATIENT_CONFIDENTIAL_CLINICAL_NOTE_SENTINEL_XYZ_999"
    hit, text = _make_search_hit(rank=1, content_text=sentinel)
    assert sentinel not in repr(text)
    assert "<redacted>" in repr(text)

    # Even if rejected, sentinel must not appear in outcome or reasons
    bad_binding = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard"),
        source_snapshot_id=uuid4(),  # trigger mismatch
        source_snapshot_member_id=hit.provenance.source_snapshot_member_id,
        source_code=hit.provenance.source_code,
        source_version=hit.provenance.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_make_artifact("src"),
        request_member_decision_ref=_make_artifact("mem"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        endpoint_code="e",
        operation_code="op",
    )
    receipt_ref = _make_artifact("receipt")
    sel = GuideEvidenceSelectionRequest(
        hit=hit,
        binding=bad_binding,
        evidence_key="k1",
        content_text=text,
        retrieval_receipt_ref=receipt_ref,
        eligibility_receipt_ref=_make_artifact("elig"),
        assessment_artifact_ref=_make_artifact("assess"),
        verifier_artifact_ref=_make_artifact("verif"),
        assessment_valid_from=datetime(2026, 9, 1, tzinfo=UTC),
        assessment_valid_until=datetime(2026, 10, 1, tzinfo=UTC),
    )
    receipt = ProductionSearchReceipt(
        artifact_ref=receipt_ref,
        variant="RET-H",
        retrieval_execution_status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code="OK",
        query_fingerprint=QueryFingerprint("sha256", "v1", "0" * 64),
        filter_snapshot_ref=_make_artifact("filter"),
        evidence_index_ref=_make_artifact("index"),
        retrieval_config_ref=_make_artifact("config"),
        adapter_artifact_ref=_make_artifact("adapter"),
        query_embedding_sha256="d" * 64,
        signal_manifest_sha256="1" * 64,
        hit_manifest_sha256="2" * 64,
        selection_manifest_sha256=compute_selection_manifest_hash([hit]),
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(receipt, (sel,), datetime(2026, 9, 15, tzinfo=UTC))
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert sentinel not in repr(outcome)
    assert sentinel not in str(outcome.reasons)


# ==============================================================================
# Rule 7: Freshness & Datetime Awareness
# ==============================================================================


def test_rejects_assessment_not_yet_valid() -> None:
    req, _ = _make_valid_handoff_components()
    future_from = datetime(2026, 9, 20, 0, 0, tzinfo=UTC)  # evaluated_at is 2026-09-15
    future_sel = GuideEvidenceSelectionRequest(
        hit=req.selections[0].hit,
        binding=req.selections[0].binding,
        evidence_key=req.selections[0].evidence_key,
        content_text=req.selections[0].content_text,
        retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=future_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (future_sel, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.ASSESSMENT_NOT_YET_VALID in outcome.reasons


def test_rejects_assessment_expired() -> None:
    req, _ = _make_valid_handoff_components()
    past_until = datetime(2026, 9, 10, 0, 0, tzinfo=UTC)  # evaluated_at is 2026-09-15
    expired_sel = GuideEvidenceSelectionRequest(
        hit=req.selections[0].hit,
        binding=req.selections[0].binding,
        evidence_key=req.selections[0].evidence_key,
        content_text=req.selections[0].content_text,
        retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=past_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (expired_sel, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.ASSESSMENT_EXPIRED in outcome.reasons


def test_rejects_naive_or_non_utc_datetime() -> None:
    req, _ = _make_valid_handoff_components()
    naive_evaluated_at = datetime(2026, 9, 15, 12, 0)  # no tzinfo!
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, req.selections, naive_evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.DATETIME_NOT_AWARE in outcome.reasons


# ==============================================================================
# Rule 8: Successful Aggregate Handoff Build & Two-Input Verification
# ==============================================================================


def test_builds_valid_handoff_and_verifies_hash_projection() -> None:
    req, receipt = _make_valid_handoff_components()
    outcome = build_guide_evidence_handoff(req)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.BUILT
    assert outcome.reasons == ()
    assert outcome.handoff is not None

    handoff = outcome.handoff
    assert handoff.retrieval_receipt_ref == req.retrieval_receipt.artifact_ref
    assert handoff.retrieval_selection_manifest_sha256 == receipt.selection_manifest_sha256
    assert handoff.evaluated_at == req.evaluated_at
    assert len(handoff.selections) == 2

    # Check verified selections
    s0 = handoff.selections[0]
    assert s0.evidence_key == "evidence:1"
    assert s0.final_rank == 1
    assert s0.content_sha256 == req.selections[0].hit.provenance.content_hash
    assert s0.content_text.reveal() == req.selections[0].content_text.reveal()
    assert s0.member_kind == SourceMemberKind.ENDPOINT_OPERATION
    assert s0.endpoint_code == "guide_api"
    assert s0.operation_code == "get_guide"
    assert s0.request_operation_code == "get_guide"
    assert s0.request_decision_stage == RequestDecisionStage.REQUEST
    assert s0.assessment_valid_from == req.selections[0].assessment_valid_from
    assert s0.assessment_valid_until == req.selections[0].assessment_valid_until

    s1 = handoff.selections[1]
    assert s1.evidence_key == "evidence:2"
    assert s1.final_rank == 2
    assert s1.content_sha256 == req.selections[1].hit.provenance.content_hash
    assert s1.member_kind == SourceMemberKind.ARTIFACT_MEMBER
    assert s1.artifact_code == "guide_doc"
    assert s1.artifact_version == "v1"

    # Recompute handoff hash using compute_guide_evidence_handoff_hash
    recomputed_hash = compute_guide_evidence_handoff_hash(
        retrieval_receipt_ref=handoff.retrieval_receipt_ref,
        retrieval_selection_manifest_sha256=handoff.retrieval_selection_manifest_sha256,
        evaluated_at=handoff.evaluated_at,
        selections=handoff.selections,
    )
    assert handoff.handoff_sha256 == recomputed_hash


def test_verify_guide_evidence_handoff_success() -> None:
    req, _ = _make_valid_handoff_components()
    build_outcome = build_guide_evidence_handoff(req)
    assert build_outcome.handoff is not None

    verification_outcome = verify_guide_evidence_handoff(req, build_outcome.handoff)
    assert verification_outcome.decision.value == "VERIFIED"
    assert verification_outcome.reasons == ()


def test_verify_guide_evidence_handoff_rejects_forged_handoff() -> None:
    req, _ = _make_valid_handoff_components()
    build_outcome = build_guide_evidence_handoff(req)
    assert build_outcome.handoff is not None
    orig_handoff = build_outcome.handoff

    # Create a forged handoff where final_rank is altered and the handoff_sha256 is re-signed
    forged_s0 = replace(orig_handoff.selections[0], final_rank=999)
    forged_selections = (forged_s0, orig_handoff.selections[1])
    forged_hash = compute_guide_evidence_handoff_hash(
        orig_handoff.retrieval_receipt_ref,
        orig_handoff.retrieval_selection_manifest_sha256,
        orig_handoff.evaluated_at,
        forged_selections,
    )
    forged_handoff = VerifiedGuideEvidenceHandoff(
        retrieval_receipt_ref=orig_handoff.retrieval_receipt_ref,
        retrieval_selection_manifest_sha256=orig_handoff.retrieval_selection_manifest_sha256,
        evaluated_at=orig_handoff.evaluated_at,
        selections=forged_selections,
        handoff_sha256=forged_hash,
    )

    # Verification against the original request MUST REJECT the forged handoff
    # even though forged_handoff has a self-consistent hash!
    outcome = verify_guide_evidence_handoff(req, forged_handoff)
    assert outcome.decision.value == "REJECTED"
    assert (
        GuideEvidenceHandoffReason.HANDOFF_MISMATCH in outcome.reasons
        or GuideEvidenceHandoffReason.FORGED_HANDOFF_HASH in outcome.reasons
    )


def test_verify_guide_evidence_handoff_rejects_corrupted_hash() -> None:
    req, _ = _make_valid_handoff_components()
    build_outcome = build_guide_evidence_handoff(req)
    assert build_outcome.handoff is not None
    orig_handoff = build_outcome.handoff

    corrupted_handoff = VerifiedGuideEvidenceHandoff(
        retrieval_receipt_ref=orig_handoff.retrieval_receipt_ref,
        retrieval_selection_manifest_sha256=orig_handoff.retrieval_selection_manifest_sha256,
        evaluated_at=orig_handoff.evaluated_at,
        selections=orig_handoff.selections,
        handoff_sha256="f" * 64,  # corrupted hash
    )
    outcome = verify_guide_evidence_handoff(req, corrupted_handoff)
    assert outcome.decision == GuideEvidenceHandoffVerificationDecision.REJECTED
    assert GuideEvidenceHandoffReason.FORGED_HANDOFF_HASH in outcome.reasons


def test_verify_guide_evidence_handoff_rejects_invalid_request() -> None:
    req, _ = _make_valid_handoff_components()
    build_outcome = build_guide_evidence_handoff(req)
    assert build_outcome.handoff is not None

    invalid_req = GuideEvidenceHandoffRequest(
        retrieval_receipt=req.retrieval_receipt,
        selections=(),  # empty!
        evaluated_at=req.evaluated_at,
    )
    outcome = verify_guide_evidence_handoff(invalid_req, build_outcome.handoff)
    assert outcome.decision == GuideEvidenceHandoffVerificationDecision.REJECTED
    assert GuideEvidenceHandoffReason.REQUEST_INVALID in outcome.reasons


# ==============================================================================
# Rule 1: Request Binding & Origin Invariants
# ==============================================================================


def test_request_source_member_binding_requires_decisions_and_operation_code() -> None:
    with pytest.raises(TypeError):
        RequestSourceMemberBinding(  # type: ignore[call-arg]
            request_guard_ref=_make_artifact("guard-1"),
            source_snapshot_id=uuid4(),
            source_snapshot_member_id=uuid4(),
            source_code="SRC",
            source_version="v1",
            member_kind=SourceMemberKind.ENDPOINT_OPERATION,
            request_source_decision_ref=_make_artifact("src-dec"),
            request_member_decision_ref=_make_artifact("mem-dec"),
        )


def test_rejects_non_nfc_or_blank_request_operation_code() -> None:
    req, _ = _make_valid_handoff_components()
    import unicodedata

    decomposed = unicodedata.normalize("NFD", "가이드")

    for bad_code in ["", "   ", decomposed]:
        bad_binding = RequestSourceMemberBinding(
            request_guard_ref=req.selections[0].binding.request_guard_ref,
            request_operation_code=bad_code,
            source_snapshot_id=req.selections[0].binding.source_snapshot_id,
            source_snapshot_member_id=req.selections[0].binding.source_snapshot_member_id,
            source_code=req.selections[0].binding.source_code,
            source_version=req.selections[0].binding.source_version,
            member_kind=req.selections[0].binding.member_kind,
            request_source_decision_ref=req.selections[0].binding.request_source_decision_ref,
            request_member_decision_ref=req.selections[0].binding.request_member_decision_ref,
            observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
            observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
            endpoint_code=req.selections[0].binding.endpoint_code,
            operation_code=req.selections[0].binding.operation_code,
        )
        bad_sel = GuideEvidenceSelectionRequest(
            hit=req.selections[0].hit,
            binding=bad_binding,
            evidence_key=req.selections[0].evidence_key,
            content_text=req.selections[0].content_text,
            retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
            eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
            assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
            verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
            assessment_valid_from=req.selections[0].assessment_valid_from,
            assessment_valid_until=req.selections[0].assessment_valid_until,
        )
        outcome = build_guide_evidence_handoff(
            GuideEvidenceHandoffRequest(req.retrieval_receipt, (bad_sel, req.selections[1]), req.evaluated_at)
        )
        assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
        assert GuideEvidenceHandoffReason.REQUEST_INVALID in outcome.reasons


def test_rejects_mismatched_request_guard_ref_across_selections() -> None:
    req, _ = _make_valid_handoff_components()
    mismatched_binding = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard-DIFFERENT"),
        request_operation_code=req.selections[1].binding.request_operation_code,
        source_snapshot_id=req.selections[1].binding.source_snapshot_id,
        source_snapshot_member_id=req.selections[1].binding.source_snapshot_member_id,
        source_code=req.selections[1].binding.source_code,
        source_version=req.selections[1].binding.source_version,
        member_kind=req.selections[1].binding.member_kind,
        request_source_decision_ref=req.selections[1].binding.request_source_decision_ref,
        request_member_decision_ref=req.selections[1].binding.request_member_decision_ref,
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        artifact_code=req.selections[1].binding.artifact_code,
        artifact_version=req.selections[1].binding.artifact_version,
    )
    mismatched_sel2 = GuideEvidenceSelectionRequest(
        hit=req.selections[1].hit,
        binding=mismatched_binding,
        evidence_key=req.selections[1].evidence_key,
        content_text=req.selections[1].content_text,
        retrieval_receipt_ref=req.selections[1].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[1].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[1].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[1].verifier_artifact_ref,
        assessment_valid_from=req.selections[1].assessment_valid_from,
        assessment_valid_until=req.selections[1].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (req.selections[0], mismatched_sel2), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.REQUEST_ORIGIN_MISMATCH in outcome.reasons


def test_rejects_mismatched_request_operation_code_across_selections() -> None:
    req, _ = _make_valid_handoff_components()
    mismatched_binding = RequestSourceMemberBinding(
        request_guard_ref=req.selections[1].binding.request_guard_ref,
        request_operation_code="different_operation_code",
        source_snapshot_id=req.selections[1].binding.source_snapshot_id,
        source_snapshot_member_id=req.selections[1].binding.source_snapshot_member_id,
        source_code=req.selections[1].binding.source_code,
        source_version=req.selections[1].binding.source_version,
        member_kind=req.selections[1].binding.member_kind,
        request_source_decision_ref=req.selections[1].binding.request_source_decision_ref,
        request_member_decision_ref=req.selections[1].binding.request_member_decision_ref,
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        artifact_code=req.selections[1].binding.artifact_code,
        artifact_version=req.selections[1].binding.artifact_version,
    )
    mismatched_sel2 = GuideEvidenceSelectionRequest(
        hit=req.selections[1].hit,
        binding=mismatched_binding,
        evidence_key=req.selections[1].evidence_key,
        content_text=req.selections[1].content_text,
        retrieval_receipt_ref=req.selections[1].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[1].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[1].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[1].verifier_artifact_ref,
        assessment_valid_from=req.selections[1].assessment_valid_from,
        assessment_valid_until=req.selections[1].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (req.selections[0], mismatched_sel2), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.REQUEST_ORIGIN_MISMATCH in outcome.reasons


# ==============================================================================
# Rule 4: Fail-Closed Validation & Robust Invariants
# ==============================================================================


def test_rejects_uppercase_sha256() -> None:
    req, _ = _make_valid_handoff_components()
    bad_artifact = ImmutableArtifactRef("code", "v1", "A" * 64)
    bad_sel = GuideEvidenceSelectionRequest(
        hit=req.selections[0].hit,
        binding=req.selections[0].binding,
        evidence_key=req.selections[0].evidence_key,
        content_text=req.selections[0].content_text,
        retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
        eligibility_receipt_ref=bad_artifact,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (bad_sel, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED


def test_rejects_sensitive_text_revealing_non_str() -> None:
    req, _ = _make_valid_handoff_components()

    class FakeSensitiveText:
        def reveal(self) -> object:
            return None

    bad_sel = GuideEvidenceSelectionRequest(
        hit=req.selections[0].hit,
        binding=req.selections[0].binding,
        evidence_key=req.selections[0].evidence_key,
        content_text=FakeSensitiveText(),  # type: ignore[arg-type]
        retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (bad_sel, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.REQUEST_INVALID in outcome.reasons


def test_rejects_malformed_nested_input_without_exception() -> None:
    req, _ = _make_valid_handoff_components()

    # Hit with None provenance
    bad_hit = ProductionSearchHit(
        provenance=None,  # type: ignore[arg-type]
        coordinate=req.selections[0].hit.coordinate,
        exact_hit=False,
        observed_trigram_score=None,
        observed_fts_score=None,
        observed_dense_score=None,
        lexical_rank=None,
        dense_rank=None,
        fusion_rank=1,
        fraction_receipt=req.selections[0].hit.fraction_receipt,
        is_eligible_for_future_reranker=True,
    )
    bad_sel = GuideEvidenceSelectionRequest(
        hit=bad_hit,
        binding=req.selections[0].binding,
        evidence_key=req.selections[0].evidence_key,
        content_text=req.selections[0].content_text,
        retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (bad_sel, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.REQUEST_INVALID in outcome.reasons


# ==============================================================================
# Additional Review Pass: RET-H Receipt Invariants
# ==============================================================================


def test_rejects_retrieval_receipt_ret_h_none_query_embedding() -> None:
    req, receipt = _make_valid_handoff_components()
    tampered_receipt = compute_production_search_receipt(
        variant="RET-H",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code=receipt.diagnostic_code,
        query_fingerprint=receipt.query_fingerprint,
        filter_snapshot_hash=receipt.filter_snapshot_ref.content_sha256,
        evidence_index_config_hash=receipt.evidence_index_ref.content_sha256,
        retrieval_config_hash=receipt.retrieval_config_ref.content_sha256,
        adapter_artifact_ref=receipt.adapter_artifact_ref,
        query_embedding_sha256=None,  # RET-H must have non-null query_embedding_sha256!
        signal_manifest_sha256=receipt.signal_manifest_sha256,
        hit_manifest_sha256=receipt.hit_manifest_sha256,
        selection_manifest_sha256=receipt.selection_manifest_sha256,
    )
    selections = tuple(
        GuideEvidenceSelectionRequest(
            hit=s.hit,
            binding=s.binding,
            evidence_key=s.evidence_key,
            content_text=s.content_text,
            retrieval_receipt_ref=tampered_receipt.artifact_ref,
            eligibility_receipt_ref=s.eligibility_receipt_ref,
            assessment_artifact_ref=s.assessment_artifact_ref,
            verifier_artifact_ref=s.verifier_artifact_ref,
            assessment_valid_from=s.assessment_valid_from,
            assessment_valid_until=s.assessment_valid_until,
        )
        for s in req.selections
    )
    outcome = build_guide_evidence_handoff(GuideEvidenceHandoffRequest(tampered_receipt, selections, req.evaluated_at))
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH in outcome.reasons


def test_rejects_retrieval_receipt_ret_h_uppercase_or_malformed_query_embedding() -> None:
    req, receipt = _make_valid_handoff_components()
    for bad_digest in ("A" * 64, "invalid-digest", "12345"):
        tampered_receipt = ProductionSearchReceipt(
            artifact_ref=receipt.artifact_ref,
            variant=receipt.variant,
            retrieval_execution_status=receipt.retrieval_execution_status,
            diagnostic_code=receipt.diagnostic_code,
            query_fingerprint=receipt.query_fingerprint,
            filter_snapshot_ref=receipt.filter_snapshot_ref,
            evidence_index_ref=receipt.evidence_index_ref,
            retrieval_config_ref=receipt.retrieval_config_ref,
            adapter_artifact_ref=receipt.adapter_artifact_ref,
            query_embedding_sha256=bad_digest,
            signal_manifest_sha256=receipt.signal_manifest_sha256,
            hit_manifest_sha256=receipt.hit_manifest_sha256,
            selection_manifest_sha256=receipt.selection_manifest_sha256,
        )
        outcome = build_guide_evidence_handoff(
            GuideEvidenceHandoffRequest(tampered_receipt, req.selections, req.evaluated_at)
        )
        assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
        assert GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH in outcome.reasons


def test_rejects_retrieval_receipt_malformed_manifest_digests() -> None:
    req, receipt = _make_valid_handoff_components()
    tampered_receipt = ProductionSearchReceipt(
        artifact_ref=receipt.artifact_ref,
        variant=receipt.variant,
        retrieval_execution_status=receipt.retrieval_execution_status,
        diagnostic_code=receipt.diagnostic_code,
        query_fingerprint=receipt.query_fingerprint,
        filter_snapshot_ref=receipt.filter_snapshot_ref,
        evidence_index_ref=receipt.evidence_index_ref,
        retrieval_config_ref=receipt.retrieval_config_ref,
        adapter_artifact_ref=receipt.adapter_artifact_ref,
        query_embedding_sha256=receipt.query_embedding_sha256,
        signal_manifest_sha256="INVALID_HEX" * 6,
        hit_manifest_sha256=receipt.hit_manifest_sha256,
        selection_manifest_sha256=receipt.selection_manifest_sha256,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(tampered_receipt, req.selections, req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH in outcome.reasons


def test_rejects_retrieval_receipt_malformed_nested_artifacts() -> None:
    req, receipt = _make_valid_handoff_components()
    bad_artifact = ImmutableArtifactRef("filter", "1.0", "UPPERCASE" * 7 + "1")
    tampered_receipt = ProductionSearchReceipt(
        artifact_ref=receipt.artifact_ref,
        variant=receipt.variant,
        retrieval_execution_status=receipt.retrieval_execution_status,
        diagnostic_code=receipt.diagnostic_code,
        query_fingerprint=receipt.query_fingerprint,
        filter_snapshot_ref=bad_artifact,
        evidence_index_ref=receipt.evidence_index_ref,
        retrieval_config_ref=receipt.retrieval_config_ref,
        adapter_artifact_ref=receipt.adapter_artifact_ref,
        query_embedding_sha256=receipt.query_embedding_sha256,
        signal_manifest_sha256=receipt.signal_manifest_sha256,
        hit_manifest_sha256=receipt.hit_manifest_sha256,
        selection_manifest_sha256=receipt.selection_manifest_sha256,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(tampered_receipt, req.selections, req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH in outcome.reasons


# ==============================================================================
# Additional Review Pass: Production Provenance Preservation
# ==============================================================================


def test_verified_selection_preserves_production_provenance() -> None:
    req, _ = _make_valid_handoff_components()
    outcome = build_guide_evidence_handoff(req)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.BUILT
    assert outcome.handoff is not None

    s0 = outcome.handoff.selections[0]
    assert isinstance(s0, VerifiedGuideEvidenceSelection)
    p0 = req.selections[0].hit.provenance
    assert s0.canonical_checksum == p0.canonical_checksum
    assert s0.external_document_id == p0.external_document_id
    assert s0.chunk_index == p0.chunk_index
    assert s0.canonicalization_spec_version == p0.canonicalization_spec_version
    assert s0.normalization_version == p0.normalization_version


def test_projection_includes_production_provenance_keys() -> None:
    from ai_worker.tasks.rag.guide_evidence_handoff import _selection_projection

    req, _ = _make_valid_handoff_components()
    outcome = build_guide_evidence_handoff(req)
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.BUILT
    assert outcome.handoff is not None

    proj = _selection_projection(outcome.handoff.selections[0])
    assert proj["canonical_checksum"] == "c" * 64
    assert proj["external_document_id"] == "DOC-1"
    assert proj["chunk_index"] == 0
    assert proj["canonicalization_spec_version"] == "v1"
    assert proj["normalization_version"] == "v1"


def test_rejects_non_nfc_or_blank_provenance_strings() -> None:
    req, _ = _make_valid_handoff_components()
    # external_document_id is blank
    hit0, text0 = _make_search_hit(rank=1, external_doc_id="   ", chunk_index=0)
    b0 = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard-1"),
        source_snapshot_id=hit0.provenance.source_snapshot_id,
        source_snapshot_member_id=hit0.provenance.source_snapshot_member_id,
        source_code=hit0.provenance.source_code,
        source_version=hit0.provenance.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_make_artifact("src-1"),
        request_member_decision_ref=_make_artifact("mem-1"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        endpoint_code="guide_api",
        operation_code="get_guide",
    )
    bad_sel = GuideEvidenceSelectionRequest(
        hit=hit0,
        binding=b0,
        evidence_key="evidence:1",
        content_text=text0,
        retrieval_receipt_ref=req.retrieval_receipt.artifact_ref,
        eligibility_receipt_ref=_make_artifact("elig-1"),
        assessment_artifact_ref=_make_artifact("assess-1"),
        verifier_artifact_ref=_make_artifact("verif-1"),
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (bad_sel, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.REQUEST_INVALID in outcome.reasons


def test_rejects_negative_chunk_index() -> None:
    req, _ = _make_valid_handoff_components()
    hit0, text0 = _make_search_hit(rank=1, external_doc_id="DOC-1", chunk_index=-1)
    b0 = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard-1"),
        source_snapshot_id=hit0.provenance.source_snapshot_id,
        source_snapshot_member_id=hit0.provenance.source_snapshot_member_id,
        source_code=hit0.provenance.source_code,
        source_version=hit0.provenance.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_make_artifact("src-1"),
        request_member_decision_ref=_make_artifact("mem-1"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        endpoint_code="guide_api",
        operation_code="get_guide",
    )
    bad_sel = GuideEvidenceSelectionRequest(
        hit=hit0,
        binding=b0,
        evidence_key="evidence:1",
        content_text=text0,
        retrieval_receipt_ref=req.retrieval_receipt.artifact_ref,
        eligibility_receipt_ref=_make_artifact("elig-1"),
        assessment_artifact_ref=_make_artifact("assess-1"),
        verifier_artifact_ref=_make_artifact("verif-1"),
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (bad_sel, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.REQUEST_INVALID in outcome.reasons


def test_rejects_non_uuid_type_for_uuid_fields() -> None:
    req, _ = _make_valid_handoff_components()
    hit0, text0 = _make_search_hit(rank=1, external_doc_id="DOC-1", chunk_index=0)
    # Put string instead of UUID
    bad_binding = RequestSourceMemberBinding(
        request_guard_ref=_make_artifact("guard-1"),
        source_snapshot_id="not-a-uuid",  # type: ignore[arg-type]
        source_snapshot_member_id=hit0.provenance.source_snapshot_member_id,
        source_code=hit0.provenance.source_code,
        source_version=hit0.provenance.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_make_artifact("src-1"),
        request_member_decision_ref=_make_artifact("mem-1"),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code="get_guide",
        endpoint_code="guide_api",
        operation_code="get_guide",
    )
    bad_sel = GuideEvidenceSelectionRequest(
        hit=hit0,
        binding=bad_binding,
        evidence_key="evidence:1",
        content_text=text0,
        retrieval_receipt_ref=req.retrieval_receipt.artifact_ref,
        eligibility_receipt_ref=_make_artifact("elig-1"),
        assessment_artifact_ref=_make_artifact("assess-1"),
        verifier_artifact_ref=_make_artifact("verif-1"),
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (bad_sel, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.REQUEST_INVALID in outcome.reasons


# ==============================================================================
# Additional Review Pass: Endpoint Operation Nullable & Validation
# ==============================================================================


def test_allows_endpoint_operation_with_none_operation_code() -> None:
    req, _ = _make_valid_handoff_components()
    # First selection has operation_code=None
    binding_none_op = RequestSourceMemberBinding(
        request_guard_ref=req.selections[0].binding.request_guard_ref,
        source_snapshot_id=req.selections[0].binding.source_snapshot_id,
        source_snapshot_member_id=req.selections[0].binding.source_snapshot_member_id,
        source_code=req.selections[0].binding.source_code,
        source_version=req.selections[0].binding.source_version,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=req.selections[0].binding.request_source_decision_ref,
        request_member_decision_ref=req.selections[0].binding.request_member_decision_ref,
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_operation_code=req.selections[0].binding.request_operation_code,
        endpoint_code="guide_api",
        operation_code=None,  # Nullable!
    )
    sel_none_op = GuideEvidenceSelectionRequest(
        hit=req.selections[0].hit,
        binding=binding_none_op,
        evidence_key=req.selections[0].evidence_key,
        content_text=req.selections[0].content_text,
        retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
        eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
        assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
        verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
        assessment_valid_from=req.selections[0].assessment_valid_from,
        assessment_valid_until=req.selections[0].assessment_valid_until,
    )
    outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(req.retrieval_receipt, (sel_none_op, req.selections[1]), req.evaluated_at)
    )
    assert outcome.decision == GuideEvidenceHandoffBuildDecision.BUILT
    assert outcome.handoff is not None
    assert outcome.handoff.selections[0].operation_code is None


def test_rejects_endpoint_operation_with_invalid_nonblank_nfc_operation_code() -> None:
    req, _ = _make_valid_handoff_components()
    for bad_op in ("", "   ", "\t"):
        bad_binding = RequestSourceMemberBinding(
            request_guard_ref=req.selections[0].binding.request_guard_ref,
            source_snapshot_id=req.selections[0].binding.source_snapshot_id,
            source_snapshot_member_id=req.selections[0].binding.source_snapshot_member_id,
            source_code=req.selections[0].binding.source_code,
            source_version=req.selections[0].binding.source_version,
            member_kind=SourceMemberKind.ENDPOINT_OPERATION,
            request_source_decision_ref=req.selections[0].binding.request_source_decision_ref,
            request_member_decision_ref=req.selections[0].binding.request_member_decision_ref,
            observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
            observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
            request_operation_code=req.selections[0].binding.request_operation_code,
            endpoint_code="guide_api",
            operation_code=bad_op,
        )
        bad_sel = GuideEvidenceSelectionRequest(
            hit=req.selections[0].hit,
            binding=bad_binding,
            evidence_key=req.selections[0].evidence_key,
            content_text=req.selections[0].content_text,
            retrieval_receipt_ref=req.selections[0].retrieval_receipt_ref,
            eligibility_receipt_ref=req.selections[0].eligibility_receipt_ref,
            assessment_artifact_ref=req.selections[0].assessment_artifact_ref,
            verifier_artifact_ref=req.selections[0].verifier_artifact_ref,
            assessment_valid_from=req.selections[0].assessment_valid_from,
            assessment_valid_until=req.selections[0].assessment_valid_until,
        )
        outcome = build_guide_evidence_handoff(
            GuideEvidenceHandoffRequest(req.retrieval_receipt, (bad_sel, req.selections[1]), req.evaluated_at)
        )
        assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
        assert GuideEvidenceHandoffReason.MEMBER_IDENTITY_INVALID in outcome.reasons


# ==============================================================================
# Additional Review Pass: Narrow Exception Handling
# ==============================================================================


def test_narrow_exception_handling_does_not_swallow_unexpected_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    req, _ = _make_valid_handoff_components()

    def _exploding_func(*args: object, **kwargs: object) -> str:
        raise RuntimeError("Internal unexpected implementation fault!")

    monkeypatch.setattr(
        "ai_worker.tasks.rag.guide_evidence_handoff.compute_guide_evidence_handoff_hash", _exploding_func
    )

    with pytest.raises(RuntimeError, match="Internal unexpected implementation fault!"):
        build_guide_evidence_handoff(req)


@pytest.mark.parametrize("error_type", [AttributeError, TypeError, ValueError])
def test_public_build_does_not_swallow_internal_expected_boundary_error_types(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    req, _ = _make_valid_handoff_components()

    def _exploding_hash(*args: object, **kwargs: object) -> str:
        raise error_type("Internal implementation defect")

    monkeypatch.setattr(
        "ai_worker.tasks.rag.guide_evidence_handoff.compute_guide_evidence_handoff_hash",
        _exploding_hash,
    )

    with pytest.raises(error_type, match="Internal implementation defect"):
        build_guide_evidence_handoff(req)


@pytest.mark.parametrize("error_type", [AttributeError, TypeError, ValueError])
def test_public_verify_does_not_swallow_internal_expected_boundary_error_types(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    req, _ = _make_valid_handoff_components()
    build_outcome = build_guide_evidence_handoff(req)
    assert build_outcome.handoff is not None

    def _exploding_hash(*args: object, **kwargs: object) -> str:
        raise error_type("Internal implementation defect")

    monkeypatch.setattr(
        "ai_worker.tasks.rag.guide_evidence_handoff.compute_guide_evidence_handoff_hash",
        _exploding_hash,
    )

    with pytest.raises(error_type, match="Internal implementation defect"):
        verify_guide_evidence_handoff(req, build_outcome.handoff)


@pytest.mark.parametrize(
    "fingerprint",
    [
        QueryFingerprint("", "v1", "0" * 64),
        QueryFingerprint("sha256", "", "0" * 64),
        QueryFingerprint("sha256", "v1", "not-a-sha256"),
        QueryFingerprint("sha\u0301256", "v1", "0" * 64),
        QueryFingerprint("sha256", "ve\u0301rsion", "0" * 64),
    ],
)
def test_rejects_structurally_invalid_query_fingerprint(fingerprint: QueryFingerprint) -> None:
    req, receipt = _make_valid_handoff_components()
    malformed_receipt = compute_production_search_receipt(
        variant=receipt.variant,
        status=receipt.retrieval_execution_status,
        diagnostic_code=receipt.diagnostic_code,
        query_fingerprint=fingerprint,
        filter_snapshot_hash=receipt.filter_snapshot_ref.content_sha256,
        evidence_index_config_hash=receipt.evidence_index_ref.content_sha256,
        retrieval_config_hash=receipt.retrieval_config_ref.content_sha256,
        adapter_artifact_ref=receipt.adapter_artifact_ref,
        query_embedding_sha256=receipt.query_embedding_sha256,
        signal_manifest_sha256=receipt.signal_manifest_sha256,
        hit_manifest_sha256=receipt.hit_manifest_sha256,
        selection_manifest_sha256=receipt.selection_manifest_sha256,
    )
    selections = tuple(
        replace(selection, retrieval_receipt_ref=malformed_receipt.artifact_ref) for selection in req.selections
    )

    outcome = build_guide_evidence_handoff(replace(req, retrieval_receipt=malformed_receipt, selections=selections))

    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH in outcome.reasons


def test_rejects_query_fingerprint_with_wrong_runtime_type() -> None:
    req, receipt = _make_valid_handoff_components()
    malformed_receipt = replace(receipt, query_fingerprint=object())  # type: ignore[arg-type]

    outcome = build_guide_evidence_handoff(replace(req, retrieval_receipt=malformed_receipt))

    assert outcome.decision == GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH in outcome.reasons
