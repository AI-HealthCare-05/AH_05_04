# Guide Evidence Handoff Contract Kernel v1 (#180 선행 계약)

- **상태**: Proposed / Review pending (pure contract kernel implemented in branch; authority issuance·persistence는 #712, production 조회는 #709/#746, authoritative assembly는 #760에서 구현됨. #180 runtime orchestration·persistence·E2E는 여전히 미구현)
- **책임자**: 구현 정현우 (`@ceohwj`), 단일 책임 리뷰어 권가빈 (`@hazelnutflavoured`)
- **필요 교차 리뷰**: 송은영 (`@phina-io`, #174 Backend/DB/REQUEST Guard), 김지혜 (`@Jye-rookie`, Source/Worker provenance)
- **상위 근거**: Issue #180, Decision [`PD-180-20260915`](../../../governance/decisions/2026-09-15-guide-evidence-handoff.md), [`PD-362-20260909`](../../../governance/decisions/2026-09-09-source-snapshot-approval-boundary.md), [`PD-315-20260908`](../../../governance/decisions/2026-09-08-production-evidence-retrieval-contract-divergence.md), [`PD-125-20260831`](../../../governance/decisions/2026-08-31-rag-p0-contract-freeze.md), [`PD-722-20260917`](../../../governance/decisions/2026-09-17-evidence-assessment-validity.md)

---

## 1. 목적과 권위 한계

본 문서는 Issue #180 Guide-only Runtime Orchestration 구현의 사전 필수 요건으로서, #174 REQUEST Guard의 Source/Member 결정 관측 결과(opaque caller observations)와 #178 Production Evidence Retrieval 결과 사이의 결정론적 결속(exact-bind)을 검증하고, #179 Guideline Card kernel이 소비할 불변 Handoff 구조를 정의하는 공유 계약이다.

### 1.1 권위 한계 (Authority Boundary)
1. **순수 검증 및 결속 한계 (Pure Kernel Boundary)**:
   - 본 순수 계약 커널은 독자적인 권한(authority)을 발급하지 않는다.
   - 데이터베이스 존재 여부나 발급자의 진위(issuer authenticity)를 증명하거나 외부 인가를 수행하지 않는다.
   - `assessment_artifact_ref`, `eligibility_receipt_ref`, `verifier_artifact_ref`, `request_guard_ref`, `request_source_decision_ref`, `request_member_decision_ref`는 불변 아티팩트 참조 규격에 따른 불투명 관측 출처 참조(opaque observed provenance ref)이자 구조적 결속일 뿐이다.
   - `Verified`는 호출자가 전달한 불투명 관측 결과(opaque caller observations: `request_guard_ref`, `request_source_decision_ref`, `request_member_decision_ref`, `retrieval_receipt`, `eligibility_receipt_ref`, `assessment_artifact_ref`, `verifier_artifact_ref`)의 구조·해시·식별자 일관성(structure, hash, and identity consistency)만을 메모리 상에서 결정론적으로 검증한다는 의미로 엄격히 제한된다.
   - **Authoritative Assembly 의존성**: 이 커널 자체는 caller가 전달한 opaque 관측을 재검증할 뿐이므로, 상위 authority 경계가 결속되기 전에는 #180 runtime이 이 handoff를 독자적 authority로 직접 소비할 수 없다. 이 책임은 과거 `#174 authenticated assembler`로 표기되었으나, 현재 실제 담당은 `#709` REQUEST authority Reader → `#697` Authority × Retrieval exact join → `#715` Content Hydration과 `#712`/`#746` Assessment·Eligibility Issuer/Reader를 결속하는 [`authoritative-guide-evidence-handoff-assembly-v1`](./authoritative-guide-evidence-handoff-assembly-v1.md) (#760)이다. 이 정정은 문서 표기에 한정되며 #174 Issue 자체의 의미와 범위를 변경하지 않는다.
2. **#178 표준 JCS 정렬 및 차단 해소 (`PD-178-20260916`)**:
   - `retrieval-selection-manifest-v2` 및 `ProductionSearchReceipt` (v2.0)가 RFC 8785 JCS 규격(`canonical_json_bytes`)으로 정렬 완료되었다.
   - `BLOCKED_BY_178_CANONICAL_HASH_CONTRACT` 비강제 marker는 코드와 계약에서 완전히 제거되었으며 차단이 해소되었다.
   - 영수증 버전은 2.0만 허용하며 legacy 1.0은 fail-close(`RETRIEVAL_RECEIPT_MISMATCH`)된다.
   - **해시 도메인 독립성**: 공용 JCS 직렬화 모듈(`ai_worker.tasks.evaluation.canonical`)을 공유하지만, `guide-evidence-handoff-v1`, `retrieval-selection-manifest-v2`, `production-search-receipt-v2` 세 프로젝션 및 해시 도메인은 상호 완전히 독립적이며 각 도메인의 preimage가 혼용되지 않는다.
3. **#180 Endpoint Member 계약 정렬 및 차단 해소 (`PD-180-EM-20260916`)**:
   - 기존에는 PD-315/PD-362가 Endpoint Member의 `operation_code`를 nullable로 허용하는 반면 Citation validator와 Citation Authorization이 non-null 값을 요구하여, `BLOCKED_BY_180_ENDPOINT_MEMBER_CONTRACT` 비강제 marker로 차단 사항을 기록했었다.
   - **해소 경위**: `PD-180-EM-20260916` 결정 및 `source-member-identity-v1` 공유 순수 커널 도입을 통해 downstream validator의 nullable `operation_code` 계약 정렬이 완료되었으며, `BLOCKED_BY_180_ENDPOINT_MEMBER_CONTRACT` 비강제 marker는 코드와 계약에서 완전히 제거되고 typed 검증으로 대체되었다.
   - **Authority Boundary 유지**: 본 계약 정렬은 순수 검증 계층에 국한되며 runtime authority 활성화를 의미하지 않는다. PD-315 거버넌스 승인 조건은 해소(Issue #680 Path B 재판정)되었으나, #180에 남아 있는 runtime integration 조건 및 외부 공개 게이트는 별도로 충족되어야 한다. authoritative assembly seam은 #760에서 구현되었다.
4. **비식별 및 민감 텍스트 보호 경계 (SensitiveText Boundary)**:
   - 검색된 증거 원문(Content A)은 인메모리 `SensitiveText`로만 캡슐화되어 전달된다.
   - 영구 저장소, 로그, `repr`, 오류 사유(`reasons`), 해시 preimage에 원문 텍스트가 절대 포함되어서는 안 된다 (`<redacted>` 보호).
   - 해시 프로젝션에는 `content_sha256` 다이제스트만 포함된다.
5. **용어 불변성**:
   - 승인 및 인가 전 단계의 handoff 결과 객체는 `Authorized` 명칭을 사용할 수 없으며, 오직 `Verified` (`VerifiedGuideEvidenceHandoff`, `VerifiedGuideEvidenceSelection`) 명칭만 허용된다.
   - 검증 사유 이름은 provenance/binding 의미로 고정한다 (`OBSERVED_PROVENANCE_REF_REQUIRED` 등).

---

## 2. 정규 데이터 모델

### 2.1 RequestSourceMemberBinding (#174 REQUEST Guard 관측 결속)
```python
@dataclass(frozen=True)
class RequestSourceMemberBinding:
    request_guard_ref: ImmutableArtifactRef
    request_operation_code: str
    source_snapshot_id: UUID
    source_code: str
    source_version: str
    source_snapshot_member_id: UUID
    member_kind: SourceMemberKind
    endpoint_code: str | None
    operation_code: str | None
    artifact_code: str | None
    artifact_version: str | None
    request_source_decision_ref: ImmutableArtifactRef
    request_member_decision_ref: ImmutableArtifactRef
    observed_source_decision_outcome: ObservedDecisionOutcome
    observed_member_decision_outcome: ObservedDecisionOutcome
    request_decision_stage: RequestDecisionStage = RequestDecisionStage.REQUEST
```

### 2.2 GuideEvidenceHandoffRequest (입력 요청)
```python
@dataclass(frozen=True)
class GuideEvidenceSelectionRequest:
    evidence_key: str
    hit: ProductionSearchHit
    binding: RequestSourceMemberBinding
    content_text: SensitiveText
    retrieval_receipt_ref: ImmutableArtifactRef
    eligibility_receipt_ref: ImmutableArtifactRef
    assessment_artifact_ref: ImmutableArtifactRef
    verifier_artifact_ref: ImmutableArtifactRef
    assessment_valid_from: datetime
    assessment_valid_until: datetime
    content_sha256: str | None = None

@dataclass(frozen=True)
class GuideEvidenceHandoffRequest:
    retrieval_receipt: ProductionSearchReceipt
    selections: tuple[GuideEvidenceSelectionRequest, ...]
    evaluated_at: datetime
```

### 2.3 VerifiedGuideEvidenceHandoff (검증 완료 불변 Handoff)
```python
@dataclass(frozen=True)
class VerifiedGuideEvidenceSelection:
    evidence_key: str
    knowledge_chunk_id: UUID
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    locator: str
    content_sha256: str
    content_text: SensitiveText
    member_kind: SourceMemberKind
    endpoint_code: str | None
    operation_code: str | None
    artifact_code: str | None
    artifact_version: str | None
    request_guard_ref: ImmutableArtifactRef
    request_operation_code: str
    request_decision_stage: RequestDecisionStage
    request_source_decision_ref: ImmutableArtifactRef
    request_member_decision_ref: ImmutableArtifactRef
    retrieval_receipt_ref: ImmutableArtifactRef
    eligibility_receipt_ref: ImmutableArtifactRef
    assessment_artifact_ref: ImmutableArtifactRef
    verifier_artifact_ref: ImmutableArtifactRef
    assessment_valid_from: datetime
    assessment_valid_until: datetime
    final_rank: int
    canonical_checksum: str
    external_document_id: str
    chunk_index: int
    canonicalization_spec_version: str
    normalization_version: str

@dataclass(frozen=True)
class VerifiedGuideEvidenceHandoff:
    retrieval_receipt_ref: ImmutableArtifactRef
    retrieval_selection_manifest_sha256: str
    evaluated_at: datetime
    selections: tuple[VerifiedGuideEvidenceSelection, ...]
    handoff_sha256: str
```

---

## 3. 검증 규칙 (Validation Rules)

1. **JCS Canonical Serializer (RFC 8785, PD-315)**:
   - 프로젝션 직렬화는 RFC 8785 JCS 사양을 엄격히 준수한다.
   - 키 정렬은 UTF-16 code unit (big-endian byte order) 순서다.
   - `json.dumps(sort_keys=True)` 금지, `float` 금지, lone surrogate 금지.
   - 안전 정수 범위 $[-(2^{53}-1), 2^{53}-1]$, 명시적 `null` 지원.
   - Handoff JCS 및 Selection Manifest JCS는 동일한 공용 모듈(`ai_worker.tasks.evaluation.canonical`)의 RFC 8785 정규 직렬화기를 사용한다 (`PD-178-20260916`으로 해시 도메인 정렬 완료 및 `BLOCKED_BY_178_CANONICAL_HASH_CONTRACT` 해소).
2. **입력 형상, 타입 검증 및 Narrow Exception Handling**:
   - `GuideEvidenceHandoffRequest`, `ProductionSearchReceipt`, `GuideEvidenceSelectionRequest`, `ProductionSearchHit`, `RequestSourceMemberBinding`, `SensitiveText` 타입 확인.
   - UUID 필드(`knowledge_chunk_id`, `source_snapshot_id`, `source_snapshot_member_id`)는 정확한 `uuid.UUID` 타입이어야 함.
   - `source_code`, `source_version`, `locator`, `external_document_id`는 비어있지 않은 NFC 정규화 문자열이어야 함.
   - `chunk_index`는 0 이상의 정수여야 함.
   - broad `except Exception:`은 금지되며, 경계 파싱 시 `(AttributeError, TypeError, ValueError, UnicodeError)`만 좁게 처리하여 typed `REJECTED` (`REQUEST_INVALID`)로 변환한다. 내부 구현 결함(예: `RuntimeError`)은 숨기지 않고 전파한다.
   - `SensitiveText.reveal()`은 반드시 `str` 인스턴스여야 하며, 비-문자열이거나 reveal 실패 시 즉시 `REQUEST_INVALID`로 fail-closed 거부한다.
   - 모든 SHA-256 다이제스트 문자열은 정확히 64자리 소문자 16진수(`[0-9a-f]{64}`)여야 하며 `.lower()` 등의 자동 변환은 허용되지 않는다.
   - 빈 `selections`는 `REQUEST_INVALID`로 거부.
3. **정규 순서, Search Hit 불변식 및 중복 검증**:
   - `hit.fusion_rank`는 1 이상 5 이하의 정수여야 하며, 엄격한 오름차순($\text{rank}[i] > \text{rank}[i-1]$)이어야 함 (순서 역전 및 중복 시 `SELECTION_ORDER_INVALID`, rank gap은 허용).
   - Coordinate와 Provenance exact-match: `hit.coordinate`와 `hit.provenance` 간 `source_code`, `source_version`, `external_document_id`, `chunk_index`가 완전히 일치해야 함 (`COORDINATE_PROVENANCE_MISMATCH`).
   - `evidence_key` 중복 금지 (`DUPLICATE_EVIDENCE_KEY`).
   - `(source_code, source_version, external_document_id, chunk_index)` 안정 좌표 중복 금지 (`DUPLICATE_STABLE_COORDINATE`).
   - `knowledge_chunk_id` 중복 금지 (`DUPLICATE_KNOWLEDGE_CHUNK_ID`).
   - 동일 member 하위 복수 chunk 허용: 서로 다른 chunk 간 동일한 `source_snapshot_member_id` 공유는 허용된다 (`DUPLICATE_SOURCE_MEMBER` 제약 없음).
4. **Receipt & Selection Manifest 검증**:
   - `receipt.variant == "RET-H"` 및 `receipt.retrieval_execution_status == RetrievalExecutionStatus.SUCCEEDED` 필수.
   - `receipt.artifact_ref.artifact_code == "production_search_receipt"` 및 `receipt.artifact_ref.version == "2.0"` 필수 (`PRODUCTION_SEARCH_RECEIPT_VERSION`, legacy "1.0"은 `RETRIEVAL_RECEIPT_MISMATCH` fail-closed).
   - `retrieval-run-v1` 계약에 따라 RET-H의 `receipt.query_embedding_sha256`는 반드시 non-null 64자리 소문자 SHA-256 다이제스트여야 함 (`RETRIEVAL_RECEIPT_MISMATCH`).
   - `signal_manifest_sha256`, `hit_manifest_sha256`, `selection_manifest_sha256`는 64자리 소문자 SHA-256이어야 함 (`RETRIEVAL_RECEIPT_MISMATCH`).
   - `receipt.artifact_ref`, `filter_snapshot_ref`, `evidence_index_ref`, `retrieval_config_ref`, `adapter_artifact_ref`가 모두 유효한 `ImmutableArtifactRef`여야 함 (`RETRIEVAL_RECEIPT_MISMATCH`).
   - `ai_worker.tasks.rag.retrieval_runtime.compute_production_search_receipt`를 호출해 재계산한 `artifact_ref`가 `request.retrieval_receipt.artifact_ref`와 정확히 일치해야 함 (`RETRIEVAL_RECEIPT_MISMATCH`).
   - `compute_selection_manifest_hash([sel.hit for sel in selections]) == request.retrieval_receipt.selection_manifest_sha256` exact-match 필수 (`SELECTION_MANIFEST_MISMATCH`).
   - 각 selection의 `retrieval_receipt_ref`가 receipt의 `artifact_ref`와 일치해야 함 (`RETRIEVAL_RECEIPT_MISMATCH`).
   - `eligibility_receipt_ref`, `assessment_artifact_ref`, `verifier_artifact_ref` 불변 아티팩트 참조 규격 준수 필수 (`OBSERVED_PROVENANCE_REF_REQUIRED`).
5. **Source & Member Identity 및 Request Origin 검증**:
   - 다중 selection 간 동일 origin 검증: 모든 selection의 `request_guard_ref`, `request_operation_code`, `request_decision_stage`가 동일해야 함 (`REQUEST_ORIGIN_MISMATCH`).
   - `b.source_code == prov.source_code` (`SOURCE_MISMATCH`).
   - `b.source_version == prov.source_version` (`SOURCE_VERSION_MISMATCH`).
   - `b.source_snapshot_id == prov.source_snapshot_id` (`SOURCE_SNAPSHOT_MISMATCH`).
   - `b.source_snapshot_member_id == prov.source_snapshot_member_id` (`SOURCE_MEMBER_MISMATCH`).
   - **Endpoint Member 계약 정렬 (PD-315/PD-362)**:
     - `ENDPOINT_OPERATION`: `endpoint_code`는 필수(비어있지 않은 NFC 문자열), `operation_code`는 **nullable**(제공될 경우 비어있지 않은 NFC, `None` 허용). `artifact_code`, `artifact_version`은 `None`이어야 함 (`MEMBER_IDENTITY_INVALID`).
     - `ARTIFACT_MEMBER`: `artifact_code`, `artifact_version`은 필수(비어있지 않은 NFC 문자열). `endpoint_code`, `operation_code`는 `None`이어야 함 (`MEMBER_IDENTITY_INVALID`).
   - 관측 결과는 반드시 `PASS`여야 함 (`OBSERVED_DECISION_NOT_PASS`).
6. **Content Hash 무결성**:
   - `sha256(sel.content_text.reveal().encode("utf-8")) == prov.content_hash` 일치 필수.
   - `sel.content_sha256` 제공 시 `prov.content_hash`와 일치 필수 (`CONTENT_HASH_MISMATCH`).
7. **Freshness & Datetime Awareness**:
   - 모든 datetime은 timezone-aware UTC여야 함 (`DATETIME_NOT_AWARE`).
   - 유효 기간: `valid_from <= evaluated_at < valid_until` (`ASSESSMENT_NOT_YET_VALID`, `ASSESSMENT_EXPIRED`).
   - **Assessment Validity Authority**:
     - Assessment validity window의 정본 권위는 승인된 [`PD-722-20260917`](../../../governance/decisions/2026-09-17-evidence-assessment-validity.md) 결정을 따른다.
     - `assessment_valid_from = authoritative evaluated_at` (최초 평가 시점 동결, retry 연장 금지).
     - `assessment_valid_until = min(assessment_valid_from + evidence-assessment-validity v1 ceiling, applicable authoritative upper bounds)` (v1 operational safety ceiling = 24h).
     - 본 24h ceiling은 재검증 없는 handoff 재사용을 위한 운영 안전 상한일 뿐이며, Source freshness 보장이나 public release 승인을 의미하지 않는다.
8. **Two-Input 재검증 (`verify_guide_evidence_handoff`)**:
   - `verify_guide_evidence_handoff(request, handoff) -> GuideEvidenceHandoffVerificationOutcome`
   - 위조된 handoff가 자체 일관적인 해시를 재계산하여 통과하는 것을 방지하기 위해, 신뢰된 원본 입력 `request`를 기반으로 재생성된 handoff와 필드 및 다이제스트를 1:1 비교 검증 (`HANDOFF_MISMATCH`, `FORGED_HANDOFF_HASH`).

---

## 4. Canonical Hash Projection 사양

```json
{
  "evaluated_at": "2026-09-15T12:00:00.000000Z",
  "projection_version": "guide-evidence-handoff-v1",
  "retrieval_receipt_ref": {
    "artifact_code": "retrieval_receipt",
    "content_sha256": "...",
    "version": "v1"
  },
  "retrieval_selection_manifest_sha256": "...",
  "selections": [
    {
      "artifact_code": null,
      "artifact_version": null,
      "assessment_artifact_ref": { ... },
      "assessment_valid_from": "2026-09-15T00:00:00.000000Z",
      "assessment_valid_until": "2026-09-15T23:59:59.000000Z",
      "canonical_checksum": "...",
      "canonicalization_spec_version": "v1",
      "chunk_index": 0,
      "content_sha256": "...",
      "eligibility_receipt_ref": { ... },
      "endpoint_code": "guide_api",
      "evidence_key": "evidence:1",
      "external_document_id": "DOC-1",
      "final_rank": 1,
      "knowledge_chunk_id": "...",
      "locator": "...",
      "member_kind": "ENDPOINT_OPERATION",
      "normalization_version": "v1",
      "operation_code": "get_guide",
      "request_decision_stage": "REQUEST",
      "request_guard_ref": { ... },
      "request_member_decision_ref": { ... },
      "request_operation_code": "get_guide",
      "request_source_decision_ref": { ... },
      "retrieval_receipt_ref": { ... },
      "source_code": "...",
      "source_snapshot_id": "...",
      "source_snapshot_member_id": "...",
      "source_version": "...",
      "verifier_artifact_ref": { ... }
    }
  ]
}
```

> **주의**: 증거 텍스트 원문은 프로젝션 대상에서 완전히 배제되며 오직 `content_sha256`만 투영된다.
