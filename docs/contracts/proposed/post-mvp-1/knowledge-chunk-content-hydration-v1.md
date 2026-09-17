# KnowledgeChunk Content Hydration 계약 v1

| 항목 | 값 |
| --- | --- |
| 문서 버전 | `knowledge-chunk-content-hydration-v1` |
| 상태 | Proposed / Review pending · Issue #711 |
| 관련 선행 계약 | [`guide-retrieval-composition-v1`](./guide-retrieval-composition-v1.md), [`knowledge-evidence-index-v1`](./knowledge-evidence-index-v1.md), [`knowledge-evidence-search-rrf-v1`](./knowledge-evidence-search-rrf-v1.md) |
| 구현 위치 | `ai_worker/tasks/rag/knowledge_chunk_content_hydration.py`, `ai_worker/adapters/sqlalchemy_knowledge_chunk_content.py` |
| 테스트 위치 | `ai_worker/tests/rag/test_knowledge_chunk_content_hydration.py`, `ai_worker/tests/rag/test_sqlalchemy_knowledge_chunk_content.py`, `tests/integration/rag/test_knowledge_chunk_content_hydration_postgresql.py` |
| 계약 및 구현 책임 | 정현우 (`@ceohwj`) — AI/RAG |
| 단일 책임 리뷰 | 미지정 — Backend/DB read-only 결속과 provenance 검증을 확인할 수 있는 담당자 1명을 Issue #711과 PR에 명시한다 |

---

## 1. 목적 및 권위 한계

본 문서는 #697/#703이 확정한 `AuthenticatedGuideRetrievalSelection`이 가리키는 persisted `knowledge_chunk`의 실제 본문을 read-only로 다시 읽고, 검색 당시 `ProductionEvidenceProvenance`와 exact-match한 뒤 `SensitiveText` content를 부착하는 경계를 규정한다.

본 계층이 답하는 질문은 하나뿐이다.

```text
이 #703 selection이 가리킨 persisted chunk의 본문이
검색 당시 provenance와 여전히 정확히 같은가?
```

### 1.1 권위 한계 (Authority Boundary)

1. **Read-only**: DB write, migration, 신규 table/column, trigger, RLS, stored procedure, advisory lock, `SELECT ... FOR UPDATE`를 일절 사용하지 않는다.
2. **신규 권위 없음**: Source ACTIVE 여부, endpoint/operation runtime status, snapshot CURRENT 여부, eligibility, freshness, assessment, approval, ranking을 재판정하지 않는다. 해당 의미는 #178/#180의 다른 gate가 소유한다.
3. **정본 타입 재사용**: expected/observed provenance는 모두 기존 `ai_worker.tasks.rag.evidence_search.ProductionEvidenceProvenance`다. hydration 전용 provenance DTO를 만들지 않는다.
4. **Sensitive text 정본**: content는 downstream `GuideEvidenceSelectionRequest`가 이미 사용하는 `ai_worker.tasks.rag.evidence_retrieval.SensitiveText`로 전달한다. Knowledge Index build domain의 `SensitiveEvidenceText`를 downstream 출력 타입으로 전파하지 않는다.
5. **Silent transform 금지**: normalize, trim, lower-case, fallback, 최신 행 선택을 하지 않는다.
6. **범위 종료 지점**: 결과는 `HydratedGuideRetrievalSelection[]`에서 멈춘다.

---

## 2. Input

```text
tuple[AuthenticatedGuideRetrievalSelection, ...]   # #697/#703 AUTHENTICATED 산출물
KnowledgeChunkContentReaderPort                    # read-only 의존성
```

빈 tuple, tuple이 아닌 입력, `AuthenticatedGuideRetrievalSelection`이 아닌 원소는 `REQUEST_INVALID`다. production Evidence Gate success는 항상 1건 이상의 selected hit를 가지므로 빈 입력은 #703 AUTHENTICATED 산출물이 아니다.

---

## 3. Lookup Identity

조회는 반드시 다음 두 값을 **함께** 사용한다.

```text
knowledge_index_id
knowledge_chunk_id
```

`knowledge_chunk_id` 단독 조회를 하지 않는다. 같은 chunk가 여러 Index Version에 결속될 수 있으므로, 검색 당시의 실제 index membership과 동일한 row를 읽기 위해 두 값을 함께 쓴다.

---

## 4. Authoritative persisted source

기존 저장 구조를 그대로 사용하며 변경하지 않는다.

```text
rag_knowledge_index
        ↓
rag_knowledge_index_member
        ↓
knowledge_chunk
        ↓
knowledge_document
        ↓
rag_source_snapshot_member
```

Observed `ProductionEvidenceProvenance`의 각 필드 출처는 #178 production search와 동일하다.

| 필드 | 출처 |
| --- | --- |
| `knowledge_index_id`, `knowledge_chunk_id`, `source_snapshot_id`, `source_snapshot_member_id`, `source_code`, `source_version`, `canonical_checksum`, `external_document_id`, `chunk_index`, `content_hash` | `rag_knowledge_index_member` |
| `index_code`, `index_version`, `index_configuration_hash` | `rag_knowledge_index` |
| `locator` | `rag_source_snapshot_member` |
| `canonicalization_spec_version` | `knowledge_document` |
| `normalization_version` | `knowledge_chunk` |

`content_hash`는 #178과 동일하게 index member에 기록된 값이다. chunk와 index member의 hash가 어긋난 경우를 SQL 조건으로 걸러내지 않는다. 걸러내면 drift가 "행 없음"으로 감춰지므로, 아래 §6 content 검증이 이를 content 판정으로 노출한다.

---

## 5. Exact provenance equality

```text
observation.provenance == selection.hit.provenance
```

전체 값 동등 비교만 사용하며 부분 필드 비교를 새로 정의하지 않는다. 이 비교 하나로 Index identity, Chunk identity, Source Snapshot/Member identity, source code·version, canonical checksum, external document, chunk index, locator, content hash, canonicalization spec version, normalization version이 모두 결속된다.

어느 한 필드라도 다르면 단일 reason `PROVENANCE_MISMATCH`로 닫는다. `NORMALIZATION_VERSION_MISMATCH`, `SOURCE_VERSION_MISMATCH`, `LOCATOR_MISMATCH` 같은 세분화 enum을 만들지 않는다.

---

## 6. UTF-8 SHA-256 content 검증

```python
sha256(content_text.reveal().encode("utf-8")).hexdigest() == expected.content_hash
```

§5가 observed provenance를 expected와 전체 값으로 결속하므로, expected `content_hash`와의 이 비교는 persisted provenance의 `content_hash`도 동시에 검증한다.

불일치 시 `CONTENT_HASH_MISMATCH`로 닫고 content를 반환하지 않는다. 본문을 normalize하거나 수정해 hash를 맞추지 않는다.

---

## 7. 검증 순서와 Outcome

| Phase | 내용 | 위반 시 reason |
| --- | --- | --- |
| 1 | 입력 구조 검증 | `REQUEST_INVALID` |
| 2 | selection 입력 순서대로 read | `CONTENT_NOT_FOUND`, `CONTENT_READER_ERROR` |
| 3 | observed provenance exact equality | `PROVENANCE_MISMATCH` |
| 4 | UTF-8 SHA-256 content 검증 | `CONTENT_HASH_MISMATCH` |
| 5 | `HydratedGuideRetrievalSelection` 생성 | — |

```python
class GuideContentHydrationDecision(StrEnum):
    HYDRATED = "HYDRATED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class HydratedGuideRetrievalSelection:
    selection: AuthenticatedGuideRetrievalSelection
    content_text: SensitiveText
```

기존 selection을 그대로 보존한다. `hit`와 `binding`을 새 DTO로 복제하지 않고 content만 부착한다.

Production selection 순서를 보존하며 별도 정렬을 적용하지 않는다.

---

## 8. Atomic fail-closed

N개 selection 중 하나라도 실패하면 전체를 거부한다.

```text
selection 1 hydration SUCCESS
selection 2 provenance mismatch
selection 3 미실행

→ decision = REJECTED
→ reasons  = (PROVENANCE_MISMATCH,)
→ selections = ()
```

partial hydrated selections를 반환하지 않으며, 거부 시 남은 selection을 읽지 않는다(fail-fast). reason은 항상 단일 typed reason 1개다.

---

## 9. Reader exception boundary

```text
KnowledgeChunkContentReaderError → CONTENT_READER_ERROR
그 외 예외                        → 그대로 전파
```

`RuntimeError`, `AssertionError` 등 programming error를 광범위한 `except Exception`으로 숨기지 않는다. #672 reader 솔기와 동일한 패턴이다.

Adapter 내부에서 SQLAlchemy/DB driver 예외를 `KnowledgeChunkContentReaderError`로 변환하는 것은 허용한다. 이때 raw SQL, connection string, credential, chunk 본문을 message·log·`__cause__` 어디에도 노출하지 않으며, 로그에는 예외 클래스명만 남긴다.

---

## 10. Read-only transaction

Adapter는 기존 production retrieval과 동일하게 다음을 선언한다.

```sql
SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY
```

금지: `INSERT`, `UPDATE`, `DELETE`, `SELECT ... FOR UPDATE`, advisory lock, 모든 DB write.

---

## 11. 조회 결과 ambiguity

| row 수 | 처리 |
| --- | --- |
| 0 | `CONTENT_NOT_FOUND` |
| 1 | 정상 검증 진행 |
| 2+ | dependency/data-integrity failure → `KnowledgeChunkContentReaderError` |

`LIMIT 1`, 최신 행 선택, arbitrary ordering으로 모호성을 감추지 않는다.

`rag_knowledge_index_member`에는 이미 `uq_rag_knowledge_index_member_chunk` (`knowledge_index_id`, `knowledge_chunk_id`) unique constraint가 있으므로 2+ row는 저장 계층에서 발생할 수 없다. adapter의 거부는 이 제약에 대한 defense-in-depth이며, 제약이 실제로 중복을 차단한다는 사실은 PostgreSQL integration test가 확인한다.

---

## 12. 민감 데이터 취급

`chunk_text`는 일반 로그에 기록하지 않는다. 금지 대상은 raw content logging, row 전체 logging, `repr`의 raw content 노출, failure reason의 raw text 포함이다. content는 `SensitiveText`의 redacted representation을 유지하며, `reveal()`로만 접근한다. 회귀는 unit·integration test에서 sentinel 문자열이 `repr`과 `caplog.text`에 나타나지 않음으로 확인한다.

---

## 13. 명시적 제외 범위

```text
GuideEvidenceSelectionRequest
GuideEvidenceHandoffRequest
build_guide_evidence_handoff()
VerifiedGuideEvidenceHandoff

eligibility_receipt_ref
assessment_artifact_ref
verifier_artifact_ref
assessment_valid_from
assessment_valid_until

Assessment Authority
Eligibility Authority
Source currentness 신규 판정

LangGraph
Guideline Generator
Citation Authorization

Backend API
Frontend
Job/Outbox/Worker

embedding 변경
Knowledge Index rebuild
Retrieval/RRF 변경

DB migration
DB trigger
RLS
stored procedure

PUBLIC_TRACK_F 변경
Production 공개
```

provisional `ai_worker/tasks/rag/evidence_gate.py`의 assessment/eligibility 타입을 production hydration에 연결하지 않으며, `EvidenceGateRetrievalReceipt ↔ ProductionSearchReceipt` 변환을 수행하지 않는다.

---

## 14. 후속 blocker

`HydratedGuideRetrievalSelection` 이후 Handoff 연결에는 다음 production authority material이 선행되어야 하며, 본 계약은 이를 추정하거나 대체하지 않는다.

```text
eligibility_receipt_ref
assessment_artifact_ref
verifier_artifact_ref
assessment_valid_from
assessment_valid_until
```

본 계약은 `docs/contracts/current/`로 승격하지 않는다. 승격은 구현·자동 테스트·실행 증빙 및 지정 책임 리뷰어 승인을 갖춘 별도 PR에서만 수행하며, 본 계약 정렬은 #180 runtime authority 활성화나 Production/Public 전환을 의미하지 않는다.
