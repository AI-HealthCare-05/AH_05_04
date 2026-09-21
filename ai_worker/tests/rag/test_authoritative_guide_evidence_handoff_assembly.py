"""Unit tests for the Authoritative Guide Evidence Handoff Assembly (#760).

#709/#697/#715가 만든 hydrated retrieval selection과 #712/#746이 남긴 persisted
Assessment·Eligibility authority를 pure in-memory exact binding으로 결속해 기존
`build_guide_evidence_handoff()`에 넘기는 seam을 검증한다.

DB, network, clock을 쓰지 않는다. 실제 #712 issuer → persistence → #746 Reader
production path 소비는
`tests/integration/rag/test_authoritative_guide_evidence_handoff_assembly_postgresql.py`가
담당한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

from ai_worker.tasks.rag.authoritative_guide_evidence_handoff import (
    AuthoritativeGuideEvidenceAssemblyDecision,
    AuthoritativeGuideEvidenceAssemblyReason,
    AuthoritativeGuideEvidenceHandoffAssemblyRequest,
    assemble_authoritative_guide_evidence_handoff,
)
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    QueryFingerprint,
    SensitiveText,
)
from ai_worker.tasks.rag.evidence_search import (
    FractionReceipt,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    StableCoordinate,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    GuideEvidenceHandoffBuildDecision,
    GuideEvidenceHandoffReason,
    ObservedDecisionOutcome,
    RequestSourceMemberBinding,
)
from ai_worker.tasks.rag.guide_retrieval_composition import AuthenticatedGuideRetrievalSelection
from ai_worker.tasks.rag.knowledge_chunk_content_hydration import HydratedGuideRetrievalSelection
from ai_worker.tasks.rag.retrieval_run import PersistedRetrievalRunReceipt, compute_receipt_hash
from ai_worker.tasks.rag.retrieval_runtime import (
    ProductionSearchReceipt,
    RetrievalExecutionStatus,
    compute_production_search_receipt,
    compute_selection_manifest_hash,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberKind
from rag_runtime.evidence_authority import (
    EvidenceAssessmentValidityPolicy,
    PersistedEvidenceAuthority,
    compute_assessment_artifact_ref,
    compute_assessment_validity_window,
    compute_eligibility_receipt_ref,
    compute_validity_policy_ref,
    compute_verifier_artifact_ref,
)

_RUN_ID = UUID("76000000-0000-4000-8000-000000000001")
_JOB_ID = UUID("76000000-0000-4000-8000-000000000002")
_SNAPSHOT_ID = UUID("76000000-0000-4000-8000-000000000003")
_MEMBER_ID = UUID("76000000-0000-4000-8000-000000000004")
_CHUNK_ID_1 = UUID("76000000-0000-4000-8000-000000000011")
_CHUNK_ID_2 = UUID("76000000-0000-4000-8000-000000000012")

_SOURCE_CODE = "MFDS"
_SOURCE_VERSION = "2026.09.17"

_AUTHORITY_EVALUATED_AT = datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)
_HANDOFF_EVALUATED_AT = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)

_POLICY = EvidenceAssessmentValidityPolicy()
_POLICY_REF = compute_validity_policy_ref(_POLICY)
_VERIFIER_REF = compute_verifier_artifact_ref()


# ==============================================================================
# Fixtures: a fully consistent #709/#697/#715 + #712/#746 production chain
# ==============================================================================


def _artifact(code: str, digest: str) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(artifact_code=code, version="v1", content_sha256=digest)


def _make_hit(
    *,
    rank: int,
    chunk_id: UUID,
    external_document_id: str,
    chunk_index: int,
    content_text: str,
) -> tuple[ProductionSearchHit, SensitiveText]:
    content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
    provenance = ProductionEvidenceProvenance(
        knowledge_index_id=UUID("76000000-0000-4000-8000-0000000000a1"),
        index_code="MFDS_GUIDE_INDEX",
        index_version="1.0",
        index_configuration_hash="4" * 64,
        knowledge_chunk_id=chunk_id,
        evidence_key=f"evidence:{chunk_index + 1}",
        source_snapshot_id=_SNAPSHOT_ID,
        source_snapshot_member_id=_MEMBER_ID,
        source_code=_SOURCE_CODE,
        source_version=_SOURCE_VERSION,
        canonical_checksum="c" * 64,
        external_document_id=external_document_id,
        chunk_index=chunk_index,
        locator=f"doc:{external_document_id}#p{chunk_index}",
        content_hash=content_hash,
        canonicalization_spec_version="canonical-v1",
        normalization_version="normalization-v1",
    )
    coordinate = StableCoordinate(
        source_code=_SOURCE_CODE,
        source_version=_SOURCE_VERSION,
        external_document_id=external_document_id,
        chunk_index=chunk_index,
    )
    hit = ProductionSearchHit(
        provenance=provenance,
        coordinate=coordinate,
        exact_hit=False,
        observed_trigram_score=None,
        observed_fts_score=None,
        observed_dense_score=None,
        lexical_rank=rank,
        dense_rank=None,
        fusion_rank=rank,
        fraction_receipt=FractionReceipt("1", "61"),
        is_eligible_for_future_reranker=True,
    )
    return hit, SensitiveText(content_text)


def _binding() -> RequestSourceMemberBinding:
    """#709 REQUEST authority가 확정한 단일 Source Member binding."""
    return RequestSourceMemberBinding(
        request_guard_ref=_artifact("request_guard", "1" * 64),
        request_operation_code="get_guide",
        source_snapshot_id=_SNAPSHOT_ID,
        source_snapshot_member_id=_MEMBER_ID,
        source_code=_SOURCE_CODE,
        source_version=_SOURCE_VERSION,
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=_artifact("request_source_decision", "2" * 64),
        request_member_decision_ref=_artifact("request_member_decision", "3" * 64),
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        endpoint_code="PRODUCT_LIST",
        operation_code="LIST_PRODUCTS",
    )


def _hydrated(hit: ProductionSearchHit, content_text: SensitiveText) -> HydratedGuideRetrievalSelection:
    return HydratedGuideRetrievalSelection(
        selection=AuthenticatedGuideRetrievalSelection(hit=hit, binding=_binding()),
        content_text=content_text,
    )


def _authority(
    hit: ProductionSearchHit,
    *,
    retrieval_run_id: UUID = _RUN_ID,
    evaluated_at: datetime = _AUTHORITY_EVALUATED_AT,
) -> PersistedEvidenceAuthority:
    """#712 issuer가 발급하고 #746 Reader가 반환했을 self-consistent authority."""
    provenance = hit.provenance
    valid_from, valid_until = compute_assessment_validity_window(evaluated_at, _POLICY, ())
    eligibility_receipt_ref = compute_eligibility_receipt_ref(
        retrieval_run_id=retrieval_run_id,
        knowledge_chunk_id=provenance.knowledge_chunk_id,
        source_snapshot_id=provenance.source_snapshot_id,
        source_snapshot_member_id=provenance.source_snapshot_member_id,
        source_code=provenance.source_code,
        source_version=provenance.source_version,
        content_sha256=provenance.content_hash,
        evaluated_at=evaluated_at,
        verifier_artifact_ref=_VERIFIER_REF,
    )
    assessment_artifact_ref = compute_assessment_artifact_ref(
        retrieval_run_id=retrieval_run_id,
        knowledge_chunk_id=provenance.knowledge_chunk_id,
        eligibility_receipt_ref=eligibility_receipt_ref,
        validity_policy_ref=_POLICY_REF,
        assessment_valid_from=valid_from,
        assessment_valid_until=valid_until,
    )
    return PersistedEvidenceAuthority(
        id=uuid4(),
        retrieval_run_id=retrieval_run_id,
        knowledge_chunk_id=provenance.knowledge_chunk_id,
        source_snapshot_id=provenance.source_snapshot_id,
        source_snapshot_member_id=provenance.source_snapshot_member_id,
        source_code=provenance.source_code,
        source_version=provenance.source_version,
        content_sha256=provenance.content_hash,
        eligibility_receipt_ref=eligibility_receipt_ref,
        assessment_artifact_ref=assessment_artifact_ref,
        verifier_artifact_ref=_VERIFIER_REF,
        validity_policy_ref=_POLICY_REF,
        evaluated_at=evaluated_at,
        assessment_valid_from=valid_from,
        assessment_valid_until=valid_until,
        created_at=evaluated_at,
    )


def _search_receipt(hits: tuple[ProductionSearchHit, ...]) -> ProductionSearchReceipt:
    return compute_production_search_receipt(
        variant="RET-H",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code="OK",
        query_fingerprint=QueryFingerprint("sha256", "v1", "0" * 64),
        filter_snapshot_ref=_artifact("filter_snapshot", "f" * 64),
        evidence_index_ref=_artifact("knowledge_index", "e" * 64),
        retrieval_config_ref=_artifact("retrieval_config", "b" * 64),
        adapter_artifact_ref=_artifact("retrieval_adapter", "a" * 64),
        query_embedding_sha256="d" * 64,
        signal_manifest_sha256="1" * 64,
        hit_manifest_sha256="2" * 64,
        selection_manifest_sha256=compute_selection_manifest_hash(list(hits)),
    )


def _persisted_receipt(
    search_receipt: ProductionSearchReceipt,
    *,
    selected_count: int,
    run_id: UUID = _RUN_ID,
    variant: str = "RET-H",
    status: str = "COMPLETED",
    search_receipt_hash: str | None = None,
    signal_manifest_hash: str | None = None,
    hit_manifest_hash: str | None = None,
) -> PersistedRetrievalRunReceipt:
    """#634/#178이 finalize한 retrieval run receipt를 자기일관적으로 재구성한다."""
    fields = {
        "run_id": run_id,
        "job_id": _JOB_ID,
        "node_id": "hybrid_retrieve",
        "variant": variant,
        "status": status,
        "query_digest": "1" * 64,
        "retrieval_configuration_hash": "2" * 64,
        "source_manifest_hash": "3" * 64,
        "search_receipt_hash": (
            search_receipt_hash if search_receipt_hash is not None else search_receipt.artifact_ref.content_sha256
        ),
        "total_signals": 4,
        "total_hits": 3,
        "selected_count": selected_count,
        "signal_manifest_hash": (
            signal_manifest_hash if signal_manifest_hash is not None else search_receipt.signal_manifest_sha256
        ),
        "hit_manifest_hash": (
            hit_manifest_hash if hit_manifest_hash is not None else search_receipt.hit_manifest_sha256
        ),
    }
    return PersistedRetrievalRunReceipt(
        receipt_hash=compute_receipt_hash(**fields),  # type: ignore[arg-type]
        diagnostic_code="OK",
        **fields,  # type: ignore[arg-type]
    )


def _valid_request(*, selection_count: int = 2) -> AuthoritativeGuideEvidenceHandoffAssemblyRequest:
    hit1, text1 = _make_hit(
        rank=1,
        chunk_id=_CHUNK_ID_1,
        external_document_id="MFDS-DOC-1",
        chunk_index=0,
        content_text="아스피린 복용 안내",
    )
    hits = [hit1]
    texts = [text1]
    if selection_count == 2:
        hit2, text2 = _make_hit(
            rank=2,
            chunk_id=_CHUNK_ID_2,
            external_document_id="MFDS-DOC-1",
            chunk_index=1,
            content_text="아스피린 주의사항",
        )
        hits.append(hit2)
        texts.append(text2)

    search_receipt = _search_receipt(tuple(hits))
    hydrated = tuple(_hydrated(hit, text) for hit, text in zip(hits, texts, strict=True))
    authorities = tuple(_authority(hit) for hit in hits)
    return AuthoritativeGuideEvidenceHandoffAssemblyRequest(
        persisted_retrieval_receipt=_persisted_receipt(search_receipt, selected_count=len(hits)),
        retrieval_receipt=search_receipt,
        hydrated_selections=hydrated,
        authorities=authorities,
        evaluated_at=_HANDOFF_EVALUATED_AT,
    )


def _assert_rejected(
    request: AuthoritativeGuideEvidenceHandoffAssemblyRequest,
    reason: AuthoritativeGuideEvidenceAssemblyReason,
) -> None:
    outcome = assemble_authoritative_guide_evidence_handoff(request)
    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.REJECTED
    assert outcome.reasons == (reason,)
    assert outcome.build_outcome is None


# ==============================================================================
# BUILT
# ==============================================================================


def test_single_selection_builds_handoff() -> None:
    outcome = assemble_authoritative_guide_evidence_handoff(_valid_request(selection_count=1))

    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.BUILT
    assert outcome.reasons == ()
    assert outcome.build_outcome is not None
    assert outcome.build_outcome.decision is GuideEvidenceHandoffBuildDecision.BUILT
    assert outcome.build_outcome.handoff is not None
    assert len(outcome.build_outcome.handoff.selections) == 1


def test_multiple_selections_build_handoff() -> None:
    outcome = assemble_authoritative_guide_evidence_handoff(_valid_request())

    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.BUILT
    assert outcome.reasons == ()
    assert outcome.build_outcome is not None
    assert outcome.build_outcome.handoff is not None
    assert len(outcome.build_outcome.handoff.selections) == 2


def test_hydrated_selection_order_is_the_output_order() -> None:
    request = _valid_request()
    outcome = assemble_authoritative_guide_evidence_handoff(request)

    assert outcome.build_outcome is not None
    assert outcome.build_outcome.handoff is not None
    assert tuple(s.knowledge_chunk_id for s in outcome.build_outcome.handoff.selections) == tuple(
        h.selection.hit.provenance.knowledge_chunk_id for h in request.hydrated_selections
    )


def test_authority_tuple_order_does_not_change_the_output() -> None:
    """authority matching은 순서가 아니라 knowledge_chunk_id로만 이뤄진다."""
    request = _valid_request()
    reversed_request = replace(request, authorities=tuple(reversed(request.authorities)))

    outcome = assemble_authoritative_guide_evidence_handoff(reversed_request)
    baseline = assemble_authoritative_guide_evidence_handoff(request)

    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.BUILT
    assert baseline.build_outcome is not None
    assert outcome.build_outcome is not None
    assert baseline.build_outcome.handoff is not None
    assert outcome.build_outcome.handoff is not None
    assert outcome.build_outcome.handoff.handoff_sha256 == baseline.build_outcome.handoff.handoff_sha256


def test_same_source_member_with_multiple_distinct_chunks_is_allowed() -> None:
    """authority는 chunk 단위, #709 binding은 Source Member 단위다 (N:1)."""
    request = _valid_request()

    members = {h.selection.hit.provenance.source_snapshot_member_id for h in request.hydrated_selections}
    chunks = {h.selection.hit.provenance.knowledge_chunk_id for h in request.hydrated_selections}
    assert len(members) == 1
    assert len(chunks) == 2

    outcome = assemble_authoritative_guide_evidence_handoff(request)
    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.BUILT


def test_assembled_selection_carries_persisted_authority_values_verbatim() -> None:
    request = _valid_request()
    outcome = assemble_authoritative_guide_evidence_handoff(request)

    assert outcome.build_outcome is not None
    assert outcome.build_outcome.handoff is not None
    authorities = {a.knowledge_chunk_id: a for a in request.authorities}
    for verified in outcome.build_outcome.handoff.selections:
        authority = authorities[verified.knowledge_chunk_id]
        assert verified.eligibility_receipt_ref.content_sha256 == authority.eligibility_receipt_ref.content_sha256
        assert verified.assessment_artifact_ref.content_sha256 == authority.assessment_artifact_ref.content_sha256
        assert verified.verifier_artifact_ref.content_sha256 == authority.verifier_artifact_ref.content_sha256
        assert verified.assessment_valid_from == authority.assessment_valid_from
        assert verified.assessment_valid_until == authority.assessment_valid_until
        assert verified.content_sha256 == authority.content_sha256


# ==============================================================================
# REQUEST_INVALID
# ==============================================================================


def test_naive_evaluated_at_is_request_invalid() -> None:
    request = replace(_valid_request(), evaluated_at=datetime(2026, 9, 17, 12, 0, 0))
    _assert_rejected(request, AuthoritativeGuideEvidenceAssemblyReason.REQUEST_INVALID)


def test_non_utc_evaluated_at_is_request_invalid() -> None:
    kst = timezone(timedelta(hours=9))
    request = replace(_valid_request(), evaluated_at=datetime(2026, 9, 17, 21, 0, 0, tzinfo=kst))
    _assert_rejected(request, AuthoritativeGuideEvidenceAssemblyReason.REQUEST_INVALID)


def test_empty_hydrated_selections_is_request_invalid() -> None:
    request = replace(_valid_request(), hydrated_selections=())
    _assert_rejected(request, AuthoritativeGuideEvidenceAssemblyReason.REQUEST_INVALID)


def test_non_request_input_is_request_invalid() -> None:
    outcome = assemble_authoritative_guide_evidence_handoff("not-a-request")  # type: ignore[arg-type]
    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.REJECTED
    assert outcome.reasons == (AuthoritativeGuideEvidenceAssemblyReason.REQUEST_INVALID,)
    assert outcome.build_outcome is None


# ==============================================================================
# RETRIEVAL_RUN_RECEIPT_MISMATCH
# ==============================================================================


def test_tampered_persisted_receipt_hash_is_rejected() -> None:
    request = _valid_request()
    tampered = replace(request.persisted_retrieval_receipt, receipt_hash="f" * 64)
    _assert_rejected(
        replace(request, persisted_retrieval_receipt=tampered),
        AuthoritativeGuideEvidenceAssemblyReason.RETRIEVAL_RUN_RECEIPT_MISMATCH,
    )


def test_search_receipt_hash_mismatch_is_rejected() -> None:
    request = _valid_request()
    persisted = _persisted_receipt(
        request.retrieval_receipt,
        selected_count=len(request.hydrated_selections),
        search_receipt_hash="9" * 64,
    )
    _assert_rejected(
        replace(request, persisted_retrieval_receipt=persisted),
        AuthoritativeGuideEvidenceAssemblyReason.RETRIEVAL_RUN_RECEIPT_MISMATCH,
    )


def test_absent_search_receipt_hash_is_rejected() -> None:
    request = _valid_request()
    fields = {
        "run_id": _RUN_ID,
        "job_id": _JOB_ID,
        "node_id": "hybrid_retrieve",
        "variant": "RET-H",
        "status": "COMPLETED",
        "query_digest": "1" * 64,
        "retrieval_configuration_hash": "2" * 64,
        "source_manifest_hash": "3" * 64,
        "search_receipt_hash": None,
        "total_signals": 4,
        "total_hits": 3,
        "selected_count": len(request.hydrated_selections),
        "signal_manifest_hash": request.retrieval_receipt.signal_manifest_sha256,
        "hit_manifest_hash": request.retrieval_receipt.hit_manifest_sha256,
    }
    persisted = PersistedRetrievalRunReceipt(
        receipt_hash=compute_receipt_hash(**fields),  # type: ignore[arg-type]
        diagnostic_code="OK",
        **fields,  # type: ignore[arg-type]
    )
    _assert_rejected(
        replace(request, persisted_retrieval_receipt=persisted),
        AuthoritativeGuideEvidenceAssemblyReason.RETRIEVAL_RUN_RECEIPT_MISMATCH,
    )


def test_signal_manifest_mismatch_is_rejected() -> None:
    request = _valid_request()
    persisted = _persisted_receipt(
        request.retrieval_receipt,
        selected_count=len(request.hydrated_selections),
        signal_manifest_hash="7" * 64,
    )
    _assert_rejected(
        replace(request, persisted_retrieval_receipt=persisted),
        AuthoritativeGuideEvidenceAssemblyReason.RETRIEVAL_RUN_RECEIPT_MISMATCH,
    )


def test_hit_manifest_mismatch_is_rejected() -> None:
    request = _valid_request()
    persisted = _persisted_receipt(
        request.retrieval_receipt,
        selected_count=len(request.hydrated_selections),
        hit_manifest_hash="8" * 64,
    )
    _assert_rejected(
        replace(request, persisted_retrieval_receipt=persisted),
        AuthoritativeGuideEvidenceAssemblyReason.RETRIEVAL_RUN_RECEIPT_MISMATCH,
    )


def test_selected_count_mismatch_is_rejected() -> None:
    request = _valid_request()
    persisted = _persisted_receipt(request.retrieval_receipt, selected_count=5)
    _assert_rejected(
        replace(request, persisted_retrieval_receipt=persisted),
        AuthoritativeGuideEvidenceAssemblyReason.RETRIEVAL_RUN_RECEIPT_MISMATCH,
    )


def test_non_ret_h_persisted_variant_is_rejected() -> None:
    request = _valid_request()
    persisted = _persisted_receipt(
        request.retrieval_receipt,
        selected_count=len(request.hydrated_selections),
        variant="RET-L",
    )
    _assert_rejected(
        replace(request, persisted_retrieval_receipt=persisted),
        AuthoritativeGuideEvidenceAssemblyReason.RETRIEVAL_RUN_RECEIPT_MISMATCH,
    )


def test_non_completed_persisted_status_is_rejected() -> None:
    request = _valid_request()
    persisted = _persisted_receipt(
        request.retrieval_receipt,
        selected_count=len(request.hydrated_selections),
        status="FAILED",
    )
    _assert_rejected(
        replace(request, persisted_retrieval_receipt=persisted),
        AuthoritativeGuideEvidenceAssemblyReason.RETRIEVAL_RUN_RECEIPT_MISMATCH,
    )


# ==============================================================================
# AUTHORITY_SET_MISMATCH
# ==============================================================================


def test_missing_authority_is_set_mismatch() -> None:
    request = _valid_request()
    _assert_rejected(
        replace(request, authorities=request.authorities[:1]),
        AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_SET_MISMATCH,
    )


def test_extra_authority_is_set_mismatch() -> None:
    request = _valid_request()
    extra_hit, _ = _make_hit(
        rank=3,
        chunk_id=UUID("76000000-0000-4000-8000-000000000013"),
        external_document_id="MFDS-DOC-2",
        chunk_index=0,
        content_text="관련 없는 chunk",
    )
    _assert_rejected(
        replace(request, authorities=(*request.authorities, _authority(extra_hit))),
        AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_SET_MISMATCH,
    )


def test_duplicate_authority_is_set_mismatch() -> None:
    request = _valid_request()
    _assert_rejected(
        replace(request, authorities=(*request.authorities, request.authorities[0])),
        AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_SET_MISMATCH,
    )


def test_authority_chunk_mismatch_is_set_mismatch() -> None:
    """chunk identity가 authority index key이므로 chunk 불일치는 항상 set 사실이다."""
    request = _valid_request()
    relabelled = replace(
        request.authorities[0],
        knowledge_chunk_id=UUID("76000000-0000-4000-8000-0000000000ff"),
    )
    _assert_rejected(
        replace(request, authorities=(relabelled, request.authorities[1])),
        AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_SET_MISMATCH,
    )


# ==============================================================================
# AUTHORITY_BINDING_MISMATCH
# ==============================================================================


def test_authority_retrieval_run_id_mismatch_is_binding_mismatch() -> None:
    request = _valid_request()
    foreign = replace(request.authorities[0], retrieval_run_id=uuid4())
    _assert_rejected(
        replace(request, authorities=(foreign, request.authorities[1])),
        AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_BINDING_MISMATCH,
    )


def test_authority_source_snapshot_mismatch_is_binding_mismatch() -> None:
    request = _valid_request()
    drifted = replace(request.authorities[0], source_snapshot_id=uuid4())
    _assert_rejected(
        replace(request, authorities=(drifted, request.authorities[1])),
        AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_BINDING_MISMATCH,
    )


def test_authority_source_member_mismatch_is_binding_mismatch() -> None:
    request = _valid_request()
    drifted = replace(request.authorities[0], source_snapshot_member_id=uuid4())
    _assert_rejected(
        replace(request, authorities=(drifted, request.authorities[1])),
        AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_BINDING_MISMATCH,
    )


def test_authority_source_code_mismatch_is_binding_mismatch() -> None:
    request = _valid_request()
    drifted = replace(request.authorities[0], source_code="EMA")
    _assert_rejected(
        replace(request, authorities=(drifted, request.authorities[1])),
        AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_BINDING_MISMATCH,
    )


def test_authority_source_version_mismatch_is_binding_mismatch() -> None:
    request = _valid_request()
    drifted = replace(request.authorities[0], source_version="2026.09.18")
    _assert_rejected(
        replace(request, authorities=(drifted, request.authorities[1])),
        AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_BINDING_MISMATCH,
    )


def test_authority_content_hash_mismatch_is_binding_mismatch() -> None:
    request = _valid_request()
    drifted = replace(request.authorities[0], content_sha256="e" * 64)
    _assert_rejected(
        replace(request, authorities=(drifted, request.authorities[1])),
        AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_BINDING_MISMATCH,
    )


# ==============================================================================
# EVIDENCE_KEY_SET_MISMATCH
# ==============================================================================


def test_duplicate_evidence_key_value_is_key_set_mismatch() -> None:
    request = _valid_request()
    first_key = request.hydrated_selections[0].selection.hit.provenance.evidence_key
    second = request.hydrated_selections[1]
    provenance = replace(second.selection.hit.provenance, evidence_key=first_key)
    hit = replace(second.selection.hit, provenance=provenance)
    selection = replace(second.selection, hit=hit)
    duplicated = replace(second, selection=selection)
    _assert_rejected(
        replace(request, hydrated_selections=(request.hydrated_selections[0], duplicated)),
        AuthoritativeGuideEvidenceAssemblyReason.EVIDENCE_KEY_SET_MISMATCH,
    )


# ==============================================================================
# HANDOFF_REJECTED: the existing kernel keeps owning assessment validity
# ==============================================================================


def test_assessment_not_yet_valid_preserves_the_existing_handoff_reason() -> None:
    request = _valid_request()
    later = datetime(2026, 9, 18, 10, 0, 0, tzinfo=UTC)
    authorities = tuple(_authority(h.selection.hit, evaluated_at=later) for h in request.hydrated_selections)

    outcome = assemble_authoritative_guide_evidence_handoff(replace(request, authorities=authorities))

    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.REJECTED
    assert outcome.reasons == (AuthoritativeGuideEvidenceAssemblyReason.HANDOFF_REJECTED,)
    assert outcome.build_outcome is not None
    assert outcome.build_outcome.decision is GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.ASSESSMENT_NOT_YET_VALID in outcome.build_outcome.reasons
    assert outcome.build_outcome.handoff is None


def test_expired_assessment_preserves_the_existing_handoff_reason() -> None:
    request = _valid_request()
    earlier = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
    authorities = tuple(_authority(h.selection.hit, evaluated_at=earlier) for h in request.hydrated_selections)

    outcome = assemble_authoritative_guide_evidence_handoff(replace(request, authorities=authorities))

    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.REJECTED
    assert outcome.reasons == (AuthoritativeGuideEvidenceAssemblyReason.HANDOFF_REJECTED,)
    assert outcome.build_outcome is not None
    assert outcome.build_outcome.decision is GuideEvidenceHandoffBuildDecision.REJECTED
    assert GuideEvidenceHandoffReason.ASSESSMENT_EXPIRED in outcome.build_outcome.reasons
    assert outcome.build_outcome.handoff is None


def test_duplicate_hydrated_chunk_is_left_to_the_existing_handoff_kernel() -> None:
    """assembly는 #697/#715가 이미 소유한 duplicate 판정을 복제하지 않는다."""
    request = _valid_request(selection_count=1)
    duplicated = (*request.hydrated_selections, request.hydrated_selections[0])
    persisted = _persisted_receipt(request.retrieval_receipt, selected_count=2)

    outcome = assemble_authoritative_guide_evidence_handoff(
        replace(request, hydrated_selections=duplicated, persisted_retrieval_receipt=persisted)
    )

    assert outcome.decision is AuthoritativeGuideEvidenceAssemblyDecision.REJECTED
    assert outcome.reasons == (AuthoritativeGuideEvidenceAssemblyReason.HANDOFF_REJECTED,)
    assert outcome.build_outcome is not None
    assert GuideEvidenceHandoffReason.DUPLICATE_KNOWLEDGE_CHUNK_ID in outcome.build_outcome.reasons
