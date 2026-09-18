"""Separate bootstrap authority stages for RET-H AWS synthetic smoke (#684).

Implements the 2-stage bootstrap authority split:
- Stage 1 (SOURCE_WRITER): Source/Snapshot/Member/Seal creation.
- Stage 2 (KNOWLEDGE_INDEX_BUILDER): Read-only DB preflight and Knowledge/Index materialization.
- Runtime parent lifecycle and cascade cleanup (DB_APP_USER).
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.openai_text_embedding import (
    OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
    OpenAITextEmbeddingAdapter,
)
from ai_worker.adapters.sqlalchemy_evaluation_bootstrap_repository import (
    SqlAlchemyEvaluationBootstrapRepository,
)
from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    SqlAlchemyKnowledgeEvidenceIndexRepository,
)
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.resources import (
    RetHSmokeRuntimeProvenance,
    SmokeSyntheticFixtureInput,
    build_ret_h_smoke_fixture_manifest,
    load_ret_h_smoke_synthetic_fixture,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchExecutionBinding,
    EvidenceSearchRequest,
    QueryFingerprint,
    RetrievalExecutionMode,
    VersionedDenseSearchConfiguration,
    VersionedEvidenceRetrievalConfiguration,
    VersionedLexicalSearchConfiguration,
)
from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeChunkIdentity,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexMemberDraft,
    SensitiveEvidenceText,
    create_knowledge_index_receipt,
)
from ai_worker.tasks.rag.retrieval_runtime import HybridRetrieveRequest
from ai_worker.tasks.rag.text_embedding import (
    TextEmbeddingFailure,
)

logger = logging.getLogger(__name__)

RET_H_SMOKE_SOURCE_CODE = "SYNTHETIC_RET_H_SMOKE"
RET_H_SMOKE_ENDPOINT_CODE = "SYNTHETIC_RET_H_SMOKE_ENDPOINT"
RET_H_SMOKE_OPERATION_CODE = "SYNTHETIC_RET_H_SMOKE_OPERATION"
RET_H_SMOKE_INDEX_CODE = "rag-ret-h-aws-smoke-synthetic-index"
RET_H_SMOKE_INDEX_VERSION = "1.0.0"

NAMESPACE_SYNTHETIC_RET_H_SMOKE = UUID("17820000-0000-4000-8000-000000000000")
SMOKE_SOURCE_ID = uuid5(NAMESPACE_SYNTHETIC_RET_H_SMOKE, f"source:{RET_H_SMOKE_SOURCE_CODE}")
SMOKE_ENDPOINT_ID = uuid5(NAMESPACE_SYNTHETIC_RET_H_SMOKE, f"endpoint:{RET_H_SMOKE_ENDPOINT_CODE}")
SMOKE_OPERATION_ID = uuid5(NAMESPACE_SYNTHETIC_RET_H_SMOKE, f"operation:{RET_H_SMOKE_OPERATION_CODE}")
SMOKE_KNOWLEDGE_INDEX_ID = uuid5(
    NAMESPACE_SYNTHETIC_RET_H_SMOKE, f"index:{RET_H_SMOKE_INDEX_CODE}:{RET_H_SMOKE_INDEX_VERSION}"
)

SessionFactory = Callable[[], AsyncSession]


@dataclass(frozen=True, slots=True)
class Stage1SourceReceipt:
    source_id: UUID
    endpoint_id: UUID
    operation_id: UUID
    snapshot_id: UUID
    member_ids: tuple[UUID, ...]
    canonical_checksum: str
    verification_seal_id: UUID
    reused: bool


@dataclass(frozen=True, slots=True)
class Stage2IndexReceipt:
    knowledge_index_id: UUID
    index_code: str
    index_version: str
    index_configuration_hash: str
    document_id: UUID
    chunk_id: UUID
    member_id: UUID
    reused: bool


def stage1_receipt_to_dict(receipt: Stage1SourceReceipt) -> dict[str, Any]:
    return {
        "source_id": str(receipt.source_id),
        "endpoint_id": str(receipt.endpoint_id),
        "operation_id": str(receipt.operation_id),
        "snapshot_id": str(receipt.snapshot_id),
        "member_ids": [str(m) for m in receipt.member_ids],
        "canonical_checksum": receipt.canonical_checksum,
        "verification_seal_id": str(receipt.verification_seal_id),
        "reused": receipt.reused,
    }


def stage1_receipt_from_dict(data: Mapping[str, Any]) -> Stage1SourceReceipt:
    return Stage1SourceReceipt(
        source_id=UUID(str(data["source_id"])),
        endpoint_id=UUID(str(data["endpoint_id"])),
        operation_id=UUID(str(data["operation_id"])),
        snapshot_id=UUID(str(data["snapshot_id"])),
        member_ids=tuple(UUID(str(m)) for m in data["member_ids"]),
        canonical_checksum=str(data["canonical_checksum"]),
        verification_seal_id=UUID(str(data["verification_seal_id"])),
        reused=bool(data["reused"]),
    )


def stage2_receipt_to_dict(receipt: Stage2IndexReceipt) -> dict[str, Any]:
    return {
        "knowledge_index_id": str(receipt.knowledge_index_id),
        "index_code": receipt.index_code,
        "index_version": receipt.index_version,
        "index_configuration_hash": receipt.index_configuration_hash,
        "document_id": str(receipt.document_id),
        "chunk_id": str(receipt.chunk_id),
        "member_id": str(receipt.member_id),
        "reused": receipt.reused,
    }


def stage2_receipt_from_dict(data: Mapping[str, Any]) -> Stage2IndexReceipt:
    return Stage2IndexReceipt(
        knowledge_index_id=UUID(str(data["knowledge_index_id"])),
        index_code=str(data["index_code"]),
        index_version=str(data["index_version"]),
        index_configuration_hash=str(data["index_configuration_hash"]),
        document_id=UUID(str(data["document_id"])),
        chunk_id=UUID(str(data["chunk_id"])),
        member_id=UUID(str(data["member_id"])),
        reused=bool(data["reused"]),
    )


# --------------------------------------------------------------------------------------
# Stage 1: SOURCE_WRITER boundary
# --------------------------------------------------------------------------------------


async def bootstrap_ret_h_smoke_stage1_source(
    *,
    session_factory: SessionFactory,
    fixture: SmokeSyntheticFixtureInput,
) -> Stage1SourceReceipt:
    """Stage 1: idempotent creation and sealing of the synthetic Source hierarchy.

    Executed exclusively by SOURCE_WRITER.
    """
    snapshot_id = uuid5(NAMESPACE_SYNTHETIC_RET_H_SMOKE, f"snapshot:{fixture.file_sha256}")
    verification_id = uuid5(NAMESPACE_SYNTHETIC_RET_H_SMOKE, f"verification:{fixture.file_sha256}")
    member_id = uuid5(NAMESPACE_SYNTHETIC_RET_H_SMOKE, f"member:{fixture.file_sha256}:0")
    now = datetime.now(UTC)

    async with session_factory() as session, session.begin():
        eval_repo = SqlAlchemyEvaluationBootstrapRepository(session)

        source_id = await eval_repo.ensure_synthetic_source(
            source_id=SMOKE_SOURCE_ID,
            source_code=fixture.source_code,
            display_name="Synthetic Smoke Source",
        )
        endpoint_id = await eval_repo.ensure_synthetic_endpoint(
            endpoint_id=SMOKE_ENDPOINT_ID,
            source_id=source_id,
            endpoint_code=fixture.endpoint_code,
            display_name="Synthetic Smoke Endpoint",
        )
        operation_id = await eval_repo.ensure_synthetic_operation(
            operation_id=SMOKE_OPERATION_ID,
            endpoint_id=endpoint_id,
            operation_code=fixture.operation_code,
            display_name="Synthetic Smoke Operation",
        )
        snapshot_is_current = await eval_repo.ensure_pending_snapshot(
            snapshot_id=snapshot_id,
            operation_id=operation_id,
            raw_hash=fixture.file_sha256,
            canon_hash=fixture.file_sha256,
            receipt_hash=fixture.file_sha256,
            now=now,
            record_count=len(fixture.records),
            source_version="1.0.0",
            external_version="1.0.0",
            schema_version="1.0",
            parser_version="1.0",
            normalization_version="v1",
            canonicalization_spec_version="canonical-v1",
        )

        if snapshot_is_current:
            existing_members = await eval_repo.get_snapshot_members(snapshot_id=snapshot_id)
            seal_id = await eval_repo.get_snapshot_verification_seal_id(snapshot_id=snapshot_id)
            return Stage1SourceReceipt(
                source_id=source_id,
                endpoint_id=endpoint_id,
                operation_id=operation_id,
                snapshot_id=snapshot_id,
                member_ids=tuple(existing_members),
                canonical_checksum=fixture.file_sha256,
                verification_seal_id=seal_id,
                reused=True,
            )

        m_id = await eval_repo.ensure_snapshot_member(
            member_id=member_id,
            snapshot_id=snapshot_id,
            endpoint_id=endpoint_id,
            operation_id=operation_id,
            locator="$.records[0]",
            content_sha256=fixture.records[0].content_sha256,
            member_kind="ENDPOINT_OPERATION",
        )
        await eval_repo.seal_snapshot(
            snapshot_id=snapshot_id,
            verification_id=verification_id,
            now=now,
            check_name="synthetic-ret-h-smoke-verification",
            verified_by="synthetic_smoke",
            details_summary="synthetic_smoke",
        )

    return Stage1SourceReceipt(
        source_id=source_id,
        endpoint_id=endpoint_id,
        operation_id=operation_id,
        snapshot_id=snapshot_id,
        member_ids=(m_id,),
        canonical_checksum=fixture.file_sha256,
        verification_seal_id=verification_id,
        reused=False,
    )


# --------------------------------------------------------------------------------------
# Stage 2: KNOWLEDGE_INDEX_BUILDER boundary
# --------------------------------------------------------------------------------------


async def _preflight_stage2_source_snapshot(
    session: AsyncSession,
    stage1_snapshot_id: UUID,
    fixture: SmokeSyntheticFixtureInput,
) -> UUID:
    snap_row = (
        (
            await session.execute(
                text(
                    "SELECT id, verification_status, verification_seal_id, canonical_checksum "
                    "FROM rag_source_snapshot WHERE id = :id"
                ),
                {"id": str(stage1_snapshot_id)},
            )
        )
        .mappings()
        .one_or_none()
    )

    if snap_row is None:
        raise EvaluationValidationError(
            EvaluationErrorCode.REPOSITORY_STATE_INVALID,
            safe_path=f"snapshot:{stage1_snapshot_id}:not_found",
        )

    if snap_row["verification_status"] != "CURRENT" or not snap_row["verification_seal_id"]:
        raise EvaluationValidationError(
            EvaluationErrorCode.REPOSITORY_STATE_INVALID,
            safe_path=f"snapshot:{stage1_snapshot_id}:unsealed",
        )

    if snap_row["canonical_checksum"] != fixture.file_sha256:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path=f"snapshot:{stage1_snapshot_id}:canonical_checksum",
        )

    member_rows = (
        (
            await session.execute(
                text(
                    "SELECT id, locator, content_sha256 FROM rag_source_snapshot_member WHERE source_snapshot_id = :sid"
                ),
                {"sid": str(stage1_snapshot_id)},
            )
        )
        .mappings()
        .all()
    )

    if len(member_rows) != 1:
        raise EvaluationValidationError(
            EvaluationErrorCode.SCHEMA_INVALID,
            safe_path=f"snapshot:{stage1_snapshot_id}:member_count",
        )

    member_row = member_rows[0]
    if member_row["locator"] != "$.records[0]" or member_row["content_sha256"] != fixture.records[0].content_sha256:
        raise EvaluationValidationError(
            EvaluationErrorCode.HASH_MISMATCH,
            safe_path=f"snapshot:{stage1_snapshot_id}:member_content",
        )
    return UUID(str(member_row["id"]))


async def _compute_stage2_embedding(
    embedding_port: Any,
    statement: str,
    *,
    expected_embedding_adapter_ref: ImmutableArtifactRef | None = None,
) -> tuple[float, ...]:
    if hasattr(embedding_port, "embed"):
        emb_res = await embedding_port.embed(
            SensitiveText(statement),
            model_ref="openai:text-embedding-3-large",
            model_version="text-embedding-3-large",
            dimension=1536,
        )
        if isinstance(emb_res, TextEmbeddingFailure) or not hasattr(emb_res, "embedding"):
            raise EvaluationValidationError(
                EvaluationErrorCode.INTERNAL_ERROR,
                safe_path="embedding_computation",
            )
        if (
            expected_embedding_adapter_ref is not None
            and getattr(emb_res, "adapter_artifact_ref", None) != expected_embedding_adapter_ref
        ):
            raise EvaluationValidationError(
                EvaluationErrorCode.INTERNAL_ERROR,
                safe_path="embedding_adapter_artifact_identity",
            )
        raw_vec = emb_res.embedding
    elif hasattr(embedding_port, "embed_text"):
        raw_vec = await embedding_port.embed_text(statement)
    else:
        raise EvaluationValidationError(
            EvaluationErrorCode.INTERNAL_ERROR,
            safe_path="embedding_port_interface",
        )

    if hasattr(raw_vec, "reveal"):
        return tuple(float(x) for x in raw_vec.reveal())
    if hasattr(raw_vec, "expose"):
        return tuple(float(x) for x in raw_vec.expose())
    return tuple(float(x) for x in raw_vec)


async def bootstrap_ret_h_smoke_stage2_knowledge_index(
    *,
    session_factory: SessionFactory,
    embedding_port: Any,
    fixture: SmokeSyntheticFixtureInput,
    stage1_snapshot_id: UUID,
    expected_embedding_adapter_ref: ImmutableArtifactRef | None = None,
) -> Stage2IndexReceipt:
    """Stage 2: fail-closed preflight, embedding computation, and Knowledge/Index build.

    Executed exclusively by KNOWLEDGE_INDEX_BUILDER.
    """
    if (
        expected_embedding_adapter_ref is not None
        and hasattr(embedding_port, "_adapter_artifact_ref")
        and embedding_port._adapter_artifact_ref != expected_embedding_adapter_ref
    ):
        raise EvaluationValidationError(
            EvaluationErrorCode.INTERNAL_ERROR,
            safe_path="embedding_adapter_artifact_identity",
        )

    doc_id = uuid5(NAMESPACE_SYNTHETIC_RET_H_SMOKE, f"doc:{fixture.records[0].evidence_ref_id}")
    chunk_id = uuid5(NAMESPACE_SYNTHETIC_RET_H_SMOKE, f"chunk:{fixture.records[0].evidence_ref_id}")

    # 1. READ-ONLY DB Preflight (fail-closed before embedding)
    async with session_factory() as session:
        member_id = await _preflight_stage2_source_snapshot(session, stage1_snapshot_id, fixture)

        # Idempotency check on Knowledge Index
        existing_idx = (
            (
                await session.execute(
                    text(
                        "SELECT id, index_configuration_hash FROM rag_knowledge_index "
                        "WHERE index_code = :code AND index_version = :ver"
                    ),
                    {"code": fixture.index_code, "ver": fixture.index_version},
                )
            )
            .mappings()
            .one_or_none()
        )

        if existing_idx is not None:
            existing_member_id = (
                await session.execute(
                    text("SELECT id FROM rag_knowledge_index_member WHERE knowledge_index_id = :kid"),
                    {"kid": str(existing_idx["id"])},
                )
            ).scalar_one_or_none()
            if existing_member_id is not None:
                return Stage2IndexReceipt(
                    knowledge_index_id=UUID(str(existing_idx["id"])),
                    index_code=fixture.index_code,
                    index_version=fixture.index_version,
                    index_configuration_hash=str(existing_idx["index_configuration_hash"]),
                    document_id=doc_id,
                    chunk_id=chunk_id,
                    member_id=UUID(str(existing_member_id)),
                    reused=True,
                )

    # 2. Compute embedding (fail-closed if embedding port fails)
    statement = fixture.records[0].statement
    vector = await _compute_stage2_embedding(
        embedding_port,
        statement,
        expected_embedding_adapter_ref=expected_embedding_adapter_ref,
    )

    # 3. Insert KnowledgeDocument and KnowledgeChunk
    async with session_factory() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO knowledge_document ("
                "id, title, publisher, source_url, document_version, document_status, record_contract_version, "
                "source_snapshot_member_id, external_document_id, document_content_hash, canonicalization_spec_version"
                ") VALUES ("
                ":id, 'Synthetic evidence', NULL, NULL, NULL, 'ACTIVE', 'KNOWLEDGE_EVIDENCE_V1', "
                ":member_id, :ext_id, :doc_hash, 'canonical-v1'"
                ") ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": str(doc_id),
                "member_id": str(member_id),
                "ext_id": fixture.records[0].evidence_ref_id,
                "doc_hash": fixture.records[0].content_sha256,
            },
        )
        await session.execute(
            text(
                "INSERT INTO knowledge_chunk ("
                "id, knowledge_document_id, chunk_index, chunk_text, embedding_model, vector_store_key, "
                "content_hash, normalization_version"
                ") VALUES ("
                ":id, :doc_id, 0, :text, NULL, NULL, :c_hash, 'normalization-v1'"
                ") ON CONFLICT (id) DO NOTHING"
            ),
            {
                "id": str(chunk_id),
                "doc_id": str(doc_id),
                "text": statement,
                "c_hash": fixture.records[0].content_sha256,
            },
        )

    # 4. Use production SqlAlchemyKnowledgeEvidenceIndexRepository to persist complete index
    index_repo = SqlAlchemyKnowledgeEvidenceIndexRepository(session_factory)
    request = KnowledgeIndexBuildRequest(
        index_code=fixture.index_code,
        index_version=fixture.index_version,
        embedding_model_ref="openai:text-embedding-3-large",
        embedding_model_version="text-embedding-3-large",
        embedding_dimension=1536,
        distance_metric=DistanceMetric.COSINE,
        members=(
            KnowledgeIndexMemberDraft(
                identity=KnowledgeChunkIdentity(
                    knowledge_chunk_id=chunk_id,
                    source_snapshot_id=stage1_snapshot_id,
                    source_snapshot_member_id=member_id,
                    source_code=fixture.source_code,
                    source_version="1.0.0",
                    canonical_checksum=fixture.file_sha256,
                    locator="$.records[0]",
                    external_document_id=fixture.records[0].evidence_ref_id,
                    chunk_index=0,
                    content_hash=fixture.records[0].content_sha256,
                ),
                content_text=SensitiveEvidenceText(statement),
                embedding=vector,
            ),
        ),
    )
    receipt = create_knowledge_index_receipt(request)
    persisted_receipt = await index_repo.persist_complete_index(request, receipt)

    # Fetch index and member IDs
    async with session_factory() as session:
        created_idx_id = (
            await session.execute(
                text("SELECT id FROM rag_knowledge_index WHERE index_code = :c AND index_version = :v"),
                {"c": fixture.index_code, "v": fixture.index_version},
            )
        ).scalar_one()
        created_member_id = (
            await session.execute(
                text("SELECT id FROM rag_knowledge_index_member WHERE knowledge_index_id = :kid"),
                {"kid": str(created_idx_id)},
            )
        ).scalar_one()

    return Stage2IndexReceipt(
        knowledge_index_id=UUID(str(created_idx_id)),
        index_code=fixture.index_code,
        index_version=fixture.index_version,
        index_configuration_hash=persisted_receipt.index_configuration_hash,
        document_id=doc_id,
        chunk_id=chunk_id,
        member_id=UUID(str(created_member_id)),
        reused=False,
    )


# --------------------------------------------------------------------------------------
# Runtime Parent Management (DB_APP_USER)
# --------------------------------------------------------------------------------------


async def ensure_synthetic_runtime_parent(
    session_factory: SessionFactory,
    *,
    user_id: UUID,
    job_id: UUID,
) -> None:
    """Ensure synthetic user and ai_job exist for the smoke execution.

    Executed exclusively by DB_APP_USER.
    """
    async with session_factory() as session, session.begin():
        # Check / insert synthetic user
        await session.execute(
            text(
                'INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin) '
                "VALUES (:id, :email, 'synthetic_smoke_password_hash', 'SMOKE_USER', false, false) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(user_id), "email": f"smoke-{user_id.hex[:12]}@internal.inv"},
        )
        # Check / insert synthetic ai_job
        await session.execute(
            text(
                "INSERT INTO ai_job (id, user_id, job_type, status, max_attempts, attempt_count) "
                "VALUES (:id, :uid, 'OCR', 'PENDING', 3, 0) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(job_id), "uid": str(user_id)},
        )


async def cleanup_synthetic_runtime_parent(
    session_factory: SessionFactory,
    *,
    user_id: UUID,
    job_id: UUID,
) -> None:
    """Clean up synthetic ai_job (cascading to retrieval_run/signal/hit) and user.

    Executed exclusively by DB_APP_USER.
    """
    async with session_factory() as session, session.begin():
        # Delete ai_job first; FK ON DELETE CASCADE removes retrieval_run, retrieval_signal, retrieval_hit
        await session.execute(text("DELETE FROM ai_job WHERE id = :id"), {"id": str(job_id)})
        # Delete synthetic user
        await session.execute(text('DELETE FROM "user" WHERE id = :id'), {"id": str(user_id)})

    # In a fresh session, verify absence of all synthetic records
    async with session_factory() as session:
        j_count = (
            await session.execute(text("SELECT COUNT(*) FROM ai_job WHERE id = :id"), {"id": str(job_id)})
        ).scalar_one()
        r_count = (
            await session.execute(text("SELECT COUNT(*) FROM retrieval_run WHERE job_id = :id"), {"id": str(job_id)})
        ).scalar_one()
        u_count = (
            await session.execute(text('SELECT COUNT(*) FROM "user" WHERE id = :id'), {"id": str(user_id)})
        ).scalar_one()

        if j_count != 0 or r_count != 0 or u_count != 0:
            raise RuntimeError(
                f"Synthetic runtime parent cleanup incomplete: jobs={j_count}, runs={r_count}, users={u_count}"
            )


# --------------------------------------------------------------------------------------
# Manifest Generation (Separately Credentialed / No DB Write)
# --------------------------------------------------------------------------------------


def _verify_manifest_consumer_round_trip(manifest_dict: Mapping[str, Any]) -> None:
    """Verify fixture manifest format and reconstruct HybridRetrieveRequest (#683 consumer contract)."""
    query_sentinel = str(manifest_dict.get("query_sentinel") or "").strip()
    source_sentinel = str(manifest_dict.get("source_sentinel") or "").strip()
    if not query_sentinel or not source_sentinel or query_sentinel == source_sentinel:
        raise EvaluationValidationError(
            EvaluationErrorCode.SCHEMA_INVALID,
            safe_path="sentinels",
        )

    synthetic_query = str(manifest_dict.get("synthetic_query") or "")
    if not synthetic_query.strip():
        raise EvaluationValidationError(
            EvaluationErrorCode.SCHEMA_INVALID,
            safe_path="synthetic_query",
        )

    approved_query_sha256 = str(manifest_dict.get("synthetic_query_sha256") or "").strip().lower()
    actual_sha256 = hashlib.sha256(synthetic_query.encode("utf-8")).hexdigest()
    if actual_sha256 != approved_query_sha256:
        raise EvaluationValidationError(
            EvaluationErrorCode.SCHEMA_INVALID,
            safe_path="synthetic_query_sha256",
        )

    if query_sentinel not in synthetic_query or source_sentinel in synthetic_query:
        raise EvaluationValidationError(
            EvaluationErrorCode.SCHEMA_INVALID,
            safe_path="query_sentinel_binding",
        )

    def _ref(key: str) -> ImmutableArtifactRef:
        raw = manifest_dict[key]
        return ImmutableArtifactRef(
            artifact_code=str(raw["artifact_code"]),
            version=str(raw["version"]),
            content_sha256=str(raw["content_sha256"]),
        )

    lexical_config = VersionedLexicalSearchConfiguration(artifact_ref=_ref("lexical_config_ref"))
    dense_config = VersionedDenseSearchConfiguration(artifact_ref=_ref("dense_config_ref"))
    retrieval_config = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=_ref("retrieval_config_ref"),
        lexical_config=lexical_config,
        dense_config=dense_config,
        expected_query_embedding_adapter_ref=_ref("embedding_adapter_ref"),
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
    )

    binding = EvidenceSearchExecutionBinding(
        filter_snapshot_ref=_ref("filter_snapshot_ref"),
        evidence_index_ref=_ref("evidence_index_ref"),
        knowledge_index_id=UUID(str(manifest_dict["knowledge_index_id"])),
        allowed_source_snapshot_ids=tuple(UUID(str(v)) for v in manifest_dict["allowed_source_snapshot_ids"]),
        allowed_source_snapshot_member_ids=tuple(
            UUID(str(v)) for v in manifest_dict["allowed_source_snapshot_member_ids"]
        ),
        retrieval_config=retrieval_config,
    )

    search_request = EvidenceSearchRequest(
        normalized_query=SensitiveText(synthetic_query),
        query_fingerprint=QueryFingerprint(
            algorithm="sha256",
            key_version="v1",
            digest=hashlib.sha256(synthetic_query.encode("utf-8")).hexdigest(),
        ),
        execution_binding=binding,
        query_embedding_receipt=None,
    )

    HybridRetrieveRequest(
        job_id=UUID(str(manifest_dict["job_id"])),
        execution_context_id=UUID(str(manifest_dict["execution_context_id"])),
        prescription_version_id=UUID(str(manifest_dict["prescription_version_id"])),
        runtime_release_bundle_id=UUID(str(manifest_dict["runtime_release_bundle_id"])),
        runtime_release_bundle_manifest_hash=str(manifest_dict["runtime_release_bundle_manifest_hash"]),
        runtime_execution_manifest_id=UUID(str(manifest_dict["runtime_execution_manifest_id"])),
        runtime_execution_manifest_hash=str(manifest_dict["runtime_execution_manifest_hash"]),
        runtime_guard_decision_ref=str(manifest_dict["runtime_guard_decision_ref"]),
        search_request=search_request,
        source_manifest_hash=str(manifest_dict["source_manifest_hash"]),
    )


def generate_ret_h_smoke_fixture_manifest(
    *,
    stage1_receipt: Stage1SourceReceipt,
    stage2_receipt: Stage2IndexReceipt,
    job_id: UUID,
    execution_context_id: UUID,
    prescription_version_id: UUID,
    provenance: RetHSmokeRuntimeProvenance,
    output_manifest_path: Path | str,
    fixture_input: SmokeSyntheticFixtureInput | None = None,
) -> Path:
    """Generate the approved fixture manifest for the #683 RET-H AWS synthetic smoke runner.

    Pipeline:
        Stage 1 receipt + Stage 2 receipt + IDs + RetHSmokeRuntimeProvenance
            -> build_ret_h_smoke_fixture_manifest()
            -> #683 fixture consumer/parser round-trip verification
            -> Atomic JSON write to output_manifest_path

    Constraints:
        - Never performs DB writes (Source/Builder/DB_APP).
        - Rejects placeholder provenance fail-closed.
        - Fails closed without writing if provenance is missing or invalid.
    """
    if provenance is None:
        raise ValueError("RetHSmokeRuntimeProvenance is required fail-closed")

    if fixture_input is None:
        fixture_input = load_ret_h_smoke_synthetic_fixture()

    manifest = build_ret_h_smoke_fixture_manifest(
        fixture_input=fixture_input,
        stage1_receipt=stage1_receipt,
        stage2_receipt=stage2_receipt,
        job_id=job_id,
        execution_context_id=execution_context_id,
        prescription_version_id=prescription_version_id,
        provenance=provenance,
    )

    # #683 consumer/parser round-trip verification
    serialized = json.dumps(manifest, indent=2)
    deserialized = json.loads(serialized)

    _verify_manifest_consumer_round_trip(deserialized)

    # Atomic write to output path
    out_path = Path(output_manifest_path).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = out_path.with_name(f".{out_path.name}.tmp.{uuid4().hex}")
    try:
        temp_path.write_text(serialized, encoding="utf-8")
        temp_path.replace(out_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise

    return out_path


# --------------------------------------------------------------------------------------
# CLI entrypoints for one-shot containers
# --------------------------------------------------------------------------------------


def _build_database_url_for_role(role_user: str, role_pass: str) -> str:
    db_host = os.environ.get("DB_HOST", "127.0.0.1")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "five_pills")
    return f"postgresql+asyncpg://{role_user}:{role_pass}@{db_host}:{db_port}/{db_name}"


async def _run_cli_stage1(output_receipt: Path | None = None) -> None:
    user = os.environ.get("SOURCE_WRITER_USER")
    password = os.environ.get("SOURCE_WRITER_PASSWORD")
    if not user or not password:
        logger.error("SOURCE_WRITER_USER and SOURCE_WRITER_PASSWORD must be set")
        sys.exit(1)

    url = _build_database_url_for_role(user, password)
    engine = create_async_engine(url, hide_parameters=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = load_ret_h_smoke_synthetic_fixture()

    try:
        receipt = await bootstrap_ret_h_smoke_stage1_source(
            session_factory=session_factory,
            fixture=fixture,
        )
        if output_receipt is not None:
            out = Path(output_receipt).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(f".{out.name}.tmp.{uuid4().hex}")
            tmp.write_text(json.dumps(stage1_receipt_to_dict(receipt), indent=2), encoding="utf-8")
            tmp.replace(out)
        print(f"Stage 1 completed successfully: snapshot_id={receipt.snapshot_id}, reused={receipt.reused}")
    finally:
        await engine.dispose()


async def _run_cli_stage2(
    output_receipt: Path | None = None,
    *,
    expected_embedding_adapter_ref: ImmutableArtifactRef = OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
) -> None:
    user = os.environ.get("KNOWLEDGE_INDEX_BUILDER_USER")
    password = os.environ.get("KNOWLEDGE_INDEX_BUILDER_PASSWORD")
    if not user or not password:
        logger.error("KNOWLEDGE_INDEX_BUILDER_USER and KNOWLEDGE_INDEX_BUILDER_PASSWORD must be set")
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY must be set for Stage 2 index builder")
        sys.exit(1)

    if expected_embedding_adapter_ref != OPENAI_TEXT_EMBEDDING_ADAPTER_REF:
        logger.error("Stage 2 requires canonical OPENAI_TEXT_EMBEDDING_ADAPTER_REF")
        sys.exit(1)

    url = _build_database_url_for_role(user, password)
    engine = create_async_engine(url, hide_parameters=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = load_ret_h_smoke_synthetic_fixture()

    from openai import AsyncOpenAI

    adapter = OpenAITextEmbeddingAdapter(
        client=AsyncOpenAI(api_key=api_key),
        adapter_artifact_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
    )

    stage1_snapshot_id = uuid5(NAMESPACE_SYNTHETIC_RET_H_SMOKE, f"snapshot:{fixture.file_sha256}")

    try:
        receipt = await bootstrap_ret_h_smoke_stage2_knowledge_index(
            session_factory=session_factory,
            embedding_port=adapter,
            fixture=fixture,
            stage1_snapshot_id=stage1_snapshot_id,
            expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        )
        if output_receipt is not None:
            out = Path(output_receipt).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(f".{out.name}.tmp.{uuid4().hex}")
            tmp.write_text(json.dumps(stage2_receipt_to_dict(receipt), indent=2), encoding="utf-8")
            tmp.replace(out)
        print(f"Stage 2 completed successfully: index_id={receipt.knowledge_index_id}, reused={receipt.reused}")
    finally:
        await engine.dispose()


def _run_cli_manifest(args: argparse.Namespace) -> None:
    stage1_receipt_path = Path(args.stage1_receipt)
    if not stage1_receipt_path.is_file():
        logger.error(f"Stage 1 receipt file not found: {stage1_receipt_path}")
        sys.exit(1)

    stage2_receipt_path = Path(args.stage2_receipt)
    if not stage2_receipt_path.is_file():
        logger.error(f"Stage 2 receipt file not found: {stage2_receipt_path}")
        sys.exit(1)

    provenance_file_path = Path(args.provenance_file)
    if not provenance_file_path.is_file():
        logger.error(f"Provenance file not found: {provenance_file_path}")
        sys.exit(1)

    try:
        s1_data = json.loads(stage1_receipt_path.read_text(encoding="utf-8"))
        stage1_receipt = stage1_receipt_from_dict(s1_data)

        s2_data = json.loads(stage2_receipt_path.read_text(encoding="utf-8"))
        stage2_receipt = stage2_receipt_from_dict(s2_data)

        prov_data = json.loads(provenance_file_path.read_text(encoding="utf-8"))
        provenance = RetHSmokeRuntimeProvenance(**prov_data)

        job_id = UUID(str(args.job_id))
        execution_context_id = UUID(str(args.execution_context_id))
        prescription_version_id = UUID(str(args.prescription_version_id))

        fixture = None
        if getattr(args, "fixture_path", None):
            fixture = load_ret_h_smoke_synthetic_fixture(Path(args.fixture_path))

        out_path = generate_ret_h_smoke_fixture_manifest(
            stage1_receipt=stage1_receipt,
            stage2_receipt=stage2_receipt,
            job_id=job_id,
            execution_context_id=execution_context_id,
            prescription_version_id=prescription_version_id,
            provenance=provenance,
            output_manifest_path=Path(args.output_manifest),
            fixture_input=fixture,
        )
        print(f"Fixture manifest successfully generated: {out_path}")
    except Exception as exc:
        logger.error(f"Manifest generation failed: {exc}")
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="RET-H AWS synthetic smoke bootstrap stages")
    subparsers = parser.add_subparsers(dest="stage", required=True)
    stage1_parser = subparsers.add_parser("stage1", help="Run Stage 1 Source bootstrap (SOURCE_WRITER)")
    stage1_parser.add_argument("--output-receipt", type=Path, required=False, help="Save receipt JSON to file")

    stage2_parser = subparsers.add_parser(
        "stage2", help="Run Stage 2 Knowledge Index bootstrap (KNOWLEDGE_INDEX_BUILDER)"
    )
    stage2_parser.add_argument("--output-receipt", type=Path, required=False, help="Save receipt JSON to file")

    manifest_parser = subparsers.add_parser(
        "manifest", help="Generate RET-H synthetic smoke fixture manifest (--output-manifest)"
    )
    manifest_parser.add_argument("--stage1-receipt", required=True, type=Path, help="Path to Stage 1 receipt JSON")
    manifest_parser.add_argument("--stage2-receipt", required=True, type=Path, help="Path to Stage 2 receipt JSON")
    manifest_parser.add_argument("--job-id", required=True, help="Job UUID")
    manifest_parser.add_argument("--execution-context-id", required=True, help="Execution context UUID")
    manifest_parser.add_argument("--prescription-version-id", required=True, help="Prescription version UUID")
    manifest_parser.add_argument("--provenance-file", required=True, type=Path, help="Path to pinned provenance JSON")
    manifest_parser.add_argument("--output-manifest", required=True, type=Path, help="Destination manifest path")
    manifest_parser.add_argument("--fixture-path", required=False, type=Path, help="Custom fixture JSON path")

    args = parser.parse_args(argv)
    if args.stage == "stage1":
        asyncio.run(_run_cli_stage1(output_receipt=getattr(args, "output_receipt", None)))
    elif args.stage == "stage2":
        asyncio.run(
            _run_cli_stage2(
                output_receipt=getattr(args, "output_receipt", None),
            )
        )
    elif args.stage == "manifest":
        _run_cli_manifest(args)


if __name__ == "__main__":
    main()
