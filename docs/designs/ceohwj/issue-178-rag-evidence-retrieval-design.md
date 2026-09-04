# Issue #178 RAG Evidence Retrieval Kernel 단위 설계

## 상태

- Issue: `#178`
- 브랜치: `feat/178-rag-evidence-retrieval`
- 범위: Knowledge Evidence Retrieval의 순수 kernel과 합성 검증
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
`BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`다. Full Execution Context·Runtime Bundle Guard, Knowledge
Evidence Index, Retrieval Run persistence와 Safety Result v2도 Current Runtime으로 구현되지 않았다.

`#167/#168`은 OCR 의약품 Candidate Index 계약·저장 구현이다. 이번 의료 Evidence Retrieval은 해당
Candidate Index의 구성원, vector, score 또는 검색 포트를 입력으로 사용하지 않는다. Candidate Index와
Evidence Index는 version과 물리 경계를 공유하지 않는다.

## 문제

현재 저장소에는 Knowledge Evidence를 대상으로 lexical·dense 검색과 rerank를 순서대로 호출하고,
각 adapter가 실제 적용한 version/hash를 확인하며, rank·score·content·provenance 무결성을 결정적으로
검증하는 순수 실행 경계가 없다.

반면 Source Guard binding, Safety 상태, Composer handoff와 Retrieval Run schema는 여러 도메인이 함께
소비하는 공유 계약이며 아직 확정되지 않았다. 이를 합성 boolean이나 로컬 dataclass로 대신하면
권위 없는 단위 테스트가 승인·공개 가능성을 증명하는 것처럼 오해될 수 있다.

## 이번 변경의 목표

1. Candidate Index와 분리된 `KNOWLEDGE_CHUNK` 전용 Retrieval Kernel을 제공한다.
2. query binding 검증, lexical·dense 검색과 rerank를 Protocol 뒤에서 실행한다.
3. 각 port가 실제 적용한 Index·filter·configuration Receipt를 입력 요청과 exact-match하고, 실제
   adapter artifact reference는 비권위적 진단 정보로 기록한다.
4. 모든 raw hit와 rerank selection의 rank, score, content hash와 provenance 무결성을 검증한다.
5. 같은 입력과 같은 port output에서 동일한 비권위적 diagnostic trace를 만든다.
6. DB·Provider 없이 비식별 합성 fixture로 단위 테스트한다.

## 제외 범위

- PostgreSQL `pg_trgm`, pgvector query, migration과 repository
- Knowledge Document parsing, chunking, embedding build와 Evidence Index persistence
- 실제 embedding provider 또는 model download
- Source 승인, Runtime Bundle membership, Guard Decision과 operation selection 판정
- RAG-13 interaction rule lookup과 `RULE_EVIDENCE`
- Evidence sufficiency, conflict, freshness와 locator 정책 판정
- Safety Result v2 상태·fallback·release decision 매핑
- Composer용 Evidence Set, Answer composition과 공개 Citation DTO
- Retrieval Run persistence schema
- semantic NLI, cross-encoder 또는 고급 reranker
- Production threshold와 Release 판정

## 검토한 접근

### 선택: 비권위적 Retrieval Kernel

`ai_worker/tasks/rag/evidence_retrieval.py`에 불변 kernel 타입, 입력 검증, search/rerank orchestration,
Receipt exact-match, hit 무결성 검사와 diagnostic trace를 구현한다. 실제 search, query HMAC 검증과
rerank 계산은 Protocol 뒤에 둔다.

이 kernel의 성공은 검색 실행과 결과 구조가 요청된 불변 artifact에 결속됐다는 뜻일 뿐이다. Source가
승인됐거나 Evidence가 충분하거나 Composer가 사용할 수 있다는 뜻이 아니다. Kernel output은 권위 있는
Source Guard와 Evidence Gate가 추가로 검증하기 전에는 생성 답변 입력으로 사용할 수 없다.

### 미선택: 축약 Source Guard boolean을 kernel에 포함

`source_approved`, `freshness_current` 같은 boolean은 목적·환경별 Approval, revocation, governance
revision, safety epoch, Bundle target/selection manifest와 scope hash를 증명하지 못한다. 기존
`source_governance.py`보다 약한 병렬 안전 경계를 만들기 때문에 채택하지 않는다.

### 미선택: 기존 Candidate Index 계약 확장

Candidate Index는 제품 식별 후보를 반환하며 의료 Claim 근거가 아니다. 해당 타입을 확장하면 물리 경계와
Citation 금지 조건을 약화하므로 채택하지 않는다.

### 미선택: PostgreSQL score를 Python으로 모사

extension 설정, tokenizer, vector distance와 tie-break가 확정되지 않은 상태에서 Python으로 모사하면
실제 adapter와 다른 결과를 만든다. Kernel은 score 계산이 아니라 port output 검증만 소유한다.

## 모듈 경계

### 구현 모듈

`ai_worker/tasks/rag/evidence_retrieval.py`는 다음 책임만 가진다.

- Kernel 실행 상태, 검색 stage, reason code와 불변 입출력 타입
- `QueryBindingVerifierPort`, `EvidenceSearchPort`, `EvidenceRerankPort` Protocol
- `retrieve_knowledge_evidence(...)` orchestration
- 실제 적용 Receipt의 exact-match
- raw hit와 rerank output의 구조·provenance·content 무결성 검증
- 비민감 `EvidenceRetrievalDiagnosticTrace` 생성

이 모듈은 PostgreSQL, SQLAlchemy, Backend model, sentence-transformers, Source Guard, Safety Result 또는
Composer를 import하지 않는다.

모든 공개 타입은 이 단위 구현을 위한 내부 provisional API다. Knowledge Evidence Index와 Privacy 계약이
승인되기 전에는 Production adapter 또는 다른 도메인의 안정 import contract로 승격하지 않는다.

### 테스트 모듈

`ai_worker/tests/rag/test_evidence_retrieval.py`는 deterministic fake port와 비식별 합성 Knowledge
Evidence를 사용한다. 테스트는 PostgreSQL, 네트워크, 외부 model, secret 또는 환자정보를 요구하지 않는다.

## 공통 불변 참조

`ImmutableArtifactRef`는 다음 필드를 하나의 값으로 결속한다.

- `artifact_code`
- `version`
- 소문자 64자리 `content_sha256`

세 필드는 비어 있지 않고 NFC여야 한다. hash는 정본 artifact가 외부에서 계산한 값이며 kernel이 artifact
내용 없이 재계산하지 않는다. 대신 각 port Receipt가 같은 reference를 반환했는지 exact-match한다.

## 입력 계약

### 민감 문자열 wrapper

`SensitiveText`는 normalized query와 Evidence content를 메모리 안에서 전달하기 위한 non-dataclass
wrapper다. 내부 원문은 명시적인 `reveal()` 호출로만 읽고 `repr()`과 `str()`은 고정된 `<redacted>`를
반환한다. 기본 JSON encoder로 직렬화할 수 없으며 generic dataclass recursive projection에서도 원문
문자열로 풀리지 않는다. Kernel은 query와 content를 일반 `str` 필드로 보존하지 않는다.

### `EvidenceRetrievalKernelRequest`

실행 입력은 다음 값을 가진다.

- `normalized_query: SensitiveText`: port에만 전달되는 비어 있지 않은 NFC transient 문자열
- `query_fingerprint`: `algorithm`, `key_version`, 소문자 64자리 digest
- `filter_snapshot_ref: ImmutableArtifactRef`
- `evidence_index_ref: ImmutableArtifactRef`
- `retrieval_config_ref: ImmutableArtifactRef`
- `lexical_config_ref: ImmutableArtifactRef`
- nullable `dense_config_ref: ImmutableArtifactRef`
- `rerank_config_ref: ImmutableArtifactRef`
- `lexical_limit`, `dense_limit`, `selection_limit`

`runtime_release_bundle_id` 또는 `bundle_id`는 포함하지 않는다. 두 이름은 승인된 shared DTO Receipt 전까지
미확정이며, Kernel request는 wire DTO나 Runtime Bundle contract가 아니다.

`lexical_limit`과 `selection_limit`은 양수이고 `dense_limit`은 0 이상이며,
`selection_limit <= lexical_limit + dense_limit`이어야 한다. Dense stage가 비활성인
경우 `dense_config_ref=null`이고 `dense_limit=0`이어야 한다. 활성인 경우 reference가 존재하고
`dense_limit > 0`이어야 한다. lexical limit은 stage별 상한이며 dense limit은 별도 stage별 상한이다.

### Query binding 검증

Kernel은 query와 fingerprint를 독립적으로 신뢰하지 않는다. 검색 전에
`QueryBindingVerifierPort.verify(normalized_query, query_fingerprint)`를 호출한다. 성공 Receipt는 요청의
algorithm, key version과 digest를 exact-match해야 한다. Verifier 결과는 다음 세 경로를 구분한다.

- binding 불일치: `VALIDATION_ERROR/QUERY_BINDING_INVALID`
- verifier 또는 key-store typed dependency failure: `DEPENDENCY_ERROR/QUERY_BINDING_DEPENDENCY_ERROR`
- malformed success Receipt 또는 fingerprint mismatch: `DEPENDENCY_ERROR/QUERY_BINDING_RECEIPT_MISMATCH`

세 경로 모두 search port를 호출하지 않는다. Port exception 객체와 message는 typed failure나 trace에
보존하지 않고 안정 diagnostic code로만 변환한다.

Kernel은 HMAC secret, canonical query 변환 또는 key rotation 정책을 소유하지 않는다. Production
verifier가 허용할 algorithm과 retained key version은 Privacy·Security 승인 계약이 소유한다. 합성
verifier는 secret이 아닌 고정 fixture mapping만 사용한다.

## 실행 상태와 reason code

Kernel은 Safety Result v2의 `evidence_status` 또는 `release_decision`을 생성하지 않는다.

`KernelExecutionStatus`는 다음 값만 가진다.

- `SUCCEEDED`
- `VALIDATION_ERROR`
- `DEPENDENCY_ERROR`

`KernelDiagnosticCode`는 다음 값만 가진다.

- `CANDIDATES_RERANKED`
- `NO_HITS`
- `QUERY_BINDING_INVALID`
- `REQUEST_INVALID`
- `QUERY_BINDING_RECEIPT_MISMATCH`
- `QUERY_BINDING_DEPENDENCY_ERROR`
- `SEARCH_DEPENDENCY_ERROR`
- `SEARCH_RECEIPT_MISMATCH`
- `SEARCH_RESULT_INVALID`
- `RERANK_DEPENDENCY_ERROR`
- `RERANK_RECEIPT_MISMATCH`
- `RERANK_RESULT_INVALID`

허용 조합은 다음과 같다.

| 실행 상태 | 허용 diagnostic |
| --- | --- |
| `SUCCEEDED` | `CANDIDATES_RERANKED`, `NO_HITS` |
| `VALIDATION_ERROR` | `QUERY_BINDING_INVALID`, `REQUEST_INVALID` |
| `DEPENDENCY_ERROR` | query binding Receipt·dependency와 나머지 search·rerank 오류 |

`NO_HITS`는 Evidence가 불충분하다는 권위적 판정이 아니다. 후속 Evidence Gate가 자신의 승인 policy와
Safety v2 계약에 따라 `execution_status`와 `evidence_status`를 별도로 결정한다.

## Port 실행 Receipt

### Query Receipt

`QueryBindingVerificationReceipt`는 요청과 같은 query fingerprint와 실제 verifier의
`adapter_artifact_ref`를 반환한다. query 원문이나 normalized query는 Receipt에 포함하지 않는다.
Kernel은 adapter reference의 형식을 검증하고 trace에 기록하지만, 승인된 Runtime Execution Manifest가
없는 이번 slice에서 특정 adapter가 허용됐다고 판정하지 않는다.

### Search Receipt

각 `EvidenceSearchResult`는 다음을 가진다.

- `stage`: `LEXICAL` 또는 `DENSE`
- 요청과 동일한 `query_fingerprint`
- 요청과 동일한 `filter_snapshot_ref`, `evidence_index_ref`, `retrieval_config_ref`
- stage에 해당하는 `stage_config_ref`
- 실제 실행 adapter의 `adapter_artifact_ref`
- `hits`

Kernel은 request의 query, filter, Index와 config reference를 applied Receipt와 exact-match한다. Adapter
artifact는 비어 있지 않은 불변 reference여야 하며 trace에 기록하지만 허용 adapter 판정에는 사용하지
않는다. Dense가 비활성일 때 dense port를 호출하지 않는다. Expected adapter artifact와의 exact-match는
후속 Runtime Bundle·Execution Manifest Guard가 소유한다.

### Rerank Receipt

`EvidenceRerankResult`는 다음을 가진다.

- 요청과 동일한 `query_fingerprint`, `filter_snapshot_ref`, `evidence_index_ref`
- 요청과 동일한 `retrieval_config_ref`, `rerank_config_ref`
- 실제 실행 adapter의 `adapter_artifact_ref`
- 요청과 동일한 `rerank_input_projection_version`
- 입력 raw-hit set의 canonical hash
- `selections`

Request는 `rerank_input_projection_version="knowledge-rerank-input-v1"`을 고정한다. Kernel은 아래
canonical projection을 rerank 호출 전에 만들고 hash와 함께 `EvidenceRerankRequest`에 전달한다. Rerank
Receipt는 같은 projection version과 hash를 그대로 반환해야 한다.

- UTF-8, Unicode NFC, JSON object key 정렬, compact separator와 명시적 `null`
- enum은 `.value` 문자열로 직렬화
- score는 아래 `CanonicalScore` 문자열로 직렬화
- 각 candidate는 `evidence_key` UTF-8 byte 순, stage signal은 `LEXICAL`, `DENSE` 고정 순서
- candidate의 전체 provenance, `content_sha256`, 각 stage·rank·score 포함
- transient `content_text` 자체는 제외하고 앞 단계에서 검증한 `content_sha256`으로 결속

이 projection의 golden SHA-256 fixture를 Kernel과 fake reranker가 공유한다. Reranker가 hash를 별도로
추정하지 않고 Kernel이 전달한 request를 소비하도록 해 serializer 중복 구현을 피한다.

## Knowledge Evidence와 raw hit

이번 slice에서 허용하는 Evidence kind는 `KNOWLEDGE_CHUNK` 하나뿐이다. `RULE_EVIDENCE`는 RAG-13의
결정론적 positive rule과 정확한 evidence binding이 제공되는 후속 변경 전까지 입력에서 거부한다.

`KnowledgeEvidenceProvenance`는 다음 값을 가진다.

- `evidence_key`
- `knowledge_chunk_ref`
- `evidence_index_ref`
- `source_snapshot_ref`
- `source_version`
- `locator`
- `content_sha256`
- `canonicalization_spec_version`

모든 문자열은 비어 있지 않고 NFC여야 하며 hash는 소문자 64자리 SHA-256이어야 한다. Hit의 Evidence
Index reference는 request와 exact-match해야 한다.

`source_manifest_member_hash`는 Source Guard의 `RELEASE_SOURCE` 의미와 혼동될 수 있어 이번 kernel
provenance에 포함하지 않는다. Knowledge Evidence Index 계약이 승인된 뒤 정확한 Source member binding을
별도 타입으로 추가한다.

`CanonicalScore`는 float가 아니라 다음 규칙의 ASCII decimal 문자열이다.

- 숫자 의미는 finite real만 허용
- `+`, exponent, leading zero, trailing fractional zero, `-0`, `NaN`, `Infinity` 금지
- 정수는 `0` 또는 `-?[1-9][0-9]*`
- 소수는 정수부와 소수점을 포함하고 마지막 fractional digit이 `1-9`

예: `0`, `1`, `-2`, `0.5`, `-0.25`, `10.125`는 유효하고 `1.0`, `01`, `-0`, `1e-3`은 유효하지
않다. 이 표현은 cross-process hash에서 binary float와 JSON number formatting 차이를 제거한다.

`KnowledgeEvidenceSearchHit`는 provenance와 다음 값을 가진다.

- `stage`
- stage 안에서 1부터 시작하는 `rank`
- `stage_score: CanonicalScore`
- reranker에만 전달되는 transient `content_text: SensitiveText`

Kernel은 `content_text`를 UTF-8로 encode해 SHA-256을 계산하고 `content_sha256`과 exact-match한다.
문자열을 normalize하거나 변환한 뒤 hash하지 않는다. Adapter는 Evidence Index에 고정된 canonical text를
그대로 반환해야 한다.

각 stage의 rank는 1부터 시작하는 중복 없는 연속 정수여야 하며 hit 수는 해당 stage limit 이하여야 한다.
같은 `evidence_key`는 한 stage에서 한 번만 나타날 수 있다. lexical과 dense에 같은 key가 등장할 수 있지만
provenance와 `content_text`는 exact-match해야 한다. 다르면 전체 실행을 `SEARCH_RESULT_INVALID`로 닫는다.

Kernel은 검증된 stage hit를 `evidence_key`별 `KnowledgeEvidenceCandidate` 하나로 정규화한다. Candidate는
하나의 provenance·content와 `LEXICAL`, `DENSE` 고정 순서의 `StageSignal` tuple을 가진다. 동일한
`knowledge_chunk_ref`가 서로 다른 `evidence_key`로 나타나거나, 하나의 `evidence_key`가 서로 다른 chunk
또는 provenance와 결속되면 `SEARCH_RESULT_INVALID`다.

Kernel은 모든 raw hit를 구조적으로 검증하지만 Source 승인이나 Bundle selection을 판정하지 않는다.
따라서 hit와 selection은 후속 권위적 Guard binding 전에는 Composer 입력이 아니다.

## rerank와 selection

`EvidenceRerankPort`는 검증된 canonical candidate와 Kernel이 계산한 input projection hash를 포함하는
`EvidenceRerankRequest`를 받아 `EvidenceRerankResult`를 반환한다. 각
`EvidenceRerankSelection`은 다음을 가진다.

- raw hit에 존재하는 `evidence_key`
- 1부터 시작하는 `rerank_rank`
- `rerank_score: CanonicalScore`

selection은 `selection_limit` 이하이고 rank가 중복 없는 연속 정수여야 한다. 같은 Evidence는 한 번만
선택할 수 있다. 알 수 없는 key, limit 초과, 비연속 rank와 NaN·infinite score는
`RERANK_RESULT_INVALID`다. Kernel은 검증된 `evidence_key`로 canonical candidate를 exact하게 다시 결속하여
`UntrustedKnowledgeEvidenceSelection`을 구성한다. 이 값은 transient content와 provenance를 함께
보존하지만, 이름과 타입 수준에서 권위 있는 Evidence Set 또는 Composer 입력이 아님을 명시한다.

동점 정렬 의미와 score fusion 공식은 rerank config artifact가 소유한다. Kernel은 받은 순서를 score로
재정렬하지 않고 rank와 Receipt 일관성만 검증한다.

## 비권위적 diagnostic trace

`EvidenceRetrievalDiagnosticTrace`는 테스트·adapter 개발을 위한 불변 내부 값이며 DB persistence,
Stream, 일반 로그, DLQ, quarantine, Safety Result, Evaluation Artifact 또는 공개 DTO 계약이 아니다.

Trace에는 다음만 포함한다.

- query fingerprint; `normalized_query` 제외
- filter, Evidence Index와 requested configuration reference
- 실제 query verifier, search와 rerank adapter artifact reference
- 각 hit의 Evidence key, stage, rank, score, content hash와 provenance
- rerank rank·score와 selection 여부
- Kernel execution status와 diagnostic code

Trace는 `content_text`, Source 원문, 질문 원문, credential, 환자 식별자, 생성 답변, Bundle ID, Guard 상세와
실행 시각을 포함하지 않는다. 실행 시각과 filter snapshot 내용을 포함하는 실제 Retrieval Run persistence는
승인된 DB·Privacy 계약이 별도로 정의한다.

Kernel outcome과 transient request/hit/selection은 generic serializer를 제공하지 않는다. 외부로 내보낼 수
있는 유일한 projection은 명시적 `to_sanitized_trace_dict(...)` 결과다. `SensitiveText` 때문에 whole-outcome
기본 JSON serialization은 실패해야 하며, request·hit·selection·typed failure의 `repr()`과 `str()`에는
query, content 또는 port exception message가 나타나면 안 된다.

## 결정성과 오류 처리

- 모든 enum 자리는 선언된 enum instance만 허용한다.
- 입력 tuple은 계약상 순서를 사용하거나 canonical UTF-8 byte 순으로 정렬한다.
- hash와 version은 불변 reference 및 Receipt exact-match로 검증한다.
- 예상 가능한 port 실패는 원문 exception 없이 typed failure로 반환한다.
- 프로그래밍 오류를 `NO_HITS` 또는 성공으로 강등하지 않는다.
- failure detail은 안정적인 필드 경로와 diagnostic code만 포함한다.
- Candidate Index 타입은 duck typing으로 수용하지 않는다.

## 테스트 기준

최소 단위 테스트는 다음 동작을 고정한다.

- lexical-only 실행과 `SUCCEEDED/CANDIDATES_RERANKED`
- lexical+dense hit가 모두 rerank port에 전달됨
- no-hit 실행은 rerank를 호출하지 않고 `SUCCEEDED/NO_HITS`
- query binding 검증 실패·Receipt mismatch에서 search 호출 0건
- request의 빈 값, NFD, hash·enum·limit 조합 오류가 port 호출 전 실패
- requested/applied filter·Index·config·query fingerprint mismatch 거부
- query verifier/search/rerank adapter artifact reference 누락 거부 및 실제 reference 기록
- query binding invalid, verifier dependency failure와 malformed/mismatch Receipt 상태 분리
- stage별 rank 중복·누락, limit 초과와 비정규 score 문자열 거부
- Candidate Index hit와 `RULE_EVIDENCE` 거부
- content hash mismatch 거부
- lexical/dense 동일 key의 content·provenance mismatch 거부
- 동일 chunk의 복수 evidence key와 동일 key의 복수 chunk binding 거부
- stage hit를 key별 canonical candidate와 정렬된 signal tuple로 정규화
- rerank input-set hash mismatch 거부
- projection golden hash가 Unicode·null·lexical-only·mixed-stage·음수 score에서 동일함
- `-0`, exponent, leading/trailing zero와 NaN·infinite score 표현 거부
- rerank의 알 수 없는 key, 중복·비연속 rank 거부와 canonical candidate의 exact selection 재결속
- 동일 입력·동일 port output의 동일 diagnostic trace
- recursive trace projection에 query와 Evidence content가 없음
- request·outcome·failure `repr`/`str`과 port exception 처리에 query·content·exception message가 없음
- whole-outcome 기본 JSON serialization 실패와 sanitized serializer만 성공
- output이 Source approval, sufficiency, Safety 상태 또는 Composer 사용 가능성을 주장하지 않음

검증 명령은 다음과 같다.

```bash
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag/test_evidence_retrieval.py -q
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run pytest ai_worker/tests/rag -q
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run ruff check ai_worker/tasks/rag ai_worker/tests/rag
UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run mypy ai_worker/tasks/rag
git diff --check
```

## 후속 통합 조건

이번 단위 구현은 다음이 연결되기 전까지 Evidence Gate, 통합 완료 또는 Issue Close 증빙이 아니다.

- Knowledge Evidence Index manifest·member·persistence 계약과 migration
- `#165/#166` 실제 Source Snapshot·Catalog Receipt
- Full Execution Context와 Runtime Bundle의 승인된 shared DTO·Guard binding
- query HMAC algorithm·canonical input·key rotation을 소유하는 Privacy·Security 계약
- PostgreSQL lexical/dense adapter와 configuration Receipt
- `#177` positive interaction rule과 Rule Evidence binding
- Safety v2의 execution/evidence/release 상태 매핑
- Retrieval Run persistence schema와 transaction owner
- Composer용 typed Evidence payload와 Citation Authorization 연결
- `#158` Dataset/Index version이 결속된 Recall@5 Artifact

현재 단위 구현의 차단 코드는 `BLOCKED_BY_KNOWLEDGE_EVIDENCE_INDEX_CONTRACT`다. 실제 Source readiness는
별도로 `BLOCKED_BY_SOURCE_GOVERNANCE_RECEIPT`를 유지한다. 두 차단 상태를 Candidate Index
`#167/#168` 완료로 해소하지 않는다.

## 완료 주장 경계

이 변경이 검증할 수 있는 주장은 “합성 Knowledge Evidence와 versioned port Receipt에 대해 검색·rerank
orchestration과 결과 무결성 검증이 결정적이다”까지다. 실제 Source 승인, Evidence sufficiency/conflict,
Safety 상태, `pg_trgm`·dense 품질, Retrieval Run 저장, Recall@5, Citation 정확성, Runtime Bundle 활성화
또는 환자 공개 안전성을 완료로 주장하지 않는다.
