# Product Decision Candidate: Guide Evidence Handoff Contract Kernel

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-180-20260915` |
| 상태 | Proposed / Review pending · Issue #180 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — PM·Product Acceptance·Privacy Gate |
| 필요 교차 리뷰 | 송은영 (`@phina-io`) — Backend·DB·REQUEST Guard / 김지혜 (`@Jye-rookie`) — Source provenance |
| 추적 Issue | [#180](https://github.com/AI-HealthCare-05/AH_05_04/issues/180) |
| 상위 결정 | [`PD-362-20260909`](./2026-09-09-source-snapshot-approval-boundary.md), [`PD-315-20260908`](./2026-09-08-production-evidence-retrieval-contract-divergence.md), [`PD-125-20260831`](./2026-08-31-rag-p0-contract-freeze.md) |

## 목적과 권위 경계

Issue #180 Runtime Orchestration 구현에 앞서, #174 REQUEST Guard의 Source/Member 결정 관측 결과(opaque caller observations)와 #178 Production Evidence Retrieval 결과 사이의 결정적 exact-match 결속을 검증하고, #179 Guideline Card kernel이 소비할 `VerifiedGuideEvidenceHandoff` 불변 구조를 구축하는 순수 계약 커널(pure contract kernel)을 수립한다.

### 권위 한계 (Authority Boundary)

1. **순수 검증 및 결속 한계 (Pure Kernel Boundary)**:
   - 본 커널은 권한(authority)을 자체 발급하지 않으며, 데이터베이스 존재 여부나 발급자의 진위(issuer authenticity)를 증명하거나 외부 인가를 수행하지 않는다.
   - `Verified`는 호출자가 전달한 불투명 관측 결과(opaque caller observations: `request_guard_ref`, `request_source_decision_ref`, `request_member_decision_ref`, `retrieval_receipt`, `eligibility_receipt_ref`, `assessment_artifact_ref`, `verifier_artifact_ref`)의 구조·해시·식별자 일관성(structure, hash, and identity consistency)만을 검증한다는 의미로 엄격히 제한된다.
   - **#174 Authenticated Assembler 의존성**: #174 authenticated assembler가 Decision ownership, 실제 `PASS` 판정, assessment artifact content의 진위를 검증하기 전에는, #180 runtime이 이 handoff를 독자적 authority로 직접 소비할 수 없다.
2. **#178 표준 JCS 정렬 및 차단 해소 (`PD-178-20260916`)**:
   - 2026-09-16 결정 `PD-178-20260916`을 통해 #178의 `compute_selection_manifest_hash` 및 `ProductionSearchReceipt` (v2.0)가 RFC 8785 JCS 규격으로 정렬되었다.
   - `BLOCKED_BY_178_CANONICAL_HASH_CONTRACT` 비강제 marker는 해소되어 코드와 계약에서 제거되었다.
   - Receipt v2.0만 허용하며 legacy v1은 fail-close(`RETRIEVAL_RECEIPT_MISMATCH`) 거부된다.
   - **해시 도메인 독립성**: 공용 JCS 직렬화 모듈(`ai_worker.tasks.evaluation.canonical`)을 공유하지만, `guide-evidence-handoff-v1`, `retrieval-selection-manifest-v2`, `production-search-receipt-v2` 세 프로젝션 및 해시 도메인은 상호 완전히 독립적이며 각 도메인의 preimage가 혼용되지 않는다.
3. **#180 Endpoint Member 계약 차단 기록 (`BLOCKED_BY_180_ENDPOINT_MEMBER_CONTRACT`)**:
   - PD-315/PD-362는 Endpoint Member의 `operation_code`를 nullable로 허용하지만 현재 Citation validator와 Citation Authorization은 non-null 값을 요구한다.
   - `BLOCKED_BY_180_ENDPOINT_MEMBER_CONTRACT`는 이 차이를 기록하는 **비강제 marker**이며, 공유 계약과 downstream validator가 정렬되기 전에는 nullable Endpoint Member handoff를 #180 runtime에 연결할 수 없다.
   - 실제 차단은 후속 orchestration typed precondition과 integration test로 구현해야 한다.
   - **해소 경위**: `BLOCKED_BY_180_ENDPOINT_MEMBER_CONTRACT`는 `PD-180-EM-20260916`과 `source-member-identity-v1` 공유 커널로 **해소되었다**. 비강제 marker는 제거하고 typed 검증으로 대체했다. 단, PD-315 승인 전까지 #180 runtime 연결은 여전히 차단 상태다.
4. **비식별 및 민감 텍스트 보호 경계 (SensitiveText Boundary)**:
   - 검색된 증거 원문(Content A)은 인메모리 `SensitiveText`로만 전달되며, 영구 저장소·로그·`repr`·해시 preimage에 절대 원문 그대로 노출되어서는 안 된다 (`<redacted>` 보호).
   - 해시 프로젝션에는 `content_sha256` 다이제스트만 포함한다.
5. **용어 불변성**:
   - 승인 전 단계의 handoff 결과 객체에는 `Authorized`라는 명칭을 사용할 수 없으며, 반드시 `Verified` (`VerifiedGuideEvidenceHandoff`, `VerifiedGuideEvidenceSelection`) 명칭만을 사용한다.
   - 과도한 검증 사유 이름은 provenance/binding 의미로 고정한다 (예: `OBSERVED_PROVENANCE_REF_REQUIRED`).

## 핵심 결정 사항

### 1. RFC 8785 JCS 정규 직렬화
- `guide-evidence-handoff-v1`의 표준 정규 바이트 직렬화는 RFC 8785 JSON Canonicalization Scheme (JCS) 표준을 엄격히 준수한다.
- Handoff JCS 및 Selection Manifest JCS는 `PD-178-20260916`을 통해 동일한 공용 RFC 8785 직렬화 모듈(`ai_worker.tasks.evaluation.canonical`)로 정렬 완료되었으며, `BLOCKED_BY_178_CANONICAL_HASH_CONTRACT` 비강제 marker는 해소되었다.
- 공용 직렬화기를 공유하지만 세 projection 및 해시 도메인(`guide-evidence-handoff-v1`, `retrieval-selection-manifest-v2`, `production-search-receipt-v2`)은 상호 완전히 독립적이다.

### 2. RequestSourceMemberBinding 구조 및 exact-match
#174 REQUEST Guard와의 결속을 위해 아래 필드를 정규화한다:
- `request_guard_ref`: 불변 아티팩트 참조
- `request_operation_code`: REQUEST 오퍼레이션 코드 (비어있지 않은 NFC 정규화 문자열)
- `request_decision_stage`: `RequestDecisionStage.REQUEST` 고정
- 다중 selection 간 동일 origin 검증: 모든 selection의 `request_guard_ref`, `request_operation_code`, `request_decision_stage`는 완전히 일치해야 함 (`REQUEST_ORIGIN_MISMATCH`).
- `source_snapshot_id`, `source_code`, `source_version`: 소스 식별자 (Provenance와 완전 일치 필수)
- `source_snapshot_member_id`, `member_kind`: 멤버 식별자 (Provenance와 완전 일치 필수)
- **Endpoint Member 계약 정렬 (PD-315/PD-362)**:
  - `ENDPOINT_OPERATION`의 경우: `endpoint_code`는 필수(비어있지 않은 NFC), `operation_code`는 **nullable**(제공될 경우 비어있지 않은 NFC, `None` 허용). `artifact_code`, `artifact_version`은 `None`이어야 함.
  - `ARTIFACT_MEMBER`의 경우: `artifact_code`, `artifact_version`은 필수(비어있지 않은 NFC), `endpoint_code`, `operation_code`는 `None`이어야 함.
- `request_source_decision_ref`, `request_member_decision_ref`: 결정 아티팩트 참조
- `observed_source_decision_outcome`, `observed_member_decision_outcome`: 관측 결과 (기본값 없이 명시적 `PASS` 요구)

### 3. Production Provenance 보존 및 엄격한 타입 검증
`VerifiedGuideEvidenceSelection` 및 handoff JCS 해시 프로젝션에 다음 #178 provenance 필드를 온전히 보존한다:
- `canonical_checksum: str` (64자리 소문자 SHA-256)
- `external_document_id: str` (비어있지 않은 NFC)
- `chunk_index: int` (0 이상의 정수)
- `canonicalization_spec_version: str` (비어있지 않은 NFC)
- `normalization_version: str` (비어있지 않은 NFC)
또한 모든 UUID 필드(`knowledge_chunk_id`, `source_snapshot_id`, `source_snapshot_member_id`)는 정확한 `uuid.UUID` 타입이어야 하며, `source_code`, `source_version`, `locator`는 비어있지 않은 NFC 문자열이어야 한다.

### 4. Retrieval Receipt 및 Search Hit 불변식 검증
- **Retrieval Receipt 검증**:
  - `receipt.variant == "RET-H"` 및 `receipt.retrieval_execution_status == RetrievalExecutionStatus.SUCCEEDED` 필수.
  - `retrieval-run-v1` 계약에 따라 RET-H의 `receipt.query_embedding_sha256`는 반드시 non-null 64자리 소문자 SHA-256 다이제스트여야 함.
  - `signal_manifest_sha256`, `hit_manifest_sha256`, `selection_manifest_sha256`는 64자리 소문자 SHA-256이어야 함.
  - receipt의 `artifact_ref`, `filter_snapshot_ref`, `evidence_index_ref`, `retrieval_config_ref`, `adapter_artifact_ref`가 모두 유효한 `ImmutableArtifactRef`여야 함.
  - `ai_worker.tasks.rag.retrieval_runtime.compute_production_search_receipt`를 호출해 재계산한 `artifact_ref`가 receipt의 `artifact_ref`와 정확히 일치해야 함 (`RETRIEVAL_RECEIPT_MISMATCH`).
  - 재계산된 selection manifest hash가 receipt의 `selection_manifest_sha256`와 일치해야 함 (`SELECTION_MANIFEST_MISMATCH`).
- **Search Hit 불변식 검증**:
  - Coordinate와 Provenance의 exact-match: `source_code`, `source_version`, `external_document_id`, `chunk_index`가 완전히 일치해야 함 (`COORDINATE_PROVENANCE_MISMATCH`).
  - Rank 불변식: $1 \le \text{fusion\_rank} \le 5$, 엄격한 단조 증가($\text{rank}[i] > \text{rank}[i-1]$), rank gap 허용 (`SELECTION_ORDER_INVALID`).
  - 동일 member 하위 복수 chunk 허용: 서로 다른 chunk 간 동일한 `source_snapshot_member_id` 공유를 허용하며, `knowledge_chunk_id` 및 안정 좌표 중복만을 금지한다.

### 5. Fail-Closed Validation 및 Narrow Exception Handling
- Broad `except Exception:`은 사용하지 않는다. 경계 파싱 시 예상 가능한 malformed 입력(`AttributeError`, `TypeError`, `ValueError`, `UnicodeError`)만을 좁게 포착하여 타입화된 `REJECTED` (`REQUEST_INVALID`)로 변환하며, 예상치 못한 내부 구현 결함(예: `RuntimeError`)은 숨기지 않고 전파한다.
- `SensitiveText.reveal()`은 반드시 `str` 인스턴스를 반환해야 하며, 비-문자열 또는 실패 시 `REQUEST_INVALID`로 fail-closed 거부한다.
- 모든 SHA-256 다이제스트는 64자리 소문자 16진수(`[0-9a-f]{64}`)만 허용하며, `.lower()` 등의 관용적 변환을 허용하지 않는다.

### 6. Freshness 및 Datetime 규격
- 모든 datetime(`evaluated_at`, `assessment_valid_from`, `assessment_valid_until`)은 timezone-aware UTC여야 한다. Naive datetime이나 비-UTC datetime은 즉시 거부된다 (`DATETIME_NOT_AWARE`).
- 신선도 검증 조건: `valid_from <= evaluated_at < valid_until` (상한 경계 exclusive, `ASSESSMENT_NOT_YET_VALID` / `ASSESSMENT_EXPIRED`).

### 7. Two-Input 검증 API (`verify_guide_evidence_handoff`)
- `verify_guide_evidence_handoff(request, handoff) -> GuideEvidenceHandoffVerificationOutcome` 구조로 설계한다.
- 단순히 handoff 내부의 해시를 재계산하는 자기 일관성(self-consistency)만으로는 위조된 handoff를 막을 수 없으므로, 권위 있는 원본 입력 `request`를 함께 인자로 받아 재검증한다.
- 검증 결과는 불리언(`bool`)이 아닌 타입화된 결정(`GuideEvidenceHandoffVerificationDecision.VERIFIED` 또는 `REJECTED`) 및 상세 이유(`reasons: tuple[GuideEvidenceHandoffReason, ...]`)로 반환한다.
