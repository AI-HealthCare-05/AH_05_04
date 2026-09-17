import hashlib
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    SqlAlchemyKnowledgeEvidenceIndexRepository,
    _artifact_origin_lock_statement,
    _source_binding_statement,
)
from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeChunkIdentity,
    KnowledgeEvidenceIndexFailureReason,
    KnowledgeEvidenceIndexValidationError,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexMemberDraft,
    SensitiveEvidenceText,
    create_knowledge_index_receipt,
)

_INDEX_ID = UUID("00000000-0000-4000-8000-000000000010")


def member() -> KnowledgeIndexMemberDraft:
    content = "합성 근거 문장"
    return KnowledgeIndexMemberDraft(
        identity=KnowledgeChunkIdentity(
            knowledge_chunk_id=UUID("00000000-0000-4000-8000-000000000001"),
            source_snapshot_id=UUID("00000000-0000-4000-8000-000000000002"),
            source_snapshot_member_id=UUID("00000000-0000-4000-8000-000000000003"),
            source_code="MFDS",
            source_version="external:v1",
            canonical_checksum="a" * 64,
            external_document_id="document-1",
            chunk_index=0,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            locator="$.records[0]",
        ),
        content_text=SensitiveEvidenceText(content),
        embedding=(1.0, 0.0),
    )


def request() -> KnowledgeIndexBuildRequest:
    return KnowledgeIndexBuildRequest(
        index_code="knowledge-evidence",
        index_version="v1",
        embedding_model_ref="synthetic-model",
        embedding_model_version="1.0.0",
        embedding_dimension=2,
        distance_metric=DistanceMetric.COSINE,
        members=(member(),),
    )


def _session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.begin.return_value.__aenter__.return_value = None
    return session


def test_source_binding_query_locks_full_chain_and_never_uses_candidate_index() -> None:
    statement = _source_binding_statement(member())
    sql = str(statement)

    assert "rag_source JOIN rag_source_endpoint" in sql
    assert "JOIN rag_source_operation" in sql
    assert "JOIN rag_source_snapshot" in sql
    assert "JOIN rag_source_snapshot_member" in sql
    assert "JOIN knowledge_document" in sql
    assert "JOIN knowledge_chunk" in sql
    assert "rag_source_ingestion_artifact" in sql
    assert "rag_source_ingestion_run" in sql
    assert "FOR UPDATE" in sql
    assert "KNOWLEDGE_EVIDENCE_V1" in statement.compile().params.values()
    assert "ACTIVE" in statement.compile().params.values()
    assert "$.records[0]" in statement.compile().params.values()
    assert "ENDPOINT_OPERATION" in statement.compile().params.values()
    assert "ARTIFACT" in statement.compile().params.values()
    assert "canonicalization_spec_version IS NOT NULL" in sql
    assert "candidate" not in sql.lower()


def test_artifact_origin_query_locks_run_and_artifact_rows() -> None:
    statement = _artifact_origin_lock_statement(member())
    sql = str(statement)

    assert "rag_source_ingestion_artifact" in sql
    assert "rag_source_ingestion_run" in sql
    assert "rag_source_snapshot" in sql
    assert "FOR UPDATE" in sql


async def test_new_index_is_inserted_atomically_and_recomputed_before_commit() -> None:
    build = request()
    receipt = create_knowledge_index_receipt(build)
    session = _session()
    session.scalar.return_value = None
    repository = SqlAlchemyKnowledgeEvidenceIndexRepository(lambda: session)
    repository._lock_and_validate_members = AsyncMock()  # type: ignore[method-assign]
    repository._load_and_recompute_receipt = AsyncMock(return_value=receipt)  # type: ignore[method-assign]

    result = await repository.persist_complete_index(build, receipt)

    assert result == receipt
    repository._lock_and_validate_members.assert_awaited_once_with(session, build)  # type: ignore[attr-defined]
    repository._load_and_recompute_receipt.assert_awaited_once()  # type: ignore[attr-defined]
    sql = [str(call.args[0]) for call in session.execute.await_args_list]
    assert sql[0].startswith("SELECT pg_advisory_xact_lock")
    assert any(statement.startswith("INSERT INTO rag_knowledge_index") for statement in sql)
    assert any(statement.startswith("INSERT INTO rag_knowledge_index_member") for statement in sql)
    session.commit.assert_not_awaited()


async def test_exact_existing_version_is_idempotent() -> None:
    build = request()
    receipt = create_knowledge_index_receipt(build)
    session = _session()
    session.scalar.return_value = str(_INDEX_ID)
    repository = SqlAlchemyKnowledgeEvidenceIndexRepository(lambda: session)
    repository._lock_and_validate_members = AsyncMock()  # type: ignore[method-assign]
    repository._load_and_recompute_receipt = AsyncMock(return_value=receipt)  # type: ignore[method-assign]

    assert await repository.persist_complete_index(build, receipt) == receipt
    assert all("INSERT INTO rag_knowledge" not in str(call.args[0]) for call in session.execute.await_args_list)


async def test_existing_version_with_changed_receipt_is_a_safe_conflict() -> None:
    build = request()
    receipt = create_knowledge_index_receipt(build)
    session = _session()
    session.scalar.return_value = str(_INDEX_ID)
    repository = SqlAlchemyKnowledgeEvidenceIndexRepository(lambda: session)
    repository._lock_and_validate_members = AsyncMock()  # type: ignore[method-assign]
    repository._load_and_recompute_receipt = AsyncMock(  # type: ignore[method-assign]
        return_value=replace(receipt, member_count=2)
    )

    with pytest.raises(KnowledgeEvidenceIndexValidationError) as exc_info:
        await repository.persist_complete_index(build, receipt)

    assert exc_info.value.reason is KnowledgeEvidenceIndexFailureReason.VERSION_CONFLICT
    assert "근거" not in repr(exc_info.value)


async def test_missing_source_binding_fails_before_any_index_insert() -> None:
    build = request()
    receipt = create_knowledge_index_receipt(build)
    session = _session()
    missing = MagicMock()
    missing.mappings.return_value.one_or_none.return_value = None
    session.execute.side_effect = [MagicMock(), MagicMock(), missing]
    repository = SqlAlchemyKnowledgeEvidenceIndexRepository(lambda: session)

    with pytest.raises(KnowledgeEvidenceIndexValidationError) as exc_info:
        await repository.persist_complete_index(build, receipt)

    assert exc_info.value.reason is KnowledgeEvidenceIndexFailureReason.SOURCE_BINDING_INVALID
    assert all("INSERT INTO rag_knowledge" not in str(call.args[0]) for call in session.execute.await_args_list)


async def test_database_chunk_text_must_match_the_bound_content_hash() -> None:
    build = request()
    receipt = create_knowledge_index_receipt(build)
    session = _session()
    corrupted = MagicMock()
    corrupted.mappings.return_value.one_or_none.return_value = {
        "id": str(build.members[0].identity.knowledge_chunk_id),
        "chunk_text": "different synthetic text",
        "member_kind": "ENDPOINT_OPERATION",
    }
    session.execute.side_effect = [MagicMock(), MagicMock(), corrupted]
    repository = SqlAlchemyKnowledgeEvidenceIndexRepository(lambda: session)

    with pytest.raises(KnowledgeEvidenceIndexValidationError) as exc_info:
        await repository.persist_complete_index(build, receipt)

    assert exc_info.value.reason is KnowledgeEvidenceIndexFailureReason.SOURCE_BINDING_INVALID


async def test_artifact_member_locks_its_ingestion_parent_before_index_insert() -> None:
    build = request()
    session = _session()
    binding = MagicMock()
    binding.mappings.return_value.one_or_none.return_value = {
        "id": str(build.members[0].identity.knowledge_chunk_id),
        "chunk_text": build.members[0].content_text.reveal(),
        "member_kind": "ARTIFACT",
    }
    artifact = MagicMock()
    artifact.scalar_one_or_none.return_value = "artifact-id"
    session.execute.side_effect = [binding, artifact]
    repository = SqlAlchemyKnowledgeEvidenceIndexRepository(lambda: session)

    await repository._lock_and_validate_members(session, build)

    assert session.execute.await_count == 2
    assert "rag_source_ingestion_run" in str(session.execute.await_args_list[1].args[0])
    assert "FOR UPDATE" in str(session.execute.await_args_list[1].args[0])
