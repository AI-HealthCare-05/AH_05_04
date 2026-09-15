from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Integer,
    String,
    column,
    select,
    table,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.evidence_search import ProductionSearchHit
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateReason,
    EvidenceGateStatus,
    PostSearchEligibilityFailure,
    PostSearchEligibilityOutcome,
    PostSearchEligibilityRequest,
    PostSearchEligibilitySuccess,
    PreSearchEligibilityFailure,
    PreSearchEligibilityOutcome,
    PreSearchEligibilityRequest,
    PreSearchEligibilitySuccess,
    ProductionEvidenceEligibilityVerifierPort,
)

logger = logging.getLogger(__name__)

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
    column("index_configuration_hash", String(64)),
    column("member_count", Integer),
)
_INDEX_MEMBER = table(
    "rag_knowledge_index_member",
    column("id", String(36)),
    column("knowledge_index_id", String(36)),
    column("knowledge_chunk_id", String(36)),
    column("source_snapshot_id", String(36)),
    column("source_snapshot_member_id", String(36)),
    column("source_code", String(100)),
    column("source_version", String(200)),
    column("canonical_checksum", String(64)),
    column("external_document_id", String(300)),
    column("chunk_index", Integer),
    column("content_hash", String(64)),
    column("member_order", Integer),
)


def _is_provenance_matching(hit: ProductionSearchHit, row: Any) -> bool:
    prov = hit.provenance
    if prov.source_version != row["source_version"]:
        return False
    if prov.canonical_checksum != row["canonical_checksum"]:
        return False
    if prov.external_document_id != row["external_document_id"]:
        return False
    if prov.chunk_index != row["chunk_index"]:
        return False
    if prov.content_hash != row["content_hash"] or prov.content_hash != row["chunk_content_hash"]:
        return False
    if row["document_status"] not in ("ACTIVE", "APPROVED"):
        return False
    if row["member_locator"] is not None and prov.locator != row["member_locator"]:
        return False
    return True


class PostgreSqlEvidenceEligibilityVerifier(ProductionEvidenceEligibilityVerifierPort):
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def pre_search(self, request: PreSearchEligibilityRequest) -> PreSearchEligibilityOutcome:
        try:
            async with self._session_factory() as session:
                # 1. Verify index exists
                idx_stmt = select(_INDEX.c.id).where(_INDEX.c.id == str(request.knowledge_index_id))
                idx_row = (await session.execute(idx_stmt)).scalar_one_or_none()
                if idx_row is None:
                    return PreSearchEligibilityFailure(
                        status=EvidenceGateStatus.VALIDATION_ERROR,
                        reason=EvidenceGateReason.INVALID_BINDING,
                        message=f"Knowledge index {request.knowledge_index_id} not found",
                    )

                # 2. Check all referenced snapshots/sources in the index
                # Join index_member -> snapshot -> operation -> endpoint -> source
                stmt = (
                    select(
                        _SOURCE.c.lifecycle_status.label("source_lifecycle"),
                        _ENDPOINT.c.lifecycle_status.label("endpoint_lifecycle"),
                        _ENDPOINT.c.runtime_status.label("endpoint_runtime"),
                        _ENDPOINT.c.acquisition_status.label("endpoint_acquisition"),
                        _OPERATION.c.runtime_status.label("operation_runtime"),
                        _OPERATION.c.acquisition_status.label("operation_acquisition"),
                        _SNAPSHOT.c.verification_status.label("snapshot_verification"),
                    )
                    .select_from(_INDEX_MEMBER)
                    .join(_SNAPSHOT, _INDEX_MEMBER.c.source_snapshot_id == _SNAPSHOT.c.id)
                    .join(_OPERATION, _SNAPSHOT.c.operation_id == _OPERATION.c.id)
                    .join(_ENDPOINT, _OPERATION.c.endpoint_id == _ENDPOINT.c.id)
                    .join(_SOURCE, _ENDPOINT.c.source_id == _SOURCE.c.id)
                    .where(_INDEX_MEMBER.c.knowledge_index_id == str(request.knowledge_index_id))
                    .distinct()
                )
                rows = (await session.execute(stmt)).mappings().all()

                for row in rows:
                    is_active = (
                        row["source_lifecycle"] == "ACTIVE"
                        and row["endpoint_lifecycle"] == "ACTIVE"
                        and row["endpoint_runtime"] in ("ACTIVE", "APPROVED")
                        and row["endpoint_acquisition"] in ("ACTIVE", "SUCCESS")
                        and row["operation_runtime"] in ("ACTIVE", "APPROVED")
                        and row["operation_acquisition"] in ("ACTIVE", "SUCCESS")
                        and row["snapshot_verification"] in ("VERIFIED", "CURRENT", "ACTIVE")
                    )
                    if not is_active:
                        return PreSearchEligibilityFailure(
                            status=EvidenceGateStatus.NO_RESULT,
                            reason=EvidenceGateReason.STALE,
                            message="One or more bound sources/snapshots are not in active/verified state",
                        )

                return PreSearchEligibilitySuccess(is_eligible=True)
        except Exception as e:
            logger.exception("pre_search eligibility check failed: %s", type(e).__name__)
            return PreSearchEligibilityFailure(
                status=EvidenceGateStatus.DEPENDENCY_ERROR,
                reason=EvidenceGateReason.INELIGIBLE,
                message="Eligibility verification dependency failure",
            )

    async def post_search(
        self,
        request: PostSearchEligibilityRequest,
        hits: Sequence[ProductionSearchHit],
    ) -> PostSearchEligibilityOutcome:
        if not hits:
            return PostSearchEligibilitySuccess(eligible_chunk_ids=frozenset())

        try:
            chunk_ids = [str(h.provenance.knowledge_chunk_id) for h in hits]
            async with self._session_factory() as session:
                # Query index member, chunk, document, and snapshot member
                stmt = (
                    select(
                        _INDEX_MEMBER.c.knowledge_chunk_id,
                        _INDEX_MEMBER.c.source_version,
                        _INDEX_MEMBER.c.canonical_checksum,
                        _INDEX_MEMBER.c.external_document_id,
                        _INDEX_MEMBER.c.chunk_index,
                        _INDEX_MEMBER.c.content_hash,
                        _CHUNK.c.content_hash.label("chunk_content_hash"),
                        _DOCUMENT.c.document_status,
                        _DOCUMENT.c.external_document_id.label("doc_external_id"),
                        _SNAPSHOT_MEMBER.c.locator.label("member_locator"),
                    )
                    .select_from(_INDEX_MEMBER)
                    .join(_CHUNK, _INDEX_MEMBER.c.knowledge_chunk_id == _CHUNK.c.id)
                    .join(_DOCUMENT, _CHUNK.c.knowledge_document_id == _DOCUMENT.c.id)
                    .outerjoin(_SNAPSHOT_MEMBER, _INDEX_MEMBER.c.source_snapshot_member_id == _SNAPSHOT_MEMBER.c.id)
                    .where(
                        _INDEX_MEMBER.c.knowledge_index_id == str(request.knowledge_index_id),
                        _INDEX_MEMBER.c.knowledge_chunk_id.in_(chunk_ids),
                    )
                )
                rows = (await session.execute(stmt)).mappings().all()
                row_map = {r["knowledge_chunk_id"]: r for r in rows}

                eligible_ids: set[UUID] = set()
                for hit in hits:
                    cid_str = str(hit.provenance.knowledge_chunk_id)
                    row = row_map.get(cid_str)
                    if row is not None and _is_provenance_matching(hit, row):
                        eligible_ids.add(hit.provenance.knowledge_chunk_id)

                return PostSearchEligibilitySuccess(eligible_chunk_ids=frozenset(eligible_ids))
        except Exception as e:
            logger.exception("post_search eligibility check failed: %s", type(e).__name__)
            return PostSearchEligibilityFailure(
                status=EvidenceGateStatus.DEPENDENCY_ERROR,
                reason=EvidenceGateReason.INELIGIBLE,
                message="Eligibility verification dependency failure",
            )
