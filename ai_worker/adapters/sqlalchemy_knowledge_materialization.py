"""Atomic SQLAlchemy adapter for MFDS Knowledge Materialization."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID, uuid4

from sqlalchemy import Integer, String, and_, column, insert, select, table
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.knowledge_snapshot_advisory_lock import acquire_snapshot_advisory_locks
from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.tasks.rag.knowledge_materialization import (
    SECTION_ORDER,
    KnowledgeDocumentDraft,
    KnowledgeMaterializationError,
    KnowledgeMaterializationFailureReason,
    KnowledgeMaterializationReceipt,
    KnowledgeMaterializationRequest,
    KnowledgeMaterializationResult,
    MaterializationOutcome,
    MaterializationSourceDocument,
    MaterializedChunkReceipt,
    MaterializedDocumentReceipt,
)
from ai_worker.tasks.rag.source_ingestion.mfds_label import LOCAL_PRIVATE_STORAGE_BACKEND

SessionFactory = Callable[[], AsyncSession]

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
    column("endpoint_code", String(100)),
    column("lifecycle_status", String(20)),
    column("runtime_status", String(20)),
    column("acquisition_status", String(20)),
)
_OPERATION = table(
    "rag_source_operation",
    column("id", String(36)),
    column("endpoint_id", String(36)),
    column("operation_code", String(100)),
    column("runtime_status", String(20)),
    column("acquisition_status", String(20)),
)
_SNAPSHOT = table(
    "rag_source_snapshot",
    column("id", String(36)),
    column("operation_id", String(36)),
    column("source_version", String(200)),
    column("canonical_checksum", String(64)),
    column("raw_manifest_checksum", String(64)),
    column("schema_version", String(100)),
    column("parser_version", String(100)),
    column("normalization_version", String(100)),
    column("canonicalization_spec_version", String(100)),
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
    column("run_status", String(40)),
)
_INGESTION_ARTIFACT = table(
    "rag_source_ingestion_artifact",
    column("id", String(36)),
    column("ingestion_run_id", String(36)),
    column("artifact_key", String(500)),
    column("artifact_kind", String(40)),
    column("page_number", Integer),
    column("storage_backend", String(50)),
    column("reject_code", String(100)),
    column("parser_location", String(500)),
    column("raw_checksum", String(64)),
    column("byte_size", Integer),
    column("content_type", String(100)),
    column("object_key", String(500)),
)
_DOCUMENT = table(
    "knowledge_document",
    column("id", String(36)),
    column("title", String(500)),
    column("publisher", String(255)),
    column("source_url", String(500)),
    column("document_version", String(100)),
    column("record_contract_version", String(40)),
    column("source_snapshot_member_id", String(36)),
    column("external_document_id", String(300)),
    column("document_content_hash", String(64)),
    column("canonicalization_spec_version", String(100)),
    column("knowledge_index_lock_marker", Integer),
    column("document_status", String(20)),
)
_CHUNK = table(
    "knowledge_chunk",
    column("id", String(36)),
    column("knowledge_document_id", String(36)),
    column("chunk_index", Integer),
    column("chunk_text", String),
    column("embedding_model", String(255)),
    column("vector_store_key", String(255)),
    column("content_hash", String(64)),
    column("normalization_version", String(100)),
    column("knowledge_index_lock_marker", Integer),
)


def _fetch_source_documents_statement(snapshot_id: UUID, member_ids: Sequence[UUID]):
    chain = (
        _SOURCE.join(_ENDPOINT, _ENDPOINT.c.source_id == _SOURCE.c.id)
        .join(_OPERATION, _OPERATION.c.endpoint_id == _ENDPOINT.c.id)
        .join(_SNAPSHOT, _SNAPSHOT.c.operation_id == _OPERATION.c.id)
        .join(_SNAPSHOT_MEMBER, _SNAPSHOT_MEMBER.c.source_snapshot_id == _SNAPSHOT.c.id)
        .join(
            _INGESTION_ARTIFACT,
            _INGESTION_ARTIFACT.c.id == _SNAPSHOT_MEMBER.c.ingestion_artifact_id,
        )
        .join(
            _INGESTION_RUN,
            and_(
                _INGESTION_RUN.c.id == _INGESTION_ARTIFACT.c.ingestion_run_id,
                _INGESTION_RUN.c.operation_id == _OPERATION.c.id,
            ),
        )
    )
    return (
        select(
            _SOURCE.c.id.label("source_id"),
            _SOURCE.c.source_code,
            _SOURCE.c.lifecycle_status.label("source_lifecycle_status"),
            _ENDPOINT.c.id.label("endpoint_id"),
            _ENDPOINT.c.endpoint_code,
            _ENDPOINT.c.lifecycle_status.label("endpoint_lifecycle_status"),
            _ENDPOINT.c.runtime_status.label("endpoint_runtime_status"),
            _ENDPOINT.c.acquisition_status.label("endpoint_acquisition_status"),
            _OPERATION.c.id.label("operation_id"),
            _OPERATION.c.operation_code,
            _OPERATION.c.runtime_status.label("operation_runtime_status"),
            _OPERATION.c.acquisition_status.label("operation_acquisition_status"),
            _SNAPSHOT.c.id.label("snapshot_id"),
            _SNAPSHOT.c.source_version,
            _SNAPSHOT.c.canonical_checksum,
            _SNAPSHOT.c.raw_manifest_checksum,
            _SNAPSHOT.c.schema_version,
            _SNAPSHOT.c.parser_version,
            _SNAPSHOT.c.normalization_version,
            _SNAPSHOT.c.canonicalization_spec_version,
            _SNAPSHOT.c.verification_status.label("snapshot_verification_status"),
            _SNAPSHOT_MEMBER.c.id.label("member_id"),
            _SNAPSHOT_MEMBER.c.member_kind,
            _SNAPSHOT_MEMBER.c.locator,
            _SNAPSHOT_MEMBER.c.content_sha256,
            _INGESTION_RUN.c.id.label("ingestion_run_id"),
            _INGESTION_RUN.c.run_status.label("ingestion_run_status"),
            _INGESTION_ARTIFACT.c.id.label("ingestion_artifact_id"),
            _INGESTION_ARTIFACT.c.artifact_key,
            _INGESTION_ARTIFACT.c.artifact_kind,
            _INGESTION_ARTIFACT.c.page_number,
            _INGESTION_ARTIFACT.c.storage_backend,
            _INGESTION_ARTIFACT.c.reject_code,
            _INGESTION_ARTIFACT.c.parser_location,
            _INGESTION_ARTIFACT.c.raw_checksum,
            _INGESTION_ARTIFACT.c.byte_size,
            _INGESTION_ARTIFACT.c.content_type,
            _INGESTION_ARTIFACT.c.object_key,
        )
        .select_from(chain)
        .where(
            _SNAPSHOT.c.id == str(snapshot_id),
            _SNAPSHOT_MEMBER.c.id.in_([str(m) for m in member_ids]),
            _SNAPSHOT_MEMBER.c.member_kind == "ARTIFACT",
        )
    )


def _materialization_row_lock_statement(snapshot_id: UUID, member_ids: Sequence[UUID]):
    chain = (
        _SOURCE.join(_ENDPOINT, _ENDPOINT.c.source_id == _SOURCE.c.id)
        .join(_OPERATION, _OPERATION.c.endpoint_id == _ENDPOINT.c.id)
        .join(_SNAPSHOT, _SNAPSHOT.c.operation_id == _OPERATION.c.id)
        .join(_SNAPSHOT_MEMBER, _SNAPSHOT_MEMBER.c.source_snapshot_id == _SNAPSHOT.c.id)
        .join(
            _INGESTION_ARTIFACT,
            _INGESTION_ARTIFACT.c.id == _SNAPSHOT_MEMBER.c.ingestion_artifact_id,
        )
        .join(
            _INGESTION_RUN,
            and_(
                _INGESTION_RUN.c.id == _INGESTION_ARTIFACT.c.ingestion_run_id,
                _INGESTION_RUN.c.operation_id == _OPERATION.c.id,
            ),
        )
    )
    return (
        select(
            _SOURCE.c.id.label("source_id"),
            _SOURCE.c.source_code,
            _SOURCE.c.lifecycle_status.label("source_lifecycle_status"),
            _ENDPOINT.c.id.label("endpoint_id"),
            _ENDPOINT.c.endpoint_code,
            _ENDPOINT.c.lifecycle_status.label("endpoint_lifecycle_status"),
            _ENDPOINT.c.runtime_status.label("endpoint_runtime_status"),
            _ENDPOINT.c.acquisition_status.label("endpoint_acquisition_status"),
            _OPERATION.c.id.label("operation_id"),
            _OPERATION.c.operation_code,
            _OPERATION.c.runtime_status.label("operation_runtime_status"),
            _OPERATION.c.acquisition_status.label("operation_acquisition_status"),
            _SNAPSHOT.c.id.label("snapshot_id"),
            _SNAPSHOT.c.source_version,
            _SNAPSHOT.c.canonical_checksum,
            _SNAPSHOT.c.raw_manifest_checksum,
            _SNAPSHOT.c.schema_version,
            _SNAPSHOT.c.parser_version,
            _SNAPSHOT.c.normalization_version,
            _SNAPSHOT.c.canonicalization_spec_version,
            _SNAPSHOT.c.verification_status.label("snapshot_verification_status"),
            _SNAPSHOT_MEMBER.c.id.label("member_id"),
            _SNAPSHOT_MEMBER.c.member_kind,
            _SNAPSHOT_MEMBER.c.locator,
            _SNAPSHOT_MEMBER.c.content_sha256,
            _INGESTION_RUN.c.id.label("ingestion_run_id"),
            _INGESTION_RUN.c.run_status.label("ingestion_run_status"),
            _INGESTION_ARTIFACT.c.id.label("ingestion_artifact_id"),
            _INGESTION_ARTIFACT.c.artifact_key,
            _INGESTION_ARTIFACT.c.artifact_kind,
            _INGESTION_ARTIFACT.c.page_number,
            _INGESTION_ARTIFACT.c.storage_backend,
            _INGESTION_ARTIFACT.c.reject_code,
            _INGESTION_ARTIFACT.c.parser_location,
            _INGESTION_ARTIFACT.c.raw_checksum,
            _INGESTION_ARTIFACT.c.byte_size,
            _INGESTION_ARTIFACT.c.content_type,
            _INGESTION_ARTIFACT.c.object_key,
        )
        .select_from(chain)
        .where(
            _SNAPSHOT.c.id == str(snapshot_id),
            _SNAPSHOT_MEMBER.c.id.in_([str(m) for m in member_ids]),
        )
        .with_for_update(
            of=(
                _SOURCE,
                _ENDPOINT,
                _OPERATION,
                _SNAPSHOT,
                _SNAPSHOT_MEMBER,
                _INGESTION_RUN,
                _INGESTION_ARTIFACT,
            )
        )
    )


class SqlAlchemyKnowledgeMaterializationRepository:
    """Atomic SQLAlchemy repository for Knowledge Materialization."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def fetch_source_documents(
        self,
        snapshot_id: UUID,
        member_ids: Sequence[UUID],
    ) -> tuple[MaterializationSourceDocument, ...]:
        async with self._session_factory() as session:
            statement = _fetch_source_documents_statement(snapshot_id, member_ids)
            result = await session.execute(statement)
            rows = result.mappings().all()

            docs: list[MaterializationSourceDocument] = []
            for row in rows:
                locator = str(row["locator"])
                section = locator.split("/")[-1]
                docs.append(
                    MaterializationSourceDocument(
                        source_id=UUID(str(row["source_id"])),
                        source_code=str(row["source_code"]),
                        source_lifecycle_status=str(row["source_lifecycle_status"]),
                        endpoint_id=UUID(str(row["endpoint_id"])),
                        endpoint_code=str(row["endpoint_code"]),
                        endpoint_lifecycle_status=str(row["endpoint_lifecycle_status"]),
                        endpoint_runtime_status=str(row["endpoint_runtime_status"]),
                        endpoint_acquisition_status=str(row["endpoint_acquisition_status"]),
                        operation_id=UUID(str(row["operation_id"])),
                        operation_code=str(row["operation_code"]),
                        operation_runtime_status=str(row["operation_runtime_status"]),
                        operation_acquisition_status=str(row["operation_acquisition_status"]),
                        snapshot_id=UUID(str(row["snapshot_id"])),
                        source_version=str(row["source_version"]),
                        canonical_checksum=str(row["canonical_checksum"]),
                        raw_manifest_checksum=str(row["raw_manifest_checksum"]),
                        schema_version=str(row["schema_version"]),
                        parser_version=str(row["parser_version"]),
                        normalization_version=str(row["normalization_version"]),
                        canonicalization_spec_version=str(row["canonicalization_spec_version"]),
                        snapshot_verification_status=str(row["snapshot_verification_status"]),
                        ingestion_run_id=UUID(str(row["ingestion_run_id"])),
                        ingestion_run_status=str(row["ingestion_run_status"]),
                        member_id=UUID(str(row["member_id"])),
                        member_kind=str(row["member_kind"]),
                        locator=locator,
                        content_sha256=str(row["content_sha256"]),
                        ingestion_artifact_id=UUID(str(row["ingestion_artifact_id"])),
                        artifact_key=str(row["artifact_key"]),
                        section=section,
                        artifact_kind=str(row["artifact_kind"]),
                        page_number=int(row["page_number"]) if row["page_number"] is not None else None,
                        storage_backend=str(row["storage_backend"]),
                        reject_code=str(row["reject_code"]) if row["reject_code"] is not None else None,
                        parser_location=str(row["parser_location"]) if row["parser_location"] is not None else None,
                        raw_checksum=str(row["raw_checksum"]),
                        byte_size=int(row["byte_size"]),
                        content_type=str(row["content_type"]),
                        object_key=str(row["object_key"]),
                    )
                )

            return tuple(sorted(docs, key=lambda d: SECTION_ORDER.index(d.section)))

    async def persist_materialization(
        self,
        request: KnowledgeMaterializationRequest,
        drafts: Sequence[KnowledgeDocumentDraft],
        source_docs: Sequence[MaterializationSourceDocument],
    ) -> KnowledgeMaterializationResult:
        async with self._session_factory() as session, session.begin():
            # 1. Advisory lock (D2 B-Plan)
            await acquire_snapshot_advisory_locks(session, [request.snapshot_id])

            # 2. Row locks and initial provenance verification (D3 & Pre-read checksum race)
            await self._lock_and_validate_provenance(session, request, source_docs)

            # 3. Exact replay check or insert
            outcome, candidate_receipt = await self._check_replay_or_insert(session, request, drafts, source_docs)

            # 4. Pre-commit receipt verification in same transaction (reconstructed from DB rows)
            verified_receipt = await self._reconstruct_and_verify_receipt(
                session, request, candidate_receipt, drafts, source_docs
            )

            # 5. Pre-commit locked provenance re-verification
            await self._revalidate_provenance_before_commit(session, request, source_docs)

            return KnowledgeMaterializationResult(
                receipt=verified_receipt,
                outcome=outcome,
            )

    async def audit_post_commit(
        self,
        receipt: KnowledgeMaterializationReceipt,
        request: KnowledgeMaterializationRequest,
        drafts: Sequence[KnowledgeDocumentDraft],
        source_docs: Sequence[MaterializationSourceDocument],
    ) -> bool:
        """Post-commit audit in a fresh independent session.

        Re-queries committed rows from a new session, reconstructs the canonical receipt,
        and verifies equality with the pre-commit receipt.
        Does NOT rollback or alter committed rows on failure.
        """
        async with self._session_factory() as fresh_session:
            try:
                reconstructed = await self._reconstruct_and_verify_receipt(
                    fresh_session, request, receipt, drafts, source_docs
                )
                return reconstructed == receipt
            except Exception:
                return False

    async def _lock_and_validate_provenance(
        self,
        session: AsyncSession,
        request: KnowledgeMaterializationRequest,
        pre_read_docs: Sequence[MaterializationSourceDocument],
    ) -> None:
        statement = _materialization_row_lock_statement(request.snapshot_id, request.member_ids)
        result = await session.execute(statement)
        rows = result.mappings().all()
        _validate_locked_provenance_rows(rows, request, pre_read_docs)

    async def _revalidate_provenance_before_commit(
        self,
        session: AsyncSession,
        request: KnowledgeMaterializationRequest,
        pre_read_docs: Sequence[MaterializationSourceDocument],
    ) -> None:
        statement = _materialization_row_lock_statement(request.snapshot_id, request.member_ids)
        result = await session.execute(statement)
        rows = result.mappings().all()
        _validate_locked_provenance_rows(rows, request, pre_read_docs)

    async def _fetch_existing_doc_rows(
        self,
        session: AsyncSession,
        drafts: Sequence[KnowledgeDocumentDraft],
    ) -> list[RowMapping]:
        existing_doc_rows: list[RowMapping] = []
        for draft in drafts:
            doc_stmt = (
                select(_DOCUMENT)
                .where(
                    _DOCUMENT.c.source_snapshot_member_id == str(draft.source_snapshot_member_id),
                    _DOCUMENT.c.external_document_id == draft.external_document_id,
                )
                .with_for_update(of=_DOCUMENT)
            )
            doc_res = await session.execute(doc_stmt)
            doc_row = doc_res.mappings().one_or_none()
            if doc_row is not None:
                existing_doc_rows.append(doc_row)
        return existing_doc_rows

    async def _build_replay_receipt(
        self,
        session: AsyncSession,
        request: KnowledgeMaterializationRequest,
        drafts: Sequence[KnowledgeDocumentDraft],
        source_docs: Sequence[MaterializationSourceDocument],
        existing_doc_rows: list[RowMapping],
    ) -> KnowledgeMaterializationReceipt:
        first_doc = source_docs[0]
        doc_receipts: list[MaterializedDocumentReceipt] = []
        source_map = {d.member_id: d for d in source_docs}

        for doc_row, draft in zip(existing_doc_rows, drafts, strict=True):
            _verify_single_document_replay(doc_row, draft)

            chunk_stmt = (
                select(_CHUNK)
                .where(_CHUNK.c.knowledge_document_id == str(doc_row["id"]))
                .order_by(_CHUNK.c.chunk_index)
                .with_for_update(of=_CHUNK)
            )
            chunk_res = await session.execute(chunk_stmt)
            chunk_rows = chunk_res.mappings().all()

            chunk_receipts = _verify_document_chunks_replay(chunk_rows, draft)
            src_doc = source_map[draft.source_snapshot_member_id]
            doc_receipts.append(
                MaterializedDocumentReceipt(
                    knowledge_document_id=UUID(str(doc_row["id"])),
                    source_snapshot_member_id=draft.source_snapshot_member_id,
                    external_document_id=draft.external_document_id,
                    document_content_hash=draft.document_content_hash,
                    locator=src_doc.locator,
                    chunks=chunk_receipts,
                )
            )

        return KnowledgeMaterializationReceipt(
            snapshot_id=request.snapshot_id,
            source_code=first_doc.source_code,
            source_version=first_doc.source_version,
            snapshot_canonical_checksum=first_doc.canonical_checksum,
            canonicalization_spec_version=first_doc.canonicalization_spec_version,
            item_seq=request.expected_item_seq,
            chunk_policy_version=request.chunk_policy_version,
            documents=tuple(doc_receipts),
        )

    async def _insert_new_documents(
        self,
        session: AsyncSession,
        request: KnowledgeMaterializationRequest,
        drafts: Sequence[KnowledgeDocumentDraft],
        source_docs: Sequence[MaterializationSourceDocument],
    ) -> KnowledgeMaterializationReceipt:
        first_doc = source_docs[0]
        doc_receipts: list[MaterializedDocumentReceipt] = []
        source_map = {d.member_id: d for d in source_docs}

        for draft in drafts:
            doc_id = uuid4()
            await session.execute(
                insert(_DOCUMENT).values(
                    id=str(doc_id),
                    title=draft.title,
                    publisher=None,
                    source_url=None,
                    document_version=None,
                    record_contract_version="KNOWLEDGE_EVIDENCE_V1",
                    source_snapshot_member_id=str(draft.source_snapshot_member_id),
                    external_document_id=draft.external_document_id,
                    document_content_hash=draft.document_content_hash,
                    canonicalization_spec_version=draft.canonicalization_spec_version,
                    knowledge_index_lock_marker=0,
                    document_status="ACTIVE",
                )
            )

            chunk_inserts = []
            chunk_receipts = []
            for c in draft.chunks:
                chunk_id = uuid4()
                chunk_inserts.append(
                    {
                        "id": str(chunk_id),
                        "knowledge_document_id": str(doc_id),
                        "chunk_index": c.chunk_index,
                        "chunk_text": c.chunk_text,
                        "embedding_model": None,
                        "vector_store_key": None,
                        "content_hash": c.content_hash,
                        "normalization_version": "mfds-label-knowledge-chunk@1",
                        "knowledge_index_lock_marker": 0,
                    }
                )
                chunk_receipts.append(
                    MaterializedChunkReceipt(
                        knowledge_chunk_id=chunk_id,
                        chunk_index=c.chunk_index,
                        content_hash=c.content_hash,
                    )
                )

            await session.execute(insert(_CHUNK), chunk_inserts)
            src_doc = source_map[draft.source_snapshot_member_id]
            doc_receipts.append(
                MaterializedDocumentReceipt(
                    knowledge_document_id=doc_id,
                    source_snapshot_member_id=draft.source_snapshot_member_id,
                    external_document_id=draft.external_document_id,
                    document_content_hash=draft.document_content_hash,
                    locator=src_doc.locator,
                    chunks=tuple(chunk_receipts),
                )
            )

        return KnowledgeMaterializationReceipt(
            snapshot_id=request.snapshot_id,
            source_code=first_doc.source_code,
            source_version=first_doc.source_version,
            snapshot_canonical_checksum=first_doc.canonical_checksum,
            canonicalization_spec_version=first_doc.canonicalization_spec_version,
            item_seq=request.expected_item_seq,
            chunk_policy_version=request.chunk_policy_version,
            documents=tuple(doc_receipts),
        )

    async def _check_replay_or_insert(
        self,
        session: AsyncSession,
        request: KnowledgeMaterializationRequest,
        drafts: Sequence[KnowledgeDocumentDraft],
        source_docs: Sequence[MaterializationSourceDocument],
    ) -> tuple[MaterializationOutcome, KnowledgeMaterializationReceipt]:
        existing_doc_rows = await self._fetch_existing_doc_rows(session, drafts)

        if existing_doc_rows and len(existing_doc_rows) != len(drafts):
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.CONTENT_CONFLICT)

        if len(existing_doc_rows) == len(drafts):
            receipt = await self._build_replay_receipt(session, request, drafts, source_docs, existing_doc_rows)
            return MaterializationOutcome.EXACT_REPLAY, receipt

        receipt = await self._insert_new_documents(session, request, drafts, source_docs)
        return MaterializationOutcome.CREATED, receipt

    async def _reconstruct_and_verify_receipt(
        self,
        session: AsyncSession,
        request: KnowledgeMaterializationRequest,
        candidate_receipt: KnowledgeMaterializationReceipt,
        drafts: Sequence[KnowledgeDocumentDraft],
        source_docs: Sequence[MaterializationSourceDocument],
    ) -> KnowledgeMaterializationReceipt:
        source_map = {d.member_id: d for d in source_docs}
        reconstructed_docs: list[MaterializedDocumentReceipt] = []

        for draft in drafts:
            doc_stmt = select(_DOCUMENT).where(
                _DOCUMENT.c.source_snapshot_member_id == str(draft.source_snapshot_member_id),
                _DOCUMENT.c.external_document_id == draft.external_document_id,
            )
            doc_res = await session.execute(doc_stmt)
            doc_row = doc_res.mappings().one_or_none()
            if doc_row is None:
                raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.RECEIPT_MISMATCH)

            chunk_stmt = (
                select(_CHUNK)
                .where(_CHUNK.c.knowledge_document_id == str(doc_row["id"]))
                .order_by(_CHUNK.c.chunk_index.asc())
            )
            chunk_res = await session.execute(chunk_stmt)
            chunk_rows = chunk_res.mappings().all()

            chunk_receipts = tuple(
                MaterializedChunkReceipt(
                    knowledge_chunk_id=UUID(str(cr["id"])),
                    chunk_index=int(cr["chunk_index"]),
                    content_hash=str(cr["content_hash"]),
                )
                for cr in chunk_rows
            )
            src_doc = source_map[draft.source_snapshot_member_id]
            reconstructed_docs.append(
                MaterializedDocumentReceipt(
                    knowledge_document_id=UUID(str(doc_row["id"])),
                    source_snapshot_member_id=UUID(str(doc_row["source_snapshot_member_id"])),
                    external_document_id=str(doc_row["external_document_id"]),
                    document_content_hash=str(doc_row["document_content_hash"]),
                    locator=src_doc.locator,
                    chunks=chunk_receipts,
                )
            )

        first_doc = source_docs[0]
        reconstructed_receipt = KnowledgeMaterializationReceipt(
            snapshot_id=request.snapshot_id,
            source_code=first_doc.source_code,
            source_version=first_doc.source_version,
            snapshot_canonical_checksum=first_doc.canonical_checksum,
            canonicalization_spec_version=first_doc.canonicalization_spec_version,
            item_seq=request.expected_item_seq,
            chunk_policy_version=request.chunk_policy_version,
            documents=tuple(reconstructed_docs),
        )

        if reconstructed_receipt != candidate_receipt:
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.RECEIPT_MISMATCH)

        return reconstructed_receipt


def _validate_locked_provenance_rows(
    rows: Sequence[RowMapping],
    request: KnowledgeMaterializationRequest,
    pre_read_docs: Sequence[MaterializationSourceDocument],
) -> None:
    if len(rows) != len(request.member_ids):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)

    row_map = {UUID(str(r["member_id"])): r for r in rows}
    for doc in pre_read_docs:
        row = row_map.get(doc.member_id)
        if row is None:
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)

        _validate_eligibility(row)
        _validate_artifact_binding(row, doc)
        _validate_snapshot_race(row, doc)


def _validate_eligibility(row: RowMapping) -> None:
    if row["ingestion_run_status"] != "SUCCEEDED":
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE)
    if row["snapshot_verification_status"] != "CURRENT":
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE)
    if row["source_lifecycle_status"] != "ACTIVE":
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE)
    if (
        row["endpoint_lifecycle_status"] != "VERIFIED"
        or row["endpoint_runtime_status"] != "ENABLED"
        or row["endpoint_acquisition_status"] != "APPROVED"
    ):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE)
    if row["operation_runtime_status"] != "ENABLED" or row["operation_acquisition_status"] != "APPROVED":
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE)


def _validate_artifact_metadata_and_path(row: RowMapping, doc: MaterializationSourceDocument) -> None:
    if row["member_kind"] != "ARTIFACT":
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    if row["storage_backend"] != LOCAL_PRIVATE_STORAGE_BACKEND:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    if row["artifact_kind"] != "RAW_RESPONSE":
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    if row["reject_code"] is not None or row["parser_location"] is not None:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    expected_page = SECTION_ORDER.index(doc.section) + 1
    if row["page_number"] != expected_page:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    if row["artifact_key"] != f"{doc.locator}.xml":
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)


def _validate_artifact_checksums(row: RowMapping, doc: MaterializationSourceDocument) -> None:
    try:
        expected_object_key = LocalPrivateSourceArtifactStore.object_key_for_checksum(row["raw_checksum"])
    except ValueError as exc:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID) from exc
    if row["object_key"] != expected_object_key:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    if row["content_sha256"] != row["raw_checksum"] or row["content_sha256"] != doc.content_sha256:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH)
    if row["raw_checksum"] != doc.raw_checksum or row["byte_size"] != doc.byte_size:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH)


def _validate_artifact_binding(row: RowMapping, doc: MaterializationSourceDocument) -> None:
    _validate_artifact_metadata_and_path(row, doc)
    _validate_artifact_checksums(row, doc)


def _validate_snapshot_race(row: RowMapping, doc: MaterializationSourceDocument) -> None:
    if (
        str(row["snapshot_id"]) != str(doc.snapshot_id)
        or row["source_version"] != doc.source_version
        or row["raw_manifest_checksum"] != doc.raw_manifest_checksum
        or row["canonical_checksum"] != doc.canonical_checksum
        or row["canonicalization_spec_version"] != doc.canonicalization_spec_version
    ):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH)


def _verify_single_document_replay(doc_row: RowMapping, draft: KnowledgeDocumentDraft) -> None:
    if (
        doc_row["title"] != draft.title
        or doc_row["document_content_hash"] != draft.document_content_hash
        or doc_row["canonicalization_spec_version"] != draft.canonicalization_spec_version
        or doc_row["document_status"] != "ACTIVE"
        or doc_row["record_contract_version"] != "KNOWLEDGE_EVIDENCE_V1"
        or doc_row["publisher"] is not None
        or doc_row["source_url"] is not None
        or doc_row["document_version"] is not None
        or doc_row["knowledge_index_lock_marker"] != 0
    ):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.CONTENT_CONFLICT)


def _verify_document_chunks_replay(
    chunk_rows: Sequence[RowMapping],
    draft: KnowledgeDocumentDraft,
) -> tuple[MaterializedChunkReceipt, ...]:
    if len(chunk_rows) != len(draft.chunks):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.CONTENT_CONFLICT)

    chunk_receipts: list[MaterializedChunkReceipt] = []
    for chunk_row, draft_chunk in zip(chunk_rows, draft.chunks, strict=True):
        if (
            chunk_row["chunk_index"] != draft_chunk.chunk_index
            or chunk_row["chunk_text"] != draft_chunk.chunk_text
            or chunk_row["content_hash"] != draft_chunk.content_hash
            or chunk_row["normalization_version"] != "mfds-label-knowledge-chunk@1"
            or chunk_row["knowledge_index_lock_marker"] != 0
        ):
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.CONTENT_CONFLICT)

        chunk_receipts.append(
            MaterializedChunkReceipt(
                knowledge_chunk_id=UUID(str(chunk_row["id"])),
                chunk_index=int(chunk_row["chunk_index"]),
                content_hash=str(chunk_row["content_hash"]),
            )
        )
    return tuple(chunk_receipts)
