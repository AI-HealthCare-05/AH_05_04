# Knowledge Evidence Search 및 Deterministic RRF 계약 v1

## 상태와 범위

- 상태: Proposed · 구현 브랜치 검증 중 · Current 아님
- 추적: Issue #178 Knowledge Evidence Search 및 결정적 RRF 구현 기반. 이 계약만으로 #178 전체(Reranker, Evidence Gate, Retrieval Run persistence, Runtime graph 연결 등)를 완료하지 않는다.
- 구현 담당: 정현우 (`@ceohwj`)
- 책임 리뷰어: Backend·DB·Security 송은영 (`@phina-io`) 1명
- 전문 검토 근거: Evidence·Scope·Safety 권가빈 (`@hazelnutflavoured`), Source provenance 김지혜 (`@Jye-rookie`)의 의견 또는 승인 근거를 첨부하되 추가 필수 PR 리뷰어로 지정하지 않는다.
- 공개 상태: `PUBLIC_TRACK_F=false` 유지

이 계약은 승인된 `rag_knowledge_index`에 인덱싱된 Knowledge Chunk를 대상으로 PostgreSQL 기반의 Lexical(Exact, Trigram, Full-Text Search) 및 pgvector 기반 Dense(Cosine) 검색을 수행하고, 이를 결정적 RRF(`rrf-rank-fusion@1`)로 융합하는 내부 Worker 계약이다. Reranker(`EvidenceRerankPort`), Evidence Gate, authoritative Retrieval Run persistence, Runtime graph 연결, 답변 생성 및 공개 Citation은 범위 밖이다.

## 프로토콜 및 인터페이스

### `EvidenceSearchPort` 프로토콜

```python
class EvidenceSearchPort(Protocol):
    async def search(
        self,
        request: EvidenceSearchRequest,
    ) -> EvidenceSearchResult | EvidenceSearchFailure:
        ...
```

- 본 프로토콜은 비동기(`async`) 인터페이스다.
- 테스트용 동기 모의 객체는 `SyntheticEvidenceSearchPort`로 완전히 분리되며, Production `EvidenceSearchPort`와 타입 및 DTO를 공유하지 않는다.
- 어댑터 생성자(`PostgresqlEvidenceSearchAdapter`)는 `session_factory`와 `adapter_artifact_ref`만 주입받으며, 검색 대상 인덱스 바인딩은 매 `request`가 독립 소유한다.

## 입력 및 설정 계약

### 1. 입력 유효성 검증

- **Query**: NFC 정규화 완료, 길이 1자 이상 2000자 이하, 선행/후행 공백 금지, 제어문자(`\x00`~`\x1f`, `\x7f`~`\x9f` 단 `\t`, `\n`, `\r` 제외), Bidi 제어문자, Zero-width 문자, BOM 금지.
- **Sensitive Wrapper**: `SensitiveText` 및 `SensitiveVector`는 `__repr__`, `__str__`, 예외 메시지, 직렬화 등에서 항상 `<redacted>`로 마스킹된다.
- **Embedding Receipt**: Dense 검색 활성화 시 필수이며, model ref/version, dimension, canonical query fingerprint가 설정 및 쿼리와 일치해야 한다. Dense 비활성화 시 `None`이어야 한다.

### 2. 검색 설정 및 정규 해시 계약

모든 설정 해시는 RFC 8785 호환 JCS 직렬화 후 SHA-256을 적용한다.

- `VersionedLexicalSearchConfiguration` (`knowledge-evidence-lexical-configuration@1`):
  - `exact_top_k`: 20
  - `trigram_top_k`: 20
  - `trigram_similarity_threshold`: 0.3
  - `fts_top_k`: 20
  - `fts_language`: `"simple"`
- `VersionedDenseSearchConfiguration` (`knowledge-evidence-dense-configuration@1`):
  - `dense_top_k`: 20
  - `distance_metric`: `"COSINE"`
  - `ef_search`: null (exact index scan / brute-force)
- `VersionedEvidenceRetrievalConfiguration` (`knowledge-evidence-retrieval-configuration@1`):
  - `lexical_config`: `VersionedLexicalSearchConfiguration`
  - `dense_config`: `VersionedDenseSearchConfiguration` (선택적)
  - `rrf_k`: 60
  - `fusion_top_k`: 30
  - `reranker_input_k`: 20

## 검색 및 순위 융합 계약

### 1. Sub-search 실행 및 필터링

PostgreSQL 어댑터는 단일 `REPEATABLE READ READ ONLY` 트랜잭션 내에서 조회를 수행하며 다음 조건을 모두 만족하는 활성 Chunk만 검색한다:
- 인덱스 구성원 (`rag_knowledge_index_member`)
- Chunk 레코드 (`knowledge_chunk`)
- 문서 레코드 (`knowledge_document`, `record_contract_version = 'KNOWLEDGE_EVIDENCE_V1'`)
- 스냅샷 구성원 (`rag_source_snapshot_member`)
- 스냅샷 레코드 (`rag_source_snapshot`, `status = 'CURRENT'`)
- 오퍼레이션 레코드 (`rag_source_operation`, `is_enabled = true`, `approval_status = 'APPROVED'`)
- 엔드포인트 레코드 (`rag_source_endpoint`, `verification_status = 'VERIFIED'`, `is_enabled = true`, `approval_status = 'APPROVED'`)
- 소스 레코드 (`rag_source`, `status = 'ACTIVE'`)

데이터 무결성 검증:
- Chunk `content_hash`는 raw chunk text의 SHA-256과 정확히 일치해야 함 (불일치 시 fail-closed).
- Member `embedding_sha256`은 저장된 vector의 정규 bytes SHA-256과 일치해야 함.

### 2. Lexical Sub-search 융합

- **Exact Sub-search**: `strpos(chunk_text, query) > 0` 매칭된 후보 최대 20건. Exact bucket에 속한 결과는 비-Exact 결과보다 항상 상위 순위를 차지한다 (엄격 우선순위).
- **Trigram Sub-search**: 트랜잭션 로컬 `set_config('pg_trgm.similarity_threshold', :th, true)`를 설정하고 `chunk_text % query` 매칭 최대 20건.
- **FTS Sub-search**: `to_tsvector('simple', chunk_text) @@ plainto_tsquery('simple', query)` 매칭 최대 20건.
- **Bucket-local Fusion**:
  - Exact 일치 항목이 2개 이상인 경우, Exact bucket 내부에서 Trigram/FTS 점수를 기반으로 RRF를 수행하여 내부 순위를 결정한다.
  - Non-Exact 결과는 Trigram 및 FTS 결과를 RRF 융합하여 Exact 버킷 다음 순위부터 연속적인 1-based rank를 부여한다.
  - 동점 시 Stable coordinate UTF-8 byte order로 결정적 tie-break를 수행한다.

### 3. Hybrid RRF 융합 (`rrf-rank-fusion@1`)

- Lexical 융합 결과(Top 20)와 Dense 검색 결과(Top 20)를 융합한다.
- 각 stage의 rank \( r \in [1, 20] \)에 대해 점수 기여는 \( \frac{1}{60 + r} \)이다.
- 점수 합산은 Python `Fraction` 기반의 무손실 유리수 연산으로 수행되며, 반올림 오차 없이 비교된다.
- 결과 영수증에는 분자와 분모를 담은 `FractionReceipt(numerator, denominator)`가 보존된다.
- 동점 처리: `(rrf_score DESC, source_code ASC, source_version ASC, external_document_id ASC, chunk_index ASC)` (모든 문자열은 UTF-8 byte order).
- 최종 출력은 상위 30건(`fusion_top_k = 30`)이며, 상위 20건(`reranker_input_k = 20`)에는 `is_eligible_for_future_reranker = True`가 설정된다.

### 4. 점수 표현 계약 (`observed-stage-score-decimal@1`)

- 관측용 stage 점수는 IEEE-754 float를 `Decimal.from_float`로 변환 후 소수점 18자리 `ROUND_HALF_EVEN`으로 포맷팅한다: `"{:.18f}"`.
- 이 값은 순위 결정에 사용되지 않으며, 진단 및 검증 목적으로만 기록된다.

## 데이터베이스 및 인덱스 사양

- GIN 인덱스:
  - `ix_knowledge_chunk_fts_simple` on `to_tsvector('simple', chunk_text)`
  - `ix_knowledge_chunk_chunk_text_trgm` on `chunk_text gin_trgm_ops`
- 트랜잭션 격리: `REPEATABLE READ READ ONLY`
- 트랜잭션 로컬 파라미터: `set_config('pg_trgm.similarity_threshold', :th, true)` (커밋/롤백 시 자동 리셋, 풀 커넥션 오염 방지).

## 보안 및 프라이버시

- 쿼리 원문, 임베딩 벡터 원문, 청크 원문, 환자 식별자는 로그, 예외, 진단 결과에 포함되지 않는다.
- 데이터 위변조 또는 무결성 검증 실패 시 상세 DB 오류를 외부로 전파하지 않고 정형화된 `EvidenceSearchFailureReason`(`DATABASE_ERROR`, `RESULT_INTEGRITY_VIOLATION` 등)으로 fail-closed 처리한다.
