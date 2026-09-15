# Issue #178 RET-H Completion Design

## 1. 목적

Issue #178의 이미 병합된 PostgreSQL `Exact/Trigram/FTS + Dense -> deterministic RRF` 검색을 실제 실행 경계로
완성한다. 완료 결과는 다음 두 가지다.

1. Runtime이 검색 실행과 신호·융합 결과·최종 선택을 versioned Retrieval Run으로 재현할 수 있다.
2. Evaluation runner가 replay가 아닌 같은 Production search adapter를 호출해 `RET-L`, `RET-D`, `RET-H` DEV 결과와
   latency를 생성할 수 있다.

이번 설계는 시간 제약을 고려해 구현을 두 PR로 제한한다.

- PR 1: Retrieval Run persistence + `hybrid_retrieve`/`evidence_gate` runtime core
- PR 2: actual DEV evaluation + AWS synthetic smoke + 문서/Issue 증빙

`RET-HR` reranker는 두 PR 모두에서 제외한다. `RET-H` 결과를 `RET-HR`로 기록하거나, RRF Top 5를 reranker 결과로
위장하거나, 가짜 reranker receipt를 생성하지 않는다.

모델 책임도 분리한다. #178 Retrieval의 vector 생성은 `text-embedding-3-large` 1536차원을 사용한다. 사용자가 승인한
`gpt-4o`는 #180 Generator의 답변 생성 모델이며 이번 두 PR에서는 호출하지 않는다. Embedding configuration hash와
Generator model/prompt hash를 합치거나 같은 필드로 기록하지 않는다.

## 2. 구현 전 Gate 0

Gemini는 구현 브랜치를 만들기 전에 최신 `develop`으로 갱신하고 아래 조건을 확인한다.

1. PR #558과 PR #560의 실제 병합 여부 및 merge commit을 기록한다.
2. Issue #315와 #362는 Closed지만 저장소의
   `docs/governance/decisions/2026-09-08-production-evidence-retrieval-contract-divergence.md` 상태가 여전히
   `Review pending`이면, GitHub Issue Close만 승인 Receipt로 간주하지 않는다.
3. `docs/contracts/`, Decision, 병합 코드가 같은 상태·필드·hash domain을 설명하는지 확인한다.
4. Alembic head를 조회해 새 migration의 `down_revision`을 최신 단일 head로 정한다. 설계 문서의 현재 revision을
   하드코딩하지 않는다.
5. 현재 worktree의 `.claude/`, `skills-lock.json`, reranker 설계 문서 등 기존 untracked 파일을 수정·삭제·커밋하지
   않는다.
6. Dense query와 DEV corpus vector는 이 설계에서 승인한 OpenAI embedding 실행 프로필
   (`text-embedding-3-large`, `dimensions=1536`)과 새 `OpenAITextEmbeddingAdapter`를 사용한다. 현재 저장소에는
   `QueryEmbeddingReceipt` 검증만 있으므로 concrete adapter 구현은 PR 1의 필수 범위다. 실제 DEV/AWS 실행 시
   credential이 없으면 `BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL`로 기록하고 RET-D/RET-H actual 평가와 AWS smoke를
   실행하지 않는다.
7. DEV resource `synthetic-knowledge-index.json`은 합성 문장 100개와 content hash만 제공하며 embedding artifact는
   제공하지 않는다. PR 2는 query와 동일한 approved adapter/model로 이 100개 문장을 embedding한 versioned
   Knowledge Index를 먼저 생성해야 한다. Gold label 또는 expected evidence ID를 vector 생성 입력에 섞지 않는다.

Gate 0에서 승인 계약이 실제로 불일치하면 persistence schema를 추정해 구현하지 않는다. PR 1의 첫 commit을 계약
정합화로 제한하고 책임 리뷰어 확인을 받은 뒤 같은 PR의 persistence commit을 진행한다.

## 3. 현재 구현에서 재사용할 정본

- `ai_worker/tasks/rag/evidence_search.py`
  - Production `EvidenceSearchPort`
  - `EvidenceSearchRequest`, `EvidenceSearchSuccess`, `EvidenceSearchFailure`
  - versioned lexical/dense/retrieval configuration과 `QueryEmbeddingReceipt`
- `ai_worker/adapters/postgresql_evidence_search.py`
  - read-only `REPEATABLE READ` PostgreSQL 검색
  - approved/current Source·Snapshot·Member·Knowledge Index 재검증
  - exact rational RRF와 deterministic UTF-8 tie-break
- `ai_worker/tasks/rag/evidence_rank_fusion.py`
  - `rrf-rank-fusion@1`의 유일한 계산 구현
- `ai_worker/tasks/rag/evidence_gate.py`
  - 기존 synthetic/provisional semantic Gate 회귀 정본
  - reranker receipt를 필수로 요구하므로 RET-H production runtime에 직접 끼워 맞추지 않음
- `backend/app/models/rag_runtime.py`
  - `AiJobExecutionContext`, pinned Bundle/Manifest/Guard reference
- `backend/app/models/rag_evidence.py`
  - Citation/Evidence 기존 모델. 이번 PR에서 공개 Citation schema로 확장하지 않음
- `ai_worker/tasks/evaluation/runner.py`, `retrieval_metrics.py`
  - 기존 Evaluation lifecycle, Recall@5, MRR, nDCG@5, Precision@5, No-hit metric
- `evals/retrieval/cases/rag-natural-language-retrieval-dev-v1/`
  - #273의 비식별 DEV 60개

## 4. 핵심 설계 결정

### 4.1 두 단계 persistence lifecycle

`retrieval_run.status`의 `RUNNING`을 실제 crash recovery 상태로 보존하면서 child row 집합을 원자 저장하려면 두 개의
짧은 transaction을 사용한다.

1. `begin_run` transaction
   - pinned `AiJobExecutionContext`와 최소 1개 Identification member를 확인한다.
   - 같은 `(job_id, node_id)` Run을 row lock으로 조회한다.
   - 없으면 `RUNNING`을 생성하고 commit한다.
   - terminal Run이면 저장된 receipt를 재검증한 뒤 동일 요청의 재시도에는 기존 결과를 반환한다.
2. read-only search transaction
   - 기존 `PostgresqlEvidenceSearchAdapter.search()`를 그대로 호출한다.
3. `finalize_run` transaction
   - Run을 `FOR UPDATE`로 잠근다.
   - 전체 signal, hit와 logical selection을 한 transaction에서 insert한다.
   - manifest hash와 row 수를 재계산해 요청 결과와 exact-match한다.
   - 마지막 문장으로 Run을 terminal status로 갱신하고 commit한다.

`finalize_run`이 실패하면 child row와 terminal update가 모두 rollback되어 Run은 `RUNNING`으로 남는다. 재시도는
기존 child row를 부분 보존하지 않고 같은 request identity로 다시 finalize한다. stale `RUNNING` 회수 정책은 기존
Worker attempt/lease 정책이 소유하며 새 독립 retry scheduler를 만들지 않는다.

### 4.2 selection은 별도 테이블이 아니라 `retrieval_hit`의 상태다

정규 물리 목표는 `retrieval_run`, `retrieval_signal`, `retrieval_hit` 세 테이블이다. 별도
`retrieval_selection` 테이블을 만들지 않는다.

- `retrieval_signal`: Exact, Trigram, FTS, Lexical-fused, Dense의 원 신호와 순위
- `retrieval_hit`: Hybrid RRF의 한 candidate당 한 행
- `retrieval_hit.final_rank`: reranker가 없는 RET-H에서는 RRF 순서를 그대로 보존
- `retrieval_hit.selected=true`: pre-gate Top 5 중 Evidence Gate를 통과해 context로 전달된 행

Gate가 중간 순위를 제외해도 rank 6 이후를 당겨 채우거나 순위를 다시 매기지 않는다. 따라서 selected 행의
`final_rank`에는 의도적인 간격이 있을 수 있다.

이 구조에서 “Run/signal/hit/selection 원자 저장”은 terminal finalize transaction에 세 논리 집합이 모두 포함된다는
뜻이다.

### 4.3 Production search output은 signal을 명시적으로 반환한다

현재 `ProductionSearchHit`만으로는 Trigram/FTS raw rank를 손실 없이 저장할 수 없다. Adapter 밖에서 SQL 순위를
재구성하지 않도록 `evidence_search.py`에 다음 production DTO를 추가한다.

```python
class ProductionSearchMethod(StrEnum):
    EXACT = "EXACT"
    TRIGRAM = "TRIGRAM"
    FTS = "FTS"
    LEXICAL = "LEXICAL"
    DENSE = "DENSE"


@dataclass(frozen=True, slots=True)
class ProductionSearchSignal:
    provenance: ProductionEvidenceProvenance
    method: ProductionSearchMethod
    raw_rank: int
    observed_score: str
```

`EvidenceSearchSuccess`에 `signals: tuple[ProductionSearchSignal, ...]`을 추가한다. 정렬은
`method -> raw_rank -> StableCoordinate UTF-8`로 고정한다. 기존 `lexical_hits`, `dense_hits`, `hybrid_hits`는
호환을 위해 유지한다. score는 이미 승인된 canonical decimal 문자열만 전달한다.

현재 adapter는 dense가 활성화돼도 lexical SQL을 항상 실행하므로 실제 `RET-D`를 측정할 수 없다. 다음 execution
mode를 `VersionedEvidenceRetrievalConfiguration`의 canonical hash에 포함한다.

```python
class RetrievalExecutionMode(StrEnum):
    LEXICAL_ONLY = "LEXICAL_ONLY"
    DENSE_ONLY = "DENSE_ONLY"
    HYBRID_RRF = "HYBRID_RRF"
```

- `LEXICAL_ONLY`: Exact/Trigram/FTS와 lexical fusion만 실행하고 query embedding을 거부한다.
- `DENSE_ONLY`: lexical SQL을 실행하지 않고 query embedding과 dense SQL만 실행한다.
- `HYBRID_RRF`: lexical과 dense를 모두 실행한 뒤 기존 `rrf-rank-fusion@1`을 호출한다.

기본값은 기존 Production 동작을 보존하는 `HYBRID_RRF`다. mode/config/hash 불일치는
`RETRIEVAL_CONFIG_INVALID`로 닫는다. Evaluation은 이 mode를 바꿀 뿐 별도 ranking 구현을 갖지 않는다.

`evidence_search.py`에는 query 전용 receipt 검증을 유지한다. 별도 `text_embedding.py`에는 query와 corpus가 같은
모델 실행 경계를 공유하도록 다음 provider-neutral port와 fail-closed 결과를 추가한다. Provider SDK 예외나 응답
본문은 orchestration 경계를 넘기지 않는다.

```python
class TextEmbeddingFailureReason(StrEnum):
    CONFIGURATION_MISMATCH = "CONFIGURATION_MISMATCH"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    RESPONSE_INVALID = "RESPONSE_INVALID"


@dataclass(frozen=True, slots=True)
class TextEmbeddingSuccess:
    embedding: SensitiveVector
    adapter_artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class TextEmbeddingFailure:
    reason: TextEmbeddingFailureReason


class TextEmbeddingPort(Protocol):
    async def embed(
        self,
        text: SensitiveText,
        *,
        model_ref: str,
        model_version: str,
        dimension: int,
    ) -> TextEmbeddingSuccess | TextEmbeddingFailure: ...
```

Runtime과 Evaluation은 Index가 고정한 model ref/version/dimension으로 이 port를 호출한다. 모델이나 provider를
임의 변경하지 않는다. #178의 고정 값은 `model_ref=openai:text-embedding-3-large`,
`model_version=text-embedding-3-large`, `dimension=1536`, `adapter_artifact_ref=openai-text-embedding-adapter@1`이다.
Adapter 요청에도 `dimensions=1536`을 명시하고 응답 vector 길이가 정확히 1536인지 검증한다. 이 identity와 출력
dimension을 증명하지 못하면 Dense 실행을 허용하지 않는다. 예기치 않은 adapter 예외도 raw message 없이
`DEPENDENCY_ERROR`로 변환한다.
Runtime orchestration은 성공 결과에 현재 query fingerprint를 결속해 기존 `QueryEmbeddingReceipt`를 만들고 Search
port에 전달한다. Corpus bootstrap은 같은 성공 결과의 vector와 adapter ref를 기존 Knowledge Index builder receipt에
결속한다. 이로써 query receipt 의미를 corpus 생성에 오용하지 않으면서 모델 실행은 하나의 concrete adapter를 쓴다.

### 4.4 RET-H production receipt를 provisional reranker receipt와 분리한다

기존 `EvidenceGateRetrievalReceipt`는 rerank configuration과 input/output hash를 필수로 요구한다. RET-H에서 이
타입을 사용하면 존재하지 않는 reranker 실행을 위조하게 된다.

새 production runtime 모듈은 실행 내용 receipt와 Application persistence receipt를 분리한다. Evaluation은 환자용
Application 테이블에 Run을 만들지 않으므로 `ProductionSearchReceipt`를 소비하고, Runtime만
`PersistedRetrievalRunReceipt`를 추가로 만든다.

```python
class RetrievalVariant(StrEnum):
    RET_L = "RET-L"
    RET_D = "RET-D"
    RET_H = "RET-H"


class RetrievalExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"


@dataclass(frozen=True, slots=True)
class ProductionSearchReceipt:
    artifact_ref: ImmutableArtifactRef
    variant: RetrievalVariant
    retrieval_execution_status: RetrievalExecutionStatus
    diagnostic_code: str
    query_fingerprint: QueryFingerprint
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    retrieval_config_ref: ImmutableArtifactRef
    adapter_artifact_ref: ImmutableArtifactRef
    query_embedding_sha256: str | None
    signal_manifest_sha256: str
    hit_manifest_sha256: str
    selection_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class PersistedRetrievalRunReceipt:
    artifact_ref: ImmutableArtifactRef
    retrieval_run_id: UUID
    job_id: UUID
    node_id: str
    search_receipt_ref: ImmutableArtifactRef
```

artifact hash는 RFC 8785 JCS projection으로 계산한다. raw 질문, embedding vector, chunk text, exception message는
projection에 포함하지 않는다. `query_embedding_sha256`은 RET-L에서 `None`이고 RET-D/RET-H에서는 기존
`canonical_embedding_sha256`의 IEEE-754 binary32 projection으로 계산한다. 같은 model ref가 다른 출력을 낸 경우를
관찰할 수 있게 하되 vector 자체는 저장하지 않는다.

### 4.5 `hybrid_retrieve`는 Graph-independent canonical node callable이다

현재 저장소에는 실제 LangGraph 조립체가 없고 LangGraph dependency도 없다. PR 1은 새 dependency나 빈 Graph를
만들지 않는다. 대신 #180이 그대로 감쌀 수 있는 async callable을 제공한다.

```python
HYBRID_RETRIEVE_NODE_ID = "hybrid_retrieve"
EVIDENCE_GATE_NODE_ID = "evidence_gate"


async def execute_hybrid_retrieve(
    request: HybridRetrieveRequest,
    *,
    search_port: EvidenceSearchPort,
    text_embedding_port: TextEmbeddingPort | None,
    run_store: RetrievalRunStorePort,
    eligibility_verifier: ProductionEvidenceEligibilityVerifierPort,
) -> HybridRetrieveOutcome:
    ...
```

내부의 pure orchestration 함수 `execute_production_retrieval()`은 Search와 production Gate를 실행해
`ProductionSearchReceipt`를 만든다. `execute_hybrid_retrieve()`는 Runtime 전용 wrapper로서 begin/finalize persistence와
`PersistedRetrievalRunReceipt`를 추가한다. Evaluation은 전자만 호출하고 Application `retrieval_run`에는 쓰지 않는다.

이 함수는 다음만 수행한다.

1. pinned Runtime/Index/Source binding 검증
2. `begin_run`
3. pre-search eligibility 검증으로 stale/inactive/conflict binding을 검색 전에 차단
4. RET-D/RET-H이면 Index identity와 일치하는 query embedding receipt 생성; RET-L이면 port 호출 없이 진행
5. Production search 호출
6. RET-H candidate Top 30에서 pre-gate Top 5를 고정
7. post-search eligibility 검증으로 provenance/locator/content binding 재검증
8. terminal finalize transaction 안에서 Context·Source·Member currentness를 다시 검사하고 Run을 저장
9. `PersistedRetrievalRunReceipt`와 gate-passed selection 반환

실제 LangGraph가 #180에서 추가될 때 node wrapper는 이 함수를 호출하고 state delta만 변환한다. Graph wrapper가 검색,
RRF, Gate 또는 persistence를 재구현하지 않는다.

### 4.6 Evidence Gate의 이번 책임

PR 1의 production Gate는 검색 candidate가 context에 들어갈 자격을 다시 검증한다.

- pinned Bundle의 Knowledge Index와 exact-match
- allowed Source Snapshot/Member 부분집합
- Source·Endpoint·Operation active/approved 상태
- Snapshot current/freshness receipt
- `source_version`, `canonical_checksum`, content hash exact-match
- locator가 같은 Snapshot Member와 exact-match
- authoritative conflict origin이 현재 Job/Source/Operation/version에 결속된 경우 차단
- pre-gate Top 5의 부분집합, 최대 5개, 중복 stable coordinate 없음, 원래 `final_rank`의 엄격한 오름차순

Gate는 pre-search binding 검증과 post-search selection 검증으로 나뉜다. 검색과 terminal 저장 사이의 상태 변경을
막기 위해 `finalize_run`도 같은 검증 조건을 write transaction 안에서 다시 조회한다. 이전 read-only verification
receipt만 신뢰해 저장하지 않는다.

Gate는 검색 점수를 의료적 지지 또는 상충 판정으로 해석하지 않는다. authoritative conflict origin이 없으면 과거
conflict event만으로 `CONFLICTED`를 추정하지 않는다. Claim-level semantic support와 Citation authorization은 #180의
후속 validator/finalizer가 소유한다.

Gate 결과는 다음으로 제한한다.

```text
SUCCEEDED / ELIGIBLE
NO_RESULT / INSUFFICIENT
NO_RESULT / STALE
NO_RESULT / CONFLICTED    # exact-bound authoritative origin이 있을 때만
DEPENDENCY_ERROR / INELIGIBLE
VALIDATION_ERROR / INVALID_BINDING
```

검색 `SUCCEEDED/NO_HITS`는 Retrieval 성공이지만 Gate `NO_RESULT/INSUFFICIENT`로 변환된다. Retrieval status를
Safety status에 그대로 복사하지 않는다.

## 5. PR 1: Retrieval Runtime Core

### 5.1 예상 파일 경계

새 파일:

- `ai_worker/tasks/rag/retrieval_runtime.py`
  - production receipt, request/outcome, canonical node IDs, orchestration
- `ai_worker/tasks/rag/retrieval_run.py`
  - begin/finalize persistence request·receipt와 `RetrievalRunStorePort`
- `ai_worker/tasks/rag/text_embedding.py`
  - provider-neutral embedding port와 redacted fail-closed result
- `ai_worker/adapters/openai_text_embedding.py`
  - `text-embedding-3-large`, 1536차원 concrete adapter; timeout와 provider 오류를 안정 reason으로 변환
- `ai_worker/tasks/rag/production_evidence_gate.py`
  - RET-H eligibility Gate와 verifier port
- `ai_worker/adapters/postgresql_evidence_eligibility.py`
  - pinned Source/Snapshot/Member/currentness/locator의 concrete pre/post verifier
- `ai_worker/adapters/sqlalchemy_retrieval_run.py`
  - Worker-facing async persistence port 구현
- `backend/app/repositories/rag_retrieval_repository.py`
  - 동일 SQLAlchemy model을 사용하는 Backend transaction/read support
- `backend/app/models/rag_retrieval.py`
  - Retrieval 전용 ORM model만 소유
- `backend/alembic/versions/178c2d3e4f50_create_retrieval_run_tables.py`
- `ai_worker/tests/rag/test_retrieval_runtime.py`
- `ai_worker/tests/rag/test_openai_text_embedding.py`
- `ai_worker/tests/rag/test_production_evidence_gate.py`
- `backend/app/tests/rag/test_rag_retrieval_repository.py`
- `tests/integration/rag/test_retrieval_run_postgresql.py`
- `tests/contract/rag/test_retrieval_run_contract.py`
- `docs/contracts/proposed/post-mvp-1/retrieval-run-v1.md`

수정 파일:

- `ai_worker/tasks/rag/evidence_search.py`
  - `ProductionSearchSignal`과 `EvidenceSearchSuccess.signals`
- `ai_worker/adapters/postgresql_evidence_search.py`
  - SQL subsearch rank를 signal DTO로 보존
- `ai_worker/core/config.py`
  - `OPENAI_EMBEDDING_MODEL=text-embedding-3-large`, `OPENAI_EMBEDDING_DIMENSION=1536`; Retrieval 활성 시 exact 검증
- `backend/app/models/__init__.py`
- `docs/contracts/README.md`
- `docs/data-schema.md`
- `docs/testing.md`
- `docs/testing/knowledge-evidence-index-178.md`

### 5.2 물리 schema

실제 FK 타입은 참조 대상의 병합된 물리 타입을 따른다. 현재 `rag_knowledge_index.id`와 Application ID는
`UUIDChar`/`CHAR(36)`이므로 target 문서의 추상 `UUID index_version_id`를 그대로 복사하지 않는다.

`retrieval_run`:

- `id CHAR(36) PK`
- `job_id CHAR(36) NOT NULL FK ai_job.id`
- `execution_context_id CHAR(36) NOT NULL`
- `prescription_version_id CHAR(36) NOT NULL`
- `runtime_release_bundle_id CHAR(36) NOT NULL`
- `runtime_release_bundle_manifest_hash CHAR(64) NOT NULL`
- `runtime_execution_manifest_id CHAR(36) NOT NULL`
- `runtime_execution_manifest_hash CHAR(64) NOT NULL`
- `runtime_guard_decision_ref VARCHAR(255) NOT NULL`
- `knowledge_index_id CHAR(36) NOT NULL FK rag_knowledge_index.id`
- `node_id VARCHAR(80) NOT NULL`, P0에서 `hybrid_retrieve`
- `variant VARCHAR(20) NOT NULL`, `RET-L|RET-D|RET-H`
- `query_digest_algorithm VARCHAR(80) NOT NULL`
- `query_digest_key_version VARCHAR(80) NOT NULL`
- `query_digest CHAR(64) NOT NULL`
- `filter_snapshot JSONB NOT NULL`; 승인된 코드·상태·opaque ID만 허용하고 질문·자유서술 금지
- `filter_snapshot_hash CHAR(64) NOT NULL`
- `source_manifest_hash CHAR(64) NOT NULL`
- `retrieval_configuration_hash CHAR(64) NOT NULL`
- nullable `query_embedding_sha256 CHAR(64)`; RET-L은 NULL, RET-D/RET-H는 NOT NULL이라는 교차 필드 규칙을 Python이 검증
- `lexical_limit`, `dense_limit`, `hybrid_limit`, `final_k` positive integer
- `status VARCHAR(30) NOT NULL`
- nullable `diagnostic_code`, `error_code`, `search_receipt_hash`, `receipt_hash`
- `started_at NOT NULL`, nullable `completed_at`
- Unique `(job_id, node_id)`

`retrieval_signal`:

- `retrieval_run_id CHAR(36) FK ON DELETE CASCADE`
- `knowledge_chunk_id CHAR(36) FK knowledge_chunk.id`
- `retrieval_method VARCHAR(20)`
- `raw_rank INTEGER > 0`
- `raw_score NUMERIC(38,18)`
- `score_projection_version VARCHAR(80)`
- Unique `(retrieval_run_id, retrieval_method, knowledge_chunk_id)`
- Unique `(retrieval_run_id, retrieval_method, raw_rank)`
- Primary key `(retrieval_run_id, retrieval_method, knowledge_chunk_id)`

`retrieval_hit`:

- `retrieval_run_id CHAR(36) FK ON DELETE CASCADE`
- `knowledge_chunk_id CHAR(36) FK knowledge_chunk.id`
- nullable `lexical_rank`, nullable `dense_rank`
- `rrf_rank INTEGER > 0`
- `rrf_score NUMERIC(38,18)` as observational projection only
- `rrf_score_numerator` canonical integer string
- `rrf_score_denominator` canonical positive integer string
- nullable `rerank_score`; RET-H에서는 항상 NULL
- `final_rank INTEGER > 0`; RET-H에서는 모든 hit에 `rrf_rank`와 같은 값을 저장
- `selected BOOLEAN NOT NULL`
- `selected=true`는 pre-gate Top 5 중 Gate를 통과한 행에만 설정하며 rank 6 이후를 채워 넣지 않음
- Unique `(retrieval_run_id, knowledge_chunk_id)`
- Unique `(retrieval_run_id, rrf_rank)`
- Unique `(retrieval_run_id, final_rank)`
- Primary key `(retrieval_run_id, knowledge_chunk_id)`

Source/Snapshot/Member/locator/content identity는 immutable `rag_knowledge_index_member`와
`rag_source_snapshot_member`를 통해 재구성한다. 같은 provenance를 `retrieval_hit`에 다시 복제해 서로 다른 값이
생길 수 있는 이중 정본을 만들지 않는다. Run의 `source_manifest_hash`와 receipt의 hit/selection manifest hash가
실행 시점 결속을 보존한다.

DB는 형식·FK·unique·check만 강제한다. 업무 상태 전이, manifest 재계산, selected Top 5 검증은 Python
Service/Repository가 수행한다. trigger, RLS, stored function을 추가하지 않는다.

### 5.3 실패와 idempotency

- malformed request/config/binding/hash mismatch -> Retrieval `VALIDATION_ERROR`
- DB/search/embedding dependency 실패 -> Retrieval `DEPENDENCY_ERROR`
- 정상 무검색 -> Retrieval `SUCCEEDED` + `NO_HITS`; Gate `NO_RESULT/INSUFFICIENT`
- stale Source -> Retrieval candidate는 공개되지 않으며 Gate `NO_RESULT/STALE`
- locator/content/member mismatch -> `VALIDATION_ERROR/INVALID_BINDING`
- 같은 terminal Run 재시도 -> stored receipt와 request identity가 같으면 기존 결과 반환
- 같은 `(job_id,node_id)`에 다른 query/config/index identity -> fail-closed conflict
- 어떤 오류에도 raw query, chunk text, vector, Provider body, exception message를 일반 로그/DLQ에 쓰지 않음

## 6. PR 2: Actual Evaluation and AWS Smoke

### 6.1 actual retrieval adapter

새 `ActualRetrievalEvaluationAdapter`는 Evaluation의 sync `EvaluationAdapter`를 억지로 event loop 안에서 실행하지
않는다. 기존 runner에 async adapter 실행 경계를 추가하되 replay adapter 호환은 유지한다.

```python
class AsyncEvaluationAdapter(Protocol):
    async def execute(self, request: AdapterRequest) -> CaseResult: ...
```

registry는 sync/async adapter를 명시적으로 구분하고 runner의 async entrypoint가 둘을 호출한다. `asyncio.run()`을
adapter 내부에서 중첩 호출하지 않는다.

Actual adapter는 variant별로 같은 search port를 다음처럼 구성한다.

- `RET-L`: lexical configuration 활성, dense configuration 없음, lexical Top 5
- `RET-D`: lexical signal을 결과 선택에 사용하지 않고 dense configuration만 활성, dense Top 5
- `RET-H`: lexical + dense + `rrf-rank-fusion@1`, Gate 통과 Top 5

SQL ranking이나 RRF를 Evaluation 코드에서 재구현하지 않는다. `ProductionSearchReceipt`에서 Evaluation bridge ID를
생성하며 Application `retrieval_run`·`retrieval_signal`·`retrieval_hit` 테이블에는 평가 실행을 저장하지 않는다.

#### DEV Knowledge Index bootstrap

실제 평가 전에 `evals/retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json`의
문장 100개를 production `KnowledgeEvidenceIndex` builder 입력으로 변환한다. 각 statement의 embedding은 query와
동일한 `TextEmbeddingPort` concrete adapter/model ref/version/dimension으로 생성하고, 기존
`SqlAlchemyKnowledgeEvidenceIndexAdapter`를 통해 별도 합성 Source Snapshot에 저장한다.

- 입력은 frozen statement와 content hash뿐이며 evaluation label/gold mapping은 읽지 않는다.
- corpus/configuration/embedding manifest hash와 adapter artifact ref를 bootstrap receipt에 기록한다.
- 기존 동일 code/version의 receipt가 exact-match하면 재사용하고, 하나라도 다르면 덮어쓰지 않고 fail-closed한다.
- embedding vector 자체는 평가 report, 일반 로그, Git artifact에 쓰지 않는다.
- `RET-L/D/H` 세 variant는 정확히 같은 완성 Index ID와 manifest hash를 사용한다.

이 bootstrap 시간은 query retrieval latency에서 제외하고 별도 setup time으로 기록한다. Actual metric의
`latency_ms`는 query embedding 시작부터 Gate 결과 확정까지 측정한다.

### 6.2 결과와 provenance

각 CaseResult는 다음을 포함한다.

- `retrieved_evidence_ids`: variant의 pre-selection ranked IDs
- `selected_evidence_ids`: 최종 Top 5/Gate 결과
- `latency_ms`: monotonic clock으로 측정한 end-to-end retrieval latency
- `actual_retrieval_invocation=true`
- Dataset/Gold mapping version/hash
- Knowledge Index code/version/configuration hash
- retrieval configuration hash
- adapter artifact ref
- RET-D/RET-H query embedding SHA-256
- git revision
- Evaluation Run/Case ID와 `ProductionSearchReceipt` hash
- `ACTUAL_RETRIEVAL_DEV` source marker

latency 보고서는 최소 count, min, median, p95, max를 기록한다. 품질 metric은 기존
`retrieval_metrics.py`의 Recall@5, Precision@5, MRR, nDCG@5, No-hit Rate를 재사용한다. 승인되지 않은 threshold로
PASS를 생성하지 않으며 성공한 DEV 결과의 decision은 threshold 판정 대상이 아니라는 뜻의 `NOT_APPLICABLE`로
유지한다. 실행 오류나 필수 provenance 누락은 기존 runner 규칙에 따라 decision을 비워 둔다.

### 6.3 #273 DEV 범위

- 공개 저장소에는 현재 존재하는 DEV 60개만 실행한다.
- HOLDOUT 40개의 본문을 생성·복사·로그·Issue에 게시하지 않는다.
- 실패 Case는 비민감 Case ID와 안정 reason code만 `failures.jsonl`에 기록한다.
- 같은 dataset/index/config/commit으로 2회 실행해 semantic artifact hash 또는 허용된 runtime-field 제외 hash를
  비교한다.

### 6.4 AWS synthetic smoke

기존 배포 방식이 저장소에서 확인된 뒤 그 경계를 재사용한다. 새 서버나 상시 환경을 만들지 않는다.

Smoke는 다음을 한 번 검증한다.

1. 배포된 ai-worker image/commit 식별
2. 합성 Knowledge Index와 비식별 query 사용
3. `RET-H` 실제 PostgreSQL search 호출
4. Retrieval Run terminal row와 signal/hit/selected row 수 검증
5. Evidence Gate success와 stale/locator mismatch fail-closed 각 1건
6. raw query sentinel이 일반 로그, Stream, quarantine, DLQ에 없는지 검사
7. p50/p95가 아닌 단일 smoke latency를 증빙에 기록하고 DEV benchmark와 혼동하지 않음

Production flag와 active Bundle pointer는 변경하지 않는다. `PUBLIC_TRACK_F=false`를 유지한다.

### 6.5 예상 파일 경계

새 파일:

- `ai_worker/tasks/evaluation/actual_retrieval.py`
- `ai_worker/tasks/evaluation/actual_retrieval_index.py`
- `ai_worker/tests/evaluation/test_actual_retrieval.py`
- `ai_worker/tests/evaluation/test_actual_retrieval_index.py`
- `tests/integration/rag/test_actual_retrieval_evaluation.py`
- `evals/configs/rag-natural-language-retrieval-dev-ret-l-v1.execution.json`
- `evals/configs/rag-natural-language-retrieval-dev-ret-d-v1.execution.json`
- `evals/configs/rag-natural-language-retrieval-dev-ret-h-v1.execution.json`
- `backend/app/release_validation/ret_h_synthetic_smoke.py`
- `docs/testing/issue-178-ret-h-completion.md`

수정 파일:

- `ai_worker/tasks/evaluation/runner.py`
- `ai_worker/tasks/evaluation/config.py`
- `ai_worker/tasks/evaluation/schema_registry.py`와 해당 schema version
- `ai_worker/tests/evaluation/`의 runner/config/reporter 회귀 테스트
- `evals/README.md`
- `docs/contracts/README.md`
- `docs/data-schema.md`
- `docs/testing.md`
- Issue #178과 #273의 stale 설명/증빙

PR #558 병합 후 schema version과 파일 구조가 달라졌다면 새 parallel schema를 만들지 않고 병합된 Set 1.4 확장 규칙을
따른다.

## 7. 두 PR의 검증 기준

### PR 1

```bash
uv run pytest ai_worker/tests/rag/test_evidence_search.py \
  ai_worker/tests/rag/test_retrieval_runtime.py \
  ai_worker/tests/rag/test_production_evidence_gate.py -q
uv run pytest backend/app/tests/rag/test_rag_retrieval_repository.py -q
uv run pytest tests/integration/rag/test_postgresql_evidence_search.py \
  tests/integration/rag/test_retrieval_run_postgresql.py -q
uv run pytest tests/contract/rag/test_retrieval_run_contract.py -q
uv run ruff check ai_worker/tasks/rag ai_worker/adapters backend/app/models \
  backend/app/repositories ai_worker/tests/rag backend/app/tests/rag tests/integration/rag tests/contract/rag
uv run mypy ai_worker/tasks/rag ai_worker/adapters backend/app/repositories
LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
  UV_CACHE_DIR=/private/tmp/ah178_uv_cache \
  uv run python scripts/ci/verify_database_head.py --heads-only
git diff --check
bash scripts/ci/run_test.sh
```

### PR 2

```bash
uv run pytest ai_worker/tests/evaluation/test_actual_retrieval.py -q
uv run pytest tests/integration/rag/test_actual_retrieval_evaluation.py -q
uv run pytest ai_worker/tests/evaluation ai_worker/tests/rag -q
uv run ruff check ai_worker/tasks/evaluation ai_worker/tests/evaluation tests/integration/rag
uv run mypy ai_worker/tasks/evaluation ai_worker/tasks/rag
git diff --check
bash scripts/ci/run_test.sh
```

AWS smoke는 사용한 commit/image, synthetic fixture, 명령, 시작/종료 시각, terminal status, row count, latency,
민감정보 sentinel 결과를 문서에 남긴다. 실행하지 못하면 PASS로 기록하지 않고 `AWS_SMOKE_NOT_EXECUTED`로 남긴다.

## 8. 리뷰와 병합 전략

### PR 1

- 구현 담당: 정현우
- 단일 책임 리뷰어: Issue #178에 기록된 송은영
- 리뷰 범위: DB schema, transaction, idempotency, Source/Bundle binding, Retrieval/Evidence Gate fail-closed
- Product/Safety 의견은 specialist evidence로 첨부하되 두 번째 필수 PR reviewer로 만들지 않음

### PR 2

- 구현 담당: 정현우
- 단일 책임 리뷰어: Issue #273에 기록된 권가빈
- 리뷰 범위: Dataset/Gold/metric/latency/provenance, DEV 해석 한계, AWS smoke evidence

PR 2 개발은 PR 1의 interface가 고정되면 시작할 수 있지만 base는 PR 1 merge 후 최신 `develop`으로 정리한다. 두 PR을
하나로 squash하거나 evaluation 결과를 PR 1에 다시 섞지 않는다.

## 9. 완료 및 비완료 주장

두 PR이 병합되고 검증 증빙이 첨부되면 다음을 주장할 수 있다.

- Production PostgreSQL RET-H가 versioned Retrieval Run을 생성한다.
- signal/hit/selection terminal set이 원자적으로 저장된다.
- canonical `hybrid_retrieve`와 `evidence_gate` callable이 fail-closed로 연결된다.
- 실제 DEV 60개에서 RET-L/D/H 품질과 latency가 재현된다.
- AWS 합성 환경에서 한 번의 end-to-end retrieval smoke가 통과한다.

다음은 주장하지 않는다.

- RET-HR 또는 reranker 완료
- 전체 #180 LangGraph/Generator/Citation/Release Gate 완료
- HOLDOUT PASS 또는 임상적 유효성
- Production 공개 가능
- 외부 의료·약학·Source·Privacy·Safety 승인 완료

Issue #178을 닫을 때 RET-HR 체크 항목은 별도 후속 Issue 링크와 `Deferred` 근거를 명시한다. 전체 #180이 미완료인
상태에서는 “RAG-16 완료”가 아니라 “#180이 소비할 retrieval node contract/runtime core 완료”로 기록한다.

## 10. Gemini 실행 규칙

Gemini에게 이 설계와 후속 implementation plan을 함께 제공한다. Gemini는 각 PR에서 다음 규칙을 지킨다.

1. 기존 계약, 모델, migration, test pattern을 먼저 읽고 파일명을 추측하지 않는다.
2. TDD로 실패 테스트를 먼저 만들고 최소 구현 후 전체 relevant suite를 실행한다.
3. SQL ranking, RRF, metric 계산을 다른 계층에 복제하지 않는다.
4. raw 질문·Source 원문·embedding·Provider body·exception message를 저장하거나 로그에 남기지 않는다.
5. RET-HR, 새 모델, 새 dependency, Graph framework, public API/flag를 추가하지 않는다.
6. trigger, RLS, stored procedure/function으로 business rule을 구현하지 않는다.
7. 사용자 소유 untracked/dirty 파일을 수정·삭제·되돌리지 않는다.
8. 계약 불일치나 scope 확대를 발견하면 임의 해석하지 않고 정확한 파일·필드·영향을 보고한다.
9. 각 PR 종료 시 변경 파일, migration head, 실행한 검사, 실패/미실행 검사, 남은 위험을 기록한다.
