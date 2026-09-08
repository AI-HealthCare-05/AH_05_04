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
| 1 | RRF·top-K | `Lexical(20) + Dense(20) -> rank 기반 RRF(최대 30) -> Reranker 입력 20 -> Evidence Gate -> Context 최대 5`로 고정한다. Exact·Trigram·`ts_rank_cd`는 versioned lexical configuration이 하나의 Lexical 순위를 만든다. RRF 초기 `k=60`도 configuration version/hash에 포함한다. Gate 결과를 3개 이상으로 채우기 위해 부족한 Evidence를 통과시키지 않는다. PR #290의 `weighted-stage-score-v1`은 synthetic 전용이며 Production에 승격하지 않는다. |
| 2 | Canonical JSON | Production Retrieval과 Evaluation 사이의 cross-language artifact hash는 RFC 8785 JCS bytes를 사용한다. Object key는 UTF-16 code unit 순서다. Unicode NFC, 집합 배열 정렬, 명시적 `null`, 제외 필드와 Envelope는 각 hash domain의 versioned projection이 JCS 전에 소유한다. Score·threshold는 JSON float 대신 canonical decimal 문자열을 유지한다. `json.dumps(sort_keys=True)` hash는 Production artifact에 사용할 수 없다. |
| 3 | Provenance | Production Evidence provenance는 `evidence_type`, `source_code`, 정규 `source_version`, Endpoint Member 또는 Artifact Member 경로, `external_record_id`, `locator`, 조건부 `supporting_excerpt`, Source Snapshot과 정확히 하나의 Snapshot Member, Knowledge Chunk UUID·안정 좌표, content hash·text canonicalization version을 보존한다. Endpoint Member는 `endpoint_code`가 필수이고 `operation_code`는 정규 Member 계약에 따라 nullable이다. Chunk 없는 구조화 Rule은 `external_record_id`, `locator`, `supporting_excerpt`가 필수다. `evidence_ref_id`는 Evaluation bridge가 소유하며 Runtime provenance에 평가 전용 ID를 주입하지 않는다. |
| 4 | 상태축 | Production Retrieval 내부 필드는 `retrieval_execution_status`로 명명한다. `safety_result.execution_status`와 같은 무자격 이름을 공유하거나 값을 직접 복사하지 않는다. Evidence Gate가 Retrieval 결과와 Evidence 판정을 입력으로 Safety 상태를 만든다. PR #270/#290의 synthetic `KernelExecutionStatus`는 이번 문서 PR에서 변경하지 않는다. |
| 5 | Node ID | Runtime Graph·Execution Manifest에는 canonical Node ID `hybrid_retrieve`를 기록한다. 내부 Python 함수명 `retrieve_knowledge_evidence()`는 유지할 수 있다. Evidence Gate는 별도 Node ID `evidence_gate`를 사용한다. |
| 6 | PostgreSQL FTS | Production PostgreSQL Search Adapter가 Exact·Trigram·`ts_rank_cd` 실행, lexical 순위와 configuration receipt를 소유한다. Kernel은 반환 rank·provenance·receipt 결속을 검증한다. Evaluation bridge는 `RET-L`, `RET-H`, `RET-HR`의 resolved configuration과 결과를 검증하며 SQL ranking을 재구현하지 않는다. |
| 7 | `source_version` | Production Source 생성 경계와 Production Retrieval Adapter가 `external:<nonempty-version>`, `api:<RFC3339 UTC 고정 6자리 소수초>:<64-lower-hex>`, `internal:<immutable-fixture-version>:<64-lower-hex>`를 강제한다. 전체 값은 1~200자, NFC이며 공백·제어문자를 허용하지 않는다. Synthetic marker는 테스트 전용 namespace에서만 허용한다. |
| 8 | Bridge 문법 | `evidence_ref_id`, `evidence_key`, `knowledge_chunk_ref`는 `^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$`를 Production Adapter 출력 전과 Evaluation bridge 입력에서 모두 검증한다. `source_version`과 `canonicalization_spec_version`은 bridge에서 1~256자의 공백·제어문자 없는 token·exact-match를 유지한다. Production `source_version`의 더 강한 문법은 7번 경계에서 검증해 `1.0.0` 같은 Evaluation synthetic token을 금지하지 않는다. 후속 구현은 같은 validator 규칙을 재사용하되 양쪽 검증을 생략하지 않는다. |

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

## Hash domain과 검증 최소 기준

Snapshot canonical checksum, Evidence Index corpus manifest hash, Retrieval configuration hash, 개별 Evidence
content hash와 Evaluation artifact hash는 서로 다른 preimage를 가진다. 같은 JCS serializer를 사용한다는 이유로
값을 같다고 보거나 한 필드로 합치지 않는다. 후속 구현은 non-BMP object key, 명시적 `null`, 빈 배열,
safe integer, 금지 float에 대한 cross-language golden vector를 공유한다.

## 적용 순서와 제외 범위

1. #166 PR에서 Catalog/Resolver 입력 경계와 Source provenance를 검토한다.
2. #166 완료 뒤 #167/#168에서 실제 Catalog export와 DB 연결을 진행한다. #167 pure Candidate Index logic은
   PR #260으로 구현됐으므로 새 unit slice를 만들지 않는다.
3. #178 Production slice에서 PostgreSQL hybrid retrieval, 정규 provenance, canonical Node·상태·configuration
   receipt를 구현한다. authoritative Retrieval Run persistence, locator 검증과 Gate origin 결속은 그 구현
   경계에서 별도 DB·Safety 검토를 받는다.
4. 위 Runtime receipt가 준비된 뒤 실제 Retrieval Evaluation을 연결한다.

이 Decision PR에는 PostgreSQL query·migration·repository, Catalog 연결, Retrieval Run persistence,
Evidence Gate·Composer 구현, Evaluation 실행 또는 공개 flag 변경을 포함하지 않는다. `PUBLIC_TRACK_F=false`를
유지한다.

## 승인과 후속 구현 조건

이 문서는 책임 리뷰어와 두 교차 리뷰어의 담당 범위 승인을 받기 전까지 `Review pending`이다. 승인은
Production Adapter나 DB 연결이 구현됐다는 뜻이 아니다. 후속 구현 PR은 이 Decision의 단계·필드·문법을
코드, configuration artifact, schema와 계약·통합·Evaluation 테스트에 함께 반영하고, 구현 증빙 없이
`current/` 계약으로 승격하지 않는다.
