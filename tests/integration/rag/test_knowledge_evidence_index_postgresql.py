import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.openai_text_embedding import OPENAI_TEXT_EMBEDDING_ADAPTER_REF
from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    _INDEX,
    _INDEX_MEMBER,
    SqlAlchemyKnowledgeEvidenceIndexRepository,
)
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.admin.knowledge_evidence_index import (
    EXPECTED_DIMENSION,
    EXPECTED_INDEX_CODE,
    EXPECTED_INDEX_VERSION,
    NOVASC_CANONICAL_CHECKSUM,
    NOVASC_ITEM_SEQ,
    NOVASC_SNAPSHOT_ID,
    NOVASC_SOURCE_VERSION,
    KnowledgeEvidenceIndexRunnerConfig,
    execute_knowledge_evidence_index_build,
)
from ai_worker.tasks.rag.evidence_retrieval import SensitiveText
from ai_worker.tasks.rag.evidence_search import SensitiveVector
from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeChunkIdentity,
    KnowledgeEvidenceIndexFailureReason,
    KnowledgeEvidenceIndexValidationError,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexMemberDraft,
    SensitiveEvidenceText,
    build_knowledge_evidence_index,
    create_knowledge_index_receipt,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotProvenanceReceipt,
    SnapshotVerificationStatus,
    SourceSnapshotMemberCreate,
    SourceSnapshotMemberKind,
)
from ai_worker.tasks.rag.text_embedding import TextEmbeddingPort, TextEmbeddingSuccess
from app.core import config
from infra.python.knowledge_index_role_policy import apply_knowledge_index_role_policy

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio

_SOURCE_ID = UUID("17800000-0000-4000-8000-000000000001")
_ENDPOINT_ID = UUID("17800000-0000-4000-8000-000000000002")
_OPERATION_ID = UUID("17800000-0000-4000-8000-000000000003")
_SNAPSHOT_ID = UUID("17800000-0000-4000-8000-000000000004")
_VERIFICATION_ID = UUID("17800000-0000-4000-8000-000000000005")
_DOCUMENT_ID = UUID("17800000-0000-4000-8000-000000000006")
_CHUNK_ID = UUID("17800000-0000-4000-8000-000000000007")
_TEXT_SENTINEL = "SYNTHETIC_EVIDENCE_TEXT_SENTINEL_178"
_CONTENT_HASH = hashlib.sha256(_TEXT_SENTINEL.encode()).hexdigest()
_NOW = datetime(2026, 9, 13, 1, 0, tzinfo=UTC)


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return alembic_config


async def _seed_source_chain(engine) -> UUID:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rag_source "
                "(id, source_code, display_name, lifecycle_status, max_rejected_records, "
                "max_rejection_rate, empty_result_policy) "
                "VALUES (:id, 'MFDS', 'Synthetic MFDS', 'ACTIVE', 0, 0, 'REJECT')"
            ),
            {"id": str(_SOURCE_ID)},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_endpoint "
                "(id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, acquisition_status) "
                "VALUES (:id, :source_id, 'PRODUCTS', 'Synthetic products', 'VERIFIED', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(_ENDPOINT_ID), "source_id": str(_SOURCE_ID)},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_operation "
                "(id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                "VALUES (:id, :endpoint_id, 'LIST', 'Synthetic list', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(_OPERATION_ID), "endpoint_id": str(_ENDPOINT_ID)},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot "
                "(id, operation_id, source_version, external_version, raw_manifest_checksum, canonical_checksum, "
                "schema_version, parser_version, normalization_version, canonicalization_spec_version, "
                "endpoint_receipt_hash, record_count, rejected_record_count, verification_status, collected_at) "
                "VALUES (:id, :operation_id, 'external:v1', 'v1', :raw_hash, :canonical_hash, 'schema-v1', "
                "'parser-v1', 'normalization-v1', 'canonical-v1', :receipt_hash, 1, 0, 'PENDING', :now)"
            ),
            {
                "id": str(_SNAPSHOT_ID),
                "operation_id": str(_OPERATION_ID),
                "raw_hash": "a" * 64,
                "canonical_hash": "b" * 64,
                "receipt_hash": "c" * 64,
                "now": _NOW,
            },
        )
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    provenance = SnapshotProvenanceReceipt(
        source_id=_SOURCE_ID,
        source_code="MFDS",
        endpoint_id=_ENDPOINT_ID,
        operation_id=_OPERATION_ID,
        source_snapshot_id=_SNAPSHOT_ID,
        source_version="external:v1",
        external_version="v1",
        canonical_checksum="b" * 64,
        canonicalization_spec_version="canonical-v1",
        endpoint_receipt_hash="c" * 64,
        verification_seal_id=None,
        verification_status=SnapshotVerificationStatus.PENDING,
        rejected_record_count=0,
        publication_verification_id=None,
    )
    async with factory() as session, session.begin():
        member = await SqlAlchemySourceSnapshotRepository(session).append_snapshot_member(
            SourceSnapshotMemberCreate(
                provenance=provenance,
                member_kind=SourceSnapshotMemberKind.ENDPOINT_OPERATION,
                endpoint_id=_ENDPOINT_ID,
                operation_id=_OPERATION_ID,
                ingestion_artifact_id=None,
                locator="$.records[0]",
                content_sha256=_CONTENT_HASH,
            )
        )
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot_verification "
                "(id, snapshot_id, check_name, verification_result, verified_by, verified_at) "
                "VALUES (:id, :snapshot_id, 'source-ingestion-integrity', 'PASSED', 'synthetic-reviewer', :now)"
            ),
            {"id": str(_VERIFICATION_ID), "snapshot_id": str(_SNAPSHOT_ID), "now": _NOW},
        )
        await connection.execute(
            text(
                "UPDATE rag_source_snapshot SET verification_status = 'CURRENT', verification_seal_id = :seal_id, "
                "verified_at = :now, effective_at = :now WHERE id = :snapshot_id"
            ),
            {"seal_id": str(_VERIFICATION_ID), "snapshot_id": str(_SNAPSHOT_ID), "now": _NOW},
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_document "
                "(id, title, publisher, source_url, document_version, document_status, record_contract_version, "
                "source_snapshot_member_id, external_document_id, document_content_hash, "
                "canonicalization_spec_version) "
                "VALUES (:id, 'Synthetic evidence', NULL, NULL, NULL, 'ACTIVE', 'KNOWLEDGE_EVIDENCE_V1', "
                ":member_id, 'document-1', :content_hash, 'canonical-v1')"
            ),
            {
                "id": str(_DOCUMENT_ID),
                "member_id": str(member.source_snapshot_member_id),
                "content_hash": _CONTENT_HASH,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_chunk "
                "(id, knowledge_document_id, chunk_index, chunk_text, embedding_model, vector_store_key, "
                "content_hash, normalization_version) "
                "VALUES (:id, :document_id, 0, :chunk_text, NULL, NULL, :content_hash, 'normalization-v1')"
            ),
            {
                "id": str(_CHUNK_ID),
                "document_id": str(_DOCUMENT_ID),
                "chunk_text": _TEXT_SENTINEL,
                "content_hash": _CONTENT_HASH,
            },
        )
    return member.source_snapshot_member_id


def _build_request(member_id: UUID, *, version: str, embedding: tuple[float, ...]) -> KnowledgeIndexBuildRequest:
    return KnowledgeIndexBuildRequest(
        index_code="knowledge-evidence",
        index_version=version,
        embedding_model_ref="synthetic-embedding",
        embedding_model_version="1.0.0",
        embedding_dimension=2,
        distance_metric=DistanceMetric.COSINE,
        members=(
            KnowledgeIndexMemberDraft(
                identity=KnowledgeChunkIdentity(
                    knowledge_chunk_id=_CHUNK_ID,
                    source_snapshot_id=_SNAPSHOT_ID,
                    source_snapshot_member_id=member_id,
                    source_code="MFDS",
                    source_version="external:v1",
                    canonical_checksum="b" * 64,
                    external_document_id="document-1",
                    chunk_index=0,
                    content_hash=_CONTENT_HASH,
                    locator="$.records[0]",
                ),
                content_text=SensitiveEvidenceText(_TEXT_SENTINEL),
                embedding=embedding,
            ),
        ),
    )


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "knowledge_evidence_178_" + uuid4().hex
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


async def test_real_postgresql_index_is_atomic_reproducible_and_non_leaking(database, caplog) -> None:
    engine = database
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")) == "0.8.6"
    member_id = await _seed_source_chain(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    repository = SqlAlchemyKnowledgeEvidenceIndexRepository(factory)
    initial = _build_request(member_id, version="v1", embedding=(1.0, 0.0))

    first = await repository.persist_complete_index(initial, create_knowledge_index_receipt(initial))
    replay = await build_knowledge_evidence_index(initial, repository=repository)
    assert replay == first

    concurrent = _build_request(member_id, version="v2", embedding=(0.5, 0.5))
    left, right = await asyncio.gather(
        build_knowledge_evidence_index(concurrent, repository=repository),
        build_knowledge_evidence_index(concurrent, repository=repository),
    )
    assert left == right

    conflict = replace(initial, members=(replace(initial.members[0], embedding=(0.0, 1.0)),))
    with pytest.raises(KnowledgeEvidenceIndexValidationError) as exc_info:
        await build_knowledge_evidence_index(conflict, repository=repository)
    assert exc_info.value.reason is KnowledgeEvidenceIndexFailureReason.VERSION_CONFLICT

    async with factory() as session:
        assert await session.scalar(select(_INDEX.c.id).where(_INDEX.c.index_version == "v1")) is not None
        assert await session.scalar(select(_INDEX.c.member_count).where(_INDEX.c.index_version == "v1")) == 1
        assert await session.scalar(select(_INDEX.c.member_count).where(_INDEX.c.index_version == "v2")) == 1
        stored_embedding = await session.scalar(select(_INDEX_MEMBER.c.embedding).limit(1))
        assert list(stored_embedding) in ([1.0, 0.0], [0.5, 0.5])
    assert _TEXT_SENTINEL not in caplog.text
    assert _TEXT_SENTINEL not in repr(first)
    assert _TEXT_SENTINEL not in repr(exc_info.value)

    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE rag_knowledge_index_member SET embedding = '[1,0,0]' WHERE knowledge_index_id = :id"),
            {"id": str(await connection.scalar(select(_INDEX.c.id).where(_INDEX.c.index_version == "v1")))},
        )
    with pytest.raises(KnowledgeEvidenceIndexValidationError) as dimension_error:
        await build_knowledge_evidence_index(initial, repository=repository)
    assert dimension_error.value.reason is KnowledgeEvidenceIndexFailureReason.EMBEDDING_INVALID


async def test_dedicated_builder_and_runtime_roles_enforce_the_index_boundary(database) -> None:
    member_id = await _seed_source_chain(database)
    suffix = uuid4().hex[:12]
    builder = f"index_builder_{suffix}"
    runtime = f"index_reader_{suffix}"
    password = "synthetic-index-role-only"
    builder_engine = create_async_engine(database.url.set(username=builder, password=password), hide_parameters=True)
    runtime_engine = create_async_engine(database.url.set(username=runtime, password=password), hide_parameters=True)
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

        repository = SqlAlchemyKnowledgeEvidenceIndexRepository(
            async_sessionmaker(builder_engine, expire_on_commit=False, autoflush=False)
        )
        request = _build_request(member_id, version="limited-role-v1", embedding=(1.0, 0.0))
        receipt = await build_knowledge_evidence_index(request, repository=repository)
        assert receipt.index_version == "limited-role-v1"

        async with runtime_engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM rag_knowledge_index")) == 1

        for engine, statement in (
            (builder_engine, "UPDATE rag_source SET lifecycle_status='REVOKED'"),
            (builder_engine, "DELETE FROM knowledge_chunk"),
            (builder_engine, "TRUNCATE rag_knowledge_index_member"),
            (builder_engine, "INSERT INTO rag_source DEFAULT VALUES"),
            (runtime_engine, "INSERT INTO rag_knowledge_index DEFAULT VALUES"),
            (runtime_engine, "UPDATE knowledge_document SET title='changed'"),
        ):
            with pytest.raises(DBAPIError) as error:
                async with engine.begin() as connection:
                    await connection.execute(text(statement))
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
    finally:
        await builder_engine.dispose()
        await runtime_engine.dispose()
        async with database.begin() as connection:
            await connection.execute(text(f'DROP OWNED BY "{builder}"'))
            await connection.execute(text(f'DROP OWNED BY "{runtime}"'))
            await connection.execute(text(f'DROP ROLE IF EXISTS "{builder}"'))
            await connection.execute(text(f'DROP ROLE IF EXISTS "{runtime}"'))


async def test_populated_foundation_refuses_lossy_downgrade(database) -> None:
    await _seed_source_chain(database)
    await database.dispose()

    with pytest.raises(RuntimeError, match="would lose Knowledge Evidence Index data"):
        await asyncio.to_thread(command.downgrade, _alembic_config(), "166f30415263")


async def test_legacy_knowledge_rows_round_trip_through_foundation_migration(database) -> None:
    await database.dispose()
    await asyncio.to_thread(command.downgrade, _alembic_config(), "166f30415263")
    async with database.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO knowledge_document "
                "(id, title, publisher, source_url, document_version, document_status) "
                "VALUES (:id, 'Legacy synthetic', 'Legacy publisher', 'https://example.invalid/legacy', "
                "'legacy-v1', 'ACTIVE')"
            ),
            {"id": str(_DOCUMENT_ID)},
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_chunk "
                "(id, knowledge_document_id, chunk_index, chunk_text, embedding_model, vector_store_key) "
                "VALUES (:id, :document_id, 0, 'Legacy synthetic text', 'legacy-model', 'legacy-vector-key')"
            ),
            {"id": str(_CHUNK_ID), "document_id": str(_DOCUMENT_ID)},
        )

    await database.dispose()
    await asyncio.to_thread(command.upgrade, _alembic_config(), "head")
    async with database.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT record_contract_version, publisher, source_url, document_version "
                    "FROM knowledge_document WHERE id = :id"
                ),
                {"id": str(_DOCUMENT_ID)},
            )
        ).one()
        chunk = (
            await connection.execute(
                text("SELECT content_hash, embedding_model, vector_store_key FROM knowledge_chunk WHERE id = :id"),
                {"id": str(_CHUNK_ID)},
            )
        ).one()
    assert tuple(row) == (
        "LEGACY_V1",
        "Legacy publisher",
        "https://example.invalid/legacy",
        "legacy-v1",
    )
    assert tuple(chunk) == (None, "legacy-model", "legacy-vector-key")

    await database.dispose()
    await asyncio.to_thread(command.downgrade, _alembic_config(), "166f30415263")


class StubPostgresEmbeddingPort(TextEmbeddingPort):
    def __init__(self) -> None:
        self.call_count = 0

    async def embed(
        self,
        text: SensitiveText,
        *,
        model_ref: str,
        model_version: str,
        dimension: int,
    ) -> TextEmbeddingSuccess:
        self.call_count += 1
        values = [float(self.call_count) / 100.0] * dimension
        return TextEmbeddingSuccess(
            embedding=SensitiveVector(values),
            adapter_artifact_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        )


NOVASC_CHUNK_IDS = {
    "EE": UUID("1f79b0e5-3d7b-488b-89ae-bf67e4314691"),
    "UD": UUID("a627c04a-8cc9-4b0a-b1f5-89e4fc2f165f"),
    "NB": UUID("f2ecb1c6-c02b-4486-8b76-da0bfd088d89"),
}


async def _seed_novasc_source_hierarchy(engine) -> None:
    async with engine.begin() as connection:
        source_id = uuid4()
        await connection.execute(
            text(
                "INSERT INTO rag_source "
                "(id, source_code, display_name, lifecycle_status, max_rejected_records, "
                "max_rejection_rate, empty_result_policy) "
                "VALUES (:id, 'MFDS_PRODUCT_LABEL', 'MFDS Product Label', 'ACTIVE', 0, 0, 'REJECT')"
            ),
            {"id": str(source_id)},
        )

        endpoint_id = uuid4()
        await connection.execute(
            text(
                "INSERT INTO rag_source_endpoint "
                "(id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, acquisition_status) "
                "VALUES (:id, :source_id, 'MFDS_NEDRUG_LABEL_XML', 'MFDS Nedrug Label XML', 'VERIFIED', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(endpoint_id), "source_id": str(source_id)},
        )

        operation_id = uuid4()
        await connection.execute(
            text(
                "INSERT INTO rag_source_operation "
                "(id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                "VALUES (:id, :endpoint_id, 'COLLECT_NOVASC_200610660_LABEL_XML', 'Novasc Label XML', 'ENABLED', 'APPROVED')"
            ),
            {"id": str(operation_id), "endpoint_id": str(endpoint_id)},
        )

        seal_id = uuid4()
        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot "
                "(id, operation_id, source_version, external_version, raw_manifest_checksum, canonical_checksum, "
                "schema_version, parser_version, normalization_version, canonicalization_spec_version, "
                "endpoint_receipt_hash, record_count, rejected_record_count, verification_status, collected_at) "
                "VALUES (:id, :operation_id, :source_version, 'v1', :raw_hash, :canonical_hash, 'schema-v1', "
                "'parser-v1', 'normalization-v1', 'canonical-v1', :receipt_hash, 3, 0, 'PENDING', :now)"
            ),
            {
                "id": str(NOVASC_SNAPSHOT_ID),
                "operation_id": str(operation_id),
                "source_version": NOVASC_SOURCE_VERSION,
                "raw_hash": "a" * 64,
                "canonical_hash": NOVASC_CANONICAL_CHECKSUM,
                "receipt_hash": "c" * 64,
                "now": _NOW,
            },
        )

        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot_verification "
                "(id, snapshot_id, check_name, verification_result, verified_by, verified_at) "
                "VALUES (:id, :snapshot_id, 'source-ingestion-integrity', 'PASSED', 'reviewer', :now)"
            ),
            {"id": str(seal_id), "snapshot_id": str(NOVASC_SNAPSHOT_ID), "now": _NOW},
        )

        await connection.execute(
            text(
                "UPDATE rag_source_snapshot SET verification_status = 'CURRENT', verification_seal_id = :seal_id, "
                "verified_at = :now, effective_at = :now WHERE id = :snapshot_id"
            ),
            {"seal_id": str(seal_id), "snapshot_id": str(NOVASC_SNAPSHOT_ID), "now": _NOW},
        )

        run_id = uuid4()
        await connection.execute(
            text(
                "INSERT INTO rag_source_ingestion_run "
                "(id, operation_id, run_group_key, snapshot_id, run_status, attempt_number, started_at, finished_at) "
                "VALUES (:id, :operation_id, 'novasc-group', :snapshot_id, 'SUCCEEDED', 1, :now, :now)"
            ),
            {
                "id": str(run_id),
                "operation_id": str(operation_id),
                "snapshot_id": str(NOVASC_SNAPSHOT_ID),
                "now": _NOW,
            },
        )

        texts = {
            "EE": "노바스크정5밀리그램 효능효과: 고혈압, 협심증",
            "UD": "노바스크정5밀리그램 용법용량: 1일 1회 5mg",
            "NB": "노바스크정5밀리그램 사용상의주의사항: 과민증 환자 금기",
        }

        for page_idx, section in enumerate(("EE", "UD", "NB"), start=1):
            member_id = uuid4()
            art_id = uuid4()
            doc_id = uuid4()
            chunk_id = NOVASC_CHUNK_IDS[section]
            chunk_text = texts[section]
            content_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
            xml_bytes = f"<section>{chunk_text}</section>".encode()
            xml_hash = hashlib.sha256(xml_bytes).hexdigest()

            await connection.execute(
                text(
                    "INSERT INTO rag_source_ingestion_artifact "
                    "(id, ingestion_run_id, storage_backend, page_number, artifact_key, "
                    "object_key, raw_checksum, byte_size, content_type) "
                    "VALUES (:id, :run_id, 'LOCAL_PRIVATE', :page_number, :art_key, :obj_key, :checksum, :size, 'application/xml')"
                ),
                {
                    "id": str(art_id),
                    "run_id": str(run_id),
                    "page_number": page_idx,
                    "art_key": f"mfds-label/{NOVASC_ITEM_SEQ}/{section}.xml",
                    "obj_key": f"obj_{section}",
                    "checksum": xml_hash,
                    "size": len(xml_bytes),
                },
            )

            await connection.execute(
                text(
                    "INSERT INTO rag_source_snapshot_member "
                    "(id, source_snapshot_id, member_kind, endpoint_id, operation_id, ingestion_artifact_id, "
                    "locator, content_sha256) "
                    "VALUES (:id, :snapshot_id, 'ARTIFACT', NULL, NULL, :artifact_id, :locator, :sha)"
                ),
                {
                    "id": str(member_id),
                    "snapshot_id": str(NOVASC_SNAPSHOT_ID),
                    "artifact_id": str(art_id),
                    "locator": f"mfds-label/{NOVASC_ITEM_SEQ}/{section}",
                    "sha": content_hash,
                },
            )

            await connection.execute(
                text(
                    "INSERT INTO knowledge_document "
                    "(id, title, publisher, source_url, document_version, document_status, record_contract_version, "
                    "source_snapshot_member_id, external_document_id, document_content_hash, "
                    "canonicalization_spec_version) "
                    "VALUES (:id, :title, 'MFDS', NULL, NULL, 'ACTIVE', 'KNOWLEDGE_EVIDENCE_V1', "
                    ":member_id, :ext_doc_id, :content_hash, 'canonical-v1')"
                ),
                {
                    "id": str(doc_id),
                    "title": f"Novasc {section}",
                    "member_id": str(member_id),
                    "ext_doc_id": f"mfds-label:{NOVASC_ITEM_SEQ}:{section}",
                    "content_hash": content_hash,
                },
            )

            await connection.execute(
                text(
                    "INSERT INTO knowledge_chunk "
                    "(id, knowledge_document_id, chunk_index, chunk_text, embedding_model, vector_store_key, "
                    "content_hash, normalization_version) "
                    "VALUES (:id, :doc_id, 0, :chunk_text, NULL, NULL, :content_hash, 'mfds-label-knowledge-chunk@1')"
                ),
                {
                    "id": str(chunk_id),
                    "doc_id": str(doc_id),
                    "chunk_text": chunk_text,
                    "content_hash": content_hash,
                },
            )


async def test_admin_runner_novasc_postgresql_integration(database) -> None:
    await _seed_novasc_source_hierarchy(database)

    suffix = uuid4().hex[:12]
    builder = f"idx_bld_{suffix}"
    runtime = f"idx_rt_{suffix}"
    password = "synthetic-runner-password"

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
    runner_config = KnowledgeEvidenceIndexRunnerConfig(
        url=builder_url,
        builder_user=builder,
        openai_api_key="synthetic-test-key",
    )

    port = StubPostgresEmbeddingPort()

    try:
        # 1. Fresh build execution
        summary = await execute_knowledge_evidence_index_build(
            config=runner_config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            expected_source_version=NOVASC_SOURCE_VERSION,
            expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
            verify_replay=True,
            embedding_port_override=port,
        )

        assert summary["execution_status"] == "SUCCESS"
        assert summary["outcome"] == "BUILT"
        assert summary["index_code"] == EXPECTED_INDEX_CODE
        assert summary["index_version"] == EXPECTED_INDEX_VERSION
        assert summary["member_count"] == 3
        assert summary["embedding_dimension"] == EXPECTED_DIMENSION
        assert summary["post_persist_readback_passed"] is True
        assert summary["exact_replay_verified"] is True
        assert summary["provider_call_count"] == 3
        assert port.call_count == 3

        async with database.connect() as connection:
            idx_count = await connection.scalar(text("SELECT count(*) FROM rag_knowledge_index"))
            member_count = await connection.scalar(text("SELECT count(*) FROM rag_knowledge_index_member"))
            first_index_id = await connection.scalar(text("SELECT id FROM rag_knowledge_index"))
            first_member_ids = set(await connection.scalars(text("SELECT id FROM rag_knowledge_index_member")))
            assert idx_count == 1
            assert member_count == 3

        # 2. Existing Index reuse (EXACT_REUSE)
        reuse_summary = await execute_knowledge_evidence_index_build(
            config=runner_config,
            snapshot_id=NOVASC_SNAPSHOT_ID,
            expected_item_seq=NOVASC_ITEM_SEQ,
            expected_canonical_checksum=NOVASC_CANONICAL_CHECKSUM,
            expected_source_version=NOVASC_SOURCE_VERSION,
            verify_replay=False,
            embedding_port_override=port,
        )

        assert reuse_summary["execution_status"] == "SUCCESS"
        assert reuse_summary["outcome"] == "EXACT_REUSE"
        assert reuse_summary["provider_call_count"] == 0
        # Port should not have been called for EXACT_REUSE
        assert port.call_count == 3

        async with database.connect() as connection:
            idx_count = await connection.scalar(text("SELECT count(*) FROM rag_knowledge_index"))
            member_count = await connection.scalar(text("SELECT count(*) FROM rag_knowledge_index_member"))
            second_index_id = await connection.scalar(text("SELECT id FROM rag_knowledge_index"))
            second_member_ids = set(await connection.scalars(text("SELECT id FROM rag_knowledge_index_member")))
            assert idx_count == 1
            assert member_count == 3
            assert second_index_id == first_index_id
            assert second_member_ids == first_member_ids

    finally:
        async with database.begin() as connection:
            await connection.execute(text(f'DROP OWNED BY "{builder}"'))
            await connection.execute(text(f'DROP OWNED BY "{runtime}"'))
            await connection.execute(text(f'DROP ROLE IF EXISTS "{builder}"'))
            await connection.execute(text(f'DROP ROLE IF EXISTS "{runtime}"'))
