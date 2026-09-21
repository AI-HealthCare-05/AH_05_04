import hashlib
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.knowledge_snapshot_advisory_lock import (
    acquire_snapshot_advisory_locks,
    snapshot_advisory_lock_key,
)
from ai_worker.adapters.sqlalchemy_knowledge_materialization import (
    SqlAlchemyKnowledgeMaterializationRepository,
    _fetch_source_documents_statement,
    _materialization_row_lock_statement,
)
from ai_worker.tasks.rag.knowledge_materialization import (
    CHUNK_POLICY_VERSION,
    KnowledgeDocumentDraft,
    KnowledgeMaterializationError,
    KnowledgeMaterializationFailureReason,
    KnowledgeMaterializationReceipt,
    KnowledgeMaterializationRequest,
    MaterializationOutcome,
    MaterializationSourceDocument,
)
from ai_worker.tasks.rag.mfds_label_chunk_policy import KnowledgeChunkDraft
from ai_worker.tasks.rag.source_ingestion.mfds_label import (
    CANONICALIZATION_SPEC_VERSION,
    LOCAL_PRIVATE_STORAGE_BACKEND,
    NORMALIZATION_VERSION,
    OBSERVED_CONTENT_TYPE,
    PARSER_VERSION,
    SCHEMA_VERSION,
)

ITEM_SEQ = "200610660"
SNAPSHOT_ID = UUID("00000000-0000-4000-8000-000000000001")
SOURCE_ID = UUID("00000000-0000-4000-8000-000000000002")
ENDPOINT_ID = UUID("00000000-0000-4000-8000-000000000003")
OPERATION_ID = UUID("00000000-0000-4000-8000-000000000004")
INGESTION_RUN_ID = UUID("00000000-0000-4000-8000-000000000005")


def _session() -> AsyncMock:
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.begin.return_value.__aenter__.return_value = None
    return session


def _make_source_doc(
    section: str,
    page_number: int,
    raw_checksum: str | None = None,
    member_id: UUID | None = None,
    ingestion_run_status: str = "SUCCEEDED",
    snapshot_verification_status: str = "CURRENT",
    source_lifecycle_status: str = "ACTIVE",
    endpoint_lifecycle_status: str = "VERIFIED",
    endpoint_runtime_status: str = "ENABLED",
    endpoint_acquisition_status: str = "APPROVED",
    operation_runtime_status: str = "ENABLED",
    operation_acquisition_status: str = "APPROVED",
    storage_backend: str = LOCAL_PRIVATE_STORAGE_BACKEND,
    artifact_kind: str = "RAW_RESPONSE",
    reject_code: str | None = None,
    parser_location: str | None = None,
    content_type: str = OBSERVED_CONTENT_TYPE,
    schema_version: str = SCHEMA_VERSION,
    parser_version: str = PARSER_VERSION,
    normalization_version: str = NORMALIZATION_VERSION,
    canonicalization_spec_version: str = CANONICALIZATION_SPEC_VERSION,
    ingestion_run_snapshot_id: UUID | None = SNAPSHOT_ID,
    ingestion_run_operation_id: UUID | None = OPERATION_ID,
) -> MaterializationSourceDocument:
    checksum = raw_checksum or hashlib.sha256(f"content-{section}".encode()).hexdigest()
    m_id = member_id or uuid4()
    art_id = uuid4()
    locator = f"mfds-label/{ITEM_SEQ}/{section}"
    return MaterializationSourceDocument(
        source_id=SOURCE_ID,
        source_code="MFDS",
        source_lifecycle_status=source_lifecycle_status,
        endpoint_id=ENDPOINT_ID,
        endpoint_code="DRUG_LABEL",
        endpoint_lifecycle_status=endpoint_lifecycle_status,
        endpoint_runtime_status=endpoint_runtime_status,
        endpoint_acquisition_status=endpoint_acquisition_status,
        operation_id=OPERATION_ID,
        operation_code="GET_LABEL",
        operation_runtime_status=operation_runtime_status,
        operation_acquisition_status=operation_acquisition_status,
        snapshot_id=SNAPSHOT_ID,
        source_version="external:20260901",
        canonical_checksum="1" * 64,
        raw_manifest_checksum="2" * 64,
        schema_version=schema_version,
        parser_version=parser_version,
        normalization_version=normalization_version,
        canonicalization_spec_version=canonicalization_spec_version,
        snapshot_verification_status=snapshot_verification_status,
        ingestion_run_id=INGESTION_RUN_ID,
        ingestion_run_status=ingestion_run_status,
        member_id=m_id,
        member_kind="ARTIFACT",
        locator=locator,
        content_sha256=checksum,
        ingestion_artifact_id=art_id,
        artifact_key=f"{locator}.xml",
        section=section,
        artifact_kind=artifact_kind,
        page_number=page_number,
        storage_backend=storage_backend,
        reject_code=reject_code,
        parser_location=parser_location,
        raw_checksum=checksum,
        byte_size=100,
        content_type=content_type,
        object_key=f"sha256/{checksum[:2]}/{checksum}.artifact",
        ingestion_run_snapshot_id=ingestion_run_snapshot_id,
        ingestion_run_operation_id=ingestion_run_operation_id,
    )


def _make_draft(
    doc: MaterializationSourceDocument,
    title: str,
    chunk_text: str,
) -> KnowledgeDocumentDraft:
    chunk_hash = hashlib.sha256(chunk_text.encode()).hexdigest()
    return KnowledgeDocumentDraft(
        source_snapshot_member_id=doc.member_id,
        external_document_id=f"mfds-label:{ITEM_SEQ}:{doc.section}",
        document_content_hash=doc.content_sha256,
        canonicalization_spec_version=doc.canonicalization_spec_version,
        title=title,
        chunks=(
            KnowledgeChunkDraft(
                chunk_index=0,
                chunk_text=chunk_text,
                normalization_version=CHUNK_POLICY_VERSION,
                content_hash=chunk_hash,
            ),
        ),
    )


def _triple_fixtures() -> tuple[
    KnowledgeMaterializationRequest,
    tuple[MaterializationSourceDocument, ...],
    tuple[KnowledgeDocumentDraft, ...],
]:
    doc_ee = _make_source_doc(section="EE", page_number=1)
    doc_ud = _make_source_doc(section="UD", page_number=2)
    doc_nb = _make_source_doc(section="NB", page_number=3)
    source_docs = (doc_ee, doc_ud, doc_nb)
    request = KnowledgeMaterializationRequest(
        snapshot_id=SNAPSHOT_ID,
        member_ids=tuple(d.member_id for d in source_docs),
        expected_item_seq=ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
    )
    draft_ee = _make_draft(doc_ee, "효능효과", "효능효과 본문")
    draft_ud = _make_draft(doc_ud, "용법용량", "용법용량 본문")
    draft_nb = _make_draft(doc_nb, "사용상의주의사항", "주의사항 본문")
    drafts = (draft_ee, draft_ud, draft_nb)
    return request, source_docs, drafts


# =========================================================================
# 1. Query Structure & Table Locking Order
# =========================================================================


def test_fetch_source_documents_query_structure_and_no_verification_select() -> None:
    request, source_docs, _ = _triple_fixtures()
    statement = _fetch_source_documents_statement(request.snapshot_id, request.member_ids)
    sql = str(statement)

    assert "rag_source" in sql
    assert "rag_source_endpoint" in sql
    assert "rag_source_operation" in sql
    assert "rag_source_snapshot" in sql
    assert "rag_source_snapshot_member" in sql
    assert "rag_source_ingestion_run" in sql
    assert "rag_source_ingestion_artifact" in sql
    assert "rag_source_snapshot_verification" not in sql
    assert "FOR UPDATE" not in sql


def test_row_locks_query_table_order() -> None:
    _, source_docs, _ = _triple_fixtures()
    statement = _materialization_row_lock_statement(SNAPSHOT_ID, [d.member_id for d in source_docs])
    sql = str(statement)

    assert "FOR UPDATE" in sql
    # Required table lock order:
    # rag_source -> rag_source_endpoint -> rag_source_operation -> rag_source_snapshot
    # -> rag_source_snapshot_member -> rag_source_ingestion_run -> rag_source_ingestion_artifact
    source_idx = sql.index("rag_source")
    endpoint_idx = sql.index("rag_source_endpoint")
    op_idx = sql.index("rag_source_operation")
    snap_idx = sql.index("rag_source_snapshot")
    member_idx = sql.index("rag_source_snapshot_member")
    run_idx = sql.index("rag_source_ingestion_run")
    artifact_idx = sql.index("rag_source_ingestion_artifact")

    assert source_idx < endpoint_idx < op_idx < snap_idx < member_idx < run_idx < artifact_idx


# =========================================================================
# 2. Eligibility Tests (D3 & Lifecycle)
# =========================================================================


@pytest.mark.parametrize(
    "ineligible_status",
    [
        "SUCCEEDED_WITH_REJECTIONS",
        "RUNNING",
        "FAILED",
        "NO_CHANGE",
        "CANCELLED",
    ],
)
async def test_ingestion_run_status_ineligible_rejected(ineligible_status: str) -> None:
    request, source_docs, drafts = _triple_fixtures()
    ineligible_doc = replace(source_docs[0], ingestion_run_status=ineligible_status)
    test_docs = (ineligible_doc, source_docs[1], source_docs[2])

    session = _session()
    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    repository._lock_and_validate_provenance = AsyncMock(  # type: ignore[method-assign]
        side_effect=KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE)
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repository.persist_materialization(request, drafts, test_docs)

    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE


async def test_snapshot_verification_status_not_current_rejected() -> None:
    request, source_docs, drafts = _triple_fixtures()
    ineligible_doc = replace(source_docs[0], snapshot_verification_status="PENDING")
    test_docs = (ineligible_doc, source_docs[1], source_docs[2])

    session = _session()
    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    repository._lock_and_validate_provenance = AsyncMock(  # type: ignore[method-assign]
        side_effect=KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE)
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repository.persist_materialization(request, drafts, test_docs)

    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE


async def test_source_lifecycle_not_active_rejected() -> None:
    request, source_docs, drafts = _triple_fixtures()
    ineligible_doc = replace(source_docs[0], source_lifecycle_status="DISABLED")
    test_docs = (ineligible_doc, source_docs[1], source_docs[2])

    session = _session()
    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    repository._lock_and_validate_provenance = AsyncMock(  # type: ignore[method-assign]
        side_effect=KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE)
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repository.persist_materialization(request, drafts, test_docs)

    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE


# =========================================================================
# 3. Provenance Binding & Mutation Tests
# =========================================================================


async def test_artifact_binding_mutation_rejected() -> None:
    request, source_docs, drafts = _triple_fixtures()
    tampered_doc = replace(source_docs[0], storage_backend="S3_PRIVATE")
    test_docs = (tampered_doc, source_docs[1], source_docs[2])

    session = _session()
    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    repository._lock_and_validate_provenance = AsyncMock(  # type: ignore[method-assign]
        side_effect=KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repository.persist_materialization(request, drafts, test_docs)

    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID


async def test_page_number_none_rejected() -> None:
    request, source_docs, drafts = _triple_fixtures()
    tampered_doc = replace(source_docs[0], page_number=None)
    test_docs = (tampered_doc, source_docs[1], source_docs[2])

    session = _session()
    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    repository._lock_and_validate_provenance = AsyncMock(  # type: ignore[method-assign]
        side_effect=KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repository.persist_materialization(request, drafts, test_docs)

    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID


async def test_locked_snapshot_checksum_race_rejected() -> None:
    request, source_docs, drafts = _triple_fixtures()
    session = _session()
    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    repository._lock_and_validate_provenance = AsyncMock(  # type: ignore[method-assign]
        side_effect=KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH)
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repository.persist_materialization(request, drafts, source_docs)

    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH


# =========================================================================
# 4. Snapshot Advisory Lock Tests (D2 B-Plan)
# =========================================================================


async def test_advisory_lock_acquired_before_row_locks() -> None:
    request, source_docs, drafts = _triple_fixtures()
    session = _session()
    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)

    # Mock internal methods to trace SQL call order
    repository._lock_and_validate_provenance = AsyncMock()  # type: ignore[method-assign]
    repository._revalidate_provenance_before_commit = AsyncMock()  # type: ignore[method-assign]
    receipt_stub = KnowledgeMaterializationReceipt(
        snapshot_id=SNAPSHOT_ID,
        source_code="MFDS",
        source_version="external:20260901",
        snapshot_canonical_checksum="1" * 64,
        canonicalization_spec_version="canon-v1",
        item_seq=ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
        documents=(),
    )
    repository._check_replay_or_insert = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            MaterializationOutcome.CREATED,
            receipt_stub,
        )
    )
    repository._reconstruct_and_verify_receipt = AsyncMock(  # type: ignore[method-assign]
        return_value=receipt_stub,
    )

    await repository.persist_materialization(request, drafts, source_docs)

    sql_statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert len(sql_statements) >= 1
    assert "pg_advisory_xact_lock" in sql_statements[0]
    expected_lock_key = snapshot_advisory_lock_key(SNAPSHOT_ID)
    assert expected_lock_key in str(session.execute.await_args_list[0])


async def test_multi_snapshot_helper_deterministic_binary_ordering() -> None:
    id_1 = UUID("ffffffff-0000-4000-8000-000000000000")
    id_2 = UUID("00000000-0000-4000-8000-000000000000")
    id_3 = UUID("88888888-0000-4000-8000-000000000000")

    session = _session()
    # Pass in reverse/mixed order
    locked_keys = await acquire_snapshot_advisory_locks(session, [id_1, id_2, id_3])

    expected_order = sorted([id_1, id_2, id_3], key=lambda u: u.bytes)
    assert locked_keys == tuple(snapshot_advisory_lock_key(u) for u in expected_order)


async def test_request_reverse_order_produces_identical_lock_order() -> None:
    id_a = UUID("11111111-0000-4000-8000-000000000000")
    id_b = UUID("22222222-0000-4000-8000-000000000000")

    session_1 = _session()
    session_2 = _session()

    keys_1 = await acquire_snapshot_advisory_locks(session_1, [id_a, id_b])
    keys_2 = await acquire_snapshot_advisory_locks(session_2, [id_b, id_a])

    assert keys_1 == keys_2


# =========================================================================
# 5. Atomicity & Receipt Mismatch Tests
# =========================================================================


async def test_receipt_mismatch_rolls_back_before_commit() -> None:
    request, source_docs, drafts = _triple_fixtures()
    session = _session()
    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)

    receipt_stub = KnowledgeMaterializationReceipt(
        snapshot_id=SNAPSHOT_ID,
        source_code="MFDS",
        source_version="external:20260901",
        snapshot_canonical_checksum="1" * 64,
        canonicalization_spec_version="canon-v1",
        item_seq=ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
        documents=(),
    )
    repository._lock_and_validate_provenance = AsyncMock()  # type: ignore[method-assign]
    repository._check_replay_or_insert = AsyncMock(  # type: ignore[method-assign]
        return_value=(
            MaterializationOutcome.CREATED,
            receipt_stub,
        )
    )
    # Receipt mismatch in Stage 1
    repository._reconstruct_and_verify_receipt = AsyncMock(  # type: ignore[method-assign]
        side_effect=KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.RECEIPT_MISMATCH)
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repository.persist_materialization(request, drafts, source_docs)

    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.RECEIPT_MISMATCH
    session.commit.assert_not_awaited()


# =========================================================================
# 6. Idempotency & Exact Replay Tests
# =========================================================================


async def test_exact_replay_returns_existing_receipt() -> None:
    request, source_docs, drafts = _triple_fixtures()
    session = _session()
    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)

    expected_receipt = KnowledgeMaterializationReceipt(
        snapshot_id=SNAPSHOT_ID,
        source_code="MFDS",
        source_version="external:20260901",
        snapshot_canonical_checksum="1" * 64,
        canonicalization_spec_version="canon-v1",
        item_seq=ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
        documents=(),
    )
    repository._lock_and_validate_provenance = AsyncMock()  # type: ignore[method-assign]
    repository._revalidate_provenance_before_commit = AsyncMock()  # type: ignore[method-assign]
    repository._check_replay_or_insert = AsyncMock(  # type: ignore[method-assign]
        return_value=(MaterializationOutcome.EXACT_REPLAY, expected_receipt)
    )
    repository._reconstruct_and_verify_receipt = AsyncMock(return_value=expected_receipt)  # type: ignore[method-assign]

    result = await repository.persist_materialization(request, drafts, source_docs)
    assert result.is_exact_replay is True
    assert result.outcome == MaterializationOutcome.EXACT_REPLAY
    assert result.receipt == expected_receipt


async def test_content_conflict_on_changed_content() -> None:
    request, source_docs, drafts = _triple_fixtures()
    session = _session()
    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)

    repository._lock_and_validate_provenance = AsyncMock()  # type: ignore[method-assign]
    repository._check_replay_or_insert = AsyncMock(  # type: ignore[method-assign]
        side_effect=KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.CONTENT_CONFLICT)
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repository.persist_materialization(request, drafts, source_docs)

    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.CONTENT_CONFLICT
    session.commit.assert_not_awaited()


# =========================================================================
# 7. Direct Provenance & Replay Validation Unit Tests
# =========================================================================


def _row_from_source_doc(doc: MaterializationSourceDocument) -> dict[str, object]:
    return {
        "source_id": str(doc.source_id),
        "source_code": doc.source_code,
        "source_lifecycle_status": doc.source_lifecycle_status,
        "endpoint_id": str(doc.endpoint_id),
        "endpoint_code": doc.endpoint_code,
        "endpoint_lifecycle_status": doc.endpoint_lifecycle_status,
        "endpoint_runtime_status": doc.endpoint_runtime_status,
        "endpoint_acquisition_status": doc.endpoint_acquisition_status,
        "operation_id": str(doc.operation_id),
        "operation_code": doc.operation_code,
        "operation_runtime_status": doc.operation_runtime_status,
        "operation_acquisition_status": doc.operation_acquisition_status,
        "snapshot_id": str(doc.snapshot_id),
        "source_version": doc.source_version,
        "canonical_checksum": doc.canonical_checksum,
        "raw_manifest_checksum": doc.raw_manifest_checksum,
        "schema_version": doc.schema_version,
        "parser_version": doc.parser_version,
        "normalization_version": doc.normalization_version,
        "canonicalization_spec_version": doc.canonicalization_spec_version,
        "snapshot_verification_status": doc.snapshot_verification_status,
        "ingestion_run_id": str(doc.ingestion_run_id),
        "ingestion_run_status": doc.ingestion_run_status,
        "member_id": str(doc.member_id),
        "member_kind": doc.member_kind,
        "locator": doc.locator,
        "content_sha256": doc.content_sha256,
        "ingestion_artifact_id": str(doc.ingestion_artifact_id),
        "artifact_key": doc.artifact_key,
        "artifact_kind": doc.artifact_kind,
        "page_number": doc.page_number,
        "storage_backend": doc.storage_backend,
        "reject_code": doc.reject_code,
        "parser_location": doc.parser_location,
        "raw_checksum": doc.raw_checksum,
        "byte_size": doc.byte_size,
        "content_type": doc.content_type,
        "object_key": doc.object_key,
        "ingestion_run_snapshot_id": str(doc.ingestion_run_snapshot_id) if doc.ingestion_run_snapshot_id else None,
        "ingestion_run_operation_id": str(doc.ingestion_run_operation_id) if doc.ingestion_run_operation_id else None,
    }


async def test_direct_lock_and_validate_provenance_success() -> None:
    request, source_docs, _ = _triple_fixtures()
    session = _session()
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = [_row_from_source_doc(d) for d in source_docs]
    session.execute.return_value = mock_result

    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    await repository._lock_and_validate_provenance(session, request, source_docs)


async def test_direct_lock_and_validate_provenance_rejects_succeeded_with_rejections() -> None:
    request, source_docs, _ = _triple_fixtures()
    session = _session()
    rows = [_row_from_source_doc(d) for d in source_docs]
    rows[0]["ingestion_run_status"] = "SUCCEEDED_WITH_REJECTIONS"
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = rows
    session.execute.return_value = mock_result

    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repository._lock_and_validate_provenance(session, request, source_docs)

    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE


async def test_direct_lock_and_validate_provenance_rejects_checksum_race() -> None:
    request, source_docs, _ = _triple_fixtures()
    session = _session()
    rows = [_row_from_source_doc(d) for d in source_docs]
    rows[0]["canonical_checksum"] = "f" * 64  # changed during write lock
    mock_result = MagicMock()
    mock_result.mappings.return_value.all.return_value = rows
    session.execute.return_value = mock_result

    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repository._lock_and_validate_provenance(session, request, source_docs)

    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH


async def test_direct_check_replay_or_insert_executes_inserts_when_no_existing() -> None:
    request, source_docs, drafts = _triple_fixtures()
    session = _session()
    empty_doc_res = MagicMock()
    empty_doc_res.mappings.return_value.one_or_none.return_value = None
    session.execute.return_value = empty_doc_res

    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    outcome, receipt = await repository._check_replay_or_insert(session, request, drafts, source_docs)

    assert outcome == MaterializationOutcome.CREATED
    assert len(receipt.documents) == 3
    # Check that INSERT statements were executed
    executed_statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert any("INSERT INTO knowledge_document" in stmt for stmt in executed_statements)
    assert any("INSERT INTO knowledge_chunk" in stmt for stmt in executed_statements)


async def test_direct_check_replay_or_insert_detects_exact_replay_and_preserves_legacy_columns() -> None:
    request, source_docs, drafts = _triple_fixtures()
    session = _session()

    doc_rows = []
    chunk_rows_per_doc = []
    for d in drafts:
        doc_id = uuid4()
        chunk_id = uuid4()
        doc_rows.append(
            {
                "id": str(doc_id),
                "title": d.title,
                "publisher": None,
                "source_url": None,
                "document_version": None,
                "record_contract_version": "KNOWLEDGE_EVIDENCE_V1",
                "source_snapshot_member_id": str(d.source_snapshot_member_id),
                "external_document_id": d.external_document_id,
                "document_content_hash": d.document_content_hash,
                "canonicalization_spec_version": d.canonicalization_spec_version,
                "knowledge_index_lock_marker": 0,
                "document_status": "ACTIVE",
            }
        )
        chunk_rows_per_doc.append(
            [
                {
                    "id": str(chunk_id),
                    "knowledge_document_id": str(doc_id),
                    "chunk_index": 0,
                    "chunk_text": d.chunks[0].chunk_text,
                    "embedding_model": "legacy-non-null-model",
                    "vector_store_key": "legacy-key",
                    "content_hash": d.chunks[0].content_hash,
                    "normalization_version": "mfds-label-knowledge-chunk@1",
                    "knowledge_index_lock_marker": 0,
                }
            ]
        )

    doc_mock_1 = MagicMock()
    doc_mock_1.mappings.return_value.one_or_none.return_value = doc_rows[0]
    doc_mock_2 = MagicMock()
    doc_mock_2.mappings.return_value.one_or_none.return_value = doc_rows[1]
    doc_mock_3 = MagicMock()
    doc_mock_3.mappings.return_value.one_or_none.return_value = doc_rows[2]

    chunks_mock_1 = MagicMock()
    chunks_mock_1.mappings.return_value.all.return_value = chunk_rows_per_doc[0]
    chunks_mock_2 = MagicMock()
    chunks_mock_2.mappings.return_value.all.return_value = chunk_rows_per_doc[1]
    chunks_mock_3 = MagicMock()
    chunks_mock_3.mappings.return_value.all.return_value = chunk_rows_per_doc[2]

    session.execute.side_effect = [
        doc_mock_1,
        doc_mock_2,
        doc_mock_3,
        chunks_mock_1,
        chunks_mock_2,
        chunks_mock_3,
    ]

    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    outcome, receipt = await repository._check_replay_or_insert(session, request, drafts, source_docs)

    assert outcome == MaterializationOutcome.EXACT_REPLAY
    assert len(receipt.documents) == 3
    executed_statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert all("INSERT INTO" not in stmt for stmt in executed_statements)


async def test_fetch_source_documents_maps_and_sorts_rows() -> None:
    request, source_docs, _ = _triple_fixtures()
    session = _session()
    # Return rows in reverse order NB, UD, EE
    rows = [
        _row_from_source_doc(source_docs[2]),
        _row_from_source_doc(source_docs[1]),
        _row_from_source_doc(source_docs[0]),
    ]
    mock_res = MagicMock()
    mock_res.mappings.return_value.all.return_value = rows
    session.execute.return_value = mock_res

    repository = SqlAlchemyKnowledgeMaterializationRepository(lambda: session)
    fetched = await repository.fetch_source_documents(request.snapshot_id, request.member_ids)

    assert len(fetched) == 3
    assert [d.section for d in fetched] == ["EE", "UD", "NB"]
    assert fetched[0].member_id == source_docs[0].member_id


async def test_index_repository_participates_in_snapshot_advisory_lock() -> None:
    from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
        SqlAlchemyKnowledgeEvidenceIndexRepository,
    )
    from ai_worker.tasks.rag.knowledge_evidence_index import (
        DistanceMetric,
        KnowledgeChunkIdentity,
        KnowledgeIndexBuildRequest,
        KnowledgeIndexMemberDraft,
        SensitiveEvidenceText,
        create_knowledge_index_receipt,
    )

    snap_id = UUID("00000000-0000-4000-8000-000000000055")
    content = "합성 근거"
    member = KnowledgeIndexMemberDraft(
        identity=KnowledgeChunkIdentity(
            knowledge_chunk_id=UUID("00000000-0000-4000-8000-000000000001"),
            evidence_key="synthetic-materialization-evidence-1",
            source_snapshot_id=snap_id,
            source_snapshot_member_id=UUID("00000000-0000-4000-8000-000000000003"),
            source_code="MFDS",
            source_version="v1",
            canonical_checksum="a" * 64,
            external_document_id="doc-1",
            chunk_index=0,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            locator="$.records[0]",
        ),
        content_text=SensitiveEvidenceText(content),
        embedding=(1.0, 0.0),
    )
    req = KnowledgeIndexBuildRequest(
        index_code="knowledge-evidence",
        index_version="v1",
        embedding_model_ref="synthetic-model",
        embedding_model_version="1.0.0",
        embedding_dimension=2,
        distance_metric=DistanceMetric.COSINE,
        members=(member,),
    )
    rcpt = create_knowledge_index_receipt(req)

    session = _session()
    session.scalar.return_value = None
    repo = SqlAlchemyKnowledgeEvidenceIndexRepository(lambda: session)
    repo._lock_and_validate_members = AsyncMock()  # type: ignore[method-assign]
    repo._load_and_recompute_receipt = AsyncMock(return_value=rcpt)  # type: ignore[method-assign]

    await repo.persist_complete_index(req, rcpt)

    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert len(statements) >= 2
    # 1. Version lock
    assert "version_key" in str(session.execute.await_args_list[0])
    # 2. Snapshot advisory lock
    assert f"knowledge-source-snapshot:{snap_id}" in str(session.execute.await_args_list[1])
