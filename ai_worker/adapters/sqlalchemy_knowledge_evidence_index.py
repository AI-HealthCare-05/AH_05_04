"""Atomic SQLAlchemy adapter for completed Knowledge Evidence Index builds."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from uuid import UUID, uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import DateTime, Integer, String, and_, column, exists, insert, or_, select, table, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.knowledge_snapshot_advisory_lock import acquire_snapshot_advisory_locks
from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeChunkIdentity,
    KnowledgeEvidenceIndexFailureReason,
    KnowledgeEvidenceIndexValidationError,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexMemberDraft,
    KnowledgeIndexReceipt,
    SensitiveEvidenceText,
    canonical_embedding_sha256,
    create_knowledge_index_receipt,
)

SessionFactory = Callable[[], AsyncSession]


def _required_persisted_evidence_key(value: object) -> str:
    if not isinstance(value, str):
        raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.SOURCE_BINDING_INVALID)
    return value


_SOURCE = table(
    "rag_source",
    column("id", String(36)),
    column("source_code", String(100)),
    column("lifecycle_status", String(20)),
)
_ENDPOINT = table(
    "rag_source_endpoint",
    column("id", String(36)),
    column("source_id", String(36)),
    column("lifecycle_status", String(20)),
    column("runtime_status", String(20)),
    column("acquisition_status", String(20)),
)
_OPERATION = table(
    "rag_source_operation",
    column("id", String(36)),
    column("endpoint_id", String(36)),
    column("runtime_status", String(20)),
    column("acquisition_status", String(20)),
)
_SNAPSHOT = table(
    "rag_source_snapshot",
    column("id", String(36)),
    column("operation_id", String(36)),
    column("source_version", String(200)),
    column("canonical_checksum", String(64)),
    column("verification_status", String(20)),
)
_SNAPSHOT_MEMBER = table(
    "rag_source_snapshot_member",
    column("id", String(36)),
    column("source_snapshot_id", String(36)),
    column("member_kind", String(30)),
    column("endpoint_id", String(36)),
    column("operation_id", String(36)),
    column("ingestion_artifact_id", String(36)),
    column("locator", String(500)),
    column("content_sha256", String(64)),
)
_INGESTION_RUN = table(
    "rag_source_ingestion_run",
    column("id", String(36)),
    column("operation_id", String(36)),
    column("snapshot_id", String(36)),
)
_INGESTION_ARTIFACT = table(
    "rag_source_ingestion_artifact",
    column("id", String(36)),
    column("ingestion_run_id", String(36)),
)
_DOCUMENT = table(
    "knowledge_document",
    column("id", String(36)),
    column("record_contract_version", String(40)),
    column("document_status", String(20)),
    column("source_snapshot_member_id", String(36)),
    column("external_document_id", String(300)),
    column("document_content_hash", String(64)),
    column("canonicalization_spec_version", String(100)),
)
_CHUNK = table(
    "knowledge_chunk",
    column("id", String(36)),
    column("knowledge_document_id", String(36)),
    column("chunk_index", Integer),
    column("chunk_text", String),
    column("content_hash", String(64)),
    column("normalization_version", String(100)),
)
_INDEX = table(
    "rag_knowledge_index",
    column("id", String(36)),
    column("index_code", String(120)),
    column("index_version", String(80)),
    column("corpus_manifest_hash", String(64)),
    column("embedding_manifest_hash", String(64)),
    column("index_configuration_hash", String(64)),
    column("embedding_model_ref", String(255)),
    column("embedding_model_version", String(80)),
    column("embedding_dimension", Integer),
    column("distance_metric", String(20)),
    column("member_count", Integer),
    column("created_at", DateTime(timezone=True)),
)
_INDEX_MEMBER = table(
    "rag_knowledge_index_member",
    column("id", String(36)),
    column("knowledge_index_id", String(36)),
    column("knowledge_chunk_id", String(36)),
    column("evidence_key", String(300)),
    column("source_snapshot_id", String(36)),
    column("source_snapshot_member_id", String(36)),
    column("source_code", String(100)),
    column("source_version", String(200)),
    column("canonical_checksum", String(64)),
    column("external_document_id", String(300)),
    column("chunk_index", Integer),
    column("content_hash", String(64)),
    column("embedding", VECTOR()),
    column("embedding_sha256", String(64)),
    column("member_order", Integer),
    column("created_at", DateTime(timezone=True)),
)


class SqlAlchemyKnowledgeEvidenceIndexRepository:
    """Owns one transaction per completed index build; exposes no update/delete path."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def persist_complete_index(
        self,
        request: KnowledgeIndexBuildRequest,
        receipt: KnowledgeIndexReceipt,
    ) -> KnowledgeIndexReceipt:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:version_key, 0))"),
                {"version_key": f"{request.index_code}:{request.index_version}"},
            )
            snapshot_ids = {member.identity.source_snapshot_id for member in request.members}
            await acquire_snapshot_advisory_locks(session, snapshot_ids)
            await self._lock_and_validate_members(session, request)
            existing_id = await session.scalar(
                select(_INDEX.c.id)
                .where(
                    _INDEX.c.index_code == request.index_code,
                    _INDEX.c.index_version == request.index_version,
                )
                .with_for_update(of=_INDEX)
            )
            if existing_id is not None:
                observed = await self._load_and_recompute_receipt(session, UUID(str(existing_id)))
                if observed != receipt:
                    raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.VERSION_CONFLICT)
                return observed

            index_id = uuid4()
            await session.execute(
                insert(_INDEX).values(
                    id=str(index_id),
                    index_code=receipt.index_code,
                    index_version=receipt.index_version,
                    corpus_manifest_hash=receipt.corpus_manifest_hash,
                    embedding_manifest_hash=receipt.embedding_manifest_hash,
                    index_configuration_hash=receipt.index_configuration_hash,
                    embedding_model_ref=receipt.embedding_model_ref,
                    embedding_model_version=receipt.embedding_model_version,
                    embedding_dimension=receipt.embedding_dimension,
                    distance_metric=receipt.distance_metric.value,
                    member_count=receipt.member_count,
                )
            )
            ordered = sorted(request.members, key=_member_sort_key)
            await session.execute(
                insert(_INDEX_MEMBER),
                [
                    {
                        "id": str(uuid4()),
                        "knowledge_index_id": str(index_id),
                        "knowledge_chunk_id": str(member.identity.knowledge_chunk_id),
                        "evidence_key": member.identity.evidence_key,
                        "source_snapshot_id": str(member.identity.source_snapshot_id),
                        "source_snapshot_member_id": str(member.identity.source_snapshot_member_id),
                        "source_code": member.identity.source_code,
                        "source_version": member.identity.source_version,
                        "canonical_checksum": member.identity.canonical_checksum,
                        "external_document_id": member.identity.external_document_id,
                        "chunk_index": member.identity.chunk_index,
                        "content_hash": member.identity.content_hash,
                        "embedding": list(member.embedding),
                        "embedding_sha256": canonical_embedding_sha256(member.embedding),
                        "member_order": position,
                    }
                    for position, member in enumerate(ordered, start=1)
                ],
            )
            observed = await self._load_and_recompute_receipt(session, index_id)
            if observed != receipt:
                raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.RECEIPT_MISMATCH)
            return observed

    async def _lock_and_validate_members(
        self,
        session: AsyncSession,
        request: KnowledgeIndexBuildRequest,
    ) -> None:
        for member in sorted(request.members, key=lambda item: item.identity.knowledge_chunk_id.bytes):
            row = (await session.execute(_source_binding_statement(member))).mappings().one_or_none()
            if row is None or hashlib.sha256(str(row["chunk_text"]).encode("utf-8")).hexdigest() != (
                member.identity.content_hash
            ):
                raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.SOURCE_BINDING_INVALID)
            if row["member_kind"] == "ARTIFACT":
                artifact = (await session.execute(_artifact_origin_lock_statement(member))).scalar_one_or_none()
                if artifact is None:
                    raise KnowledgeEvidenceIndexValidationError(
                        KnowledgeEvidenceIndexFailureReason.SOURCE_BINDING_INVALID
                    )

    async def _load_and_recompute_receipt(
        self,
        session: AsyncSession,
        index_id: UUID,
    ) -> KnowledgeIndexReceipt:
        index_row = (await session.execute(select(_INDEX).where(_INDEX.c.id == str(index_id)))).mappings().one_or_none()
        if index_row is None:
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.RECEIPT_MISMATCH)
        rows = (
            (
                await session.execute(
                    select(
                        _INDEX_MEMBER,
                        _SNAPSHOT_MEMBER.c.locator,
                        _CHUNK.c.chunk_text,
                    )
                    .select_from(
                        _INDEX_MEMBER.join(
                            _SNAPSHOT_MEMBER,
                            _SNAPSHOT_MEMBER.c.id == _INDEX_MEMBER.c.source_snapshot_member_id,
                        ).join(_CHUNK, _CHUNK.c.id == _INDEX_MEMBER.c.knowledge_chunk_id)
                    )
                    .where(_INDEX_MEMBER.c.knowledge_index_id == str(index_id))
                    .order_by(_INDEX_MEMBER.c.member_order)
                )
            )
            .mappings()
            .all()
        )
        rebuilt = _request_from_persisted(index_row, rows)
        recomputed = create_knowledge_index_receipt(rebuilt)
        if recomputed != _receipt_from_index_row(index_row):
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.RECEIPT_MISMATCH)
        return recomputed


def _source_binding_statement(member: KnowledgeIndexMemberDraft):
    identity = member.identity
    source_chain = (
        _SOURCE.join(_ENDPOINT, _ENDPOINT.c.source_id == _SOURCE.c.id)
        .join(_OPERATION, _OPERATION.c.endpoint_id == _ENDPOINT.c.id)
        .join(_SNAPSHOT, _SNAPSHOT.c.operation_id == _OPERATION.c.id)
        .join(_SNAPSHOT_MEMBER, _SNAPSHOT_MEMBER.c.source_snapshot_id == _SNAPSHOT.c.id)
        .join(_DOCUMENT, _DOCUMENT.c.source_snapshot_member_id == _SNAPSHOT_MEMBER.c.id)
        .join(_CHUNK, _CHUNK.c.knowledge_document_id == _DOCUMENT.c.id)
    )
    member_origin_matches = or_(
        and_(
            _SNAPSHOT_MEMBER.c.member_kind == "ENDPOINT_OPERATION",
            _SNAPSHOT_MEMBER.c.endpoint_id == _ENDPOINT.c.id,
            or_(_SNAPSHOT_MEMBER.c.operation_id.is_(None), _SNAPSHOT_MEMBER.c.operation_id == _OPERATION.c.id),
            _SNAPSHOT_MEMBER.c.ingestion_artifact_id.is_(None),
        ),
        and_(
            _SNAPSHOT_MEMBER.c.member_kind == "ARTIFACT",
            _SNAPSHOT_MEMBER.c.endpoint_id.is_(None),
            _SNAPSHOT_MEMBER.c.operation_id.is_(None),
            exists(
                select(1)
                .select_from(
                    _INGESTION_ARTIFACT.join(
                        _INGESTION_RUN,
                        _INGESTION_RUN.c.id == _INGESTION_ARTIFACT.c.ingestion_run_id,
                    )
                )
                .where(
                    _INGESTION_ARTIFACT.c.id == _SNAPSHOT_MEMBER.c.ingestion_artifact_id,
                    _INGESTION_RUN.c.snapshot_id == _SNAPSHOT.c.id,
                    _INGESTION_RUN.c.operation_id == _OPERATION.c.id,
                )
            ),
        ),
    )
    return (
        select(_CHUNK.c.id, _CHUNK.c.chunk_text, _SNAPSHOT_MEMBER.c.member_kind)
        .select_from(source_chain)
        .where(
            _SOURCE.c.source_code == identity.source_code,
            _SOURCE.c.lifecycle_status == "ACTIVE",
            _ENDPOINT.c.lifecycle_status == "VERIFIED",
            _ENDPOINT.c.runtime_status == "ENABLED",
            _ENDPOINT.c.acquisition_status == "APPROVED",
            _OPERATION.c.runtime_status == "ENABLED",
            _OPERATION.c.acquisition_status == "APPROVED",
            _SNAPSHOT.c.id == str(identity.source_snapshot_id),
            _SNAPSHOT.c.source_version == identity.source_version,
            _SNAPSHOT.c.canonical_checksum == identity.canonical_checksum,
            _SNAPSHOT.c.verification_status == "CURRENT",
            _SNAPSHOT_MEMBER.c.id == str(identity.source_snapshot_member_id),
            _SNAPSHOT_MEMBER.c.locator == identity.locator,
            member_origin_matches,
            _DOCUMENT.c.record_contract_version == "KNOWLEDGE_EVIDENCE_V1",
            _DOCUMENT.c.document_status == "ACTIVE",
            _DOCUMENT.c.external_document_id == identity.external_document_id,
            _DOCUMENT.c.document_content_hash == _SNAPSHOT_MEMBER.c.content_sha256,
            _DOCUMENT.c.canonicalization_spec_version.is_not(None),
            _CHUNK.c.id == str(identity.knowledge_chunk_id),
            _CHUNK.c.chunk_index == identity.chunk_index,
            _CHUNK.c.content_hash == identity.content_hash,
            _CHUNK.c.normalization_version.is_not(None),
        )
        .with_for_update(of=[_SOURCE, _ENDPOINT, _OPERATION, _SNAPSHOT, _SNAPSHOT_MEMBER, _DOCUMENT, _CHUNK])
    )


def _artifact_origin_lock_statement(member: KnowledgeIndexMemberDraft):
    identity = member.identity
    chain = (
        _SNAPSHOT_MEMBER.join(
            _INGESTION_ARTIFACT,
            _INGESTION_ARTIFACT.c.id == _SNAPSHOT_MEMBER.c.ingestion_artifact_id,
        )
        .join(_INGESTION_RUN, _INGESTION_RUN.c.id == _INGESTION_ARTIFACT.c.ingestion_run_id)
        .join(_SNAPSHOT, _SNAPSHOT.c.id == _INGESTION_RUN.c.snapshot_id)
    )
    return (
        select(_INGESTION_ARTIFACT.c.id)
        .select_from(chain)
        .where(
            _SNAPSHOT_MEMBER.c.id == str(identity.source_snapshot_member_id),
            _SNAPSHOT_MEMBER.c.member_kind == "ARTIFACT",
            _SNAPSHOT_MEMBER.c.source_snapshot_id == str(identity.source_snapshot_id),
            _SNAPSHOT.c.id == str(identity.source_snapshot_id),
            _INGESTION_RUN.c.operation_id == _SNAPSHOT.c.operation_id,
        )
        .with_for_update(of=[_INGESTION_RUN, _INGESTION_ARTIFACT])
    )


def _request_from_persisted(index_row: RowMapping, rows: Sequence[RowMapping]) -> KnowledgeIndexBuildRequest:
    members = tuple(
        KnowledgeIndexMemberDraft(
            identity=KnowledgeChunkIdentity(
                knowledge_chunk_id=UUID(str(row["knowledge_chunk_id"])),
                evidence_key=_required_persisted_evidence_key(row["evidence_key"]),
                source_snapshot_id=UUID(str(row["source_snapshot_id"])),
                source_snapshot_member_id=UUID(str(row["source_snapshot_member_id"])),
                source_code=str(row["source_code"]),
                source_version=str(row["source_version"]),
                canonical_checksum=str(row["canonical_checksum"]),
                external_document_id=str(row["external_document_id"]),
                chunk_index=int(row["chunk_index"]),
                content_hash=str(row["content_hash"]),
                locator=str(row["locator"]),
            ),
            content_text=SensitiveEvidenceText(str(row["chunk_text"])),
            embedding=tuple(float(item) for item in row["embedding"]),
        )
        for row in rows
    )
    return KnowledgeIndexBuildRequest(
        index_code=str(index_row["index_code"]),
        index_version=str(index_row["index_version"]),
        embedding_model_ref=str(index_row["embedding_model_ref"]),
        embedding_model_version=str(index_row["embedding_model_version"]),
        embedding_dimension=int(index_row["embedding_dimension"]),
        distance_metric=DistanceMetric(str(index_row["distance_metric"])),
        members=members,
    )


def _receipt_from_index_row(row: RowMapping) -> KnowledgeIndexReceipt:
    return KnowledgeIndexReceipt(
        index_code=str(row["index_code"]),
        index_version=str(row["index_version"]),
        corpus_manifest_hash=str(row["corpus_manifest_hash"]),
        embedding_manifest_hash=str(row["embedding_manifest_hash"]),
        index_configuration_hash=str(row["index_configuration_hash"]),
        embedding_model_ref=str(row["embedding_model_ref"]),
        embedding_model_version=str(row["embedding_model_version"]),
        embedding_dimension=int(row["embedding_dimension"]),
        distance_metric=DistanceMetric(str(row["distance_metric"])),
        member_count=int(row["member_count"]),
    )


def _member_sort_key(member: KnowledgeIndexMemberDraft) -> tuple[bytes, bytes, bytes, int]:
    identity = member.identity
    return (
        identity.source_code.encode(),
        identity.source_version.encode(),
        identity.external_document_id.encode(),
        identity.chunk_index,
    )


__all__ = ["SqlAlchemyKnowledgeEvidenceIndexRepository"]
