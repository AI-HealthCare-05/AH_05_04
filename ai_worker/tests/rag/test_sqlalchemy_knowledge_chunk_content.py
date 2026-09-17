"""Unit tests for the read-only KnowledgeChunk content SQLAlchemy adapter (#711)."""

from __future__ import annotations

import hashlib
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_knowledge_chunk_content import (
    SqlAlchemyKnowledgeChunkContentReader,
    _content_statement,
)
from ai_worker.tasks.rag.knowledge_chunk_content_hydration import KnowledgeChunkContentReaderError

_INDEX_ID = UUID("71100000-0000-4000-8000-000000000001")
_CHUNK_ID = UUID("71100000-0000-4000-8000-000000000002")
_SNAPSHOT_ID = UUID("71100000-0000-4000-8000-000000000003")
_MEMBER_ID = UUID("71100000-0000-4000-8000-000000000004")

_RAW_CONTENT_SENTINEL = "SYNTHETIC_CHUNK_BODY_SENTINEL_711"
_CONTENT_HASH = hashlib.sha256(_RAW_CONTENT_SENTINEL.encode("utf-8")).hexdigest()


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "index_code": "GUIDELINE_INDEX",
        "index_version": "v1",
        "index_configuration_hash": "4" * 64,
        "knowledge_index_id": str(_INDEX_ID),
        "knowledge_chunk_id": str(_CHUNK_ID),
        "source_snapshot_id": str(_SNAPSHOT_ID),
        "source_snapshot_member_id": str(_MEMBER_ID),
        "source_code": "MFDS_LABEL",
        "source_version": "2026.1",
        "canonical_checksum": "c" * 64,
        "external_document_id": "DOC-1",
        "chunk_index": 0,
        "content_hash": _CONTENT_HASH,
        "locator": "$.records[0]",
        "canonicalization_spec_version": "canonical-v1",
        "chunk_text": _RAW_CONTENT_SENTINEL,
        "normalization_version": "normalization-v1",
    }
    row.update(overrides)
    return row


def _reader(rows: list[dict[str, Any]] | None = None, *, error: BaseException | None = None):
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.begin.return_value.__aenter__.return_value = None

    executed: list[Any] = []

    async def _execute(statement, *args, **kwargs):
        executed.append(statement)
        if error is not None and len(executed) > 1:
            raise error
        result = MagicMock()
        result.mappings.return_value.all.return_value = list(rows or [])
        return result

    session.execute.side_effect = _execute
    reader = SqlAlchemyKnowledgeChunkContentReader(lambda: session)
    return reader, executed


def test_statement_joins_membership_chunk_document_and_snapshot_member_without_limit() -> None:
    sql = str(_content_statement(_INDEX_ID, _CHUNK_ID))

    assert "FROM rag_knowledge_index_member" in sql
    assert "JOIN rag_knowledge_index " in sql
    assert "JOIN knowledge_chunk" in sql
    assert "JOIN knowledge_document" in sql
    assert "JOIN rag_source_snapshot_member" in sql
    assert "LIMIT" not in sql.upper()
    assert "ORDER BY" not in sql.upper()


def test_statement_is_read_only_and_binds_both_lookup_identifiers() -> None:
    statement = _content_statement(_INDEX_ID, _CHUNK_ID)
    sql = str(statement).upper()

    assert "FOR UPDATE" not in sql
    assert "FOR SHARE" not in sql
    for forbidden in ("INSERT", "UPDATE ", "DELETE"):
        assert forbidden not in sql
    params = set(statement.compile().params.values())
    assert str(_INDEX_ID) in params
    assert str(_CHUNK_ID) in params


async def test_read_content_declares_read_only_repeatable_read_transaction() -> None:
    reader, executed = _reader([_row()])

    await reader.read_content(knowledge_index_id=_INDEX_ID, knowledge_chunk_id=_CHUNK_ID)

    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY" in str(executed[0])


async def test_read_content_rebuilds_production_provenance_from_persisted_row() -> None:
    reader, _ = _reader([_row()])

    observation = await reader.read_content(knowledge_index_id=_INDEX_ID, knowledge_chunk_id=_CHUNK_ID)

    assert observation is not None
    provenance = observation.provenance
    assert provenance.knowledge_index_id == _INDEX_ID
    assert provenance.knowledge_chunk_id == _CHUNK_ID
    assert provenance.index_code == "GUIDELINE_INDEX"
    assert provenance.index_version == "v1"
    assert provenance.index_configuration_hash == "4" * 64
    assert provenance.source_snapshot_id == _SNAPSHOT_ID
    assert provenance.source_snapshot_member_id == _MEMBER_ID
    assert provenance.source_code == "MFDS_LABEL"
    assert provenance.source_version == "2026.1"
    assert provenance.canonical_checksum == "c" * 64
    assert provenance.external_document_id == "DOC-1"
    assert provenance.chunk_index == 0
    assert provenance.locator == "$.records[0]"
    assert provenance.content_hash == _CONTENT_HASH
    assert provenance.canonicalization_spec_version == "canonical-v1"
    assert provenance.normalization_version == "normalization-v1"
    assert observation.content_text.reveal() == _RAW_CONTENT_SENTINEL


async def test_missing_row_returns_none_rather_than_raising() -> None:
    reader, _ = _reader([])

    assert await reader.read_content(knowledge_index_id=_INDEX_ID, knowledge_chunk_id=_CHUNK_ID) is None


async def test_ambiguous_membership_rows_fail_closed_without_picking_one() -> None:
    reader, _ = _reader([_row(), _row(locator="$.records[1]")])

    with pytest.raises(KnowledgeChunkContentReaderError):
        await reader.read_content(knowledge_index_id=_INDEX_ID, knowledge_chunk_id=_CHUNK_ID)


async def test_database_exception_is_converted_to_typed_reader_error(caplog) -> None:
    statement = "SELECT chunk_text FROM knowledge_chunk WHERE id = 'secret'"
    failure = OperationalError(statement, {}, Exception("connection to host=db user=app failed"))
    reader, _ = _reader([_row()], error=failure)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(KnowledgeChunkContentReaderError) as exc_info:
            await reader.read_content(knowledge_index_id=_INDEX_ID, knowledge_chunk_id=_CHUNK_ID)

    message = str(exc_info.value)
    assert "secret" not in message
    assert "host=db" not in message
    assert exc_info.value.__cause__ is None
    assert "host=db" not in caplog.text
    assert "OperationalError" in caplog.text


async def test_malformed_persisted_row_fails_closed_as_reader_error() -> None:
    reader, _ = _reader([_row(chunk_index=None)])

    with pytest.raises(KnowledgeChunkContentReaderError):
        await reader.read_content(knowledge_index_id=_INDEX_ID, knowledge_chunk_id=_CHUNK_ID)


async def test_chunk_body_is_never_logged(caplog) -> None:
    reader, _ = _reader([_row()])

    with caplog.at_level(logging.DEBUG):
        observation = await reader.read_content(knowledge_index_id=_INDEX_ID, knowledge_chunk_id=_CHUNK_ID)

    assert observation is not None
    assert _RAW_CONTENT_SENTINEL not in caplog.text
    assert _RAW_CONTENT_SENTINEL not in repr(observation)
