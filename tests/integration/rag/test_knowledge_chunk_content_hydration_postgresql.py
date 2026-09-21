"""Real PostgreSQL integration tests for KnowledgeChunk content hydration (#711).

Exercises the read-only SQLAlchemy reader and the hydration kernel against
actually persisted Source -> Snapshot -> Member -> Document -> Chunk ->
Knowledge Index membership rows, including tampering fixtures that must
fail closed.
"""

import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_knowledge_chunk_content import SqlAlchemyKnowledgeChunkContentReader
from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    SqlAlchemyKnowledgeEvidenceIndexRepository,
)
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.evidence_search import (
    FractionReceipt,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    StableCoordinate,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    ObservedDecisionOutcome,
    RequestDecisionStage,
    RequestSourceMemberBinding,
)
from ai_worker.tasks.rag.guide_retrieval_composition import AuthenticatedGuideRetrievalSelection
from ai_worker.tasks.rag.knowledge_chunk_content_hydration import (
    GuideContentHydrationDecision,
    GuideContentHydrationReason,
    hydrate_guide_retrieval_content,
)
from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeChunkIdentity,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexMemberDraft,
    SensitiveEvidenceText,
    build_knowledge_evidence_index,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotProvenanceReceipt,
    SnapshotVerificationStatus,
    SourceSnapshotMemberCreate,
    SourceSnapshotMemberKind,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberKind
from app.core import config

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio

_SOURCE_ID = UUID("71100000-0000-4000-8000-000000000011")
_ENDPOINT_ID = UUID("71100000-0000-4000-8000-000000000012")
_OPERATION_ID = UUID("71100000-0000-4000-8000-000000000013")
_SNAPSHOT_ID = UUID("71100000-0000-4000-8000-000000000014")
_VERIFICATION_ID = UUID("71100000-0000-4000-8000-000000000015")
_DOCUMENT_ID = UUID("71100000-0000-4000-8000-000000000016")

_CHUNK_FIRST_ID = UUID("71100000-0000-4000-8000-000000000021")
_CHUNK_SECOND_ID = UUID("71100000-0000-4000-8000-000000000022")

_TEXT_FIRST = "아스피린 장용정 100mg 복용 안내"
_TEXT_SECOND = "해열 소염 진통제 안내서 아스피린 포함"

_INDEX_CODE = "hydration-test-index"
_INDEX_VERSION = "1.0.0"
_LOCATOR = "$.records[0]"
_EXTERNAL_DOCUMENT_ID = "doc-1"
_CANONICAL_CHECKSUM = "b" * 64
_NOW = datetime(2026, 9, 17, 1, 0, tzinfo=UTC)


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "knowledge_hydration_711_" + uuid4().hex
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


async def _seed(engine) -> tuple[UUID, UUID, str]:
    """Seed Source -> Snapshot -> Member -> Document -> 2 Chunks and build the Index."""
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
                "'parser-v1', 'normalization-v1', 'canonical-v1', :receipt_hash, 2, 0, 'PENDING', :now)"
            ),
            {
                "id": str(_SNAPSHOT_ID),
                "operation_id": str(_OPERATION_ID),
                "raw_hash": "a" * 64,
                "canonical_hash": _CANONICAL_CHECKSUM,
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
        canonical_checksum=_CANONICAL_CHECKSUM,
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
                locator=_LOCATOR,
                content_sha256="d" * 64,
            )
        )
    member_id = member.source_snapshot_member_id

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
                ":member_id, :external_document_id, :doc_hash, 'canonical-v1')"
            ),
            {
                "id": str(_DOCUMENT_ID),
                "member_id": str(member_id),
                "external_document_id": _EXTERNAL_DOCUMENT_ID,
                "doc_hash": "d" * 64,
            },
        )
        for chunk_id, index, body in (
            (_CHUNK_FIRST_ID, 0, _TEXT_FIRST),
            (_CHUNK_SECOND_ID, 1, _TEXT_SECOND),
        ):
            await connection.execute(
                text(
                    "INSERT INTO knowledge_chunk "
                    "(id, knowledge_document_id, chunk_index, chunk_text, embedding_model, vector_store_key, "
                    "content_hash, normalization_version) "
                    "VALUES (:id, :document_id, :idx, :chunk_text, NULL, NULL, :content_hash, 'normalization-v1')"
                ),
                {
                    "id": str(chunk_id),
                    "document_id": str(_DOCUMENT_ID),
                    "idx": index,
                    "chunk_text": body,
                    "content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                },
            )

    drafts = tuple(
        KnowledgeIndexMemberDraft(
            identity=KnowledgeChunkIdentity(
                knowledge_chunk_id=chunk_id,
                evidence_key=f"synthetic-hydration-evidence-{index + 1}",
                source_snapshot_id=_SNAPSHOT_ID,
                source_snapshot_member_id=member_id,
                source_code="MFDS",
                source_version="external:v1",
                canonical_checksum=_CANONICAL_CHECKSUM,
                external_document_id=_EXTERNAL_DOCUMENT_ID,
                chunk_index=index,
                content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                locator=_LOCATOR,
            ),
            content_text=SensitiveEvidenceText(body),
            embedding=embedding,
        )
        for chunk_id, index, body, embedding in (
            (_CHUNK_FIRST_ID, 0, _TEXT_FIRST, (1.0, 0.0)),
            (_CHUNK_SECOND_ID, 1, _TEXT_SECOND, (0.0, 1.0)),
        )
    )
    receipt = await build_knowledge_evidence_index(
        KnowledgeIndexBuildRequest(
            index_code=_INDEX_CODE,
            index_version=_INDEX_VERSION,
            embedding_model_ref="test-embedding-model",
            embedding_model_version="1.0",
            embedding_dimension=2,
            distance_metric=DistanceMetric.COSINE,
            members=drafts,
        ),
        repository=SqlAlchemyKnowledgeEvidenceIndexRepository(factory),
    )

    async with factory() as session:
        index_id = await session.scalar(
            text("SELECT id FROM rag_knowledge_index WHERE index_code = :code AND index_version = :version"),
            {"code": _INDEX_CODE, "version": _INDEX_VERSION},
        )
    return UUID(str(index_id)), member_id, receipt.index_configuration_hash


def _selection(
    *,
    index_id: UUID,
    member_id: UUID,
    index_hash: str,
    chunk_id: UUID,
    chunk_index: int,
    body: str,
) -> AuthenticatedGuideRetrievalSelection:
    """Build the #703 selection production retrieval would have produced for a chunk."""
    provenance = ProductionEvidenceProvenance(
        knowledge_index_id=index_id,
        index_code=_INDEX_CODE,
        index_version=_INDEX_VERSION,
        index_configuration_hash=index_hash,
        knowledge_chunk_id=chunk_id,
        evidence_key=f"synthetic-hydration-evidence-{chunk_index + 1}",
        source_snapshot_id=_SNAPSHOT_ID,
        source_snapshot_member_id=member_id,
        source_code="MFDS",
        source_version="external:v1",
        canonical_checksum=_CANONICAL_CHECKSUM,
        external_document_id=_EXTERNAL_DOCUMENT_ID,
        chunk_index=chunk_index,
        locator=_LOCATOR,
        content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        canonicalization_spec_version="canonical-v1",
        normalization_version="normalization-v1",
    )
    hit = ProductionSearchHit(
        provenance=provenance,
        coordinate=StableCoordinate(
            source_code="MFDS",
            source_version="external:v1",
            external_document_id=_EXTERNAL_DOCUMENT_ID,
            chunk_index=chunk_index,
        ),
        exact_hit=True,
        observed_trigram_score=None,
        observed_fts_score=None,
        observed_dense_score=None,
        lexical_rank=chunk_index + 1,
        dense_rank=None,
        fusion_rank=chunk_index + 1,
        fraction_receipt=FractionReceipt("1", "60"),
        is_eligible_for_future_reranker=True,
    )
    artifact = ImmutableArtifactRef("authority", "1.0", "a" * 64)
    binding = RequestSourceMemberBinding(
        request_guard_ref=artifact,
        request_operation_code="GUIDE_GENERATE",
        source_snapshot_id=_SNAPSHOT_ID,
        source_snapshot_member_id=member_id,
        source_code="MFDS",
        source_version="external:v1",
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        request_source_decision_ref=artifact,
        request_member_decision_ref=artifact,
        observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
        observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
        request_decision_stage=RequestDecisionStage.REQUEST,
        endpoint_code="PRODUCTS",
        operation_code="LIST",
    )
    return AuthenticatedGuideRetrievalSelection(hit=hit, binding=binding)


async def _both_selections(engine) -> tuple[tuple[AuthenticatedGuideRetrievalSelection, ...], UUID, UUID]:
    index_id, member_id, index_hash = await _seed(engine)
    selections = tuple(
        _selection(
            index_id=index_id,
            member_id=member_id,
            index_hash=index_hash,
            chunk_id=chunk_id,
            chunk_index=chunk_index,
            body=body,
        )
        for chunk_id, chunk_index, body in (
            (_CHUNK_FIRST_ID, 0, _TEXT_FIRST),
            (_CHUNK_SECOND_ID, 1, _TEXT_SECOND),
        )
    )
    return selections, index_id, member_id


async def test_actual_persisted_chunks_hydrate_with_exact_content(database) -> None:
    engine = database
    selections, _, _ = await _both_selections(engine)
    reader = SqlAlchemyKnowledgeChunkContentReader(async_sessionmaker(engine, expire_on_commit=False))

    outcome = await hydrate_guide_retrieval_content(selections, reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.HYDRATED
    assert outcome.reasons == ()
    assert tuple(h.selection for h in outcome.selections) == selections
    assert [h.content_text.reveal() for h in outcome.selections] == [_TEXT_FIRST, _TEXT_SECOND]


async def test_wrong_knowledge_index_id_is_not_found(database) -> None:
    engine = database
    selections, _, _ = await _both_selections(engine)
    selection = selections[0]
    wrong = replace(
        selection,
        hit=replace(
            selection.hit,
            provenance=replace(selection.hit.provenance, knowledge_index_id=uuid4()),
        ),
    )
    reader = SqlAlchemyKnowledgeChunkContentReader(async_sessionmaker(engine, expire_on_commit=False))

    outcome = await hydrate_guide_retrieval_content((wrong,), reader=reader)

    assert outcome.reasons == (GuideContentHydrationReason.CONTENT_NOT_FOUND,)
    assert outcome.selections == ()


async def test_wrong_knowledge_chunk_id_is_not_found(database) -> None:
    engine = database
    selections, _, _ = await _both_selections(engine)
    selection = selections[0]
    wrong = replace(
        selection,
        hit=replace(
            selection.hit,
            provenance=replace(selection.hit.provenance, knowledge_chunk_id=uuid4()),
        ),
    )
    reader = SqlAlchemyKnowledgeChunkContentReader(async_sessionmaker(engine, expire_on_commit=False))

    outcome = await hydrate_guide_retrieval_content((wrong,), reader=reader)

    assert outcome.reasons == (GuideContentHydrationReason.CONTENT_NOT_FOUND,)


async def test_tampered_persisted_chunk_text_fails_closed(database) -> None:
    engine = database
    selections, _, _ = await _both_selections(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE knowledge_chunk SET chunk_text = :body WHERE id = :id"),
            {"body": _TEXT_FIRST + " 변조", "id": str(_CHUNK_FIRST_ID)},
        )
    reader = SqlAlchemyKnowledgeChunkContentReader(async_sessionmaker(engine, expire_on_commit=False))

    outcome = await hydrate_guide_retrieval_content(selections, reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.REJECTED
    assert outcome.reasons == (GuideContentHydrationReason.CONTENT_HASH_MISMATCH,)
    assert outcome.selections == ()


async def test_tampered_persisted_index_member_provenance_is_provenance_mismatch(database) -> None:
    engine = database
    selections, index_id, _ = await _both_selections(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE rag_knowledge_index_member SET canonical_checksum = :checksum "
                "WHERE knowledge_index_id = :index_id AND knowledge_chunk_id = :chunk_id"
            ),
            {"checksum": "f" * 64, "index_id": str(index_id), "chunk_id": str(_CHUNK_FIRST_ID)},
        )
    reader = SqlAlchemyKnowledgeChunkContentReader(async_sessionmaker(engine, expire_on_commit=False))

    outcome = await hydrate_guide_retrieval_content(selections, reader=reader)

    assert outcome.reasons == (GuideContentHydrationReason.PROVENANCE_MISMATCH,)
    assert outcome.selections == ()


async def test_tampered_persisted_document_provenance_is_provenance_mismatch(database) -> None:
    engine = database
    selections, _, _ = await _both_selections(engine)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE knowledge_document SET canonicalization_spec_version = 'canonical-v2' WHERE id = :id"),
            {"id": str(_DOCUMENT_ID)},
        )
    reader = SqlAlchemyKnowledgeChunkContentReader(async_sessionmaker(engine, expire_on_commit=False))

    outcome = await hydrate_guide_retrieval_content(selections, reader=reader)

    assert outcome.reasons == (GuideContentHydrationReason.PROVENANCE_MISMATCH,)


async def test_lookup_identity_cannot_become_ambiguous_in_storage(database) -> None:
    """`uq_rag_knowledge_index_member_chunk` makes the lookup identity at most one row.

    The adapter still refuses 2+ rows rather than picking one; that guard is
    defense-in-depth for this constraint, and is covered at the adapter unit level.
    """
    engine = database
    _, index_id, _ = await _both_selections(engine)

    with pytest.raises(IntegrityError):
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO rag_knowledge_index_member "
                    "(id, knowledge_index_id, knowledge_chunk_id, evidence_key, source_snapshot_id, "
                    "source_snapshot_member_id, source_code, source_version, canonical_checksum, "
                    "external_document_id, chunk_index, content_hash, embedding, embedding_sha256, "
                    "member_order) "
                    "SELECT :new_id, knowledge_index_id, knowledge_chunk_id, evidence_key, source_snapshot_id, "
                    "source_snapshot_member_id, source_code, source_version, canonical_checksum, "
                    "external_document_id, chunk_index, content_hash, embedding, embedding_sha256, 99 "
                    "FROM rag_knowledge_index_member "
                    "WHERE knowledge_index_id = :index_id AND knowledge_chunk_id = :chunk_id"
                ),
                {"new_id": str(uuid4()), "index_id": str(index_id), "chunk_id": str(_CHUNK_FIRST_ID)},
            )

    reader = SqlAlchemyKnowledgeChunkContentReader(async_sessionmaker(engine, expire_on_commit=False))
    assert await reader.read_content(knowledge_index_id=index_id, knowledge_chunk_id=_CHUNK_FIRST_ID) is not None


async def test_reader_transaction_is_repeatable_read_and_read_only(database) -> None:
    engine = database
    _, index_id, _ = await _both_selections(engine)
    raw_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    observed: list[tuple[str, str]] = []

    class ObservingSession:
        def __init__(self, delegate):
            self._delegate = delegate

        async def execute(self, statement, *args, **kwargs):
            result = await self._delegate.execute(statement, *args, **kwargs)
            if not observed and "SET TRANSACTION" not in str(statement):
                isolation = await self._delegate.scalar(text("SHOW transaction_isolation"))
                read_only = await self._delegate.scalar(text("SHOW transaction_read_only"))
                observed.append((str(isolation), str(read_only)))
            return result

        def begin(self):
            return self._delegate.begin()

        def __getattr__(self, name):
            return getattr(self._delegate, name)

    @asynccontextmanager
    async def observing_factory():
        async with raw_factory() as session:
            yield ObservingSession(session)

    reader = SqlAlchemyKnowledgeChunkContentReader(observing_factory)
    assert await reader.read_content(knowledge_index_id=index_id, knowledge_chunk_id=_CHUNK_FIRST_ID) is not None

    assert len(observed) == 1
    isolation, read_only = observed[0]
    assert isolation.lower() == "repeatable read"
    assert read_only.lower() == "on"


async def test_hydration_writes_nothing_to_the_database(database) -> None:
    engine = database
    selections, _, _ = await _both_selections(engine)

    async def _counts() -> dict[str, int]:
        async with engine.connect() as connection:
            return {
                table_name: int(await connection.scalar(text(f"SELECT count(*) FROM {table_name}")) or 0)
                for table_name in (
                    "knowledge_chunk",
                    "knowledge_document",
                    "rag_knowledge_index",
                    "rag_knowledge_index_member",
                    "rag_source_snapshot_member",
                )
            }

    before = await _counts()
    async with engine.connect() as connection:
        bodies_before = list(
            await connection.scalars(text("SELECT chunk_text FROM knowledge_chunk ORDER BY chunk_index"))
        )

    reader = SqlAlchemyKnowledgeChunkContentReader(async_sessionmaker(engine, expire_on_commit=False))
    outcome = await hydrate_guide_retrieval_content(selections, reader=reader)
    assert outcome.decision is GuideContentHydrationDecision.HYDRATED

    assert await _counts() == before
    async with engine.connect() as connection:
        bodies_after = list(
            await connection.scalars(text("SELECT chunk_text FROM knowledge_chunk ORDER BY chunk_index"))
        )
    assert bodies_after == bodies_before


async def test_raw_chunk_body_is_not_logged_during_hydration(database, caplog) -> None:
    engine = database
    selections, _, _ = await _both_selections(engine)
    reader = SqlAlchemyKnowledgeChunkContentReader(async_sessionmaker(engine, expire_on_commit=False))

    with caplog.at_level(logging.DEBUG, logger="ai_worker"):
        outcome = await hydrate_guide_retrieval_content(selections, reader=reader)

    assert outcome.decision is GuideContentHydrationDecision.HYDRATED
    assert _TEXT_FIRST not in caplog.text
    assert _TEXT_SECOND not in caplog.text
    assert _TEXT_FIRST not in repr(outcome)
