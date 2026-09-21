"""Read-only SQLAlchemy adapter for persisted KnowledgeChunk content hydration (#711).

Implements `KnowledgeChunkContentReaderPort` only. The adapter reads the single
`rag_knowledge_index_member` row addressed by (`knowledge_index_id`,
`knowledge_chunk_id`) together with its chunk, document, and Source Snapshot
Member rows, and rebuilds the existing `ProductionEvidenceProvenance` from those
persisted values.

Boundaries:
- Read-Only: the transaction is declared REPEATABLE READ / READ ONLY. No INSERT,
  UPDATE, DELETE, `SELECT ... FOR UPDATE`, or advisory lock is issued, and no
  schema or migration is involved.
- No Currentness Re-Judgement: Source lifecycle, endpoint/operation runtime
  status, snapshot verification status, eligibility, and approval are owned by
  the #178/#180 gates and are deliberately not re-applied here. Hydration reads
  exactly the membership row production retrieval already selected.
- Provenance Field Meaning: values are taken from the same persisted columns the
  #178 production search uses, so `content_hash` is the index member's recorded
  hash. Chunk/member hash drift is deliberately not filtered out in SQL; it is
  surfaced to the hydration kernel, which closes it as a content verdict rather
  than hiding it as a missing row.
- Ambiguity: `uq_rag_knowledge_index_member_chunk` already makes this lookup
  identity at most one row. As defense-in-depth the adapter still refuses two or
  more rows as a data integrity failure and raises
  `KnowledgeChunkContentReaderError`; no `LIMIT 1`, ordering, or newest-row
  selection is ever applied.
- Sensitive Content: the chunk body is returned only inside `SensitiveText`, and
  rows, bodies, SQL text, and connection details are never logged. Dependency
  failures are logged by exception class name only.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID

from sqlalchemy import Integer, String, and_, column, select, table, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.evidence_retrieval import SensitiveText
from ai_worker.tasks.rag.evidence_search import ProductionEvidenceProvenance
from ai_worker.tasks.rag.knowledge_chunk_content_hydration import (
    KnowledgeChunkContentObservation,
    KnowledgeChunkContentReaderError,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]

_INDEX = table(
    "rag_knowledge_index",
    column("id", String(36)),
    column("index_code", String(120)),
    column("index_version", String(80)),
    column("index_configuration_hash", String(64)),
)
_INDEX_MEMBER = table(
    "rag_knowledge_index_member",
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
)
_CHUNK = table(
    "knowledge_chunk",
    column("id", String(36)),
    column("knowledge_document_id", String(36)),
    column("chunk_text", String),
    column("normalization_version", String(100)),
)
_DOCUMENT = table(
    "knowledge_document",
    column("id", String(36)),
    column("canonicalization_spec_version", String(100)),
)
_SNAPSHOT_MEMBER = table(
    "rag_source_snapshot_member",
    column("id", String(36)),
    column("locator", String(500)),
)


def _required_persisted_evidence_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("persisted evidence_key is missing")
    return value


def _content_statement(knowledge_index_id: UUID, knowledge_chunk_id: UUID):
    """Select the membership row for the exact lookup identity, without a row limit."""
    return (
        select(
            _INDEX.c.index_code,
            _INDEX.c.index_version,
            _INDEX.c.index_configuration_hash,
            _INDEX_MEMBER.c.knowledge_index_id,
            _INDEX_MEMBER.c.knowledge_chunk_id,
            _INDEX_MEMBER.c.evidence_key,
            _INDEX_MEMBER.c.source_snapshot_id,
            _INDEX_MEMBER.c.source_snapshot_member_id,
            _INDEX_MEMBER.c.source_code,
            _INDEX_MEMBER.c.source_version,
            _INDEX_MEMBER.c.canonical_checksum,
            _INDEX_MEMBER.c.external_document_id,
            _INDEX_MEMBER.c.chunk_index,
            _INDEX_MEMBER.c.content_hash,
            _SNAPSHOT_MEMBER.c.locator,
            _DOCUMENT.c.canonicalization_spec_version,
            _CHUNK.c.chunk_text,
            _CHUNK.c.normalization_version,
        )
        .select_from(_INDEX_MEMBER)
        .join(_INDEX, _INDEX.c.id == _INDEX_MEMBER.c.knowledge_index_id)
        .join(_CHUNK, _CHUNK.c.id == _INDEX_MEMBER.c.knowledge_chunk_id)
        .join(_DOCUMENT, _DOCUMENT.c.id == _CHUNK.c.knowledge_document_id)
        .join(_SNAPSHOT_MEMBER, _SNAPSHOT_MEMBER.c.id == _INDEX_MEMBER.c.source_snapshot_member_id)
        .where(
            and_(
                _INDEX_MEMBER.c.knowledge_index_id == str(knowledge_index_id),
                _INDEX_MEMBER.c.knowledge_chunk_id == str(knowledge_chunk_id),
            )
        )
    )


def _to_observation(row: RowMapping) -> KnowledgeChunkContentObservation:
    """Rebuild the production provenance and body from one persisted row."""
    provenance = ProductionEvidenceProvenance(
        knowledge_index_id=UUID(str(row["knowledge_index_id"])),
        index_code=str(row["index_code"]),
        index_version=str(row["index_version"]),
        index_configuration_hash=str(row["index_configuration_hash"]),
        knowledge_chunk_id=UUID(str(row["knowledge_chunk_id"])),
        evidence_key=_required_persisted_evidence_key(row["evidence_key"]),
        source_snapshot_id=UUID(str(row["source_snapshot_id"])),
        source_snapshot_member_id=UUID(str(row["source_snapshot_member_id"])),
        source_code=str(row["source_code"]),
        source_version=str(row["source_version"]),
        canonical_checksum=str(row["canonical_checksum"]),
        external_document_id=str(row["external_document_id"]),
        chunk_index=int(row["chunk_index"]),
        locator=str(row["locator"]),
        content_hash=str(row["content_hash"]),
        canonicalization_spec_version=str(row["canonicalization_spec_version"]),
        normalization_version=str(row["normalization_version"]),
    )
    return KnowledgeChunkContentObservation(
        provenance=provenance,
        content_text=SensitiveText(str(row["chunk_text"])),
    )


class SqlAlchemyKnowledgeChunkContentReader:
    """Production `KnowledgeChunkContentReaderPort` over the persisted Knowledge Index."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def read_content(
        self,
        *,
        knowledge_index_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> KnowledgeChunkContentObservation | None:
        try:
            rows = await self._fetch_rows(
                knowledge_index_id=knowledge_index_id,
                knowledge_chunk_id=knowledge_chunk_id,
            )
        except SQLAlchemyError as exc:
            logger.error(
                "KnowledgeChunk content read failed with database exception: %s",
                exc.__class__.__name__,
            )
            raise KnowledgeChunkContentReaderError("knowledge chunk content read failed") from None

        if not rows:
            return None
        if len(rows) > 1:
            logger.error(
                "KnowledgeChunk content lookup is ambiguous: %d index membership rows",
                len(rows),
            )
            raise KnowledgeChunkContentReaderError("knowledge chunk content lookup is ambiguous")

        try:
            return _to_observation(rows[0])
        except (TypeError, ValueError) as exc:
            logger.error(
                "KnowledgeChunk content row is malformed: %s",
                exc.__class__.__name__,
            )
            raise KnowledgeChunkContentReaderError("knowledge chunk content row is malformed") from None

    async def _fetch_rows(
        self,
        *,
        knowledge_index_id: UUID,
        knowledge_chunk_id: UUID,
    ) -> list[RowMapping]:
        async with self._session_factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            result = await session.execute(_content_statement(knowledge_index_id, knowledge_chunk_id))
            return list(result.mappings().all())
