import asyncio
import hashlib
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4
from xml.etree import ElementTree

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.knowledge_snapshot_advisory_lock import (
    acquire_snapshot_advisory_locks,
    snapshot_advisory_lock_key,
)
from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    _INDEX_MEMBER,
    SqlAlchemyKnowledgeEvidenceIndexRepository,
)
from ai_worker.adapters.sqlalchemy_knowledge_materialization import (
    _CHUNK,
    _DOCUMENT,
    SqlAlchemyKnowledgeMaterializationRepository,
)
from ai_worker.admin.knowledge_materialization import (
    MaterializationRunnerConfig,
    execute_materialization,
)
from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeChunkIdentity,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexMemberDraft,
    SensitiveEvidenceText,
    build_knowledge_evidence_index,
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
from ai_worker.tasks.rag.mfds_label_chunk_policy import (
    ChunkPolicyError,
    ChunkPolicyFailureReason,
    KnowledgeChunkDraft,
    build_chunk_drafts,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata
from ai_worker.tasks.rag.source_ingestion.mfds_label import (
    CANONICALIZATION_SPEC_VERSION,
    LOCAL_PRIVATE_STORAGE_BACKEND,
    NORMALIZATION_VERSION,
    OBSERVED_CONTENT_TYPE,
    PARSER_VERSION,
    SCHEMA_VERSION,
    SECTION_TITLES,
    ParsedMfdsLabelDocument,
)
from app.core import config
from infra.python.knowledge_index_role_policy import apply_knowledge_index_role_policy

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 9, 13, 1, 0, tzinfo=UTC)
_CANONICAL_SPEC = CANONICALIZATION_SPEC_VERSION
_SOURCE_VERSION = "external:v1"
_ITEM_SEQ = "200610660"


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "knowledge_mat_178_" + uuid4().hex
    original = config.database_url
    cluster = create_async_engine(original, isolation_level="AUTOCOMMIT", hide_parameters=True)
    engine = create_async_engine(make_url(original).set(database=name), hide_parameters=True)
    try:
        async with cluster.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        monkeypatch.setattr(config, "DB_NAME", name)
        await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
        yield engine
    finally:
        await engine.dispose()
        async with cluster.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await cluster.dispose()


async def _seed_provenance(
    engine,
    *,
    source_id: UUID | None = None,
    endpoint_id: UUID | None = None,
    operation_id: UUID | None = None,
    ingestion_run_id: UUID | None = None,
    snapshot_id: UUID | None = None,
    run_status: str = "SUCCEEDED",
    snapshot_status: str = "CURRENT",
    sections: tuple[str, ...] = ("EE", "UD", "NB"),
    schema_version: str = SCHEMA_VERSION,
    parser_version: str = PARSER_VERSION,
    normalization_version: str = NORMALIZATION_VERSION,
    canonicalization_spec_version: str = CANONICALIZATION_SPEC_VERSION,
    content_type: str = OBSERVED_CONTENT_TYPE,
    storage_backend: str = LOCAL_PRIVATE_STORAGE_BACKEND,
    run_snapshot_id: UUID | None = None,
    source_version: str = _SOURCE_VERSION,
    source_code: str = "MFDS",
    endpoint_code: str = "PRODUCTS",
    operation_code: str = "LIST",
    raw_xml_bodies: dict[str, bytes] | None = None,
) -> tuple[
    KnowledgeMaterializationRequest,
    tuple[MaterializationSourceDocument, ...],
    tuple[KnowledgeDocumentDraft, ...],
]:
    src_id = source_id or uuid4()
    ep_id = endpoint_id or uuid4()
    op_id = operation_id or uuid4()
    run_id = ingestion_run_id or uuid4()
    snap_id = snapshot_id or uuid4()
    verification_id = uuid4()
    effective_run_snap_id = (
        run_snapshot_id if run_snapshot_id is not None else (None if run_status == "FAILED" else snap_id)
    )

    canonical_checksum = hashlib.sha256(b"canonical_snapshot").hexdigest()
    raw_manifest_checksum = hashlib.sha256(b"raw_manifest").hexdigest()

    async with engine.begin() as connection:
        existing_source = await connection.execute(
            text("SELECT id FROM rag_source WHERE source_code = :source_code"),
            {"source_code": source_code},
        )
        src_row = existing_source.mappings().first()
        if src_row is not None:
            src_id = UUID(str(src_row["id"]))
        else:
            await connection.execute(
                text(
                    "INSERT INTO rag_source "
                    "(id, source_code, display_name, lifecycle_status, max_rejected_records, "
                    "max_rejection_rate, empty_result_policy) "
                    "VALUES (:id, :source_code, 'Synthetic MFDS', 'ACTIVE', 0, 0, 'REJECT')"
                ),
                {"id": str(src_id), "source_code": source_code},
            )

        existing_ep = await connection.execute(
            text("SELECT id FROM rag_source_endpoint WHERE source_id = :source_id AND endpoint_code = :endpoint_code"),
            {"source_id": str(src_id), "endpoint_code": endpoint_code},
        )
        ep_row = existing_ep.mappings().first()
        if ep_row is not None:
            ep_id = UUID(str(ep_row["id"]))
        else:
            await connection.execute(
                text(
                    "INSERT INTO rag_source_endpoint "
                    "(id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, acquisition_status) "
                    "VALUES (:id, :source_id, :endpoint_code, 'Synthetic products', 'VERIFIED', 'ENABLED', 'APPROVED')"
                ),
                {"id": str(ep_id), "source_id": str(src_id), "endpoint_code": endpoint_code},
            )

        existing_op = await connection.execute(
            text("SELECT id FROM rag_source_operation WHERE endpoint_id = :endpoint_id AND operation_code = :op_code"),
            {"endpoint_id": str(ep_id), "op_code": operation_code},
        )
        op_row = existing_op.mappings().first()
        if op_row is not None:
            op_id = UUID(str(op_row["id"]))
        else:
            await connection.execute(
                text(
                    "INSERT INTO rag_source_operation "
                    "(id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                    "VALUES (:id, :endpoint_id, :op_code, 'Synthetic list', 'ENABLED', 'APPROVED')"
                ),
                {"id": str(op_id), "endpoint_id": str(ep_id), "op_code": operation_code},
            )
        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot "
                "(id, operation_id, source_version, external_version, raw_manifest_checksum, canonical_checksum, "
                "schema_version, parser_version, normalization_version, canonicalization_spec_version, "
                "endpoint_receipt_hash, record_count, rejected_record_count, verification_status, collected_at) "
                "VALUES (:id, :operation_id, :source_version, 'v1', :raw_hash, :canonical_hash, :schema_ver, "
                ":parser_ver, :norm_ver, :canon_spec, :receipt_hash, 1, 0, 'PENDING', :now)"
            ),
            {
                "id": str(snap_id),
                "operation_id": str(op_id),
                "source_version": source_version,
                "raw_hash": raw_manifest_checksum,
                "canonical_hash": canonical_checksum,
                "schema_ver": schema_version,
                "parser_ver": parser_version,
                "norm_ver": normalization_version,
                "canon_spec": canonicalization_spec_version,
                "receipt_hash": "c" * 64,
                "now": _NOW,
            },
        )
        if snapshot_status == "CURRENT":
            await connection.execute(
                text(
                    "INSERT INTO rag_source_snapshot_verification "
                    "(id, snapshot_id, check_name, verification_result, verified_by, verified_at) "
                    "VALUES (:id, :snapshot_id, 'source-ingestion-integrity', 'PASSED', 'synthetic-reviewer', :now)"
                ),
                {"id": str(verification_id), "snapshot_id": str(snap_id), "now": _NOW},
            )
            await connection.execute(
                text(
                    "UPDATE rag_source_snapshot SET verification_status = 'CURRENT', verification_seal_id = :seal_id, "
                    "verified_at = :now, effective_at = :now WHERE id = :snapshot_id"
                ),
                {"seal_id": str(verification_id), "snapshot_id": str(snap_id), "now": _NOW},
            )
        await connection.execute(
            text(
                "INSERT INTO rag_source_ingestion_run "
                "(id, operation_id, run_group_key, snapshot_id, run_status, attempt_number, started_at, finished_at) "
                "VALUES (:id, :operation_id, :run_group_key, :snapshot_id, :status, 1, :now, :now)"
            ),
            {
                "id": str(run_id),
                "operation_id": str(op_id),
                "run_group_key": "mfds-group-1",
                "snapshot_id": str(effective_run_snap_id) if effective_run_snap_id is not None else None,
                "status": run_status,
                "now": _NOW,
            },
        )

    source_docs: list[MaterializationSourceDocument] = []
    drafts: list[KnowledgeDocumentDraft] = []
    member_ids: list[UUID] = []

    for idx, section in enumerate(sections, start=1):
        artifact_id = uuid4()
        member_id = uuid4()
        member_ids.append(member_id)

        if raw_xml_bodies and section in raw_xml_bodies:
            xml_body = raw_xml_bodies[section]
        else:
            xml_body = f"<ITEM><ITEM_SEQ>{_ITEM_SEQ}</ITEM_SEQ><{section}>Synthetic content for {section}</{section}></ITEM>".encode()
        sha = hashlib.sha256(xml_body).hexdigest()
        object_key = LocalPrivateSourceArtifactStore.object_key_for_checksum(sha)
        locator = f"mfds-label/{_ITEM_SEQ}/{section}"
        artifact_key = f"{locator}.xml"
        ext_doc_id = f"{_ITEM_SEQ}:{section}"

        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO rag_source_ingestion_artifact "
                    "(id, ingestion_run_id, storage_backend, page_number, artifact_key, "
                    "object_key, raw_checksum, byte_size, content_type) "
                    "VALUES (:id, :run_id, :storage_backend, :page_number, :art_key, "
                    ":obj_key, :checksum, :size, :content_type)"
                ),
                {
                    "id": str(artifact_id),
                    "run_id": str(run_id),
                    "storage_backend": storage_backend,
                    "page_number": idx,
                    "art_key": artifact_key,
                    "obj_key": object_key,
                    "checksum": sha,
                    "size": len(xml_body),
                    "content_type": content_type,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_source_snapshot_member "
                    "(id, source_snapshot_id, member_kind, endpoint_id, operation_id, ingestion_artifact_id, "
                    "locator, content_sha256) "
                    "VALUES (:id, :snapshot_id, 'ARTIFACT', NULL, NULL, :artifact_id, "
                    ":locator, :sha)"
                ),
                {
                    "id": str(member_id),
                    "snapshot_id": str(snap_id),
                    "artifact_id": str(artifact_id),
                    "locator": locator,
                    "sha": sha,
                },
            )

        src_doc = MaterializationSourceDocument(
            source_id=src_id,
            source_code=source_code,
            source_lifecycle_status="ACTIVE",
            endpoint_id=ep_id,
            endpoint_code=endpoint_code,
            endpoint_lifecycle_status="VERIFIED",
            endpoint_runtime_status="ENABLED",
            endpoint_acquisition_status="APPROVED",
            operation_id=op_id,
            operation_code=operation_code,
            operation_runtime_status="ENABLED",
            operation_acquisition_status="APPROVED",
            snapshot_id=snap_id,
            source_version=source_version,
            snapshot_verification_status=snapshot_status,
            raw_manifest_checksum=raw_manifest_checksum,
            canonical_checksum=canonical_checksum,
            schema_version=schema_version,
            parser_version=parser_version,
            normalization_version=normalization_version,
            canonicalization_spec_version=canonicalization_spec_version,
            member_id=member_id,
            member_kind="ARTIFACT",
            locator=locator,
            content_sha256=sha,
            ingestion_run_id=run_id,
            ingestion_run_status=run_status,
            ingestion_artifact_id=artifact_id,
            artifact_key=artifact_key,
            section=section,
            storage_backend=storage_backend,
            artifact_kind="RAW_RESPONSE",
            page_number=idx,
            reject_code=None,
            parser_location=None,
            raw_checksum=sha,
            byte_size=len(xml_body),
            content_type=content_type,
            object_key=object_key,
            ingestion_run_snapshot_id=effective_run_snap_id,
            ingestion_run_operation_id=op_id,
        )
        source_docs.append(src_doc)

        chunk_text = f"Synthetic content for {section}"
        chunk_hash = hashlib.sha256(chunk_text.encode()).hexdigest()
        draft = KnowledgeDocumentDraft(
            title=f"Synthetic MFDS {_ITEM_SEQ} {section}",
            source_snapshot_member_id=member_id,
            external_document_id=ext_doc_id,
            document_content_hash=sha,
            canonicalization_spec_version=_CANONICAL_SPEC,
            chunks=(
                KnowledgeChunkDraft(
                    chunk_index=0,
                    content_hash=chunk_hash,
                    normalization_version=CHUNK_POLICY_VERSION,
                    chunk_text=chunk_text,
                ),
            ),
        )
        drafts.append(draft)

    req = KnowledgeMaterializationRequest(
        snapshot_id=snap_id,
        member_ids=tuple(member_ids),
        expected_item_seq=_ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
    )
    return req, tuple(source_docs), tuple(drafts)


# =============================================================================
# 2. Concurrency Tests (D2 Cases A ~ F: Helper Semantic Tests on Advisory Lock)
# =============================================================================


async def test_d2_case_a_same_snapshot_advisory_lock_serializes(database) -> None:
    """Case A: Materialization and Index TXs on the same snapshot serialize via advisory lock."""
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    snapshot_id = uuid4()

    event_tx1_locked = asyncio.Event()
    event_tx2_attempted = asyncio.Event()
    event_tx2_acquired = asyncio.Event()

    async def _tx1():
        async with factory() as session, session.begin():
            await acquire_snapshot_advisory_locks(session, [snapshot_id])
            event_tx1_locked.set()
            # Wait until TX2 has attempted and blocked
            await event_tx2_attempted.wait()
            await asyncio.sleep(0.05)
            # Commit releases lock

    async def _tx2():
        await event_tx1_locked.wait()
        async with factory() as session, session.begin():
            # Signal that TX2 is attempting lock
            event_tx2_attempted.set()
            await acquire_snapshot_advisory_locks(session, [snapshot_id])
            event_tx2_acquired.set()

    t1 = asyncio.create_task(_tx1())
    t2 = asyncio.create_task(_tx2())

    # Wait until TX2 attempts
    await event_tx2_attempted.wait()
    # TX2 must NOT have acquired lock yet because TX1 has it
    assert not event_tx2_acquired.is_set()

    # Verify via pg_locks that one lock is granted and one is waiting
    lock_key = snapshot_advisory_lock_key(snapshot_id)
    async with factory() as check_session:
        res = await check_session.execute(
            text(
                "SELECT granted FROM pg_locks "
                "WHERE locktype = 'advisory' "
                "AND objid = (hashtextextended(:key, 0)::bit(32)::bigint)"
            ),
            {"key": lock_key},
        )
        granted_statuses = [r[0] for r in res.fetchall()]
        # One granted=True, one granted=False (waiting)
        if len(granted_statuses) == 2:
            assert True in granted_statuses
            assert False in granted_statuses

    await asyncio.gather(t1, t2)
    assert event_tx2_acquired.is_set()


async def test_d2_case_b_partial_overlap_serializes_without_deadlock(database) -> None:
    """Case B: TX1 locks (A, B), TX2 locks (B); B serializes cleanly without deadlock."""
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    snap_a = UUID("00000000-0000-4000-8000-000000000001")
    snap_b = UUID("00000000-0000-4000-8000-000000000002")

    event_tx_ab_locked = asyncio.Event()
    order_executed: list[str] = []

    async def _tx_ab():
        async with factory() as session, session.begin():
            await acquire_snapshot_advisory_locks(session, [snap_a, snap_b])
            event_tx_ab_locked.set()
            await asyncio.sleep(0.05)
            order_executed.append("AB")

    async def _tx_b():
        await event_tx_ab_locked.wait()
        async with factory() as session, session.begin():
            await acquire_snapshot_advisory_locks(session, [snap_b])
            order_executed.append("B")

    await asyncio.gather(_tx_ab(), _tx_b())
    assert order_executed == ["AB", "B"]


async def test_d2_case_c_reversed_inputs_acquire_in_binary_order_no_deadlock(database) -> None:
    """Case C: Reversed input orders [A, B] and [B, A] sort by UUID.bytes; no deadlock."""
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    # Binary order: id_1.bytes < id_2.bytes
    id_1 = UUID("11111111-0000-4000-8000-000000000000")
    id_2 = UUID("22222222-0000-4000-8000-000000000000")

    executed = 0

    async def _worker(input_order: list[UUID]):
        nonlocal executed
        async with factory() as session, session.begin():
            keys = await acquire_snapshot_advisory_locks(session, input_order)
            assert keys == (snapshot_advisory_lock_key(id_1), snapshot_advisory_lock_key(id_2))
            executed += 1

    await asyncio.gather(_worker([id_1, id_2]), _worker([id_2, id_1]))
    assert executed == 2


async def test_d2_case_d_disjoint_snapshots_do_not_block_each_other(database) -> None:
    """Case D: Disjoint snapshots A and C do not block each other."""
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    snap_a = UUID("aaaaaaaa-0000-4000-8000-000000000000")
    snap_c = UUID("cccccccc-0000-4000-8000-000000000000")

    tx1_holding = asyncio.Event()
    tx2_finished = asyncio.Event()

    async def _tx_a():
        async with factory() as session, session.begin():
            await acquire_snapshot_advisory_locks(session, [snap_a])
            tx1_holding.set()
            await tx2_finished.wait()

    async def _tx_c():
        await tx1_holding.wait()
        async with factory() as session, session.begin():
            # Must acquire immediately without waiting for TX_A
            await acquire_snapshot_advisory_locks(session, [snap_c])
            tx2_finished.set()

    await asyncio.gather(_tx_a(), _tx_c())
    assert tx2_finished.is_set()


async def test_d2_case_e_rollback_releases_lock(database) -> None:
    """Case E: Rollback releases advisory lock so next transaction proceeds."""
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    snap_id = uuid4()

    with pytest.raises(RuntimeError):
        async with factory() as session, session.begin():
            await acquire_snapshot_advisory_locks(session, [snap_id])
            raise RuntimeError("forced rollback")

    # TX2 should acquire immediately
    async with factory() as session, session.begin():
        locked = await acquire_snapshot_advisory_locks(session, [snap_id])
        assert len(locked) == 1


async def test_d2_case_f_commit_releases_lock(database) -> None:
    """Case F: Commit releases advisory lock so next transaction proceeds."""
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    snap_id = uuid4()

    async with factory() as session, session.begin():
        await acquire_snapshot_advisory_locks(session, [snap_id])

    # Next TX acquires cleanly
    async with factory() as session, session.begin():
        locked = await acquire_snapshot_advisory_locks(session, [snap_id])
        assert len(locked) == 1


async def test_d2_cross_flow_materialization_and_index_mutual_exclusion(database, monkeypatch) -> None:
    """Materialization and Index repositories concurrently accessing the same Snapshot

    prove PostgreSQL advisory lock mutual exclusion via pg_locks observation (no sleeps).
    """
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    mat_repo = SqlAlchemyKnowledgeMaterializationRepository(factory)
    idx_repo = SqlAlchemyKnowledgeEvidenceIndexRepository(factory)

    # 1. Seed provenance & perform initial materialization create
    request, source_docs, drafts = await _seed_provenance(engine)
    snapshot_id = request.snapshot_id
    initial_res = await mat_repo.persist_materialization(request, drafts, source_docs)
    assert initial_res.outcome == MaterializationOutcome.CREATED
    first_chunk_receipt = initial_res.receipt.documents[0].chunks[0]

    # 2. Prepare Index build request for the materialized chunk
    first_doc = source_docs[0]
    first_draft = drafts[0]
    first_chunk_draft = first_draft.chunks[0]

    identity = KnowledgeChunkIdentity(
        knowledge_chunk_id=first_chunk_receipt.knowledge_chunk_id,
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=first_doc.member_id,
        source_code=first_doc.source_code,
        source_version=first_doc.source_version,
        canonical_checksum=first_doc.canonical_checksum,
        external_document_id=first_draft.external_document_id,
        chunk_index=first_chunk_draft.chunk_index,
        content_hash=first_chunk_draft.content_hash,
        locator=first_doc.locator,
    )
    index_request = KnowledgeIndexBuildRequest(
        index_code="mfds-evidence-index-cross",
        index_version="v1.0.0",
        embedding_model_ref="synthetic-model",
        embedding_model_version="1.0.0",
        embedding_dimension=2,
        distance_metric=DistanceMetric.COSINE,
        members=(
            KnowledgeIndexMemberDraft(
                identity=identity,
                content_text=SensitiveEvidenceText(first_chunk_draft.chunk_text),
                embedding=(1.0, 0.0),
            ),
        ),
    )

    # 3. Synchronizing barriers (no sleeps)
    event_mat_locked = asyncio.Event()
    event_release_mat = asyncio.Event()
    event_idx_attempting = asyncio.Event()

    import ai_worker.adapters.sqlalchemy_knowledge_materialization as mat_mod

    orig_mat_acquire = mat_mod.acquire_snapshot_advisory_locks

    async def _hooked_mat_acquire(session, snapshot_ids):
        keys = await orig_mat_acquire(session, snapshot_ids)
        event_mat_locked.set()
        await event_release_mat.wait()
        return keys

    monkeypatch.setattr(mat_mod, "acquire_snapshot_advisory_locks", _hooked_mat_acquire)

    # 4. Task 1: Start Materialization exact replay (acquires lock, waits on barrier)
    mat_task = asyncio.create_task(mat_repo.persist_materialization(request, drafts, source_docs))
    await event_mat_locked.wait()

    # 5. Task 2: Start Index Build concurrently for the same snapshot
    import ai_worker.adapters.sqlalchemy_knowledge_evidence_index as idx_mod

    orig_idx_acquire = idx_mod.acquire_snapshot_advisory_locks

    async def _hooked_idx_acquire(session, snapshot_ids):
        event_idx_attempting.set()
        return await orig_idx_acquire(session, snapshot_ids)

    monkeypatch.setattr(idx_mod, "acquire_snapshot_advisory_locks", _hooked_idx_acquire)

    idx_task = asyncio.create_task(build_knowledge_evidence_index(index_request, repository=idx_repo))
    await event_idx_attempting.wait()

    # 6. Observe pg_locks directly from an independent connection
    lock_key = snapshot_advisory_lock_key(snapshot_id)
    observed_conflict = False
    for _ in range(50):
        async with factory() as check_session:
            res = await check_session.execute(
                text(
                    "SELECT granted FROM pg_locks "
                    "WHERE locktype = 'advisory' "
                    "AND objid = (hashtextextended(:key, 0)::bit(32)::bigint)"
                ),
                {"key": lock_key},
            )
            statuses = [r[0] for r in res.fetchall()]
            if len(statuses) >= 2 and True in statuses and False in statuses:
                observed_conflict = True
                break
        await asyncio.sleep(0.01)

    assert observed_conflict, "Expected one granted lock and one waiting lock on pg_locks"

    # 7. Release Materialization exact replay to commit
    event_release_mat.set()

    # 8. Both Materialization exact replay and Index persist complete successfully
    mat_res, idx_receipt = await asyncio.gather(mat_task, idx_task)
    assert mat_res.outcome == MaterializationOutcome.EXACT_REPLAY
    assert mat_res.receipt == initial_res.receipt
    assert idx_receipt.index_code == "mfds-evidence-index-cross"
    assert idx_receipt.member_count == 1


# =============================================================================
# 3. Transaction Atomicity & Natural Key Tests
# =============================================================================


async def test_transaction_atomicity_create_exact_replay_and_conflict(database) -> None:
    """Normal create, exact replay identity preservation, and content conflict rejection."""
    engine = database
    req, source_docs, drafts = await _seed_provenance(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    # 1. First Create
    res1 = await repo.persist_materialization(req, drafts, source_docs)
    assert res1.outcome == MaterializationOutcome.CREATED
    assert res1.is_exact_replay is False
    assert len(res1.receipt.documents) == 3

    # Check rows persisted
    async with factory() as session:
        doc_count = await session.scalar(select(text("count(*)")).select_from(_DOCUMENT))
        chunk_count = await session.scalar(select(text("count(*)")).select_from(_CHUNK))
        assert doc_count == 3
        assert chunk_count == 3

    # 2. Exact Replay
    res2 = await repo.persist_materialization(req, drafts, source_docs)
    assert res2.outcome == MaterializationOutcome.EXACT_REPLAY
    assert res2.is_exact_replay is True
    assert res2.receipt == res1.receipt

    # Row counts remain unchanged
    async with factory() as session:
        doc_count_after = await session.scalar(select(text("count(*)")).select_from(_DOCUMENT))
        chunk_count_after = await session.scalar(select(text("count(*)")).select_from(_CHUNK))
        assert doc_count_after == 3
        assert chunk_count_after == 3

    # 3. Content Conflict on Natural Key (source_snapshot_member_id, external_document_id)
    mutated_chunk = KnowledgeChunkDraft(
        chunk_index=0,
        content_hash=hashlib.sha256(b"Tampered text content").hexdigest(),
        normalization_version=CHUNK_POLICY_VERSION,
        chunk_text="Tampered text content",
    )
    mutated_draft = KnowledgeDocumentDraft(
        title=drafts[0].title,
        source_snapshot_member_id=drafts[0].source_snapshot_member_id,
        external_document_id=drafts[0].external_document_id,
        document_content_hash=drafts[0].document_content_hash,
        canonicalization_spec_version=drafts[0].canonicalization_spec_version,
        chunks=(mutated_chunk,),
    )
    conflicting_drafts = (mutated_draft, drafts[1], drafts[2])

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repo.persist_materialization(req, conflicting_drafts, source_docs)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.CONTENT_CONFLICT


async def test_provenance_mismatch_rolls_back_completely(database) -> None:
    """Provenance mismatch rolls back with zero documents or chunks persisted."""
    engine = database
    req, source_docs, drafts = await _seed_provenance(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    # Tamper with pre-read checksum so provenance validation fails
    tampered_doc = replace(source_docs[0], raw_checksum="0" * 64)
    tampered_source_docs = (tampered_doc, source_docs[1], source_docs[2])

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repo.persist_materialization(req, drafts, tampered_source_docs)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH

    # Verify absolute zero rows written (all-or-nothing rollback)
    async with factory() as session:
        doc_count = await session.scalar(select(text("count(*)")).select_from(_DOCUMENT))
        chunk_count = await session.scalar(select(text("count(*)")).select_from(_CHUNK))
        assert doc_count == 0
        assert chunk_count == 0


async def test_pre_commit_provenance_revalidation_failure_rolls_back(database) -> None:
    """Pre-commit race: If provenance is revoked between start and commit, roll back completely."""
    engine = database
    req, source_docs, drafts = await _seed_provenance(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    # Patch _revalidate_provenance_before_commit to raise SOURCE_NOT_ELIGIBLE
    async def _failing_revalidate(*args, **kwargs):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE)

    repo._revalidate_provenance_before_commit = _failing_revalidate  # type: ignore[method-assign]

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repo.persist_materialization(req, drafts, source_docs)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE

    # Zero rows remain
    async with factory() as session:
        assert await session.scalar(select(text("count(*)")).select_from(_DOCUMENT)) == 0
        assert await session.scalar(select(text("count(*)")).select_from(_CHUNK)) == 0


# =============================================================================
# 4. D3 Eligibility Matrix Tests
# =============================================================================


@pytest.mark.parametrize(
    ("run_status", "expected_reason"),
    [
        ("SUCCEEDED_WITH_REJECTIONS", KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE),
        ("RUNNING", KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE),
        ("NO_CHANGE", KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE),
        ("FAILED", KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE),
    ],
)
async def test_d3_eligibility_run_status_rejected(database, run_status: str, expected_reason) -> None:
    """Non-SUCCEEDED ingestion runs are strictly rejected."""
    engine = database
    req, source_docs, drafts = await _seed_provenance(engine, run_status=run_status)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repo.persist_materialization(req, drafts, source_docs)
    assert exc_info.value.reason is expected_reason


async def test_d3_eligibility_snapshot_pending_rejected(database) -> None:
    """Snapshot with verification_status=PENDING is strictly rejected (CURRENT only)."""
    engine = database
    req, source_docs, drafts = await _seed_provenance(engine, snapshot_status="PENDING")
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repo.persist_materialization(req, drafts, source_docs)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE


# =============================================================================
# 5. Builder Least-Privilege Tests
# =============================================================================


async def test_builder_least_privilege_enforces_permissions(database) -> None:
    """Builder role can SELECT/INSERT knowledge document/chunk, but blocked on UPDATE/DELETE/DDL."""
    engine = database
    req, source_docs, drafts = await _seed_provenance(engine)

    suffix = uuid4().hex[:12]
    builder = f"mat_builder_{suffix}"
    runtime = f"mat_runtime_{suffix}"
    password = "synthetic-role-password"

    builder_engine = create_async_engine(database.url.set(username=builder, password=password), hide_parameters=True)
    try:
        async with database.begin() as connection:
            await connection.execute(text(f"CREATE ROLE \"{builder}\" LOGIN PASSWORD '{password}'"))
            await connection.execute(text(f"CREATE ROLE \"{runtime}\" LOGIN PASSWORD '{password}'"))
            await apply_knowledge_index_role_policy(
                connection,
                owner=config.DB_USER,
                runtime=runtime,
                builder=builder,
            )

        builder_factory = async_sessionmaker(builder_engine, expire_on_commit=False, autoflush=False)
        builder_repo = SqlAlchemyKnowledgeMaterializationRepository(builder_factory)

        # 1. Allowed: Builder executes materialization (SELECT source, row locks, INSERT doc/chunk)
        res = await builder_repo.persist_materialization(req, drafts, source_docs)
        assert res.outcome == MaterializationOutcome.CREATED
        assert len(res.receipt.documents) == 3

        # 2. Blocked: UPDATE on business columns / provenance
        blocked_statements = [
            "UPDATE rag_source SET lifecycle_status='REVOKED'",
            "UPDATE knowledge_document SET title='hacked'",
            "UPDATE knowledge_chunk SET chunk_text='hacked'",
            "DELETE FROM knowledge_document",
            "DELETE FROM knowledge_chunk",
            "DROP TABLE knowledge_document",
        ]
        for stmt in blocked_statements:
            with pytest.raises(DBAPIError) as err:
                async with builder_engine.begin() as connection:
                    await connection.execute(text(stmt))
            assert getattr(err.value.orig, "sqlstate", None) == "42501"

    finally:
        await builder_engine.dispose()
        async with database.begin() as connection:
            await connection.execute(text(f'DROP OWNED BY "{builder}"'))
            await connection.execute(text(f'DROP OWNED BY "{runtime}"'))
            await connection.execute(text(f'DROP ROLE IF EXISTS "{builder}"'))
            await connection.execute(text(f'DROP ROLE IF EXISTS "{runtime}"'))


# =============================================================================
# 6. D4 Post-Commit Audit Tests
# =============================================================================


async def test_post_commit_audit_roundtrip_and_failure_safety(database) -> None:
    """Post-commit audit re-queries in a new session; failure does not rollback committed rows."""
    engine = database
    req, source_docs, drafts = await _seed_provenance(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    # 1. Materialize & commit
    res = await repo.persist_materialization(req, drafts, source_docs)
    assert res.outcome == MaterializationOutcome.CREATED

    # 2. Audit success in new session
    audit_ok = await repo.audit_post_commit(res.receipt, req, drafts, source_docs)
    assert audit_ok is True

    # 3. Forced audit failure (tampered receipt)
    tampered_receipt = KnowledgeMaterializationReceipt(
        snapshot_id=res.receipt.snapshot_id,
        source_code=res.receipt.source_code,
        source_version=res.receipt.source_version,
        snapshot_canonical_checksum="0" * 64,  # tampered
        canonicalization_spec_version=res.receipt.canonicalization_spec_version,
        item_seq=res.receipt.item_seq,
        chunk_policy_version=res.receipt.chunk_policy_version,
        documents=res.receipt.documents,
    )
    audit_failed = await repo.audit_post_commit(tampered_receipt, req, drafts, source_docs)
    assert audit_failed is False

    # 4. Verify safety: DB committed rows STILL EXIST intact
    async with factory() as session:
        doc_count = await session.scalar(select(text("count(*)")).select_from(_DOCUMENT))
        chunk_count = await session.scalar(select(text("count(*)")).select_from(_CHUNK))
        assert doc_count == 3
        assert chunk_count == 3


# =============================================================================
# 7. Existing Index Compatibility Tests
# =============================================================================


async def test_materialized_chunks_compatible_with_knowledge_evidence_index(database) -> None:
    """Materialized KnowledgeChunks wire directly into SqlAlchemyKnowledgeEvidenceIndexRepository."""
    engine = database
    req, source_docs, drafts = await _seed_provenance(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    mat_repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    # 1. Materialize
    res = await mat_repo.persist_materialization(req, drafts, source_docs)
    doc_receipt = res.receipt.documents[0]
    chunk_receipt = doc_receipt.chunks[0]

    # 2. Build index request using materialized chunk identity
    index_repo = SqlAlchemyKnowledgeEvidenceIndexRepository(factory)
    identity = KnowledgeChunkIdentity(
        knowledge_chunk_id=chunk_receipt.knowledge_chunk_id,
        source_snapshot_id=req.snapshot_id,
        source_snapshot_member_id=doc_receipt.source_snapshot_member_id,
        source_code=res.receipt.source_code,
        source_version=res.receipt.source_version,
        canonical_checksum=res.receipt.snapshot_canonical_checksum,
        external_document_id=doc_receipt.external_document_id,
        chunk_index=chunk_receipt.chunk_index,
        content_hash=chunk_receipt.content_hash,
        locator=doc_receipt.locator,
    )
    index_request = KnowledgeIndexBuildRequest(
        index_code="mfds-evidence-index",
        index_version="v1.0.0",
        embedding_model_ref="synthetic-model",
        embedding_model_version="1.0.0",
        embedding_dimension=2,
        distance_metric=DistanceMetric.COSINE,
        members=(
            KnowledgeIndexMemberDraft(
                identity=identity,
                content_text=SensitiveEvidenceText(drafts[0].chunks[0].chunk_text),
                embedding=(1.0, 0.0),
            ),
        ),
    )

    # 3. Build & persist index
    idx_receipt = await build_knowledge_evidence_index(index_request, repository=index_repo)
    assert idx_receipt.index_code == "mfds-evidence-index"
    assert idx_receipt.member_count == 1

    # Verify persisted index member
    async with factory() as session:
        stored_members = await session.scalar(select(text("count(*)")).select_from(_INDEX_MEMBER))
        assert stored_members == 1


# =============================================================================
# 8. NN Empty Article Fail-Closed
# =============================================================================


async def test_nn_empty_article_fail_closed_unsupported() -> None:
    """Empty NN article remains CHUNK_POLICY_UNSUPPORTED without breaking ingestion."""
    parsed = ParsedMfdsLabelDocument(
        section="NN",
        document_title="e약은요 정보",
        article_count=1,
        paragraph_count=0,
        nonempty_paragraph_count=0,
        content_status="VALID",
        empty_article_titles=("사용상의주의사항",),
        root=ElementTree.fromstring("<DOC/>"),
    )
    with pytest.raises(ChunkPolicyError) as exc_info:
        build_chunk_drafts(parsed, chunk_policy_version=CHUNK_POLICY_VERSION)
    assert exc_info.value.reason is ChunkPolicyFailureReason.CHUNK_POLICY_UNSUPPORTED


# =============================================================================
# 9. Provenance & Version Negative Regressions (MUST FIX 1)
# =============================================================================


async def test_negative_snapshot_member_run_snapshot_mismatch_rejected(database) -> None:
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    # 1. Seed Snapshot B (with its own run and artifact)
    _, source_docs_b, _ = await _seed_provenance(engine, operation_code="LIST_B")
    doc_b = source_docs_b[0]

    # 2. Seed Snapshot A
    request_a, source_docs_a, drafts_a = await _seed_provenance(engine, source_version="external:v2")

    # 3. Point Snapshot A member to Snapshot B artifact
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE rag_source_snapshot_member SET ingestion_artifact_id = :artifact_id WHERE id = :member_id"),
            {
                "artifact_id": str(doc_b.ingestion_artifact_id),
                "member_id": str(source_docs_a[0].member_id),
            },
        )

    # Update in-memory source doc to mirror the cross-snapshot artifact
    mutated_doc_0 = replace(
        source_docs_a[0],
        ingestion_artifact_id=doc_b.ingestion_artifact_id,
        artifact_key=doc_b.artifact_key,
        content_sha256=doc_b.content_sha256,
        raw_checksum=doc_b.raw_checksum,
        object_key=doc_b.object_key,
        ingestion_run_id=doc_b.ingestion_run_id,
        ingestion_run_status=doc_b.ingestion_run_status,
        ingestion_run_snapshot_id=doc_b.snapshot_id,
        ingestion_run_operation_id=doc_b.operation_id,
    )
    test_docs = (mutated_doc_0, source_docs_a[1], source_docs_a[2])

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repo.persist_materialization(request_a, drafts_a, test_docs)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID

    async with factory() as session:
        doc_count = await session.scalar(select(text("count(*)")).select_from(_DOCUMENT))
        chunk_count = await session.scalar(select(text("count(*)")).select_from(_CHUNK))
        assert doc_count == 0
        assert chunk_count == 0


async def test_negative_content_type_mismatch_rejected(database) -> None:
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    request, source_docs, drafts = await _seed_provenance(
        engine,
        content_type="text/plain",
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repo.persist_materialization(request, drafts, source_docs)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID

    async with factory() as session:
        doc_count = await session.scalar(select(text("count(*)")).select_from(_DOCUMENT))
        chunk_count = await session.scalar(select(text("count(*)")).select_from(_CHUNK))
        assert doc_count == 0
        assert chunk_count == 0


async def test_negative_schema_version_mismatch_fail_closed(database) -> None:
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    request, source_docs, drafts = await _seed_provenance(
        engine,
        schema_version="invalid-schema@99",
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repo.persist_materialization(request, drafts, source_docs)
    assert exc_info.value.reason in (
        KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH,
        KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID,
    )

    async with factory() as session:
        doc_count = await session.scalar(select(text("count(*)")).select_from(_DOCUMENT))
        chunk_count = await session.scalar(select(text("count(*)")).select_from(_CHUNK))
        assert doc_count == 0
        assert chunk_count == 0


async def test_negative_parser_version_mismatch_fail_closed(database) -> None:
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    request, source_docs, drafts = await _seed_provenance(
        engine,
        parser_version="invalid-parser@99",
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repo.persist_materialization(request, drafts, source_docs)
    assert exc_info.value.reason in (
        KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH,
        KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID,
    )

    async with factory() as session:
        doc_count = await session.scalar(select(text("count(*)")).select_from(_DOCUMENT))
        chunk_count = await session.scalar(select(text("count(*)")).select_from(_CHUNK))
        assert doc_count == 0
        assert chunk_count == 0


async def test_negative_normalization_version_mismatch_fail_closed(database) -> None:
    engine = database
    factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqlAlchemyKnowledgeMaterializationRepository(factory)

    request, source_docs, drafts = await _seed_provenance(
        engine,
        normalization_version="invalid-norm@99",
    )

    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        await repo.persist_materialization(request, drafts, source_docs)
    assert exc_info.value.reason in (
        KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH,
        KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID,
    )

    async with factory() as session:
        doc_count = await session.scalar(select(text("count(*)")).select_from(_DOCUMENT))
        chunk_count = await session.scalar(select(text("count(*)")).select_from(_CHUNK))
        assert doc_count == 0
        assert chunk_count == 0


async def test_admin_runner_execute_materialization_postgresql_integration(database, tmp_path) -> None:
    """Verifies admin runner end-to-end against real PostgreSQL with builder role and local private reader."""
    engine = database
    raw_xml_bodies = {
        section: (
            f'<DOC type="{section}" title="{SECTION_TITLES[section][0]}">'
            f'<ARTICLE title="{SECTION_TITLES[section][0]}">'
            f"<PARAGRAPH>Synthetic content for {section}</PARAGRAPH>"
            f"</ARTICLE></DOC>"
        ).encode()
        for section in ("EE", "UD", "NB")
    }
    req, source_docs, drafts = await _seed_provenance(
        engine,
        source_code="MFDS_PRODUCT_LABEL",
        endpoint_code="MFDS_NEDRUG_LABEL_XML",
        operation_code="COLLECT_NOVASC_200610660_LABEL_XML",
        raw_xml_bodies=raw_xml_bodies,
    )
    snapshot_id = req.snapshot_id
    item_seq = req.expected_item_seq
    expected_checksum = source_docs[0].canonical_checksum

    # Store raw artifacts so LocalPrivateSourceArtifactReader can read them
    artifact_root = tmp_path / "artifacts"
    store = LocalPrivateSourceArtifactStore(artifact_root)
    for doc in source_docs:
        xml_bytes = raw_xml_bodies[doc.section]
        store_file = tmp_path / f"temp_{doc.section}.xml"
        store_file.write_bytes(xml_bytes)
        store.put_verified(
            page_number=doc.page_number,
            file_path=store_file,
            metadata=RawArtifactMetadata(
                artifact_key=doc.artifact_key,
                raw_checksum=doc.raw_checksum,
                byte_size=len(xml_bytes),
                content_type=doc.content_type,
            ),
        )

    # Make artifact_root read-only for LocalPrivateSourceArtifactReader
    os.chmod(artifact_root, 0o500)
    for p in artifact_root.rglob("*"):
        if p.is_dir():
            os.chmod(p, 0o500)
        else:
            os.chmod(p, 0o400)

    # Provision builder role
    suffix = uuid4().hex[:12]
    builder = f"mat_bld_{suffix}"
    runtime = f"mat_rt_{suffix}"
    password = "synthetic-role-password"
    async with database.begin() as connection:
        await connection.execute(text(f"CREATE ROLE \"{builder}\" LOGIN PASSWORD '{password}'"))
        await connection.execute(text(f"CREATE ROLE \"{runtime}\" LOGIN PASSWORD '{password}'"))
        await apply_knowledge_index_role_policy(
            connection,
            owner=config.DB_USER,
            runtime=runtime,
            builder=builder,
        )

    builder_url = database.url.set(username=builder, password=password)
    runner_config = MaterializationRunnerConfig(
        url=builder_url,
        builder_user=builder,
        artifact_root=artifact_root,
    )

    try:
        summary = await execute_materialization(
            config=runner_config,
            snapshot_id=snapshot_id,
            expected_item_seq=item_seq,
            expected_canonical_checksum=expected_checksum,
            verify_replay=True,
        )

        assert summary["execution_status"] == "SUCCESS"
        assert summary["outcome"] == "CREATED"
        assert summary["source_code"] == "MFDS_PRODUCT_LABEL"
        assert summary["document_count"] == 3
        assert summary["chunk_count"] == 3
        assert summary["post_commit_audit_passed"] is True
        assert summary["exact_replay_verified"] is True
        assert len(summary["knowledge_document_ids"]) == 3
        assert len(summary["knowledge_chunk_ids"]) == 3
    finally:
        # Restore permissions so pytest cleanup can succeed
        os.chmod(artifact_root, 0o700)
        for p in artifact_root.rglob("*"):
            if p.is_dir():
                os.chmod(p, 0o700)
            else:
                os.chmod(p, 0o600)
        async with database.begin() as connection:
            await connection.execute(text(f'DROP OWNED BY "{builder}"'))
            await connection.execute(text(f'DROP OWNED BY "{runtime}"'))
            await connection.execute(text(f'DROP ROLE IF EXISTS "{builder}"'))
            await connection.execute(text(f'DROP ROLE IF EXISTS "{runtime}"'))
