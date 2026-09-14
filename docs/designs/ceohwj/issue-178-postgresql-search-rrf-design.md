# Issue #178 PostgreSQL Evidence Search·RRF 설계

| 항목 | 값 |
| --- | --- |
| 대상 Issue | [#178](https://github.com/AI-HealthCare-05/AH_05_04/issues/178) |
| 문서 상태 | Draft implementation design · PR 범위 분리만 Requester-approved · Current Runtime 아님 |
| 구현 담당 | 정현우 (`@ceohwj`) |
| 단일 책임 리뷰 | 송은영 (`@phina-io`) — Backend·DB·Security·AI Worker Search/RRF 경계 |
| 전문 검토 근거 | Evidence·Scope·Safety 권가빈, Source provenance 김지혜; 추가 필수 PR 리뷰어가 아님 |
| 기준 | RAG P0 Approved Target, Approved `PD-362`, PR #482 저장 기반; `PD-315-20260908`은 Review pending 후보 |
| 공개 | `PUBLIC_TRACK_F=false` 유지 |

## 1. 결정

다음 PR은 **실제 PostgreSQL Knowledge Evidence Search와 결정적 RRF**까지만 구현한다.

```text
Query binding
  -> PostgreSQL Lexical: Exact + Trigram + FTS -> Lexical Top 20
  -> PostgreSQL Dense: pgvector cosine          -> Dense Top 20
  -> Application RRF                            -> Top 30
  -> handoff boundary                           -> first 20 eligible for a future reranker
```

이 PR은 `RET-L`, `RET-D`, `RET-H` 실행에 필요한 검색 기반을 만든다. `RET-HR`의 실제 reranker,
Evidence Gate, Retrieval Run 영속화, `hybrid_retrieve` Runtime node, Citation·Composer, 평가 Runner 연결은
후속 PR로 남긴다.

RRF를 `EvidenceRerankPort` 구현이라고 부르지 않는다. 정규 파이프라인에서 RRF와 reranker는
서로 다른 단계다. 모델·provider·score·timeout·artifact 계약이 없는 상태에서 가중합, 모델
호출 또는 pass-through를 실제 reranker로 승격하지 않는다.

### 1.1 권위·승인 선행조건

사용자가 승인한 것은 **이번 PR을 Search + RRF로 제한하는 범위 분리**다. Exact/Trigram/FTS 세부
configuration, `rrf_k=60`, Top-K와 안정 좌표를 정한 `PD-315-20260908`은 저장소 상태상 여전히
`Review pending`이며 이 문서가 그 승인을 대신하지 않는다.

- Gemini는 설계·테스트 초안을 준비할 수 있지만, Production 동작을 구현하기 전 #178 또는 후속 Decision에
  권가빈의 Evidence·Safety 책임 승인과 김지혜의 Source provenance 검토 근거를 기록한다.
- 구현 PR의 필수 리뷰어는 송은영 한 명으로 유지한다. 위 근거 제공자는 추가 필수 PR 리뷰어가 아니다.
- 승인 근거가 생기기 전에는 아래 고정값을 **제안 configuration**으로만 취급하고 Current·Approved·Production
  완료로 표현하지 않는다.
- 승인 과정에서 값이 바뀌면 이 설계와 Proposed contract를 먼저 같은 값으로 갱신한 다음 구현한다.

## 2. 목표와 완료 주장 경계

이 PR이 검증할 수 있는 최대 주장은 다음과 같다.

> 고정된 Knowledge Evidence Index와 요청별 허용 Source Snapshot member 집합, caller가 제공한 구조·결속이
> 유효한 query embedding receipt에 대해 PostgreSQL Exact·Trigram·FTS, pgvector cosine 검색과 rank-only
> RRF가 결정적으로 실행되며, 부적격·변조 provenance는 fail-closed 처리된다.

다음은 이 PR의 완료 주장이 아니다.

- 실제 reranker 품질 또는 `RET-HR` 완료
- Evidence sufficiency·conflict·applicability 판정
- Retrieval Run/signal/hit 원자 저장
- `#180` Runtime Graph와 `hybrid_retrieve` node 연결
- Recall@5·MRR·nDCG@5·latency 기준 통과
- Proposed 계약의 Current 승격
- Production 활성화 또는 환자 공개

## 3. 현재 구현과 설계 전제

### 3.1 재사용하는 구현

- `ai_worker/tasks/rag/evidence_retrieval.py`
  - 민감 query/content wrapper, query fingerprint, artifact reference, search hit/result 검증 개념
- `ai_worker/tasks/rag/knowledge_evidence_index.py`
  - Index receipt, stable coordinate, embedding dimension·model·hash 검증
- `ai_worker/adapters/sqlalchemy_knowledge_evidence_index.py`
  - `AsyncSession` factory, SQLAlchemy Core table projection, typed dependency failure 패턴
- `rag_knowledge_index`, `rag_knowledge_index_member`
  - 고정 Index version, Source Snapshot/member, canonical Chunk identity, pgvector embedding
- `ai_worker.tasks.evaluation.canonical.canonical_json_bytes`
  - RFC 8785 호환 JSON projection에서 사용하는 UTF-16 object-key 정렬·float 금지 구현

### 3.2 Production으로 그대로 승격하지 않는 구현

- `evidence_retrieval_synthetic_adapters.py`의 `weighted-stage-score-v1`
- synthetic `evidence_key`를 Production Chunk identity로 사용하는 경계
- Python raw lexical score와 cosine score를 더하는 방식
- `json.dumps(sort_keys=True)`로 계산한 Production configuration/hash
- 전체 Index를 메모리로 불러와 정렬하는 synthetic search

### 3.3 해소해야 하는 구조적 차이

1. 현재 `EvidenceSearchPort`는 sync이지만 Production DB는 `AsyncSession`/`asyncpg` 전용이다.
2. 현재 kernel은 stage dedupe 뒤에 바로 `EvidenceRerankPort`를 호출하여 RRF와 rerank를 분리하지 못한다.
3. Dense query vector의 model/version/dimension receipt를 생성하는 경계가 없다.
4. `filter_snapshot_ref`는 opaque reference뿐이며 허용 Snapshot/member 집합을 실제로 담지 않는다.
5. 현재 `KnowledgeEvidenceProvenance`는 Production stable coordinate와 Source/Member UUID를 모두 표현하지 않는다.

이 차이를 sync DB driver 추가, event-loop 내 블로킹, synthetic key 재사용, hidden global registry로 우회하지
않는다.

## 4. 범위

### 4.1 포함

- Production Knowledge Evidence Search용 async port 및 PostgreSQL adapter
- 요청별 immutable search binding
- caller가 생성한 query embedding receipt의 검증·소비 경계
- Exact, Trigram, FTS 각 Top 20의 결정적 lexical fusion과 Lexical Top 20
- PostgreSQL pgvector `<=>` cosine distance의 Dense Top 20
- `rrf-rank-fusion@1`, `rrf_k=60`, Hybrid Top 30
- stable coordinate UTF-8 tie-break
- Source/Endpoint/Operation/Snapshot/member/Document/Chunk/Index exact binding 재검증
- typed fail-closed result, 민감문자열 비로그·비직렬화 검증
- 관련 migration/index, model, unit, PostgreSQL integration, query-plan smoke test
- `docs/testing/knowledge-evidence-index-178.md`의 PR #482 상태를 현재 사실로 바로잡고 새 PR의 pre-merge
  검증 결과를 추가

### 4.2 제외

- provider-specific query embedding adapter 또는 실제 외부 embedding API 호출
- reranker model/provider 선정 및 `EvidenceRerankPort` Production adapter
- Retrieval Run/signal/hit table·repository·transaction
- Evidence Gate 정책 변경
- Runtime Bundle/REQUEST Guard 생성 및 `#180` Graph 조립
- Evaluation bridge/runner 및 `RET-L/D/H/HR` 결과 생성
- HNSW/IVFFlat 도입과 ANN latency 성능 주장
- API·OpenAPI·Frontend·공개 Citation 변경
- OCR Candidate Index 코드·테이블·vector 재사용

## 5. 선택한 구조

### 5.1 대안

#### A. 기존 sync Port 이름을 synthetic 전용으로 축소하고 하나의 async Production Port를 추가 — 선택

현재 `evidence_retrieval.EvidenceSearchPort`는 `SyntheticEvidenceSearchPort`로 이름을 바꾸어 기존
sync kernel과 회귀 fixture만 소유하게 한다. 새 `evidence_search.EvidenceSearchPort`는 Lexical·Dense·RRF를
한 요청과 한 DB transaction에서 실행하는 유일한 async Production port다. 이번 PR에서 Runtime Graph가
없으므로 Production 조립을 가장한 새 service/registry는 만들지 않는다.

장점은 PostgreSQL adapter가 실제 권위 `EvidenceSearchPort`를 구현하고 두 Port가 같은 이름·의미를
공유하지 않는 점이다. 비용은 기존 protocol import와 type hint를 synthetic 이름으로 바꾸는 기계적
회귀 변경이 추가되는 점이다. 후속 `#180`은 새 async Port만 소비하며 synthetic Port를 Runtime registry에
등록하지 않는다.

#### B. 기존 kernel·port·test 전체를 async + RRF + optional reranker로 즉시 전환 — 보류

최종 구조는 깔끔하지만 3,000줄 이상의 synthetic 회귀 파일과 RAG-15 소비 타입까지 바꾸게 된다.
실제 Runtime 소비자와 reranker 계약이 없는 현재에는 PR 범위가 필요 이상으로 커진다.

#### C. sync PostgreSQL driver를 추가해 기존 port를 그대로 구현 — 기각

새 dependency와 두 개의 DB 실행 방식을 도입하고 Worker event loop에서 blocking 위험을 만든다.
`CONTRIBUTING.md`의 최소 dependency·명시적 async 경계 원칙에 맞지 않는다.

### 5.2 권장 모듈

```text
ai_worker/tasks/rag/evidence_search.py
  EvidenceSearchPort, request/result, binding, provenance, query embedding receipt

ai_worker/tasks/rag/evidence_rank_fusion.py
  rrf-rank-fusion@1 pure implementation and canonical fraction receipt

ai_worker/adapters/postgresql_evidence_search.py
  AsyncSession-based Exact/Trigram/FTS/pgvector adapter

ai_worker/tests/rag/test_evidence_search.py
  async Port contract, request/provenance/failure tests

ai_worker/tests/rag/test_evidence_rank_fusion.py
  RRF golden vectors and tie-break tests

tests/integration/rag/test_postgresql_evidence_search.py
  real PostgreSQL lexical/dense/filter/index-plan tests
```

기존 `evidence_retrieval.py`를 거대한 Production/
synthetic 복합 파일로 확장하지 않는다. 공통 불변 타입을 재사용할 때는 import 순환이
없고 의미가 완전히 같은 타입만 작은 공통 module로 이동한다. 단일 사용처를 위한 registry,
factory, strategy layer는 만들지 않는다.

`evidence_search.EvidenceSearchPort.search(request)`는 async이며 PostgreSQL adapter가 구현하는 유일한
Production Search port다. 기존 protocol은 `SyntheticEvidenceSearchPort`로 rename하고 기존 sync kernel과
synthetic adapter의 type hint만 그 이름으로 정렬한다. Production Port와 synthetic Port는 request/result
DTO도 공유하지 않으며 상호 교환 가능하다고 주장하지 않는다. 이 rename은 의미를 명확히 하는 회귀 변경이고,
blocking sync bridge·union return type·동일 이름의 두 Port를 남기지 않는다.

```python
class EvidenceSearchPort(Protocol):
    async def search(
        self,
        request: EvidenceSearchRequest,
    ) -> EvidenceSearchSuccess | EvidenceSearchFailure: ...
```

`EvidenceSearchRequest`는 normalized query/fingerprint, execution binding, nullable query embedding receipt만
가진다. execution binding은 request가 단독 소유하며 adapter 생성자나 mutable instance state에 복제하지
않는다. 결과는 Lexical/Dense stage rank·score/provenance와 Hybrid fusion을 한 immutable receipt로 반환한다.
실제 reranker selection이나 Evidence Gate 결과 필드는 넣지 않는다.

## 6. 도메인 계약

### 6.1 요청별 search binding

`PostgresqlEvidenceSearchAdapter`는 global 또는 DB의 “latest”를 스스로 선택하지 않는다. 생성자는
`AsyncSession` factory와 고정 adapter artifact ref만 받으며 request별 상태를 보존하지 않는다. caller가 만든
immutable binding은 `EvidenceSearchRequest.execution_binding`으로만 전달한다.

```text
EvidenceSearchExecutionBinding
  filter_snapshot_ref
  evidence_index_ref
  knowledge_index_id
  allowed_source_snapshot_ids
  allowed_source_snapshot_member_ids
  retrieval_config: VersionedEvidenceRetrievalConfiguration
```

- 하나의 adapter instance는 여러 요청에서 재사용할 수 있지만 mutable binding/cache를 갖지 않는다.
- request 최상위에 binding의 artifact ref나 limit을 중복 필드로 두지 않는다. binding 자체의 ref·ID·config shape가
  유효하지 않으면 SQL을 실행하지 않고 validation failure로 닫는다.
- `VersionedEvidenceRetrievalConfiguration`은 exact frozen type이며 자신의 artifact ref, 아래 8.2 projection,
  `VersionedLexicalSearchConfiguration`, nullable `VersionedDenseSearchConfiguration`을 함께 가진다. 각 nested
  configuration도 artifact ref와 실제 실행값을 함께 보존하고 canonical projection/hash를 재계산한다.
- adapter는 config ref만 받고 실제 값을 hidden registry·environment·module global에서 조회하지 않는다.
- `evidence_index_ref`는 `artifact_code=index_code`, `version=index_version`,
  `content_sha256=index_configuration_hash`로 투영한다. Index configuration hash가 corpus와 embedding manifest
  hash를 다시 결속하므로 세 hash를 서로 같은 값으로 취급하지 않는다.
- 위 `ImmutableArtifactRef` 투영은 기존 Current 계약이 아니라 이번 PR의 Proposed Search contract가 승인해야
  하는 새 의미다. 승인 전 구현 상수로 숨기지 않고 contract field mapping과 golden test에 같은 값을 기록한다.
- 허용 ID 집합은 비어 있으면 실행하지 않는다.
- 이 PR의 integration fixture는 고정 ID로 binding을 구성한다. 실제 REQUEST Guard에서 binding을 만드는
  책임은 `#180` Runtime 연결 PR에 남긴다.
- `filter_snapshot_ref`의 내용/hash projection이 승인되기 전에 DB의 latest row로 이 reference를 재구성했다고
  주장하지 않는다.

### 6.2 Production Evidence identity

Production dedupe·tie-break key는 `evidence_key`가 아닌 다음 stable coordinate다.

```text
(source_code, source_version, external_document_id, chunk_index)
```

hit provenance는 최소한 다음을 보존한다.

```text
knowledge_index_id / index code / index version / index configuration hash
knowledge_chunk_id
source_snapshot_id / source_snapshot_member_id
source_code / source_version / canonical_checksum
external_document_id / chunk_index
locator
content_hash / canonicalization_spec_version / normalization_version
```

동일 stable coordinate에 다른 `content_hash`, Chunk UUID, Snapshot/member 결속이 관측되면 두 candidate로
반환하지 않고 전체 search를 validation failure로 닫는다. Evaluation bridge의 `evidence_key`와
`evidence_ref_id`는 Evaluation producer가 후속에 생성하며 Runtime adapter가 추정하지 않는다.

### 6.3 Query embedding 입력 경계

이 PR은 실제 provider adapter를 만들지 않으므로 미래 구현만을 위한 `EvidenceQueryEmbeddingPort`를
추가하지 않는다. async `EvidenceSearchPort` request가 caller에서 이미 생성된 nullable
`QueryEmbeddingReceipt`를 입력으로 받는다.

```text
QueryEmbeddingReceipt
  -> query_fingerprint
  -> model_ref / model_version
  -> dimension
  -> SensitiveVector(finite non-zero vector)
  -> adapter_artifact_ref
```

- Dense가 활성인 request는 receipt가 정확히 하나 있어야 하고, 비활성이면 receipt는 `null`이어야 한다.
- `query_fingerprint`는 request와 exact-match해야 한다.
- receipt와 `SensitiveVector`는 정확한 frozen runtime type과 immutable tuple만 허용한다. subclass·mutable
  sequence·NaN·Infinity·zero vector를 거부한다.
- `adapter_artifact_ref`는 binding의 dense configuration이 고정한
  `expected_query_embedding_adapter_ref`와 exact-match한다.
- PostgreSQL integration test는 고정 비식별 합성 vector receipt를 직접 주입한다.
- 실제 provider 호출 경계는 provider adapter를 구현하는 후속 PR에서 Port와 구현체를 함께 추가한다.

adapter는 embedding receipt의 model/version/dimension을 `rag_knowledge_index`와 exact-match한 뒤에만 Dense SQL을
실행한다. vector나 query 원문을 receipt, exception, 일반 로그에 넣지 않는다.

이 receipt는 **caller가 제공한 vector의 구조·설정 결속**을 검증할 뿐, 그 vector가 실제로 해당 query에서
provider에 의해 생성됐다는 암호학적 증명은 아니다. 이 PR의 Dense 완료 주장은 “검증된 내부 caller가 제공한
vector에 대한 exact cosine 검색”으로 제한한다. 후속 provider PR은 query fingerprint와 vector를 함께 생성한
configured adapter만 receipt를 발급하고, `#180`은 임의 생성 DTO가 아니라 그 경로의 결과만 Search에 전달한다.

### 6.4 Query validation

`normalized_query`는 Search Port가 SQL 또는 receipt 생성 전에 다음을 fail-closed 검증한다.

- 정확한 `SensitiveText` runtime type
- Unicode NFC이며 `query == query.strip()`
- Python code point 기준 `1..2000`자; 내부 줄바꿈과 연속 whitespace는 보존
- NUL `U+0000`, zero-width `U+200B/U+200C/U+200D`, bidi control `U+202A..U+202E`와
  `U+2066..U+2069`, BOM `U+FEFF` 금지
- trusted caller는 query fingerprint를 변환 전후가 없는 위 exact accepted string에 결속해 생성해야 함

adapter는 trim·casefold·whitespace collapse를 조용히 수행하지 않는다. 위반은 DB connection을 열거나
embedding receipt/vector를 읽기 전에 `REQUEST_INVALID`로 종료한다. 이 2,000자 한계와 금지 문자 집합은 현재
Chat 입력 경계와 정렬하며 변경 시 Proposed Search contract와 입력 경계를 함께 version-up한다.

Search adapter는 HMAC key를 소유하지 않으므로 raw query와 fingerprint의 암호학적 결속을 스스로 재계산하지
않는다. 이 PR은 구조가 유효하고 embedding receipt와 동일한 fingerprint가 전달됐는지만 검증한다. 후속 `#180`은
기존 `QueryBindingVerifierPort`를 먼저 호출하고 성공 receipt가 확인된 동일 query/fingerprint만 Search에
전달해야 하며, 이 연결 전에는 query-binding Production 완료를 주장하지 않는다.

## 7. PostgreSQL Search

### 7.1 일관된 read transaction

`PostgresqlEvidenceSearchAdapter.search(request)` 한 번이 session과 transaction 하나를 소유한다.

1. query type·길이·Unicode·금지 문자를 먼저 검증한다. 실패하면 embedding receipt/vector에 접근하지 않는다.
2. 나머지 request·artifact·configuration·embedding receipt의 in-memory shape와 exact binding을 검증한다.
3. `AsyncSession`을 열고 어떤 SELECT보다 먼저 read-only `REPEATABLE READ` transaction을 시작한다.
4. 고정 Trigram threshold는 같은 transaction에서 `SET LOCAL` 또는 transaction-local `set_config(..., true)`로
   명시한다. connection/session 기본값에 의존하지 않는다.
5. eligible base relation 검증, Exact·Trigram·FTS, Dense, Python RRF와 반환 직전 provenance 재검증을 모두
   같은 snapshot에서 수행한다.
6. dependency 또는 validation failure도 transaction 밖에서 raw DB exception을 노출하지 않고 typed result로
   변환한다.

이 경계는 한 search 안에서 Source·Endpoint·Operation·Snapshot 상태가 서로 다른 시점으로 섞이는 것을 막는다.
transaction 종료 직후 상태가 바뀔 가능성은 `#180` Runtime의 결과 commit currentness 재검증이 닫는다.

### 7.2 공통 filter·provenance 재검증

Lexical과 Dense query는 같은 eligible base relation을 사용한다. 다음을 모두 SQL predicate·join과
Python post-validation으로 검증한다.

- `rag_knowledge_index.id = binding.knowledge_index_id`
- Index code/version/configuration hash가 `evidence_index_ref`와 exact-match
- `rag_knowledge_index_member.knowledge_index_id` exact-match
- member의 Snapshot/member/Chunk UUID가 binding allowed set에 포함
- Source `ACTIVE`
- `ENDPOINT_OPERATION` member는 직접 Endpoint/Operation chain에서, `ARTIFACT` member는
  Ingestion Artifact -> Ingestion Run -> Operation -> Endpoint chain에서 origin을 재구성
- 재구성된 Endpoint `VERIFIED`, Runtime `ENABLED`, Acquisition `APPROVED`
- 재구성된 Operation Runtime `ENABLED`, Acquisition `APPROVED`
- Snapshot은 현재 병합 schema에서 `CURRENT`이고 verification seal·timestamp shape가 유효
- Snapshot `source_version`, `external_version`, `canonical_checksum`의 `PD-362` 문법·hash suffix exact binding
- Snapshot member origin shape가 `ENDPOINT_OPERATION` 또는 `ARTIFACT` 중 하나로 완결
- Document는 `KNOWLEDGE_EVIDENCE_V1`, Chunk hash·normalization version이 존재
- Index member의 비정규화 필드가 joined Source/Snapshot/member/Document/Chunk와 exact-match
- `SHA-256(chunk_text UTF-8 bytes) = content_hash`

현재 schema의 `CURRENT`는 병합된 runtime 계약이며 Approved Target의 파생 Freshness 전체를 대체하지
않는다. 이 PR은 `CURRENT` 확인을 “Target freshness 완료”로 표시하지 않으며, Runtime REQUEST Guard와
결과 commit 재검증은 후속으로 남긴다.

### 7.3 Lexical sub-search

모든 lexical 비교는 Index가 고정한 canonical `knowledge_chunk.chunk_text`에 대해 실행한다. DB에서
임의 casefold/NFC 변환을 추정하지 않는다. request의 `normalized_query`는 caller가 만든 비어 있지 않은
Unicode NFC 문자열이어야 하며 앞뒤 공백이나 연속 whitespace를 adapter가 조용히 바꾸지 않는다. Exact의
제안 P0 의미는 **case-sensitive normalized query의 canonical chunk text 내 연속 substring 일치**이고 SQL은
`strpos(chunk_text, :query) > 0`을 사용한다. PostgreSQL text substring은 유효한 UTF-8 문자열에 대해 실행하며
원문 byte hash나 Chunk normalization version을 다시 쓰지 않는다.

SQL에서 stable coordinate tie-break는 database/default collation에 맡기지 않고 다음 순서를 명시한다.

```sql
convert_to(source_code, 'UTF8') ASC,
convert_to(source_version, 'UTF8') ASC,
convert_to(external_document_id, 'UTF8') ASC,
chunk_index ASC
```

모든 sub-search는 `LIMIT 20` 전에 이 tie-break를 적용한다. Python post-validation은 같은 필드의 UTF-8 bytes와
숫자 `chunk_index`로 순서를 다시 계산해 SQL 결과와 exact-match하지 않으면 validation failure로 닫는다.

제안 `postgresql-knowledge-lexical@1` configuration은 다음 값을 빠짐없이 고정한다.

```text
exact_strategy = case-sensitive-substring-v1
query_normalization = caller-supplied-nonblank-nfc-no-silent-transform-v1
trigram_match_operator = %
trigram_score_function = similarity
trigram_threshold = 0.3          # DEV 제안값; 승인·평가 전 Production 활성화 금지
fts_regconfig = simple
fts_vector_expression = to_tsvector('simple', chunk_text)
fts_query_constructor = plainto_tsquery('simple', query)
fts_score_function = ts_rank_cd
exact_limit = trigram_limit = fts_limit = 20
```

`0.3`은 기존 synthetic threshold를 Production으로 승인한 값이 아니라 DEV integration과 Proposed contract를
재현하기 위한 후보값이다. 승인 또는 DEV 평가가 다른 값을 요구하면 configuration version과 hash를 바꾸며
기존 artifact를 덮어쓰지 않는다. `%`의 threshold는 transaction-local로 `0.3`을 명시하고,
`similarity(chunk_text, :query)`는 순위 score에만 사용한다. SQLAlchemy bound parameter를 사용하고 query 원문을
SQL 문자열·로그에 삽입하지 않는다. `plainto_tsquery`가 빈 query를 만들면 FTS는 0건이며 오류로 간주하지 않는다.

후보 pool과 rank 생성 순서는 다음처럼 고정한다.

1. eligible relation 전체에서 Exact·Trigram·FTS가 각각 상위 20개 stable coordinate를 만든다. Exact 목록은
   `similarity DESC`, `ts_rank_cd DESC`, stable coordinate 순이고, 나머지 두 목록은 자기 score와 stable
   coordinate 순이다. 이 raw score 순서는 sub-search candidate pool 선택에만 사용한다.
2. 세 목록의 union을 만들고 각 candidate의 Exact 여부를 canonical `chunk_text`에 대해 다시 확인한다.
3. union을 Exact/non-Exact bucket으로 나눈다.
4. 각 bucket 안에서 관측된 Trigram score와 FTS score를 각각 다시 정렬해 1-based continuous **bucket-local rank**를
   만든다. 해당 sub-search Top 20에 없던 candidate의 그 signal 기여는 0이다.
5. bucket마다 두 rank를 `rrf_k=60`으로 합치고, Exact bucket 전체를 non-Exact bucket보다 먼저 둔다.
6. 최종 동점은 stable coordinate UTF-8 order로 풀고 전체 Lexical Top 20에 1-based continuous rank를 부여한다.

raw trigram score와 `ts_rank_cd`는 서로 더하지 않는다. Exact 후보가 20개를 넘을 때 어떤 20개를 pool에 넣는지는
Trigram rank, FTS rank, stable coordinate 순으로 결정해 DB 반환 순서에 의존하지 않는다.

### 7.4 Dense search

- query embedding receipt를 Index model/version/dimension과 exact-match한다.
- `rag_knowledge_index_member.embedding <=> :query_vector` 식을 PostgreSQL의 `ORDER BY`와 `LIMIT 20`에
  직접 사용한다.
- cosine similarity receipt는 `1 - cosine_distance`를 canonical decimal string으로 변환하되,
  rank 판정은 DB distance와 stable coordinate tie-break를 사용한다.
- P0 Top 20에는 minimum similarity cutoff를 적용하지 않는다. configuration은
  `minimum_similarity = null`, `cutoff_policy = none-v1`을 명시해 누락값과 의도적 비활성을 구분한다.
- cosine distance가 finite 범위 `[0, 2]`, 변환한 similarity가 `[-1, 1]`을 벗어나거나 vector dimension이
  다르면 전체 stage를 validation failure로 닫는다.
- Dense가 비활성이면 embedding receipt를 읽지 않고 Dense SQL을 실행하지 않는다.

이 PR은 exact cosine Top 20의 정확성을 구현한다. 현재 dimension/version을 고정한 HNSW migration
artifact가 없으므로 ANN index나 latency 개선을 주장하지 않는다. 성능 테스트가 필요성을 입증한 뒤
version-specific cast·partial HNSW DDL·DDL hash를 별도 변경으로 도입한다.

### 7.5 Lexical index migration

현재 `knowledge_chunk.chunk_text` 상에 Production lexical index가 없으므로 forward migration에 다음을
추가한다.

- `GIN (to_tsvector('simple', chunk_text))` expression index
- `GIN (chunk_text gin_trgm_ops)` index

외부 Physical Target의 `section_path` 포함 expression과 달리 현재 병합된 `knowledge_chunk`에는 해당 컬럼이
없으므로 이 PR은 존재하지 않는 값을 추정하지 않고 `chunk_text`만 사용한다. 후속 schema가 `section_path`를
도입하면 새 configuration/index version으로 변경한다.

Exact `strpos`는 이 PR에서 index-backed라고 주장하지 않는다. eligible Index member로 먼저 범위를 제한한 뒤
실행하며 latency 평가는 후속 평가에 남긴다. Exact 가속을 위한 별도 projection/column은 측정 근거 없이
추가하지 않는다.

최종 operator class는 PostgreSQL integration test의 `EXPLAIN (FORMAT JSON)`으로 실제 query와 일치함을
검증한다. 인덱스를 생성했다는 사실만으로 사용 증명을 대신하지 않는다. `pg_trgm`은
기존 migration에서 이미 생성하지만 새 migration·CI fixture에서 선행 존재를 검증한다.

downgrade는 index만 제거하며 Knowledge/Evidence data를 삭제·변환하지 않는다. 기존 applied migration을
수정하지 않는다.

### 7.6 Raw signal score receipt

PostgreSQL native score는 candidate pool과 raw rank를 정하는 데만 사용한다. receipt에 넣는 관측용
`raw_score`는 `observed-stage-score-decimal@1`로 다음처럼 변환하며, 변환한 값으로 순위를 다시 계산하지 않는다.

```text
input = DBAPI가 반환한 finite Python float
decimal_conversion = Decimal.from_float(input)
quantum = Decimal("0.000000000000000001")
rounding = ROUND_HALF_EVEN
decimal_context_precision = 50
negative_zero = "0"
serialization = base-10 canonical decimal; exponent와 불필요한 trailing zero 금지
```

- Exact hit의 `raw_score`는 항상 `"1"`이다.
- Trigram `similarity`, FTS `ts_rank_cd`, Dense cosine similarity는 위 변환을 사용한다.
- Exact·Trigram·FTS·Dense raw signal은 method별 raw rank/score로 분리한다.
- Lexical bucket RRF와 Hybrid RRF의 authoritative score는 이 관측값이 아니라 `Fraction` numerator/denominator다.
- DB native score 순서와 stable coordinate로 만든 raw rank가 Python 재검증 결과와 다르면 전체 search를
  `SEARCH_RESULT_INVALID`로 닫는다.

향후 `retrieval_signal.raw_score` 저장 타입이 다른 scale을 승인하면 Search receipt를 조용히 재해석하지 않고
새 score projection version과 변환 test를 추가한다.

## 8. 결정적 RRF

### 8.1 공식

Algorithm ID는 `rrf-rank-fusion@1`이다.

```text
score(candidate) = sum(1 / (60 + rank_in_stage))
```

- rank는 1-based positive continuous integer다.
- 미등장 stage의 기여는 0이다.
- Python `fractions.Fraction`으로 비교하며 정렬 전 반올림하지 않는다.
- receipt는 기약분수 `numerator`/`denominator` base-10 string을 저장한다.
- score 동점은 stable coordinate의 필드별 UTF-8 byte tuple, 마지막에 `chunk_index` 숫자로 풀어낸다.
- Lexical/Dense의 동일 stable coordinate는 provenance·content exact-match 후 하나로 dedupe한다.
- 한 stage의 중복 coordinate, 비연속 rank, 다른 content hash, detached provenance는 전체 fusion을
  validation failure로 닫는다.
- Hybrid RRF는 Top 30을 반환하고 앞 20개만 후속 reranker input 적격 후보로 표시한다.

RRF output은 `rerank_rank`, `rerank_score` 이름을 사용하지 않는다. `fusion_rank`,
`authoritative_fraction`, 관측용 nullable `numeric_score` 또는 동등한 명시적 이름을 사용한다.

### 8.2 Configuration receipt

Production retrieval configuration projection은 최소한 다음을 포함한다.

```text
algorithm_id = rrf-rank-fusion@1
lexical_config_ref
dense_config_ref | null
expected_query_embedding_adapter_ref | null
rrf_k = 60
exact_limit = 20
trigram_limit = 20
fts_limit = 20
lexical_limit = 20
dense_limit = 20
hybrid_limit = 30
future_reranker_input_limit = 20
stable_coordinate_fields
tie_break = stable-coordinate-fieldwise-utf8-ascending
lexical exact/trigram/FTS configuration
dense distance metric = COSINE
dense minimum_similarity = null
dense cutoff_policy = none-v1
observed_score_projection = observed-stage-score-decimal@1
observed_score_quantum = "0.000000000000000001"
observed_score_rounding = ROUND_HALF_EVEN
transaction_isolation = REPEATABLE_READ
transaction_access = READ_ONLY
```

projection은 RFC 8785 호환 canonical JSON bytes를 사용한다. float를 금지하고 threshold·quantum은 canonical
decimal string으로 표현한다. nested config ref와 실제 nested projection/hash가 exact-match하지 않으면 DB를
호출하지 않고 `RETRIEVAL_CONFIG_INVALID`로 닫는다. synthetic adapter의 `json.dumps(sort_keys=True)` hash를
재사용하지 않는다.

## 9. 오류·Privacy 계약

### 9.1 안정 실패 분류

최소 실패 축은 다음을 구분한다.

- `REQUEST_INVALID`
- `FILTER_BINDING_INVALID`
- `RETRIEVAL_CONFIG_INVALID`
- `INDEX_BINDING_INVALID`
- `SOURCE_BINDING_INVALID`
- `SOURCE_NOT_CURRENT`
- `QUERY_EMBEDDING_INVALID`
- `LEXICAL_DEPENDENCY_ERROR`
- `DENSE_DEPENDENCY_ERROR`
- `SEARCH_RESULT_INVALID`
- `FUSION_INPUT_INVALID`

기존 public/shared enum에 이 값을 추가해야 한다면 Decision·Proposed contract·test를 같이 갱신하고
단일 책임 리뷰어의 범위 승인을 받는다. adapter exception message를 typed failure에 복사하지 않는다.

### 9.2 금지 데이터

다음은 `repr`, exception, 일반 로그, Stream, DLQ, quarantine, receipt에 넣지 않는다.

- raw/normalized patient query
- query embedding vector
- Knowledge chunk text 또는 Source 원문
- 처방·OCR·Chat 원문과 식별자
- HMAC key, provider credential, SQL parameter value
- adapter/DB/provider의 raw exception message

query fingerprint, artifact ref, stable configuration ID/hash, 비민감 provenance ID/hash만 제한된 diagnostic에
포함할 수 있다. 이 diagnostic을 authoritative Retrieval Run으로 저장하지 않는다.

## 10. 테스트 설계

Gemini 구현은 TDD로 진행하며 각 행동의 실패를 먼저 관측한 다음 최소 구현을 추가한다.

### 10.1 Pure unit

- RRF 미등장 stage 기여 0
- `1/(60+r)` exact Fraction golden vector
- 매우 근접한 분수 score 비교에서 반올림 미사용
- 동점 stable coordinate UTF-8 fieldwise order; 한글·ASCII·non-BMP 포함
- 동일 coordinate/content exact dedupe
- coordinate 동일·content hash 불일치 fail-closed
- stage 내 중복·비연속 rank 거부
- Top 30과 future reranker-input 20 경계
- 기존 sync protocol이 `SyntheticEvidenceSearchPort`로만 노출되고 Production `EvidenceSearchPort`와 이름·DTO를
  공유하지 않음
- adapter 생성자는 session factory와 adapter artifact ref만 받고 binding은 request가 단독 소유함
- nested lexical/dense/retrieval configuration의 projection/hash mismatch와 hidden registry 미사용
- query 길이 0/2001, leading/trailing whitespace, non-NFC, NUL·zero-width·bidi·BOM 거부와 DB session 0회
- embedding receipt exact frozen type, immutable vector, expected adapter artifact exact binding
- `observed-stage-score-decimal@1`의 float -> 18자리 ROUND_HALF_EVEN, trailing zero·negative zero golden vector
- canonical configuration JSON non-BMP key·null·float 금지 golden vector
- wrapper·request·failure·result의 query/content/vector 비노출

### 10.2 PostgreSQL integration

- Exact hit이 non-Exact 보다 먼저임
- Exact Top 20 pool의 `similarity -> ts_rank_cd -> stable coordinate` 순서와 bucket-local rank 재부여
- Exact bucket 내 Trigram/FTS RRF와 UTF-8 tie-break
- Trigram-only 오타 hit
- FTS-only 토큰 hit·`ts_rank_cd` rank
- native Trigram/FTS score로 정한 raw rank가 관측용 18자리 score 반올림 뒤에도 바뀌지 않음
- Lexical Top 20·1-based continuous rank
- cosine Top 20·distance tie-break·similarity receipt
- Dense disabled 시 embedding receipt가 `null`이고 Dense SQL 0회; receipt가 있으면 request invalid
- Dense enabled 시 receipt 누락·중복 및 model/version/dimension/query fingerprint mismatch fail-closed
- Dense `minimum_similarity=null`에서 음수 similarity candidate도 Top 20 순위 후보이며 cutoff를 암묵 적용하지 않음
- 다른 Index version·Snapshot·member의 hit 0건
- inactive Source, disabled/unapproved Endpoint·Operation, non-CURRENT Snapshot 차단
- `source_version`/`external_version`/checksum suffix 변조 차단
- member origin shape·Document/Chunk/index denormalized provenance 불일치 차단
- Chunk text/content hash 변조 차단
- 한 search가 read-only `REPEATABLE READ` transaction 하나만 사용하고 모든 SELECT가 같은 snapshot을 관측
- transaction 중 Snapshot/Endpoint 상태를 별도 connection에서 변경해도 현재 search 결과가 혼합되지 않으며,
  다음 search부터 변경 상태가 반영됨
- transaction-local Trigram threshold가 connection pool의 다음 borrower에게 남지 않음
- rollback에서 lexical index만 제거하고 데이터 보존
- `EXPLAIN (FORMAT JSON)` query-plan smoke; fixture 크기에 따라 planner가 seq scan을 선택하는 경우
  `enable_seqscan=off`는 테스트 transaction에서만 사용해 index 호환성을 증명
- SQLAlchemy bound parameter·exception/log에 민감 query/vector 비노출

### 10.3 회귀·검증 명령

먼저 최소 검사를 실행하고 마지막에 저장소 필수 검사를 실행한다.

```bash
UV_CACHE_DIR=/private/tmp/ah178_search_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_search.py -q
UV_CACHE_DIR=/private/tmp/ah178_search_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_rank_fusion.py -q
UV_CACHE_DIR=/private/tmp/ah178_search_uv_cache uv run pytest tests/integration/rag/test_postgresql_evidence_search.py -q
UV_CACHE_DIR=/private/tmp/ah178_search_uv_cache uv run pytest ai_worker/tests/rag -q
UV_CACHE_DIR=/private/tmp/ah178_search_uv_cache uv run ruff check ai_worker/tasks/rag ai_worker/adapters ai_worker/tests/rag tests/integration/rag
UV_CACHE_DIR=/private/tmp/ah178_search_uv_cache uv run ruff format --check ai_worker/tasks/rag ai_worker/adapters ai_worker/tests/rag tests/integration/rag
UV_CACHE_DIR=/private/tmp/ah178_search_uv_cache uv run mypy ai_worker/tasks/rag ai_worker/adapters
bash scripts/ci/run_test.sh
git diff --check
```

PostgreSQL integration 테스트는 기본 Worker unit lane에 조용히 추가하지 않는다.
`tests/contract/test_python_test_inventory.py`에서 명시적 integration lane으로 분류하고 기존 CI 인벤토리를
갱신한다. 합성 fixture만 사용하며 외부 embedding/provider를 호출하지 않는다.

## 11. PR·문서 계약

### 11.1 브랜치·리뷰

- `develop` 최신 기준 `feat/178-postgresql-evidence-search-rrf`
- PR 대상: `develop`
- 구현 담당과 단일 책임 리뷰어를 Issue·PR에 별도 표시
- 단일 책임 리뷰어: 송은영
- 리뷰 범위: PostgreSQL query/index, Source/Index provenance, async Worker boundary, Security/Privacy,
  contract/test consistency
- Evidence/Safety·Source 전문 검토 근거는 별도 첨부하되 추가 필수 PR 리뷰어로 지정하지 않음

### 11.2 변경 문서

구현 PR은 최소한 다음을 같이 갱신한다.

- Production Search/RRF proposed contract 또는 현재 Knowledge Evidence Index proposed contract의 별도 후속 문서
- `docs/contracts/README.md` index
- `docs/data-schema.md`: lexical index·exact dense Search/RRF가 구현된 범위와 미구현 범위 분리
- `docs/testing.md`: unit/integration lane
- `docs/testing/knowledge-evidence-index-178.md`: PR #482 병합 상태와 이번 PR의 pre-merge 실제 검증 결과

같은 PR 문서에서 아직 발생하지 않은 “병합 완료”를 미리 주장하지 않는다. 병합 전에는 commit SHA·실행
환경·명령·pass/skip/fail 수를 `pre-merge verification`으로 기록한다. 병합 후 develop 재검증이나 상태 변경이
필요하면 별도 후속 commit/PR에서 `post-merge verification`으로 추가한다.

실제 구현·migration·contract/integration test·단일 책임 리뷰 승인이 한 PR에 모이기 전에
Proposed 계약을 `current/`로 옮기지 않는다. Search/RRF 병합만으로 #178을 닫지 않는다.

### 11.3 복잡성 도입 근거

PR 본문에 `CONTRIBUTING.md`가 요구하는 다음 다섯 항목을 기록한다.

1. 현재 sync synthetic port로는 `asyncpg` PostgreSQL query와 RRF/reranker 분리를 표현할 수 없다.
2. 기존 Port를 synthetic 전용 이름으로 축소하고 request-bound async Production Search port, explicit
   embedding receipt input, pure RRF를 제안한다.
3. protocol rename, 새 모듈·Production port·migration·integration lane의 유지보수 비용이 추가된다.
4. sync driver 추가, synthetic weighted score 승격, kernel 전체 async 전환, DB 전체 메모리 정렬을 검토했다.
5. 실제 DB I/O·민감 embedding receipt 입력·결정적 합성을 안전하게 분리하려면 지금 필요하다. provider
   abstraction은 실제 adapter가 생기는 후속 PR까지 만들지 않는다.

## 12. Gemini 구현 핸드오프

Gemini는 다음 순서를 따른다.

1. `develop`을 최신화하고 전용 Issue 브랜치를 만든다. 다른 작업트리의 변경을 가져오거나
   되돌리지 않는다.
2. `AGENTS.md`, `CONTRIBUTING.md`, `SECURITY.md`, `docs/privacy-safety.md`, 이 설계, `PD-315`,
   RAG Runtime/Source/Evaluation target을 먼저 읽는다.
3. #178 또는 연결 Decision에서 PD-315 세부값, Proposed lexical configuration과 Index artifact mapping의
   책임 승인·전문 검토 근거를 확인한다. 없으면 코드 구현을 시작하지 않고 blocker를 기록한다.
4. 기존 sync protocol을 `SyntheticEvidenceSearchPort`로 rename하고 Production `EvidenceSearchPort`가 하나만
   존재하는 import/type test를 먼저 고정한다.
5. stateless adapter constructor와 request 단독 binding, query validation·embedding receipt 신뢰 경계,
   `observed-stage-score-decimal@1` golden test를 고정한다.
6. shared enum·DTO·error meaning·DB schema를 바꾸는 부분을 먼저 표시하고 Proposed contract·test를
   같이 작성한다. 미확정 값을 추정하지 않는다.
7. Pure RRF golden test -> RED -> 최소 구현 -> GREEN을 먼저 완료한다.
8. read-only `REPEATABLE READ` transaction과 PostgreSQL filter/provenance test -> RED -> eligible base
   relation을 구현한다.
9. Exact -> Trigram -> FTS -> bucket-local lexical fusion을 하나씩 TDD로 추가한다.
10. query embedding receipt 검증 -> Dense `<=>` -> cutoff 없는 Top 20을 TDD로 추가한다.
11. lexical migration/index·query-plan smoke·downgrade 보존을 검증한다.
12. 전체 RAG unit, integration, Ruff, format, Mypy, CI script를 실행하고 fresh output을 보존한다.
13. 검증 문서에 PR #482의 병합 사실과 이번 PR의 pre-merge 명령·pass/skip/fail 수·DB/extension
    version·미완료 범위를 구분해 기록한다.
14. PR에 `Part of #178`을 사용하고 Issue auto-close keyword를 쓰지 않는다.

다음 중 하나라도 발생하면 구현을 추정해 계속하지 말고 #178에 blocker를 기록한다.

- 실제 reranker algorithm/provider·score 계약이 필요함
- `filter_snapshot_ref`의 authoritative content/hash를 이 PR에서 정본으로 생성해야 함
- Approved Target의 파생 Freshness를 현재 `CURRENT` schema에 섞어 추정해야 함
- Runtime Guard·Retrieval Run·Evidence Gate transaction을 이 PR에 포함해야만 테스트가 통과함
- HNSW dimension/version-specific DDL 계약 없이 ANN 성능을 주장해야 함
- 기존 shared contract와 이 설계가 충돌함
- `PD-315` 또는 Proposed lexical/index-ref 계약의 책임 승인 근거가 없음
- PostgreSQL locale/collation에서 제안 Exact·Trigram·FTS 의미를 재현하지 못함

## 13. 후속 순서

1. reranker model/provider/configuration/receipt/timeout 계약 승인
2. 실제 `EvidenceRerankPort` adapter·`RET-HR`
3. Retrieval Run/signal/hit 원자 저장
4. `#180` `hybrid_retrieve` node·Evidence Gate 연결
5. `RET-L`, `RET-D`, `RET-H`, `RET-HR` 실제 Runner·Recall@5·latency 평가
6. 검증 문서 갱신, Proposed -> Target/Current 승격 판단

#178은 위 후속 완료 조건과 Issue 본문의 전체 체크리스트가 충족될 때까지 Open으로 유지한다.
