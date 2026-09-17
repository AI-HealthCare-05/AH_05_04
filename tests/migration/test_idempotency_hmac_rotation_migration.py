from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator, Iterator
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

from app.core import config  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "backend" / "alembic" / "versions" / "235a1b2c3d4e_idempotency_hmac_rotation_scope.py"
ASYNC_SCOPE_INDEX = "uq_idempotency_async_scope"
SYNC_SCOPE_INDEX = "uq_idempotency_sync_scope"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("idempotency_hmac_rotation_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Migration module을 불러올 수 없습니다.")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _migration_revision() -> str:
    revision = _load_migration().revision
    assert isinstance(revision, str)
    return revision


def _migration_down_revision() -> str:
    down_revision = _load_migration().down_revision
    assert isinstance(down_revision, str)
    return down_revision


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
def isolated_idempotency_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    database = f"idempotency685_{uuid4().hex[:12]}"
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


class ScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class FakeConnection:
    def __init__(self, *, async_conflicts: int = 0, sync_conflicts: int = 0) -> None:
        self.async_conflicts = async_conflicts
        self.sync_conflicts = sync_conflicts
        self.statements: list[str] = []

    def execute(self, statement: object) -> ScalarResult:
        text = str(statement)
        self.statements.append(text)
        if "LOCK TABLE idempotency_record" in text:
            return ScalarResult(0)
        if "record_type = 'ASYNC_JOB'" in text:
            return ScalarResult(self.async_conflicts)
        if "record_type = 'SYNC_MUTATION'" in text:
            return ScalarResult(self.sync_conflicts)
        return ScalarResult(0)


async def _seed_user(connection: AsyncConnection, user_id: str, email_suffix: str) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO "user" (id, email, hashed_password, name, is_active, account_status, token_version, is_admin)
            VALUES (:user_id, :email, 'synthetic-password-hash', 'migration685', true, 'ACTIVE', 0, false)
            """
        ),
        {"user_id": user_id, "email": f"migration685-{email_suffix}@example.com"},
    )


async def _seed_ai_job(connection: AsyncConnection, *, job_id: str, user_id: str) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO ai_job (id, user_id, job_type, status, attempt_count, max_attempts)
            VALUES (:job_id, :user_id, 'OCR', 'PENDING', 0, 1)
            """
        ),
        {"job_id": job_id, "user_id": user_id},
    )


async def _seed_async_idempotency_record(
    connection: AsyncConnection,
    *,
    record_id: str,
    user_id: str,
    job_id: str,
    operation_id: str,
    key_hmac_version: str,
    key_hmac: str,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO idempotency_record (
                id, user_id, operation_id, key_hmac_version, key_hmac, request_hash,
                record_type, job_id, expires_at
            )
            VALUES (
                :record_id, :user_id, :operation_id, :key_hmac_version, :key_hmac, :request_hash,
                'ASYNC_JOB', :job_id, now() + interval '1 day'
            )
            """
        ),
        {
            "record_id": record_id,
            "user_id": user_id,
            "operation_id": operation_id,
            "key_hmac_version": key_hmac_version,
            "key_hmac": key_hmac,
            "request_hash": f"request-{record_id}",
            "job_id": job_id,
        },
    )


async def _seed_sync_idempotency_record(
    connection: AsyncConnection,
    *,
    record_id: str,
    user_id: str,
    parent_resource_id: str,
    operation_id: str,
    key_hmac_version: str,
    key_hmac: str,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO idempotency_record (
                id, user_id, operation_id, key_hmac_version, key_hmac, request_hash, record_type,
                parent_resource_id, response_status, response_body_snapshot, encryption_key_version, expires_at
            )
            VALUES (
                :record_id, :user_id, :operation_id, :key_hmac_version, :key_hmac, :request_hash,
                'SYNC_MUTATION', :parent_resource_id, 200, :response_body_snapshot, 'test-key-v1',
                now() + interval '1 day'
            )
            """
        ),
        {
            "record_id": record_id,
            "user_id": user_id,
            "operation_id": operation_id,
            "key_hmac_version": key_hmac_version,
            "key_hmac": key_hmac,
            "request_hash": f"request-{record_id}",
            "parent_resource_id": parent_resource_id,
            "response_body_snapshot": b"{}",
        },
    )


async def _seed_conflicting_hmac_rotation_rows(operation_prefix: str) -> tuple[str, int]:
    user_id = str(uuid4())
    async_job_ids = [str(uuid4()), str(uuid4())]
    async_record_ids = [str(uuid4()), str(uuid4())]
    sync_record_ids = [str(uuid4()), str(uuid4())]
    parent_resource_id = str(uuid4())
    key_hmac = "hmac-same-digest-for-downgrade-conflict"

    async with _connection() as connection:
        async with connection.begin():
            await _seed_user(connection, user_id, operation_prefix)
            for job_id in async_job_ids:
                await _seed_ai_job(connection, job_id=job_id, user_id=user_id)
            for record_id, job_id, version in zip(async_record_ids, async_job_ids, ("v1", "v2"), strict=True):
                await _seed_async_idempotency_record(
                    connection,
                    record_id=record_id,
                    user_id=user_id,
                    job_id=job_id,
                    operation_id=f"{operation_prefix}-async",
                    key_hmac_version=version,
                    key_hmac=key_hmac,
                )
            for record_id, version in zip(sync_record_ids, ("v1", "v2"), strict=True):
                await _seed_sync_idempotency_record(
                    connection,
                    record_id=record_id,
                    user_id=user_id,
                    parent_resource_id=parent_resource_id,
                    operation_id=f"{operation_prefix}-sync",
                    key_hmac_version=version,
                    key_hmac=key_hmac,
                )
    return user_id, 4


async def _seed_non_conflicting_hmac_rotation_rows(operation_prefix: str) -> tuple[str, int]:
    user_id = str(uuid4())
    async_job_ids = [str(uuid4()), str(uuid4())]

    async with _connection() as connection:
        async with connection.begin():
            await _seed_user(connection, user_id, operation_prefix)
            for job_id in async_job_ids:
                await _seed_ai_job(connection, job_id=job_id, user_id=user_id)
            for index, job_id in enumerate(async_job_ids, start=1):
                await _seed_async_idempotency_record(
                    connection,
                    record_id=str(uuid4()),
                    user_id=user_id,
                    job_id=job_id,
                    operation_id=f"{operation_prefix}-async",
                    key_hmac_version=f"v{index}",
                    key_hmac=f"hmac-async-{index}",
                )
            for index in range(1, 3):
                await _seed_sync_idempotency_record(
                    connection,
                    record_id=str(uuid4()),
                    user_id=user_id,
                    parent_resource_id=str(uuid4()),
                    operation_id=f"{operation_prefix}-sync",
                    key_hmac_version=f"v{index}",
                    key_hmac="hmac-sync-shared-digest",
                )
    return user_id, 4


async def _cleanup_idempotency_fixture(user_id: str) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text("DELETE FROM idempotency_record WHERE user_id = :user_id"), {"user_id": user_id}
            )
            await connection.execute(text("DELETE FROM ai_job WHERE user_id = :user_id"), {"user_id": user_id})
            await connection.execute(text('DELETE FROM "user" WHERE id = :user_id'), {"user_id": user_id})


async def _count_idempotency_rows(user_id: str) -> int:
    async with _connection() as connection:
        result = await connection.execute(
            text("SELECT count(*) FROM idempotency_record WHERE user_id = :user_id"),
            {"user_id": user_id},
        )
        return int(result.scalar_one())


async def _index_definition(index_name: str) -> str:
    async with _connection() as connection:
        result = await connection.execute(
            text(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'idempotency_record'
                  AND indexname = :index_name
                """
            ),
            {"index_name": index_name},
        )
        return str(result.scalar_one())


def test_migration_metadata_has_single_parent_revision() -> None:
    migration = _load_migration()

    assert migration.revision == "235a1b2c3d4e"
    assert isinstance(migration.down_revision, str)
    assert migration.down_revision


def test_downgrade_guard_allows_when_previous_unique_scope_has_no_conflict() -> None:
    migration = _load_migration()
    connection = FakeConnection()

    migration._ensure_downgrade_unique_scope_is_data_safe(connection)

    assert any("LOCK TABLE idempotency_record" in statement for statement in connection.statements)
    assert len(connection.statements) == 3


@pytest.mark.parametrize(
    ("async_conflicts", "sync_conflicts"),
    [
        (1, 0),
        (0, 1),
    ],
)
def test_downgrade_guard_rejects_when_previous_unique_scope_would_conflict(
    async_conflicts: int,
    sync_conflicts: int,
) -> None:
    migration = _load_migration()
    connection = FakeConnection(async_conflicts=async_conflicts, sync_conflicts=sync_conflicts)

    with pytest.raises(RuntimeError) as error:
        migration._ensure_downgrade_unique_scope_is_data_safe(connection)

    message = str(error.value)
    assert "Cannot downgrade revision 235a1b2c3d4e" in message
    assert f"async_conflicts={async_conflicts}" in message
    assert f"sync_conflicts={sync_conflicts}" in message


def test_postgresql_downgrade_rejects_conflicts_and_preserves_data_and_indexes(
    isolated_idempotency_database: None,
) -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, _migration_revision())
    user_id, expected_rows = asyncio.run(_seed_conflicting_hmac_rotation_rows("conflict"))

    try:
        with pytest.raises(RuntimeError) as error:
            command.downgrade(cfg, _migration_down_revision())

        message = str(error.value)
        assert "Cannot downgrade revision 235a1b2c3d4e" in message
        assert "async_conflicts=1" in message
        assert "sync_conflicts=1" in message
        assert asyncio.run(_count_idempotency_rows(user_id)) == expected_rows
        assert "key_hmac_version" in asyncio.run(_index_definition(ASYNC_SCOPE_INDEX))
        assert "key_hmac_version" in asyncio.run(_index_definition(SYNC_SCOPE_INDEX))
    finally:
        asyncio.run(_cleanup_idempotency_fixture(user_id))
        command.upgrade(cfg, "head")


def test_postgresql_downgrade_succeeds_when_previous_unique_scope_has_no_conflict(
    isolated_idempotency_database: None,
) -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, _migration_revision())
    user_id, expected_rows = asyncio.run(_seed_non_conflicting_hmac_rotation_rows("no-conflict"))

    try:
        command.downgrade(cfg, _migration_down_revision())

        assert asyncio.run(_count_idempotency_rows(user_id)) == expected_rows
        assert "key_hmac_version" not in asyncio.run(_index_definition(ASYNC_SCOPE_INDEX))
        assert "key_hmac_version" not in asyncio.run(_index_definition(SYNC_SCOPE_INDEX))
    finally:
        asyncio.run(_cleanup_idempotency_fixture(user_id))
        command.upgrade(cfg, "head")
