# Knowledge Evidence Index Foundation Design

## Status and authority

- Status: requester-approved decomposition and implementation design; designated reviewer approval remains required
  before merge.
- Tracking: prerequisite subproject for Issue #178. This work does not close #178.
- Governing baseline: `PD-315-20260908`, `PD-362`, and the approved-but-not-current
  `rag-runtime-v1` and `rag-source-ingestion-v1` targets.
- Implementation owner: 정현우 (`@ceohwj`).
- Required reviewers: 권가빈 (`@hazelnutflavoured`) for Evidence/Scope/Safety, 송은영
  (`@phina-io`) for Backend/DB/Security, and 김지혜 (`@Jye-rookie`) for Source provenance.
- Publication: `PUBLIC_TRACK_F=false` remains unchanged. This foundation is not a production activation or a
  current-contract promotion.

PR #361 shows that the responsible Evidence/Safety reviewer approved the final PD-315 head and that the Source
review findings were resolved before merge. The remaining `Review pending` labels in PD-315 and the RAG Runtime
target are stale document metadata and must be corrected in the implementation PR without claiming that the runtime
itself is implemented.

## Goal

Create the versioned PostgreSQL Knowledge Evidence Index that #178's production Retrieval Adapter can consume. The
index must bind every searchable chunk to one approved Source Snapshot member, a stable runtime identity, canonical
text bytes, a versioned embedding configuration, and independently verifiable corpus and embedding receipts.

## Scope

### Included

- a proposed Knowledge Evidence Index contract and contract index entry;
- an explicit Source Snapshot member model for Endpoint/Operation and captured Artifact origins;
- forward evolution of the existing schema-only `knowledge_document` and `knowledge_chunk` tables;
- immutable Knowledge Index version and member rows;
- pgvector storage for a member embedding under one versioned embedding configuration;
- RFC 8785 JCS corpus-manifest hashing and deterministic embedding-byte hashing;
- an atomic Python Service/Repository write boundary and a read-only retrieval projection;
- migration, model, repository, PostgreSQL integration, hash golden-vector, tamper, and non-leakage tests;
- local PostgreSQL 17 pgvector support and a locked Python SQLAlchemy adapter dependency.

### Excluded

- document acquisition, parsing, chunking policy selection, or an embedding-provider call;
- generated embeddings from real medical or patient content;
- lexical SQL, dense query execution, RRF, rerank, Evidence Gate, Retrieval Run, Composer, or Evaluation Runner;
- Interaction Rule and Lifestyle Guideline evidence;
- approximate HNSW/IVFFlat indexes or performance claims;
- runtime Bundle activation, public Citation, external Source approval, or `PUBLIC_TRACK_F` changes;
- modification or reuse of the OCR Candidate Index.

The implementation uses only de-identified synthetic official-source fixtures. It never stores a patient query,
prescription, OCR text, chat text, credential, or HMAC key.

## Repository constraints

1. Python owns validation, transaction order, and lifecycle decisions. No trigger, RLS policy, stored procedure, or
   user-defined database function is introduced.
2. Existing migration history remains intact. Schema changes use one forward migration whose downgrade refuses to
   drop populated new state.
3. Candidate Index and Knowledge Evidence Index keep separate models, tables, versions, hashes, vectors, ports, and
   tests.
4. Source Snapshot checksum, Source member content hash, chunk content hash, corpus manifest hash, embedding hash,
   and index configuration hash are distinct domains and cannot substitute for one another.
5. Text and vectors never appear in ordinary logs, exception messages, receipts, or test failure snapshots.
6. No current API or public DTO changes. The proposed contract stays under `docs/contracts/proposed/` until the full
   runtime implementation, tests, evidence, and designated approvals justify promotion.
7. The local pgvector dependency is infrastructure for testing and future #178 consumption only. Production remains
   disabled.

## Considered approaches

### Evolve the existing Knowledge tables and add versioned index tables — selected

Keep `knowledge_document` and `knowledge_chunk` as the canonical document/chunk identities, add the missing Source
and canonical-text bindings through a forward migration, and store model-specific vectors in a new index-member
table. This avoids a second competing definition of a Knowledge Chunk while separating stable chunk identity from
replaceable index versions.

The cost is a careful migration over legacy schema-only columns. The migration does not reinterpret existing rows as
approved production evidence. Existing rows remain legacy and cannot enter a production index until every new
provenance field is populated through an explicit reviewed import.

### Add parallel `rag_knowledge_*` document and chunk tables — rejected

This would avoid altering the schema-only tables but create two database meanings for `knowledge_document` and
`knowledge_chunk`, duplicate citation relationships, and require a later cutover with no current consumer benefit.

### Put vectors directly on `knowledge_chunk` — rejected

A chunk can be embedded again under a new model, dimension, normalization, or configuration without changing its
canonical source identity. A single vector column would overwrite history or force the chunk identity to change.

### Keep vectors in the existing external `vector_store_key` boundary — rejected

The approved Local target requires PostgreSQL plus pgvector and a version/hash-reproducible index. An opaque external
point key cannot prove that the selected vector belongs to the pinned corpus and embedding configuration.

## Architecture

### 1. Source Snapshot member

Add `rag_source_snapshot_member` to represent the exact origin within a Source Snapshot. A row has:

```text
id: UUID
source_snapshot_id: UUID
member_kind: ENDPOINT_OPERATION | ARTIFACT
endpoint_id: UUID | null
operation_id: UUID | null
ingestion_artifact_id: UUID | null
locator: nonblank NFC string, max 500 characters
content_sha256: 64 lowercase hexadecimal characters
created_at: timestamp
```

`ENDPOINT_OPERATION` requires `endpoint_id`, allows the Snapshot's matching `operation_id`, and forbids
`ingestion_artifact_id`. `ARTIFACT` requires `ingestion_artifact_id` and forbids endpoint/operation columns. The
repository verifies the full parent chain: Source → Endpoint → Operation → Snapshot or Ingestion Run → Artifact.
Ordinary CHECK/FK/UNIQUE constraints enforce row shape and duplicate prevention; Python verifies cross-table
ownership and exact Snapshot membership in the insertion transaction.

This table belongs to the Source provenance domain. Its model, migration, repository validation, and tests therefore
require the named Source and DB reviewers in the same PR.

### 2. Stable document and chunk identity

Forward-evolve `knowledge_document` with:

```text
record_contract_version: LEGACY_V1 | KNOWLEDGE_EVIDENCE_V1
source_snapshot_member_id: UUID | null during legacy compatibility
external_document_id: string | null during legacy compatibility
document_content_hash: 64-lower-hex | null during legacy compatibility
canonicalization_spec_version: string | null during legacy compatibility
```

Forward-evolve `knowledge_chunk` with:

```text
content_hash: 64-lower-hex | null during legacy compatibility
normalization_version: string | null during legacy compatibility
```

The migration backfills every existing document as `LEGACY_V1`. It makes legacy-only `source_url`, `publisher`,
`document_version`, `embedding_model`, and `vector_store_key` nullable so a `KNOWLEDGE_EVIDENCE_V1` row is not forced
to invent values for the superseded external-vector contract. An ordinary document CHECK requires the complete old
shape for `LEGACY_V1` and the complete new provenance shape for `KNOWLEDGE_EVIDENCE_V1`; the repository additionally
verifies the child chunk shape because a cross-table CHECK is not used. A production unique constraint covers
`source_snapshot_member_id + external_document_id`. No existing row is promoted or reinterpreted by the migration.

The production repository accepts only rows for which every field above is present. It validates
`content_hash == SHA-256(canonical chunk_text UTF-8 bytes)` and never silently normalizes the stored text during
verification. Canonicalization occurs before the repository boundary under the named version.

The stable production Chunk coordinate is:

```text
(source_code, source_version, external_document_id, chunk_index)
```

`content_hash` is not a second identity. The same stable coordinate with a different content hash is a validation
failure, not a new candidate. New `source_version` means a new runtime identity even when text and hashes are equal.

Legacy columns remain readable for `LEGACY_V1` fixtures but are not accepted as production provenance. The migration
does not backfill or infer approval from them.

### 3. Versioned index and members

Add `rag_knowledge_index`:

```text
id: UUID
index_code: nonblank NFC string, max 120 characters
index_version: nonblank NFC string, max 80 characters
corpus_manifest_hash: 64-lower-hex
embedding_manifest_hash: 64-lower-hex
index_configuration_hash: 64-lower-hex
embedding_model_ref: nonblank NFC string, max 255 characters
embedding_model_version: nonblank NFC string, max 80 characters
embedding_dimension: positive integer, maximum 2000
distance_metric: COSINE
member_count: nonnegative integer
created_at: timestamp
```

An index row is a completed immutable artifact. Draft building and external embedding calls are outside the database
transaction and do not create a partially visible index. One transaction inserts the completed index and all
members after validating every receipt. The runtime role receives SELECT only; the dedicated builder repository has
INSERT but no update/delete method.

Add `rag_knowledge_index_member`:

```text
id: UUID
knowledge_index_id: UUID
knowledge_chunk_id: UUID
source_snapshot_id: UUID
source_snapshot_member_id: UUID
source_code: string
source_version: string, max 200 characters
canonical_checksum: 64-lower-hex
external_document_id: string
chunk_index: nonnegative integer
content_hash: 64-lower-hex
embedding: vector
embedding_sha256: 64-lower-hex
member_order: positive integer
created_at: timestamp
```

The repository requires all denormalized identity fields to exact-match the joined Source, Snapshot member,
document, and chunk rows. They are stored in the member so the persisted receipt can be recomputed without treating
mutable joins as the original hash preimage. Unique constraints cover index/member order, index/chunk, and
index/stable coordinate.

The pgvector column uses the unbounded `vector` type because embedding dimension belongs to the versioned index row,
not to a global schema constant. The 2,000-dimension index limit deliberately preserves compatibility with a future
HNSW/IVFFlat `vector` index without choosing one now. The repository validates that every vector has exactly
`embedding_dimension` finite, non-negative-zero float32 values and a nonzero Euclidean norm. The initial adapter uses
exact cosine scans filtered to one index ID; approximate indexes are deferred until measurement and a separate
configuration decision justify them.

### 4. Hash domains

All JSON hashes use the repository's RFC 8785-compatible UTF-16 object-key ordering, reject floats and unsafe JSON
integers, preserve explicit nulls, and apply no implicit Unicode normalization.

`knowledge-evidence-corpus-manifest@1` hashes the JCS array sorted by the UTF-8 bytes of the stable coordinate:

```json
{
  "chunk_index": 0,
  "content_hash": "<64-lower-hex>",
  "external_document_id": "<id>",
  "source_code": "<code>",
  "source_version": "<version>"
}
```

`knowledge-evidence-embedding@1` hashes the exact concatenation:

```text
UTF-8("knowledge-evidence-embedding@1\n")
+ uint32-big-endian(dimension)
+ each finite float encoded once as IEEE-754 binary32 big-endian
```

`knowledge-evidence-embedding-manifest@1` hashes a JCS array containing each stable coordinate and its
`embedding_sha256`, in the same stable order.

`knowledge-evidence-index-configuration@1` hashes the model ref/version, dimension, `COSINE` metric, corpus projection
version, embedding projection version, and both manifest hashes. It never includes raw text or vector values.

Golden tests cover non-BMP object keys, explicit null, stable-coordinate UTF-8 ordering, negative zero and zero-vector
rejection, NaN/infinity rejection, dimension boundaries, and a one-bit vector mutation.

### 5. Application flow

Create one AI Worker build service and one Backend SQLAlchemy repository boundary:

```text
ValidatedSourceSnapshotMember inputs
  → canonical document/chunk validation
  → precomputed embedding artifact validation
  → corpus and embedding receipt calculation
  → repository transaction with parent rows locked in stable UUID order
  → re-verify Source/Snapshot/member/document/chunk bindings
  → insert completed index
  → insert ordered members
  → recompute persisted receipts
  → commit and return opaque KnowledgeIndexReceipt
```

The receipt returns only index ID/code/version, member count, model ref/version, dimension, metric, and the three
hashes. It contains no chunk text, vector, locator, endpoint URL, storage key, or Source body.

Concurrent creation of the same `index_code + index_version` is idempotent only when all three hashes and every
immutable configuration field match. A mismatch fails with a fixed safe conflict reason. Transaction failure leaves
no index or member rows.

### 6. Local infrastructure

Use the official version-pinned server image `pgvector/pgvector:0.8.6-pg17-bookworm` for the local PostgreSQL service and
enable `vector` through the forward migration. Keep `pg_trgm` enabled independently. Add and lock `pgvector==0.5.0`
for SQLAlchemy/Psycopg vector conversion. The official adapters support SQLAlchemy and async Psycopg registration;
runtime assembly registers the vector type on the existing async engine without enabling SQL echo.

This changes local development and integration-test infrastructure only. Production compose, credentials, network,
backup, and activation are excluded.

## Failure behavior

- Missing or mismatched Source/Snapshot/member provenance: validation failure; no partial insert.
- Snapshot version/checksum binding mismatch: validation failure, never `CONFLICTED` Evidence.
- Duplicate stable coordinate with different content hash: validation failure.
- Wrong vector dimension, non-finite value, or changed embedding hash: validation failure.
- Existing version with different receipts/configuration: deterministic version conflict.
- PostgreSQL/pgvector dependency failure: dependency error with a fixed safe reason.
- Any exception surface contains identifiers and fixed codes only; text, vectors, Source bodies, URLs, and storage
  coordinates are excluded.

These are build-time outcomes, not Retrieval, Evidence, Safety, or Release states.

## Verification

The implementation PR must provide fresh evidence for:

1. pure hash and stable-identity unit tests;
2. model and repository unit tests, including mutation and rollback cases;
3. Alembic upgrade/downgrade tests and a populated-state downgrade refusal;
4. real PostgreSQL 17 + pgvector integration tests for atomic build, exact round trip, dimension validation,
   concurrency, and persisted receipt recomputation;
5. proof that Candidate Index tables and ports are not imported or queried;
6. sentinel tests proving text/vector/query material does not enter receipts, logs, exceptions, or ordinary audit;
7. existing Retrieval/Gate tests;
8. Ruff, formatting, Mypy, repository test script, and applicable synthetic RAG evaluations.

No Recall@5, latency, production quality, current-runtime, or publication claim is made by this foundation.

## Follow-up boundary for #178

#178 may consume this foundation only after its contract and implementation reviewers approve the exact Index
Receipt and Source member binding. The next #178 slice then owns:

- PostgreSQL Exact/Trigram/`ts_rank_cd` and dense cosine search;
- rank-based exact-rational RRF and its fraction receipt;
- versioned rerank;
- Retrieval Receipt/Run persistence;
- the production Evidence eligibility verifier and Gate origin binding;
- Evaluation bridge and protected Runner connection.

The Retrieval implementation must not reconstruct the index manifest, accept legacy `vector_store_key` as evidence,
or infer Source approval solely from the presence of an index member.
