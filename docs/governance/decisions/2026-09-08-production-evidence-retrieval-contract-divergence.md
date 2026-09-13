# Product Decision Candidate: Production Evidence Retrieval 정규 계약

| 항목 | 값 |
| --- | --- |
| Decision ID | `PD-315-20260908` |
| 상태 | Review pending · Issue #315 |
| 제안·구현 | 정현우 (`@ceohwj`) — AI/RAG |
| 책임 리뷰 | 권가빈 (`@hazelnutflavoured`) — Evidence 계약·Safety 경계 |
| 교차 리뷰 | 송은영 (`@phina-io`) — DB·hash domain / 김지혜 (`@Jye-rookie`) — Source provenance |
| 추적 Issue | [#315](https://github.com/AI-HealthCare-05/AH_05_04/issues/315) |
| 상위 결정 | [`PD-125-20260831`](./2026-08-31-rag-p0-contract-freeze.md) |

## 목적과 권위 경계

PR #290의 synthetic Evidence Search·Rerank Adapter를 Production 구현으로 승격하기 전에 정규 문서와의
divergence 8건을 해소한다. 이 Decision은 Authority Manifest
`post-mvp-rag-evaluation-contract@2026-08-29.11`의 `rag-design` v1.50,
`rag-db-schema` v1.47, `rag-source-policy` v1.18, `rag-evaluation-plan` v1.35를 저장소 후속 구현 경계에
투영한다. 정규 원본을 변경하거나 현재 Runtime 계약으로 승격하지 않는다.

## 결정

| # | 영역 | Production Adapter가 적용할 결정 |
| ---: | --- | --- |
| 1 | RRF·top-K | `Lexical(20) + Dense(20) -> rank 기반 RRF(출력 30) -> Reranker 입력 20 -> Evidence Gate -> Context 최대 5`를 P0 승인 기본 configuration으로 고정한다. 공식·dedupe·tie-break와 `rrf_k`는 아래 세부 결정을 따른다. PR #290의 `weighted-stage-score-v1`은 synthetic 전용이다. |
| 2 | Canonical JSON | Production Retrieval과 Evaluation 사이의 JSON artifact hash는 RFC 8785 JCS bytes를 사용한다. JSON이 아닌 Evidence text·query digest는 별도 byte preimage를 유지한다. |
| 3 | Provenance | Production provenance는 공통 Source·Snapshot Member 결속 위에 `KNOWLEDGE_CHUNK`, `INTERACTION_RULE`, `LIFESTYLE_GUIDELINE`별 필드와 조건부 필수성을 분리한다. Evaluation 전용 `evidence_ref_id`를 Runtime provenance에 넣지 않는다. |
| 4 | 상태축 | Retrieval receipt는 terminal `retrieval_execution_status`를, `retrieval_run.status`는 `RUNNING`을 포함한 persistence lifecycle을 소유한다. Safety finalizer가 Retrieval diagnostic·Evidence Gate 결과·실패 원인을 함께 판정해 `safety_result.execution_status`로 변환하며 값을 직접 복사하지 않는다. PR #270/#290의 synthetic 타입은 이번 PR에서 변경하지 않는다. |
| 5 | Node ID | Runtime Graph·Execution Manifest에는 canonical Node ID `hybrid_retrieve`를 기록한다. 내부 Python 함수명 `retrieve_knowledge_evidence()`는 유지할 수 있다. Evidence Gate는 별도 Node ID `evidence_gate`를 사용한다. |
| 6 | PostgreSQL FTS | Production PostgreSQL Search Adapter가 Exact·Trigram·`ts_rank_cd` 실행, lexical 순위와 configuration receipt를 소유한다. Kernel은 rank·provenance·receipt를 검증한다. Evaluation runner는 `RET-L`, `RET-D`, `RET-H`, `RET-HR` 실행과 결과를 검증하며 bridge나 runner가 SQL ranking을 재구현하지 않는다. |
| 7 | `source_version` | Production Source 생성 경계가 Source 유형과 외부 불변 version 존재 여부에 맞는 정규 형식을 생성한다. Production Retrieval Adapter는 문자열만 보지 않고 Source metadata와 형식의 결속을 재검증한다. |
| 8 | Bridge 문법 | Production Adapter는 Runtime UUID·안정 좌표만 검증한다. Evaluation bridge producer가 `evidence_ref_id`, `evidence_mapping_stable_key`, `evidence_key`, `knowledge_chunk_ref`를 생성·검증하고 bridge loader가 같은 문법과 exact-match를 독립 검증한다. |

## 결정적 RRF와 Lexical 순위

- Algorithm ID는 `rrf-rank-fusion@1`이고 `rank`는 1부터 시작한다.
- 이 RRF는 `KNOWLEDGE_CHUNK` 검색에만 적용한다. `INTERACTION_RULE`은 canonical Graph의 `rule_check`에서
  결정론적으로 평가하고 연결된 `rule_evidence`를 RRF 후보로 다시 검색하지 않는다.
- canonical dedupe·tie-break identity는
  `(source_code, source_version, external_document_id, chunk_index)` 안정 좌표다. 같은 identity에 서로 다른
  `content_hash`가 오면 별도 후보로 취급하지 않고 `VALIDATION_ERROR`로 닫는다.
- 같은 목록에서 같은 key가 반복되면 전체 결과를 invalid로 닫고, 어떤 목록에 없는 후보의 기여값은 `0`이다.
- Lexical은 Exact 일치 후보를 먼저 둔다. 각 Exact/non-Exact bucket 안에서는 Trigram과 `ts_rank_cd`가 만든
  두 순위를 `sum(1 / (rrf_k + rank))`로 합치고, 동점은 안정 좌표의 UTF-8 byte order로 푼다.
- 최종 Hybrid RRF도 Lexical·Dense 두 순위를 같은 공식으로 합치며 동점 규칙도 같다. 순위 비교는 각 항을
  기약분수로 유지한 exact rational 합에 대해 수행하고 비교 전 반올림하지 않는다. 최종 동률만 안정 좌표로
  푼다. 상위 30개 중 앞의 20개만 reranker에 전달한다.
- Receipt/hash의 authoritative score는 `{"numerator":"<base-10 integer>","denominator":"<positive base-10 integer>"}`
  기약분수로 JCS 직렬화한다. 두 문자열은 부호가 필요한 분자를 제외하고 leading zero를 허용하지 않는다.
  후속 `retrieval_hit.rrf_score`는 `NUMERIC(38,18)` round-half-even 관측값으로 저장하되 순위·동률 판정에
  재사용하지 않고, `rrf_rank`, raw ranks, configuration과 authoritative fraction receipt로 재현한다.
- `rrf_k=60`, RRF 출력 30, Lexical 20, Dense 20, Reranker 입력 20, Context 최대 5는 이 Decision 승인 시
  P0 기본 configuration이다. DEV 결과가 변경을 요구하면 기존 값을 수정하지 않고 새 configuration version을
  제안·승인하며, HOLDOUT은 선택된 한 version을 실행 전에 동결한다. Gate 결과를 목표 수량에 맞추려고 채우지
  않는다. Golden vector는 미등장 후보, 중복 identity, content hash 충돌, exact-rational 근접값·동률과
  UTF-8 tie-break를 포함한다.

## Canonical JSON과 hash domain

RFC 8785 JCS Object key는 UTF-16 code unit 순서다. Unicode 정규화, 집합 배열 정렬, 명시적 `null`, 제외
필드와 Envelope는 각 JSON hash domain의 versioned projection이 JCS 전에 소유한다. 따라서 JCS 사용 자체가
모든 문자열의 NFC 변환을 뜻하지 않는다. 특히 현재 Source Ingestion의 `mfds-product-approval@1` Snapshot
checksum은 원문 Unicode를 보존하고 NFC를 적용하지 않는 정규 계약을 유지하며, 이 Decision이 그 preimage를
변경하지 않는다. Score·threshold는 JSON float 대신 canonical decimal 문자열을 유지한다.
`json.dumps(sort_keys=True)` hash는 Production JSON artifact에 사용할 수 없다.

Snapshot canonical checksum, Evidence Index corpus manifest hash, Retrieval configuration hash와 Evaluation
artifact hash는 각각 다른 JSON preimage를 JCS로 직렬화한다. 개별 Evidence content hash는 Index가 고정한
canonical text의 UTF-8 bytes, query digest는 승인된 versioned HMAC preimage를 사용하며 JCS 대상이 아니다.
서로 다른 hash domain의 값을 같다고 보거나 한 필드로 합치지 않는다. JSON golden vector는 non-BMP Object
key, 명시적 `null`, 빈 배열, safe integer와 금지 float를 포함한다.

## Evidence 유형별 Production provenance

모든 유형은 `evidence_type`, `source_code`, `source_version`, `source_snapshot_id`, Snapshot의
`canonical_checksum`, `locator`와 정확히 하나의 Endpoint/Operation Snapshot Member 또는 Artifact Snapshot
Member를 가진다. Endpoint 경로의
`endpoint_code`는 필수이고 `operation_code`는 정규 Member 계약에 따라 nullable이다.

| `evidence_type` | Runtime provenance와 stable key | bridge `content_sha256` preimage |
| --- | --- | --- |
| `KNOWLEDGE_CHUNK` | `knowledge_chunk_id`, `external_document_id`, `chunk_index`, `content_hash`, text canonicalization version 필수. Stable key는 `(source_code, source_version, external_document_id, chunk_index)`. `external_record_id`와 별도 `supporting_excerpt`를 공통 필드로 복제하지 않는다. | `knowledge_chunk.content_hash`: Index가 고정한 canonical chunk text UTF-8 bytes의 SHA-256 |
| `INTERACTION_RULE` | `rule_evidence_id`, `rule_id`, `rule_code`, `rule_version`, `evidence_role`, nullable `knowledge_chunk_id`, `supporting_excerpt`를 보존한다. Stable key는 `(rule_code, rule_version, source_code, source_version, locator)`. Chunk 없는 직접 구조화 Rule은 `external_record_id`도 필수다. | `evidence-bridge-content@1` JCS projection: `evidence_type`, `rule_code`, `rule_version`, `evidence_role`, `source_code`, `source_version`, `locator`, nullable `external_record_id`, `supporting_excerpt`, `knowledge_chunk_stable_key`. UUID·Snapshot checksum·Member byte hash 제외 |
| `LIFESTYLE_GUIDELINE` | `guideline_evidence_id`, `guideline_id`, `guideline_code`, `guideline_version`, `claim_key`, nullable `action_key`, nullable `knowledge_chunk_id`, `supporting_excerpt`를 보존한다. Stable key는 `(guideline_code, guideline_version, claim_key, action_key, source_code, source_version, locator)`. 물리 계약에 없는 `external_record_id`를 추가하지 않는다. | `evidence-bridge-content@1` JCS projection: `evidence_type`, `guideline_code`, `guideline_version`, `claim_key`, nullable `action_key`, `source_code`, `source_version`, `locator`, `supporting_excerpt`, `knowledge_chunk_stable_key`. UUID·Snapshot checksum·Member byte hash 제외 |

`evidence-bridge-content@1`은 위 표의 key를 정확히 사용하고 nullable key도 명시적 `null`로 포함한다. Bridge의
`knowledge_chunk_stable_key`는 연결 Chunk가 없으면 `null`, 있으면 `source_code`, `source_version`,
`external_document_id`, `chunk_index`를 가진 Object다. Bridge의
`content_sha256`은 해당 bytes의 SHA-256 lower hex다. 이는 `source_snapshot.canonical_checksum`, Snapshot
Member `content_sha256`, `knowledge_chunk.content_hash`를 서로 대체하지 않으며 Rule·Guideline DB에 새 공통
content-hash 컬럼이 있다고 가정하지 않는다. 후속 구현은 projection golden vector와 schema/version 변경을
같은 PR에 포함한다. Runtime provenance의 `canonical_checksum`은 `source_snapshot_id`가 가리키는 불변
Snapshot 값과 exact-match하여 당시 내용 동일성을 증명하지만 `evidence-bridge-content@1` preimage에는 넣지
않는다.

Runtime은 위 필드와 안정 좌표를 검증한다. Evaluation bridge producer는 검증된 Runtime identity를
`evidence_type + stable_key + source_version + locator + content_sha256` mapping에 결속해 `evidence_ref_id`와
opaque bridge reference를 만든다.

## Retrieval 상태와 Safety 변환

Retrieval receipt의 필드명은 `retrieval_execution_status`이고 #178 P0 terminal 허용값은 `SUCCEEDED |
DEPENDENCY_ERROR | VALIDATION_ERROR`다. `retrieval_run.status`는 실행 중 `RUNNING`, 완료 뒤에는 같은 terminal
값 중 하나를 저장한다. Retrieval dependency의 timeout은 `DEPENDENCY_ERROR`로 정규화하며 Safety Result의
`TIMED_OUT/PROVIDER_TIMEOUT`은 Provider 호출 timeout에만 사용한다. `NO_HITS`는 `SUCCEEDED`와 결합되는
diagnostic code이며 Retrieval 단계에서 `NO_RESULT`로 승격하지 않는다.

| Retrieval·Gate·후속 결과 | Safety finalizer 결과 |
| --- | --- |
| Retrieval `SUCCEEDED/CANDIDATES_RERANKED` + Evidence `SUFFICIENT` + 생성·검증 성공 | `execution_status=SUCCEEDED`; 별도 Release Gate가 공개 여부 결정 |
| Retrieval `SUCCEEDED/NO_HITS`, 승인 근거 없음, Retrieval 근거 부족 | `execution_status=NO_RESULT`, `evidence_status=INSUFFICIENT`, `release_decision=REJECTED`, `fallback_code=NO_APPROVED_EVIDENCE` |
| Runtime REQUEST Guard가 pinned Source·Operation·요청 version에 authoritative `SOURCE_VERSION_CONFLICT` 감사 근거를 exact-bind | `execution_status=NO_RESULT`, `evidence_status=CONFLICTED`, `release_decision=REJECTED`, `fallback_code=CONFLICTING_EVIDENCE` |
| Source TTL 만료 | `execution_status=NO_RESULT`, `evidence_status=STALE`, `release_decision=REJECTED`, `fallback_code=NO_APPROVED_EVIDENCE` |
| 지원 범위 밖 | `execution_status=SUCCEEDED`, `evidence_status=INSUFFICIENT`, `release_decision=LIMITED`, `fallback_code=UNSUPPORTED_REQUEST`; 승인된 한계 안내만 공개 |
| 사용자 Context에 미적용 | `execution_status=SUCCEEDED`, `evidence_status=INSUFFICIENT`, `release_decision=LIMITED`, `fallback_code=null`; 해당 Claim 생략·승인된 한계 안내 |
| Retrieval `DEPENDENCY_ERROR` | `execution_status=DEPENDENCY_ERROR`, `evidence_status=INSUFFICIENT`, `release_decision=REJECTED`, `fallback_code=DEPENDENCY_UNAVAILABLE` |
| Retrieval `VALIDATION_ERROR` | `execution_status=VALIDATION_ERROR`, `evidence_status=INSUFFICIENT`, `release_decision=REJECTED`, `fallback_code=VALIDATION_FAILED` |

Safety finalizer가 이 표를 소유한다. 수집 이력의 unbound 또는 latest `SOURCE_VERSION_CONFLICT`를 현재 Job에
직접 투영하지 않는다. #178에서 위 exact origin 결속을 증명할 수 없으면 충돌 Source를 Runtime selection에서
차단할 뿐 `evidence_status=CONFLICTED`를 추론하지 않는다. Evidence Gate 단독 성공이나 Retrieval
`SUCCEEDED`만으로 공개 가능 또는 Safety `SUCCEEDED`를 만들지 않는다. Kernel·Evidence Gate의 중간 outcome은
근거 판정 전에 실행이 실패하면 nullable `evidence_status`를 가질 수 있지만, 영속 Safety Result finalizer는
위 표의 완결된 상태 조합으로 변환한다.

## Source version과 Evaluation bridge 문법

Production `source_version` 전체 값은 1~200자, NFC이며 공백·제어문자를 허용하지 않는다.

| 조건 | 허용 형식 |
| --- | --- |
| 제공자가 불변 version을 제공 | `external:<nonempty-version>` |
| `source_type=API`이고 외부 불변 version이 없음 | `api:<RFC3339 UTC 고정 6자리 소수초>:<canonical_checksum 64-lower-hex>` |
| `source_type=INTERNAL_CURATED_DATA` | `internal:commit-<40 lower-hex>-manifest-<64 lower-hex>:<canonical_checksum 64-lower-hex>` |

API·Internal 형식의 hash suffix는 같은 `source_snapshot_id`의 `canonical_checksum`과 exact-match해야 하며,
Source producer와 Production Retrieval Adapter가 각각 이 결속을 검증한다. Source producer의 수집 시점
불일치는 Snapshot을 생성하지 않고 수집 validation failure로 닫는다. 이 실패의 정확한 ingestion
`failure_code`는 #362에서 Source Ingestion 계약과 함께 고정하며 `SOURCE_VERSION_CONFLICT`로 재사용하지
않는다. `SOURCE_VERSION_CONFLICT`는 기존 Source Ingestion 계약대로 이미 관측된 동일
`source_version`이 서로 다른 canonical contract와 재사용된 수집 사건에 유지한다. 제공자의
불변 외부 version 재사용과 Canonical 내용 불일치는 이 사건의 대표적 경로이지만,
`external:` prefix만으로 한정하지 않는다. #362는 Snapshot이 생성되지 않는 conflict도 Operation, 시도한
`source_version`과 비교한 canonical contract를 append-only 감사 근거로 보존하여 후속 Guard가 origin을
검증할 수 있게 한다. 이미 저장된 Snapshot을 읽는
Production Retrieval Adapter에서 suffix와 `canonical_checksum`이 다르면 detached 또는 변조된 provenance
결속 실패이므로 `retrieval_execution_status=VALIDATION_ERROR`로 닫고 Safety finalizer가
`execution_status=VALIDATION_ERROR`, `evidence_status=INSUFFICIENT`, `release_decision=REJECTED`,
`fallback_code=VALIDATION_FAILED`로 변환한다.
Adapter 검증 실패를 `evidence_status=CONFLICTED` 또는 `CONFLICTING_EVIDENCE`로 분류하지 않는다.

PD-362 Source producer 구체화에 따라 Internal Fixture의 기존 승인 Git tag 경로는 제거한다.
Git tag는 이동 가능한 ref이므로 불변 식별자로 취급하지 않는다. Internal `fixture_version`은
40자리 lowercase Commit SHA와 64자리 lowercase Fixture Manifest SHA-256을 함께
exact-bind해야 한다. 승인 tag나 release 이름은 필요하면 Receipt의 별도 검토 metadata로
보존하며 `source_version` 정본에는 포함하지 않는다.

`external:` payload는 같은 Snapshot에 보존된 non-null `external_version`과 byte-for-byte exact-match해야 한다.
외부 불변 version이 없는 API·Internal 경로의 `external_version`은 `null`이어야 한다. #362 producer와
Production Retrieval Adapter가 각각 이 결속을 검증하며, Adapter에서 발견한 불일치는 위와 같은
`VALIDATION_ERROR/VALIDATION_FAILED`로 닫는다. 전체 200자 상한에는 prefix도 포함되므로 `external:` payload의
실질 상한은 191자다. 정규 DB의 `external_version VARCHAR(200)` 물리 용량을 Production 문법 허용치로
오인하지 않으며 #362가 191/192자 경계를 검증한다.

Synthetic marker는 테스트 전용 namespace에서만 허용한다. Evaluation bridge의 네 stable ID는
`^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$`를 따르고 `source_version`과
`canonicalization_spec_version`은 1~256자의 공백·제어문자 없는 token이다. Bridge producer와 loader가
각각 검증하므로 `1.0.0` 같은 Evaluation synthetic token을 Production Source version으로 오인하지 않는다.
Runtime `locator`는 일반 Evidence Mapping의 `locator`, Knowledge Index Bridge의 `source_locator`에 값 변경 없이
투영한다.

승인된 새 `source_version`은 `canonical_checksum`이나 bridge `content_sha256`이 이전과 같아도 새 Runtime
identity다. Bridge producer는 stable key와 `evidence_ref_id`를 새로 만들고 새
`evidence-mapping-manifest.json`에 결속하며, 이전 Dataset·Run의 mapping을 이어 쓰지 않고 재평가한다. 같은
내용 재수집이 `NO_CHANGE`로 끝나 새 Snapshot과 `source_version`을 만들지 않은 경우에는 재결속하지 않는다.

## 정규 근거와 현재 divergence

| # | 정규 근거 | 현재 저장소 위치 |
| ---: | --- | --- |
| 1 | `rag-design` §18, `rag-evaluation-plan` §2 `RET-H`·`RET-HR` | `VersionedEvidenceRerankAdapter`의 `weighted-stage-score-v1` |
| 2 | `rag-db-schema` §6 `rag.source_snapshot` Canonical Checksum | `evaluation/canonical.py::canonical_json_bytes`, Retrieval의 `json.dumps(sort_keys=True)` hash |
| 3 | `rag-source-policy` §8, `rag-db-schema`의 Knowledge·Rule·Guideline Evidence | `evidence_retrieval.py::KnowledgeEvidenceProvenance` |
| 4 | `rag-design` §22 상태 계약 | `evidence_retrieval.py::KernelExecutionStatus`와 `execution_status` |
| 5 | `rag-design` §16 canonical Node ID | `evidence_retrieval.py::retrieve_knowledge_evidence` |
| 6 | `rag-design` §18, `rag-db-schema` §1의 PostgreSQL native FTS | synthetic lexical exact·trigram만 구현 |
| 7 | `rag-source-policy` §7, `rag-db-schema` §6의 Source Version 형식 | synthetic adapter의 nonblank NFC 검증 |
| 8 | `rag-evaluation-plan` §4와 `provenance_v1.py::IndexBridgeEntry` | Retrieval adapter가 stable ID 문법을 강제하지 않음 |

## 적용 순서와 제외 범위

1. #166 PR에서 Catalog/Resolver 입력 경계와 Source provenance를 검토한다.
2. #166 완료 뒤 #167/#168에서 실제 Catalog export와 DB 연결을 진행한다. #167 pure Candidate Index logic은
   PR #260으로 구현됐으므로 새 unit slice를 만들지 않는다.
3. #362에서 Source ingestion이 Production `source_version`을 생성·검증하고 외부 Version 보존과 200자 상한,
   파생 Freshness·Snapshot 승인 경계를 정규 계약과 맞춘다. #178은 이 생산자 경계가 완료되기 전에
   Production `source_version` 검증을 활성화하지 않는다.
4. #178 Production slice에서 PostgreSQL hybrid retrieval, 정규 provenance, canonical Node·상태·configuration
   receipt를 구현한다. authoritative Retrieval Run persistence, locator 검증과 Gate origin 결속은 그 구현
   경계에서 별도 DB·Safety 검토를 받는다.
5. 위 Runtime receipt가 준비된 뒤 실제 Retrieval Evaluation을 연결한다.

이 Decision PR에는 PostgreSQL query·migration·repository, Catalog 연결, Retrieval Run persistence,
Evidence Gate·Composer 구현, Evaluation 실행 또는 공개 flag 변경을 포함하지 않는다. `PUBLIC_TRACK_F=false`를
유지한다. #166 D-05가 소유하는 Candidate Catalog projection hash와 Runtime medication Catalog manifest hash,
PR #329/#167 v2 Catalog envelope 계산식은 이 Decision이 변경하지 않는다.

## 승인과 후속 구현 조건

이 문서는 책임 리뷰어와 두 교차 리뷰어의 담당 범위 승인을 받기 전까지 `Review pending`이다. 승인은
Production Adapter나 DB 연결이 구현됐다는 뜻이 아니다. 후속 구현 PR은 이 Decision의 단계·필드·문법을
코드, configuration artifact, schema와 계약·통합·Evaluation 테스트에 함께 반영하고, 구현 증빙 없이
`current/` 계약으로 승격하지 않는다.
