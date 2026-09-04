# Issue #167 RAG 의약품 후보 인덱스 로직 설계

## 상태

- Issue: `#167`
- 브랜치: `feat/167-rag-candidate-index`
- 범위: RAG-07A의 결정적 후보 인덱스 빌드 로직과 RAG-07B 인계 계약
- 구현 담당자: 정현우 (`@ceohwj`)
- 담당 리뷰어: 김지혜 (`@Jye-rookie`)
- 교차 영역 리뷰어: 송은영 (`@phina-io`)
- Product·Safety·Evaluation provenance 리뷰어: 권가빈 (`@hazelnutflavoured`)
- 공개 게이트: `PUBLIC_TRACK_F=false`

## 정본과 착수 상태

이 설계는 저장소의 승인된 목표 계약(Approved Target)과 외부 정본 묶음(Authority Bundle)
`post-mvp-rag-evaluation-contract@2026-08-29.11`을 따른다.

| 정본 | 버전 | 역할 |
| --- | --- | --- |
| `rag-design` | `1.50` | 규범적 아키텍처 |
| `rag-db-schema` | `1.47` | 규범적 물리 목표 |
| `rag-source-policy` | `1.18` | 규범적 Source Governance |
| `rag-evaluation-plan` | `1.35` | 규범적 Evaluation |
| `rag-implementation-order` | `1.16` | 비규범적 구현 순서 |

Issue #125와 Issue #185는 닫혔고 계약 산출물이 저장소에 존재한다. Issue #166은 아직 열려
있으며 Source Governance Receipt는 실제 Source·Catalog 준비 상태를 `BLOCKED`로 기록한다.
따라서 이 변경에서는 순수 계약, 결정적 로직, 합성 테스트를 구현할 수 있지만 연결된 MFDS
Catalog, 활성 Candidate Index 또는 통합 완료를 주장해서는 안 된다. RAG-06 Catalog export
Receipt가 연결될 때까지 통합 차단 코드는 `BLOCKED_BY_RAG_04_OR_06`이다.

## 문제

Candidate Resolver는 승인된 의약품 Catalog에서 파생된 재현 가능한 OCR 전용 인덱스가 필요하다.
버전이 고정된 빌드 경계가 없으면 같은 Catalog가 서로 다른 구성원 집합을 만들 수 있고, 미승인 또는
만료된 구성원이 검색에 들어갈 수 있으며, lexical·vector hit가 생성 근거인 버전을 잃을 수 있다.

RAG-07A는 PostgreSQL 저장, build transaction, active pointer, Candidate Search finalization,
Single Candidate Gate 판정 또는 환자 공개 DTO를 소유하지 않으면서 이 경계를 정의해야 한다. 또한
의약품 Candidate 검색과 의료 Evidence 검색을 물리적·의미적으로 분리해야 한다.

## 목표

1. 같은 승인 Catalog export와 build configuration에서 입력 record 순서와 무관하게 동일한 정렬
   Candidate 구성원 집합과 content hash를 생성한다.
2. 공식 Product Identity와 Source, Catalog, normalization, embedding, index configuration
   provenance를 타입이 지정된 불변 값으로 보존한다.
3. Catalog가 부분 상태, 만료, 미승인, 내부 불일치 또는 충돌하는 공식 Identity를 포함하면 구성원을
   반환하기 전에 fail-closed한다.
4. RAG-07B와 후속 Resolver가 소비할 Candidate 검색 단계 순서와 내부 raw hit 계약을 정의하되 DB
   저장이나 환자 공개 판정을 구현하지 않는다.
5. Candidate 데이터, score, vector와 진단 metadata가 의료 Evidence 또는 환자 DTO 필드로 사용되지
   않게 한다.

## 선택한 접근

`ai_worker/tasks/rag/candidate_index.py`에 의존성이 적은 순수 참조 경계를 구현한다.
이 모듈은 불변 입출력 타입, 검증, canonical 구성원 생성, manifest hashing과 search port를
소유한다. PostgreSQL adapter는 후속 RAG-07B에서 Exact, Alias, `pg_trgm`, `pgvector` 물리 쿼리를
구현한다.

RAG-07A는 PostgreSQL similarity 또는 ANN 동작을 Python으로 모사하지 않는다. 이를 모사하면 승인된
DB configuration과 다른 score나 정렬 결과를 만들 수 있다. 대신 RAG-07A가 검색 단계 순서, 타입이
지정된 signal, limit, identity key와 provenance를 고정한다. 결정적 단위 테스트에서는 단계별 hit를
통제해서 반환하는 합성 search adapter를 사용한다.

Protocol만 정의하는 방식은 결정적 build, validation, manifest integrity 완료 기준을 충족하지 못하므로
채택하지 않는다. 직접 DB까지 구현하는 방식은 migration, repository, lock, persistence와 active
pointer가 RAG-07B 소유이므로 채택하지 않는다.

## 모듈 경계

### 구현 모듈

`ai_worker/tasks/rag/candidate_index.py`는 RAG-07A의 전체 공개 경계를 포함한다.

- Catalog 입력, Candidate 구성원, build configuration, manifest, failure, 검색 단계, raw hit와 검색
  결과를 위한 enum 및 불변 dataclass
- fail-closed validation과 결정적 구성원·manifest 생성을 위한 `build_candidate_index(...)`
- RAG-07B가 구현하는 `Protocol`인 `CandidateIndexSearchPort`
- 검색 단계 순서 실행, limit 강제, provenance 검증과 raw hit 조립을 위한
  `search_candidate_index(...)`

첫 구현을 Issue에 지정된 단일 모듈에 두어 RAG-07B가 하나의 import surface를 사용하게 한다. 이후
모듈 분리가 필요하더라도 공개 이름은 `candidate_index.py`에서 다시 export한다. 이 Issue에서는
선제적으로 추가 package 구조를 만들지 않는다.

### 테스트 모듈

`ai_worker/tests/rag/test_candidate_index.py`는 비식별 합성 Catalog record와 결정적 fake search
port를 사용해 공개 API를 검증한다. 테스트는 PostgreSQL, 외부 model download, Source endpoint,
credential 또는 환자정보를 요구하지 않는다.

## Catalog 입력 계약

`CandidateCatalogExport`는 순수 builder가 소비하는 RAG-06 인계값이다. 다음 값을 포함한다.

- `catalog_version`
- `catalog_manifest_hash`
- 비어 있지 않은 `source_refs`
- `schema_version`과 `normalization_version`
- `verification_status`, `freshness_status`, `is_complete`
- Product, Ingredient, Component, Alias와 Search Entry record
- 선언된 count 및 duplicate, orphan, conflict count

입력 record 타입은 안정적인 공식 Identity와 Snapshot 단위 publication row를 구분한다. Product
Identity는 `entity_type=PRODUCT`, `code_system`, `canonical_code` tuple이다. DB UUID, Source row
UUID, object key, 표시 이름, normalized text 또는 이름 hash를 이 tuple 대신 사용해서는 안 된다.

Candidate Search Entry는 `PRODUCT_NAME`과 `APPROVED_ALIAS`로 제한한다. 승인 Alias는 Product
Identity를 대상으로 해야 하고 active, approved, effective 상태이며 고정된 Catalog export에 포함돼야
한다. Ingredient Alias는 후속 ingredient-exact 진단 경계에서만 사용하며 이 인덱스의 Product
Candidate 구성원이 되면 안 된다.

Catalog export의 모든 문자열은 Unicode NFC여야 한다. RAG-07A는 NFD 등 다른 표현을 조용히 NFC로
변환하지 않는다. 자동 변환은 RAG-06 Parser의 계약 위반을 숨기고 저장 문자열과 canonical hash가 서로
다른 내용을 식별하게 만들 수 있으므로, NFC가 아닌 문자열이 하나라도 있으면 전체 build를
`CATALOG_TEXT_NOT_NFC`로 실패시킨다. 실패 detail에는 원문이 아니라 안정적인 필드 경로만 포함한다.

`PRODUCT_NAME` Search Entry의 `display_text`와 `normalized_text`는 연결된 Product row의
`product_name`, `normalized_product_name`과 각각 exact-match해야 한다. Alias Entry가 Alias row와
대조되는 것과 같은 참조 무결성 규칙이며, 불일치하면 구성원을 만들기 전에 fail-closed한다.

### Source 결속 계약

`CandidateCatalogSourceRef`는 `snapshot_id`와 `source_version`을 하나의 불변 값으로 결속한다.
Catalog와 manifest는 이 값의 정렬된 tuple인 `source_refs`를 사용하며, 두 필드를 서로 독립된 배열로
저장하지 않는다. 독립 배열은 정렬 뒤 Snapshot과 Version의 대응 관계를 잃어 서로 다른 Source
조합이 같은 hash로 표현될 수 있기 때문이다.

각 Source reference의 두 필드는 비어 있지 않고 Unicode NFC여야 한다. 하나의 Catalog export에서
같은 `snapshot_id`가 다른 `source_version`과 결속되거나 동일한 Source reference가 중복되면
`CATALOG_SOURCE_BINDING_INVALID`로 전체 build를 실패시킨다. Canonical payload와 manifest hash는
`(snapshot_id, source_version)` UTF-8 byte 순으로 정렬한 전체 `source_refs`를 포함하므로 결속이
달라지면 hash도 달라진다.

Product, Ingredient, Component, Alias와 Search Entry는 각각 자신의 `source_snapshot_id`를 가지며
그 값은 Catalog의 `source_refs` 중 하나를 참조해야 한다. 이 참조가 `source_refs`에 없으면 orphan
Component·Alias reference와 동일하게 `REFERENTIAL_INTEGRITY_INVALID`로 실패시킨다.
`CATALOG_SOURCE_BINDING_INVALID`는 `source_refs` 자체의 내부 무결성(중복 reference, 동일
`snapshot_id`의 상충하는 `source_version`)에만 사용하고 개별 record의 참조 실패에는 사용하지 않는다.
Source 결속 규칙은 다음과 같다.

- `PRODUCT_NAME` Search Entry의 Source Snapshot은 연결된 Product publication의 Snapshot과 같아야 한다.
- `APPROVED_ALIAS` Search Entry의 Source Snapshot은 연결된 Alias publication의 Snapshot과 같아야 한다.
- Alias와 Product는 서로 다른 승인 Snapshot에서 관리될 수 있으므로 두 Snapshot이 같을 필요는 없다.
  다만 Product와 Alias의 Snapshot은 모두 같은 Catalog export의 `source_refs`에 포함돼야 한다.

RAG-06은 Catalog manifest의 정본 envelope와 canonicalization specification version을 발행해야 한다.
RAG-07A는 현재 `catalog_manifest_hash`의 형식과 위 Source 결속을 검증하며, 정본 envelope가 확정되기
전까지 알 수 없는 canonicalization version을 추정하거나 hash 일치로 간주하지 않는다.

### 필수값과 상태 무결성

Catalog envelope의 version·schema·normalization·manifest hash, 모든 Source reference, 공식 Identity,
publication/reference와 검색 문자열의 필수 필드는 비어 있으면 안 된다. nullable 필드는 `null`이거나
비어 있지 않은 값이어야 하며 공백 문자열을 값으로 사용하지 않는다. Product, Alias, Search Entry의
표시 문자열과 normalized 문자열도 이 규칙을 따른다. 위반은 원문 없이 안정적인 필드 경로를 담은
`CATALOG_REQUIRED_FIELD_INVALID`로 실패시킨다.

Catalog는 `source_refs`와 Product를 하나 이상 포함해야 한다. 둘 중 하나라도 비어 있으면 필수 필드
누락과 동일하게 `CATALOG_REQUIRED_FIELD_INVALID`로 실패시키며 `details`에 `source_refs` 또는
`products`를 담는다.

필터링과 중복 제거가 끝난 뒤에도 active·approved Candidate 구성원이 하나 이상 존재해야 한다. 선언된
Source reference와 Product가 개별적으로는 유효했지만 필터링 결과 구성원이 0건이면 같은
`CATALOG_REQUIRED_FIELD_INVALID`를 `details=("members",)`로 사용하며, 빈 Candidate index는 성공으로
발행하지 않는다.

선언된 active·approved Search Entry가 비활성 Product를 참조하거나, 승인 Alias Entry가
미승인·비활성·비유효 Alias를 참조하는 상태 모순은 정상적인 제외가 아니라
`REFERENTIAL_INTEGRITY_INVALID`다. inactive 또는 pending Search Entry만 의도적으로 제외할 수 있다.

## Candidate 구성원 계약

`CandidateIndexMember`는 검색 가능한 Product entry 하나에 대한 결정적 build output이다. 다음 값을
포함한다.

- 안정적인 Product Identity tuple
- Product publication reference와 Product 표시 속성
- `entry_type`
- 표시용 문자열과 normalized 검색 문자열
- nullable 승인 Alias reference
- Product Source Snapshot, Entry Source Snapshot과 nullable Alias Source Snapshot
- Catalog provenance
- normalization version
- 안정적인 `member_key`와 `member_content_hash`
- 버전이 고정된 embedding port가 제공하는 nullable embedding vector

구성원에는 환자 식별자, 처방 문자열, OCR raw value, 미검토 LLM output, 의료 claim, Evidence chunk,
Citation locator, HIRA 파생 코드 또는 환자 공개 status를 포함하지 않는다. Identifier Exact 조회는
별도로 승인된 RAG-07B 입력으로 유보하고 Candidate 구성원 text나 embedding에 합치지 않는다.

구성원 key는 공식 Product Identity, entry type과 안정적인 entry reference로 만든다. 구성원 content
hash는 일시적인 runtime object를 제외한 전체 canonical 구성원 payload로 계산한다. 완전히 동일하게
반복된 입력 구성원은 하나로 합친다. 동일한 stable key가 다른 content를 가지면 conflict로 판단해
전체 build를 실패시킨다.

구성원의 `product_source_snapshot_id`, `entry_source_snapshot_id`, `alias_source_snapshot_id`는 위
Source 결속 검증을 통과한 값을 그대로 보존한다. Alias가 아닌 구성원은
`alias_source_snapshot_id=null`이어야 한다. 하나의 모호한 Source 필드로 세 publication의 근거를
합치지 않는다.

## 빌드 설정과 임베딩

`CandidateIndexBuildConfig`는 다음 값을 포함한다.

- `index_code`와 `index_version`
- `normalization_version`
- lexical configuration version
- 검색 순서 version
- `candidate_limit`
- `display_limit=1`
- embedding mode인 `LEXICAL_ONLY` 또는 `HYBRID`
- nullable embedding provider, model, model version, dimension, distance metric과 ANN configuration
- canonical configuration hash

`LEXICAL_ONLY`는 모든 embedding·ANN 필드가 null이어야 하며 vector가 없는 구성원을 생성한다.
`HYBRID`는 버전이 고정된 embedding provider와 model, 양수 dimension, `COSINE` distance, 모든 구성원의
완전한 vector를 요구한다. build 성공 전에 vector count, 순서, dimension, finite value와 model
provenance를 검증한다. vector가 없거나 유효하지 않으면 전체 build를 실패시키며 설정된 index를
lexical-only로 자동 강등하지 않는다.

Build configuration의 필수 문자열과 `HYBRID`의 provider·model·model version은 비어 있으면 안 된다.
ANN configuration은 비어 있지 않아야 하며 key는 중복될 수 없고 key와 value 모두 canonical payload로
직렬화 가능한 유효 값이어야 한다. enum 자리는 문자열 유사값이 아니라 선언된 enum instance만
허용한다. `display_limit=1` 고정 규칙과 별개로 도달할 수 없는 중복 범위 검사는 두지 않는다.

embedding 구현은 좁은 protocol로 주입한다. Production model 선택, model download, network access,
batching과 PostgreSQL 저장은 이 Issue의 범위가 아니다.

## 결정적 build

builder는 다음 순서로 처리한다.

1. Catalog envelope, 선언 count, 승인 상태, freshness, completeness와 duplicate·orphan·conflict
   count가 모두 0인지 검증한다.
2. Product identity, publication, component, alias와 Search Entry 사이의 참조 무결성을 검증한다.
3. inactive 또는 pending Search Entry를 제외한다. active·approved Candidate entry가 비활성 Product나
   유효하지 않은 Alias를 참조하면 조용히 부분 제외하지 않고 오류로 처리한다.
4. 구성원을 생성하고 stable member key conflict를 거부한다.
5. 명시적으로 버전이 고정된 UTF-8 byte-order key로 구성원을 정렬한다.
6. configuration이 `HYBRID`이면 embedding을 생성하고 검증한다.
7. canonical 구성원 hash, 정렬된 member-set hash, configuration hash와 최종 content hash를
   SHA-256으로 계산한다.
8. 완전한 `CandidateIndexBuildSuccess` 하나 또는 타입이 지정된 `CandidateIndexBuildFailure` 하나만
   반환한다. failure에는 partial 구성원이나 manifest가 없다.

Canonical payload는 UTF-8, Unicode NFC, compact sorted-key JSON, 명시적 null, 유한 JSON number와
소문자 64자리 SHA-256을 사용한다. 승인된 RAG-06 export 계약이 안정적인 의미 필드로 지정하지 않는
한 DB ID, 입력 순서, timestamp, object key와 process-local value는 결정적 hash에서 제외한다.

현재 RAG-07A는 `catalog_manifest_hash`가 소문자 64자리 SHA-256 형식인지 검증하지만 RAG-06 export
전체를 재계산할 정본 envelope가 아직 없으므로 값 자체를 재계산하지 않는다. RAG-06은 RAG-07A와
동일한 UTF-8·NFC·compact sorted-key canonicalization과 명시적 의미 필드 목록으로 manifest hash를
발행해야 하며, 그 계약이 확정되면 RAG-07A 또는 RAG-07B 통합 경계에서 exact recomputation을 추가한다.

## Manifest 계약

`CandidateIndexManifest`는 다음 값을 포함한다.

- index code/version과 build mode
- Catalog version과 Catalog manifest hash
- `(snapshot_id, source_version)`이 결속된 정렬 `source_refs`
- schema 및 normalization version
- lexical, 검색 순서, embedding, distance와 ANN configuration provenance
- 구성원 count, Product Identity count, Product-name count, approved-alias count와 vector count
- member-set hash, configuration hash와 최종 content hash
- 명시적인 Candidate index kind인 `MEDICATION_CANDIDATE`

manifest에는 Evidence index version을 포함하지 않으며 index kind로 `KNOWLEDGE_EVIDENCE`를 허용하지
않는다. RAG-07B는 manifest와 구성원의 의미 hash를 바꾸지 않고 저장해야 한다.

## 실패 계약

Build failure는 다음 closed enum만 사용한다.

- `CATALOG_NOT_APPROVED`
- `CATALOG_STALE`
- `CATALOG_PARTIAL`
- `CATALOG_MANIFEST_INVALID`
- `CATALOG_COUNT_MISMATCH`
- `CATALOG_TEXT_NOT_NFC`
- `CATALOG_REQUIRED_FIELD_INVALID`
- `CATALOG_SOURCE_BINDING_INVALID`
- `DUPLICATE_PRODUCT_IDENTITY`
- `REFERENTIAL_INTEGRITY_INVALID`
- `ALIAS_CONFLICT`
- `MEMBER_CONFLICT`
- `BUILD_CONFIG_INVALID`
- `EMBEDDING_OUTPUT_INVALID`

failure는 안정적인 reason code와 안전한 구조 정보만 노출한다. Source raw row, OCR 문자열, 처방 문자열,
vector, credential 또는 provider 원문 오류를 포함하면 안 된다. Alias·member conflict detail도
`normalized_alias`, 표시 문자열 또는 검색 문자열 대신 record reference와 안정적인 필드 경로만
사용한다.

## 검색 계약

`CandidateSearchQuery`는 정확히 active Candidate Index version, 비어 있지 않은 normalized query와
retrieval limit만 포함한다. RAG-07A는 strength compatibility를 판단하거나 query를 변경하지 않는다.
Raw OCR, 미확정 structured output, nullable strength metadata와 환자 DTO 필드는 입력받지 않는다.

`CandidateIndexSearchPort`는 물리 검색 단계별 method를 제공한다.

1. Product-name Exact
2. 승인 Product Alias Exact
3. Trigram·edit-distance Candidate recall
4. OCR 전용 dense vector Candidate recall

선택적 Identifier Exact 단계는 승인된 Source·Catalog Receipt와 RAG-07B 물리 계약이 확보될 때까지
활성화하지 않는다. Ingredient Exact는 진단용 Resolver 분기이며 Product Candidate 단계로 자동
취급하지 않는다.

`search_candidate_index(...)`는 고정된 순서로 단계를 호출한다. dense 검색은 active manifest가
`HYBRID`일 때만 마지막 보조 recall 단계로 호출한다. 모든 hit가 요청된 active index version을
참조하고 Product Identity, member key, stage, rank, stage score, Catalog version, Source Snapshot
provenance, normalization version과 해당 시 embedding version을 포함하는지 검증한다.

Search port가 `None`, tuple이 아닌 container, 선언된 raw-hit 타입이 아닌 원소 또는 유효하지 않은
반환 구조를 주면 예외를 외부로 전파하지 않고 타입이 지정된 search failure로 닫는다. embedding
port의 잘못된 container나 원소는 `EMBEDDING_OUTPUT_INVALID`, search port 호출 또는 반환 구조의
실패는 `PORT_FAILURE`를 사용한다. 이미 타입이 지정된 hit의 index·Catalog·Source provenance가
manifest와 다르면 `HIT_PROVENANCE_MISMATCH`다.

출력은 여러 단계에서 같은 Product Identity가 반복되더라도 raw stage hit를 보존한다. RAG-07B와 후속
Resolver가 감사와 fusion을 위해 개별 Exact·Alias·Trigram·Vector signal을 필요로 하기 때문이다.
RAG-07A는 grouped 또는 deduplicated 검색 결과 view, 최종 Resolver score, RRF, attribute
compatibility, `SINGLE_CANDIDATE` 판정, Candidate Search row 생성 또는 `MATCHED` 저장을 제공하지
않는다.

`retrieval_limit`은 각 검색 단계가 반환할 수 있는 raw hit의 상한이다. RAG-07A는 단계 signal을
보존하므로 전체 raw hit 수는 `검색 단계 수 × retrieval_limit`까지 가능하다. RAG-07B와 Resolver는
이 합계를 `candidate_limit` 이하로 가정하지 말고, 별도의 fusion·공식 Identity dedupe 뒤 외부 노출
한도를 적용해야 한다.

score, rank, distance, limit, hash와 raw hit는 내부 정보이며 이 모듈에서 환자 응답으로 투영하면 안
된다.

## RAG-07B 인계

RAG-07B는 불변 build success, manifest, 구성원, query, search port와 raw hit 타입을 소비한다.
다음 항목은 RAG-07B가 소유한다.

- PostgreSQL table, migration, FK, check와 partial index
- `btree`, `GIN ... gin_trgm_ops`, pgvector와 version별 HNSW configuration
- build transaction, lock, idempotent rebuild와 rollback
- `BUILDING | READY | RETIRED | FAILED` persistence status
- active Runtime Bundle 선택과 active pointer
- DB integration test

RAG-07B는 실패한 RAG-07A build를 partial success로 해석하거나 manifest hash를 변경하거나 Candidate
vector와 Evidence vector를 혼합하거나 내부 hit metadata를 환자 DTO로 반환하면 안 된다.
또한 raw hit의 member key와 Entry Source Snapshot이 실제 active index 구성원에 존재하는지 저장소
경계에서 검증해야 한다. RAG-07A의 순수 search port는 DB 구성원 집합을 소유하지 않으므로 문자열
형식 검사만으로 실제 membership을 증명했다고 간주하지 않는다.

## 테스트 전략

단위 테스트는 test-first red-green-refactor cycle을 따르며 다음을 검증한다.

- 순서가 다른 동일 입력은 같은 구성원과 hash를 생성한다.
- 완전히 동일한 Search Entry 반복은 결정적으로 하나로 합쳐진다.
- 동일 stable member key가 다른 content를 가지면 전체 build가 실패한다.
- 이름이 같은 서로 다른 공식 Product Identity는 분리해서 유지한다.
- 중복 공식 Product Identity 정의는 fail-closed한다.
- Product 및 승인 Product Alias entry는 허용한다.
- Ingredient Alias, 미승인 Alias, 비활성 Alias와 HIRA 파생값은 Product Candidate 구성원이 되지 않는다.
- orphan Component, Alias와 Search Entry reference는 fail-closed한다.
- partial, stale, unapproved, count mismatch 또는 invalid-hash Catalog는 구성원을 반환하지 않는다.
- 비어 있는 Catalog·Source·Identity·reference·필수 문자열과 구성원 0건 결과는 성공하지 않는다.
- Source Snapshot과 Version의 결속 변경은 다른 manifest/content hash를 만들며 중복·충돌은 실패한다.
- Catalog와 build config의 문자열이 NFC가 아니면 자동 변환 없이 구성원을 반환하지 않는다.
- Product-name Search Entry 문자열이 Product row와 다르면 참조 무결성 오류로 실패한다.
- Product-name Entry와 Product, Alias Entry와 Alias의 Source Snapshot 결속을 각각 검증하고, 서로 다른
  승인 Product·Alias Snapshot은 같은 Catalog export 안에서 보존한다.
- active·approved Entry가 비활성 Product 또는 유효하지 않은 Alias를 참조하면 부분 제외하지 않는다.
- lexical-only와 hybrid configuration의 nullability 규칙을 검증한다.
- 누락, non-finite, 잘못된 count·순서·dimension의 embedding은 구성원을 반환하지 않는다.
- manifest count와 모든 SHA-256 값을 정확히 재현한다.
- 검색 단계 호출 순서와 dense 단계가 마지막에만 호출되는 동작을 검증한다.
- retrieval limit은 양수이고 설정된 candidate limit 이하이며 각 단계에 동일하게 전달된다.
- hit의 index·Catalog·Source·model provenance mismatch는 fail-closed한다.
- embedding/search port의 `None`, 잘못된 container와 원소 타입은 예외 없이 typed failure가 된다.
- conflict failure detail에 Alias나 Candidate 원문이 포함되지 않는다.
- 반복 Product raw hit가 단계별 signal을 각각 보존한다.
- production import가 Backend setting, SQLAlchemy, PostgreSQL, 외부 model download 또는 환자 DTO
  module을 요구하지 않는다.

Issue 단위 검증 명령은 다음과 같다.

```bash
uv run pytest ai_worker/tests/rag -q
uv run ruff check ai_worker/tasks/rag ai_worker/tests/rag
uv run ruff format ai_worker/tasks/rag ai_worker/tests/rag --check
uv run mypy ai_worker/tasks/rag
```

`uv run pytest tests/integration/rag/test_candidate_index_query.py -q`는 RAG-06 export와 RAG-07B
PostgreSQL 경계가 만들어질 때까지 `BLOCKED_BY_RAG_04_OR_06`이다. 존재하지 않는 테스트를 통과로
보고하지 않고 미실행으로 기록한다.

## 병렬 작업과 통합 안전

PR #260은 Issue #167 구현과 함께 사용자가 같은 브랜치에 통합하도록 지정한 한국어 설계 문서 이관을
포함한다. 이관 문서는 Issue #214, #216, #231, #241의 승인된 설계·계획과 경로 검증이며 Evaluation
runtime 코드, schema, 고정 Dataset 내용은 변경하지 않는다. 따라서 Pull Request 본문은 Issue #167만
완료한다고 축약하지 않고 관련 Issue, 문서 이관 범위와 현재 검증 수치를 정확히 기재한다.

리뷰 요청은 실제 변경 영역을 따른다. Candidate Index와 MFDS Catalog 인계는 `@Jye-rookie`,
Backend·Source·loader/provider 경계는 `@phina-io`, Product·Safety·Evaluation provenance는
`@hazelnutflavoured`의 검토 범위로 명시한다. 문서 이관을 별도 PR로 이미 올렸다는 이유로 이 브랜치의
실제 diff와 교차 영역 리뷰를 숨기거나 생략하지 않는다.

매 commit 전에 현재 branch, worktree status와 전체 diff를 확인한다. 병렬 변경을 보존하고 이
Issue의 일부로 prunable worktree metadata를 제거하지 않는다.

## 제외 범위

- MFDS endpoint 호출, parsing, Source Snapshot 생성 또는 Catalog persistence
- RAG-06 Product·Ingredient·Alias·Component 구현
- migration, repository, build lock, transaction 또는 active pointer
- Python에서 PostgreSQL similarity 계산 또는 ANN 실행
- Resolver RRF, attribute compatibility, ambiguity policy 또는 Single Candidate Gate
- Candidate Search·Result·Identification DB row 또는 API route
- 환자 공개 DTO, `READY`, `MATCHED` 또는 사용자 확인 동작
- 의료 Knowledge·Evidence indexing, retrieval 또는 Citation
- HIRA ingestion 또는 보험코드 활성화
- Production 활성화, 외부 Source 승인 또는 `PUBLIC_TRACK_F` 변경
