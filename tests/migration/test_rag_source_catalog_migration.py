"""RAG Source/Catalog Alembic schema and rollback guard tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
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
RAG_SOURCE_CATALOG_REVISION = "164f3a2b1c0d"
RAG_SOURCE_CATALOG_BASE_REVISION = "171c0f751206"


def create_alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


def create_alembic_database_url() -> str:
    return config.database_url


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(create_alembic_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


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


async def _count_table(table_name: str) -> int:
    async with _connection() as connection:
        result = await connection.execute(text(f"SELECT count(*) FROM {table_name}"))
        return int(result.scalar_one())


async def _fetch_schema_object_names() -> set[str]:
    async with _connection() as connection:
        constraints = await connection.execute(
            text(
                """
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name IN (
                    'rag_source_snapshot',
                    'rag_source_ingestion_run',
                    'rag_medication_product',
                    'rag_medication_ingredient',
                    'rag_medication_alias',
                    'rag_medication_product_component'
                  )
                """
            )
        )
        indexes = await connection.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename IN (
                    'rag_source_snapshot',
                    'rag_source_ingestion_run',
                    'rag_medication_product',
                    'rag_medication_ingredient',
                    'rag_medication_alias',
                    'rag_medication_product_component'
                  )
                """
            )
        )
        triggers = await connection.execute(
            text(
                """
                SELECT trigger_name
                FROM information_schema.triggers
                WHERE event_object_schema = 'public'
                  AND event_object_table = 'rag_source_snapshot'
                """
            )
        )
        return {
            *(row[0] for row in constraints),
            *(row[0] for row in indexes),
            *(row[0] for row in triggers),
        }


async def _seed_source_catalog_chain() -> dict[str, str]:
    ids = {
        "source_id": str(uuid4()),
        "endpoint_id": str(uuid4()),
        "operation_id": str(uuid4()),
        "snapshot_id": str(uuid4()),
        "verification_id": str(uuid4()),
        "ingestion_run_id": str(uuid4()),
        "product_id": str(uuid4()),
        "ingredient_id": str(uuid4()),
        "alias_id": str(uuid4()),
        "component_id": str(uuid4()),
    }
    checksum_a = "a" * 64
    checksum_b = "b" * 64
    collected_at = datetime.now(UTC)

    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source (
                        id, source_code, display_name, lifecycle_status
                    )
                    VALUES (:source_id, :source_code, 'MFDS Product Approval', 'ACTIVE')
                    """
                ),
                {"source_id": ids["source_id"], "source_code": f"MFDS_PRODUCT_{uuid4().hex[:10]}"},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source_endpoint (
                        id, source_id, endpoint_code, display_name,
                        lifecycle_status, runtime_status, acquisition_status
                    )
                    VALUES (
                        :endpoint_id, :source_id, 'PRODUCT_LIST', 'Product List',
                        'VERIFIED', 'DISABLED', 'APPROVED'
                    )
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source_operation (
                        id, endpoint_id, operation_code, display_name,
                        runtime_status, acquisition_status
                    )
                    VALUES (
                        :operation_id, :endpoint_id, 'LIST_PRODUCTS', 'List Products',
                        'DISABLED', 'APPROVED'
                    )
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source_snapshot (
                        id, operation_id, source_version, raw_manifest_checksum,
                        canonical_checksum, schema_version, parser_version,
                        normalization_version, canonicalization_spec_version,
                        record_count, rejected_record_count, verification_status,
                        collected_at
                    )
                    VALUES (
                        :snapshot_id, :operation_id, :source_version, :checksum_a,
                        :checksum_b, 'schema-v1', 'parser-v1',
                        'normalization-v1', 'canonical-v1',
                        1, 0, 'CURRENT', :collected_at
                    )
                    """
                ),
                {
                    **ids,
                    "source_version": f"api:2026-09-07:{uuid4().hex[:8]}",
                    "checksum_a": checksum_a,
                    "checksum_b": checksum_b,
                    "collected_at": collected_at,
                },
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source_snapshot_verification (
                        id, snapshot_id, check_name, verification_result, verified_at
                    )
                    VALUES (:verification_id, :snapshot_id, 'checksum', 'PASSED', :collected_at)
                    """
                ),
                {**ids, "collected_at": collected_at},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source_ingestion_run (
                        id, operation_id, snapshot_id, run_status, attempt_number, started_at
                    )
                    VALUES (:ingestion_run_id, :operation_id, :snapshot_id, 'SUCCEEDED', 1, :collected_at)
                    """
                ),
                {**ids, "collected_at": collected_at},
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_medication_product (
                        id, source_snapshot_id, source_record_key, code_system,
                        canonical_code, product_name, normalized_product_name, product_status
                    )
                    VALUES (
                        :product_id, :snapshot_id, 'ITEM_SEQ:200000001', 'MFDS_ITEM_SEQ',
                        '200000001', '테스트정', '테스트정', 'ACTIVE'
                    )
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_medication_ingredient (
                        id, source_snapshot_id, source_record_key, ingredient_code_system,
                        ingredient_code, ingredient_name, normalized_ingredient_name
                    )
                    VALUES (
                        :ingredient_id, :snapshot_id, 'INGREDIENT:ACETAMINOPHEN',
                        'MFDS_INGREDIENT', 'I0001', '아세트아미노펜', '아세트아미노펜'
                    )
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_medication_alias (
                        id, source_snapshot_id, product_id, target_type,
                        alias_text, normalized_alias_text, is_approved
                    )
                    VALUES (
                        :alias_id, :snapshot_id, :product_id, 'PRODUCT',
                        '테스트 별칭', '테스트별칭', true
                    )
                    """
                ),
                ids,
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_medication_product_component (
                        id, source_snapshot_id, product_id, ingredient_id,
                        component_role, display_order
                    )
                    VALUES (
                        :component_id, :snapshot_id, :product_id, :ingredient_id,
                        'ACTIVE_INGREDIENT', 1
                    )
                    """
                ),
                ids,
            )

    return ids


async def _create_stale_snapshot_for_same_operation(ids: dict[str, str]) -> str:
    stale_snapshot_id = str(uuid4())
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO rag_source_snapshot (
                        id, operation_id, source_version, raw_manifest_checksum,
                        canonical_checksum, schema_version, parser_version,
                        normalization_version, canonicalization_spec_version,
                        record_count, rejected_record_count, verification_status,
                        collected_at
                    )
                    VALUES (
                        :stale_snapshot_id, :operation_id, :source_version, :checksum_a,
                        :checksum_b, 'schema-v1', 'parser-v1',
                        'normalization-v1', 'canonical-v1',
                        1, 0, 'STALE', :collected_at
                    )
                    """
                ),
                {
                    **ids,
                    "stale_snapshot_id": stale_snapshot_id,
                    "source_version": f"api:stale:{uuid4().hex[:8]}",
                    "checksum_a": "d" * 64,
                    "checksum_b": "e" * 64,
                    "collected_at": datetime.now(UTC),
                },
            )
    return stale_snapshot_id


async def _cleanup_source_catalog_chain(ids: dict[str, str]) -> None:
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text("ALTER TABLE rag_source_snapshot DISABLE TRIGGER trg_rag_source_snapshot_prevent_delete")
            )
            await connection.execute(
                text("ALTER TABLE rag_source_snapshot DISABLE TRIGGER trg_rag_source_snapshot_prevent_update")
            )
            try:
                await connection.execute(
                    text(
                        "DELETE FROM rag_medication_product_component WHERE source_snapshot_id IN (SELECT id FROM rag_source_snapshot WHERE operation_id = :operation_id)"
                    ),
                    ids,
                )
                await connection.execute(
                    text(
                        "DELETE FROM rag_medication_alias WHERE source_snapshot_id IN (SELECT id FROM rag_source_snapshot WHERE operation_id = :operation_id)"
                    ),
                    ids,
                )
                await connection.execute(
                    text(
                        "DELETE FROM rag_medication_ingredient WHERE source_snapshot_id IN (SELECT id FROM rag_source_snapshot WHERE operation_id = :operation_id)"
                    ),
                    ids,
                )
                await connection.execute(
                    text(
                        "DELETE FROM rag_medication_product WHERE source_snapshot_id IN (SELECT id FROM rag_source_snapshot WHERE operation_id = :operation_id)"
                    ),
                    ids,
                )
                await connection.execute(
                    text(
                        "DELETE FROM rag_source_snapshot_verification WHERE snapshot_id IN (SELECT id FROM rag_source_snapshot WHERE operation_id = :operation_id)"
                    ),
                    ids,
                )
                await connection.execute(
                    text("DELETE FROM rag_source_ingestion_run WHERE operation_id = :operation_id"), ids
                )
                await connection.execute(
                    text("DELETE FROM rag_source_snapshot WHERE operation_id = :operation_id"), ids
                )
                await connection.execute(text("DELETE FROM rag_source_operation WHERE id = :operation_id"), ids)
                await connection.execute(text("DELETE FROM rag_source_endpoint WHERE id = :endpoint_id"), ids)
                await connection.execute(text("DELETE FROM rag_source WHERE id = :source_id"), ids)
            finally:
                await connection.execute(
                    text("ALTER TABLE rag_source_snapshot ENABLE TRIGGER trg_rag_source_snapshot_prevent_update")
                )
                await connection.execute(
                    text("ALTER TABLE rag_source_snapshot ENABLE TRIGGER trg_rag_source_snapshot_prevent_delete")
                )


async def _execute_expect_db_error(sql: str, params: Mapping[str, object]) -> None:
    async with _connection() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError):
                await connection.execute(text(sql), params)
        finally:
            await transaction.rollback()


def test_rag_source_catalog_empty_downgrade_roundtrips() -> None:
    alembic_config = create_alembic_config()

    try:
        command.upgrade(alembic_config, "head")

        command.downgrade(alembic_config, RAG_SOURCE_CATALOG_BASE_REVISION)
        assert asyncio.run(_table_exists("rag_source")) is False

        command.upgrade(alembic_config, "head")
        assert asyncio.run(_table_exists("rag_source")) is True
    finally:
        command.upgrade(alembic_config, "head")


def test_rag_source_catalog_schema_constraints_exist_after_alembic_upgrade() -> None:
    alembic_config = create_alembic_config()

    command.upgrade(alembic_config, "head")

    schema_objects = asyncio.run(_fetch_schema_object_names())

    assert "uq_rag_source_snapshot_current" in schema_objects
    assert "chk_rag_source_snapshot_rejected_record_count_lte_record_count" in schema_objects
    assert "uq_rag_source_ingestion_run_attempt" in schema_objects
    assert "uq_rag_medication_product_id_snapshot" in schema_objects
    assert "uq_rag_medication_ingredient_id_snapshot" in schema_objects
    assert "fk_rag_medication_alias_product_snapshot" in schema_objects
    assert "fk_rag_medication_alias_ingredient_snapshot" in schema_objects
    assert "fk_rag_medication_component_product_snapshot" in schema_objects
    assert "fk_rag_medication_component_ingredient_snapshot" in schema_objects
    assert "trg_rag_source_snapshot_prevent_update" in schema_objects
    assert "trg_rag_source_snapshot_prevent_delete" in schema_objects


def test_rag_source_catalog_unique_constraints_are_enforced_after_alembic_upgrade() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        ids = asyncio.run(_seed_source_catalog_chain())

        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO rag_source_snapshot (
                    id, operation_id, source_version, raw_manifest_checksum,
                    canonical_checksum, schema_version, parser_version,
                    normalization_version, canonicalization_spec_version,
                    record_count, rejected_record_count, verification_status,
                    collected_at
                )
                VALUES (
                    :duplicate_current_snapshot_id, :operation_id, :source_version, :checksum_a,
                    :checksum_b, 'schema-v1', 'parser-v1',
                    'normalization-v1', 'canonical-v1',
                    1, 0, 'CURRENT', :collected_at
                )
                """,
                {
                    **ids,
                    "duplicate_current_snapshot_id": str(uuid4()),
                    "source_version": f"api:duplicate-current:{uuid4().hex[:8]}",
                    "checksum_a": "f" * 64,
                    "checksum_b": "0" * 64,
                    "collected_at": datetime.now(UTC),
                },
            )
        )
        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO rag_medication_product (
                    id, source_snapshot_id, source_record_key, code_system,
                    canonical_code, product_name, normalized_product_name, product_status
                )
                VALUES (
                    :duplicate_product_id, :snapshot_id, 'ITEM_SEQ:DUPLICATE',
                    'MFDS_ITEM_SEQ', '200000001',
                    '중복제품', '중복제품', 'ACTIVE'
                )
                """,
                {**ids, "duplicate_product_id": str(uuid4())},
            )
        )
    finally:
        command.upgrade(alembic_config, "head")
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))


def test_rag_source_catalog_snapshot_is_append_only_in_alembic_schema() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        ids = asyncio.run(_seed_source_catalog_chain())

        asyncio.run(
            _execute_expect_db_error(
                """
                UPDATE rag_source_snapshot
                SET canonical_checksum = :changed_checksum
                WHERE id = :snapshot_id
                """,
                {**ids, "changed_checksum": "c" * 64},
            )
        )
        asyncio.run(
            _execute_expect_db_error(
                """
                DELETE FROM rag_source_snapshot
                WHERE id = :snapshot_id
                """,
                ids,
            )
        )
    finally:
        command.upgrade(alembic_config, "head")
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))


def test_rag_source_catalog_downgrade_blocks_non_empty_tables_and_preserves_data() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        ids = asyncio.run(_seed_source_catalog_chain())

        with pytest.raises(RuntimeError, match="Cannot downgrade revision 164f3a2b1c0d"):
            command.downgrade(alembic_config, RAG_SOURCE_CATALOG_BASE_REVISION)

        assert asyncio.run(_table_exists("rag_source_snapshot")) is True
        assert asyncio.run(_count_table("rag_source")) >= 1
    finally:
        command.upgrade(alembic_config, "head")
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))


def test_rag_source_catalog_cross_snapshot_fk_is_enforced_after_alembic_upgrade() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        ids = asyncio.run(_seed_source_catalog_chain())

        stale_snapshot_id = asyncio.run(_create_stale_snapshot_for_same_operation(ids))

        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO rag_medication_alias (
                    id, source_snapshot_id, product_id, target_type,
                    alias_text, normalized_alias_text, is_approved
                )
                VALUES (
                    :bad_alias_id, :stale_snapshot_id, :product_id, 'PRODUCT',
                    '잘못된 별칭', '잘못된별칭', true
                )
                """,
                {
                    **ids,
                    "bad_alias_id": str(uuid4()),
                    "stale_snapshot_id": stale_snapshot_id,
                },
            )
        )
        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO rag_medication_product_component (
                    id, source_snapshot_id, product_id, ingredient_id,
                    component_role, display_order
                )
                VALUES (
                    :bad_component_id, :stale_snapshot_id, :product_id, :ingredient_id,
                    'ACTIVE_INGREDIENT', 2
                )
                """,
                {
                    **ids,
                    "bad_component_id": str(uuid4()),
                    "stale_snapshot_id": stale_snapshot_id,
                },
            )
        )
    finally:
        command.upgrade(alembic_config, "head")
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
