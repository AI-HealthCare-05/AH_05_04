from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid5

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
from ai_worker.tasks.evaluation.schemas.common import ImmutableReference
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
    create_knowledge_index_receipt,
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
SYNTHETIC_ENDPOINT_CODE = "SYNTHETIC_DEV_INDEX_ENDPOINT"
SYNTHETIC_OPERATION_CODE = "SYNTHETIC_DEV_INDEX_RECORDS"

NAMESPACE_SYNTHETIC_DEV = UUID("17810000-0000-4000-8000-000000000000")

_FIXED_SOURCE_ID = UUID("17810000-0000-4000-8000-000000000001")
_FIXED_ENDPOINT_ID = UUID("17810000-0000-4000-8000-000000000002")
_FIXED_OPERATION_ID = UUID("17810000-0000-4000-8000-000000000003")
_FIXED_SNAPSHOT_ID = UUID("17810000-0000-4000-8000-000000000004")
_FIXED_VERIFICATION_ID = UUID("17810000-0000-4000-8000-000000000005")

ACTUAL_RETRIEVAL_ADAPTER_ID = "knowledge-evidence-retrieval.actual.v1"
ACTUAL_RETRIEVAL_ADAPTER_VERSION = "1.0.0"
ADAPTER_CANONICAL_PROJECTION = {
    "adapter_id": ACTUAL_RETRIEVAL_ADAPTER_ID,
    "adapter_version": ACTUAL_RETRIEVAL_ADAPTER_VERSION,
    "contract_version": "1.0.0",
    "runtime_module": "ai_worker.tasks.evaluation.actual_retrieval",
}
ACTUAL_RETRIEVAL_ADAPTER_HASH = canonical_sha256(ADAPTER_CANONICAL_PROJECTION)
ACTUAL_RETRIEVAL_ADAPTER_REF = ImmutableArtifactRef(
    artifact_code=ACTUAL_RETRIEVAL_ADAPTER_ID,
    version=ACTUAL_RETRIEVAL_ADAPTER_VERSION,
    content_sha256=ACTUAL_RETRIEVAL_ADAPTER_HASH,
)


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


def _parse_embedding(val: object) -> tuple[float, ...]:
    if isinstance(val, (list, tuple)):
        return tuple(float(v) for v in val)
    if isinstance(val, str):
        cleaned = val.strip()
        if cleaned.startswith("[") and cleaned.endswith("]"):
            cleaned = cleaned[1:-1]
        if not cleaned.strip():
            return ()
        return tuple(float(x.strip()) for x in cleaned.split(","))
    return tuple(float(v) for v in val)  # type: ignore[union-attr]


@dataclass(frozen=True, slots=True)
class BootstrapReceiptSummary:
    knowledge_index_id: UUID
    index_code: str
    index_version: str
    source_snapshot_id: UUID
    source_snapshot_member_ids: tuple[UUID, ...]
    evidence_index_ref: ImmutableArtifactRef
    receipt: IndexBuildReceipt
    reused: bool

    @property
    def source_snapshot_member_id(self) -> UUID:
        return self.source_snapshot_member_ids[0]


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


async def bootstrap_dev_knowledge_index(  # noqa: C901
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

    # Resolve manifest paths and real hashes
    repo_root = synthetic_index_path.resolve().parents[5]
    dataset_path = repo_root / "evals/retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json"
    evidence_mapping_path = (
        repo_root / "evals/retrieval/evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json"
    )
    dataset_sha256 = (
        hashlib.sha256(dataset_path.read_bytes()).hexdigest()
        if dataset_path.is_file()
        else "b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2"
    )
    evidence_mapping_sha256 = (
        hashlib.sha256(evidence_mapping_path.read_bytes()).hexdigest()
        if evidence_mapping_path.is_file()
        else "e3949bfecebefbd73abf279d6919e13bb42685751d7aa1d96197a15379f6acd5"
    )

    # 1. Check existing index
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT id, corpus_manifest_hash, embedding_manifest_hash, index_configuration_hash, "
                "embedding_model_ref, embedding_model_version, embedding_dimension, distance_metric, member_count "
                "FROM rag_knowledge_index WHERE index_code = :code AND index_version = :ver"
            ),
            {"code": SYNTHETIC_INDEX_CODE, "ver": SYNTHETIC_INDEX_VERSION},
        )
        existing = result.mappings().first()

    if existing is not None:
        if (
            existing["member_count"] != 100
            or existing["embedding_model_ref"] != PRODUCTION_EMBEDDING_MODEL_REF
            or existing["embedding_model_version"] != PRODUCTION_EMBEDDING_MODEL_VERSION
            or existing["embedding_dimension"] != PRODUCTION_EMBEDDING_DIMENSION
            or existing["distance_metric"] != DistanceMetric.COSINE.value
        ):
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                f"Existing index metadata mismatch for {SYNTHETIC_INDEX_CODE}:{SYNTHETIC_INDEX_VERSION}",
            )
        knowledge_index_id = UUID(str(existing["id"]))

        # Retrieve member information by joining rag_source_snapshot_member, chunk, and document
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT "
                            "m.knowledge_chunk_id, m.source_snapshot_id, m.source_snapshot_member_id, "
                            "m.source_code, m.source_version, m.canonical_checksum, m.external_document_id, "
                            "m.chunk_index, m.content_hash, m.embedding, m.embedding_sha256, m.member_order, "
                            "sm.locator, sm.content_sha256 AS snapshot_member_content_sha256, "
                            "c.chunk_text, c.content_hash AS chunk_content_hash, "
                            "d.external_document_id AS doc_external_document_id, "
                            "d.document_content_hash AS doc_content_hash, d.document_status AS doc_status "
                            "FROM rag_knowledge_index_member m "
                            "JOIN rag_source_snapshot_member sm ON m.source_snapshot_member_id = sm.id "
                            "JOIN knowledge_chunk c ON m.knowledge_chunk_id = c.id "
                            "JOIN knowledge_document d ON c.knowledge_document_id = d.id "
                            "WHERE m.knowledge_index_id = :idx_id "
                            "ORDER BY m.member_order ASC, m.knowledge_chunk_id ASC"
                        ),
                        {"idx_id": str(knowledge_index_id)},
                    )
                )
                .mappings()
                .all()
            )

        if len(rows) != 100:
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                f"Expected 100 index members for {knowledge_index_id}, found {len(rows)}",
            )

        records_by_ref = {r["evidence_ref_id"]: r for r in records}
        for idx, row in enumerate(rows):
            ext_id = str(row["external_document_id"])
            if ext_id not in records_by_ref:
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    f"Unknown member {ext_id} in existing index",
                )
            rec = records_by_ref[ext_id]
            expected_locator = f"$.records[{idx}]"
            chunk_text = str(row["chunk_text"])
            if (
                row["source_code"] != SYNTHETIC_SOURCE_CODE
                or row["source_version"] != "external:1.0.0"
                or row["canonical_checksum"] != file_sha256
                or row["external_document_id"] != row["doc_external_document_id"]
                or row["locator"] != expected_locator
                or row["chunk_index"] != 0
                or row["content_hash"] != rec["content_sha256"]
                or row["snapshot_member_content_sha256"] != rec["content_sha256"]
                or row["chunk_content_hash"] != rec["content_sha256"]
                or row["doc_content_hash"] != rec["content_sha256"]
                or hashlib.sha256(chunk_text.encode("utf-8")).hexdigest() != rec["content_sha256"]
                or row["doc_status"] != "ACTIVE"
            ):
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    f"Index member {ext_id} integrity mismatch",
                )

        # Reconstruct KnowledgeIndexBuildRequest with actual embeddings and recompute receipt
        rebuilt_members = tuple(
            KnowledgeIndexMemberDraft(
                identity=KnowledgeChunkIdentity(
                    knowledge_chunk_id=UUID(str(r["knowledge_chunk_id"])),
                    source_snapshot_id=UUID(str(r["source_snapshot_id"])),
                    source_snapshot_member_id=UUID(str(r["source_snapshot_member_id"])),
                    source_code=str(r["source_code"]),
                    source_version=str(r["source_version"]),
                    canonical_checksum=str(r["canonical_checksum"]),
                    external_document_id=str(r["external_document_id"]),
                    chunk_index=int(r["chunk_index"]),
                    content_hash=str(r["content_hash"]),
                    locator=str(r["locator"]),
                ),
                content_text=SensitiveEvidenceText(str(r["chunk_text"])),
                embedding=_parse_embedding(r["embedding"]),
            )
            for r in rows
        )
        rebuilt_req = KnowledgeIndexBuildRequest(
            index_code=SYNTHETIC_INDEX_CODE,
            index_version=SYNTHETIC_INDEX_VERSION,
            embedding_model_ref=PRODUCTION_EMBEDDING_MODEL_REF,
            embedding_model_version=PRODUCTION_EMBEDDING_MODEL_VERSION,
            embedding_dimension=PRODUCTION_EMBEDDING_DIMENSION,
            distance_metric=DistanceMetric.COSINE,
            members=rebuilt_members,
        )
        expected_receipt = create_knowledge_index_receipt(rebuilt_req)
        if (
            existing["corpus_manifest_hash"] != expected_receipt.corpus_manifest_hash
            or existing["embedding_manifest_hash"] != expected_receipt.embedding_manifest_hash
            or existing["index_configuration_hash"] != expected_receipt.index_configuration_hash
        ):
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                "Existing index manifest hash mismatch with recomputed receipt",
            )

        bridge_entries = [
            IndexBridgeEntry(
                evidence_ref_id=str(r["external_document_id"]),
                evidence_mapping_stable_key=f"SYNTHETIC_KEY_{r['external_document_id']}",
                evidence_key=str(r["external_document_id"]),
                knowledge_chunk_ref=str(r["knowledge_chunk_id"]),
                source_locator=str(r["locator"]),
                source_version="external:1.0.0",
                content_sha256=str(r["content_hash"]),
            )
            for r in rows
        ]
        sorted_bridges = tuple(
            sorted(bridge_entries, key=lambda e: (e.evidence_ref_id, e.evidence_key, e.knowledge_chunk_ref))
        )
        receipt = _build_index_receipt(
            bridge_entries=sorted_bridges,
            source_snapshot_id=UUID(str(rows[0]["source_snapshot_id"])),
            knowledge_index_id=knowledge_index_id,
            source_snapshot_file_sha256=file_sha256,
            index_configuration_hash=str(existing["index_configuration_hash"]),
            dataset_file_sha256=dataset_sha256,
            evidence_mapping_file_sha256=evidence_mapping_sha256,
        )

        return BootstrapReceiptSummary(
            knowledge_index_id=knowledge_index_id,
            index_code=SYNTHETIC_INDEX_CODE,
            index_version=SYNTHETIC_INDEX_VERSION,
            source_snapshot_id=UUID(str(rows[0]["source_snapshot_id"])),
            source_snapshot_member_ids=tuple(UUID(str(r["source_snapshot_member_id"])) for r in rows),
            evidence_index_ref=ImmutableArtifactRef(
                artifact_code=SYNTHETIC_INDEX_CODE,
                version=SYNTHETIC_INDEX_VERSION,
                content_sha256=str(existing["index_configuration_hash"]),
            ),
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

        snap_row = (
            (
                await connection.execute(
                    text(
                        "SELECT id, verification_status, verification_seal_id, source_version, canonical_checksum "
                        "FROM rag_source_snapshot WHERE id = :id"
                    ),
                    {"id": str(_FIXED_SNAPSHOT_ID)},
                )
            )
            .mappings()
            .first()
        )

        if snap_row is None:
            await connection.execute(
                text(
                    "INSERT INTO rag_source_snapshot "
                    "(id, operation_id, source_version, external_version, raw_manifest_checksum, canonical_checksum, "
                    "schema_version, parser_version, normalization_version, canonicalization_spec_version, "
                    "endpoint_receipt_hash, record_count, rejected_record_count, verification_status, collected_at) "
                    "VALUES (:id, :op_id, 'external:1.0.0', '1.0.0', :raw_hash, :canon_hash, 'schema-v1', "
                    "'parser-v1', 'canonical-v1', '1.0.0', :receipt_hash, 100, 0, 'PENDING', :now)"
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
            snapshot_is_current = False
        else:
            if snap_row["source_version"] != "external:1.0.0" or snap_row["canonical_checksum"] != file_sha256:
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    "Existing snapshot metadata mismatch with synthetic specification",
                )
            if snap_row["verification_status"] == "CURRENT":
                snapshot_is_current = True
            elif snap_row["verification_status"] == "PENDING":
                snapshot_is_current = False
            else:
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    f"Unsupported snapshot verification status: {snap_row['verification_status']}",
                )

    snapshot_id = _FIXED_SNAPSHOT_ID
    member_ids: list[UUID] = []

    if snapshot_is_current:
        # Cannot append members to CURRENT snapshot. Validate existing 100 members.
        async with factory() as session:
            sm_rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id, locator, content_sha256, member_kind "
                            "FROM rag_source_snapshot_member WHERE source_snapshot_id = :s_id"
                        ),
                        {"s_id": str(snapshot_id)},
                    )
                )
                .mappings()
                .all()
            )
        if len(sm_rows) != 100:
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                f"CURRENT snapshot has {len(sm_rows)} members, expected exactly 100",
            )
        sm_by_loc = {row["locator"]: row for row in sm_rows}
        for idx, rec in enumerate(records):
            loc = f"$.records[{idx}]"
            if loc not in sm_by_loc or sm_by_loc[loc]["content_sha256"] != rec["content_sha256"]:
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    f"CURRENT snapshot member mismatch for locator {loc}",
                )
            member_ids.append(UUID(str(sm_by_loc[loc]["id"])))
    else:
        # PENDING snapshot: query existing members and append any missing ones
        async with factory() as session:
            existing_sm_rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id, locator, content_sha256 "
                            "FROM rag_source_snapshot_member WHERE source_snapshot_id = :s_id"
                        ),
                        {"s_id": str(snapshot_id)},
                    )
                )
                .mappings()
                .all()
            )
        existing_sm = {row["locator"]: row for row in existing_sm_rows}

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

        for idx, rec in enumerate(records):
            loc = f"$.records[{idx}]"
            c_hash = rec["content_sha256"]
            if loc in existing_sm:
                if existing_sm[loc]["content_sha256"] != c_hash:
                    raise EvaluationValidationError(
                        EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                        f"Existing PENDING snapshot member hash mismatch for locator {loc}",
                    )
                member_ids.append(UUID(str(existing_sm[loc]["id"])))
            else:
                async with factory() as session, session.begin():
                    member = await SqlAlchemySourceSnapshotRepository(session).append_snapshot_member(
                        SourceSnapshotMemberCreate(
                            provenance=prov,
                            member_kind=SourceSnapshotMemberKind.ENDPOINT_OPERATION,
                            endpoint_id=endpoint_id,
                            operation_id=operation_id,
                            ingestion_artifact_id=None,
                            locator=loc,
                            content_sha256=c_hash,
                        )
                    )
                member_ids.append(member.source_snapshot_member_id)

        # All 100 members appended, seal snapshot
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

    # 3. Create Documents, Chunks, Embeddings, Drafts, Bridge Entries
    drafts: list[KnowledgeIndexMemberDraft] = []
    bridge_entries: list[IndexBridgeEntry] = []

    for idx, rec in enumerate(records):
        statement = rec["statement"]
        evidence_ref = rec["evidence_ref_id"]
        c_hash = rec["content_sha256"]
        member_id = member_ids[idx]
        loc = f"$.records[{idx}]"

        document_id = uuid5(NAMESPACE_SYNTHETIC_DEV, f"document:{evidence_ref}")
        chunk_id = uuid5(NAMESPACE_SYNTHETIC_DEV, f"chunk:{evidence_ref}")

        async with engine.begin() as connection:
            # Check or insert KnowledgeDocument
            doc_row = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, source_snapshot_member_id, external_document_id, "
                            "document_content_hash, document_status, record_contract_version "
                            "FROM knowledge_document WHERE id = :id"
                        ),
                        {"id": str(document_id)},
                    )
                )
                .mappings()
                .first()
            )
            if doc_row is not None:
                if (
                    UUID(str(doc_row["source_snapshot_member_id"])) != member_id
                    or doc_row["external_document_id"] != evidence_ref
                    or doc_row["document_content_hash"] != c_hash
                    or doc_row["document_status"] != "ACTIVE"
                    or doc_row["record_contract_version"] != "KNOWLEDGE_EVIDENCE_V1"
                ):
                    raise EvaluationValidationError(
                        EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                        f"Existing document {document_id} conflict with record {evidence_ref}",
                    )
            else:
                await connection.execute(
                    text(
                        "INSERT INTO knowledge_document "
                        "(id, title, publisher, source_url, document_version, document_status, record_contract_version, "
                        "source_snapshot_member_id, external_document_id, document_content_hash, "
                        "canonicalization_spec_version, knowledge_index_lock_marker) "
                        "VALUES (:id, :title, NULL, NULL, NULL, 'ACTIVE', 'KNOWLEDGE_EVIDENCE_V1', "
                        ":member_id, :external_document_id, :doc_hash, '1.0.0', 0)"
                    ),
                    {
                        "id": str(document_id),
                        "title": f"Synthetic Document {evidence_ref}",
                        "member_id": str(member_id),
                        "external_document_id": evidence_ref,
                        "doc_hash": c_hash,
                    },
                )

            # Check or insert KnowledgeChunk
            chunk_row = (
                (
                    await connection.execute(
                        text(
                            "SELECT id, knowledge_document_id, chunk_index, chunk_text, content_hash, embedding_model "
                            "FROM knowledge_chunk WHERE id = :id"
                        ),
                        {"id": str(chunk_id)},
                    )
                )
                .mappings()
                .first()
            )
            if chunk_row is not None:
                if (
                    UUID(str(chunk_row["knowledge_document_id"])) != document_id
                    or chunk_row["chunk_index"] != 0
                    or chunk_row["chunk_text"] != statement
                    or chunk_row["content_hash"] != c_hash
                    or chunk_row["embedding_model"] != PRODUCTION_EMBEDDING_MODEL_REF
                ):
                    raise EvaluationValidationError(
                        EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                        f"Existing chunk {chunk_id} conflict with record {evidence_ref}",
                    )
            else:
                await connection.execute(
                    text(
                        "INSERT INTO knowledge_chunk "
                        "(id, knowledge_document_id, chunk_index, chunk_text, embedding_model, vector_store_key, "
                        "content_hash, normalization_version) "
                        "VALUES (:id, :doc_id, 0, :text, :model, NULL, :content_hash, 'canonical-v1')"
                    ),
                    {
                        "id": str(chunk_id),
                        "doc_id": str(document_id),
                        "text": statement,
                        "model": PRODUCTION_EMBEDDING_MODEL_REF,
                        "content_hash": c_hash,
                    },
                )

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
                    EvaluationErrorCode.INTERNAL_ERROR, f"Embedding failed: {embed_res}"
                )
            vec_tuple = embed_res.embedding.reveal()
        elif hasattr(text_embedding_port, "embed_query"):
            v_res = await text_embedding_port.embed_query(SensitiveText(statement))
            vec_tuple = v_res.reveal() if hasattr(v_res, "reveal") else tuple(v_res)
        else:
            raise EvaluationValidationError(
                EvaluationErrorCode.INTERNAL_ERROR, "Embedding port missing embed method"
            )

        drafts.append(
            KnowledgeIndexMemberDraft(
                identity=KnowledgeChunkIdentity(
                    knowledge_chunk_id=chunk_id,
                    source_snapshot_id=snapshot_id,
                    source_snapshot_member_id=member_id,
                    source_code=SYNTHETIC_SOURCE_CODE,
                    source_version="external:1.0.0",
                    canonical_checksum=file_sha256,
                    external_document_id=evidence_ref,
                    chunk_index=0,
                    content_hash=c_hash,
                    locator=loc,
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
                source_locator=loc,
                source_version="external:1.0.0",
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
    repo = SqlAlchemyKnowledgeEvidenceIndexRepository(factory)
    built_receipt = await build_knowledge_evidence_index(build_req, repository=repo)

    async with factory() as session:
        idx_row = (
            await session.execute(
                text("SELECT id FROM rag_knowledge_index WHERE index_code = :code AND index_version = :ver"),
                {"code": SYNTHETIC_INDEX_CODE, "ver": SYNTHETIC_INDEX_VERSION},
            )
        ).mappings().one()
        knowledge_index_id = UUID(str(idx_row["id"]))

    sorted_bridges = tuple(
        sorted(bridge_entries, key=lambda e: (e.evidence_ref_id, e.evidence_key, e.knowledge_chunk_ref))
    )
    receipt = _build_index_receipt(
        bridge_entries=sorted_bridges,
        source_snapshot_id=snapshot_id,
        knowledge_index_id=knowledge_index_id,
        source_snapshot_file_sha256=file_sha256,
        index_configuration_hash=built_receipt.index_configuration_hash,
        dataset_file_sha256=dataset_sha256,
        evidence_mapping_file_sha256=evidence_mapping_sha256,
    )

    return BootstrapReceiptSummary(
        knowledge_index_id=knowledge_index_id,
        index_code=SYNTHETIC_INDEX_CODE,
        index_version=SYNTHETIC_INDEX_VERSION,
        source_snapshot_id=snapshot_id,
        source_snapshot_member_ids=tuple(member_ids),
        evidence_index_ref=ImmutableArtifactRef(
            artifact_code=SYNTHETIC_INDEX_CODE,
            version=SYNTHETIC_INDEX_VERSION,
            content_sha256=built_receipt.index_configuration_hash,
        ),
        receipt=receipt,
        reused=False,
    )


def _build_index_receipt(
    *,
    bridge_entries: tuple[IndexBridgeEntry, ...],
    source_snapshot_id: UUID,
    knowledge_index_id: UUID,
    source_snapshot_file_sha256: str,
    index_configuration_hash: str,
    dataset_file_sha256: str,
    evidence_mapping_file_sha256: str,
) -> IndexBuildReceipt:
    review_evidence_ref = ImmutableReference(
        id="review-synthetic-knowledge-index-v1",
        version="1.0.0",
        hash=canonical_sha256({
            "review_id": "review-synthetic-knowledge-index-v1",
            "version": "1.0.0",
            "purpose": "synthetic-dev-knowledge-index-review",
        }),
    )
    built_by = ReviewProvenanceV12(
        team_gold_status="APPROVED",
        authored_by={"actor_id": "ceohwj", "namespace": "GITHUB_LOGIN", "role": "EVALUATION_IMPLEMENTER"},
        authored_at="2026-09-14T00:00:00.000000Z",
        reviewed_by={"actor_id": "hazelnutflavoured", "namespace": "GITHUB_LOGIN", "role": "EVALUATION_REVIEWER"},
        reviewed_at="2026-09-14T01:00:00.000000Z",
        approved_by={"actor_id": "phina-io", "namespace": "GITHUB_LOGIN", "role": "DATASET_CUSTODIAN"},
        approved_at="2026-09-14T02:00:00.000000Z",
        evidence_review_refs=(review_evidence_ref,),
        external_medical_review_status="NOT_REQUESTED",
        external_medical_approval_receipt_ref=None,
    )
    build_config_payload = {
        "embedding_dimension": PRODUCTION_EMBEDDING_DIMENSION,
        "embedding_model_ref": PRODUCTION_EMBEDDING_MODEL_REF,
        "embedding_model_version": PRODUCTION_EMBEDDING_MODEL_VERSION,
        "distance_metric": DistanceMetric.COSINE.value,
        "index_code": SYNTHETIC_INDEX_CODE,
        "index_version": SYNTHETIC_INDEX_VERSION,
    }
    build_config_hash = canonical_sha256(build_config_payload)

    raw_payload = {
        "schema_id": "rag-eval.index-build-receipt",
        "schema_version": "1.0.0",
        "receipt_id": SYNTHETIC_RECEIPT_ID,
        "receipt_version": SYNTHETIC_RECEIPT_VERSION,
        "dataset_ref": {
            "id": "rag-natural-language-retrieval-dev",
            "version": "1.0.0",
            "hash": dataset_file_sha256,
        },
        "evidence_mapping_ref": {
            "id": "rag-natural-language-retrieval-dev-evidence",
            "version": "1.0.0",
            "hash": evidence_mapping_file_sha256,
        },
        "source_snapshot_ref": {
            "id": f"urn:uuid:{source_snapshot_id}",
            "version": "external:1.0.0",
            "hash": source_snapshot_file_sha256,
        },
        "evidence_index_ref": {
            "id": SYNTHETIC_INDEX_CODE,
            "version": SYNTHETIC_INDEX_VERSION,
            "hash": index_configuration_hash,
        },
        "build_config_ref": {
            "id": "config:synthetic-build-config-v1",
            "version": "1.0.0",
            "hash": build_config_hash,
        },
        "adapter_artifact_ref": {
            "id": ACTUAL_RETRIEVAL_ADAPTER_REF.artifact_code,
            "version": ACTUAL_RETRIEVAL_ADAPTER_REF.version,
            "hash": ACTUAL_RETRIEVAL_ADAPTER_REF.content_sha256,
        },
        "canonicalization_spec_version": "1.0.0",
        "bridge_entries": [e.model_dump(mode="json") for e in bridge_entries],
        "built_at": "2026-09-14T02:00:00.000000Z",
        "built_by": built_by.model_dump(mode="json"),
    }
    receipt_sha = canonical_sha256(raw_payload, excluded_top_level_keys=frozenset({"receipt_sha256"}))
    raw_payload["receipt_sha256"] = receipt_sha
    return IndexBuildReceipt.model_validate(raw_payload)
