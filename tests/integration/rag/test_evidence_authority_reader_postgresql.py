"""Real PostgreSQL integration tests for the production Assessment·Eligibility Authority Reader (#746).

#712 issuer가 실제 PostgreSQL에 남긴 immutable authority를 commit·session close 이후
새 production Reader session이 exact lookup으로 다시 읽어 같은 사실을 복원하는지 검증한다.
synthetic in-memory fixture만으로는 이 경계를 증명할 수 없다.

production ai_worker 코드는 backend를 import하지 않는다. 여기서 #712 Repository를 쓰는 것은
테스트가 writer와 reader 양쪽 계약을 맞대기 위해서다.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_evidence_authority import (
    SqlAlchemyAssessmentEligibilityAuthorityReader,
)
from ai_worker.tasks.rag.assessment_eligibility_authority import (
    AssessmentEligibilityAuthorityReaderError,
)
from ai_worker.tasks.rag.evidence_authority_issuer import issue_assessment_eligibility_authority
from app.core import config
from app.repositories.rag_evidence_authority_repository import RagEvidenceAuthorityRepository
from rag_runtime.evidence_authority import (
    EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS,
    ImmutableArtifactRef,
    IssueAssessmentAuthorityRequest,
    PersistedEvidenceAuthority,
)

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.asyncio

_SOURCE_CODE = "MFDS"
_SOURCE_VERSION = "2026.09.17"
_CONTENT_SHA256 = "d" * 64
_CANONICAL_CHECKSUM = "c" * 64
_EMBEDDING_SHA256 = "e" * 64

_EVALUATED_AT = datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)
_MAX_VALIDITY = timedelta(seconds=EVIDENCE_ASSESSMENT_MAX_VALIDITY_DURATION_SECONDS)


def _alembic_config() -> Config:
    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(ROOT / "backend/alembic"))
    return alembic_config


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "evidence_authority_746_" + uuid4().hex
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


async def _seed_selected_hit(engine) -> dict[str, UUID]:
    """#712 authority가 결속할 selected hit 한 건과 그 Source/Index provenance를 실제로 적재한다."""
    ids = {
        key: uuid4()
        for key in (
            "user",
            "job",
            "index",
            "index_member",
            "document",
            "chunk",
            "source",
            "endpoint",
            "operation",
            "snapshot",
            "member",
            "run",
        )
    }
    suffix = uuid4().hex[:10]

    async with engine.begin() as connection:
        await connection.execute(
            text(
                'INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin) '
                "VALUES (:id, :email, 'hash', 'reader746', true, false)"
            ),
            {"id": str(ids["user"]), "email": f"reader746-{suffix}@example.com"},
        )
        await connection.execute(
            text(
                "INSERT INTO ai_job (id, user_id, job_type, status, max_attempts, attempt_count) "
                "VALUES (:id, :uid, 'OCR', 'PENDING', 3, 0)"
            ),
            {"id": str(ids["job"]), "uid": str(ids["user"])},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status) "
                "VALUES (:id, :code, 'MFDS Evidence Source', 'DRAFT')"
            ),
            {"id": str(ids["source"]), "code": f"MFDS_R746_{suffix}"},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_endpoint "
                "(id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, acquisition_status) "
                "VALUES (:id, :source_id, 'PRODUCT_LIST', 'Product List', 'DRAFT', 'DISABLED', 'PENDING')"
            ),
            {"id": str(ids["endpoint"]), "source_id": str(ids["source"])},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_operation "
                "(id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                "VALUES (:id, :endpoint_id, 'LIST_PRODUCTS', 'List Products', 'DISABLED', 'PENDING')"
            ),
            {"id": str(ids["operation"]), "endpoint_id": str(ids["endpoint"])},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot "
                "(id, operation_id, source_version, raw_manifest_checksum, canonical_checksum, schema_version, "
                "parser_version, normalization_version, canonicalization_spec_version, record_count, "
                "rejected_record_count, verification_status, collected_at) "
                "VALUES (:id, :operation_id, :version, :h, :h, 'schema-v1', 'parser-v1', 'normalization-v1', "
                "'canonical-v1', 1, 0, 'PENDING', NOW())"
            ),
            {
                "id": str(ids["snapshot"]),
                "operation_id": str(ids["operation"]),
                "version": _SOURCE_VERSION,
                "h": "a" * 64,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO rag_source_snapshot_member "
                "(id, source_snapshot_id, member_kind, endpoint_id, operation_id, locator, content_sha256) "
                "VALUES (:id, :snapshot_id, 'ENDPOINT_OPERATION', :endpoint_id, :operation_id, :locator, :h)"
            ),
            {
                "id": str(ids["member"]),
                "snapshot_id": str(ids["snapshot"]),
                "endpoint_id": str(ids["endpoint"]),
                "operation_id": str(ids["operation"]),
                "locator": f"product/{suffix}",
                "h": _CONTENT_SHA256,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO rag_knowledge_index (id, index_code, index_version, corpus_manifest_hash, "
                "embedding_manifest_hash, index_configuration_hash, embedding_model_ref, "
                "embedding_model_version, embedding_dimension, distance_metric, member_count) "
                "VALUES (:id, :code, '1.0', :h, :h, :h, 'text-embedding-3-large', '1.0', 2, 'COSINE', 1)"
            ),
            {"id": str(ids["index"]), "code": f"R746_IDX_{suffix}", "h": "a" * 64},
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_document (id, title, source_url, document_version, document_status, "
                "record_contract_version, publisher) "
                "VALUES (:id, 'Reader Doc', :url, '1.0', 'ACTIVE', 'LEGACY_V1', 'MFDS')"
            ),
            {"id": str(ids["document"]), "url": f"https://example.com/r746-{suffix}"},
        )
        await connection.execute(
            text(
                "INSERT INTO knowledge_chunk (id, knowledge_document_id, chunk_index, chunk_text, "
                "content_hash, normalization_version) "
                "VALUES (:id, :doc_id, 0, '아스피린 복용 안내', :h, 'v1')"
            ),
            {"id": str(ids["chunk"]), "doc_id": str(ids["document"]), "h": _CONTENT_SHA256},
        )
        await connection.execute(
            text(
                "INSERT INTO rag_knowledge_index_member "
                "(id, knowledge_index_id, knowledge_chunk_id, evidence_key, source_snapshot_id, source_snapshot_member_id, "
                "source_code, source_version, canonical_checksum, external_document_id, chunk_index, "
                "content_hash, embedding, embedding_sha256, member_order) "
                "VALUES (:id, :index_id, :chunk_id, :evidence_key, :snapshot_id, :member_id, :source_code, :source_version, "
                ":canonical, :external_id, 0, :h, '[1,0]', :embedding_sha256, 1)"
            ),
            {
                "id": str(ids["index_member"]),
                "index_id": str(ids["index"]),
                "chunk_id": str(ids["chunk"]),
                "evidence_key": "synthetic-r746-evidence-1",
                "snapshot_id": str(ids["snapshot"]),
                "member_id": str(ids["member"]),
                "source_code": _SOURCE_CODE,
                "source_version": _SOURCE_VERSION,
                "canonical": _CANONICAL_CHECKSUM,
                "external_id": f"external-{suffix}",
                "h": _CONTENT_SHA256,
                "embedding_sha256": _EMBEDDING_SHA256,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO retrieval_run ("
                "id, job_id, execution_context_id, prescription_version_id, "
                "runtime_release_bundle_id, runtime_release_bundle_manifest_hash, "
                "runtime_execution_manifest_id, runtime_execution_manifest_hash, "
                "runtime_guard_decision_ref, knowledge_index_id, node_id, variant, "
                "query_digest_algorithm, query_digest_key_version, query_digest, "
                "filter_snapshot, filter_snapshot_hash, source_manifest_hash, "
                "retrieval_configuration_hash, lexical_limit, dense_limit, hybrid_limit, final_k, "
                "status, diagnostic_code, search_receipt_hash, receipt_hash, started_at, completed_at"
                ") VALUES ("
                ":id, :job_id, :ctx, :pv, :bundle, :h3, :manifest, :h4, 'ref-746', :index_id, :node, 'RET-H', "
                "'sha256', 'v1', :h1, :filter_snapshot, :h5, :h6, :h2, 20, 20, 30, 5, "
                "'COMPLETED', 'OK', :h7, :h8, NOW(), NOW())"
            ),
            {
                "id": str(ids["run"]),
                "job_id": str(ids["job"]),
                "ctx": str(uuid4()),
                "pv": str(uuid4()),
                "bundle": str(uuid4()),
                "manifest": str(uuid4()),
                "index_id": str(ids["index"]),
                "node": f"hybrid_retrieve_{suffix[:6]}",
                "filter_snapshot": json.dumps({"code": "ASPIRIN"}),
                "h1": "1" * 64,
                "h2": "2" * 64,
                "h3": "3" * 64,
                "h4": "4" * 64,
                "h5": "5" * 64,
                "h6": "6" * 64,
                "h7": "7" * 64,
                "h8": "8" * 64,
            },
        )
        await connection.execute(
            text(
                "INSERT INTO retrieval_hit ("
                "retrieval_run_id, knowledge_chunk_id, lexical_rank, dense_rank, rrf_rank, "
                "rrf_score, rrf_score_numerator, rrf_score_denominator, final_rank, selected"
                ") VALUES (:run_id, :chunk_id, 1, NULL, 1, 0.016393442622950820, '1', '61', 1, true)"
            ),
            {"run_id": str(ids["run"]), "chunk_id": str(ids["chunk"])},
        )
    return ids


async def _issue_authority(engine, ids: dict[str, UUID]) -> PersistedEvidenceAuthority:
    """#712 issuer로 authority를 실제로 발급하고 commit한 뒤 session을 닫는다."""
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        issued = await issue_assessment_eligibility_authority(
            IssueAssessmentAuthorityRequest(
                retrieval_run_id=ids["run"],
                knowledge_chunk_id=ids["chunk"],
                source_snapshot_id=ids["snapshot"],
                source_snapshot_member_id=ids["member"],
                source_code=_SOURCE_CODE,
                source_version=_SOURCE_VERSION,
                content_sha256=_CONTENT_SHA256,
                evaluated_at=_EVALUATED_AT,
            ),
            RagEvidenceAuthorityRepository(session),
        )
        await session.commit()
    return issued


def _reader(engine) -> SqlAlchemyAssessmentEligibilityAuthorityReader:
    """실제 조회는 쓰기와 완전히 분리된 새 session factory에서 수행한다."""
    return SqlAlchemyAssessmentEligibilityAuthorityReader(async_sessionmaker(engine, expire_on_commit=False))


async def _corrupt(engine, column: str, value: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(f"UPDATE rag_evidence_authority SET {column} = :value"), {"value": value})


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


async def test_issued_authority_round_trips_through_a_fresh_reader_session(database) -> None:
    ids = await _seed_selected_hit(database)
    issued = await _issue_authority(database, ids)

    reader = _reader(database)

    by_selection = await reader.read_by_selection(
        retrieval_run_id=ids["run"],
        knowledge_chunk_id=ids["chunk"],
    )
    assert by_selection == issued

    by_assessment = await reader.read_by_assessment_ref(
        assessment_artifact_ref=issued.assessment_artifact_ref,
    )
    assert by_assessment == issued


async def test_round_trip_preserves_the_four_artifact_refs_and_validity_window(database) -> None:
    ids = await _seed_selected_hit(database)
    issued = await _issue_authority(database, ids)

    authority = await _reader(database).read_by_selection(
        retrieval_run_id=ids["run"],
        knowledge_chunk_id=ids["chunk"],
    )

    assert authority is not None
    for persisted, expected in (
        (authority.eligibility_receipt_ref, issued.eligibility_receipt_ref),
        (authority.assessment_artifact_ref, issued.assessment_artifact_ref),
        (authority.verifier_artifact_ref, issued.verifier_artifact_ref),
        (authority.validity_policy_ref, issued.validity_policy_ref),
    ):
        assert isinstance(persisted, ImmutableArtifactRef)
        assert persisted == expected

    assert authority.evaluated_at == _EVALUATED_AT
    assert authority.assessment_valid_from == _EVALUATED_AT
    assert authority.assessment_valid_until == _EVALUATED_AT + _MAX_VALIDITY
    assert authority.assessment_valid_until.tzinfo is not None


# ---------------------------------------------------------------------------
# Normal no-row
# ---------------------------------------------------------------------------


async def test_unknown_selection_returns_none(database) -> None:
    ids = await _seed_selected_hit(database)
    await _issue_authority(database, ids)

    assert (
        await _reader(database).read_by_selection(
            retrieval_run_id=uuid4(),
            knowledge_chunk_id=ids["chunk"],
        )
        is None
    )


async def test_unknown_assessment_ref_returns_none(database) -> None:
    ids = await _seed_selected_hit(database)
    issued = await _issue_authority(database, ids)

    unknown = ImmutableArtifactRef(
        artifact_code=issued.assessment_artifact_ref.artifact_code,
        version=issued.assessment_artifact_ref.version,
        content_sha256="f" * 64,
    )

    assert await _reader(database).read_by_assessment_ref(assessment_artifact_ref=unknown) is None


# ---------------------------------------------------------------------------
# Corruption regression against the real row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("eligibility_receipt_sha256", "f" * 64),
        ("verifier_artifact_sha256", "f" * 64),
        ("validity_policy_sha256", "f" * 64),
        ("source_version", "2026.09.18"),
        ("content_sha256", "e" * 64),
    ],
)
async def test_corrupted_persisted_authority_fails_closed(database, column: str, value: str) -> None:
    ids = await _seed_selected_hit(database)
    await _issue_authority(database, ids)
    await _corrupt(database, column, value)

    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await _reader(database).read_by_selection(
            retrieval_run_id=ids["run"],
            knowledge_chunk_id=ids["chunk"],
        )


async def test_corrupted_assessment_hash_is_not_readable_by_its_original_ref(database) -> None:
    ids = await _seed_selected_hit(database)
    issued = await _issue_authority(database, ids)
    await _corrupt(database, "assessment_artifact_sha256", "f" * 64)

    reader = _reader(database)

    # 원래 ref로는 더 이상 행이 없고, 변조된 ref로는 재계산 대조에서 fail closed한다.
    assert await reader.read_by_assessment_ref(assessment_artifact_ref=issued.assessment_artifact_ref) is None

    mutated = ImmutableArtifactRef(
        artifact_code=issued.assessment_artifact_ref.artifact_code,
        version=issued.assessment_artifact_ref.version,
        content_sha256="f" * 64,
    )
    with pytest.raises(AssessmentEligibilityAuthorityReaderError):
        await reader.read_by_assessment_ref(assessment_artifact_ref=mutated)


# ---------------------------------------------------------------------------
# Read-only boundary
# ---------------------------------------------------------------------------


async def test_reader_transaction_is_read_only_against_real_postgresql(database) -> None:
    ids = await _seed_selected_hit(database)
    await _issue_authority(database, ids)

    reader = _reader(database)
    await reader.read_by_selection(retrieval_run_id=ids["run"], knowledge_chunk_id=ids["chunk"])

    # READ ONLY 선언이 실제로 적용되는지, 같은 선언 아래 쓰기가 거부되는지 확인한다.
    sessions = async_sessionmaker(database, expire_on_commit=False)
    async with sessions() as session, session.begin():
        await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        assert (await session.scalar(text("SHOW transaction_read_only"))) == "on"
        assert (await session.scalar(text("SHOW transaction_isolation"))) == "repeatable read"
