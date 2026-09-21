"""Authoritative Guide Evidence Handoff Assembly (#760).

#709/#697/#715가 확정한 hydrated production retrieval selection과 #712/#746이 남긴
persisted Assessment·Eligibility authority를 exact binding으로 결속해 기존
`GuideEvidenceSelectionRequest` / `GuideEvidenceHandoffRequest`를 만들고 기존
`build_guide_evidence_handoff()`에 넘기는 단일 assembly seam입니다.

Scope & Authority Boundaries:
- Pure Seam: DB, network, clock, persistence I/O가 없습니다. `backend.*`, `sqlalchemy.*`,
  `ai_worker.adapters.*`를 import하지 않으며, caller가 이미 읽어온 authoritative 결과만
  소비합니다. #746 Reader를 직접 호출하지 않습니다.
- No New Authority: eligibility, assessment, Source CURRENT/ACTIVE, validity window,
  ranking을 재판정하거나 재산출하지 않습니다. assessment validity(not yet valid / expired)
  판정은 기존 Handoff kernel이 계속 소유합니다.
- No New Hash Domain: `PersistedRetrievalRunReceipt.receipt_hash`는 기존
  `compute_receipt_hash()`로만 재검증합니다. 새 receipt/hash/evidence-key 도메인을 만들지
  않습니다.
- Why PersistedRetrievalRunReceipt: `PersistedEvidenceAuthority`의 selection identity는
  (`retrieval_run_id`, `knowledge_chunk_id`)인데 `ProductionSearchReceipt`에는
  `retrieval_run_id`가 없습니다. 따라서 authority를 실제 retrieval execution에 결속하려면
  persisted run receipt가 함께 필요합니다.
- Authority Index Key: authority는 `knowledge_chunk_id`로만 index합니다. authority tuple
  순서는 matching에 쓰지 않으며, 출력 순서는 언제나 `hydrated_selections` 순서입니다.
  chunk identity가 index key이므로 authority의 chunk 불일치는 언제나 set 사실
  (AUTHORITY_SET_MISMATCH)이고, 나머지 결속 필드 불일치만 AUTHORITY_BINDING_MISMATCH입니다.
- Evidence Key Boundary: evidence key 생성 정책은 이 seam이 소유하지 않습니다. hydrated
  production provenance가 같은 index-member row에서 읽은 opaque `evidence_key`를 그대로
  소비하고 `(source_snapshot_id, evidence_key)` anchor가 한 chunk에만 결속되는지만 확인합니다.
  rank/chunk id/uuid/hash 기반 생성을 하지 않습니다.
- Caller-Supplied Clock: `evaluated_at`은 caller가 명시적으로 전달하는 handoff evaluation
  timestamp입니다. 내부에서 `datetime.now()`나 DB clock을 쓰지 않고, timezone-aware UTC가
  아니면 fail closed합니다.
- Artifact Ref Transport: `rag_runtime.evidence_authority.ImmutableArtifactRef`는
  `PD-175-20260910` import 경계 때문에 AI Worker의 `ImmutableArtifactRef`와 별개 타입입니다.
  `request_authority_artifact.worker_artifact_ref()`와 같은 무손실 field-for-field 이동만
  수행하며 ref를 재계산하지 않습니다.
- Fail-Closed Atomicity: 검증은 phase-ordered fail-fast이고, assembly 단계 거부는 단일 typed
  reason과 `build_outcome = None`을 돌려줍니다. 기존 Handoff Builder까지 도달한 뒤의 거부는
  HANDOFF_REJECTED 하나로 표시하되 원본 `GuideEvidenceHandoffBuildOutcome`을 그대로 보존해
  기존 `GuideEvidenceHandoffReason`을 잃거나 다시 mapping하지 않습니다.
- Excluded Material: Generator, Citation Authorization, Release Gate, LangGraph, Guide API,
  persistence는 이 seam의 책임이 아닙니다. 결과는 `GuideEvidenceHandoffBuildOutcome`에서
  멈춥니다.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.guide_evidence_handoff import (
    GuideEvidenceHandoffBuildDecision,
    GuideEvidenceHandoffBuildOutcome,
    GuideEvidenceHandoffRequest,
    GuideEvidenceSelectionRequest,
    build_guide_evidence_handoff,
)
from ai_worker.tasks.rag.knowledge_chunk_content_hydration import HydratedGuideRetrievalSelection
from ai_worker.tasks.rag.retrieval_run import PersistedRetrievalRunReceipt, compute_receipt_hash
from ai_worker.tasks.rag.retrieval_runtime import ProductionSearchReceipt
from rag_runtime.evidence_authority import ImmutableArtifactRef as SharedArtifactRef
from rag_runtime.evidence_authority import PersistedEvidenceAuthority

__all__ = [
    "AuthoritativeGuideEvidenceAssemblyDecision",
    "AuthoritativeGuideEvidenceAssemblyOutcome",
    "AuthoritativeGuideEvidenceAssemblyReason",
    "AuthoritativeGuideEvidenceHandoffAssemblyRequest",
    "assemble_authoritative_guide_evidence_handoff",
]

RETRIEVAL_RUN_VARIANT = "RET-H"
RETRIEVAL_RUN_COMPLETED_STATUS = "COMPLETED"


class AuthoritativeGuideEvidenceAssemblyDecision(StrEnum):
    BUILT = "BUILT"
    REJECTED = "REJECTED"


class AuthoritativeGuideEvidenceAssemblyReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    RETRIEVAL_RUN_RECEIPT_MISMATCH = "RETRIEVAL_RUN_RECEIPT_MISMATCH"
    AUTHORITY_SET_MISMATCH = "AUTHORITY_SET_MISMATCH"
    AUTHORITY_BINDING_MISMATCH = "AUTHORITY_BINDING_MISMATCH"
    EVIDENCE_KEY_SET_MISMATCH = "EVIDENCE_KEY_SET_MISMATCH"
    HANDOFF_REJECTED = "HANDOFF_REJECTED"


@dataclass(frozen=True, slots=True)
class AuthoritativeGuideEvidenceHandoffAssemblyRequest:
    persisted_retrieval_receipt: PersistedRetrievalRunReceipt
    retrieval_receipt: ProductionSearchReceipt
    hydrated_selections: tuple[HydratedGuideRetrievalSelection, ...]
    authorities: tuple[PersistedEvidenceAuthority, ...]
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class AuthoritativeGuideEvidenceAssemblyOutcome:
    decision: AuthoritativeGuideEvidenceAssemblyDecision
    reasons: tuple[AuthoritativeGuideEvidenceAssemblyReason, ...]
    build_outcome: GuideEvidenceHandoffBuildOutcome | None


def _rejected(
    reason: AuthoritativeGuideEvidenceAssemblyReason,
) -> AuthoritativeGuideEvidenceAssemblyOutcome:
    return AuthoritativeGuideEvidenceAssemblyOutcome(
        decision=AuthoritativeGuideEvidenceAssemblyDecision.REJECTED,
        reasons=(reason,),
        build_outcome=None,
    )


def _is_utc_datetime(value: object) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _worker_artifact_ref(value: SharedArtifactRef) -> ImmutableArtifactRef:
    """공유 `rag_runtime` authority ref를 AI Worker ref로 무손실 이동한다.

    `request_authority_artifact.worker_artifact_ref()`와 같은 transport다. 값은 그대로
    옮기며 artifact identity를 재계산하지 않는다.
    """
    return ImmutableArtifactRef(
        artifact_code=value.artifact_code,
        version=value.version,
        content_sha256=value.content_sha256,
    )


def _request_shape_is_valid(request: AuthoritativeGuideEvidenceHandoffAssemblyRequest) -> bool:
    """입력이 상류 production 산출물의 정본 타입으로 채워졌는지만 확인한다."""
    if type(request) is not AuthoritativeGuideEvidenceHandoffAssemblyRequest:
        return False
    if (
        type(request.persisted_retrieval_receipt) is not PersistedRetrievalRunReceipt
        or type(request.retrieval_receipt) is not ProductionSearchReceipt
        or type(request.hydrated_selections) is not tuple
        or not request.hydrated_selections
        or type(request.authorities) is not tuple
        or not request.authorities
        or not _is_utc_datetime(request.evaluated_at)
    ):
        return False
    for hydrated in request.hydrated_selections:
        if type(hydrated) is not HydratedGuideRetrievalSelection:
            return False
    for authority in request.authorities:
        if type(authority) is not PersistedEvidenceAuthority:
            return False
    return True


def _check_persisted_retrieval_receipt(
    request: AuthoritativeGuideEvidenceHandoffAssemblyRequest,
) -> AuthoritativeGuideEvidenceAssemblyReason | None:
    """Persisted run receipt의 자기무결성과 production search receipt 결속을 exact 검증한다."""
    persisted = request.persisted_retrieval_receipt
    search_receipt = request.retrieval_receipt

    recomputed = compute_receipt_hash(
        run_id=persisted.run_id,
        job_id=persisted.job_id,
        node_id=persisted.node_id,
        variant=persisted.variant,
        status=persisted.status,
        query_digest=persisted.query_digest,
        retrieval_configuration_hash=persisted.retrieval_configuration_hash,
        source_manifest_hash=persisted.source_manifest_hash,
        search_receipt_hash=persisted.search_receipt_hash,
        total_signals=persisted.total_signals,
        total_hits=persisted.total_hits,
        selected_count=persisted.selected_count,
        signal_manifest_hash=persisted.signal_manifest_hash,
        hit_manifest_hash=persisted.hit_manifest_hash,
        terminal_replay_payload_hash=persisted.terminal_replay_payload_hash,
    )
    if recomputed != persisted.receipt_hash:
        return AuthoritativeGuideEvidenceAssemblyReason.RETRIEVAL_RUN_RECEIPT_MISMATCH

    if (
        persisted.variant != RETRIEVAL_RUN_VARIANT
        or persisted.status != RETRIEVAL_RUN_COMPLETED_STATUS
        or persisted.variant != search_receipt.variant
        or persisted.search_receipt_hash != search_receipt.artifact_ref.content_sha256
        or persisted.signal_manifest_hash != search_receipt.signal_manifest_sha256
        or persisted.hit_manifest_hash != search_receipt.hit_manifest_sha256
        or persisted.selected_count != len(request.hydrated_selections)
    ):
        return AuthoritativeGuideEvidenceAssemblyReason.RETRIEVAL_RUN_RECEIPT_MISMATCH

    return None


def _index_authorities(
    request: AuthoritativeGuideEvidenceHandoffAssemblyRequest,
) -> tuple[dict[UUID, PersistedEvidenceAuthority] | None, AuthoritativeGuideEvidenceAssemblyReason | None]:
    """authority를 knowledge_chunk_id로 index하고 duplicate/missing/extra를 fail closed한다."""
    indexed: dict[UUID, PersistedEvidenceAuthority] = {}
    for authority in request.authorities:
        if authority.knowledge_chunk_id in indexed:
            return None, AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_SET_MISMATCH
        indexed[authority.knowledge_chunk_id] = authority

    hydrated_chunk_ids = {
        hydrated.selection.hit.provenance.knowledge_chunk_id for hydrated in request.hydrated_selections
    }
    if set(indexed) != hydrated_chunk_ids:
        return None, AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_SET_MISMATCH

    return indexed, None


def _check_authority_binding(
    hydrated: HydratedGuideRetrievalSelection,
    authority: PersistedEvidenceAuthority,
    run_id: UUID,
) -> AuthoritativeGuideEvidenceAssemblyReason | None:
    """authority가 실제 retrieval execution과 선택된 chunk에 exact bind됐는지 확인한다.

    normalize, trim, case-fold, fallback은 하지 않는다. `knowledge_chunk_id`는 authority
    index key라서 이미 동일하므로 여기서 다시 비교하지 않는다.
    """
    provenance = hydrated.selection.hit.provenance
    if (
        authority.retrieval_run_id != run_id
        or authority.source_snapshot_id != provenance.source_snapshot_id
        or authority.source_snapshot_member_id != provenance.source_snapshot_member_id
        or authority.source_code != provenance.source_code
        or authority.source_version != provenance.source_version
        or authority.content_sha256 != provenance.content_hash
    ):
        return AuthoritativeGuideEvidenceAssemblyReason.AUTHORITY_BINDING_MISMATCH
    return None


def _check_evidence_key_set(
    request: AuthoritativeGuideEvidenceHandoffAssemblyRequest,
) -> AuthoritativeGuideEvidenceAssemblyReason | None:
    """Persisted snapshot/key anchor가 selection 안에서 한 chunk만 가리키는지 확인한다."""
    chunks_by_anchor: dict[tuple[UUID, str], UUID] = {}
    for hydrated in request.hydrated_selections:
        provenance = hydrated.selection.hit.provenance
        value = provenance.evidence_key
        if (
            not isinstance(value, str)
            or not value
            or value != value.strip()
            or not unicodedata.is_normalized("NFC", value)
        ):
            return AuthoritativeGuideEvidenceAssemblyReason.EVIDENCE_KEY_SET_MISMATCH
        anchor = (provenance.source_snapshot_id, value)
        existing_chunk = chunks_by_anchor.setdefault(anchor, provenance.knowledge_chunk_id)
        if existing_chunk != provenance.knowledge_chunk_id:
            return AuthoritativeGuideEvidenceAssemblyReason.EVIDENCE_KEY_SET_MISMATCH

    return None


def assemble_authoritative_guide_evidence_handoff(
    request: AuthoritativeGuideEvidenceHandoffAssemblyRequest,
) -> AuthoritativeGuideEvidenceAssemblyOutcome:
    """Persisted authority와 hydrated selection을 결속해 기존 Handoff Builder를 호출한다.

    검증은 phase-ordered fail-fast이고, assembly 단계 거부는 단일 typed reason을 돌려준다:
    - Phase 1: 입력 구조와 caller-supplied UTC `evaluated_at` (REQUEST_INVALID)
    - Phase 2: persisted run receipt 자기무결성과 production search receipt 결속
      (RETRIEVAL_RUN_RECEIPT_MISMATCH)
    - Phase 3: chunk 단위 authority 집합 cardinality (AUTHORITY_SET_MISMATCH)
    - Phase 4: selection별 run/snapshot/member/source/content exact binding
      (AUTHORITY_BINDING_MISMATCH)
    - Phase 5: persisted member evidence key 유효성·유일성 (EVIDENCE_KEY_SET_MISMATCH)
    - Phase 6: 기존 `build_guide_evidence_handoff()` 위임 (HANDOFF_REJECTED)

    Phase 6에서 거부되면 원본 `GuideEvidenceHandoffBuildOutcome`을 그대로 실어 보내므로
    기존 `GuideEvidenceHandoffReason`(예: ASSESSMENT_EXPIRED)이 보존된다. 출력 selection
    순서는 언제나 `hydrated_selections` 순서이며 이 seam은 별도 ranking/sorting을 하지 않는다.
    """
    # --------------------------------------------------------------------------
    # Phase 1: Request Shape & Caller-Supplied Clock
    # --------------------------------------------------------------------------
    if not _request_shape_is_valid(request):
        return _rejected(AuthoritativeGuideEvidenceAssemblyReason.REQUEST_INVALID)

    # --------------------------------------------------------------------------
    # Phase 2: Persisted Retrieval Run Receipt Integrity & Execution Binding
    # --------------------------------------------------------------------------
    receipt_error = _check_persisted_retrieval_receipt(request)
    if receipt_error is not None:
        return _rejected(receipt_error)

    # --------------------------------------------------------------------------
    # Phase 3: Authority Set Cardinality (chunk unit)
    # --------------------------------------------------------------------------
    authorities_by_chunk, set_error = _index_authorities(request)
    if set_error is not None:
        return _rejected(set_error)
    assert authorities_by_chunk is not None

    # --------------------------------------------------------------------------
    # Phase 4: Selection <-> Authority Exact Binding
    # --------------------------------------------------------------------------
    run_id = request.persisted_retrieval_receipt.run_id
    for hydrated in request.hydrated_selections:
        authority = authorities_by_chunk[hydrated.selection.hit.provenance.knowledge_chunk_id]
        binding_error = _check_authority_binding(hydrated, authority, run_id)
        if binding_error is not None:
            return _rejected(binding_error)

    # --------------------------------------------------------------------------
    # Phase 5: Persisted Member Evidence Keys
    # --------------------------------------------------------------------------
    key_error = _check_evidence_key_set(request)
    if key_error is not None:
        return _rejected(key_error)

    # --------------------------------------------------------------------------
    # Phase 6: Existing Handoff Builder
    # --------------------------------------------------------------------------
    selections = tuple(
        _build_selection_request(request, hydrated, authorities_by_chunk) for hydrated in request.hydrated_selections
    )
    build_outcome = build_guide_evidence_handoff(
        GuideEvidenceHandoffRequest(
            retrieval_receipt=request.retrieval_receipt,
            selections=selections,
            evaluated_at=request.evaluated_at,
        )
    )
    if build_outcome.decision is not GuideEvidenceHandoffBuildDecision.BUILT:
        return AuthoritativeGuideEvidenceAssemblyOutcome(
            decision=AuthoritativeGuideEvidenceAssemblyDecision.REJECTED,
            reasons=(AuthoritativeGuideEvidenceAssemblyReason.HANDOFF_REJECTED,),
            build_outcome=build_outcome,
        )

    return AuthoritativeGuideEvidenceAssemblyOutcome(
        decision=AuthoritativeGuideEvidenceAssemblyDecision.BUILT,
        reasons=(),
        build_outcome=build_outcome,
    )


def _build_selection_request(
    request: AuthoritativeGuideEvidenceHandoffAssemblyRequest,
    hydrated: HydratedGuideRetrievalSelection,
    authorities_by_chunk: dict[UUID, PersistedEvidenceAuthority],
) -> GuideEvidenceSelectionRequest:
    """상류가 확정한 값만 그대로 옮겨 하나의 selection request를 만든다.

    hit / binding / content를 재구성하거나 normalize하지 않고, authority ref와 validity
    window를 재계산하지 않는다.
    """
    chunk_id = hydrated.selection.hit.provenance.knowledge_chunk_id
    authority = authorities_by_chunk[chunk_id]
    return GuideEvidenceSelectionRequest(
        hit=hydrated.selection.hit,
        binding=hydrated.selection.binding,
        evidence_key=hydrated.selection.hit.provenance.evidence_key,
        content_text=hydrated.content_text,
        retrieval_receipt_ref=request.retrieval_receipt.artifact_ref,
        eligibility_receipt_ref=_worker_artifact_ref(authority.eligibility_receipt_ref),
        assessment_artifact_ref=_worker_artifact_ref(authority.assessment_artifact_ref),
        verifier_artifact_ref=_worker_artifact_ref(authority.verifier_artifact_ref),
        assessment_valid_from=authority.assessment_valid_from,
        assessment_valid_until=authority.assessment_valid_until,
        content_sha256=authority.content_sha256,
    )
