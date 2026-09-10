"""RAG-12A canonical 구성 영속화 migration의 downgrade 안전성 (Issue #175).

`175a1b2c3d4e`는 identity 컬럼을 NOT NULL로 추가하므로, 데이터가 있는 상태의 downgrade는
그 컬럼을 지워 provenance를 잃는다. 그래서 guard가 거부하도록 구현했고, 이 테스트가 그
거부 경로와 빈 DB 성공 경로를 모두 고정한다.
"""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    PROJECT_ROOT / "backend" / "alembic" / "versions" / "175a1b2c3d4e_persist_runtime_bundle_canonical_configuration.py"
)
RUNTIME_BUNDLE_REVISION = "175a1b2c3d4e"
RUNTIME_BUNDLE_BASE_REVISION = "206a1b2c3d4e"

IDENTITY_COLUMNS = {
    "rag_runtime_bundle_source": (
        "source_version",
        "canonical_checksum",
        "approval_version",
        "scope_policy_hash",
        "freshness_policy_hash",
    ),
    "rag_runtime_release_bundle": (
        "environment_code",
        "catalog_version",
        "catalog_manifest_hash",
        "candidate_index_version",
    ),
}


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("runtime_bundle_canonical_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Migration module을 불러올 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class FakeConnection:
    """`FROM <table>`이 있는 SELECT만 테이블별 count로 응답한다.

    두 테이블의 count가 비대칭일 때도 guard가 반응하는지 보기 위해 테이블별로 값을 나눈다.
    """

    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts

    def execute(self, statement: object) -> ScalarResult:
        rendered = str(statement)
        for table, count in self._counts.items():
            if f"FROM {table}" in rendered:
                return ScalarResult(count)
        return ScalarResult(0)


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


def _hash(char: str) -> str:
    return char * 64


async def _seed_bundle_with_member() -> dict[str, str]:
    """합성 Source snapshot·Manifest·Bundle·member 한 세트를 넣는다."""
    suffix = uuid4().hex[:10]
    ids = {key: str(uuid4()) for key in ("source", "endpoint", "operation", "snapshot", "manifest", "bundle", "member")}
    source_version = f"api:2026-09-10:{suffix}"
    # SHA-256 컬럼은 64 hex를 요구하고 manifest/bundle hash에는 unique 제약이 있으므로
    # 실행마다 유일한 값을 만든다.
    unique_hash = uuid4().hex * 2
    bundle_hash = uuid4().hex * 2
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status) "
                    "VALUES (:id, :code, 'MFDS Migration Source', 'ACTIVE')"
                ),
                {"id": ids["source"], "code": f"MFDS_MIG_{suffix}"},
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_source_endpoint "
                    "(id, source_id, endpoint_code, display_name, lifecycle_status, runtime_status, "
                    " acquisition_status) "
                    "VALUES (:id, :source_id, 'PRODUCT_LIST', 'Product List', 'VERIFIED', 'ENABLED', 'APPROVED')"
                ),
                {"id": ids["endpoint"], "source_id": ids["source"]},
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_source_operation "
                    "(id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status) "
                    "VALUES (:id, :endpoint_id, 'LIST_PRODUCTS', 'List Products', 'ENABLED', 'APPROVED')"
                ),
                {"id": ids["operation"], "endpoint_id": ids["endpoint"]},
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_source_snapshot "
                    "(id, operation_id, source_version, raw_manifest_checksum, canonical_checksum, schema_version, "
                    " parser_version, normalization_version, canonicalization_spec_version, record_count, "
                    " rejected_record_count, verification_status, collected_at) "
                    "VALUES (:id, :operation_id, :source_version, :raw, :canonical, 'schema-v1', 'parser-v1', "
                    " 'normalization-v1', 'canonical-v1', 1, 0, 'CURRENT', now())"
                ),
                {
                    "id": ids["snapshot"],
                    "operation_id": ids["operation"],
                    "source_version": source_version,
                    "raw": _hash("a"),
                    "canonical": _hash("b"),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_runtime_execution_manifest "
                    "(id, manifest_key, manifest_version, manifest_hash, schema_version, git_commit_sha) "
                    "VALUES (:id, :key, '1.0.0', :hash, 'runtime-manifest-v1', 'abcdef1')"
                ),
                {"id": ids["manifest"], "key": f"rag-runtime-mig-{suffix}", "hash": unique_hash},
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_runtime_release_bundle "
                    "(id, bundle_key, bundle_version, bundle_status, execution_manifest_id, bundle_manifest_hash, "
                    " environment_code, catalog_version, catalog_manifest_hash, candidate_index_ref, "
                    " candidate_index_version, candidate_index_manifest_hash) "
                    "VALUES (:id, :key, '1.0.0', 'BUILDING', :manifest_id, :hash, 'local', 'catalog-1.0.0', "
                    " :catalog_hash, 'candidate-index:local', '1.0.0', :index_hash)"
                ),
                {
                    "id": ids["bundle"],
                    "key": f"local-rag-runtime-mig-{suffix}",
                    "manifest_id": ids["manifest"],
                    "hash": bundle_hash,
                    "catalog_hash": _hash("9"),
                    "index_hash": _hash("e"),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO rag_runtime_bundle_source "
                    "(id, bundle_id, source_snapshot_id, source_version, canonical_checksum, approval_version, "
                    " scope_policy_hash, freshness_policy_hash, source_purpose) "
                    "VALUES (:id, :bundle_id, :snapshot_id, :source_version, :canonical, 'approval-v1', "
                    " :scope, :freshness, 'CATALOG')"
                ),
                {
                    "id": ids["member"],
                    "bundle_id": ids["bundle"],
                    "snapshot_id": ids["snapshot"],
                    "source_version": source_version,
                    "canonical": _hash("b"),
                    "scope": _hash("c"),
                    "freshness": _hash("d"),
                },
            )
    return ids


async def _delete_seeded(ids: dict[str, str]) -> None:
    """Runtime 행만 지운다.

    `rag_source_snapshot`은 `165e8f706152`의 append-only trigger가 DELETE를 막으므로 남겨 둔다.
    Source 체인은 seed마다 유일한 code/version을 쓰고, `_require_empty`가 검사하는 대상도
    Runtime 두 테이블이므로 남아 있어도 이 테스트에 영향이 없다.
    """
    async with _connection() as connection:
        async with connection.begin():
            for table, key in (
                ("rag_runtime_bundle_source", "member"),
                ("rag_runtime_release_bundle", "bundle"),
                ("rag_runtime_execution_manifest", "manifest"),
            ):
                await connection.execute(text(f"DELETE FROM {table} WHERE id = :id"), {"id": ids[key]})


async def _clear_runtime_tables() -> None:
    """Runtime 테이블만 비운다 — 다른 테스트가 남긴 행과 무관하게 빈 DB 경로를 검증한다."""
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    "TRUNCATE TABLE rag_runtime_bundle_source, rag_release_evaluation_approval, "
                    "rag_runtime_environment_transition, rag_runtime_environment, "
                    "rag_runtime_release_bundle, rag_runtime_execution_manifest"
                )
            )


async def _count(table: str) -> int:
    async with _connection() as connection:
        result = await connection.execute(text(f"SELECT count(*) FROM {table}"))
        return int(result.scalar_one())


async def _existing_columns(table: str) -> set[str]:
    async with _connection() as connection:
        result = await connection.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = :table AND table_schema = current_schema()"
            ),
            {"table": table},
        )
        return {row[0] for row in result}


@pytest.fixture(autouse=True)
def _restore_head() -> Any:
    yield
    command.upgrade(_alembic_config(), RUNTIME_BUNDLE_REVISION)


def test_require_empty_rejects_bundle_rows() -> None:
    migration = _load_migration()

    with pytest.raises(RuntimeError, match="rag_runtime_release_bundle=2"):
        migration._require_empty(FakeConnection({"rag_runtime_release_bundle": 2}))


def test_require_empty_rejects_member_rows_even_when_bundles_are_empty() -> None:
    """비대칭 데이터에서도 거부해야 한다 — member만 남아 있어도 identity 컬럼이 사라진다."""
    migration = _load_migration()

    with pytest.raises(RuntimeError, match="rag_runtime_bundle_source=3"):
        migration._require_empty(FakeConnection({"rag_runtime_bundle_source": 3}))


def test_require_empty_allows_empty_tables() -> None:
    migration = _load_migration()

    migration._require_empty(FakeConnection({}))


def test_real_alembic_downgrade_is_refused_while_bundle_data_exists() -> None:
    """실제 Alembic downgrade가 거부되고, 행과 identity 컬럼이 모두 보존되어야 한다."""
    ids = asyncio.run(_seed_bundle_with_member())
    try:
        with pytest.raises(RuntimeError, match="Runtime Bundle 행이 존재하면"):
            command.downgrade(_alembic_config(), RUNTIME_BUNDLE_BASE_REVISION)

        assert asyncio.run(_count("rag_runtime_release_bundle")) == 1
        assert asyncio.run(_count("rag_runtime_bundle_source")) == 1
        for table, columns in IDENTITY_COLUMNS.items():
            existing = asyncio.run(_existing_columns(table))
            assert set(columns).issubset(existing), table
    finally:
        asyncio.run(_delete_seeded(ids))


def test_real_alembic_downgrade_succeeds_and_removes_identity_columns_when_empty() -> None:
    asyncio.run(_clear_runtime_tables())
    assert asyncio.run(_count("rag_runtime_release_bundle")) == 0
    assert asyncio.run(_count("rag_runtime_bundle_source")) == 0

    command.downgrade(_alembic_config(), RUNTIME_BUNDLE_BASE_REVISION)

    for table, columns in IDENTITY_COLUMNS.items():
        existing = asyncio.run(_existing_columns(table))
        assert set(columns).isdisjoint(existing), table

    command.upgrade(_alembic_config(), RUNTIME_BUNDLE_REVISION)

    for table, columns in IDENTITY_COLUMNS.items():
        existing = asyncio.run(_existing_columns(table))
        assert set(columns).issubset(existing), table
