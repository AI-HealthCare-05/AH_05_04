# RAG-15 Production Guideline Evidence Input 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 상태 | Proposed · 구현·로컬 검증 완료 · 담당 리뷰 대기 · Current 아님 |
| 구현 담당 | 정현우 — AI/RAG Guideline Generator·Card |
| 책임 리뷰 | 권가빈 — Safety·제품 수용 |
| Source·provenance 근거 | 김지혜 — production Source 좌표 의미 확인 근거 (required reviewer 아님) |
| 상위 승인 근거 | [Guideline Card typed port 계약 v1](../../targets/post-mvp-1/guideline-card-v1.md), [Authoritative Guide Evidence Handoff Assembly 계약 v1](./authoritative-guide-evidence-handoff-assembly-v1.md), [Guide Evidence Handoff Contract Kernel v1](./guide-evidence-handoff-v1.md) |
| 추적 Issue | [#774](https://github.com/AI-HealthCare-05/AH_05_04/issues/774) |

## 목적과 상태

이 문서는 RAG-15 Guideline Generator/Card가 소비하는 **production evidence 입력
정본**과 그 **canonical selection projection·hash domain**을 확정한다.

이전까지 RAG-15 입력은 RAG-14 synthetic Evidence Gate 계약
(`ai_worker.tasks.rag.evidence_gate`)에 직접 결속돼 있었다. #729 → #760 → #765로
production authority chain이 `develop`에 병합된 뒤, 이 계약은 RAG-15가
`VerifiedGuideEvidenceHandoff`를 상류 정본으로 삼아 필요한 최소 필드만 소비하도록
입력 경계를 이동시킨다.

이 문서와 구현의 병합은 Current Runtime, Generator orchestration wiring, Card
영속화, Citation 승인, Release 승인, `PUBLIC_TRACK_F` 활성화를 의미하지 않는다.

## Legacy 경계

다음은 **production 입력이 아니다**. production 승격하지 않으며, production import
graph에서 제거됐다.

```text
evidence_gate.EvidenceGateOutcome
evidence_gate.GatePassedKnowledgeEvidenceSelection
evidence_gate.canonical_gate_selection_hash()
```

특히 다음 converter는 **구현하지 않으며 앞으로도 만들지 않는다**.

```text
VerifiedGuideEvidenceHandoff → EvidenceGateOutcome
```

legacy와 production은 별도 receipt·hash domain으로 유지한다. legacy
`canonical_gate_selection_hash()` 값은 production binding으로 인정되지 않는다.

`evidence_gate` 모듈 자체는 RAG-14 synthetic 경로와 그 회귀 테스트를 위해 남아
있으나, 아래 네 production 파일의 실행 코드에서 참조되지 않는다.

```text
ai_worker/tasks/rag/guideline_generator.py
ai_worker/tasks/rag/guideline_generator_prompt.py
ai_worker/tasks/rag/guideline_card.py
ai_worker/adapters/openai_guideline_generator.py
```

이 분리는 `ai_worker/tests/rag/test_guideline_production_evidence.py`의 AST 기반
source-level guard가 파일별로 강제한다. guard는 docstring이 아니라 실행 코드가
바인딩·참조하는 식별자만 검사한다.

## 상류 정본

```text
#729 Guide Runtime Preflight
        ↓
#760 Authoritative Guide Evidence Handoff
        ↓
#765 Guide Orchestration Slice 1 → READY_FOR_GENERATION
        ↓
ReadyGuideGenerationInputs
├─ ReadyGuideRuntimeContext
└─ VerifiedGuideEvidenceHandoff
        ↓
#774 project_guideline_evidence_from_handoff()
        ↓
ProductionGuidelineEvidenceSet
        ↓
GuidelineGenerationRequest / GuidelineCardRequest
```

`VerifiedGuideEvidenceHandoff`가 검증한 Source·Member·Assessment·Content
authority와 assessment 유효기간 freshness는 RAG-15가 **재판정하지 않는다**.

## 입력 계약

### `ProductionGuidelineEvidence`

`ai_worker.tasks.rag.guideline_production_evidence.ProductionGuidelineEvidence`.
`VerifiedGuideEvidenceSelection`의 31개 필드 중 RAG-15가 실제로 소비하는 12개만
가진다.

| 필드 | 타입 | 소비 경로 |
| --- | --- | --- |
| `evidence_key` | `str` | Provider slot 복원 키, `GuidelineCitation.evidence_key`, binding 결속 키, canonical projection |
| `source_snapshot_id` | `UUID` | Citation production source identity, canonical projection |
| `source_snapshot_member_id` | `UUID` | Citation production source identity, canonical projection |
| `source_code` | `str` | Citation production source identity, canonical projection |
| `source_version` | `str` | Citation 복원, draft citation exact-match, canonical 순서, canonical projection |
| `locator` | `str` | Citation 복원, draft citation exact-match, canonical 순서, canonical projection |
| `content_sha256` | `str` | 본문 무결성 결속, draft citation exact-match, canonical projection |
| `content_text` | `SensitiveText` | Provider projection에 노출하는 유일한 본문. canonical projection 제외 |
| `retrieval_receipt_ref` | `ImmutableArtifactRef` | `GuidelineCitation.retrieval_receipt_ref`, canonical projection |
| `eligibility_receipt_ref` | `ImmutableArtifactRef` | `GuidelineCitation.eligibility_receipt_ref`, canonical projection |
| `assessment_artifact_ref` | `ImmutableArtifactRef` | `GuidelineCitation.assessment_artifact_ref`, binding exact-match, canonical projection |
| `verifier_artifact_ref` | `ImmutableArtifactRef` | `GuidelineCitation.verifier_artifact_ref`, canonical projection |

의도적으로 **제외한** upstream audit-only 필드와 근거:

| 제외 필드 | 근거 |
| --- | --- |
| `knowledge_chunk_id` | 내부 chunk 식별자. Provider 전달 금지 대상이며 Citation·binding·projection에 필요 없다. content 좌표는 `source_snapshot_member_id + locator + content_sha256`이 고정한다. |
| `member_kind`, `endpoint_code`, `operation_code`, `artifact_code`, `artifact_version` | #180 endpoint-member authority 감사 값. RAG-15가 해석하지 않는다. |
| `request_guard_ref`, `request_operation_code`, `request_decision_stage`, `request_source_decision_ref`, `request_member_decision_ref` | #174 REQUEST Guard 감사 provenance. RAG-15가 재판정하지 않는다. |
| `assessment_valid_from`, `assessment_valid_until` | #760이 `evaluated_at` 기준 half-open 구간으로 이미 fail-closed 판정했다. RAG-15가 freshness를 재평가하면 authority 재판정이 된다. |
| `final_rank` | 상류 retrieval 순위 감사 값. RAG-15 canonical 순서는 `(source_version, locator, evidence_key)`이므로 retrieval 순위 변동과 무관하게 안정적이다. |
| `canonical_checksum`, `external_document_id`, `chunk_index`, `canonicalization_spec_version`, `normalization_version` | #178 retrieval·normalization 감사 값. |

제외한 필드는 모두 #760 `handoff_sha256`에 이미 결속되어 있으므로 상류 감사
가능성은 손실되지 않는다.

### `ProductionGuidelineEvidenceSet`

| 필드 | 타입 | 계약 |
| --- | --- | --- |
| `evaluated_at` | `datetime` | handoff 평가 시각을 그대로 옮긴다. Card는 자기 `evaluated_at`과의 exact match만 확인하고 유효기간 판정은 하지 않는다. |
| `handoff_sha256` | `str` | 상류 authority anchor. RAG-15는 재계산·재검증하지 않는다. |
| `selections` | `tuple[ProductionGuidelineEvidence, ...]` | 1개 이상. RAG-15 canonical 순서로 정렬된다. |

**status 필드가 없다.** legacy Gate의 `SUCCEEDED` / `SUFFICIENT` /
`EVIDENCE_INSUFFICIENT` / `EVIDENCE_CONFLICTED` / `EVIDENCE_STALE`를 복제하지
않는다. production path에서는 #760 handoff가 RAG-15보다 먼저 fail closed되므로,
RAG-15에 도달한 evidence set은 이미 검증된 non-empty selection만 담는다.

## Handoff → RAG-15 projection

`project_guideline_evidence_from_handoff(handoff) -> ProductionGuidelineEvidenceSet`

순수 deterministic 함수다. 다음이 **없다**.

```text
DB I/O
network
clock
normalization
fallback
authority 재판정
```

실패 경로가 없으므로 outcome/reason enum도 없다. `SensitiveText`와
`ImmutableArtifactRef`는 상류 객체를 aliasing하지 않고 복사한다. selection은
`(source_version, locator, evidence_key)`로 정렬되며, 상류가 보장한 `evidence_key`
유일성 덕에 순서는 결정적이다.

## Generator 입력 정렬

```python
GuidelineGenerationRequest(
    medication_identities=...,
    evidence=production_evidence_set,
    policy=...,
)
```

legacy `evidence_gate_outcome` field는 **제거됐다**. 다음과 같은 legacy/production
dual-mode를 production request에 두지 않는다.

```python
evidence_gate_outcome: EvidenceGateOutcome | None   # 금지
production_evidence: ProductionGuidelineEvidenceSet | None   # 금지
```

`OpenAIGuidelineGeneratorAdapter._is_valid_evidence_precondition()`은 Provider 호출
이전에 fail closed한다. 확인 대상은 production evidence 타입 일치, selection
non-empty, medication non-empty, `policy.maximum_claims >= 1`이다. legacy Gate
status 축은 더 이상 검사 대상이 아니다.

## Provider projection 경계

Provider는 계속 untrusted selector이며, privacy 경계는 변경 없다. Provider 입력에
포함되는 값은 다음뿐이다.

```text
medication_slot, code_system, canonical_code
evidence_slot, content_text
```

Provider에 전달하지 않는 값:

```text
prescription_version_medication_id (환자 결속 식별자)
evidence_key
source_snapshot_id, source_snapshot_member_id, source_code
source_version, locator
content_sha256, handoff_sha256
retrieval_receipt_ref, eligibility_receipt_ref
assessment_artifact_ref, verifier_artifact_ref
```

## Citation 복원

`GuidelineCitationDraft`와 `GuidelineCitation`의 source identity는 legacy
`source_snapshot_ref: ImmutableArtifactRef`에서 production Source 좌표로 교체됐다.

```text
source_snapshot_ref: ImmutableArtifactRef   (제거)
        ↓
source_snapshot_id: UUID
source_snapshot_member_id: UUID
source_code: str
```

production evidence는 Source Snapshot artifact 참조를 담지 않으며, RAG-15는 그것을
위조하지 않는다. `canonical_checksum`에서 artifact 참조를 합성하는 방식은 채택하지
않았다.

이 변경으로 `GuidelineCard` canonical payload의 citation 항목이 바뀌므로 Card
self-hash(`guideline-card@1`) 값도 바뀐다. Card는 아직 영속화되지 않고 공개되지
않으므로 마이그레이션 대상 데이터는 없다.

Citation은 production evidence에서만 복원된다. legacy
`KnowledgeEvidenceCandidate` / Gate provenance는 재구성하지 않는다. 알 수 없는
`evidence_slot`은 fail closed된다.

## Production selection canonical projection

| 항목 | 값 |
| --- | --- |
| projection version | `guideline-production-evidence-selection-v1` |
| 정규화 | RFC 8785 JCS (`ai_worker.tasks.evaluation.canonical`, `guide_evidence_handoff.canonical_jcs_sha256` 경유) |
| 함수 | `canonical_production_guideline_evidence_selection_projection()` |

포함 기준은 "future Dynamic Guideline Evidence Binding Authority가 exact-bind해야
하는 immutable fact"다.

| 포함 필드 | 근거 |
| --- | --- |
| `projection_version` | domain 분리. legacy preimage와 절대 충돌하지 않게 하는 explicit version. |
| `evidence_key` | binding이 결속하는 evidence 신원. |
| `source_snapshot_id`, `source_snapshot_member_id`, `source_code`, `source_version` | 승인된 Source 좌표. 다른 snapshot·member·version의 동일 본문을 같은 binding으로 재사용할 수 없게 한다. |
| `locator` | snapshot 내 인용 위치. 같은 문서의 다른 위치를 재사용할 수 없게 한다. |
| `content_sha256` | 본문 무결성. raw `content_text`를 제외하므로 본문 결속은 전적으로 이 hash가 담당한다. |
| `assessment_artifact_ref` | 이 evidence를 승인한 assessment. |
| `verifier_artifact_ref` | 그 assessment를 검증한 verifier. assessment만 고정하면 동일 assessment가 다른 verifier로 검증된 경우를 구분할 수 없다. |
| `eligibility_receipt_ref` | 이 evidence가 통과한 eligibility 판정. |
| `retrieval_receipt_ref` | 이 evidence를 선택한 retrieval 실행. |

**제외**: `content_text` (raw 본문 미포함). 따라서 `content_sha256`을 그대로 두고
본문만 바꾸면 projection hash는 변하지 않는다. 그 불일치는 두 지점이 각각 fail
closed로 거부한다.

1. #760 `build_guide_evidence_handoff()` — `CONTENT_HASH_MISMATCH`
2. Card `_is_valid_production_evidence_selection()` — `content_text` 재해시 확인

**제외**: `assessment_valid_from` / `assessment_valid_until` — 유효기간은 상류
authority이고 #760 `handoff_sha256`이 이미 결속한다. `final_rank` — 상류 순위 감사
값이며 RAG-15 순서 의미와 무관하다.

## Production selection hash

```python
compute_production_guideline_evidence_selection_hash(selection) -> str
```

위 canonical projection의 RFC 8785 JCS SHA-256이다.

금지 사항:

```text
canonical_gate_selection_hash() 재사용
legacy hash wrapping
legacy preimage compatibility layer
legacy projection version 재활용
```

## `ApprovedGuidelineEvidenceBinding.selection_projection_sha256`

이 field의 **production 의미**는 다음으로 확정된다.

```text
Production Guideline Evidence Selection canonical projection SHA-256
= compute_production_guideline_evidence_selection_hash(selection)
```

Card finalizer의 `_bind_citation()`은 draft citation의 `evidence_key`에 대응하는
production evidence로 이 hash를 재계산하고 binding 값과 exact match를 요구한다.
legacy `canonical_gate_selection_hash()` 값을 담은 binding은 `VALIDATION_FAILED`로
거부된다.

이번 계약이 소유하는 범위는 hash semantics, validation semantics, 문서화까지다.
다음은 **구현하지 않는다**.

```text
ApprovedGuidelineEvidenceBinding production issuer
ApprovedGuidelineEvidenceBinding persistence
GuidelineApprovalVerifier production adapter
Dynamic Guideline Evidence Binding Authority
```

후속 Dynamic Binding Authority(B)는 이 계약의 projection·hash·field 의미를 소비한다.

## Card 구조 검증 경계

`_is_bindable_production_evidence()`는 **구조 검증만** 한다. authority 재판정이
아니다.

확인하는 것:

- `ProductionGuidelineEvidenceSet` 타입 일치
- `evaluated_at`이 timezone-aware UTC이고 Card `evaluated_at`과 exact match
- `handoff_sha256`이 SHA-256 형식
- selection 1개 이상, 각 selection 구조 유효
- `evidence_key` 중복 없음, `(source_snapshot_member_id, source_version, locator)` 좌표 중복 없음
- `content_text` 재해시 == `content_sha256`

확인하지 않는 것:

- assessment 유효기간 (#760 소유)
- `handoff_sha256` 재계산 (#760 소유)
- Source·Member·Decision authority (#174/#672/#709 소유)

## 유지되는 semantics

다음은 변경하지 않았다.

```text
GuidelineCardStatus
GuidelineCardReason
GuidelineFallbackCode
GuidelineGenerationFailure
금지 의료행동 정책 (_FORBIDDEN_ACTION_RES)
승인 fallback copy
한국어 scope semantics
approval verifier port 및 fail-closed 규칙
request/fallback 분리 snapshot 규칙
Provider dual timeout·max_retries=0·observability 규격
```

새 public/runtime status를 만들지 않았다.

## UNRESOLVED — requires owner decision

legacy Gate가 제공했던 세 evidence 상태는 production request에서 **표현 불가능**해
졌다.

```text
EVIDENCE_INSUFFICIENT → NO_APPROVED_EVIDENCE
EVIDENCE_CONFLICTED   → CONFLICTING_EVIDENCE
EVIDENCE_STALE        → NO_APPROVED_EVIDENCE
```

`GuidelineCardReason`·`GuidelineFallbackCode` enum 멤버와 각 코드의 승인 fallback
copy 요구는 그대로 유지된다. 그러나 production `GuidelineCardRequest`로는 이 세
outcome에 도달할 수 없다. production path에서 근거가 부족·상충·만료된 경우는
#760 handoff build가 REJECTED로 닫고 #765 orchestration이 STOPPED로 멈추기
때문이다.

따라서 **upstream handoff rejection을 공개 fallback code로 사영하는 책임자와
매핑이 미정이다.** 이는 #765 `guide_orchestration`이 명시적으로 보류한 "No Public
Runtime Status" 경계에 속하며, #774에서 임의로 구현하지 않았다. #180 runtime
mapping 작업에서 Safety 책임 리뷰와 함께 결정해야 한다.

## 구현 참조

```text
ai_worker/tasks/rag/guideline_production_evidence.py   (신규)
ai_worker/tasks/rag/guideline_generator.py
ai_worker/tasks/rag/guideline_generator_prompt.py
ai_worker/tasks/rag/guideline_card.py
ai_worker/adapters/openai_guideline_generator.py

ai_worker/tests/rag/test_guideline_production_evidence.py   (신규)
ai_worker/tests/rag/test_guideline_card.py
ai_worker/tests/rag/test_guideline_generator.py
ai_worker/tests/rag/test_guideline_generator_prompt.py
ai_worker/tests/rag/test_openai_guideline_generator.py
ai_worker/tests/rag/test_guide_orchestration.py
ai_worker/tests/evaluation/test_guideline_card.py
```

## 명시적 미구현

```text
Generator orchestration wiring (guide_orchestration.py는 확장하지 않음)
finalize_guideline_card() runtime call site
Dynamic Guideline Evidence Binding Authority
ApprovedGuidelineEvidenceBinding issuer·persistence
GuidelineApprovalVerifier production adapter
ClaimCitationCandidateSet · ClaimSupportVerificationReceipt
CitationAuthorizationRequest/Receipt runtime wiring · Citation Finalizer
Release Gate · LangGraph · Worker · Guide API · Persistence
PUBLIC_TRACK_F
```
