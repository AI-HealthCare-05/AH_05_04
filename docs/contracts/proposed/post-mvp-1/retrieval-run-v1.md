# Retrieval Run 및 Evidence Gate Runtime Core v1 (#178)

- **상태**: Proposed (구현 브랜치 `feat/178-ret-h-runtime` 검증 중, target/current 미승격)
- **책임자**: 구현 정현우, 단일 책임 리뷰어 송은영 (@phina-io)
- **상위 근거**: Issue #178, `docs/designs/ceohwj/issue-178-ret-h-completion-design.md`, Decision 2026-09-08 `production-evidence-retrieval-contract-divergence`

---

## 1. 목적과 범위

본 문서는 Issue #178 Knowledge Evidence Search(Exact/Trigram/FTS + Dense -> deterministic RRF)의 실행 결과를 versioned Retrieval Run으로 영속화하고, Production Evidence Gate의 사전/사후 적격성을 검증하여 `hybrid_retrieve` canonical runtime callable로 완성하는 공유 계약을 정의한다.

### 포함 범위
1. `retrieval_run`, `retrieval_signal`, `retrieval_hit` 3개 테이블의 물리 스키마와 제약 조건.
2. 2-phase 트랜잭션 수명 주기 (`begin_run` -> read-only search -> `finalize_run`).
3. 터미널 원자성 및 멱등성 식별 (`job_id`, `node_id`).
4. Canonical JCS 해시 계산 및 검증 (Manifest, Configuration, Filter, Search Receipt).
5. Pre-gate Top 5 중 Gate를 통과한 결과만 `selected=true`로 표시하며, 결측 순위를 뒤에서 당겨 채우지 않는 non-backfill 정책.
6. 민감 정보(원문 쿼리 텍스트, chunk 원문 텍스트, 민감 벡터 배열, Provider 에러 본문 등)의 영속화 제외 및 마스킹.
7. OpenAITextEmbeddingAdapter (`text-embedding-3-large`, 1536차원, fail-closed).

### 제외 범위
1. `RET-HR` (Cross-encoder reranker) 및 reranker receipt 생성 금지.
2. Actual DEV 평가 실행 및 AWS synthetic smoke (PR 2 범위).
3. LangGraph 종속성 및 Graph wrapper 조립 (#180 범위).
4. Citation 공개 및 Track F 배포 게이트 해제 (`PUBLIC_TRACK_F=false` 유지).

---

## 2. 수명 주기와 2-Phase 트랜잭션

### 2.1 트랜잭션 흐름
1. **`begin_run` 트랜잭션**:
   - `(job_id, node_id)`로 기존 row를 FOR UPDATE로 조회한다.
   - 기존 완료(`COMPLETED`) Run이 존재하고 요청 식별자(쿼리 digest, 설정 해시, index ID)가 일치하면 기존 receipt를 반환(멱등 재현).
   - 기존 완료 Run의 식별자가 불일치하면 `CONFLICT`로 거부(fail-closed).
   - 존재하지 않으면 `status='RUNNING'`으로 row를 생성하고 commit한다.

2. **Read-only Search 트랜잭션**:
   - `REPEATABLE READ READ ONLY` 격리 수준에서 `PostgresqlEvidenceSearchAdapter.search()`를 호출한다.
   - Lexical(Exact, Trigram, FTS) 및 Dense(pgvector Cosine) 검색을 수행하고 deterministic RRF(`rrf-rank-fusion@1`)를 계산한다.
   - raw 신호(`EXACT`, `TRIGRAM`, `FTS`, `LEXICAL`, `DENSE`)를 수집한다.

3. **`finalize_run` 트랜잭션**:
   - `retrieval_run`을 FOR UPDATE로 잠근다.
   - 단일 트랜잭션 내에서 모든 `retrieval_signal` 및 `retrieval_hit` row를 삽입한다.
   - Pre-gate Top 5 중 Evidence Gate 통과 항목만 `selected=true`로 표시한다.
   - 해시 및 row 수를 재계산해 검증한 뒤 `retrieval_run`을 terminal status(`COMPLETED` 또는 `FAILED`)로 업데이트하고 commit한다.
   - 트랜잭션 실패 시 child row와 status 갱신이 모두 롤백되어 `RUNNING` 상태로 남으며 재시도가 가능하다.

---

## 3. 물리 스키마 사양

### 3.1 `retrieval_run`
- `id CHAR(36) PK` (UUID)
- `job_id CHAR(36) NOT NULL FK ai_job.id ON DELETE CASCADE`
- `execution_context_id CHAR(36) NOT NULL`
- `prescription_version_id CHAR(36) NOT NULL`
- `runtime_release_bundle_id CHAR(36) NOT NULL`
- `runtime_release_bundle_manifest_hash CHAR(64) NOT NULL`
- `runtime_execution_manifest_id CHAR(36) NOT NULL`
- `runtime_execution_manifest_hash CHAR(64) NOT NULL`
- `runtime_guard_decision_ref VARCHAR(255) NOT NULL`
- `knowledge_index_id CHAR(36) NOT NULL FK rag_knowledge_index.id ON DELETE RESTRICT`
- `node_id VARCHAR(80) NOT NULL` (P0: `hybrid_retrieve`)
- `variant VARCHAR(20) NOT NULL` (`RET-L`, `RET-D`, `RET-H`)
- `query_digest_algorithm VARCHAR(80) NOT NULL`
- `query_digest_key_version VARCHAR(80) NOT NULL`
- `query_digest CHAR(64) NOT NULL`
- `filter_snapshot JSONB NOT NULL` (진단/승인 코드만 허용, 자유 서술 금지)
- `filter_snapshot_hash CHAR(64) NOT NULL`
- `source_manifest_hash CHAR(64) NOT NULL`
- `retrieval_configuration_hash CHAR(64) NOT NULL`
- `query_embedding_sha256 CHAR(64) NULL` (RET-L은 NULL, RET-D/H는 NOT NULL)
- `lexical_limit INTEGER NOT NULL CHECK > 0`
- `dense_limit INTEGER NOT NULL CHECK > 0`
- `hybrid_limit INTEGER NOT NULL CHECK > 0`
- `final_k INTEGER NOT NULL CHECK > 0`
- `status VARCHAR(30) NOT NULL` (`RUNNING`, `COMPLETED`, `FAILED`)
- `diagnostic_code VARCHAR(80) NULL`
- `error_code VARCHAR(80) NULL`
- `search_receipt_hash CHAR(64) NULL`
- `receipt_hash CHAR(64) NULL`
- `started_at TIMESTAMPTZ NOT NULL`
- `completed_at TIMESTAMPTZ NULL`
- Unique: `(job_id, node_id)`

### 3.2 `retrieval_signal`
- `retrieval_run_id CHAR(36) NOT NULL FK retrieval_run.id ON DELETE CASCADE`
- `retrieval_method VARCHAR(20) NOT NULL` (`EXACT`, `TRIGRAM`, `FTS`, `LEXICAL`, `DENSE`)
- `knowledge_chunk_id CHAR(36) NOT NULL FK knowledge_chunk.id ON DELETE RESTRICT`
- `raw_rank INTEGER NOT NULL CHECK > 0`
- `raw_score NUMERIC(38,18) NOT NULL`
- `score_projection_version VARCHAR(80) NOT NULL`
- Primary Key: `(retrieval_run_id, retrieval_method, knowledge_chunk_id)`
- Unique: `(retrieval_run_id, retrieval_method, raw_rank)`

### 3.3 `retrieval_hit`
- `retrieval_run_id CHAR(36) NOT NULL FK retrieval_run.id ON DELETE CASCADE`
- `knowledge_chunk_id CHAR(36) NOT NULL FK knowledge_chunk.id ON DELETE RESTRICT`
- `lexical_rank INTEGER NULL CHECK > 0`
- `dense_rank INTEGER NULL CHECK > 0`
- `rrf_rank INTEGER NOT NULL CHECK > 0`
- `rrf_score NUMERIC(38,18) NOT NULL`
- `rrf_score_numerator VARCHAR(64) NOT NULL`
- `rrf_score_denominator VARCHAR(64) NOT NULL`
- `rerank_score NUMERIC(38,18) NULL` (RET-H에서는 항상 NULL)
- `final_rank INTEGER NOT NULL CHECK > 0`
- `selected BOOLEAN NOT NULL`
- Primary Key: `(retrieval_run_id, knowledge_chunk_id)`
- Unique: `(retrieval_run_id, rrf_rank)`
- Unique: `(retrieval_run_id, final_rank)`

---

## 4. Production Evidence Gate 계약

1. **Pre-search 검증**:
   - Pinned Bundle과 Knowledge Index 일치 여부 확인.
   - Pinned Source Snapshot / Member가 `ACTIVE` / `APPROVED` 상태인지 확인.
   - Authoritative conflict origin이 현재 컨텍스트와 상충하는지 차단.

2. **Post-search 검증**:
   - Pre-gate candidate Top 5에 대해 Source version, canonical checksum, content hash, locator 일치 재확인.
   - Gate를 통과한 행만 `selected=true`로 지정 (최대 5개).
   - Gate 탈락 행이 발생해도 rank 6 이후를 당겨 채우지 않음 (`selected` 행들의 `final_rank`에 간격 발생 허용).

---

## 5. 민감정보 보호 및 Citation 제한

- 원문 query text, chunk 텍스트 전문, embedding raw vector, OpenAI API 에러 전문은 테이블에 저장하지 않는다.
- 본 PR 1 구현은 Citation 발행 권한(`RagCitationReleaseStatus`)을 부여하지 않으며, `PUBLIC_TRACK_F=false` 배포 게이트를 유지한다.
