from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from ai_worker.adapters.sqlalchemy_evaluation_bootstrap_repository import (
    SqlAlchemyEvaluationBootstrapRepository,
)
from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    SqlAlchemyKnowledgeEvidenceIndexRepository,
)
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import (
    SqlAlchemySourceSnapshotRepository,
)
from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import load_dataset
from ai_worker.tasks.evaluation.schemas.common import (
    ActorNamespace,
    ExternalMedicalReviewStatus,
    TeamGoldStatus,
)
from ai_worker.tasks.evaluation.schemas.common_v1_2 import (
    ActorRefV12,
    ActorRoleV12,
    ReviewProvenanceV12,
)
from ai_worker.tasks.evaluation.schemas.provenance_v1 import (
    IndexBridgeEntry,
    IndexBuildReceipt,
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
from ai_worker.tasks.rag.text_embedding import (
    TextEmbeddingFailure,
    TextEmbeddingPort,
    TextEmbeddingSuccess,
)

SYNTHETIC_INDEX_CODE = "rag-natural-language-retrieval-dev-synthetic-index"
SYNTHETIC_INDEX_VERSION = "1.0.0"
SYNTHETIC_RECEIPT_ID = "rag-natural-language-retrieval-dev-index-receipt"
SYNTHETIC_RECEIPT_VERSION = "1.0.0"
SYNTHETIC_SOURCE_CODE = "SYNTHETIC_DEV"
SYNTHETIC_ENDPOINT_CODE = "SYNTHETIC_DEV_INDEX_ENDPOINT"
SYNTHETIC_OPERATION_CODE = "SYNTHETIC_DEV_INDEX_RECORDS"

FAKE_EMBEDDING_MODEL_REF = "fake:deterministic-fake-embedding"
FAKE_EMBEDDING_MODEL_VERSION = "1.0.0"
FAKE_EMBEDDING_DIMENSION = 1536

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
ACTUAL_RETRIEVAL_ADAPTER_HASH = canonical_sha256(cast(dict[str, Any], ADAPTER_CANONICAL_PROJECTION))
ACTUAL_RETRIEVAL_ADAPTER_REF = ImmutableArtifactRef(
    artifact_code=ACTUAL_RETRIEVAL_ADAPTER_ID,
    version=ACTUAL_RETRIEVAL_ADAPTER_VERSION,
    content_sha256=ACTUAL_RETRIEVAL_ADAPTER_HASH,
)


class DeterministicFakeEmbeddingAdapter(TextEmbeddingPort):
    """Offline deterministic 1536-dimensional embedding adapter for test/dev."""

    def __init__(self, dimension: int = FAKE_EMBEDDING_DIMENSION) -> None:
        self.dimension = dimension
        self.model_ref = FAKE_EMBEDDING_MODEL_REF
        self.model_version = FAKE_EMBEDDING_MODEL_VERSION
        self._adapter_artifact_ref = ImmutableArtifactRef(
            artifact_code="deterministic-fake-embedding",
            version="1.0.0",
            content_sha256="0" * 64,
        )

    async def embed_query(self, query: SensitiveText | str) -> SensitiveVector:
        val = (
            query.reveal() if hasattr(query, "reveal") else (query.expose() if hasattr(query, "expose") else str(query))
        )
        return self._compute_vector(val)

    async def embed(
        self,
        text: SensitiveText,
        *,
        model_ref: str,
        model_version: str,
        dimension: int,
    ) -> TextEmbeddingSuccess | TextEmbeddingFailure:
        val = text.reveal() if hasattr(text, "reveal") else (text.expose() if hasattr(text, "expose") else str(text))
        vec = self._compute_vector(val)
        return TextEmbeddingSuccess(
            embedding=vec,
            adapter_artifact_ref=self._adapter_artifact_ref,
        )

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
    if hasattr(val, "__iter__"):
        return tuple(float(v) for v in cast(Sequence[Any], val))
    return ()


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
    text_embedding_port: DeterministicFakeEmbeddingAdapter | object | None = None,
    synthetic_index_path: Path,
) -> BootstrapReceiptSummary:
    """Bootstrap or reuse the 100-statement DEV knowledge index idempotently."""
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    # 1. Legacy state detection scoped to synthetic evaluation namespace
    async with factory() as session:
        legacy_res = await session.execute(
            text(
                "SELECT 1 FROM ( "
                "  SELECT 1 FROM rag_source WHERE id::text LIKE '17800000-%' "
                "  UNION ALL "
                "  SELECT 1 FROM rag_source_endpoint WHERE id::text LIKE '17800000-%' "
                "  UNION ALL "
                "  SELECT 1 FROM rag_source_operation WHERE id::text LIKE '17800000-%' "
                "  UNION ALL "
                "  SELECT 1 FROM rag_source_snapshot WHERE id::text LIKE '17800000-%' "
                "  UNION ALL "
                "  SELECT 1 FROM rag_source_snapshot_verification WHERE id::text LIKE '17800000-%' "
                "  UNION ALL "
                "  SELECT 1 FROM rag_source_snapshot_member WHERE id::text LIKE '17800000-%' "
                "  UNION ALL "
                "  SELECT 1 FROM rag_source_endpoint ep "
                "  JOIN rag_source s ON ep.source_id = s.id "
                "  WHERE (s.source_code = :src_code OR s.id::text LIKE '17800000-%' OR s.id = :fixed_source_id) "
                "    AND ep.endpoint_code = 'SYNTHETIC_INDEX' "
                "  UNION ALL "
                "  SELECT 1 FROM rag_source_operation op "
                "  JOIN rag_source_endpoint ep ON op.endpoint_id = ep.id "
                "  JOIN rag_source s ON ep.source_id = s.id "
                "  WHERE (s.source_code = :src_code OR s.id::text LIKE '17800000-%' OR s.id = :fixed_source_id) "
                "    AND op.operation_code = 'INDEX_RECORDS' "
                "  UNION ALL "
                "  SELECT 1 FROM rag_source_snapshot_member sm "
                "  JOIN rag_source_snapshot snap ON sm.source_snapshot_id = snap.id "
                "  JOIN rag_source_operation op ON snap.operation_id = op.id "
                "  JOIN rag_source_endpoint ep ON op.endpoint_id = ep.id "
                "  JOIN rag_source s ON ep.source_id = s.id "
                "  WHERE (s.source_code = :src_code OR s.id::text LIKE '17800000-%' OR s.id = :fixed_source_id OR snap.id = :fixed_snap_id) "
                "    AND sm.locator = '$.records' "
                ") legacy LIMIT 1"
            ),
            {
                "src_code": SYNTHETIC_SOURCE_CODE,
                "fixed_source_id": str(_FIXED_SOURCE_ID),
                "fixed_snap_id": str(_FIXED_SNAPSHOT_ID),
            },
        )
        if legacy_res.first() is not None:
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                "Detected legacy synthetic dev entity or unindexed locator '$.records' in synthetic dev hierarchy",
            )

    records = load_synthetic_knowledge_statements(synthetic_index_path)
    file_bytes = synthetic_index_path.read_bytes()
    file_sha256 = hashlib.sha256(file_bytes).hexdigest()

    repo_root = synthetic_index_path.resolve().parents[5]
    dataset_path = repo_root / "evals/retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json"
    evals_root = repo_root / "evals"

    if not dataset_path.is_file():
        raise EvaluationValidationError(
            EvaluationErrorCode.RESOURCE_NOT_FOUND,
            f"Dataset manifest not found: {dataset_path}",
        )

    validated_dataset = load_dataset(dataset_path, evals_root=evals_root)
    dataset_id = validated_dataset.manifest.dataset_code
    dataset_ver = validated_dataset.manifest.dataset_version
    dataset_sha256 = validated_dataset.manifest.manifest_sha256

    evidence_mapping = validated_dataset.evidence_mapping
    evidence_mapping_id = evidence_mapping.mapping_id
    evidence_mapping_ver = evidence_mapping.mapping_version
    evidence_mapping_sha256 = evidence_mapping.manifest_sha256

    evidence_mapping_by_ref: dict[str, Any] = {}
    for entry in evidence_mapping.entries:
        if getattr(entry, "evidence_type", None) and entry.evidence_type.value == "KNOWLEDGE_CHUNK":
            evidence_mapping_by_ref[entry.evidence_ref_id] = entry

    if text_embedding_port is not None:
        expected_model_ref = getattr(text_embedding_port, "model_ref", PRODUCTION_EMBEDDING_MODEL_REF)
        expected_model_version = getattr(text_embedding_port, "model_version", PRODUCTION_EMBEDDING_MODEL_VERSION)
        expected_dimension = getattr(text_embedding_port, "dimension", PRODUCTION_EMBEDDING_DIMENSION)
    else:
        expected_model_ref = PRODUCTION_EMBEDDING_MODEL_REF
        expected_model_version = PRODUCTION_EMBEDDING_MODEL_VERSION
        expected_dimension = PRODUCTION_EMBEDDING_DIMENSION

    # 2. Check existing index (Reuse)
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
            or existing["embedding_model_ref"] != expected_model_ref
            or existing["embedding_model_version"] != expected_model_version
            or existing["embedding_dimension"] != expected_dimension
            or existing["distance_metric"] != DistanceMetric.COSINE.value
        ):
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                f"Existing index metadata mismatch for {SYNTHETIC_INDEX_CODE}:{SYNTHETIC_INDEX_VERSION}",
            )
        knowledge_index_id = UUID(str(existing["id"]))

        # Verify entire hierarchy & snapshot verification seal
        async with factory() as session:
            snap_hier = (
                (
                    await session.execute(
                        text(
                            "SELECT s.id AS source_id, s.lifecycle_status AS source_lifecycle, "
                            "ep.id AS endpoint_id, ep.lifecycle_status AS endpoint_lifecycle, ep.runtime_status AS endpoint_runtime, "
                            "ep.acquisition_status AS endpoint_acquisition, "
                            "op.id AS operation_id, op.runtime_status AS operation_runtime, "
                            "op.acquisition_status AS operation_acquisition, "
                            "snap.id AS snapshot_id, snap.verification_status, snap.verification_seal_id, snap.canonical_checksum, "
                            "v.verification_result "
                            "FROM rag_source s "
                            "JOIN rag_source_endpoint ep ON ep.source_id = s.id AND ep.endpoint_code = :ep_code "
                            "JOIN rag_source_operation op ON op.endpoint_id = ep.id AND op.operation_code = :op_code "
                            "JOIN rag_source_snapshot snap ON snap.operation_id = op.id AND snap.id = :snap_id "
                            "LEFT JOIN rag_source_snapshot_verification v ON v.id = snap.verification_seal_id "
                            "WHERE s.source_code = :src_code"
                        ),
                        {
                            "src_code": SYNTHETIC_SOURCE_CODE,
                            "ep_code": SYNTHETIC_ENDPOINT_CODE,
                            "op_code": SYNTHETIC_OPERATION_CODE,
                            "snap_id": str(_FIXED_SNAPSHOT_ID),
                        },
                    )
                )
                .mappings()
                .first()
            )

        if snap_hier is None:
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                "Snapshot hierarchy missing for synthetic dev reuse",
            )
        if (
            snap_hier["source_lifecycle"] != "ACTIVE"
            or snap_hier["endpoint_lifecycle"] != "VERIFIED"
            or snap_hier["endpoint_runtime"] != "ENABLED"
            or snap_hier["endpoint_acquisition"] != "APPROVED"
            or snap_hier["operation_runtime"] != "ENABLED"
            or snap_hier["operation_acquisition"] != "APPROVED"
            or snap_hier["verification_status"] != "CURRENT"
            or snap_hier["verification_seal_id"] is None
            or snap_hier["canonical_checksum"] != file_sha256
            or snap_hier["verification_result"] != "PASSED"
        ):
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                "Snapshot hierarchy or seal invalid for reuse",
            )

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
                UUID(str(row["source_snapshot_id"])) != _FIXED_SNAPSHOT_ID
                or row["source_code"] != SYNTHETIC_SOURCE_CODE
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
            embedding_model_ref=expected_model_ref,
            embedding_model_version=expected_model_version,
            embedding_dimension=expected_dimension,
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

        reused_bridge_entries = [
            IndexBridgeEntry(
                evidence_ref_id=str(r["external_document_id"]),
                evidence_mapping_stable_key=str(evidence_mapping_by_ref[str(r["external_document_id"])].stable_key),
                evidence_key=str(r["external_document_id"]),
                knowledge_chunk_ref=str(r["knowledge_chunk_id"]),
                source_locator=str(r["locator"]),
                source_version="external:1.0.0",
                content_sha256=str(r["content_hash"]),
            )
            for r in rows
            if str(r["external_document_id"]) in evidence_mapping_by_ref
        ]
        sorted_bridges = tuple(
            sorted(reused_bridge_entries, key=lambda e: (e.evidence_ref_id, e.evidence_key, e.knowledge_chunk_ref))
        )
        receipt = _build_index_receipt(
            bridge_entries=sorted_bridges,
            source_snapshot_id=UUID(str(rows[0]["source_snapshot_id"])),
            knowledge_index_id=knowledge_index_id,
            source_snapshot_file_sha256=file_sha256,
            index_configuration_hash=str(existing["index_configuration_hash"]),
            dataset_id=dataset_id,
            dataset_ver=dataset_ver,
            dataset_sha256=dataset_sha256,
            evidence_mapping_id=evidence_mapping_id,
            evidence_mapping_ver=evidence_mapping_ver,
            evidence_mapping_sha256=evidence_mapping_sha256,
            embedding_model_ref=expected_model_ref,
            embedding_model_version=expected_model_version,
            embedding_dimension=expected_dimension,
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

    # 3. Fresh Bootstrap
    if text_embedding_port is None:
        raise EvaluationValidationError(
            EvaluationErrorCode.REPOSITORY_STATE_INVALID,
            "Text embedding port required for fresh synthetic index bootstrap",
        )

    now = datetime(2026, 9, 14, 0, 0, tzinfo=UTC)

    async with engine.begin() as connection:
        eval_repo = SqlAlchemyEvaluationBootstrapRepository(connection)
        source_id = await eval_repo.ensure_synthetic_source(
            source_id=_FIXED_SOURCE_ID,
            source_code=SYNTHETIC_SOURCE_CODE,
        )
        endpoint_id = await eval_repo.ensure_synthetic_endpoint(
            endpoint_id=_FIXED_ENDPOINT_ID,
            source_id=source_id,
            endpoint_code=SYNTHETIC_ENDPOINT_CODE,
        )
        operation_id = await eval_repo.ensure_synthetic_operation(
            operation_id=_FIXED_OPERATION_ID,
            endpoint_id=endpoint_id,
            operation_code=SYNTHETIC_OPERATION_CODE,
        )
        snapshot_is_current = await eval_repo.ensure_pending_snapshot(
            snapshot_id=_FIXED_SNAPSHOT_ID,
            operation_id=operation_id,
            raw_hash=file_sha256,
            canon_hash=file_sha256,
            receipt_hash=file_sha256,
            now=now,
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
                            "SELECT id, locator, content_sha256, member_kind, endpoint_id, operation_id, ingestion_artifact_id "
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
            if loc not in sm_by_loc:
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    f"CURRENT snapshot member missing for locator {loc}",
                )
            row = sm_by_loc[loc]
            if (
                row["content_sha256"] != rec["content_sha256"]
                or row["member_kind"] != "ENDPOINT_OPERATION"
                or UUID(str(row["endpoint_id"])) != endpoint_id
                or UUID(str(row["operation_id"])) != operation_id
                or row["ingestion_artifact_id"] is not None
            ):
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    f"CURRENT snapshot member mismatch for locator {loc}",
                )
            member_ids.append(UUID(str(row["id"])))
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

        # Full verification before sealing snapshot
        async with factory() as session:
            all_sm_rows = (
                (
                    await session.execute(
                        text(
                            "SELECT id, locator, content_sha256, member_kind, endpoint_id, operation_id, ingestion_artifact_id "
                            "FROM rag_source_snapshot_member WHERE source_snapshot_id = :s_id"
                        ),
                        {"s_id": str(snapshot_id)},
                    )
                )
                .mappings()
                .all()
            )
        if len(all_sm_rows) != 100:
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                f"Expected exactly 100 snapshot members before sealing, found {len(all_sm_rows)}",
            )
        sm_by_loc = {row["locator"]: row for row in all_sm_rows}
        for idx, rec in enumerate(records):
            loc = f"$.records[{idx}]"
            if loc not in sm_by_loc:
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    f"Missing member for locator {loc}",
                )
            row = sm_by_loc[loc]
            if (
                row["content_sha256"] != rec["content_sha256"]
                or row["member_kind"] != "ENDPOINT_OPERATION"
                or UUID(str(row["endpoint_id"])) != endpoint_id
                or UUID(str(row["operation_id"])) != operation_id
                or row["ingestion_artifact_id"] is not None
            ):
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    f"Member validation mismatch for locator {loc}",
                )

        # All 100 members verified, seal snapshot using CAS
        async with engine.begin() as connection:
            eval_repo = SqlAlchemyEvaluationBootstrapRepository(connection)
            await eval_repo.seal_snapshot(
                snapshot_id=snapshot_id,
                verification_id=_FIXED_VERIFICATION_ID,
                now=now,
            )

    # 4. Create Documents, Chunks, Embeddings, Drafts, Bridge Entries
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
            eval_repo = SqlAlchemyEvaluationBootstrapRepository(connection)
            await eval_repo.ensure_knowledge_document(
                doc_id=document_id,
                title=f"Synthetic Document {evidence_ref}",
                member_id=member_id,
                external_document_id=evidence_ref,
                document_content_hash=c_hash,
            )
            await eval_repo.ensure_knowledge_chunk(
                chunk_id=chunk_id,
                doc_id=document_id,
                chunk_text=statement,
                model_ref=expected_model_ref,
                content_hash=c_hash,
            )

        # Get embedding vector
        if hasattr(text_embedding_port, "embed"):
            embed_res = await text_embedding_port.embed(
                SensitiveText(statement),
                model_ref=expected_model_ref,
                model_version=expected_model_version,
                dimension=expected_dimension,
            )
            if not isinstance(embed_res, TextEmbeddingSuccess):
                raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR, f"Embedding failed: {embed_res}")
            vec_tuple = embed_res.embedding.reveal()
        elif hasattr(text_embedding_port, "embed_query"):
            v_res = await text_embedding_port.embed_query(SensitiveText(statement))
            vec_tuple = v_res.reveal() if hasattr(v_res, "reveal") else tuple(v_res)
        else:
            raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR, "Embedding port missing embed method")

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
        if evidence_ref in evidence_mapping_by_ref:
            bridge_entries.append(
                IndexBridgeEntry(
                    evidence_ref_id=evidence_ref,
                    evidence_mapping_stable_key=str(evidence_mapping_by_ref[evidence_ref].stable_key),
                    evidence_key=evidence_ref,
                    knowledge_chunk_ref=str(chunk_id),
                    source_locator=loc,
                    source_version="external:1.0.0",
                    content_sha256=c_hash,
                )
            )

    # 5. Build Knowledge Evidence Index
    build_req = KnowledgeIndexBuildRequest(
        index_code=SYNTHETIC_INDEX_CODE,
        index_version=SYNTHETIC_INDEX_VERSION,
        embedding_model_ref=expected_model_ref,
        embedding_model_version=expected_model_version,
        embedding_dimension=expected_dimension,
        distance_metric=DistanceMetric.COSINE,
        members=tuple(drafts),
    )
    repo = SqlAlchemyKnowledgeEvidenceIndexRepository(factory)
    built_receipt = await build_knowledge_evidence_index(build_req, repository=repo)

    async with factory() as session:
        idx_row = (
            (
                await session.execute(
                    text("SELECT id FROM rag_knowledge_index WHERE index_code = :code AND index_version = :ver"),
                    {"code": SYNTHETIC_INDEX_CODE, "ver": SYNTHETIC_INDEX_VERSION},
                )
            )
            .mappings()
            .one()
        )
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
        dataset_id=dataset_id,
        dataset_ver=dataset_ver,
        dataset_sha256=dataset_sha256,
        evidence_mapping_id=evidence_mapping_id,
        evidence_mapping_ver=evidence_mapping_ver,
        evidence_mapping_sha256=evidence_mapping_sha256,
        embedding_model_ref=expected_model_ref,
        embedding_model_version=expected_model_version,
        embedding_dimension=expected_dimension,
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
    dataset_id: str,
    dataset_ver: str,
    dataset_sha256: str,
    evidence_mapping_id: str,
    evidence_mapping_ver: str,
    evidence_mapping_sha256: str,
    embedding_model_ref: str,
    embedding_model_version: str,
    embedding_dimension: int,
) -> IndexBuildReceipt:
    built_by = ReviewProvenanceV12(
        team_gold_status=TeamGoldStatus.DRAFT,
        authored_by=ActorRefV12(
            actor_id="ceohwj",
            namespace=ActorNamespace.GITHUB_LOGIN,
            role=ActorRoleV12.EVALUATION_IMPLEMENTER,
        ),
        authored_at="2026-09-14T00:00:00.000000Z",
        reviewed_by=None,
        reviewed_at=None,
        approved_by=None,
        approved_at=None,
        evidence_review_refs=(),
        external_medical_review_status=ExternalMedicalReviewStatus.NOT_REQUESTED,
        external_medical_approval_receipt_ref=None,
    )
    build_config_payload = {
        "embedding_dimension": embedding_dimension,
        "embedding_model_ref": embedding_model_ref,
        "embedding_model_version": embedding_model_version,
        "distance_metric": DistanceMetric.COSINE.value,
        "index_code": SYNTHETIC_INDEX_CODE,
        "index_version": SYNTHETIC_INDEX_VERSION,
    }
    build_config_hash = canonical_sha256(cast(dict[str, Any], build_config_payload))

    raw_payload = {
        "schema_id": "rag-eval.index-build-receipt",
        "schema_version": "1.0.0",
        "receipt_id": SYNTHETIC_RECEIPT_ID,
        "receipt_version": SYNTHETIC_RECEIPT_VERSION,
        "dataset_ref": {
            "id": dataset_id,
            "version": dataset_ver,
            "hash": dataset_sha256,
        },
        "evidence_mapping_ref": {
            "id": evidence_mapping_id,
            "version": evidence_mapping_ver,
            "hash": evidence_mapping_sha256,
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
    receipt_sha = canonical_sha256(
        cast(dict[str, Any], raw_payload),
        excluded_top_level_keys=frozenset({"receipt_sha256"}),
    )
    raw_payload["receipt_sha256"] = receipt_sha
    return IndexBuildReceipt.model_validate(raw_payload)
