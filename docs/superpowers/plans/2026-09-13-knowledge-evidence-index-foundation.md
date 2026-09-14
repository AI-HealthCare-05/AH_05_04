# Knowledge Evidence Index Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the versioned PostgreSQL Knowledge Evidence Index foundation that #178's production retrieval adapter can consume without treating legacy Knowledge rows or OCR Candidate vectors as medical evidence.

**Architecture:** A pure AI Worker domain module owns stable identities and canonical receipts. A forward Backend migration and SQLAlchemy models evolve legacy Knowledge rows and add Source Snapshot members plus immutable index/member rows. A dedicated AI Worker SQLAlchemy adapter validates persisted Source provenance and writes one complete index atomically.

**Tech Stack:** Python 3.13+, dataclasses, SQLAlchemy asyncio, PostgreSQL 17, pgvector 0.8.6, pgvector-python 0.5.0, Alembic, pytest, Ruff, Mypy.

**Spec:** `docs/superpowers/specs/2026-09-13-knowledge-evidence-index-design.md`

## Global Constraints

- `PUBLIC_TRACK_F=false` remains unchanged and no current-contract promotion is made.
- No trigger, RLS policy, stored procedure, user-defined database function, external provider call, or real medical/patient fixture is added.
- Candidate Index types, tables, vectors, ports, and receipts are not imported or queried.
- Source Snapshot checksum, member hash, chunk hash, corpus hash, embedding hash, and configuration hash remain distinct.
- Raw text and vectors are prohibited from receipts, logs, exceptions, and ordinary audits.
- Existing rows are backfilled only as `LEGACY_V1`; no migration infers production approval.

---

### Task 1: Contract and canonical receipt kernel

**Files:**
- Create: `docs/contracts/proposed/post-mvp-1/knowledge-evidence-index-v1.md`
- Modify: `docs/contracts/README.md`
- Create: `ai_worker/tasks/rag/knowledge_evidence_index.py`
- Create: `ai_worker/tests/rag/test_knowledge_evidence_index.py`

**Interfaces:**
- Consumes: canonical source code/version, Snapshot/member UUIDs, canonical chunk text, precomputed float vectors.
- Produces: `KnowledgeChunkIdentity`, `KnowledgeIndexMemberDraft`, `KnowledgeIndexBuildRequest`, `KnowledgeIndexReceipt`, `KnowledgeEvidenceIndexRepository`, `build_knowledge_evidence_index()`, and canonical hash helpers.

- [ ] **Step 1: Write failing tests for stable identity and hash domains**

```python
def test_corpus_manifest_uses_stable_coordinate_not_vector_or_locator() -> None:
    first = member_draft(locator="endpoint://one", embedding=(1.0, 0.0))
    second = replace(first, locator="endpoint://two", embedding=(0.0, 1.0))
    assert canonical_corpus_manifest_hash((first,)) == canonical_corpus_manifest_hash((second,))

def test_embedding_hash_rejects_negative_zero_and_zero_vector() -> None:
    with pytest.raises(KnowledgeEvidenceIndexValidationError):
        canonical_embedding_sha256((-0.0, 1.0))
    with pytest.raises(KnowledgeEvidenceIndexValidationError):
        canonical_embedding_sha256((0.0, 0.0))
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run pytest ai_worker/tests/rag/test_knowledge_evidence_index.py -q`

Expected: collection fails because `knowledge_evidence_index` does not exist.

- [ ] **Step 3: Implement strict immutable types and hashes**

Implement frozen slot dataclasses, lowercase SHA-256/UUID/NFC validation, UTF-8 stable-coordinate sorting, RFC 8785-compatible JSON through the existing canonical implementation, and `struct.pack(">f", value)` embedding bytes. `KnowledgeEvidenceIndexValidationError` exposes only a fixed reason enum.

```python
class KnowledgeEvidenceIndexFailureReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    SOURCE_BINDING_INVALID = "SOURCE_BINDING_INVALID"
    CONTENT_HASH_MISMATCH = "CONTENT_HASH_MISMATCH"
    EMBEDDING_INVALID = "EMBEDDING_INVALID"
    RECEIPT_MISMATCH = "RECEIPT_MISMATCH"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
```

- [ ] **Step 4: Define the repository Protocol and atomic build service**

```python
class KnowledgeEvidenceIndexRepository(Protocol):
    async def persist_complete_index(
        self, request: KnowledgeIndexBuildRequest, receipt: KnowledgeIndexReceipt
    ) -> KnowledgeIndexReceipt: ...

async def build_knowledge_evidence_index(
    request: KnowledgeIndexBuildRequest,
    *,
    repository: KnowledgeEvidenceIndexRepository,
) -> KnowledgeIndexReceipt:
    expected = create_knowledge_index_receipt(request)
    observed = await repository.persist_complete_index(request, expected)
    if observed != expected:
        raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.RECEIPT_MISMATCH)
    return observed
```

- [ ] **Step 5: Add the proposed contract and index entry**

Document exact fields, projection versions, byte preimages, failure reasons, legacy boundary, non-public status, the single responsible reviewer, and required specialist evidence. Add it only under the Proposed section of `docs/contracts/README.md`.

- [ ] **Step 6: Run unit and static checks**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run pytest ai_worker/tests/rag/test_knowledge_evidence_index.py -q
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run ruff check ai_worker/tasks/rag/knowledge_evidence_index.py ai_worker/tests/rag/test_knowledge_evidence_index.py
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run mypy ai_worker/tasks/rag/knowledge_evidence_index.py
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add docs/contracts/README.md docs/contracts/proposed/post-mvp-1/knowledge-evidence-index-v1.md ai_worker/tasks/rag/knowledge_evidence_index.py ai_worker/tests/rag/test_knowledge_evidence_index.py
git commit -m "✨ feat: #178 Knowledge Evidence Index 계약 기반 추가"
```

### Task 2: pgvector infrastructure and forward schema

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `docker-compose.yml`
- Modify: `backend/app/models/knowledge.py`
- Modify: `backend/app/models/rag_source.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/178a1b2c3d4e_knowledge_evidence_index_foundation.py`
- Create: `backend/app/tests/models/test_knowledge_evidence_index_models.py`
- Modify: `backend/app/tests/migration/test_postgresql_schema.py`

**Interfaces:**
- Consumes: latest Alembic head `e8c41a09d652`.
- Produces: `RagSourceSnapshotMember`, evolved `KnowledgeDocument`/`KnowledgeChunk`, `RagKnowledgeIndex`, `RagKnowledgeIndexMember`, and the `vector` extension.

- [ ] **Step 1: Write failing model metadata tests**

Assert the four models expose the exact columns and that `RagKnowledgeIndexMember.embedding` compiles to unbounded `VECTOR`. Assert the document contract enum is exactly `LEGACY_V1 | KNOWLEDGE_EVIDENCE_V1`.

- [ ] **Step 2: Write failing migration source tests**

Add assertions for:

```python
assert revision.down_revision == "e8c41a09d652"
assert "CREATE EXTENSION IF NOT EXISTS vector" in upgrade_sql
assert "CREATE TRIGGER" not in migration_source
assert "CREATE POLICY" not in migration_source
assert "CREATE FUNCTION" not in migration_source
```

Also test that downgrade aborts when any new Source member/index/member row exists.

- [ ] **Step 3: Run focused tests and confirm RED**

Run: `UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run pytest backend/app/tests/models/test_knowledge_evidence_index_models.py backend/app/tests/migration/test_postgresql_schema.py -q`

Expected: missing models/revision assertions fail.

- [ ] **Step 4: Add pinned pgvector dependencies and local image**

Add `pgvector==0.5.0` to project dependencies, regenerate `uv.lock`, and pin every migration-bearing PostgreSQL
service to `pgvector/pgvector:0.8.6-pg17-bookworm`. Install the extension in the production admin bootstrap before
the restricted Alembic migration role runs; do not activate Track F or broaden application credentials.

- [ ] **Step 5: Implement models and migration**

The migration must:

1. enable `vector` and leave existing `pg_trgm` behavior intact;
2. create `rag_source_snapshot_member`;
3. add `record_contract_version` and nullable production provenance columns to `knowledge_document`;
4. make legacy-only document/chunk columns nullable and backfill existing documents as `LEGACY_V1`;
5. add chunk hash/normalization columns;
6. create `rag_knowledge_index` and `rag_knowledge_index_member` with ordinary FK/UNIQUE/CHECK constraints;
7. reject unsafe downgrade when new rows exist before dropping only this revision's objects.

Use `VECTOR()` for the member embedding and `vector_dims(embedding) BETWEEN 1 AND 2000` as an extension-provided CHECK. Do not encode application transitions in SQL.

- [ ] **Step 6: Run model and migration checks**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run pytest backend/app/tests/models/test_knowledge_evidence_index_models.py backend/app/tests/migration/test_postgresql_schema.py -q
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run ruff check backend/app/models backend/alembic/versions/178a1b2c3d4e_knowledge_evidence_index_foundation.py backend/app/tests/models/test_knowledge_evidence_index_models.py
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock docker-compose.yml backend/app/models backend/alembic/versions/178a1b2c3d4e_knowledge_evidence_index_foundation.py backend/app/tests
git commit -m "🗃️ db: #178 Knowledge Evidence Index 저장 기반 추가"
```

### Task 3: Source Snapshot member append boundary

**Files:**
- Modify: `ai_worker/tasks/rag/source_ingestion/snapshot_lifecycle.py`
- Modify: `ai_worker/adapters/sqlalchemy_source_snapshot_repository.py`
- Modify: `ai_worker/tests/rag/source_ingestion/test_sqlalchemy_snapshot_repository.py`
- Create: `ai_worker/tests/rag/source_ingestion/test_source_snapshot_member.py`

**Interfaces:**
- Consumes: a verified `SnapshotProvenanceReceipt` and either its exact Endpoint/Operation pair or a captured ingestion Artifact.
- Produces: `SourceSnapshotMemberCreate`, `SourceSnapshotMemberReceipt`, and `append_snapshot_member()`.

- [ ] **Step 1: Write failing contract tests for the two member shapes**

Test Endpoint/Operation, Artifact, mixed-shape rejection, nullable operation behavior, locator NFC, content hash, and redacted representation.

- [ ] **Step 2: Write failing SQL adapter tests**

Capture statements and require parent-chain joins plus `FOR UPDATE` before insert. Require an unsealed `PENDING`
Snapshot so the later CURRENT seal freezes membership. Test that an Artifact from another run/Snapshot is rejected
and that a repeated byte-identical member is idempotent.

- [ ] **Step 3: Run tests and confirm RED**

Run: `UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run pytest ai_worker/tests/rag/source_ingestion/test_source_snapshot_member.py ai_worker/tests/rag/source_ingestion/test_sqlalchemy_snapshot_repository.py -q`

- [ ] **Step 4: Implement the append-only member boundary**

Extend the existing Source repository rather than adding a second Source writer. Insert no Source body, endpoint URL, artifact storage key, or raw locator query string into errors. Require the same Snapshot receipt used by the caller to exact-match the locked persisted Source chain.

- [ ] **Step 5: Run focused Source checks**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run pytest ai_worker/tests/rag/source_ingestion/test_source_snapshot_member.py ai_worker/tests/rag/source_ingestion/test_sqlalchemy_snapshot_repository.py -q
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run ruff check ai_worker/tasks/rag/source_ingestion ai_worker/adapters/sqlalchemy_source_snapshot_repository.py ai_worker/tests/rag/source_ingestion
```

- [ ] **Step 6: Commit**

```bash
git add ai_worker/tasks/rag/source_ingestion/snapshot_lifecycle.py ai_worker/adapters/sqlalchemy_source_snapshot_repository.py ai_worker/tests/rag/source_ingestion
git commit -m "✨ feat: #178 Source Snapshot member 결속 추가"
```

### Task 4: Atomic Knowledge Index SQLAlchemy adapter

**Files:**
- Create: `ai_worker/adapters/sqlalchemy_knowledge_evidence_index.py`
- Create: `ai_worker/tests/rag/test_sqlalchemy_knowledge_evidence_index.py`
- Modify: `ai_worker/core/runtime_assembly.py`
- Modify: `ai_worker/tests/core/test_runtime_assembly.py`

**Interfaces:**
- Consumes: `KnowledgeIndexBuildRequest` and expected `KnowledgeIndexReceipt` from Task 1.
- Produces: `SqlAlchemyKnowledgeEvidenceIndexRepository` using the pinned SQLAlchemy `VECTOR` bind/result adapter.

- [ ] **Step 1: Write failing repository statement tests**

Require stable UUID lock order, Source→Endpoint/Operation→Snapshot/member→Document/chunk exact joins, complete member insertion, and persisted receipt recomputation. Assert no Candidate table appears in generated SQL.

- [ ] **Step 2: Write failing behavior tests**

Cover exact idempotent replay, version conflict, content mutation, Snapshot checksum mismatch, vector dimension mismatch, non-finite/zero vector, repository exception normalization, and returned receipt mismatch.

- [ ] **Step 3: Run tests and confirm RED**

Run: `UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run pytest ai_worker/tests/rag/test_sqlalchemy_knowledge_evidence_index.py ai_worker/tests/core/test_runtime_assembly.py -q`

- [ ] **Step 4: Implement the adapter**

Use SQLAlchemy Core table definitions local to the adapter, `async with session.begin()`, `hide_parameters=True`, and the pgvector SQLAlchemy `VECTOR` bind/result adapter without a second raw asyncpg codec. Do not commit in the repository method; the transaction context owns commit/rollback.

- [ ] **Step 5: Implement exact persisted receipt recomputation**

Reload the inserted rows ordered by `member_order`, reconstruct the domain projection without returning chunk text/vector, and compare all three hashes plus model/dimension/metric/member count before commit.

- [ ] **Step 6: Run adapter and type checks**

Run:

```bash
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run pytest ai_worker/tests/rag/test_sqlalchemy_knowledge_evidence_index.py ai_worker/tests/core/test_runtime_assembly.py -q
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run ruff check ai_worker/adapters/sqlalchemy_knowledge_evidence_index.py ai_worker/core/runtime_assembly.py ai_worker/tests/rag/test_sqlalchemy_knowledge_evidence_index.py
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run mypy ai_worker/tasks/rag/knowledge_evidence_index.py ai_worker/adapters/sqlalchemy_knowledge_evidence_index.py
```

- [ ] **Step 7: Commit**

```bash
git add ai_worker/adapters/sqlalchemy_knowledge_evidence_index.py ai_worker/tests/rag/test_sqlalchemy_knowledge_evidence_index.py ai_worker/core/runtime_assembly.py ai_worker/tests/core/test_runtime_assembly.py
git commit -m "✨ feat: #178 Knowledge Evidence Index 저장 adapter 구현"
```

### Task 5: Real PostgreSQL integration and evidence

**Files:**
- Create: `ai_worker/tests/rag/test_knowledge_evidence_index_postgresql.py`
- Modify: `docs/data-schema.md`
- Modify: `docs/testing.md`
- Create: `docs/testing/knowledge-evidence-index-178.md`
- Modify: `docs/superpowers/specs/2026-09-13-knowledge-evidence-index-design.md`

**Interfaces:**
- Consumes: migrated PostgreSQL 17 + pgvector schema and Tasks 1–4.
- Produces: real DB evidence that the foundation is atomic, reproducible, isolated, and non-public.

- [ ] **Step 1: Write real PostgreSQL integration tests**

Test extension presence, legacy row preservation, Source member parent binding, complete index round trip, exact vector round trip, persisted hash recomputation, concurrent same-version replay, conflict rollback, dimension rejection, and populated downgrade refusal.

- [ ] **Step 2: Add non-leakage sentinels**

Use synthetic sentinels for chunk text and vector values. Capture logs and safe exceptions and assert the sentinels occur zero times outside the database values explicitly read by the test.

- [ ] **Step 3: Run the integration tests**

Run: `UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache bash scripts/ci/run_test.sh`

Expected: repository test suite passes with the pgvector-enabled local PostgreSQL service.

- [ ] **Step 4: Update implementation-state documentation**

Document the exact tables, proposed-contract status, migration revision, hash domains, version-pinned pgvector
extension provisioning, the responsible reviewer and specialist evidence, test results, and the remaining #178 Retrieval/Gate/Run/Evaluation work. Keep
PD-315's `Review pending` metadata unchanged until the single responsible reviewer approves and the required
Evidence/Safety and Source specialist evidence is attached; only then may an authorized change align its status
without changing the runtime target's `Not implemented` status.

- [ ] **Step 5: Run all required completion checks**

```bash
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run pytest ai_worker/tests/rag -q
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run pytest ai_worker/tests/evaluation -q
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run ruff check .
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run ruff format . --check
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache uv run mypy backend/app ai_worker
UV_CACHE_DIR=/private/tmp/ah178_index_uv_cache bash scripts/ci/run_test.sh
git diff --check
```

Expected: all pass. If an applicable RAG evaluation remains synthetic-only, record it explicitly without claiming production quality or Recall@5 completion.

- [ ] **Step 6: Commit**

```bash
git add docs ai_worker/tests/rag/test_knowledge_evidence_index_postgresql.py
git commit -m "✅ test: #178 Evidence Index PostgreSQL 통합 증빙 추가"
```
