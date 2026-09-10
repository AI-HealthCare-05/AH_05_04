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
    PROJECT_ROOT / "backend" / "alembic" / "versions" / "206a1b2c3d4e_add_refresh_rotation_password_reset.py"
)
REFRESH_ROTATION_REVISION = "206a1b2c3d4e"
REFRESH_ROTATION_BASE_REVISION = "201a1b2c3d4e"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location(
        "refresh_rotation_password_reset_migration",
        MIGRATION_PATH,
    )

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
    """`FROM <table>`이 포함된 SELECT만 테이블별 count로 응답하고, LOCK 문 등
    그 외 statement는 0을 돌려준다 — 두 테이블의 count가 서로 달라도(비대칭
    데이터) guard가 올바르게 반응하는지 확인하기 위해 테이블별로 값을 구분한다."""

    def __init__(self, counts: dict[str, int]) -> None:
        self._counts = counts
        self.execute_count = 0

    def execute(self, statement: object) -> ScalarResult:
        self.execute_count += 1
        text_str = str(statement)
        for table, count in self._counts.items():
            if f"FROM {table}" in text_str:
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


async def _create_user() -> str:
    user_id = str(uuid4())
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO "user" (id, email, hashed_password, name, is_active, is_admin)
                    VALUES (:id, :email, 'synthetic-password-hash', '리프레시다운그레이드테스터', true, false)
                    """
                ),
                {"id": user_id, "email": f"refresh-downgrade-{uuid4().hex[:10]}@example.com"},
            )
    return user_id


async def _seed_refresh_session(user_id: str) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text("INSERT INTO refresh_session (id, user_id, active_jti) VALUES (:id, :user_id, :jti)"),
                {"id": str(uuid4()), "user_id": user_id, "jti": uuid4().hex[:32]},
            )


async def _cleanup_user(user_id: str) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("DELETE FROM refresh_session WHERE user_id = :user_id"), {"user_id": user_id})
            await connection.execute(text('DELETE FROM "user" WHERE id = :user_id'), {"user_id": user_id})


def test_downgrade_guard_rejects_when_password_reset_token_has_data() -> None:
    migration = _load_migration()
    connection = FakeConnection({"password_reset_token": 3, "refresh_session": 0})

    with pytest.raises(RuntimeError, match="Cannot downgrade revision 206a1b2c3d4e"):
        migration._ensure_refresh_rotation_downgrade_is_data_safe(connection)

    # LOCK TABLE + SELECT count(*) x2 — 세 statement 모두 실제로 실행됐는지 확인한다.
    assert connection.execute_count == 3


def test_downgrade_guard_rejects_when_only_refresh_session_has_data() -> None:
    """PR #404 리뷰: password_reset_token만 검사하던 이전 guard는 그 테이블이
    비어 있으면 refresh_session에만 로그인 세션 row가 남아 있어도 검사를
    통과시켰다 — 두 테이블을 모두 검사하는지 확인한다."""
    migration = _load_migration()
    connection = FakeConnection({"password_reset_token": 0, "refresh_session": 5})

    with pytest.raises(RuntimeError, match="Cannot downgrade revision 206a1b2c3d4e"):
        migration._ensure_refresh_rotation_downgrade_is_data_safe(connection)

    assert connection.execute_count == 3


def test_downgrade_guard_allows_when_both_tables_empty() -> None:
    migration = _load_migration()
    connection = FakeConnection({"password_reset_token": 0, "refresh_session": 0})

    migration._ensure_refresh_rotation_downgrade_is_data_safe(connection)

    assert connection.execute_count == 3


def test_downgrade_blocks_real_alembic_run_when_only_refresh_session_has_data(monkeypatch) -> None:
    """Use a disposable #404-era DB: current head deliberately cannot downgrade past #398."""
    database = f"refresh404_{uuid4().hex[:12]}"
    cluster_url = config.database_url

    async def database_action(create: bool) -> None:
        engine = create_async_engine(cluster_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.execute(
                    text(f'CREATE DATABASE "{database}"' if create else f'DROP DATABASE "{database}" WITH (FORCE)')
                )
        finally:
            await engine.dispose()

    asyncio.run(database_action(True))
    monkeypatch.setattr(config, "DB_NAME", database)
    try:
        alembic_config = _alembic_config()
        command.upgrade(alembic_config, REFRESH_ROTATION_REVISION)
        user_id = asyncio.run(_create_user())
        asyncio.run(_seed_refresh_session(user_id))
        with pytest.raises(RuntimeError, match=f"Cannot downgrade revision {REFRESH_ROTATION_REVISION}"):
            command.downgrade(alembic_config, REFRESH_ROTATION_BASE_REVISION)
        asyncio.run(_cleanup_user(user_id))
        command.downgrade(alembic_config, REFRESH_ROTATION_BASE_REVISION)
        command.upgrade(alembic_config, REFRESH_ROTATION_REVISION)
    finally:
        asyncio.run(database_action(False))
