# Issue #178 RET-H Runtime Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete PR 1 of Issue #178 by adding the production embedding boundary, true lexical/dense/hybrid execution modes, versioned Retrieval Run persistence, the canonical `hybrid_retrieve` callable, and the structural production Evidence Gate.

**Architecture:** Keep PostgreSQL search and deterministic RRF as the only ranking implementation. The runtime opens a short transaction to create or recover a `RUNNING` run, performs embedding/search and evidence eligibility checks outside that write transaction, then atomically writes signals, hits, selection state, and the terminal receipt in a second transaction. The callable remains independent of LangGraph so Issue #180 can wrap it without duplicating retrieval behavior.

**Tech Stack:** Python 3.13, asyncio, OpenAI Python SDK, SQLAlchemy 2 async, PostgreSQL/pgvector, Alembic, Pydantic Settings, pytest, Ruff, mypy.

**Spec:** `docs/designs/ceohwj/issue-178-ret-h-completion-design.md`

## Global Constraints

- Start from the latest `origin/develop` containing PR #558 merge `eaa1f39634e1604bf7893ebce19dcec9e85465af` and PR #560 merge `b9c3d0cfb392695fb984567e4c319ffa54db9e9c`.
- Implement PR 1 only. Do not add actual DEV evaluation, AWS smoke execution, RET-HR, reranking, LangGraph, Generator, Citation finalization, or `gpt-4o` calls.
- Retrieval embeddings use `text-embedding-3-large` with an explicit `dimensions=1536`; reject every configuration or response dimension mismatch.
- `gpt-4o` belongs to the later Issue #180 Generator and must not be called from this PR.
- Preserve `PUBLIC_TRACK_F=false` and the existing default `PROTECTED_RETRIEVAL_ENABLED=false`.
- Never persist or log raw query text, chunk text, embedding vectors, provider response bodies, credentials, or exception messages.
- Do not add triggers, RLS policies, stored procedures, user-defined database functions, or new dependencies.
- Do not modify or commit `.claude/`, `skills-lock.json`, or `docs/designs/ceohwj/issue-178-local-cross-encoder-reranker-design.md`.
- Before creating the migration, run `uv run python scripts/ci/verify_database_head.py --heads-only` and use the reported single head as `down_revision`; do not copy a stale revision from the design.
- Use tests first for every task and keep commits limited to the files listed for that task.

---

### Task 1: Freeze the embedding and retrieval-mode contracts

**Files:**
- Create: `ai_worker/tasks/rag/text_embedding.py`
- Create: `ai_worker/adapters/openai_text_embedding.py`
- Create: `ai_worker/tests/rag/test_openai_text_embedding.py`
- Modify: `ai_worker/core/config.py`
- Modify: `ai_worker/tasks/rag/evidence_search.py`
- Modify: `ai_worker/tests/rag/test_evidence_search.py`
- Modify: `envs/example.local.env`
- Modify: `envs/example.prod.env`
- Modify: `infra/docker/docker-compose.prod.yml`

**Interfaces:**
- Produces: `TextEmbeddingPort.embed(text, *, model_ref, model_version, dimension)` returning `TextEmbeddingSuccess | TextEmbeddingFailure`.
- Produces: `RetrievalExecutionMode` with `LEXICAL_ONLY`, `DENSE_ONLY`, and `HYBRID_RRF`.
- Produces: `ProductionSearchMethod` and `ProductionSearchSignal`.
- Extends: `EvidenceSearchSuccess.signals` and the canonical hash of `VersionedEvidenceRetrievalConfiguration`.

- [ ] **Step 1: Write failing contract tests**

Add tests asserting all of the following exact behavior:

```python
def test_retrieval_configuration_hash_changes_with_execution_mode() -> None:
    lexical = make_configuration(mode=RetrievalExecutionMode.LEXICAL_ONLY)
    dense = make_configuration(mode=RetrievalExecutionMode.DENSE_ONLY)
    hybrid = make_configuration(mode=RetrievalExecutionMode.HYBRID_RRF)
    assert len({lexical.compute_canonical_hash(), dense.compute_canonical_hash(), hybrid.compute_canonical_hash()}) == 3


def test_dense_only_requires_dense_config_and_embedding_adapter_ref() -> None:
    outcome = validate_retrieval_configuration(
        make_configuration(
            mode=RetrievalExecutionMode.DENSE_ONLY,
            dense_config=None,
            expected_query_embedding_adapter_ref=None,
        )
    )
    assert outcome == EvidenceSearchFailure(EvidenceSearchFailureReason.RETRIEVAL_CONFIG_INVALID)
```

In `test_openai_text_embedding.py`, use a fake async SDK object and assert that the adapter sends model `text-embedding-3-large`, `dimensions=1536`, and `encoding_format="float"`; converts a 1536-element finite non-zero response into `SensitiveVector`; rejects 3072, zero, NaN, reordered/multiple response items, and a response model mismatch; and returns stable failure enums without provider messages.

- [ ] **Step 2: Run the tests and confirm the missing types fail**

Run:

```bash
uv run pytest ai_worker/tests/rag/test_evidence_search.py ai_worker/tests/rag/test_openai_text_embedding.py -q
```

Expected: failure because the new enums, port, adapter, configuration fields, and signals do not exist.

- [ ] **Step 3: Implement the provider-neutral port and OpenAI adapter**

Define these public types in `text_embedding.py`:

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
    ) -> TextEmbeddingSuccess | TextEmbeddingFailure:
        raise NotImplementedError
```

The concrete adapter must accept an injected async OpenAI client for tests, call `client.embeddings.create`, never retry internally, require the four fixed identity values from the spec, and catch SDK/transport exceptions only at its boundary. Do not stringify caught exceptions.

Add these settings with exact defaults and validation active only when protected retrieval is enabled:

```python
OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-large"
OPENAI_EMBEDDING_DIMENSION: int = Field(default=1536, ge=1, le=2000)
OPENAI_EMBEDDING_TIMEOUT_SECONDS: float = Field(default=20.0, gt=0, allow_inf_nan=False)
```

- [ ] **Step 4: Implement execution-mode and signal contracts**

Add `execution_mode` to `VersionedEvidenceRetrievalConfiguration.compute_canonical_hash()`. Enforce:

- `LEXICAL_ONLY`: `dense_config` and expected embedding adapter ref are both absent, and the request embedding receipt is absent.
- `DENSE_ONLY`: dense config, expected adapter ref, and matching query embedding receipt are present.
- `HYBRID_RRF`: lexical config plus the same dense requirements are present.

Add `ProductionSearchSignal` fields exactly as specified in the design and default `EvidenceSearchSuccess.signals` only if doing so preserves existing construction compatibility; otherwise update every constructor in the same commit.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest ai_worker/tests/rag/test_evidence_search.py ai_worker/tests/rag/test_openai_text_embedding.py -q
uv run ruff check ai_worker/tasks/rag/text_embedding.py ai_worker/adapters/openai_text_embedding.py ai_worker/core/config.py ai_worker/tasks/rag/evidence_search.py ai_worker/tests/rag/test_openai_text_embedding.py ai_worker/tests/rag/test_evidence_search.py
uv run mypy ai_worker/tasks/rag/text_embedding.py ai_worker/adapters/openai_text_embedding.py ai_worker/tasks/rag/evidence_search.py
```

Expected: all pass.

- [ ] **Step 6: Commit the contract boundary**

```bash
git add ai_worker/tasks/rag/text_embedding.py ai_worker/adapters/openai_text_embedding.py ai_worker/tests/rag/test_openai_text_embedding.py ai_worker/core/config.py ai_worker/tasks/rag/evidence_search.py ai_worker/tests/rag/test_evidence_search.py envs/example.local.env envs/example.prod.env infra/docker/docker-compose.prod.yml
git commit -m "feat(rag): add versioned embedding and retrieval modes"
```

### Task 2: Make PostgreSQL search expose true mode-specific signals

**Files:**
- Modify: `ai_worker/adapters/postgresql_evidence_search.py`
- Modify: `tests/integration/rag/test_postgresql_evidence_search.py`

**Interfaces:**
- Consumes: `RetrievalExecutionMode`, `ProductionSearchMethod`, and `ProductionSearchSignal` from Task 1.
- Produces: one `EvidenceSearchSuccess` whose signal order is `method -> raw_rank -> StableCoordinate UTF-8`.

- [ ] **Step 1: Add integration tests proving each SQL path is skipped**

Create three tests named `test_lexical_only_does_not_execute_dense_sql`,
`test_dense_only_does_not_execute_lexical_sql`, and
`test_hybrid_returns_all_raw_and_fused_signals_in_canonical_order` using the existing `database` fixture.

Instrument through the existing adapter/session seam rather than matching SQL strings. Assert that `DENSE_ONLY.hybrid_hits` are the dense order with `fusion_rank` 1..N and that no lexical signals exist. Assert that `LEXICAL_ONLY` rejects a supplied query embedding receipt before opening the search transaction.

- [ ] **Step 2: Run the new tests and verify failure**

```bash
uv run pytest tests/integration/rag/test_postgresql_evidence_search.py -k 'lexical_only or dense_only or raw_and_fused' -q
```

Expected: failure because the adapter currently always performs lexical work and does not return raw signals.

- [ ] **Step 3: Split mode orchestration without copying ranking logic**

In `PostgresqlEvidenceSearchAdapter.search()` branch before executing subqueries. Reuse the existing exact/trigram/FTS, lexical fusion, dense, and `rrf-rank-fusion@1` helpers. For lexical-only and dense-only, adapt their existing ranked hits into `ProductionSearchHit`; do not invoke RRF with fabricated empty sides. Preserve the existing `REPEATABLE READ READ ONLY` transaction and integrity checks.

Emit raw `EXACT`, `TRIGRAM`, `FTS`, `LEXICAL`, and `DENSE` signals at the point each ranked result is known. Use the existing canonical decimal formatter and never recompute SQL rank outside the adapter.

- [ ] **Step 4: Run the full search test set and static checks**

```bash
uv run pytest ai_worker/tests/rag/test_evidence_search.py tests/integration/rag/test_postgresql_evidence_search.py -q
uv run ruff check ai_worker/adapters/postgresql_evidence_search.py tests/integration/rag/test_postgresql_evidence_search.py
uv run mypy ai_worker/adapters/postgresql_evidence_search.py
```

Expected: all pass, including existing deterministic RRF tests.

- [ ] **Step 5: Commit search modes**

```bash
git add ai_worker/adapters/postgresql_evidence_search.py tests/integration/rag/test_postgresql_evidence_search.py
git commit -m "feat(rag): expose deterministic retrieval signals"
```

### Task 3: Add Retrieval Run schema and contract

**Files:**
- Create: `backend/app/models/rag_retrieval.py`
- Create: `backend/alembic/versions/178c2d3e4f50_create_retrieval_run_tables.py`
- Create: `tests/contract/rag/test_retrieval_run_contract.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/tests/migration/test_postgresql_schema.py`
- Create: `docs/contracts/proposed/post-mvp-1/retrieval-run-v1.md`
- Modify: `docs/contracts/README.md`
- Modify: `docs/data-schema.md`

**Interfaces:**
- Produces: `RetrievalRun`, `RetrievalSignal`, and `RetrievalHit` ORM models and matching Alembic tables.
- Produces: status/variant/method enums shared by repository validation.

- [ ] **Step 1: Verify the current migration head**

```bash
LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run python scripts/ci/verify_database_head.py --heads-only
```

Expected: exactly one head. Set the new migration's `down_revision` to that output.

- [ ] **Step 2: Write failing model and contract tests**

Assert exact table names, PKs, FKs, unique constraints, check constraints, nullable fields, and enum values from design section 5.2. Include negative tests for duplicate `(job_id, node_id)`, duplicate signal rank, duplicate RRF/final rank, non-positive ranks, malformed hashes, and a selected hit outside pre-gate rank 1..5 at the Python validation boundary.

- [ ] **Step 3: Implement models and migration**

Use `UUIDChar()` for application IDs, ordinary constraints only, and forward-only creation. `retrieval_signal` and `retrieval_hit` use the composite primary keys from the spec. Keep source provenance normalized through existing immutable index/member relations; do not duplicate locator or source identity columns in `retrieval_hit`.

The migration downgrade may delete these new tables because this PR creates them and they have no earlier production data contract. Drop children before the parent and do not alter prior migrations.

- [ ] **Step 4: Document the proposed shared contract**

The contract must specify the two-transaction lifecycle, terminal atomicity, idempotency identity, hash projections, selected-without-backfill behavior, sensitive-data exclusions, and the fact that PR 1 does not authorize Citation publication. Add one and only one index entry in `docs/contracts/README.md`; do not promote the contract into `current/`.

- [ ] **Step 5: Run schema and contract verification**

```bash
uv run pytest backend/app/tests/migration/test_postgresql_schema.py tests/contract/rag/test_retrieval_run_contract.py -q
uv run python scripts/ci/check_database_logic.py
uv run python scripts/ci/check_protected_table_writes.py
uv run python scripts/ci/verify_database_head.py --heads-only
```

Expected: all pass and exactly one Alembic head remains.

- [ ] **Step 6: Commit schema and contract**

```bash
git add backend/app/models/rag_retrieval.py backend/app/models/__init__.py backend/alembic/versions tests/contract/rag/test_retrieval_run_contract.py backend/app/tests/migration/test_postgresql_schema.py docs/contracts/proposed/post-mvp-1/retrieval-run-v1.md docs/contracts/README.md docs/data-schema.md
git commit -m "feat(rag): add versioned retrieval run schema"
```

### Task 4: Implement idempotent begin/finalize persistence

**Files:**
- Create: `ai_worker/tasks/rag/retrieval_run.py`
- Create: `ai_worker/adapters/sqlalchemy_retrieval_run.py`
- Create: `backend/app/repositories/rag_retrieval_repository.py`
- Create: `backend/app/tests/rag/test_rag_retrieval_repository.py`
- Create: `tests/integration/rag/test_retrieval_run_postgresql.py`

**Interfaces:**
- Produces: pure persistence DTOs and `RetrievalRunStorePort.begin_run(request)` / `RetrievalRunStorePort.finalize_run(request)` in `ai_worker/tasks/rag/retrieval_run.py`.
- Produces: persisted receipt loading for same-identity terminal retries.
- Consumes: ORM models from Task 3 and canonical signal/hit DTOs from Tasks 1–2.

- [ ] **Step 1: Write failing repository tests**

Cover these state transitions:

```text
absent + matching request -> create RUNNING
RUNNING + same identity -> recover same run id
terminal + same identity -> return verified stored receipt
RUNNING/terminal + different query/config/index identity -> conflict
finalize success -> insert every child then terminal status in one commit
finalize injected failure -> no child rows and RUNNING remains
two concurrent begin calls -> one run id and one parent row
```

Also mutate stored manifest/hash/count fields independently and assert reload fails closed rather than self-validating corrupt rows.

- [ ] **Step 2: Run repository tests and verify missing implementation failure**

```bash
uv run pytest backend/app/tests/rag/test_rag_retrieval_repository.py tests/integration/rag/test_retrieval_run_postgresql.py -q
```

- [ ] **Step 3: Implement `begin_run`**

Define the immutable begin/finalize request and outcome unions plus `RetrievalRunStorePort` in the pure task module; the task module must not import SQLAlchemy or Backend models. Use a short write transaction in the adapter. Verify `AiJobExecutionContext`, at least one matching `AiJobExecutionIdentification`, bundle/manifest hashes, pinned Knowledge Index, query digest identity, and configuration identity. Lock an existing `(job_id, node_id)` row with `FOR UPDATE`. Do not hold this transaction open during provider or search calls.

- [ ] **Step 4: Implement `finalize_run`**

Lock the parent row, reject non-`RUNNING` state unless the exact stored terminal receipt is being replayed, revalidate Context/Source/Member currentness in the same transaction, insert all signal and hit rows, recompute row counts and JCS manifests from the insert payload, then issue the terminal parent update as the last statement. On every exception, rollback without preserving child rows.

- [ ] **Step 5: Run repository and migration tests**

```bash
uv run pytest backend/app/tests/rag/test_rag_retrieval_repository.py tests/integration/rag/test_retrieval_run_postgresql.py backend/app/tests/migration/test_postgresql_schema.py -q
uv run ruff check ai_worker/tasks/rag/retrieval_run.py ai_worker/adapters/sqlalchemy_retrieval_run.py backend/app/repositories/rag_retrieval_repository.py backend/app/tests/rag/test_rag_retrieval_repository.py tests/integration/rag/test_retrieval_run_postgresql.py
uv run mypy ai_worker/tasks/rag/retrieval_run.py ai_worker/adapters/sqlalchemy_retrieval_run.py backend/app/repositories/rag_retrieval_repository.py
```

Expected: all pass.

- [ ] **Step 6: Commit persistence**

```bash
git add ai_worker/tasks/rag/retrieval_run.py ai_worker/adapters/sqlalchemy_retrieval_run.py backend/app/repositories/rag_retrieval_repository.py backend/app/tests/rag/test_rag_retrieval_repository.py tests/integration/rag/test_retrieval_run_postgresql.py
git commit -m "feat(rag): persist retrieval runs atomically"
```

### Task 5: Implement the production Evidence Gate

**Files:**
- Create: `ai_worker/tasks/rag/production_evidence_gate.py`
- Create: `ai_worker/adapters/postgresql_evidence_eligibility.py`
- Create: `ai_worker/tests/rag/test_production_evidence_gate.py`
- Modify: `tests/integration/rag/test_retrieval_run_postgresql.py`

**Interfaces:**
- Produces: `ProductionEvidenceEligibilityVerifierPort.pre_search(request)` and `.post_search(request, hits)`.
- Produces: gate outcomes `SUCCEEDED/ELIGIBLE`, `NO_RESULT/INSUFFICIENT`, `NO_RESULT/STALE`, `NO_RESULT/CONFLICTED`, `DEPENDENCY_ERROR/INELIGIBLE`, and `VALIDATION_ERROR/INVALID_BINDING`.

- [ ] **Step 1: Write failing pure gate tests**

Assert that the gate accepts only an ordered subset of pre-gate ranks 1..5, preserves original ranks with gaps, never promotes rank 6+, rejects duplicate coordinates, and maps successful zero-hit retrieval to `NO_RESULT/INSUFFICIENT`. Add separate tests for stale source, exact-bound authoritative conflict, locator mismatch, content hash mismatch, and dependency failure.

- [ ] **Step 2: Run and confirm failure**

```bash
uv run pytest ai_worker/tests/rag/test_production_evidence_gate.py -q
```

- [ ] **Step 3: Implement pure gate types and logic**

Keep the gate structural. It may verify bundle/index/source/snapshot/member/locator/content identity and exact-bound conflict origin. It must not infer claim support, medical correctness, Citation authorization, or conflict from historical events without the authoritative bound origin.

- [ ] **Step 4: Implement PostgreSQL pre/post eligibility checks**

Pre-search must distinguish stale/inactive/conflicting sources from genuine no-hit results. Post-search must exact-match every returned provenance field against the pinned immutable rows. Expose only stable reason enums; never return SQL or exception text.

- [ ] **Step 5: Add TOCTOU integration tests**

Change an eligible Source/Member state after post-search verification but before `finalize_run`; assert finalization rolls back children and leaves the run non-terminal. Restore the fixture in test teardown using the approved test owner connection.

- [ ] **Step 6: Run focused verification and commit**

```bash
uv run pytest ai_worker/tests/rag/test_production_evidence_gate.py tests/integration/rag/test_retrieval_run_postgresql.py -q
uv run ruff check ai_worker/tasks/rag/production_evidence_gate.py ai_worker/adapters/postgresql_evidence_eligibility.py ai_worker/tests/rag/test_production_evidence_gate.py
uv run mypy ai_worker/tasks/rag/production_evidence_gate.py ai_worker/adapters/postgresql_evidence_eligibility.py
git add ai_worker/tasks/rag/production_evidence_gate.py ai_worker/adapters/postgresql_evidence_eligibility.py ai_worker/tests/rag/test_production_evidence_gate.py tests/integration/rag/test_retrieval_run_postgresql.py
git commit -m "feat(rag): add production evidence eligibility gate"
```

### Task 6: Compose the canonical `hybrid_retrieve` runtime callable

**Files:**
- Create: `ai_worker/tasks/rag/retrieval_runtime.py`
- Create: `ai_worker/tests/rag/test_retrieval_runtime.py`
- Modify: `ai_worker/tasks/rag/__init__.py` only if that package already exports public task interfaces

**Interfaces:**
- Produces: `execute_production_retrieval(request, *, search_port, text_embedding_port, eligibility_verifier) -> ProductionRetrievalOutcome` for persistence-free evaluation reuse.
- Produces: `execute_hybrid_retrieve(request, *, search_port, text_embedding_port, run_store, eligibility_verifier) -> HybridRetrieveOutcome` for production begin/finalize persistence.
- Produces: `ProductionSearchReceipt` and `PersistedRetrievalRunReceipt` with canonical JCS hashes.

- [ ] **Step 1: Write orchestration-order tests with fakes**

Record calls from fake ports and assert this exact successful order:

```text
begin_run -> pre_search -> embed (RET-D/H only) -> search -> post_search -> finalize_run
```

Assert `RET-L` never calls the embedding port; terminal same-identity replay performs no provider/search call; every failure stops downstream calls; and neither output nor recorded fake log includes query text or vector values.

- [ ] **Step 2: Write receipt hash tests**

Add a golden JCS vector containing non-BMP text only in non-sensitive artifact identifiers. Assert `query_embedding_sha256` is null for RET-L and equals `canonical_embedding_sha256` for RET-D/H. Assert changing any config/index/adapter/signal/hit/selection field changes the corresponding receipt hash.

- [ ] **Step 3: Run and confirm missing callable failure**

```bash
uv run pytest ai_worker/tests/rag/test_retrieval_runtime.py -q
```

- [ ] **Step 4: Implement pure and persisted orchestration**

Set constants exactly:

```python
HYBRID_RETRIEVE_NODE_ID = "hybrid_retrieve"
EVIDENCE_GATE_NODE_ID = "evidence_gate"
```

`execute_production_retrieval` performs validation, optional embedding, search, pre-gate Top 5 fixation, and gate evaluation without Application Retrieval Run writes. `execute_hybrid_retrieve` wraps it with `begin_run` and `finalize_run`. Catch unexpected dependency exceptions at the owning adapter boundary; the task layer handles only declared failure unions.

- [ ] **Step 5: Run all PR 1 focused tests**

```bash
uv run pytest ai_worker/tests/rag/test_evidence_search.py ai_worker/tests/rag/test_openai_text_embedding.py ai_worker/tests/rag/test_retrieval_runtime.py ai_worker/tests/rag/test_production_evidence_gate.py backend/app/tests/rag/test_rag_retrieval_repository.py tests/integration/rag/test_postgresql_evidence_search.py tests/integration/rag/test_retrieval_run_postgresql.py tests/contract/rag/test_retrieval_run_contract.py -q
uv run ruff check ai_worker/tasks/rag ai_worker/adapters backend/app/models backend/app/repositories ai_worker/tests/rag backend/app/tests/rag tests/integration/rag tests/contract/rag
uv run mypy ai_worker/tasks/rag ai_worker/adapters backend/app/repositories
```

Expected: all pass.

- [ ] **Step 6: Commit runtime composition**

```bash
git add ai_worker/tasks/rag/retrieval_runtime.py ai_worker/tests/rag/test_retrieval_runtime.py ai_worker/tasks/rag/__init__.py
git commit -m "feat(rag): compose canonical hybrid retrieval runtime"
```

### Task 7: Complete documentation and full verification

**Files:**
- Modify: `docs/testing.md`
- Modify: `docs/testing/knowledge-evidence-index-178.md`
- Create: `docs/testing/issue-178-ret-h-runtime-core.md`
- Modify: `docs/designs/ceohwj/issue-178-ret-h-completion-design.md` only for factual drift discovered during implementation

**Interfaces:**
- Produces: reproducible PR evidence and an explicit handoff boundary for PR 2 and Issue #180.

- [ ] **Step 1: Record implementation evidence**

Document the actual migration revision, merge-base commit, embedding profile, configuration/adapter artifact identifiers, test counts, and any skipped external checks. State that DEV metrics, AWS smoke, RET-HR, LangGraph assembly, Generator, Citation, and publication remain incomplete.

- [ ] **Step 2: Run full repository verification**

```bash
LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 UV_CACHE_DIR=/private/tmp/ah178_uv_cache uv run python scripts/ci/verify_database_head.py --heads-only
git diff --check
bash scripts/ci/run_test.sh
```

Expected: one Alembic head, no diff errors, and the full repository suite passes. If an external dependency prevents a check, record the exact command, stable failure category, and that it was not passed; do not replace it with a weaker claim.

- [ ] **Step 3: Inspect scope before PR creation**

```bash
git status --short
git diff --stat origin/develop...HEAD
git log --oneline origin/develop..HEAD
```

Expected: only PR 1 files and the two handoff documents are present. `.claude/`, `skills-lock.json`, the reranker design, evaluation implementation, AWS execution artifacts, and unrelated branch changes are absent.

- [ ] **Step 4: Commit documentation**

```bash
git add docs/testing.md docs/testing/knowledge-evidence-index-178.md docs/testing/issue-178-ret-h-runtime-core.md docs/designs/ceohwj/issue-178-ret-h-completion-design.md docs/superpowers/plans/2026-09-15-issue-178-ret-h-runtime-core.md
git commit -m "docs(rag): record RET-H runtime verification"
```

- [ ] **Step 5: Create the focused PR**

The PR body must link Issue #178, name 정현우 as implementation owner and 송은영 as the single responsible reviewer, enumerate affected DB/Runtime/Evidence Gate domains, report every executed/skipped check, preserve `PUBLIC_TRACK_F=false`, and state that PR 2 carries actual DEV evaluation and AWS smoke.
