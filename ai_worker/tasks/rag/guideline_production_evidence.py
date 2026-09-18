"""RAG-15 Production Guideline Evidence input contract (#774).

RAG-15 Generator/Card가 소비하는 production evidence 정본을 정의한다. 상류 정본은
#760 `VerifiedGuideEvidenceHandoff`이며, 이 모듈은 그 handoff에서 RAG-15가 실제로
소비하는 최소 필드만 순수하게 투영한다.

Scope & Authority Boundaries:
- Legacy Domain 분리: RAG-14 `evidence_gate.EvidenceGateOutcome` /
  `GatePassedKnowledgeEvidenceSelection` / `canonical_gate_selection_hash()`는 production
  입력이 아니다. 이 모듈은 legacy receipt/hash domain을 import하지도, 재구성하지도,
  wrapping하지도 않는다. `VerifiedGuideEvidenceHandoff → EvidenceGateOutcome` converter는
  존재하지 않으며 만들지 않는다.
- Pure Projection: DB, network, clock, normalization, fallback, authority 재판정이 없다.
  #760이 이미 검증한 Source/Member/Assessment/Content authority와 assessment 유효기간
  freshness를 다시 평가하지 않는다. 투영 실패 개념이 없으므로 outcome/reason enum도 없다.
- No Legacy Status Replication: legacy Gate의 `SUCCEEDED` / `SUFFICIENT` /
  `EVIDENCE_INSUFFICIENT` / `EVIDENCE_STALE` 상태를 복제하지 않는다. production path에서는
  #760 handoff가 RAG-15보다 먼저 fail closed되므로, RAG-15에 도달한 evidence set은 이미
  검증된 non-empty selection만 담는다.
- Hash Domain 분리: `compute_production_guideline_evidence_selection_hash()`는
  `canonical_gate_selection_hash()`와 preimage 호환이 없는 별도 public domain이다.
  `ApprovedGuidelineEvidenceBinding.selection_projection_sha256`의 production 의미는
  이 함수의 결과다.
- Not Implemented Here: Dynamic Guideline Evidence Binding Authority, binding issuer,
  binding persistence, `GuidelineApprovalVerifier` production adapter, Citation
  Authorization, Release Gate는 이 모듈의 책임이 아니다. 이 모듈은 그것들이 나중에
  exact-bind할 canonical projection과 hash만 확정한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.guide_evidence_handoff import (
    VerifiedGuideEvidenceHandoff,
    VerifiedGuideEvidenceSelection,
    canonical_jcs_sha256,
)

__all__ = [
    "PRODUCTION_GUIDELINE_EVIDENCE_SELECTION_PROJECTION_VERSION",
    "ProductionGuidelineEvidence",
    "ProductionGuidelineEvidenceSet",
    "canonical_production_guideline_evidence_selection_projection",
    "compute_production_guideline_evidence_selection_hash",
    "project_guideline_evidence_from_handoff",
]

PRODUCTION_GUIDELINE_EVIDENCE_SELECTION_PROJECTION_VERSION = "guideline-production-evidence-selection-v1"


@dataclass(frozen=True, slots=True)
class ProductionGuidelineEvidence:
    """RAG-15가 소비하는 최소 production evidence selection.

    `VerifiedGuideEvidenceSelection`의 31개 필드를 기계적으로 복사하지 않는다. 각 필드는
    아래 네 소비 경로 중 하나 이상에 실제로 필요해서 포함된다.

    - `evidence_key`: Provider slot 복원 키, `GuidelineCitation.evidence_key`,
      `ApprovedGuidelineEvidenceBinding.evidence_key` 결속 키.
    - `source_snapshot_id` / `source_snapshot_member_id` / `source_code`: Citation의
      production source identity. legacy `source_snapshot_ref` artifact 참조를
      위조하지 않고 #760이 검증한 Source 좌표를 그대로 사용한다.
    - `source_version` / `locator` / `content_sha256`: Citation 복원 및 draft citation
      exact-match 검증에 필요하다. `content_sha256`이 raw text를 canonical projection
      밖에서 결속한다.
    - `content_text`: Provider projection에 노출하는 유일한 본문이다. canonical
      projection에는 포함하지 않는다.
    - `retrieval_receipt_ref` / `eligibility_receipt_ref` / `assessment_artifact_ref` /
      `verifier_artifact_ref`: `GuidelineCitation`이 보존하는 상류 authority 참조이며,
      `assessment_artifact_ref`는 binding exact-match 대상이다.

    의도적으로 제외한 upstream audit-only 필드:
    `knowledge_chunk_id`, `member_kind`, `endpoint_code`, `operation_code`,
    `artifact_code`, `artifact_version`, `request_guard_ref`, `request_operation_code`,
    `request_decision_stage`, `request_source_decision_ref`,
    `request_member_decision_ref`, `assessment_valid_from`, `assessment_valid_until`,
    `final_rank`, `canonical_checksum`, `external_document_id`, `chunk_index`,
    `canonicalization_spec_version`, `normalization_version`.

    assessment 유효기간은 #760이 `evaluated_at` 기준으로 이미 fail closed 판정했으므로
    RAG-15가 재판정하지 않는다. `final_rank`는 상류 retrieval 순위 감사값이고 RAG-15의
    canonical 순서는 `(source_version, locator, evidence_key)`이므로 제외한다. 제외한
    필드는 모두 #760 `handoff_sha256`에 이미 결속되어 있다.
    """

    evidence_key: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    locator: str
    content_sha256: str
    content_text: SensitiveText
    retrieval_receipt_ref: ImmutableArtifactRef
    eligibility_receipt_ref: ImmutableArtifactRef
    assessment_artifact_ref: ImmutableArtifactRef
    verifier_artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class ProductionGuidelineEvidenceSet:
    """한 #760 handoff에서 투영된 RAG-15 production evidence 전체.

    `evaluated_at`은 handoff의 평가 시각을 그대로 옮긴다. Card는 자신의
    `evaluated_at`이 이 값과 정확히 같은지만 확인하며(같은 요청 평가인지),
    유효기간 판정은 하지 않는다. `handoff_sha256`은 상류 authority anchor이고
    RAG-15는 이를 재계산하거나 재검증하지 않는다.
    """

    evaluated_at: datetime
    handoff_sha256: str
    selections: tuple[ProductionGuidelineEvidence, ...]


def _artifact_projection(ref: ImmutableArtifactRef) -> dict[str, str]:
    return {
        "artifact_code": ref.artifact_code,
        "content_sha256": ref.content_sha256,
        "version": ref.version,
    }


def canonical_production_guideline_evidence_selection_projection(
    selection: ProductionGuidelineEvidence,
) -> dict[str, object]:
    """production selection의 canonical projection을 만든다.

    포함 기준은 "future Dynamic Guideline Evidence Binding Authority가 exact-bind해야
    하는 immutable fact"다. 필드별 근거:

    - `evidence_key`: binding이 결속하는 evidence 신원.
    - `source_snapshot_id` / `source_snapshot_member_id` / `source_code` /
      `source_version`: 승인된 Source 좌표. 다른 snapshot·member·version의 동일 본문을
      같은 binding으로 재사용할 수 없게 한다.
    - `locator`: snapshot 내 인용 위치. 같은 문서의 다른 위치를 재사용할 수 없게 한다.
    - `content_sha256`: 본문 무결성. raw `content_text`를 projection에서 제외하므로
      본문 결속은 전적으로 이 hash가 담당한다.
    - `assessment_artifact_ref`: 이 evidence를 승인한 assessment.
    - `verifier_artifact_ref`: 그 assessment를 검증한 verifier. assessment만 고정하면
      동일 assessment가 다른 verifier로 검증된 경우를 구분할 수 없으므로 함께 고정한다.
    - `eligibility_receipt_ref` / `retrieval_receipt_ref`: 이 evidence가 통과한
      eligibility 판정과 그것을 선택한 retrieval 실행.

    `content_text`는 의도적으로 제외한다(raw 본문 미포함 요구). 따라서 본문이 바뀌면
    `content_sha256`이 바뀌어야만 hash가 바뀐다. `content_sha256`과 실제 본문의 일치는
    #760이 이미 검증했고 Card가 draft citation 결속 시 다시 확인한다.
    """
    return {
        "assessment_artifact_ref": _artifact_projection(selection.assessment_artifact_ref),
        "content_sha256": selection.content_sha256,
        "eligibility_receipt_ref": _artifact_projection(selection.eligibility_receipt_ref),
        "evidence_key": selection.evidence_key,
        "locator": selection.locator,
        "projection_version": PRODUCTION_GUIDELINE_EVIDENCE_SELECTION_PROJECTION_VERSION,
        "retrieval_receipt_ref": _artifact_projection(selection.retrieval_receipt_ref),
        "source_code": selection.source_code,
        "source_snapshot_id": str(selection.source_snapshot_id),
        "source_snapshot_member_id": str(selection.source_snapshot_member_id),
        "source_version": selection.source_version,
        "verifier_artifact_ref": _artifact_projection(selection.verifier_artifact_ref),
    }


def compute_production_guideline_evidence_selection_hash(
    selection: ProductionGuidelineEvidence,
) -> str:
    """production selection canonical projection의 RFC 8785 JCS SHA-256.

    `ApprovedGuidelineEvidenceBinding.selection_projection_sha256`의 production 의미다.
    legacy `canonical_gate_selection_hash()`와 preimage 호환이 없고, legacy 값을
    production binding으로 인정하지 않는다.
    """
    return canonical_jcs_sha256(canonical_production_guideline_evidence_selection_projection(selection))


def _production_selection_order(selection: ProductionGuidelineEvidence) -> tuple[str, str, str]:
    """RAG-15 canonical selection 순서.

    Provider projection과 citation 복원이 쓰는 순서와 동일하다. 상류 `final_rank`를
    쓰지 않으므로 이 순서는 retrieval 순위 변동과 무관하게 안정적이다.
    """
    return (selection.source_version, selection.locator, selection.evidence_key)


def _project_selection(selection: VerifiedGuideEvidenceSelection) -> ProductionGuidelineEvidence:
    return ProductionGuidelineEvidence(
        evidence_key=selection.evidence_key,
        source_snapshot_id=selection.source_snapshot_id,
        source_snapshot_member_id=selection.source_snapshot_member_id,
        source_code=selection.source_code,
        source_version=selection.source_version,
        locator=selection.locator,
        content_sha256=selection.content_sha256,
        content_text=SensitiveText(selection.content_text.reveal()),
        retrieval_receipt_ref=_copy_artifact_ref(selection.retrieval_receipt_ref),
        eligibility_receipt_ref=_copy_artifact_ref(selection.eligibility_receipt_ref),
        assessment_artifact_ref=_copy_artifact_ref(selection.assessment_artifact_ref),
        verifier_artifact_ref=_copy_artifact_ref(selection.verifier_artifact_ref),
    )


def _copy_artifact_ref(value: ImmutableArtifactRef) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(value.artifact_code, value.version, value.content_sha256)


def project_guideline_evidence_from_handoff(
    handoff: VerifiedGuideEvidenceHandoff,
) -> ProductionGuidelineEvidenceSet:
    """#760 handoff에서 RAG-15 production evidence set을 순수하게 투영한다.

    DB/network/clock이 없고, normalization·fallback·authority 재판정이 없다. handoff가
    이미 검증한 사실을 다시 판정하지 않으므로 실패 경로가 없다. selection은 RAG-15
    canonical 순서로 정렬되며, 상류가 보장한 `evidence_key` 유일성 덕에 순서는 결정적이다.
    """
    projected = [_project_selection(item) for item in handoff.selections]
    return ProductionGuidelineEvidenceSet(
        evaluated_at=handoff.evaluated_at,
        handoff_sha256=handoff.handoff_sha256,
        selections=tuple(sorted(projected, key=_production_selection_order)),
    )
