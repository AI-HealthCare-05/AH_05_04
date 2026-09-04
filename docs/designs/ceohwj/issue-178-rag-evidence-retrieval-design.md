# Issue #178 RAG Evidence Retrieval 단위 설계

## 상태

- Issue: `#178`
- 브랜치: `feat/178-rag-evidence-retrieval`
- 범위: 의료 Knowledge·Rule Evidence Retrieval의 순수 계약, orchestration과 합성 Evidence Gate
- 구현 담당자: 정현우 (`@ceohwj`)
- 담당 리뷰어: 권가빈 (`@hazelnutflavoured`) — Evidence·Scope·Safety
- DB·Source 리뷰어: 송은영 (`@phina-io`), 김지혜 (`@Jye-rookie`)
- 공개 게이트: `PUBLIC_TRACK_F=false`

## 정본과 착수 상태

이 설계는 저장소의 Approved Target인 다음 문서를 따른다.

- `docs/contracts/targets/post-mvp-1/rag-runtime-v1.md`
- `docs/contracts/targets/post-mvp-1/rag-source-ingestion-v1.md`
- `docs/contracts/targets/post-mvp-1/rag-evaluation-v1.md`
- `docs/contracts/targets/post-mvp-1/safety-result-v2.md`
- `docs/validation/rag/rag-source-governance-contract-receipt.md`

RAG Source Governance의 합성 계약은 검증됐지만 실제 Source Snapshot·Catalog readiness는
`BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`다. `#177` Rule Evidence와 `#158` Retrieval Metric도 아직
통합 가능한 실행 산출물을 제공하지 않는다. 따라서 이번 변경은 외부 Source, PostgreSQL, Runtime
Bundle, LangGraph, Answer Composer 또는 공개 Citation에 연결하지 않는다.

`#167/#168`은 OCR 의약품 Candidate Index 계약·저장 구현이다. 이번 의료 Evidence Retrieval은 해당
Candidate Index의 구성원, vector, score, 검색 포트를 입력으로 사용하지 않는다. 두 인덱스는 version과
물리 경계를 공유하지 않는다.

## 문제

현재 저장소에는 승인된 Source Snapshot의 Knowledge Chunk와 Rule Evidence만 검색하고, 실제 선택한
구성원의 provenance를 검증하며, 불충분·상충·만료·locator 오류를 fail-closed하는 실행 경계가 없다.
기존 `knowledge_document`와 `knowledge_chunk` 모델은 schema-only 골격이며 Source approval, Snapshot,
locator, Evidence Index version 또는 Retrieval Run을 표현하지 못한다.

검색·rerank 공식과 DB schema가 확정되기 전에 이를 추정해 구현하면 Candidate Index와 Evidence Index를
혼용하거나, 재현할 수 없는 score를 저장하거나, 승인되지 않은 Evidence를 Answer Composer로 전달할 수
있다.

## 목표

1. 의료 Evidence 전용 입력·출력·실패 타입을 Candidate Index와 분리한다.
2. transient normalized query를 검색 포트에만 전달하고 저장 가능한 결과에는 versioned query
   fingerprint만 남긴다.
3. lexical·dense 검색과 rerank를 versioned port로 실행하고 실제 선택 구성원의 stage rank·score,
   rerank rank·score와 provenance를 보존한다.
4. approval, active Bundle membership, prescription/current scope, Source freshness, locator와 conflict
   관측값을 검증하고 fail-closed 상태를 반환한다.
5. 같은 입력, 포트 출력과 configuration에서 같은 정렬 결과와 Retrieval Run projection을 만든다.
6. DB·Provider 없이 비식별 합성 fixture로 단위 검증할 수 있게 한다.

## 제외 범위

- PostgreSQL `pg_trgm`, pgvector query와 migration
- Knowledge Document parsing, chunking, embedding build와 Evidence Index persistence
- 실제 embedding provider 또는 model download
- RAG-13 Rule 생성·저장·판정
- LangGraph node 연결, Answer composition과 공개 Citation DTO
- semantic NLI, cross-encoder 또는 고급 reranker
- Production sufficiency threshold와 Release 판정
- raw 질문, Source 원문 또는 폐기 답변 저장

## 검토한 접근

### 선택: 순수 orchestration + versioned search/rerank port

`ai_worker/tasks/rag/evidence_retrieval.py` 하나에 불변 타입, 입력 검증, 검색 단계 orchestration,
선택 provenance 검증, Evidence Gate와 sanitized Retrieval Run projection을 둔다. lexical·dense 검색과
rerank 계산은 각각 Protocol 뒤에 두며 합성 adapter로 테스트한다.

이 접근은 승인되지 않은 DB 쿼리와 score fusion 공식을 추정하지 않으면서 #178의 단위 구현 경계를
검증할 수 있다. 후속 adapter는 공개 타입을 소비하되 순수 모듈이 PostgreSQL, SQLAlchemy,
sentence-transformers 또는 Backend model을 import하지 않게 한다.

### 미선택: Python에서 pg_trgm·dense score 모사

PostgreSQL extension 설정, tokenizer, vector distance와 정렬 동작이 확정되지 않은 상태에서 Python으로
유사 구현하면 실제 adapter와 다른 score 및 tie-break 결과를 만든다. 이번 범위에서는 채택하지 않는다.

### 미선택: 기존 Candidate Index 계약 확장

Candidate Index는 제품 식별 후보를 반환하며 의료 Claim 근거가 아니다. 해당 타입을 확장하면 물리 경계와
Citation 금지 조건을 약화하므로 채택하지 않는다.

## 모듈 경계

### 구현 모듈

`ai_worker/tasks/rag/evidence_retrieval.py`는 다음 책임만 가진다.

- Evidence 종류, 검색 stage, 상태, reason code와 불변 입출력 타입
- `EvidenceSearchPort`, `EvidenceRerankPort` Protocol
- `retrieve_evidence(...)` orchestration
- 검색·rerank 결과의 version, rank, score와 provenance 검증
- Evidence Gate와 저장 가능한 Retrieval Run projection 생성

첫 단위 구현은 기존 `candidate_index.py`와 같은 단일 import surface를 사용한다. DB adapter가 추가되는
후속 변경에서 내부 파일을 분리하더라도 공개 이름은 이 모듈에서 다시 export한다.

### 테스트 모듈

`ai_worker/tests/rag/test_evidence_retrieval.py`는 deterministic fake search/rerank port와 비식별 합성
Knowledge·Rule Evidence를 사용한다. 테스트는 PostgreSQL, 네트워크, 외부 model, secret 또는 환자정보를
요구하지 않는다.

## 입력 계약

### `EvidenceRetrievalRequest`

실행 입력은 다음 값을 가진다.

- `normalized_query`: 검색 포트에만 전달되는 NFC transient 문자열
- `query_fingerprint`: `algorithm`, `key_version`, 소문자 64자리 digest의 결속값
- `prescription_version_id`
- `question_type`
- 비어 있지 않고 중복 없는 정렬 `scope_codes`
- `runtime_release_bundle_id`, `bundle_manifest_hash`
- `evidence_index_version`

`normalized_query`와 query fingerprint의 cryptographic 일치는 이 순수 모듈에서 검증하지 않는다. HMAC
secret을 소유한 상위 intake 경계가 fingerprint를 생성하고 두 값의 결속을 보증해야 한다. 순수 모듈은
원문 질문이나 query를 hash 또는 HMAC으로 가장하지 않는다.

공백 query, NFD 문자열, 비정렬·중복 scope, 잘못된 SHA-256 형식, 빈 version 또는 enum 유사 문자열은
검색 포트를 호출하기 전에 `REQUEST_INVALID`로 실패한다. 실패 detail은 원문 값이 아니라 안정적인 필드
경로만 포함한다.

### `EvidenceRetrievalConfig`

설정은 다음 version과 제한을 결속한다.

- `retrieval_config_version`, `retrieval_config_hash`
- `lexical_config_version`
- nullable `dense_config_version`
- `rerank_config_version`, `rerank_config_hash`
- `gate_policy_version`, `gate_policy_hash`
- `candidate_limit`, `selection_limit`
- 합성 또는 승인 policy가 제공하는 `minimum_evidence_count`
- 허용 `EvidenceKind` 집합

이번 변경은 score fusion algorithm, embedding model, distance metric 또는 threshold 값을 자체적으로
정하지 않는다. 검색·rerank adapter가 받은 config version/hash에 맞는 구현을 선택한다. 모든 limit과
minimum count는 양수이고 `selection_limit <= candidate_limit`이어야 한다. Dense stage를 사용하지 않는
구성은 `dense_config_version=null`로 명시한다.

## Evidence와 검색 결과 계약

### Evidence 종류

허용 종류는 다음 둘뿐이다.

- `KNOWLEDGE_CHUNK`
- `RULE_EVIDENCE`

Candidate product, Candidate index member, OCR raw value, 미검수 LLM output, HIRA row 또는 자유 문자열
source type은 허용하지 않는다.

### `EvidenceProvenance`

각 Evidence는 다음 값을 함께 가진다.

- `evidence_id`, `evidence_kind`
- `evidence_index_version`
- `source_snapshot_id`, `source_version`
- `source_manifest_member_hash`
- `locator`
- `bundle_member_id`
- `approval_version`
- `freshness_policy_version`

문자열 식별자와 locator는 비어 있지 않고 NFC여야 하며 hash는 소문자 64자리 SHA-256이어야 한다.
Evidence Index version은 request와 exact-match해야 한다.

### 검색 stage와 hit

검색 stage는 `LEXICAL`과 `DENSE`다. 각 `EvidenceSearchHit`는 stage 안에서 1부터 시작하는 중복 없는
연속 rank, finite score와 전체 provenance를 가진다. 한 stage에서 `candidate_limit`보다 많은 hit를
반환하거나, 같은 Evidence를 중복 반환하거나, request와 다른 index version을 반환하면
`SEARCH_RESULT_INVALID`로 실패한다.

Dense stage 사용 여부는 config가 결정한다. Dense가 비활성일 때 dense port를 호출하지 않는다.
검색 port의 typed failure는 원문 exception이나 query를 노출하지 않고 `DEPENDENCY_ERROR` 상태와 안전한
reason code로 변환한다.

### rerank와 최종 선택

`EvidenceRerankPort`는 lexical·dense raw hit와 config를 받아 `EvidenceRerankedHit`을 반환한다. 순수
orchestration은 fusion 공식을 구현하지 않지만 다음 불변식을 검증한다.

- rerank 결과는 raw hit에 존재한 `evidence_id`만 참조한다.
- 선택 결과의 provenance는 raw hit와 exact-match한다.
- rerank rank는 1부터 시작하는 중복 없는 연속 정수다.
- rerank score는 finite number다.
- 같은 Evidence는 한 번만 선택된다.
- 결과 수는 `selection_limit` 이하이다.

동점 정렬 의미는 rerank config 소유이며 adapter가 결정적 순서를 반환해야 한다. orchestration은 받은
순서를 다시 score로 재정렬하지 않고 rank 일관성만 검증한다.

## Evidence Gate

각 reranked Evidence에는 상위 Source/Bundle 검증 경계가 계산한 다음 boolean 관측값을 결속한다.

- `source_approved`
- `source_active`
- `bundle_member_matches`
- `scope_allowed`
- `freshness_current`
- `locator_valid`
- `conflict_detected`

이 값은 Production Source Guard를 대체하지 않는다. 이번 단위 구현에서는 전달된 합성 관측값을
fail-closed 규칙에 적용하는 참조 경계만 제공한다.

Gate 판정 우선순위는 다음과 같다.

1. search 또는 rerank port의 typed failure: `DEPENDENCY_ERROR`
2. 결과 구조·provenance 위반: `DEPENDENCY_ERROR`
3. 선택 Evidence 중 freshness 실패: `STALE`
4. 선택 Evidence 중 conflict 관측: `CONFLICTED`
5. approval, active, bundle, scope 또는 locator 실패: `INSUFFICIENT`
6. 적격 선택 수가 `minimum_evidence_count` 미만: `INSUFFICIENT`
7. 모든 조건 충족: `SUFFICIENT`

`SUFFICIENT`만 Answer Composer가 소비할 수 있는 `SelectedEvidenceSet`을 가진다. 나머지 상태는 선택
Evidence를 공개 입력으로 반환하지 않고, 내부 진단용으로 식별자·reason code만 포함하는 sanitized
Retrieval Run을 만든다. 이 설계는 fallback 문구나 composer 호출 자체를 구현하지 않는다.

## Retrieval Run projection

`RetrievalRunRecord`에는 다음만 포함한다.

- query fingerprint; `normalized_query` 제외
- prescription version, question type, scope code
- Bundle ID와 manifest hash
- Retrieval, lexical, dense, rerank, gate policy version/hash
- Evidence Index version
- 각 raw hit의 Evidence ID, kind, stage, rank, score와 Source/locator provenance
- rerank rank·score와 최종 선택 여부
- 최종 상태와 안전한 reason code

dataclass를 recursive serialization했을 때에도 `normalized_query`, Source 원문, Evidence text, 환자 질문,
credential 또는 생성 답변 필드가 존재하지 않아야 한다. 일반 로그, Stream, DLQ와 quarantine projection은
이 record보다 좁은 별도 계약을 사용해야 하며 이번 범위에서는 구현하지 않는다.

## 결정성과 오류 처리

- 입력 tuple의 순서는 계약상 정렬된 값만 허용하거나 canonical UTF-8 byte 순으로 고정한다.
- hash는 이미 생성된 versioned digest를 검증하며 secret을 소유하지 않는다.
- 예상 가능한 adapter 실패는 typed result로 반환한다.
- 프로그래밍 오류를 성공 또는 `INSUFFICIENT`로 강등하지 않는다.
- 오류 detail에는 query, Evidence text, URL 전체 또는 Source 원문을 포함하지 않는다.
- Candidate Index 타입은 duck typing으로 수용하지 않고 Evidence 전용 dataclass·enum instance를
  런타임에 검증한다.

## 테스트 기준

최소 단위 테스트는 다음 동작을 고정한다.

- lexical-only 정상 실행과 `SUFFICIENT`
- lexical+dense hit를 rerank port에 모두 전달
- 동일 입력·동일 port 출력의 동일 Retrieval Run
- raw query가 Retrieval Run recursive projection에 없음
- Candidate Index hit 또는 허용되지 않은 Evidence kind 거부
- request/config의 빈 값, NFD, hash·enum·limit 오류가 port 호출 전 실패
- stage별 rank 중복·누락, limit 초과, NaN·infinite score 거부
- request와 다른 Evidence Index version·provenance 거부
- rerank가 알 수 없는 hit, provenance 변경, 중복·비연속 rank를 반환하면 실패
- Source 미승인·비활성, Bundle·scope·locator 불일치는 `INSUFFICIENT`
- freshness 실패는 `STALE`
- conflict 관측은 `CONFLICTED`
- 적격 Evidence 수 부족은 `INSUFFICIENT`
- search/rerank typed failure는 query 비노출 `DEPENDENCY_ERROR`
- 실패 상태에 composer용 `SelectedEvidenceSet`이 없음

검증 명령은 다음과 같다.

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_retrieval.py -q
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag -q
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run ruff check ai_worker/tasks/rag ai_worker/tests/rag
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run mypy ai_worker/tasks/rag
git diff --check
```

## 후속 통합 조건

이번 단위 구현은 다음이 연결되기 전까지 통합 완료나 Issue Close 증빙이 아니다.

- Knowledge/Evidence Index manifest·member·persistence 계약과 migration
- `#165/#166` 실제 Source Snapshot·Catalog Receipt
- `#177` Rule Evidence 실행 인터페이스
- Full Execution Context·Runtime Bundle Guard
- query fingerprint 생성·key rotation을 소유하는 Privacy 승인 경계
- PostgreSQL lexical/dense adapter와 configuration Receipt
- `#158` Dataset/Index version이 결속된 Recall@5 Artifact

현재 단위 구현의 차단 코드는 `BLOCKED_BY_KNOWLEDGE_EVIDENCE_INDEX_CONTRACT`다. 실제 Source readiness는
별도로 `BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`를 유지한다. 두 차단 상태를 Candidate Index
`#167/#168` 완료로 해소하지 않는다.

## 완료 주장 경계

이 변경이 검증할 수 있는 주장은 “합성 Evidence와 versioned port output에 대해 Retrieval orchestration과
Evidence Gate가 결정적이고 fail-closed한다”까지다. 실제 `pg_trgm`·dense 품질, Source 승인,
Recall@5, Citation 정확성, Runtime Bundle 활성화 또는 환자 공개 안전성을 완료로 주장하지 않는다.
