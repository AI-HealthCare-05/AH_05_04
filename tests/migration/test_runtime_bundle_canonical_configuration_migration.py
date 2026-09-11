"""RAG-12A canonical 구성 영속화 migration의 downgrade 안전성 (Issue #175).

`175a1b2c3d4e`는 identity 컬럼을 NOT NULL로 추가하므로, 데이터가 있는 상태의 downgrade는
그 컬럼을 지워 provenance를 잃는다. 그래서 guard가 거부하도록 구현했고, 이 테스트가 그
거부 경로와 빈 DB 성공 경로를 모두 고정한다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    PROJECT_ROOT / "backend" / "alembic" / "versions" / "175a1b2c3d4e_persist_runtime_bundle_canonical_configuration.py"
)
RUNTIME_BUNDLE_REVISION = "175a1b2c3d4e"

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


ISOLATED_DATABASE = "runtime_bundle175_migration_test"


def _isolated_url(database: str) -> URL:
    """URL.create를 쓴다 — 문자열 조립은 비밀번호 특수문자에서 host 파싱이 깨진다."""
    return URL.create(
        drivername="postgresql+asyncpg",
        username=config.DB_USER,
        password=config.DB_PASSWORD,
        host="127.0.0.1",
        port=config.DB_EXPOSE_PORT,
        database=database,
    )


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    """전용 DB를 향해 실제 Alembic을 subprocess로 실행한다.

    `backend/alembic/env.py`가 import 시점에 `app_config.database_url`로 `sqlalchemy.url`을
    덮어쓰기 때문에, in-process `Config.set_main_option`으로는 대상 DB를 바꿀 수 없다. CI가
    쓰는 방식과 같게 환경변수를 준 별도 프로세스로 실행해 공유 test DB를 전혀 건드리지 않는다.

    격리가 필수인 이유: #398의 `3984b5c6d7e8` downgrade는 무조건 `RuntimeError`를 던진다.
    공유 DB의 revision을 그 위로 올려두면 이후 downgrade하는 다른 migration 테스트가 모두
    막히므로, 이 파일은 공유 revision 상태를 바꾸지 않는다.
    """
    # 자격증명을 명시적으로 넘긴다. 이 프로세스는 `uv run --env-file .env`로 값을 받지만
    # subprocess는 .env를 읽지 않으므로, 전달하지 않으면 config가 기본 host로 폴백해 실패한다.
    environment = {
        **os.environ,
        "DB_HOST": "127.0.0.1",
        "DB_PORT": str(config.DB_EXPOSE_PORT),
        "DB_EXPOSE_PORT": str(config.DB_EXPOSE_PORT),
        "DB_USER": config.DB_USER,
        "DB_PASSWORD": config.DB_PASSWORD,
        "DB_NAME": ISOLATED_DATABASE,
        "PYTHONPATH": f"{PROJECT_ROOT / 'backend'}:{PROJECT_ROOT}",
    }
    return subprocess.run(
        ["uv", "run", "alembic", "-c", "backend/alembic.ini", *args],
        cwd=str(PROJECT_ROOT),
        env=environment,
        capture_output=True,
        text=True,
    )


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(_isolated_url(ISOLATED_DATABASE), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@asynccontextmanager
async def _maintenance_connection() -> AsyncIterator[asyncpg.Connection]:
    """CREATE/DROP DATABASE는 raw asyncpg로 연결한다.

    `checks.yml`의 "Verify isolated Source cleanup workflow" step과 같은 방식이다. SQLAlchemy
    engine 경유는 이 저장소 환경에서 asyncpg SSL 협상 단계의 host 조회로 넘어가 실패한다.
    """
    connection = await asyncpg.connect(
        host="127.0.0.1",
        port=config.DB_EXPOSE_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database="postgres",
    )
    try:
        yield connection
    finally:
        await connection.close()


async def _recreate_isolated_database() -> None:
    async with _maintenance_connection() as connection:
        await connection.execute(f"DROP DATABASE IF EXISTS {ISOLATED_DATABASE} WITH (FORCE)")
        await connection.execute(f"CREATE DATABASE {ISOLATED_DATABASE}")


async def _drop_isolated_database() -> None:
    async with _maintenance_connection() as connection:
        await connection.execute(f"DROP DATABASE IF EXISTS {ISOLATED_DATABASE} WITH (FORCE)")


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
                    # PENDING + verified_at/effective_at NULL로 넣는다. #398의
                    # chk_rag_snapshot_verification_seal은 그 밖의 상태에 verification_seal_id를
                    # 요구하는데, 이 테스트는 snapshot 검증 상태와 무관하고 복합 FK 대상 행만 필요하다.
                    " 'normalization-v1', 'canonical-v1', 1, 0, 'PENDING', now())"
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


@pytest.fixture
def isolated_database_at_runtime_revision() -> Any:
    """전용 DB를 테스트 대상 Runtime revision까지 올린다. 후속 merge head와 분리한다."""
    asyncio.run(_recreate_isolated_database())
    result = _run_alembic("upgrade", RUNTIME_BUNDLE_REVISION)
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        yield
    finally:
        asyncio.run(_drop_isolated_database())


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


def test_real_alembic_downgrade_is_refused_while_bundle_data_exists(
    isolated_database_at_runtime_revision: None,
) -> None:
    """실제 Alembic downgrade가 거부되고, 행과 identity 컬럼이 모두 보존되어야 한다."""
    _ = isolated_database_at_runtime_revision
    asyncio.run(_seed_bundle_with_member())

    # 부모 revision에서 멈추므로 #175만 되돌리고 #398 부모 자체의 downgrade는 실행하지 않는다.
    # merge head에서 상대 -1로 분기를 추측하지 않고 테스트 대상 revision과 부모를 명시한다.
    result = _run_alembic("downgrade", "3984b5c6d7e8")

    assert result.returncode != 0
    assert "Runtime Bundle 행이 존재하면" in result.stdout + result.stderr
    # 거부 후에도 행과 identity 컬럼이 그대로 남아야 rollback 안전성이 성립한다.
    assert asyncio.run(_count("rag_runtime_release_bundle")) == 1
    assert asyncio.run(_count("rag_runtime_bundle_source")) == 1
    for table, columns in IDENTITY_COLUMNS.items():
        assert set(columns).issubset(asyncio.run(_existing_columns(table))), table


def test_real_alembic_downgrade_succeeds_and_removes_identity_columns_when_empty(
    isolated_database_at_runtime_revision: None,
) -> None:
    _ = isolated_database_at_runtime_revision
    assert asyncio.run(_count("rag_runtime_release_bundle")) == 0
    assert asyncio.run(_count("rag_runtime_bundle_source")) == 0

    downgraded = _run_alembic("downgrade", "3984b5c6d7e8")
    assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr

    for table, columns in IDENTITY_COLUMNS.items():
        assert set(columns).isdisjoint(asyncio.run(_existing_columns(table))), table

    restored = _run_alembic("upgrade", RUNTIME_BUNDLE_REVISION)
    assert restored.returncode == 0, restored.stdout + restored.stderr

    for table, columns in IDENTITY_COLUMNS.items():
        assert set(columns).issubset(asyncio.run(_existing_columns(table))), table
