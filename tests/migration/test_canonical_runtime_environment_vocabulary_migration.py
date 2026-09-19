"""Migration tests for canonical runtime environment vocabulary persistence (Issue #810, PD-799-20260918).

Verifies:
1. upgrade() creates chk_rag_runtime_environment_code and chk_rag_runtime_bundle_environment_code.
2. downgrade() drops both constraints without data loss.
3. Parameterized allowed/rejected vocabulary on PostgreSQL.
4. Preflight data validation on upgrade prevents migrating invalid data.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from rag_runtime.runtime_environment import RuntimeEnvironmentCode

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    PROJECT_ROOT / "backend" / "alembic" / "versions" / "810a1b2c3d4e_canonical_runtime_environment_vocabulary.py"
)
VOCABULARY_REVISION = "810a1b2c3d4e"
BASE_REVISION = "809a2b3c4d5e"

ENV_TABLE = "rag_runtime_environment"
BUNDLE_TABLE = "rag_runtime_release_bundle"
ENV_CONSTRAINT = "chk_rag_runtime_environment_code"
BUNDLE_CONSTRAINT = "chk_rag_runtime_bundle_environment_code"

ISOLATED_DATABASE = "canonical_runtime_env_810_migration_test"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("runtime_env_vocabulary_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Migration module을 불러올 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _isolated_url(database: str) -> URL:
    return URL.create(
        drivername="postgresql+asyncpg",
        username=config.DB_USER,
        password=config.DB_PASSWORD,
        host="127.0.0.1",
        port=config.DB_EXPOSE_PORT,
        database=database,
    )


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
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
        [sys.executable, "-m", "alembic", "-c", "backend/alembic.ini", *args],
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


async def _constraint_exists(table: str, constraint_name: str) -> bool:
    async with _connection() as connection:
        result = await connection.scalar(
            text(
                "SELECT count(*) FROM pg_constraint c "
                "JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = :table AND c.conname = :constraint"
            ),
            {"table": table, "constraint": constraint_name},
        )
        return bool(result)


async def _seed_manifest(connection: AsyncConnection) -> str:
    manifest_id = str(uuid4())
    suffix = uuid4().hex[:8]
    await connection.execute(
        text(
            """
            INSERT INTO rag_runtime_execution_manifest (
                id, manifest_key, manifest_version, manifest_hash, schema_version, git_commit_sha
            )
            VALUES (:id, :key, '1.0.0', :hash, 'runtime-manifest-v1', 'abcdef1')
            """
        ),
        {"id": manifest_id, "key": f"manifest-{suffix}", "hash": uuid4().hex * 2},
    )
    return manifest_id


@pytest.fixture(scope="module")
def isolated_database_at_vocabulary_revision() -> Any:
    asyncio.run(_recreate_isolated_database())
    result = _run_alembic("upgrade", VOCABULARY_REVISION)
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        yield
    finally:
        asyncio.run(_drop_isolated_database())


# =========================================================================
# Unit tests for migration module preflight validation
# =========================================================================


class FakePreflightConnection:
    def __init__(self, env_codes: list[str], bundle_codes: list[str]) -> None:
        self._env_codes = env_codes
        self._bundle_codes = bundle_codes

    def execute(self, statement: object) -> list[tuple[str]]:
        rendered = str(statement)
        if "FROM rag_runtime_environment" in rendered:
            return [(code,) for code in self._env_codes]
        if "FROM rag_runtime_release_bundle" in rendered:
            return [(code,) for code in self._bundle_codes]
        return []


def test_validate_existing_data_accepts_canonical_and_empty() -> None:
    migration = _load_migration()
    conn = FakePreflightConnection(
        env_codes=["LOCAL", "TEST", "CLOSED_DEMO", "PRODUCTION"],
        bundle_codes=["LOCAL", "PRODUCTION"],
    )
    migration._validate_existing_data(conn)


def test_validate_existing_data_rejects_noncanonical_env() -> None:
    migration = _load_migration()
    conn = FakePreflightConnection(
        env_codes=["LOCAL", "local"],
        bundle_codes=["PRODUCTION"],
    )
    with pytest.raises(RuntimeError, match="rag_runtime_environment contains non-canonical"):
        migration._validate_existing_data(conn)


def test_validate_existing_data_rejects_noncanonical_bundle() -> None:
    migration = _load_migration()
    conn = FakePreflightConnection(
        env_codes=["TEST"],
        bundle_codes=["STAGING"],
    )
    with pytest.raises(RuntimeError, match="rag_runtime_release_bundle contains non-canonical"):
        migration._validate_existing_data(conn)


# =========================================================================
# Integration tests for PostgreSQL CHECK constraints
# =========================================================================


def test_migration_creates_both_check_constraints(
    isolated_database_at_vocabulary_revision: None,
) -> None:
    _ = isolated_database_at_vocabulary_revision
    assert asyncio.run(_constraint_exists(ENV_TABLE, ENV_CONSTRAINT)) is True
    assert asyncio.run(_constraint_exists(BUNDLE_TABLE, BUNDLE_CONSTRAINT)) is True


@pytest.mark.parametrize("code", list(RuntimeEnvironmentCode))
def test_allowed_canonical_codes_accepted_in_both_tables(
    isolated_database_at_vocabulary_revision: None,
    code: str,
) -> None:
    _ = isolated_database_at_vocabulary_revision

    async def _test() -> None:
        async with _connection() as connection:
            async with connection.begin():
                manifest_id = await _seed_manifest(connection)
                bundle_id = str(uuid4())
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_runtime_release_bundle (
                            id, bundle_key, bundle_version, bundle_status,
                            execution_manifest_id, bundle_manifest_hash,
                            environment_code, catalog_version, catalog_manifest_hash
                        )
                        VALUES (
                            :id, :key, '1.0.0', 'BUILDING',
                            :manifest_id, :bundle_hash,
                            :env_code, 'catalog-1.0.0', :cat_hash
                        )
                        """
                    ),
                    {
                        "id": bundle_id,
                        "key": f"bundle-{code}-{uuid4().hex[:8]}",
                        "manifest_id": manifest_id,
                        "bundle_hash": uuid4().hex * 2,
                        "env_code": code,
                        "cat_hash": uuid4().hex * 2,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_runtime_environment (
                            id, environment_code, environment_status
                        )
                        VALUES (:id, :env_code, 'SUSPENDED')
                        """
                    ),
                    {"id": str(uuid4()), "env_code": code},
                )

    asyncio.run(_test())


@pytest.mark.parametrize(
    "invalid_code",
    [
        "local",
        "test",
        "production",
        "closed_demo",
        "STAGING",
        "DEV",
        "UNKNOWN",
        " LOCAL ",
        "TEST ",
        " TEST",
        "",
    ],
)
def test_rejected_codes_violate_check_constraints(
    isolated_database_at_vocabulary_revision: None,
    invalid_code: str,
) -> None:
    _ = isolated_database_at_vocabulary_revision

    async def _test() -> None:
        async with _connection() as connection:
            # Test bundle check constraint
            async with connection.begin():
                manifest_id = await _seed_manifest(connection)
                with pytest.raises(IntegrityError):
                    await connection.execute(
                        text(
                            """
                            INSERT INTO rag_runtime_release_bundle (
                                id, bundle_key, bundle_version, bundle_status,
                                execution_manifest_id, bundle_manifest_hash,
                                environment_code, catalog_version, catalog_manifest_hash
                            )
                            VALUES (
                                :id, :key, '1.0.0', 'BUILDING',
                                :manifest_id, :bundle_hash,
                                :env_code, 'catalog-1.0.0', :cat_hash
                            )
                            """
                        ),
                        {
                            "id": str(uuid4()),
                            "key": f"bundle-inv-{uuid4().hex[:8]}",
                            "manifest_id": manifest_id,
                            "bundle_hash": uuid4().hex * 2,
                            "env_code": invalid_code,
                            "cat_hash": uuid4().hex * 2,
                        },
                    )

            # Test environment check constraint
            async with connection.begin():
                with pytest.raises(IntegrityError):
                    await connection.execute(
                        text(
                            """
                            INSERT INTO rag_runtime_environment (
                                id, environment_code, environment_status
                            )
                            VALUES (:id, :env_code, 'SUSPENDED')
                            """
                        ),
                        {"id": str(uuid4()), "env_code": invalid_code},
                    )

    asyncio.run(_test())


def test_downgrade_drops_constraints_and_upgrade_restores(
    isolated_database_at_vocabulary_revision: None,
) -> None:
    _ = isolated_database_at_vocabulary_revision

    # Downgrade to BASE_REVISION
    downgrade_result = _run_alembic("downgrade", BASE_REVISION)
    assert downgrade_result.returncode == 0, downgrade_result.stdout + downgrade_result.stderr

    # Both constraints must be absent
    assert asyncio.run(_constraint_exists(ENV_TABLE, ENV_CONSTRAINT)) is False
    assert asyncio.run(_constraint_exists(BUNDLE_TABLE, BUNDLE_CONSTRAINT)) is False

    # Non-canonical value is allowed while downgraded (provided nonblank constraint passes)
    async def _insert_legacy() -> tuple[str, str]:
        async with _connection() as connection:
            async with connection.begin():
                manifest_id = await _seed_manifest(connection)
                bundle_id = str(uuid4())
                env_id = str(uuid4())
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_runtime_release_bundle (
                            id, bundle_key, bundle_version, bundle_status,
                            execution_manifest_id, bundle_manifest_hash,
                            environment_code, catalog_version, catalog_manifest_hash
                        )
                        VALUES (
                            :id, :key, '1.0.0', 'BUILDING',
                            :manifest_id, :bundle_hash,
                            'local', 'catalog-1.0.0', :cat_hash
                        )
                        """
                    ),
                    {
                        "id": bundle_id,
                        "key": f"bundle-legacy-{uuid4().hex[:8]}",
                        "manifest_id": manifest_id,
                        "bundle_hash": uuid4().hex * 2,
                        "cat_hash": uuid4().hex * 2,
                    },
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_runtime_environment (
                            id, environment_code, environment_status
                        )
                        VALUES (:id, 'local', 'SUSPENDED')
                        """
                    ),
                    {"id": env_id},
                )
                return bundle_id, env_id

    bundle_id, env_id = asyncio.run(_insert_legacy())

    # Upgrading while non-canonical data exists must fail preflight validation
    upgrade_fail = _run_alembic("upgrade", VOCABULARY_REVISION)
    assert upgrade_fail.returncode != 0
    assert "non-canonical" in upgrade_fail.stdout + upgrade_fail.stderr

    # Clean up non-canonical data
    async def _cleanup() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("DELETE FROM rag_runtime_environment WHERE id = :id"),
                    {"id": env_id},
                )
                await connection.execute(
                    text("DELETE FROM rag_runtime_release_bundle WHERE id = :id"),
                    {"id": bundle_id},
                )

    asyncio.run(_cleanup())

    # Now upgrade succeeds and constraints are re-established
    upgrade_success = _run_alembic("upgrade", VOCABULARY_REVISION)
    assert upgrade_success.returncode == 0, upgrade_success.stdout + upgrade_success.stderr
    assert asyncio.run(_constraint_exists(ENV_TABLE, ENV_CONSTRAINT)) is True
    assert asyncio.run(_constraint_exists(BUNDLE_TABLE, BUNDLE_CONSTRAINT)) is True
