"""#712 Assessment·Eligibility Authority persistence migration의 DB 수준 불변식 검증.

`create_all` 기반 단위 테스트가 아니라 실제 Alembic migration이 만든 스키마에서
fail-closed 제약이 동작하는지 확인한다. 특히 PD-722 §6.1 half-open 구간과 selected hit
결속이 Python 검증에만 의존하지 않고 DB 제약으로도 강제되는지 확인한다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "backend" / "alembic" / "versions" / "712a1b2c3d4e_create_rag_evidence_authority.py"

REVISION = "712a1b2c3d4e"
TABLE_NAME = "rag_evidence_authority"

EVALUATED_AT = datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)
VALID_UNTIL = EVALUATED_AT + timedelta(hours=24)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("evidence_authority_persistence_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Migration module을 불러올 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
def isolated_authority_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    database = f"authority712_{uuid4().hex[:12]}"
    cluster_url = config.database_url

    async def database_action(create: bool) -> None:
        engine = create_async_engine(cluster_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                statement = f'CREATE DATABASE "{database}"' if create else f'DROP DATABASE "{database}" WITH (FORCE)'
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(database_action(True))
    monkeypatch.setattr(config, "DB_NAME", database)
    try:
        yield
    finally:
        asyncio.run(database_action(False))


async def _table_names() -> set[str]:
    async with _connection() as connection:
        rows = await connection.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename = :name"),
            {"name": TABLE_NAME},
        )
        return set(rows)


async def _row_count() -> int:
    async with _connection() as connection:
        return int((await connection.scalar(text(f"SELECT count(*) FROM {TABLE_NAME}"))) or 0)


async def _seed_selected_hit() -> dict[str, str]:
    """authority가 결속할 selected hit 한 건과 그 Source/Index provenance를 적재한다."""
    ids = {
        key: str(uuid4())
        for key in (
            "user",
            "job",
            "index",
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

    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    'INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin) '
                    "VALUES (:id, :email, 'hash', 'migration712', true, false)"
                ),
                {"id": ids["user"], "email": f"migration712-{suffix}@example.com"},
            )
            await connection.execute(
                text(
                    "INSERT INTO ai_job (id, user_id, job_type, status, max_attempts, attempt_count) "
                    "VALUES (:id, :uid, 'OCR', 'PENDING', 3, 0)"
                ),
                {"id": ids["job"], "uid": ids["user"]},
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status) "
                    "VALUES (:id, :code, 'MFDS Evidence Source', 'DRAFT')"
                ),
                {"id": ids["source"], "code": f"MFDS_MIG712_{suffix}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_source_endpoint "
                    "(id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, "
                    "acquisition_status) "
                    "VALUES (:id, :source_id, 'PRODUCT_LIST', 'Product List', 'DRAFT', 'DISABLED', 'PENDING')"
                ),
                {"id": ids["endpoint"], "source_id": ids["source"]},
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_source_operation "
                    "(id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                    "VALUES (:id, :endpoint_id, 'LIST_PRODUCTS', 'List Products', 'DISABLED', 'PENDING')"
                ),
                {"id": ids["operation"], "endpoint_id": ids["endpoint"]},
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
                    "id": ids["snapshot"],
                    "operation_id": ids["operation"],
                    "version": f"api:2026-09-17:{suffix}",
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
                    "id": ids["member"],
                    "snapshot_id": ids["snapshot"],
                    "endpoint_id": ids["endpoint"],
                    "operation_id": ids["operation"],
                    "locator": f"product/{suffix}",
                    "h": "d" * 64,
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_knowledge_index (id, index_code, index_version, corpus_manifest_hash, "
                    "embedding_manifest_hash, index_configuration_hash, embedding_model_ref, "
                    "embedding_model_version, embedding_dimension, distance_metric, member_count) "
                    "VALUES (:id, :code, '1.0', :h, :h, :h, 'text-embedding-3-large', '1.0', 1536, 'COSINE', 1)"
                ),
                {"id": ids["index"], "code": f"MIG712_IDX_{suffix}", "h": "a" * 64},
            )
            await connection.execute(
                text(
                    "INSERT INTO knowledge_document (id, title, source_url, document_version, document_status, "
                    "record_contract_version, publisher) "
                    "VALUES (:id, 'Migration Doc', :url, '1.0', 'ACTIVE', 'LEGACY_V1', 'MFDS')"
                ),
                {"id": ids["document"], "url": f"https://example.com/mig712-{suffix}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO knowledge_chunk (id, knowledge_document_id, chunk_index, chunk_text, "
                    "content_hash, normalization_version) "
                    "VALUES (:id, :doc_id, 0, '아스피린 복용 안내', :h, 'v1')"
                ),
                {"id": ids["chunk"], "doc_id": ids["document"], "h": "d" * 64},
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
                    ":id, :job_id, :ctx, :pv, :bundle, :h3, :manifest, :h4, 'ref-712', :index_id, :node, 'RET-H', "
                    "'sha256', 'v1', :h1, :filter_snapshot, :h5, :h6, :h2, 20, 20, 30, 5, "
                    "'COMPLETED', 'OK', :h7, :h8, NOW(), NOW())"
                ),
                {
                    "id": ids["run"],
                    "job_id": ids["job"],
                    "ctx": str(uuid4()),
                    "pv": str(uuid4()),
                    "bundle": str(uuid4()),
                    "manifest": str(uuid4()),
                    "index_id": ids["index"],
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
                {"run_id": ids["run"], "chunk_id": ids["chunk"]},
            )
    return ids


_INSERT_AUTHORITY = text(
    """
    INSERT INTO rag_evidence_authority (
        id, retrieval_run_id, knowledge_chunk_id, source_snapshot_id, source_snapshot_member_id,
        source_code, source_version, content_sha256,
        eligibility_receipt_artifact_code, eligibility_receipt_version, eligibility_receipt_sha256,
        assessment_artifact_code, assessment_artifact_version, assessment_artifact_sha256,
        verifier_artifact_code, verifier_artifact_version, verifier_artifact_sha256,
        validity_policy_artifact_code, validity_policy_version, validity_policy_sha256,
        evaluated_at, assessment_valid_from, assessment_valid_until
    ) VALUES (
        :id, :run_id, :chunk_id, :snapshot_id, :member_id,
        :source_code, :source_version, :content_sha256,
        'production_evidence_eligibility_receipt', 'v1', :eligibility_sha256,
        'production_evidence_assessment', 'v1', :assessment_sha256,
        'postgresql_evidence_eligibility_verifier', 'v1', :verifier_sha256,
        'evidence-assessment-validity', 'v1', :policy_sha256,
        :evaluated_at, :valid_from, :valid_until
    )
    """
)


def _authority_params(ids: dict[str, str], **overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "id": str(uuid4()),
        "run_id": ids["run"],
        "chunk_id": ids["chunk"],
        "snapshot_id": ids["snapshot"],
        "member_id": ids["member"],
        "source_code": "MFDS",
        "source_version": "2026.09.17",
        "content_sha256": "d" * 64,
        "eligibility_sha256": "1" * 64,
        "assessment_sha256": "2" * 64,
        "verifier_sha256": "3" * 64,
        "policy_sha256": "4" * 64,
        "evaluated_at": EVALUATED_AT,
        "valid_from": EVALUATED_AT,
        "valid_until": VALID_UNTIL,
    }
    params.update(overrides)
    return params


async def _insert_authority(params: dict[str, object]) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(_INSERT_AUTHORITY, params)


async def _expect_rejected(params: dict[str, object]) -> None:
    async with _connection() as connection:
        with pytest.raises((IntegrityError, DBAPIError)):
            async with connection.begin():
                await connection.execute(_INSERT_AUTHORITY, params)


def test_migration_metadata_chains_to_single_parent_revision() -> None:
    migration = _load_migration()

    assert migration.revision == REVISION
    assert isinstance(migration.down_revision, str)
    assert migration.down_revision


def test_migration_creates_and_drops_the_authority_table(isolated_authority_database: None) -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, REVISION)

    assert asyncio.run(_table_names()) == {TABLE_NAME}

    command.downgrade(cfg, _load_migration().down_revision)
    assert asyncio.run(_table_names()) == set()


def test_migrated_schema_enforces_authority_invariants(isolated_authority_database: None) -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, REVISION)

    ids = asyncio.run(_seed_selected_hit())
    asyncio.run(_insert_authority(_authority_params(ids)))

    # 같은 selected hit에 authority를 두 번 남길 수 없다.
    asyncio.run(_expect_rejected(_authority_params(ids, assessment_sha256="9" * 64)))

    # 다른 selected hit이 같은 assessment artifact identity를 재사용할 수 없다.
    other = asyncio.run(_seed_selected_hit())
    asyncio.run(_expect_rejected(_authority_params(other)))

    # 저장되지 않은 selected hit에는 authority를 결속할 수 없다.
    asyncio.run(_expect_rejected(_authority_params(ids, run_id=str(uuid4()), assessment_sha256="a" * 64)))
    asyncio.run(_expect_rejected(_authority_params(ids, chunk_id=str(uuid4()), assessment_sha256="b" * 64)))

    # PD-722 §6.1: 구간이 역전되거나 비어 있을 수 없다.
    asyncio.run(
        _expect_rejected(
            _authority_params(
                other,
                valid_from=VALID_UNTIL,
                valid_until=EVALUATED_AT,
                evaluated_at=EVALUATED_AT,
                assessment_sha256="c" * 64,
            )
        )
    )
    asyncio.run(
        _expect_rejected(
            _authority_params(other, valid_from=EVALUATED_AT, valid_until=EVALUATED_AT, assessment_sha256="e" * 64)
        )
    )

    # PD-722 §6.1: evaluated_at은 [valid_from, valid_until) 안이어야 한다 (종료는 배타적).
    asyncio.run(_expect_rejected(_authority_params(other, evaluated_at=VALID_UNTIL, assessment_sha256="f" * 64)))
    asyncio.run(
        _expect_rejected(
            _authority_params(other, evaluated_at=EVALUATED_AT - timedelta(seconds=1), assessment_sha256="0" * 64)
        )
    )

    # digest 열은 lowercase 64-hex만 허용한다.
    asyncio.run(
        _expect_rejected(_authority_params(other, verifier_sha256="not-a-sha256", assessment_sha256="1" * 63 + "a"))
    )
    asyncio.run(_expect_rejected(_authority_params(other, assessment_sha256="A" * 64)))

    # source 식별자는 공백일 수 없다.
    asyncio.run(_expect_rejected(_authority_params(other, source_code="   ", assessment_sha256="2" * 63 + "a")))
    asyncio.run(_expect_rejected(_authority_params(other, source_version="", assessment_sha256="3" * 63 + "a")))

    assert asyncio.run(_row_count()) == 1


def test_selected_hit_cannot_be_deleted_while_authority_exists(isolated_authority_database: None) -> None:
    """ON DELETE RESTRICT: authority가 가리키는 hit을 지워 고아 증거를 만들 수 없다."""
    cfg = create_alembic_config()
    command.upgrade(cfg, REVISION)

    ids = asyncio.run(_seed_selected_hit())
    asyncio.run(_insert_authority(_authority_params(ids)))

    async def delete_hit() -> None:
        async with _connection() as connection:
            with pytest.raises((IntegrityError, DBAPIError)):
                async with connection.begin():
                    await connection.execute(
                        text("DELETE FROM retrieval_hit WHERE retrieval_run_id = :run_id"),
                        {"run_id": ids["run"]},
                    )

    asyncio.run(delete_hit())
    assert asyncio.run(_row_count()) == 1


def test_downgrade_refuses_when_authority_data_exists(isolated_authority_database: None) -> None:
    """append-only historical 증거를 조용히 잃지 않는다."""
    cfg = create_alembic_config()
    command.upgrade(cfg, REVISION)

    ids = asyncio.run(_seed_selected_hit())
    asyncio.run(_insert_authority(_authority_params(ids)))
    assert asyncio.run(_row_count()) == 1

    with pytest.raises(RuntimeError) as error:
        command.downgrade(cfg, _load_migration().down_revision)

    assert "Refusing to downgrade evidence authority table with existing data" in str(error.value)
    assert TABLE_NAME in str(error.value)

    # 표가 drop되지 않고 기존 행도 그대로 남아 있어야 한다.
    assert asyncio.run(_table_names()) == {TABLE_NAME}
    assert asyncio.run(_row_count()) == 1


def test_empty_upgrade_downgrade_upgrade_round_trip(isolated_authority_database: None) -> None:
    """데이터가 없으면 downgrade와 재upgrade가 그대로 통과한다."""
    cfg = create_alembic_config()
    down_revision = _load_migration().down_revision

    command.upgrade(cfg, REVISION)
    assert asyncio.run(_table_names()) == {TABLE_NAME}

    command.downgrade(cfg, down_revision)
    assert asyncio.run(_table_names()) == set()

    command.upgrade(cfg, REVISION)
    assert asyncio.run(_table_names()) == {TABLE_NAME}
    assert asyncio.run(_row_count()) == 0
