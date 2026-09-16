from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    SqlAlchemyKnowledgeEvidenceIndexRepository,
)
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import (
    SqlAlchemySourceSnapshotRepository,
)
from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.schemas.provenance_v1 import (
    IndexBridgeEntry,
    IndexBuildReceipt,
    ReviewProvenanceV12,
)
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    SensitiveText,
)
from ai_worker.tasks.rag.evidence_search import (
    SensitiveVector,
)
from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeChunkIdentity,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexMemberDraft,
    SensitiveEvidenceText,
    build_knowledge_evidence_index,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    PRODUCTION_EMBEDDING_DIMENSION,
    PRODUCTION_EMBEDDING_MODEL_REF,
    PRODUCTION_EMBEDDING_MODEL_VERSION,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotProvenanceReceipt,
    SnapshotVerificationStatus,
    SourceSnapshotMemberCreate,
    SourceSnapshotMemberKind,
)
from ai_worker.tasks.rag.text_embedding import TextEmbeddingSuccess

SYNTHETIC_INDEX_CODE = "rag-natural-language-retrieval-dev-synthetic-index"
SYNTHETIC_INDEX_VERSION = "1.0.0"
SYNTHETIC_RECEIPT_ID = "rag-natural-language-retrieval-dev-index-receipt"
SYNTHETIC_RECEIPT_VERSION = "1.0.0"
SYNTHETIC_SOURCE_CODE = "SYNTHETIC_DEV"
SYNTHETIC_ENDPOINT_CODE = "SYNTHETIC_INDEX"
SYNTHETIC_OPERATION_CODE = "INDEX_RECORDS"

_FIXED_SOURCE_ID = UUID("17800000-0000-4000-8000-000000000101")
_FIXED_ENDPOINT_ID = UUID("17800000-0000-4000-8000-000000000102")
_FIXED_OPERATION_ID = UUID("17800000-0000-4000-8000-000000000103")
_FIXED_SNAPSHOT_ID = UUID("17800000-0000-4000-8000-000000000104")
_FIXED_VERIFICATION_ID = UUID("17800000-0000-4000-8000-000000000105")
_FIXED_DOCUMENT_ID = UUID("17800000-0000-4000-8000-000000000106")


class DeterministicFakeEmbeddingAdapter:
    """Offline deterministic 1536-dimensional embedding adapter for test/dev."""

    def __init__(self, dimension: int = PRODUCTION_EMBEDDING_DIMENSION) -> None:
        self.dimension = dimension
        self.model_ref = PRODUCTION_EMBEDDING_MODEL_REF
        self.model_version = PRODUCTION_EMBEDDING_MODEL_VERSION

    async def embed_query(self, query: SensitiveText | str) -> SensitiveVector:
        val = query.expose() if hasattr(query, "expose") else str(query)
        return self._compute_vector(val)

    async def embed(
        self,
        texts: SensitiveText | str | Sequence[SensitiveText | str],
        *,
        model_ref: str = PRODUCTION_EMBEDDING_MODEL_REF,
        model_version: str = PRODUCTION_EMBEDDING_MODEL_VERSION,
        dimension: int = PRODUCTION_EMBEDDING_DIMENSION,
    ) -> TextEmbeddingSuccess | tuple[SensitiveVector, ...]:
        if isinstance(texts, (SensitiveText, str)):
            val = texts.expose() if hasattr(texts, "expose") else str(texts)
            vec = self._compute_vector(val)
            return TextEmbeddingSuccess(
                embedding=vec,
                adapter_artifact_ref=ImmutableArtifactRef(
                    artifact_code="deterministic-fake-embedding",
                    version="1.0.0",
                    content_sha256="0" * 64,
                ),
            )
        return tuple(self._compute_vector(t.expose() if hasattr(t, "expose") else str(t)) for t in texts)

    def _compute_vector(self, text_val: str) -> SensitiveVector:
        sha = hashlib.sha256(text_val.encode("utf-8")).digest()
        vals: list[float] = []
        for i in range(self.dimension):
            byte_val = sha[i % len(sha)]
            # ensure non-zero deterministic component
            vals.append(float((byte_val + i) % 256) / 255.0 + 0.001)
        norm = math.sqrt(sum(v * v for v in vals))
        normalized = tuple(v / norm for v in vals)
        return SensitiveVector(normalized)


@dataclass(frozen=True, slots=True)
class BootstrapReceiptSummary:
    knowledge_index_id: UUID
    index_code: str
    index_version: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    receipt: IndexBuildReceipt
    reused: bool


def load_synthetic_knowledge_statements(path: Path) -> list[dict[str, str]]:
    content = json.loads(path.read_text(encoding="utf-8"))
    records = content.get("records", [])
    if not isinstance(records, list) or len(records) != 100:
        raise EvaluationValidationError(
            EvaluationErrorCode.RESOURCE_BYTES_INVALID,
            f"Expected exactly 100 synthetic statements in {path}, got {len(records)}",
        )
    for r in records:
        expected_sha = r.get("content_sha256") or r.get("statement_sha256")
        statement = r.get("statement", "")
        if not expected_sha or hashlib.sha256(statement.encode("utf-8")).hexdigest() != expected_sha:
            raise EvaluationValidationError(
                EvaluationErrorCode.HASH_MISMATCH,
                f"Statement hash mismatch for evidence_ref_id={r.get('evidence_ref_id')}",
            )
    return records


async def bootstrap_dev_knowledge_index(
    engine,
    *,
    text_embedding_port: DeterministicFakeEmbeddingAdapter | object,
    synthetic_index_path: Path,
) -> BootstrapReceiptSummary:
    """Bootstrap or reuse the 100-statement DEV knowledge index idempotently."""
    records = load_synthetic_knowledge_statements(synthetic_index_path)
    file_bytes = synthetic_index_path.read_bytes()
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    # 1. Check existing index
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT id, corpus_manifest_hash, embedding_manifest_hash, member_count "
                "FROM rag_knowledge_index WHERE index_code = :code AND index_version = :ver"
            ),
            {"code": SYNTHETIC_INDEX_CODE, "ver": SYNTHETIC_INDEX_VERSION},
        )
        existing = result.mappings().first()

    if existing is not None:
        if existing["member_count"] != 100:
            raise EvaluationValidationError(
                EvaluationErrorCode.VERSION_CONFLICT,
                f"Existing index member count {existing['member_count']} != 100",
            )
        knowledge_index_id = UUID(str(existing["id"]))

        # Retrieve member information to build the receipt
        async with factory() as session:
            chunk_rows = (
                (
                    await session.execute(
                        text(
                            "SELECT m.knowledge_chunk_id, m.external_document_id, m.locator, "
                            "m.content_hash, m.source_snapshot_id, m.source_snapshot_member_id "
                            "FROM rag_knowledge_index_member m "
                            "WHERE m.knowledge_index_id = :idx_id "
                            "ORDER BY m.chunk_index ASC"
                        ),
                        {"idx_id": str(knowledge_index_id)},
                    )
                )
                .mappings()
                .all()
            )

        if len(chunk_rows) != 100:
            raise EvaluationValidationError(
                EvaluationErrorCode.VERSION_CONFLICT,
                f"Expected 100 index members for {knowledge_index_id}, found {len(chunk_rows)}",
            )

        bridge_entries: list[IndexBridgeEntry] = []
        for row in chunk_rows:
            bridge_entries.append(
                IndexBridgeEntry(
                    evidence_ref_id=row["external_document_id"],
                    evidence_mapping_stable_key=f"SYNTHETIC_KEY_{row['external_document_id']}",
                    evidence_key=row["external_document_id"],
                    knowledge_chunk_ref=str(row["knowledge_chunk_id"]),
                    source_locator=row["locator"],
                    source_version="1.0.0",
                    content_sha256=row["content_hash"],
                )
            )

        receipt = _build_index_receipt(
            bridge_entries=tuple(
                sorted(bridge_entries, key=lambda e: (e.evidence_ref_id, e.evidence_key, e.knowledge_chunk_ref))
            ),
            source_snapshot_id=UUID(str(chunk_rows[0]["source_snapshot_id"])),
            knowledge_index_id=knowledge_index_id,
            dataset_file_sha256=file_sha256,
        )

        return BootstrapReceiptSummary(
            knowledge_index_id=knowledge_index_id,
            index_code=SYNTHETIC_INDEX_CODE,
            index_version=SYNTHETIC_INDEX_VERSION,
            source_snapshot_id=UUID(str(chunk_rows[0]["source_snapshot_id"])),
            source_snapshot_member_id=UUID(str(chunk_rows[0]["source_snapshot_member_id"])),
            receipt=receipt,
            reused=True,
        )

    # 2. Fresh Bootstrap
    now = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rag_source "
                "(id, source_code, display_name, lifecycle_status, max_rejected_records, "
                "max_rejection_rate, empty_result_policy) "
                "VALUES (:id, :code, 'Synthetic DEV Source', 'ACTIVE', 0, 0, 'REJECT') "
                "ON CONFLICT (source_code) DO NOTHING"
            ),
            {"id": str(_FIXED_SOURCE_ID), "code": SYNTHETIC_SOURCE_CODE},
        )
        src_row = (
            (
                await connection.execute(
                    text("SELECT id FROM rag_source WHERE source_code = :code"),
                    {"code": SYNTHETIC_SOURCE_CODE},
                )
            )
            .mappings()
            .one()
        )
        source_id = UUID(str(src_row["id"]))

        await connection.execute(
            text(
                "INSERT INTO rag_source_endpoint "
                "(id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, acquisition_status) "
                "VALUES (:id, :source_id, :code, 'Synthetic DEV Index Endpoint', 'VERIFIED', 'ENABLED', 'APPROVED') "
                "ON CONFLICT (source_id, endpoint_code) DO NOTHING"
            ),
            {"id": str(_FIXED_ENDPOINT_ID), "source_id": str(source_id), "code": SYNTHETIC_ENDPOINT_CODE},
        )
        ep_row = (
            (
                await connection.execute(
                    text("SELECT id FROM rag_source_endpoint WHERE source_id = :source_id AND endpoint_code = :code"),
                    {"source_id": str(source_id), "code": SYNTHETIC_ENDPOINT_CODE},
                )
            )
            .mappings()
            .one()
        )
        endpoint_id = UUID(str(ep_row["id"]))

        await connection.execute(
            text(
                "INSERT INTO rag_source_operation "
                "(id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                "VALUES (:id, :endpoint_id, :code, 'Synthetic DEV Index Records', 'ENABLED', 'APPROVED') "
                "ON CONFLICT (endpoint_id, operation_code) DO NOTHING"
            ),
            {"id": str(_FIXED_OPERATION_ID), "endpoint_id": str(endpoint_id), "code": SYNTHETIC_OPERATION_CODE},
        )
        op_row = (
            (
                await connection.execute(
                    text(
                        "SELECT id FROM rag_source_operation WHERE endpoint_id = :endpoint_id AND operation_code = :code"
                    ),
                    {"endpoint_id": str(endpoint_id), "code": SYNTHETIC_OPERATION_CODE},
                )
            )
            .mappings()
            .one()
        )
        operation_id = UUID(str(op_row["id"]))

        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot "
                "(id, operation_id, source_version, external_version, raw_manifest_checksum, canonical_checksum, "
                "schema_version, parser_version, normalization_version, canonicalization_spec_version, "
                "endpoint_receipt_hash, record_count, rejected_record_count, verification_status, collected_at) "
                "VALUES (:id, :op_id, 'external:1.0.0', '1.0.0', :raw_hash, :canon_hash, 'schema-v1', "
                "'parser-v1', 'canonical-v1', '1.0.0', :receipt_hash, 100, 0, 'PENDING', :now) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": str(_FIXED_SNAPSHOT_ID),
                "op_id": str(operation_id),
                "raw_hash": file_sha256,
                "canon_hash": file_sha256,
                "receipt_hash": file_sha256,
                "now": now,
            },
        )
        snapshot_id = _FIXED_SNAPSHOT_ID

    prov = SnapshotProvenanceReceipt(
        source_id=source_id,
        source_code=SYNTHETIC_SOURCE_CODE,
        endpoint_id=endpoint_id,
        operation_id=operation_id,
        source_snapshot_id=snapshot_id,
        source_version="external:1.0.0",
        external_version="1.0.0",
        canonical_checksum=file_sha256,
        canonicalization_spec_version="1.0.0",
        endpoint_receipt_hash=file_sha256,
        verification_seal_id=None,
        verification_status=SnapshotVerificationStatus.PENDING,
        rejected_record_count=0,
        publication_verification_id=None,
    )

    async with factory() as session, session.begin():
        member = await SqlAlchemySourceSnapshotRepository(session).append_snapshot_member(
            SourceSnapshotMemberCreate(
                provenance=prov,
                member_kind=SourceSnapshotMemberKind.ENDPOINT_OPERATION,
                endpoint_id=endpoint_id,
                operation_id=operation_id,
                ingestion_artifact_id=None,
                locator="$.records",
                content_sha256=file_sha256,
            )
        )
    member_id = member.source_snapshot_member_id

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot_verification "
                "(id, snapshot_id, check_name, verification_result, verified_by, verified_at) "
                "VALUES (:id, :snapshot_id, 'synthetic-dev-verification', 'PASSED', 'synthetic-reviewer', :now) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(_FIXED_VERIFICATION_ID), "snapshot_id": str(snapshot_id), "now": now},
        )
        await connection.execute(
            text(
                "UPDATE rag_source_snapshot SET verification_status = 'CURRENT', verification_seal_id = :seal_id, "
                "verified_at = :now, effective_at = :now WHERE id = :snapshot_id"
            ),
            {"seal_id": str(_FIXED_VERIFICATION_ID), "snapshot_id": str(snapshot_id), "now": now},
        )

        await connection.execute(
            text(
                "INSERT INTO knowledge_document "
                "(id, title, publisher, source_url, document_version, document_status, record_contract_version, "
                "source_snapshot_member_id, external_document_id, document_content_hash, "
                "canonicalization_spec_version) "
                "VALUES (:id, 'Synthetic Dev Knowledge Document', NULL, NULL, NULL, 'ACTIVE', 'KNOWLEDGE_EVIDENCE_V1', "
                ":member_id, 'synthetic-knowledge-index.json', :doc_hash, '1.0.0') "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(_FIXED_DOCUMENT_ID), "member_id": str(member_id), "doc_hash": file_sha256},
        )

    # 3. Compute Embeddings and insert Chunks
    drafts: list[KnowledgeIndexMemberDraft] = []
    bridge_entries = []

    for idx, rec in enumerate(records):
        statement = rec["statement"]
        evidence_ref = rec["evidence_ref_id"]
        c_hash = rec["content_sha256"]
        chunk_id = uuid4()

        # Get embedding vector
        if hasattr(text_embedding_port, "embed"):
            embed_res = await text_embedding_port.embed(
                SensitiveText(statement),
                model_ref=PRODUCTION_EMBEDDING_MODEL_REF,
                model_version=PRODUCTION_EMBEDDING_MODEL_VERSION,
                dimension=PRODUCTION_EMBEDDING_DIMENSION,
            )
            if not isinstance(embed_res, TextEmbeddingSuccess):
                raise EvaluationValidationError(
                    EvaluationErrorCode.DEPENDENCY_UNAVAILABLE, f"Embedding failed: {embed_res}"
                )
            vec_tuple = embed_res.embedding.reveal()
        elif hasattr(text_embedding_port, "embed_query"):
            v_res = await text_embedding_port.embed_query(SensitiveText(statement))
            vec_tuple = v_res.reveal() if hasattr(v_res, "reveal") else tuple(v_res)
        else:
            raise EvaluationValidationError(
                EvaluationErrorCode.DEPENDENCY_UNAVAILABLE, "Embedding port missing embed method"
            )

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO knowledge_chunk "
                    "(id, knowledge_document_id, chunk_index, chunk_text, embedding_model, vector_store_key, "
                    "content_hash, normalization_version) "
                    "VALUES (:id, :doc_id, :idx, :text, :model, NULL, :content_hash, 'canonical-v1')"
                ),
                {
                    "id": str(chunk_id),
                    "doc_id": str(_FIXED_DOCUMENT_ID),
                    "idx": idx,
                    "text": statement,
                    "model": PRODUCTION_EMBEDDING_MODEL_REF,
                    "content_hash": c_hash,
                },
            )

        drafts.append(
            KnowledgeIndexMemberDraft(
                identity=KnowledgeChunkIdentity(
                    knowledge_chunk_id=chunk_id,
                    source_snapshot_id=snapshot_id,
                    source_snapshot_member_id=member_id,
                    source_code=SYNTHETIC_SOURCE_CODE,
                    source_version="1.0.0",
                    canonical_checksum=file_sha256,
                    external_document_id=evidence_ref,
                    chunk_index=idx,
                    content_hash=c_hash,
                    locator=f"$.records[{idx}]",
                ),
                content_text=SensitiveEvidenceText(statement),
                embedding=vec_tuple,
            )
        )
        bridge_entries.append(
            IndexBridgeEntry(
                evidence_ref_id=evidence_ref,
                evidence_mapping_stable_key=f"SYNTHETIC_KEY_{evidence_ref}",
                evidence_key=evidence_ref,
                knowledge_chunk_ref=str(chunk_id),
                source_locator=f"$.records[{idx}]",
                source_version="1.0.0",
                content_sha256=c_hash,
            )
        )

    # 4. Build Knowledge Evidence Index
    build_req = KnowledgeIndexBuildRequest(
        index_code=SYNTHETIC_INDEX_CODE,
        index_version=SYNTHETIC_INDEX_VERSION,
        embedding_model_ref=PRODUCTION_EMBEDDING_MODEL_REF,
        embedding_model_version=PRODUCTION_EMBEDDING_MODEL_VERSION,
        embedding_dimension=PRODUCTION_EMBEDDING_DIMENSION,
        distance_metric=DistanceMetric.COSINE,
        members=tuple(drafts),
    )
    built_index = build_knowledge_evidence_index(build_req)
    repo = SqlAlchemyKnowledgeEvidenceIndexRepository(factory)
    persisted = await repo.persist_complete_index(built_index)

    sorted_bridges = tuple(
        sorted(bridge_entries, key=lambda e: (e.evidence_ref_id, e.evidence_key, e.knowledge_chunk_ref))
    )
    receipt = _build_index_receipt(
        bridge_entries=sorted_bridges,
        source_snapshot_id=snapshot_id,
        knowledge_index_id=persisted.header.knowledge_index_id,
        dataset_file_sha256=file_sha256,
    )

    return BootstrapReceiptSummary(
        knowledge_index_id=persisted.header.knowledge_index_id,
        index_code=SYNTHETIC_INDEX_CODE,
        index_version=SYNTHETIC_INDEX_VERSION,
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        receipt=receipt,
        reused=False,
    )


def _build_index_receipt(
    *,
    bridge_entries: tuple[IndexBridgeEntry, ...],
    source_snapshot_id: UUID,
    knowledge_index_id: UUID,
    dataset_file_sha256: str,
) -> IndexBuildReceipt:
    built_by = ReviewProvenanceV12(
        team_gold_status="APPROVED",
        authored_by={"actor_id": "ceohwj", "namespace": "GITHUB_LOGIN", "role": "EVALUATION_IMPLEMENTER"},
        authored_at="2026-09-14T00:00:00.000000Z",
        reviewed_by={"actor_id": "hazelnutflavoured", "namespace": "GITHUB_LOGIN", "role": "EVALUATION_REVIEWER"},
        reviewed_at="2026-09-14T01:00:00.000000Z",
        approved_by={"actor_id": "phina-io", "namespace": "GITHUB_LOGIN", "role": "DATASET_CUSTODIAN"},
        approved_at="2026-09-14T02:00:00.000000Z",
        evidence_review_refs=(),
        external_medical_review_status="NOT_REQUESTED",
        external_medical_approval_receipt_ref=None,
    )
    raw_payload = {
        "schema_id": "rag-eval.index-build-receipt",
        "schema_version": "1.0.0",
        "receipt_id": SYNTHETIC_RECEIPT_ID,
        "receipt_version": SYNTHETIC_RECEIPT_VERSION,
        "dataset_ref": {
            "path": "evals/retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json",
            "sha256": dataset_file_sha256,
        },
        "evidence_mapping_ref": {
            "path": "evals/retrieval/evidence/rag-natural-language-retrieval-dev-evidence.json",
            "sha256": dataset_file_sha256,
        },
        "source_snapshot_ref": {
            "locator": f"urn:uuid:{source_snapshot_id}",
            "sha256": dataset_file_sha256,
            "version": "1.0.0",
        },
        "evidence_index_ref": {
            "locator": f"urn:uuid:{knowledge_index_id}",
            "sha256": dataset_file_sha256,
            "version": "1.0.0",
        },
        "build_config_ref": {
            "locator": "config:synthetic-build-config-v1",
            "sha256": dataset_file_sha256,
            "version": "1.0.0",
        },
        "adapter_artifact_ref": {
            "locator": "adapter:actual-retrieval-dev-adapter-v1",
            "sha256": dataset_file_sha256,
            "version": "1.0.0",
        },
        "canonicalization_spec_version": "1.0.0",
        "bridge_entries": [e.model_dump(mode="json") for e in bridge_entries],
        "built_at": "2026-09-14T02:00:00.000000Z",
        "built_by": built_by.model_dump(mode="json"),
    }
    receipt_sha = canonical_sha256(raw_payload)
    raw_payload["receipt_sha256"] = receipt_sha
    return IndexBuildReceipt.model_validate(raw_payload)
