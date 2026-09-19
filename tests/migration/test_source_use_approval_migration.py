"""#807 migration metadata and schema-definition parity checks."""

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

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "backend" / "alembic" / "versions" / "807a1b2c3d4e_source_use_approval.py"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("source_use_approval_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("#807 migration module을 불러올 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(
        config.database_url,
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
def isolated_source_use_approval_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    database = f"source_use_approval807_{uuid4().hex[:12]}"
    cluster_url = config.database_url

    async def database_action(create: bool) -> None:
        engine = create_async_engine(
            cluster_url,
            isolation_level="AUTOCOMMIT",
            poolclass=NullPool,
        )
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


async def _approval_table_exists() -> bool:
    async with _connection() as connection:
        result = await connection.scalar(text("SELECT to_regclass('public.rag_source_use_approval')"))
        return result is not None


async def _approval_row_count() -> int:
    async with _connection() as connection:
        result = await connection.scalar(text("SELECT count(*) FROM rag_source_use_approval"))
        return int(result)


async def _current_revision() -> str:
    async with _connection() as connection:
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        return str(revision)


async def _seed_source_use_approval() -> str:
    user_id = str(uuid4())
    source_id = str(uuid4())
    endpoint_id = str(uuid4())
    operation_id = str(uuid4())
    snapshot_id = str(uuid4())
    approval_id = str(uuid4())

    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO "user" (
                        id,
                        email,
                        hashed_password,
                        name,
                        is_active,
                        account_status,
                        token_version,
                        is_admin
                    )
                    VALUES (
                        :user_id,
                        :email,
                        'synthetic-password-hash',
                        'Migration807',
                        true,
                        'ACTIVE',
                        0,
                        false
                    )
                    """
                ),
                {"user_id": user_id, "email": f"migration807-{user_id[:8]}@example.com"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source (
                        id,
                        source_code,
                        display_name,
                        lifecycle_status
                    )
                    VALUES (
                        :source_id,
                        'SOURCE_807_DOWNGRADE',
                        'Synthetic Source',
                        'DRAFT'
                    )
                    """
                ),
                {"source_id": source_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source_endpoint (
                        id,
                        source_id,
                        endpoint_code,
                        display_name,
                        lifecycle_status,
                        runtime_status,
                        acquisition_status
                    )
                    VALUES (
                        :endpoint_id,
                        :source_id,
                        'ENDPOINT',
                        'Synthetic Endpoint',
                        'DRAFT',
                        'DISABLED',
                        'PENDING'
                    )
                    """
                ),
                {"endpoint_id": endpoint_id, "source_id": source_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source_operation (
                        id,
                        endpoint_id,
                        operation_code,
                        display_name,
                        runtime_status,
                        acquisition_status
                    )
                    VALUES (
                        :operation_id,
                        :endpoint_id,
                        'OPERATION',
                        'Synthetic Operation',
                        'DISABLED',
                        'PENDING'
                    )
                    """
                ),
                {"operation_id": operation_id, "endpoint_id": endpoint_id},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source_snapshot (
                        id,
                        operation_id,
                        source_version,
                        raw_manifest_checksum,
                        canonical_checksum,
                        schema_version,
                        parser_version,
                        normalization_version,
                        canonicalization_spec_version,
                        record_count,
                        rejected_record_count,
                        verification_status,
                        collected_at
                    )
                    VALUES (
                        :snapshot_id,
                        :operation_id,
                        'version-807',
                        :raw_hash,
                        :canonical_hash,
                        'schema-v1',
                        'parser-v1',
                        'normalization-v1',
                        'canonical-v1',
                        1,
                        0,
                        'PENDING',
                        now()
                    )
                    """
                ),
                {
                    "snapshot_id": snapshot_id,
                    "operation_id": operation_id,
                    "raw_hash": "a" * 64,
                    "canonical_hash": "b" * 64,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source_use_approval (
                        id,
                        source_snapshot_id,
                        source_code,
                        source_version,
                        environment,
                        purpose,
                        approval_version,
                        valid_from,
                        expires_at,
                        actor_id,
                        evidence_ref
                    )
                    VALUES (
                        :approval_id,
                        :snapshot_id,
                        'SOURCE_807_DOWNGRADE',
                        'version-807',
                        'PRODUCTION',
                        'PATIENT_CITATION',
                        'approval-1',
                        now(),
                        now() + interval '1 day',
                        :user_id,
                        'evidence://807/downgrade-test'
                    )
                    """
                ),
                {
                    "approval_id": approval_id,
                    "snapshot_id": snapshot_id,
                    "user_id": user_id,
                },
            )
    return approval_id


def test_migration_has_expected_revision_and_single_parent() -> None:
    migration = _load_migration()

    assert migration.revision == "807a1b2c3d4e"
    assert migration.down_revision == "8f1c2d3e4a5b"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_migration_declares_exact_source_use_approval_constraints() -> None:
    migration_text = MIGRATION_PATH.read_text(encoding="utf-8")

    expected_fragments = (
        "source_snapshot_id",
        "source_code",
        "source_version",
        "environment IN ('LOCAL', 'TEST', 'CLOSED_DEMO', 'PRODUCTION')",
        "purpose IN ('PRODUCT_IDENTIFICATION', 'SAFETY_ROUTING', 'RULE_DERIVATION', 'RETRIEVAL', 'PATIENT_CITATION')",
        "approval_version",
        "expires_at > valid_from",
        "chk_rag_source_use_approval_revocation_shape",
        "fk_rag_source_use_approval_snapshot_version",
        "LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE",
    )
    for fragment in expected_fragments:
        assert fragment in migration_text


@pytest.mark.parametrize(
    ("forbidden",),
    (("CREATE " + "TRIGGER",), ("CREATE " + "POLICY",), ("CREATE " + "FUNCTION",)),
)
def test_migration_does_not_add_database_business_logic(forbidden: str) -> None:
    assert forbidden not in MIGRATION_PATH.read_text(encoding="utf-8").upper()


def test_downgrade_refuses_when_source_use_approval_data_exists(
    isolated_source_use_approval_database: None,
) -> None:
    cfg = create_alembic_config()
    migration = _load_migration()

    command.upgrade(cfg, migration.revision)

    asyncio.run(_seed_source_use_approval())

    assert asyncio.run(_approval_table_exists())
    assert asyncio.run(_approval_row_count()) == 1

    with pytest.raises(
        RuntimeError,
        match="Refusing to downgrade Source Use Approval records with existing data",
    ):
        command.downgrade(
            cfg,
            migration.down_revision,
        )

    assert asyncio.run(_approval_table_exists())
    assert asyncio.run(_approval_row_count()) == 1
    assert asyncio.run(_current_revision()) == migration.revision


def test_empty_source_use_approval_table_can_downgrade_and_reupgrade(
    isolated_source_use_approval_database: None,
) -> None:
    cfg = create_alembic_config()
    migration = _load_migration()

    command.upgrade(cfg, migration.revision)

    assert asyncio.run(_approval_table_exists())
    assert asyncio.run(_approval_row_count()) == 0

    command.downgrade(cfg, migration.down_revision)

    assert not asyncio.run(_approval_table_exists())
    assert asyncio.run(_current_revision()) == migration.down_revision

    command.upgrade(cfg, migration.revision)

    assert asyncio.run(_approval_table_exists())
    assert asyncio.run(_approval_row_count()) == 0
    assert asyncio.run(_current_revision()) == migration.revision
