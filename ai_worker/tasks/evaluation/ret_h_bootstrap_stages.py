"""Separate bootstrap authority stages for RET-H AWS synthetic smoke (#684).

Implements the 2-stage bootstrap authority split:
- Stage 1 (SOURCE_WRITER): Source/Snapshot/Member/Seal creation.
- Stage 2 (KNOWLEDGE_INDEX_BUILDER): Read-only DB preflight and Knowledge/Index materialization.
- Runtime parent lifecycle and cascade cleanup (DB_APP_USER).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_evaluation_bootstrap_repository import (
    SqlAlchemyEvaluationBootstrapRepository,
)
from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    SqlAlchemyKnowledgeEvidenceIndexRepository,
)
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.resources import (
    SmokeSyntheticFixtureInput,
    load_ret_h_smoke_synthetic_fixture,
)
from ai_worker.tasks.rag.evidence_retrieval import SensitiveText
from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeChunkIdentity,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexMemberDraft,
    SensitiveEvidenceText,
    create_knowledge_index_receipt,
)
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


async def _compute_stage2_embedding(embedding_port: Any, statement: str) -> tuple[float, ...]:
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
) -> Stage2IndexReceipt:
    """Stage 2: fail-closed preflight, embedding computation, and Knowledge/Index build.

    Executed exclusively by KNOWLEDGE_INDEX_BUILDER.
    """
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
    vector = await _compute_stage2_embedding(embedding_port, statement)

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
# CLI entrypoints for one-shot containers
# --------------------------------------------------------------------------------------


def _build_database_url_for_role(role_user: str, role_pass: str) -> str:
    db_host = os.environ.get("DB_HOST", "127.0.0.1")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "five_pills")
    return f"postgresql+asyncpg://{role_user}:{role_pass}@{db_host}:{db_port}/{db_name}"


async def _run_cli_stage1() -> None:
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
        print(f"Stage 1 completed successfully: snapshot_id={receipt.snapshot_id}, reused={receipt.reused}")
    finally:
        await engine.dispose()


async def _run_cli_stage2(embedding_adapter_sha256: str | None = None) -> None:
    user = os.environ.get("KNOWLEDGE_INDEX_BUILDER_USER")
    password = os.environ.get("KNOWLEDGE_INDEX_BUILDER_PASSWORD")
    if not user or not password:
        logger.error("KNOWLEDGE_INDEX_BUILDER_USER and KNOWLEDGE_INDEX_BUILDER_PASSWORD must be set")
        sys.exit(1)

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY must be set for Stage 2 index builder")
        sys.exit(1)

    emb_sha = (embedding_adapter_sha256 or os.environ.get("EMBEDDING_ADAPTER_SHA256") or "").strip().lower()
    if not emb_sha or len(emb_sha) != 64 or emb_sha == "0" * 64 or not all(c in "0123456789abcdef" for c in emb_sha):
        logger.error("A valid 64-character lowercase hex EMBEDDING_ADAPTER_SHA256 must be provided")
        sys.exit(1)

    url = _build_database_url_for_role(user, password)
    engine = create_async_engine(url, hide_parameters=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    fixture = load_ret_h_smoke_synthetic_fixture()

    from openai import AsyncOpenAI

    from ai_worker.adapters.openai_text_embedding import OpenAITextEmbeddingAdapter
    from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef

    adapter = OpenAITextEmbeddingAdapter(
        client=AsyncOpenAI(api_key=api_key),
        adapter_artifact_ref=ImmutableArtifactRef("openai-text-embedding-adapter", "1.0.0", emb_sha),
    )

    stage1_snapshot_id = uuid5(NAMESPACE_SYNTHETIC_RET_H_SMOKE, f"snapshot:{fixture.file_sha256}")

    try:
        receipt = await bootstrap_ret_h_smoke_stage2_knowledge_index(
            session_factory=session_factory,
            embedding_port=adapter,
            fixture=fixture,
            stage1_snapshot_id=stage1_snapshot_id,
        )
        print(f"Stage 2 completed successfully: index_id={receipt.knowledge_index_id}, reused={receipt.reused}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="RET-H AWS synthetic smoke bootstrap stages")
    subparsers = parser.add_subparsers(dest="stage", required=True)
    subparsers.add_parser("stage1", help="Run Stage 1 Source bootstrap (SOURCE_WRITER)")
    stage2_parser = subparsers.add_parser(
        "stage2", help="Run Stage 2 Knowledge Index bootstrap (KNOWLEDGE_INDEX_BUILDER)"
    )
    stage2_parser.add_argument(
        "--embedding-adapter-sha256",
        required=False,
        help="SHA256 hash of openai-text-embedding-adapter",
    )

    args = parser.parse_args()
    if args.stage == "stage1":
        asyncio.run(_run_cli_stage1())
    elif args.stage == "stage2":
        asyncio.run(_run_cli_stage2(embedding_adapter_sha256=getattr(args, "embedding_adapter_sha256", None)))


if __name__ == "__main__":
    main()
