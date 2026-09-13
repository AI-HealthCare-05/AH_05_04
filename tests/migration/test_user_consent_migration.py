from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "backend/alembic/versions/207b1c2d3e4_create_user_consent.py"
USER_CONSENT_REVISION = "207b1c2d3e4"
USER_CONSENT_BASE_REVISION = "428a1b2c3d4e"


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("user_consent_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


class ScalarResult:
    def __init__(self, has_row: bool) -> None:
        self._has_row = has_row

    def first(self) -> tuple[int] | None:
        return (1,) if self._has_row else None


class FakeConnection:
    def __init__(self, *, has_row: bool) -> None:
        self.has_row = has_row
        self.statements: list[str] = []

    def execute(self, statement: object) -> ScalarResult:
        statement_text = str(statement)
        self.statements.append(statement_text)
        return ScalarResult(self.has_row and "SELECT 1 FROM user_consent" in statement_text)


async def _ensure_pg_trgm_in_public() -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public"))
            await connection.execute(text("ALTER EXTENSION pg_trgm SET SCHEMA public"))


async def _schema_snapshot() -> tuple[set[str], set[str]]:
    async with _connection() as connection:
        constraints = await connection.execute(
            text(
                "SELECT constraint_name FROM information_schema.table_constraints "
                "WHERE table_schema = 'public' AND table_name = 'user_consent'"
            )
        )
        indexes = await connection.execute(
            text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = 'user_consent'")
        )
        return {row[0] for row in constraints}, {row[0] for row in indexes}


async def _create_user() -> str:
    user_id = str(uuid4())
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO "user"
                    (id, email, hashed_password, name, is_active, account_status, token_version, is_admin)
                    VALUES (:id, :email, 'synthetic-password-hash', '동의마이그레이션테스터',
                            true, 'ACTIVE', 0, false)
                    """
                ),
                {"id": user_id, "email": f"consent-migration-{uuid4().hex[:10]}@example.com"},
            )
    return user_id


async def _cleanup_user(user_id: str) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("DELETE FROM user_consent WHERE user_id = :user_id"), {"user_id": user_id})
            await connection.execute(text('DELETE FROM "user" WHERE id = :user_id'), {"user_id": user_id})


async def _insert_valid_consent(user_id: str) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO user_consent
                    (id, user_id, purpose, status, policy_version, granted_at)
                    VALUES (:id, :user_id, 'OCR', 'GRANTED', 'ocr-consent.v1', :now)
                    """
                ),
                {"id": str(uuid4()), "user_id": user_id, "now": datetime.now(UTC)},
            )


async def _assert_constraints(user_id: str) -> None:
    async with _connection() as connection:
        for sql, params in [
            (
                "INSERT INTO user_consent (id, user_id, purpose, status, policy_version, granted_at) "
                "VALUES (:id, :user_id, 'OCR', 'GRANTED', 'ocr-consent.v1', now())",
                {},
            ),
            (
                "INSERT INTO user_consent (id, user_id, purpose, status, policy_version, granted_at) "
                "VALUES (:id, :user_id, 'BAD', 'GRANTED', 'ocr-consent.v1', now())",
                {},
            ),
            (
                "INSERT INTO user_consent (id, user_id, purpose, status, policy_version, granted_at) "
                "VALUES (:id, :user_id, 'GUIDE', 'BAD', 'guide-consent.v1', now())",
                {},
            ),
            (
                "INSERT INTO user_consent (id, user_id, purpose, status, policy_version, granted_at) "
                "VALUES (:id, :user_id, 'GUIDE', 'GRANTED', '', now())",
                {},
            ),
            (
                "INSERT INTO user_consent (id, user_id, purpose, status, policy_version) "
                "VALUES (:id, :user_id, 'GUIDE', 'GRANTED', 'guide-consent.v1')",
                {},
            ),
            (
                "INSERT INTO user_consent (id, user_id, purpose, status, policy_version) "
                "VALUES (:id, :user_id, 'CHAT', 'WITHDRAWN', 'chat-consent.v1')",
                {},
            ),
        ]:
            transaction = await connection.begin()
            try:
                with pytest.raises(DBAPIError):
                    await connection.execute(text(sql), {"id": str(uuid4()), "user_id": user_id, **params})
            finally:
                await transaction.rollback()


def test_user_consent_downgrade_guard_unit(monkeypatch: pytest.MonkeyPatch) -> None:
    migration = _load_migration()
    fake_connection = FakeConnection(has_row=True)
    monkeypatch.setattr(migration.op, "get_bind", lambda: fake_connection)

    with pytest.raises(RuntimeError, match="Cannot downgrade user_consent"):
        migration._raise_if_user_consent_exists()

    assert fake_connection.statements[0] == "LOCK TABLE user_consent IN ACCESS EXCLUSIVE MODE"
    assert "SELECT 1 FROM user_consent" in fake_connection.statements[1]


def test_user_consent_migration_constraints_and_downgrade_guard() -> None:
    alembic_config = _alembic_config()
    asyncio.run(_ensure_pg_trgm_in_public())
    command.upgrade(alembic_config, "head")
    constraints, indexes = asyncio.run(_schema_snapshot())
    assert {
        "fk_user_consent_user",
        "uq_user_consent_user_purpose",
        "chk_user_consent_purpose",
        "chk_user_consent_status",
        "chk_user_consent_policy_version_non_empty",
        "chk_user_consent_status_timestamps",
    } <= constraints
    assert "idx_user_consent_user_status" in indexes

    user_id = asyncio.run(_create_user())
    try:
        asyncio.run(_insert_valid_consent(user_id))
        asyncio.run(_assert_constraints(user_id))
        with pytest.raises(RuntimeError, match="Cannot downgrade user_consent"):
            command.downgrade(alembic_config, USER_CONSENT_BASE_REVISION)
    finally:
        asyncio.run(_cleanup_user(user_id))
        command.upgrade(alembic_config, "head")

    try:
        command.downgrade(alembic_config, USER_CONSENT_BASE_REVISION)
        constraints, _ = asyncio.run(_schema_snapshot())
        assert constraints == set()
    finally:
        command.upgrade(alembic_config, "head")
