from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_INDEX_REVISION = "583a1b2c3d4f"
CANDIDATE_INDEX_BASE_REVISION = "166f50617283"
CANDIDATE_INDEX_TABLES = {"rag_candidate_index_version", "rag_candidate_index_member"}
READY_INDEX_NAME = "uq_rag_candidate_index_ready_per_code"


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


def _upgrade_to_candidate_index() -> None:
    command.upgrade(create_alembic_config(), CANDIDATE_INDEX_REVISION)


async def _table_exists(table_name: str) -> bool:
    async with _connection() as connection:
        result = await connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                      AND table_name = :table_name
                )
                """
            ),
            {"table_name": table_name},
        )
        return bool(result.scalar_one())


async def _index_exists(index_name: str) -> bool:
    async with _connection() as connection:
        result = await connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND indexname = :index_name
                )
                """
            ),
            {"index_name": index_name},
        )
        return bool(result.scalar_one())


async def _candidate_index_version_count() -> int:
    async with _connection() as connection:
        result = await connection.execute(text("SELECT COUNT(*) FROM rag_candidate_index_version"))
        return int(result.scalar_one())


async def _seed_candidate_index_version() -> str:
    catalog_set_id = str(uuid4())
    candidate_index_version_id = str(uuid4())
    suffix = uuid4().hex[:10]
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_catalog_set (
                        id, catalog_version, schema_version, normalization_version,
                        manifest_spec_version, envelope_hash, manifest_json
                    )
                    VALUES (
                        :catalog_set_id, 'catalog-1.0.0', 'schema-v1', 'normalization-v1',
                        :manifest_spec_version, :envelope_hash, :manifest_json
                    )
                    """
                ),
                {
                    "catalog_set_id": catalog_set_id,
                    "manifest_spec_version": f"candidate-index-migration-{suffix}",
                    "envelope_hash": "a" * 64,
                    "manifest_json": b"{}",
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_candidate_index_version (
                        id, index_code, index_version, status, build_mode, catalog_set_id,
                        catalog_version, catalog_manifest_hash, schema_version, normalization_version,
                        lexical_config_version, search_order_version, candidate_limit, display_limit,
                        member_count, product_identity_count, product_name_count, approved_alias_count,
                        vector_count, member_set_hash, configuration_hash, content_hash
                    )
                    VALUES (
                        :candidate_index_version_id, :index_code, 'v1', 'BUILDING', 'LEXICAL_ONLY',
                        :catalog_set_id, 'catalog-1.0.0', :envelope_hash, 'schema-v1', 'normalization-v1',
                        'lexical-v1', 'search-order-v1', 20, 10,
                        0, 0, 0, 0, 0, :member_set_hash, :configuration_hash, :content_hash
                    )
                    """
                ),
                {
                    "candidate_index_version_id": candidate_index_version_id,
                    "index_code": f"migration-index-{suffix}",
                    "catalog_set_id": catalog_set_id,
                    "envelope_hash": "a" * 64,
                    "member_set_hash": "b" * 64,
                    "configuration_hash": "c" * 64,
                    "content_hash": "d" * 64,
                },
            )
    return candidate_index_version_id


async def _cleanup_candidate_index_tables() -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("DELETE FROM rag_candidate_index_member"))
            await connection.execute(text("DELETE FROM rag_candidate_index_version"))
            await connection.execute(
                text("DELETE FROM rag_catalog_set WHERE manifest_spec_version LIKE 'candidate-index-migration-%'")
            )


def test_candidate_index_ready_partial_unique_exists_after_upgrade() -> None:
    _upgrade_to_candidate_index()

    assert asyncio.run(_index_exists(READY_INDEX_NAME))


def test_candidate_index_empty_downgrade_removes_tables() -> None:
    cfg = create_alembic_config()
    _upgrade_to_candidate_index()
    asyncio.run(_cleanup_candidate_index_tables())

    command.downgrade(cfg, CANDIDATE_INDEX_BASE_REVISION)

    assert not asyncio.run(_table_exists("rag_candidate_index_version"))
    assert not asyncio.run(_table_exists("rag_candidate_index_member"))
    command.upgrade(cfg, CANDIDATE_INDEX_REVISION)


def test_candidate_index_downgrade_blocks_when_data_exists_and_preserves_schema() -> None:
    cfg = create_alembic_config()
    _upgrade_to_candidate_index()
    asyncio.run(_cleanup_candidate_index_tables())
    asyncio.run(_seed_candidate_index_version())

    try:
        with pytest.raises(RuntimeError, match="Candidate Index build records exist"):
            command.downgrade(cfg, CANDIDATE_INDEX_BASE_REVISION)

        assert asyncio.run(_table_exists("rag_candidate_index_version"))
        assert asyncio.run(_table_exists("rag_candidate_index_member"))
        assert asyncio.run(_candidate_index_version_count()) == 1
    finally:
        asyncio.run(_cleanup_candidate_index_tables())
        command.downgrade(cfg, CANDIDATE_INDEX_BASE_REVISION)
        command.upgrade(cfg, CANDIDATE_INDEX_REVISION)
