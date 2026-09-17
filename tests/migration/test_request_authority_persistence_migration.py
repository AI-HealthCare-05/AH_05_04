"""#713 REQUEST authority persistence migration의 DB 수준 불변식 검증.

`create_all` 기반 단위 테스트가 아니라 실제 Alembic migration이 만든 스키마에서
fail-closed 제약이 동작하는지 확인한다.
"""

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
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config  # type: ignore[import-untyped]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    PROJECT_ROOT / "backend" / "alembic" / "versions" / "713a1b2c3d4e_create_request_authority_persistence.py"
)

AUTHORITY_TABLES = (
    "rag_request_guard_authority",
    "rag_request_source_decision",
    "rag_request_member_decision",
)
GUARD_ARTIFACT_CODE = "request_guard_authority"
SOURCE_ARTIFACT_CODE = "request_source_decision_authority"
MEMBER_ARTIFACT_CODE = "request_member_decision_authority"
ARTIFACT_VERSION = "1.0"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("request_authority_persistence_migration", MIGRATION_PATH)
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
    database = f"authority713_{uuid4().hex[:12]}"
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


async def _seed_user(connection: AsyncConnection, user_id: str) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO "user" (id, email, hashed_password, name, is_active, account_status, token_version, is_admin)
            VALUES (:user_id, :email, 'synthetic-password-hash', 'migration713', true, 'ACTIVE', 0, false)
            """
        ),
        {"user_id": user_id, "email": f"migration713-{user_id[:8]}@example.com"},
    )


_INSERT_GUARD = text(
    """
    INSERT INTO rag_request_guard_authority (
        id, artifact_code, artifact_version, artifact_content_sha256,
        user_id, request_operation_code, decision_stage
    )
    VALUES (
        :id, :artifact_code, :artifact_version, :content_sha256,
        :user_id, :operation_code, :decision_stage
    )
    """
)

_INSERT_MEMBER = text(
    """
    INSERT INTO rag_request_member_decision (
        id, artifact_code, artifact_version, artifact_content_sha256,
        request_guard_artifact_code, request_guard_artifact_version, request_guard_content_sha256,
        user_id, request_operation_code, decision_stage,
        source_snapshot_id, source_snapshot_member_id, member_kind,
        endpoint_code, operation_code, member_artifact_code, member_artifact_version,
        actual_decision_outcome
    )
    VALUES (
        :id, :artifact_code, :artifact_version, :content_sha256,
        :guard_artifact_code, :guard_artifact_version, :guard_content_sha256,
        :user_id, :operation_code, 'REQUEST',
        :snapshot_id, :member_id, :member_kind,
        :endpoint_code, :member_operation_code, :member_artifact_code, :member_artifact_version,
        :outcome
    )
    """
)

_INSERT_SOURCE = text(
    """
    INSERT INTO rag_request_source_decision (
        id, artifact_code, artifact_version, artifact_content_sha256,
        request_guard_artifact_code, request_guard_artifact_version, request_guard_content_sha256,
        user_id, request_operation_code, decision_stage,
        source_snapshot_id, source_code, source_version, actual_decision_outcome
    )
    VALUES (
        :id, :artifact_code, :artifact_version, :content_sha256,
        :guard_artifact_code, :guard_artifact_version, :guard_content_sha256,
        :user_id, :operation_code, 'REQUEST',
        :snapshot_id, :source_code, :source_version, :outcome
    )
    """
)


def _guard_params(user_id: str, content_sha256: str, **overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "id": str(uuid4()),
        "artifact_code": GUARD_ARTIFACT_CODE,
        "artifact_version": ARTIFACT_VERSION,
        "content_sha256": content_sha256,
        "user_id": user_id,
        "operation_code": "GUIDE_SYNC_ANSWER",
        "decision_stage": "REQUEST",
    }
    params.update(overrides)
    return params


def _member_params(user_id: str, guard_sha256: str, **overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "id": str(uuid4()),
        "artifact_code": MEMBER_ARTIFACT_CODE,
        "artifact_version": ARTIFACT_VERSION,
        "content_sha256": "2" * 64,
        "guard_artifact_code": GUARD_ARTIFACT_CODE,
        "guard_artifact_version": ARTIFACT_VERSION,
        "guard_content_sha256": guard_sha256,
        "user_id": user_id,
        "operation_code": "GUIDE_SYNC_ANSWER",
        "snapshot_id": str(uuid4()),
        "member_id": str(uuid4()),
        "member_kind": "ENDPOINT_OPERATION",
        "endpoint_code": "MFDS_DUR",
        "member_operation_code": None,
        "member_artifact_code": None,
        "member_artifact_version": None,
        "outcome": "PASS",
    }
    params.update(overrides)
    return params


def _source_params(user_id: str, guard_sha256: str, **overrides: object) -> dict[str, object]:
    params: dict[str, object] = {
        "id": str(uuid4()),
        "artifact_code": SOURCE_ARTIFACT_CODE,
        "artifact_version": ARTIFACT_VERSION,
        "content_sha256": "3" * 64,
        "guard_artifact_code": GUARD_ARTIFACT_CODE,
        "guard_artifact_version": ARTIFACT_VERSION,
        "guard_content_sha256": guard_sha256,
        "user_id": user_id,
        "operation_code": "GUIDE_SYNC_ANSWER",
        "snapshot_id": str(uuid4()),
        "source_code": "MFDS",
        "source_version": "2026.09.01",
        "outcome": "PASS",
    }
    params.update(overrides)
    return params


async def _table_names() -> set[str]:
    async with _connection() as connection:
        result = await connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename = ANY(:names)"),
            {"names": list(AUTHORITY_TABLES)},
        )
        return {str(row[0]) for row in result}


async def _seeded_guard() -> tuple[str, str]:
    """migration된 DB에 사용자와 Guard 한 건을 남기고 (user_id, guard_sha256)을 돌려준다."""
    user_id = str(uuid4())
    guard_sha256 = "1" * 64
    async with _connection() as connection:
        async with connection.begin():
            await _seed_user(connection, user_id)
            await connection.execute(_INSERT_GUARD, _guard_params(user_id, guard_sha256))
    return user_id, guard_sha256


async def _expect_rejected(statement: Any, params: dict[str, object]) -> None:
    async with _connection() as connection:
        with pytest.raises((IntegrityError, DBAPIError)):
            async with connection.begin():
                await connection.execute(statement, params)


def test_migration_metadata_chains_to_single_parent_revision() -> None:
    migration = _load_migration()

    assert migration.revision == "713a1b2c3d4e"
    assert isinstance(migration.down_revision, str)
    assert migration.down_revision


def test_migration_creates_and_drops_all_authority_tables(isolated_authority_database: None) -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, "713a1b2c3d4e")

    assert asyncio.run(_table_names()) == set(AUTHORITY_TABLES)

    command.downgrade(cfg, _load_migration().down_revision)
    assert asyncio.run(_table_names()) == set()


def test_migrated_schema_enforces_authority_invariants(isolated_authority_database: None) -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, "713a1b2c3d4e")

    user_id, guard_sha256 = asyncio.run(_seeded_guard())

    # Guard: REQUEST 이외의 stage를 저장할 수 없다.
    asyncio.run(_expect_rejected(_INSERT_GUARD, _guard_params(user_id, "a" * 64, decision_stage="APPROVAL")))

    # Guard: 동일 artifact identity를 두 번 남길 수 없다.
    asyncio.run(_expect_rejected(_INSERT_GUARD, _guard_params(user_id, guard_sha256)))

    # Guard: artifact_code를 다른 authority 종류로 바꿔 넣을 수 없다.
    asyncio.run(_expect_rejected(_INSERT_GUARD, _guard_params(user_id, "b" * 64, artifact_code=SOURCE_ARTIFACT_CODE)))

    # Source Decision: PASS/FAIL 이외의 outcome을 저장할 수 없다.
    asyncio.run(_expect_rejected(_INSERT_SOURCE, _source_params(user_id, guard_sha256, outcome="UNKNOWN")))

    # Source Decision: 저장되지 않은 Guard를 참조할 수 없다.
    asyncio.run(_expect_rejected(_INSERT_SOURCE, _source_params(user_id, "c" * 64)))

    # Member Decision: ENDPOINT_OPERATION에 artifact 필드를 함께 담을 수 없다.
    asyncio.run(
        _expect_rejected(
            _INSERT_MEMBER,
            _member_params(user_id, guard_sha256, member_artifact_code="x", member_artifact_version="1"),
        )
    )

    # Member Decision: ARTIFACT는 endpoint 필드를 가질 수 없다.
    asyncio.run(
        _expect_rejected(
            _INSERT_MEMBER,
            _member_params(
                user_id,
                guard_sha256,
                member_kind="ARTIFACT",
                endpoint_code="MFDS_DUR",
                member_artifact_code="mfds_label_bundle",
                member_artifact_version="2026.09",
            ),
        )
    )

    # Member Decision: 지원하지 않는 member_kind를 저장할 수 없다.
    asyncio.run(_expect_rejected(_INSERT_MEMBER, _member_params(user_id, guard_sha256, member_kind="ENDPOINT")))


def test_migrated_schema_accepts_contract_member_identity_variants(isolated_authority_database: None) -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, "713a1b2c3d4e")

    user_id, guard_sha256 = asyncio.run(_seeded_guard())

    async def insert_variants() -> int:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    _INSERT_MEMBER,
                    _member_params(user_id, guard_sha256, content_sha256="4" * 64),
                )
                await connection.execute(
                    _INSERT_MEMBER,
                    _member_params(
                        user_id,
                        guard_sha256,
                        content_sha256="5" * 64,
                        member_operation_code="LIST",
                    ),
                )
                await connection.execute(
                    _INSERT_MEMBER,
                    _member_params(
                        user_id,
                        guard_sha256,
                        content_sha256="6" * 64,
                        member_kind="ARTIFACT",
                        endpoint_code=None,
                        member_artifact_code="mfds_label_bundle",
                        member_artifact_version="2026.09",
                    ),
                )
            result = await connection.execute(text("SELECT count(*) FROM rag_request_member_decision"))
            return int(result.scalar_one())

    assert asyncio.run(insert_variants()) == 3


def test_migrated_schema_blocks_guard_deletion_while_decisions_exist(isolated_authority_database: None) -> None:
    cfg = create_alembic_config()
    command.upgrade(cfg, "713a1b2c3d4e")

    user_id, guard_sha256 = asyncio.run(_seeded_guard())

    async def seed_source_decision() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(_INSERT_SOURCE, _source_params(user_id, guard_sha256))

    asyncio.run(seed_source_decision())

    async def delete_guard() -> None:
        async with _connection() as connection:
            with pytest.raises((IntegrityError, DBAPIError)):
                async with connection.begin():
                    await connection.execute(
                        text("DELETE FROM rag_request_guard_authority WHERE artifact_content_sha256 = :sha"),
                        {"sha": guard_sha256},
                    )

    asyncio.run(delete_guard())


async def _seed_full_authority_chain() -> tuple[str, str]:
    """Guard → Source Decision → Member Decision 한 체인을 실제로 남긴다."""
    user_id, guard_sha256 = await _seeded_guard()
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(_INSERT_SOURCE, _source_params(user_id, guard_sha256))
            await connection.execute(_INSERT_MEMBER, _member_params(user_id, guard_sha256))
    return user_id, guard_sha256


async def _authority_row_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    async with _connection() as connection:
        for table in AUTHORITY_TABLES:
            result = await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
            counts[table] = int(result.scalar_one())
    return counts


def test_downgrade_refuses_when_authority_data_exists(isolated_authority_database: None) -> None:
    """append-only historical 증거를 조용히 잃지 않는다."""
    cfg = create_alembic_config()
    command.upgrade(cfg, "713a1b2c3d4e")

    asyncio.run(_seed_full_authority_chain())
    assert asyncio.run(_authority_row_counts()) == {table: 1 for table in AUTHORITY_TABLES}

    with pytest.raises(RuntimeError) as error:
        command.downgrade(cfg, _load_migration().down_revision)

    message = str(error.value)
    assert "Refusing to downgrade request authority tables with existing data" in message
    for table in AUTHORITY_TABLES:
        assert table in message

    # 세 표가 drop되지 않고 기존 행도 그대로 남아 있어야 한다.
    assert asyncio.run(_table_names()) == set(AUTHORITY_TABLES)
    assert asyncio.run(_authority_row_counts()) == {table: 1 for table in AUTHORITY_TABLES}


def test_downgrade_guard_checks_every_authority_table(isolated_authority_database: None) -> None:
    """Guard만 남아 있어도(자식 Decision 없이) downgrade를 거부한다."""
    cfg = create_alembic_config()
    command.upgrade(cfg, "713a1b2c3d4e")

    asyncio.run(_seeded_guard())

    with pytest.raises(RuntimeError) as error:
        command.downgrade(cfg, _load_migration().down_revision)

    message = str(error.value)
    assert "rag_request_guard_authority" in message
    assert "rag_request_member_decision" not in message
    assert "rag_request_source_decision" not in message
    assert asyncio.run(_authority_row_counts())["rag_request_guard_authority"] == 1
