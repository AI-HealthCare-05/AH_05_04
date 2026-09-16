"""Evaluation Bootstrap Repository for synthetic dev knowledge index provisioning.

Encapsulates explicit SQL operations for setting up DEV evaluation sources,
endpoints, operations, snapshots, verification seals, and documents.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError


class SqlAlchemyEvaluationBootstrapRepository:
    """Explicit repository adapter for evaluation synthetic data bootstrap."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def ensure_synthetic_source(
        self,
        *,
        source_id: UUID,
        source_code: str,
        display_name: str = "Synthetic DEV Source",
    ) -> UUID:
        await self._connection.execute(
            text(
                "INSERT INTO rag_source "
                "(id, source_code, display_name, lifecycle_status, max_rejected_records, "
                "max_rejection_rate, empty_result_policy) "
                "VALUES (:id, :code, :name, 'ACTIVE', 0, 0, 'REJECT') "
                "ON CONFLICT (source_code) DO NOTHING"
            ),
            {"id": str(source_id), "code": source_code, "name": display_name},
        )
        src_row = (
            (
                await self._connection.execute(
                    text("SELECT id FROM rag_source WHERE source_code = :code"),
                    {"code": source_code},
                )
            )
            .mappings()
            .one()
        )
        return UUID(str(src_row["id"]))

    async def ensure_synthetic_endpoint(
        self,
        *,
        endpoint_id: UUID,
        source_id: UUID,
        endpoint_code: str,
        display_name: str = "Synthetic DEV Index Endpoint",
    ) -> UUID:
        await self._connection.execute(
            text(
                "INSERT INTO rag_source_endpoint "
                "(id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, acquisition_status) "
                "VALUES (:id, :source_id, :code, :name, 'VERIFIED', 'ENABLED', 'APPROVED') "
                "ON CONFLICT (source_id, endpoint_code) DO NOTHING"
            ),
            {
                "id": str(endpoint_id),
                "source_id": str(source_id),
                "code": endpoint_code,
                "name": display_name,
            },
        )
        ep_row = (
            (
                await self._connection.execute(
                    text(
                        "SELECT id, lifecycle_status, runtime_status, acquisition_status "
                        "FROM rag_source_endpoint WHERE source_id = :source_id AND endpoint_code = :code"
                    ),
                    {"source_id": str(source_id), "code": endpoint_code},
                )
            )
            .mappings()
            .one()
        )
        if (
            ep_row["lifecycle_status"] != "VERIFIED"
            or ep_row["runtime_status"] != "ENABLED"
            or ep_row["acquisition_status"] != "APPROVED"
        ):
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                "Existing synthetic endpoint state is invalid",
            )
        return UUID(str(ep_row["id"]))

    async def ensure_synthetic_operation(
        self,
        *,
        operation_id: UUID,
        endpoint_id: UUID,
        operation_code: str,
        display_name: str = "Synthetic DEV Index Records",
    ) -> UUID:
        await self._connection.execute(
            text(
                "INSERT INTO rag_source_operation "
                "(id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                "VALUES (:id, :endpoint_id, :code, :name, 'ENABLED', 'APPROVED') "
                "ON CONFLICT (endpoint_id, operation_code) DO NOTHING"
            ),
            {
                "id": str(operation_id),
                "endpoint_id": str(endpoint_id),
                "code": operation_code,
                "name": display_name,
            },
        )
        op_row = (
            (
                await self._connection.execute(
                    text(
                        "SELECT id, runtime_status, acquisition_status "
                        "FROM rag_source_operation WHERE endpoint_id = :endpoint_id AND operation_code = :code"
                    ),
                    {"endpoint_id": str(endpoint_id), "code": operation_code},
                )
            )
            .mappings()
            .one()
        )
        if op_row["runtime_status"] != "ENABLED" or op_row["acquisition_status"] != "APPROVED":
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                "Existing synthetic operation state is invalid",
            )
        return UUID(str(op_row["id"]))

    async def ensure_pending_snapshot(
        self,
        *,
        snapshot_id: UUID,
        operation_id: UUID,
        raw_hash: str,
        canon_hash: str,
        receipt_hash: str,
        now: datetime,
    ) -> bool:
        snap_row = (
            (
                await self._connection.execute(
                    text(
                        "SELECT id, verification_status, verification_seal_id, source_version, canonical_checksum "
                        "FROM rag_source_snapshot WHERE id = :id"
                    ),
                    {"id": str(snapshot_id)},
                )
            )
            .mappings()
            .first()
        )
        if snap_row is None:
            await self._connection.execute(
                text(
                    "INSERT INTO rag_source_snapshot "
                    "(id, operation_id, source_version, external_version, raw_manifest_checksum, canonical_checksum, "
                    "schema_version, parser_version, normalization_version, canonicalization_spec_version, "
                    "endpoint_receipt_hash, record_count, rejected_record_count, verification_status, collected_at) "
                    "VALUES (:id, :op_id, 'external:1.0.0', '1.0.0', :raw_hash, :canon_hash, 'schema-v1', "
                    "'parser-v1', 'canonical-v1', '1.0.0', :receipt_hash, 100, 0, 'PENDING', :now)"
                ),
                {
                    "id": str(snapshot_id),
                    "op_id": str(operation_id),
                    "raw_hash": raw_hash,
                    "canon_hash": canon_hash,
                    "receipt_hash": receipt_hash,
                    "now": now,
                },
            )
            return False

        if snap_row["source_version"] != "external:1.0.0" or snap_row["canonical_checksum"] != canon_hash:
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                f"Snapshot {snapshot_id} exists but metadata differs from synthetic dev contract",
            )

        if snap_row["verification_status"] == "CURRENT":
            if snap_row["verification_seal_id"] is None:
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    "CURRENT snapshot missing verification_seal_id",
                )
            return True
        elif snap_row["verification_status"] == "PENDING":
            return False
        else:
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                f"Unsupported snapshot verification status: {snap_row['verification_status']}",
            )

    async def seal_snapshot(
        self,
        *,
        snapshot_id: UUID,
        verification_id: UUID,
        now: datetime,
    ) -> None:
        await self._connection.execute(
            text(
                "INSERT INTO rag_source_snapshot_verification "
                "(id, snapshot_id, check_name, verification_result, verified_by, verified_at) "
                "VALUES (:id, :snapshot_id, 'synthetic-dev-verification', 'PASSED', 'synthetic-reviewer', :now) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": str(verification_id), "snapshot_id": str(snapshot_id), "now": now},
        )
        update_res = await self._connection.execute(
            text(
                "UPDATE rag_source_snapshot "
                "SET verification_status = 'CURRENT', verification_seal_id = :seal_id, "
                "verified_at = :now, effective_at = :now "
                "WHERE id = :snapshot_id AND verification_status = 'PENDING' AND verification_seal_id IS NULL"
            ),
            {"seal_id": str(verification_id), "snapshot_id": str(snapshot_id), "now": now},
        )
        if update_res.rowcount != 1:
            raise EvaluationValidationError(
                EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                f"CAS snapshot seal failed for {snapshot_id}: expected 1 updated row, got {update_res.rowcount}",
            )

    async def insert_knowledge_document(
        self,
        *,
        doc_id: UUID,
        title: str,
        member_id: UUID,
        external_document_id: str,
        document_content_hash: str,
    ) -> None:
        await self._connection.execute(
            text(
                "INSERT INTO knowledge_document "
                "(id, title, publisher, source_url, document_version, document_status, record_contract_version, "
                "source_snapshot_member_id, external_document_id, document_content_hash, "
                "canonicalization_spec_version, knowledge_index_lock_marker) "
                "VALUES (:id, :title, NULL, NULL, NULL, 'ACTIVE', 'KNOWLEDGE_EVIDENCE_V1', "
                ":member_id, :external_document_id, :doc_hash, '1.0.0', 0)"
            ),
            {
                "id": str(doc_id),
                "title": title,
                "member_id": str(member_id),
                "external_document_id": external_document_id,
                "doc_hash": document_content_hash,
            },
        )

    async def ensure_knowledge_document(
        self,
        *,
        doc_id: UUID,
        title: str,
        member_id: UUID,
        external_document_id: str,
        document_content_hash: str,
    ) -> None:
        doc_row = (
            (
                await self._connection.execute(
                    text(
                        "SELECT id, source_snapshot_member_id, external_document_id, "
                        "document_content_hash, document_status, record_contract_version "
                        "FROM knowledge_document WHERE id = :id"
                    ),
                    {"id": str(doc_id)},
                )
            )
            .mappings()
            .first()
        )
        if doc_row is not None:
            if (
                UUID(str(doc_row["source_snapshot_member_id"])) != member_id
                or doc_row["external_document_id"] != external_document_id
                or doc_row["document_content_hash"] != document_content_hash
                or doc_row["document_status"] != "ACTIVE"
                or doc_row["record_contract_version"] != "KNOWLEDGE_EVIDENCE_V1"
            ):
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    f"Existing document {doc_id} conflict with record {external_document_id}",
                )
            return

        await self.insert_knowledge_document(
            doc_id=doc_id,
            title=title,
            member_id=member_id,
            external_document_id=external_document_id,
            document_content_hash=document_content_hash,
        )

    async def insert_knowledge_chunk(
        self,
        *,
        chunk_id: UUID,
        doc_id: UUID,
        chunk_text: str,
        model_ref: str,
        content_hash: str,
    ) -> None:
        await self._connection.execute(
            text(
                "INSERT INTO knowledge_chunk "
                "(id, knowledge_document_id, chunk_index, chunk_text, embedding_model, vector_store_key, "
                "content_hash, normalization_version) "
                "VALUES (:id, :doc_id, 0, :text, :model, NULL, :content_hash, 'canonical-v1')"
            ),
            {
                "id": str(chunk_id),
                "doc_id": str(doc_id),
                "text": chunk_text,
                "model": model_ref,
                "content_hash": content_hash,
            },
        )

    async def ensure_knowledge_chunk(
        self,
        *,
        chunk_id: UUID,
        doc_id: UUID,
        chunk_text: str,
        model_ref: str,
        content_hash: str,
    ) -> None:
        chunk_row = (
            (
                await self._connection.execute(
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
                UUID(str(chunk_row["knowledge_document_id"])) != doc_id
                or chunk_row["chunk_index"] != 0
                or chunk_row["chunk_text"] != chunk_text
                or chunk_row["content_hash"] != content_hash
                or chunk_row["embedding_model"] != model_ref
            ):
                raise EvaluationValidationError(
                    EvaluationErrorCode.REPOSITORY_STATE_INVALID,
                    f"Existing chunk {chunk_id} conflict with chunk text/hash",
                )
            return

        await self.insert_knowledge_chunk(
            chunk_id=chunk_id,
            doc_id=doc_id,
            chunk_text=chunk_text,
            model_ref=model_ref,
            content_hash=content_hash,
        )
