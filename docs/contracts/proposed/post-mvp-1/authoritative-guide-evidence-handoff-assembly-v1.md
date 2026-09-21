# Authoritative Guide Evidence Handoff Assembly 계약 v1 (#760)

| 항목 | 값 |
| --- | --- |
| 문서 버전 | `authoritative-guide-evidence-handoff-assembly-v1` |
| 상태 | Proposed / Review pending · Issue #760 |
| 관련 선행 계약 | [`guide-retrieval-composition-v1`](./guide-retrieval-composition-v1.md), [`knowledge-chunk-content-hydration-v1`](./knowledge-chunk-content-hydration-v1.md), [`assessment-eligibility-authority-persistence-v1`](./assessment-eligibility-authority-persistence-v1.md), [`guide-evidence-handoff-v1`](./guide-evidence-handoff-v1.md), [`retrieval-run-v1`](./retrieval-run-v1.md) |
| 구현 위치 | `ai_worker/tasks/rag/authoritative_guide_evidence_handoff.py` |
| 테스트 위치 | `ai_worker/tests/rag/test_authoritative_guide_evidence_handoff_assembly.py`, `tests/integration/rag/test_authoritative_guide_evidence_handoff_assembly_postgresql.py` |
| 계약 및 구현 책임 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | `@hazelnutflavoured` — pure assembly seam, persisted run receipt 재검증, authority exact binding, evidence key 경계, 기존 Handoff kernel 재사용, DB/schema/migration 없음 |
| 전문 근거 제공 | persisted authority row 확인이 필요할 경우 `@phina-io` (specialist evidence provider, 추가 필수 PR reviewer 아님) |

---

## 1. 목적 및 권위 한계

본 문서는 이미 authoritative한 두 production 결과를 **pure in-memory exact binding**으로 결속해 기존 `GuideEvidenceSelectionRequest` / `GuideEvidenceHandoffRequest`를 구성하고, 기존 `build_guide_evidence_handoff()`에 그대로 넘기는 단일 seam을 규정한다.

```text
#709 REQUEST authority Reader
        ↓
#697 Authority × Production Retrieval exact join
        ↓
#715 KnowledgeChunk Content Hydration
        ↓
HydratedGuideRetrievalSelection[]

#712 Assessment·Eligibility Issuer / Persistence
        ↓
#746 Assessment·Eligibility Authority Reader
        ↓
PersistedEvidenceAuthority[]

        ↓ (#760 Assembly)

GuideEvidenceSelectionRequest[]
        ↓
GuideEvidenceHandoffRequest
        ↓
기존 build_guide_evidence_handoff()
        ↓
VerifiedGuideEvidenceHandoff | REJECTED
```

본 계층이 답하는 질문은 하나뿐이다.

```text
이 hydrated production selection들과 이 persisted authority들이
정확히 같은 retrieval execution·chunk·Source·content에 결속되어 있는가?
```

### 1.1 권위 한계 (Authority Boundary)

1. **Pure seam**: DB read/write, migration, schema, DB role/grant, network, clock, persistence I/O가 없다. 구현 모듈은 `backend.*`, `sqlalchemy.*`, `ai_worker.adapters.*`를 import하지 않으며 #709/#746 Reader를 직접 호출하지 않는다. caller가 이미 읽어온 authoritative 결과만 입력으로 받는다.
2. **신규 권위 없음**: eligibility, assessment, Source CURRENT/ACTIVE, snapshot 승인, approval revoke, verifier deployment, ranking을 재판정하지 않는다. assessment validity(`not yet valid` / `expired`) 판정은 기존 `guide-evidence-handoff-v1` kernel이 계속 소유한다.
3. **신규 hash domain 없음**: `PersistedRetrievalRunReceipt.receipt_hash`는 기존 `ai_worker.tasks.rag.retrieval_run.compute_receipt_hash()`로만 재검증한다. 새 receipt, handoff, evidence-key hash domain을 정의하지 않는다.
4. **상류 타입 재정의 없음**: `PersistedRetrievalRunReceipt`, `ProductionSearchReceipt`, `HydratedGuideRetrievalSelection`, `PersistedEvidenceAuthority`, `GuideEvidenceSelectionRequest`, `GuideEvidenceHandoffRequest`는 모두 기존 정본 타입을 그대로 쓴다.
5. **Silent transform 금지**: normalize, trim, case-fold, fallback, 최신 행 선택, sorting, rank 변경, content rewrite, authority ref 재계산, validity window 재산출을 하지 않는다.
6. **범위 종료 지점**: 결과는 `GuideEvidenceHandoffBuildOutcome`에서 멈춘다. Generator, Finalizer, Citation Authorization, Release Gate, LangGraph, Guide API, Frontend, `PUBLIC_TRACK_F`는 본 계약의 범위가 아니다.

---

## 2. `PersistedRetrievalRunReceipt`가 입력에 필요한 이유

`PersistedEvidenceAuthority`의 selection identity는 다음 두 값이다.

```text
retrieval_run_id
knowledge_chunk_id
```

그러나 `ProductionSearchReceipt`에는 `retrieval_run_id`가 없다. 따라서 authority를 **실제 retrieval execution**에 결속하려면 기존 `PersistedRetrievalRunReceipt.run_id`가 함께 필요하다. Assembly는 이 값을 조회·추론·보정하지 않고 caller가 전달한 persisted receipt에서만 읽는다.

---

## 3. Input

```python
@dataclass(frozen=True, slots=True)
class AuthoritativeGuideEvidenceHandoffAssemblyRequest:
    persisted_retrieval_receipt: PersistedRetrievalRunReceipt
    retrieval_receipt: ProductionSearchReceipt
    hydrated_selections: tuple[HydratedGuideRetrievalSelection, ...]
    authorities: tuple[PersistedEvidenceAuthority, ...]
    evaluated_at: datetime
```

`evaluated_at`은 caller가 명시적으로 전달하는 handoff evaluation timestamp다. Assembly 내부에서 `datetime.now()`, `datetime.now(UTC)`, DB clock, system clock을 쓰지 않으며, timezone-aware UTC가 아니면 `REQUEST_INVALID`로 fail closed한다.

빈 `hydrated_selections`, 빈 `authorities`, 정본 타입이 아닌 원소는 `REQUEST_INVALID`다.

---

## 4. 검증 규칙

검증은 phase-ordered fail-fast이며, assembly 단계 거부는 **단일 typed reason**과 `build_outcome = None`을 돌려준다.

### 4.1 Phase 1 — Request shape (`REQUEST_INVALID`)

입력 구조 정본 타입과 caller-supplied UTC `evaluated_at`.

### 4.2 Phase 2 — Persisted run receipt (`RETRIEVAL_RUN_RECEIPT_MISMATCH`)

먼저 기존 `compute_receipt_hash()`로 `receipt_hash` 자기무결성을 재검증하고, persisted fields를 그대로(조회·추론·보정 없이) 사용한다. 이어 다음을 exact 검증한다.

```text
persisted.variant                == "RET-H"
persisted.status                 == "COMPLETED"
persisted.variant                == retrieval_receipt.variant
persisted.search_receipt_hash    == retrieval_receipt.artifact_ref.content_sha256
persisted.signal_manifest_hash   == retrieval_receipt.signal_manifest_sha256
persisted.hit_manifest_hash      == retrieval_receipt.hit_manifest_sha256
persisted.selected_count         == len(hydrated_selections)
```

`search_receipt_hash`가 `None`이면 결속 불가이므로 fail closed한다.

### 4.3 Phase 3 — Authority 집합 (`AUTHORITY_SET_MISMATCH`)

`authorities`는 `knowledge_chunk_id`로만 index한다. authority tuple 순서는 matching에 사용하지 않는다.

```text
duplicate authority 없음
missing authority 없음
extra / unused authority 없음
```

즉 `set(authority.knowledge_chunk_id) == set(hydrated chunk id)`를 요구한다.

> **Reason semantics 고정**: `knowledge_chunk_id`가 authority index key이므로, authority의 chunk 불일치는 언제나 "한 hydrated chunk의 authority 누락 + 사용되지 않는 authority" 라는 **집합 사실**이다. 따라서 chunk 불일치는 `AUTHORITY_BINDING_MISMATCH`가 아니라 항상 `AUTHORITY_SET_MISMATCH`로 고정한다.

### 4.4 Phase 4 — Selection ↔ Authority exact binding (`AUTHORITY_BINDING_MISMATCH`)

각 hydrated selection의 `hit.provenance`와 matched authority 사이에서 다음 모두 exact equality를 요구한다.

| 축 | authority | hydrated selection |
| --- | --- | --- |
| Retrieval run | `retrieval_run_id` | `persisted_retrieval_receipt.run_id` |
| Source Snapshot | `source_snapshot_id` | `hit.provenance.source_snapshot_id` |
| Source Member | `source_snapshot_member_id` | `hit.provenance.source_snapshot_member_id` |
| Source code | `source_code` | `hit.provenance.source_code` |
| Source version | `source_version` | `hit.provenance.source_version` |
| Content | `content_sha256` | `hit.provenance.content_hash` |

chunk identity는 Phase 3의 index key로 이미 동일하므로 여기서 다시 비교하지 않는다.

**Cardinality**: authority는 chunk 단위, #709 binding은 Source Member 단위다. 따라서 같은 Source Member에 속한 서로 다른 chunk 여러 건은 정상 production 결과이며 거부하지 않는다.

### 4.5 Phase 5 — Evidence key 집합 (`EVIDENCE_KEY_SET_MISMATCH`)

본 Issue는 새 evidence-key 생성 정책을 정의하지 않는다. `evidence:{rank}`, `evidence:{chunk_id}`, uuid/hash 기반 생성 모두 금지한다. Assembly는 `HydratedGuideRetrievalSelection.hit.provenance.evidence_key`를 그대로 소비한다. 이 값은 production search와 #711 hydration이 동일한 `rag_knowledge_index_member` row에서 읽은 persisted binding이다.

```text
모든 hydrated chunk provenance에 key 정확히 1개
서로 다른 chunk 사이 duplicate evidence_key value 없음
비어 있거나 공백으로 둘러싸였거나 NFC 정규형이 아닌 key 없음
```

출력 순서는 `hydrated_selections` 순서를 따른다. 별도 caller mapping은 받지 않으므로 persisted binding과
다른 key를 #760 입력에서 덮어쓸 수 없다.

### 4.6 Phase 6 — 기존 Handoff Builder 위임 (`HANDOFF_REJECTED`)

Assembly는 selection마다 상류가 확정한 값을 그대로 옮긴다.

```python
GuideEvidenceSelectionRequest(
    hit=hydrated.selection.hit,
    binding=hydrated.selection.binding,
    evidence_key=hydrated.selection.hit.provenance.evidence_key,
    content_text=hydrated.content_text,
    retrieval_receipt_ref=request.retrieval_receipt.artifact_ref,
    eligibility_receipt_ref=authority.eligibility_receipt_ref,
    assessment_artifact_ref=authority.assessment_artifact_ref,
    verifier_artifact_ref=authority.verifier_artifact_ref,
    assessment_valid_from=authority.assessment_valid_from,
    assessment_valid_until=authority.assessment_valid_until,
    content_sha256=authority.content_sha256,
)
```

그 뒤 `GuideEvidenceHandoffRequest`를 만들어 기존 `build_guide_evidence_handoff()`를 그대로 호출한다. 기존 kernel이 이미 소유한 다음 로직은 복제하지 않는다.

```text
ProductionSearchReceipt 검증
selection manifest 검증
coordinate / provenance 검증
REQUEST binding 검증
content SHA 검증
selection order / duplicate 판정
assessment validity 판정
handoff hash 생성
```

#### Artifact ref transport

`rag_runtime.evidence_authority.ImmutableArtifactRef`는 `PD-175-20260910`이 고정한 `backend` → `ai_worker` import 경계 때문에 AI Worker의 `ImmutableArtifactRef`와 별개 타입이다. Assembly는 `request_authority_artifact.worker_artifact_ref()`와 동일한 **무손실 field-for-field 이동**만 수행하며 artifact identity를 재계산하지 않는다.

---

## 5. Output

```python
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
class AuthoritativeGuideEvidenceAssemblyOutcome:
    decision: AuthoritativeGuideEvidenceAssemblyDecision
    reasons: tuple[AuthoritativeGuideEvidenceAssemblyReason, ...]
    build_outcome: GuideEvidenceHandoffBuildOutcome | None
```

| 상황 | `decision` | `reasons` | `build_outcome` |
| --- | --- | --- | --- |
| Assembly 단계 실패 | `REJECTED` | 단일 assembly reason | `None` |
| 기존 Builder까지 도달했으나 거부 | `REJECTED` | `(HANDOFF_REJECTED,)` | 원본 `GuideEvidenceHandoffBuildOutcome(REJECTED)` |
| 성공 | `BUILT` | `()` | `GuideEvidenceHandoffBuildOutcome(BUILT)` (`handoff is not None`) |

기존 `GuideEvidenceHandoffReason`(`ASSESSMENT_NOT_YET_VALID`, `ASSESSMENT_EXPIRED`, `DUPLICATE_KNOWLEDGE_CHUNK_ID` 등)을 잃거나 assembly reason으로 다시 mapping하지 않는다. 원본 outcome을 그대로 실어 보낸다.

---

## 6. #174 경계

오래된 Handoff 문서의 `#174 authenticated assembler` 표현은 현재 authority pipeline과 어긋난다. 현재 #174는 Preflight API, transaction, snapshot persistence, Job/Outbox, Intake/Execution Context, Identification, 202 API를 포괄하는 상위 Issue다.

Guide evidence authority chain의 실제 담당은 다음과 같다.

```text
#709 REQUEST authority Reader        — Decision ownership / PASS 관측의 production 조회
#697 Authority × Retrieval exact join
#715 Content Hydration
#712 Assessment / Eligibility Issuer·Persistence
#746 Assessment / Eligibility Reader
#760 Authoritative Handoff Assembly  — 본 계약
```

본 계약은 이 경계를 문서에 정렬할 뿐, #174 Issue 자체의 의미와 범위를 변경하지 않는다.

---

## 7. 검증

| 구분 | 위치 |
| --- | --- |
| Pure unit | `ai_worker/tests/rag/test_authoritative_guide_evidence_handoff_assembly.py` |
| Focused production-chain integration | `tests/integration/rag/test_authoritative_guide_evidence_handoff_assembly_postgresql.py` |

Integration은 실제 PostgreSQL에서 `#712 issuer → RagEvidenceAuthorityRepository persist → commit → session close → #746 Reader → PersistedEvidenceAuthority`를 통과한 authority를 그대로 Assembly에 넣어 `BUILT`까지 확인한다. #715 Content Reader DB 경로는 [`knowledge-chunk-content-hydration-v1`](./knowledge-chunk-content-hydration-v1.md)이 이미 검증하므로 여기서 중복 구현하지 않는다.

---

## 8. 미구현 경계

`BUILT`는 다음 중 어느 것도 의미하지 않는다.

```text
Generator executed
Citation authorized
Release approved
PUBLIC_TRACK_F enabled
#180 runtime orchestration / persistence / E2E 완료
```
