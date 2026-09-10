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
# 166a7b8c9d0e의 직전 revision. develop head가 바뀌면 함께 갱신한다.
CATALOG_IDENTITY_BASE_REVISION = "164b6c7d8e9f"
CATALOG_IDENTITY_REVISION = "166a7b8c9d0e"
CATALOG_MIGRATION_PATHS = (
    PROJECT_ROOT / "backend/alembic/versions/166a7b8c9d0e_add_catalog_identity_alias_search.py",
    PROJECT_ROOT / "backend/alembic/versions/166b8c9d0e1f_add_catalog_set_manifest.py",
)


def test_catalog_migrations_do_not_define_database_triggers_or_functions() -> None:
    forbidden_fragments = (
        "create trigger",
        "create function",
        "returns trigger",
        "language plpgsql",
        "assembly_xid",
    )
    for path in CATALOG_MIGRATION_PATHS:
        source = path.read_text(encoding="utf-8").lower()
        assert all(fragment not in source for fragment in forbidden_fragments), path


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
                    'rag_source_ingestion_artifact',
                    'rag_medication_product',
                    'rag_medication_ingredient',
                    'rag_medication_alias',
                    'rag_medication_product_component',
                    'rag_entity_identity',
                    'rag_medication_search_entry'
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
                    'rag_source_ingestion_artifact',
                    'rag_medication_product',
                    'rag_medication_ingredient',
                    'rag_medication_alias',
                    'rag_medication_product_component',
                    'rag_entity_identity',
                    'rag_medication_search_entry'
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
                  AND event_object_table IN (
                    'rag_source_snapshot',
                    'rag_source_ingestion_artifact',
                    'rag_medication_search_entry'
                  )
                """
            )
        )
        return {
            *(row[0] for row in constraints),
            *(row[0] for row in indexes),
            *(row[0] for row in triggers),
        }


async def _seed_source_catalog_chain(
    *, include_verification: bool = True, status: str = "CURRENT", rejected_count: int = 0
) -> dict[str, str]:
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
        "product_identity_id": str(uuid4()),
        "ingredient_identity_id": str(uuid4()),
    }
    checksum_a = "a" * 64
    checksum_b = "b" * 64
    collected_at = datetime.now(UTC)
    ids["source_version"] = f"api:2026-09-07:{uuid4().hex[:8]}"

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
                        1, :rejected_count, :status, :collected_at
                    )
                    """
                ),
                {
                    **ids,
                    "checksum_a": checksum_a,
                    "checksum_b": checksum_b,
                    "collected_at": collected_at,
                    "status": status,
                    "rejected_count": rejected_count,
                },
            )
            if include_verification:
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
                        id, operation_id, run_group_key, snapshot_id, run_status, attempt_number, started_at
                    )
                    VALUES (:ingestion_run_id, :operation_id, 'initial-load', :snapshot_id, 'SUCCEEDED', 1, :collected_at)
                    """
                ),
                {**ids, "collected_at": collected_at},
            )
            has_stable_identity = bool(
                (
                    await connection.execute(text("SELECT to_regclass('public.rag_entity_identity') IS NOT NULL"))
                ).scalar_one()
            )
            if has_stable_identity:
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_entity_identity (id, entity_type, code_system, canonical_code)
                        VALUES
                            (:product_identity_id, 'PRODUCT', 'MFDS_ITEM_SEQ', '200000001'),
                            (:ingredient_identity_id, 'INGREDIENT', 'MFDS_INGREDIENT', 'I0001')
                        """
                    ),
                    ids,
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_medication_product (
                            id, source_snapshot_id, entity_identity_id, source_record_key, code_system,
                            canonical_code, product_name, normalized_product_name, product_status
                        ) VALUES (
                            :product_id, :snapshot_id, :product_identity_id, 'ITEM_SEQ:200000001',
                            'MFDS_ITEM_SEQ', '200000001', '테스트정', '테스트정', 'ACTIVE'
                        )
                        """
                    ),
                    ids,
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_medication_ingredient (
                            id, source_snapshot_id, entity_identity_id, source_record_key,
                            ingredient_code_system, ingredient_code, ingredient_name,
                            normalized_ingredient_name
                        ) VALUES (
                            :ingredient_id, :snapshot_id, :ingredient_identity_id,
                            'INGREDIENT:ACETAMINOPHEN', 'MFDS_INGREDIENT', 'I0001',
                            '아세트아미노펜', '아세트아미노펜'
                        )
                        """
                    ),
                    ids,
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_medication_alias (
                            id, source_snapshot_id, target_identity_id, target_type, alias_text,
                            normalized_alias_text, alias_source, review_status, record_status, is_effective
                        ) VALUES (
                            :alias_id, :snapshot_id, :product_identity_id, 'PRODUCT', '테스트 별칭',
                            '테스트별칭', 'SYNTHETIC', 'APPROVED', 'ACTIVE', true
                        )
                        """
                    ),
                    ids,
                )
            else:
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_medication_product (
                            id, source_snapshot_id, source_record_key, code_system,
                            canonical_code, product_name, normalized_product_name, product_status
                        ) VALUES (
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
                        ) VALUES (
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
                        ) VALUES (
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
            await connection.execute(
                text(
                    "ALTER TABLE rag_source_ingestion_artifact "
                    "DISABLE TRIGGER trg_rag_source_ingestion_artifact_prevent_delete"
                )
            )
            await connection.execute(
                text(
                    "ALTER TABLE rag_source_snapshot_verification DISABLE TRIGGER trg_rag_snapshot_verification_immutable"
                )
            )
            try:
                has_search_entry = bool(
                    (
                        await connection.execute(
                            text("SELECT to_regclass('public.rag_medication_search_entry') IS NOT NULL")
                        )
                    ).scalar_one()
                )
                if has_search_entry:
                    await connection.execute(
                        text(
                            "DELETE FROM rag_medication_search_entry WHERE product_id IN "
                            "(SELECT id FROM rag_medication_product WHERE source_snapshot_id IN "
                            "(SELECT id FROM rag_source_snapshot WHERE operation_id = :operation_id))"
                        ),
                        ids,
                    )
                await connection.execute(
                    text(
                        "DELETE FROM rag_source_ingestion_artifact "
                        "WHERE ingestion_run_id IN "
                        "(SELECT id FROM rag_source_ingestion_run WHERE operation_id = :operation_id)"
                    ),
                    ids,
                )
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
                has_identity = bool(
                    (
                        await connection.execute(text("SELECT to_regclass('public.rag_entity_identity') IS NOT NULL"))
                    ).scalar_one()
                )
                if has_identity:
                    await connection.execute(
                        text(
                            "DELETE FROM rag_entity_identity WHERE id IN "
                            "(:product_identity_id, :ingredient_identity_id)"
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
                    text(
                        "ALTER TABLE rag_source_snapshot_verification ENABLE TRIGGER trg_rag_snapshot_verification_immutable"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE rag_source_ingestion_artifact "
                        "ENABLE TRIGGER trg_rag_source_ingestion_artifact_prevent_delete"
                    )
                )
                await connection.execute(
                    text("ALTER TABLE rag_source_snapshot ENABLE TRIGGER trg_rag_source_snapshot_prevent_update")
                )
                await connection.execute(
                    text("ALTER TABLE rag_source_snapshot ENABLE TRIGGER trg_rag_source_snapshot_prevent_delete")
                )


async def _execute_expect_db_error(
    sql: str,
    params: Mapping[str, object],
    *,
    expected_text: str | None = None,
) -> None:
    async with _connection() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError) as exc_info:
                await connection.execute(text(sql), params)
            if expected_text is not None:
                assert expected_text in str(exc_info.value)
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
    assert "uq_rag_source_snapshot_active_version" in schema_objects
    assert "chk_rag_source_snapshot_endpoint_receipt_hash" in schema_objects
    assert "chk_rag_source_snapshot_rejected_record_count_lte_record_count" in schema_objects
    assert "chk_rag_source_ingestion_run_group_key_nonblank" in schema_objects
    assert "uq_rag_source_ingestion_run_attempt" in schema_objects
    assert "uq_rag_medication_product_id_snapshot" in schema_objects
    assert "uq_rag_medication_ingredient_id_snapshot" in schema_objects
    assert "fk_rag_medication_alias_target_identity" in schema_objects
    assert "uq_rag_entity_identity_natural" in schema_objects
    assert "fk_rag_medication_product_identity" in schema_objects
    assert "fk_rag_medication_ingredient_identity" in schema_objects
    assert "uq_rag_medication_alias_observation" in schema_objects
    assert "idx_rag_medication_alias_normalized_text" in schema_objects
    assert "idx_rag_medication_alias_normalized_text_trgm" in schema_objects
    assert "fk_rag_medication_search_entry_product_identity" in schema_objects
    assert "fk_rag_medication_search_entry_alias_identity" in schema_objects
    assert "trg_rag_medication_search_entry_validate" not in schema_objects
    assert "fk_rag_medication_component_product_snapshot" in schema_objects
    assert "fk_rag_medication_component_ingredient_snapshot" in schema_objects
    assert "trg_rag_source_snapshot_prevent_update" in schema_objects
    assert "trg_rag_source_snapshot_prevent_delete" in schema_objects
    assert "uq_rag_source_artifact_run_page" in schema_objects
    assert "uq_rag_source_artifact_run_key" in schema_objects
    assert "chk_rag_source_artifact_checksum" in schema_objects
    assert "chk_rag_source_artifact_kind_metadata" in schema_objects
    assert "trg_rag_source_ingestion_artifact_prevent_update" in schema_objects
    assert "trg_rag_source_ingestion_artifact_prevent_delete" in schema_objects


def test_rag_source_ingestion_artifact_is_append_only_and_run_scoped() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    async def insert_artifact(ids: dict[str, str]) -> str:
        artifact_id = str(uuid4())
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_source_ingestion_artifact (
                            id, ingestion_run_id, page_number, artifact_key,
                            storage_backend, object_key, raw_checksum, byte_size, content_type
                        )
                        VALUES (
                            :artifact_id, :ingestion_run_id, 1, 'page-0001.json',
                            'PRIVATE_OBJECT_STORAGE', 'source/synthetic/page-0001.json',
                            :raw_checksum, 128, 'application/json'
                        )
                        """
                    ),
                    {**ids, "artifact_id": artifact_id, "raw_checksum": "d" * 64},
                )
        return artifact_id

    try:
        command.upgrade(alembic_config, "head")
        ids = asyncio.run(_seed_source_catalog_chain())
        artifact_id = asyncio.run(insert_artifact(ids))

        asyncio.run(
            _execute_expect_db_error(
                "UPDATE rag_source_ingestion_artifact SET byte_size = 129 WHERE id = :artifact_id",
                {"artifact_id": artifact_id},
                expected_text="rows are append-only",
            )
        )
        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO rag_source_ingestion_artifact (
                    id, ingestion_run_id, page_number, artifact_key,
                    storage_backend, object_key, raw_checksum, byte_size, content_type
                )
                VALUES (
                    :artifact_id, :ingestion_run_id, 1, 'page-0002.json',
                    'PRIVATE_OBJECT_STORAGE', 'source/synthetic/page-0002.json',
                    :raw_checksum, 128, 'application/json'
                )
                """,
                {**ids, "artifact_id": str(uuid4()), "raw_checksum": "e" * 64},
                expected_text="uq_rag_source_artifact_run_page",
            )
        )
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(alembic_config, "head")


def test_rag_source_ingestion_artifact_downgrade_preserves_existing_references() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "165d7e6f5041")
        ids = asyncio.run(_seed_source_catalog_chain(include_verification=False))

        async def insert_artifact() -> None:
            assert ids is not None
            async with _connection() as connection:
                async with connection.begin():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO rag_source_ingestion_artifact (
                                id, ingestion_run_id, page_number, artifact_key,
                                storage_backend, object_key, raw_checksum, byte_size, content_type
                            )
                            VALUES (
                                :artifact_id, :ingestion_run_id, 1, 'page-0001.json',
                                'PRIVATE_OBJECT_STORAGE', 'source/synthetic/page-0001.json',
                                :raw_checksum, 128, 'application/json'
                            )
                            """
                        ),
                        {**ids, "artifact_id": str(uuid4()), "raw_checksum": "d" * 64},
                    )

        asyncio.run(insert_artifact())

        with pytest.raises(RuntimeError, match="Cannot downgrade revision 165a4b3c2d1e"):
            command.downgrade(alembic_config, RAG_SOURCE_CATALOG_REVISION)

        assert asyncio.run(_count_table("rag_source_ingestion_artifact")) == 1
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(alembic_config, "head")


def test_rag_source_reject_artifact_metadata_and_downgrade_are_fail_closed() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    async def insert_rejection(ids: dict[str, str]) -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_source_ingestion_artifact (
                            id, ingestion_run_id, page_number, artifact_kind,
                            artifact_key, storage_backend, object_key,
                            raw_checksum, byte_size, content_type,
                            reject_code, parser_location
                        )
                        VALUES (
                            :artifact_id, :ingestion_run_id, NULL, 'REJECTS',
                            'reject-0001.json', 'PRIVATE_OBJECT_STORAGE',
                            'source/synthetic/reject-0001.json', :raw_checksum,
                            64, 'application/json', 'MISSING_ITEM_SEQ',
                            'page[1].record[3]'
                        )
                        """
                    ),
                    {**ids, "artifact_id": str(uuid4()), "raw_checksum": "d" * 64},
                )

    try:
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "165d7e6f5041")
        ids = asyncio.run(_seed_source_catalog_chain(include_verification=False))
        asyncio.run(insert_rejection(ids))

        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO rag_source_ingestion_artifact (
                    id, ingestion_run_id, page_number, artifact_kind,
                    artifact_key, storage_backend, object_key,
                    raw_checksum, byte_size, content_type,
                    reject_code, parser_location
                )
                VALUES (
                    :artifact_id, :ingestion_run_id, NULL, 'REJECTS',
                    'reject-0002.json', 'PRIVATE_OBJECT_STORAGE',
                    'source/synthetic/reject-0002.json', :raw_checksum,
                    64, 'application/json', 'unsafe-code', 'page[1].record[4]'
                )
                """,
                {**ids, "artifact_id": str(uuid4()), "raw_checksum": "e" * 64},
                expected_text="chk_rag_source_artifact_kind_metadata",
            )
        )

        with pytest.raises(RuntimeError, match="Cannot downgrade revision 165b5c4d3e2f"):
            command.downgrade(alembic_config, "165a4b3c2d1e")

        assert asyncio.run(_count_table("rag_source_ingestion_artifact")) == 1
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(alembic_config, "head")


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
                expected_text="uq_rag_source_snapshot_current",
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
                expected_text="uq_rag_medication_product_snapshot_identity",
            )
        )
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(alembic_config, "head")


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
                expected_text="immutable fields cannot be updated",
            )
        )
        asyncio.run(
            _execute_expect_db_error(
                """
                UPDATE rag_source_snapshot
                SET endpoint_receipt_hash = :endpoint_receipt_hash
                WHERE id = :snapshot_id
                """,
                {**ids, "endpoint_receipt_hash": "d" * 64},
                expected_text="immutable fields cannot be updated",
            )
        )

        stale_snapshot_id = asyncio.run(_create_stale_snapshot_for_same_operation(ids))

        asyncio.run(
            _execute_expect_db_error(
                """
                DELETE FROM rag_source_snapshot
                WHERE id = :stale_snapshot_id
                """,
                {**ids, "stale_snapshot_id": stale_snapshot_id},
                expected_text="rows are append-only",
            )
        )
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(alembic_config, "head")


def test_failed_snapshot_allows_same_version_retry_after_alembic_upgrade() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    async def insert_failed_and_retry(ids: dict[str, str]) -> None:
        async with _connection() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(
                    text(
                        """
                        UPDATE rag_source_snapshot
                        SET verification_status = 'FAILED'
                        WHERE id = :snapshot_id
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
                            endpoint_receipt_hash, record_count, rejected_record_count,
                            verification_status, collected_at
                        )
                        VALUES (
                            :retry_snapshot_id, :operation_id, :source_version, :checksum_a,
                            :checksum_b, 'schema-v1', 'parser-v1',
                            'normalization-v1', 'canonical-v1',
                            :endpoint_receipt_hash, 1, 0, 'PENDING', :collected_at
                        )
                        """
                    ),
                    {
                        **ids,
                        "retry_snapshot_id": str(uuid4()),
                        "endpoint_receipt_hash": "f" * 64,
                        "checksum_a": "a" * 64,
                        "checksum_b": "b" * 64,
                        "collected_at": datetime.now(UTC),
                    },
                )
            finally:
                await transaction.rollback()

    try:
        command.upgrade(alembic_config, "head")
        ids = asyncio.run(_seed_source_catalog_chain())
        asyncio.run(insert_failed_and_retry(ids))
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(alembic_config, "head")


def test_snapshot_receipt_provenance_blocks_unsafe_downgrade() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    async def insert_snapshot_with_receipt(ids: dict[str, str]) -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_source_snapshot (
                            id, operation_id, source_version, raw_manifest_checksum,
                            canonical_checksum, schema_version, parser_version,
                            normalization_version, canonicalization_spec_version,
                            endpoint_receipt_hash, record_count, rejected_record_count,
                            verification_status, collected_at
                        )
                        VALUES (
                            :receipt_snapshot_id, :operation_id, :receipt_source_version,
                            :checksum_a, :checksum_b, 'schema-v1', 'parser-v1',
                            'normalization-v1', 'canonical-v1', :endpoint_receipt_hash,
                            1, 0, 'STALE', :collected_at
                        )
                        """
                    ),
                    {
                        **ids,
                        "receipt_snapshot_id": str(uuid4()),
                        "receipt_source_version": f"api:receipt:{uuid4().hex[:8]}",
                        "endpoint_receipt_hash": "f" * 64,
                        "checksum_a": "a" * 64,
                        "checksum_b": "b" * 64,
                        "collected_at": datetime.now(UTC),
                    },
                )

    try:
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "165d7e6f5041")
        ids = asyncio.run(_seed_source_catalog_chain(include_verification=False))
        asyncio.run(insert_snapshot_with_receipt(ids))

        with pytest.raises(RuntimeError, match="Cannot downgrade revision 165c6d5e4f30"):
            command.downgrade(alembic_config, "165b5c4d3e2f")
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(alembic_config, "head")


def test_rag_source_ingestion_attempt_is_scoped_by_run_group_after_alembic_upgrade() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None
    collected_at = datetime.now(UTC)

    try:
        command.upgrade(alembic_config, "head")
        ids = asyncio.run(_seed_source_catalog_chain())

        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO rag_source_ingestion_run (
                    id, operation_id, run_group_key, snapshot_id, run_status, attempt_number, started_at
                )
                VALUES (
                    :duplicate_run_id, :operation_id, 'initial-load', NULL, 'FAILED', 1, :collected_at
                )
                """,
                {**ids, "duplicate_run_id": str(uuid4()), "collected_at": collected_at},
                expected_text="uq_rag_source_ingestion_run_attempt",
            )
        )

        async def create_next_run() -> int:
            async with _connection() as connection:
                async with connection.begin():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO rag_source_ingestion_run (
                                id, operation_id, run_group_key, snapshot_id, run_status, attempt_number, started_at
                            )
                            VALUES (
                                :next_run_id, :operation_id, 'manual-refresh', :snapshot_id, 'RUNNING', 1, :collected_at
                            )
                            """
                        ),
                        {**ids, "next_run_id": str(uuid4()), "collected_at": collected_at},
                    )
                    result = await connection.execute(
                        text(
                            """
                            SELECT count(*)
                            FROM rag_source_ingestion_run
                            WHERE operation_id = :operation_id
                              AND attempt_number = 1
                            """
                        ),
                        ids,
                    )
                    return int(result.scalar_one())

        assert asyncio.run(create_next_run()) == 2
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(alembic_config, "head")


def test_rag_source_catalog_downgrade_blocks_non_empty_tables_and_preserves_data() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "165d7e6f5041")
        ids = asyncio.run(_seed_source_catalog_chain(include_verification=False))

        with pytest.raises(RuntimeError, match="Cannot downgrade revision 164f3a2b1c0d"):
            command.downgrade(alembic_config, RAG_SOURCE_CATALOG_BASE_REVISION)

        assert asyncio.run(_table_exists("rag_source_snapshot")) is True
        assert asyncio.run(_count_table("rag_source")) >= 1
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(alembic_config, "head")


def test_rag_source_catalog_component_snapshot_fk_is_enforced_after_alembic_upgrade() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None

    try:
        command.upgrade(alembic_config, "head")
        ids = asyncio.run(_seed_source_catalog_chain())

        stale_snapshot_id = asyncio.run(_create_stale_snapshot_for_same_operation(ids))
        stale_product_id = str(uuid4())
        stale_ingredient_id = str(uuid4())

        async def create_stale_catalog_rows() -> None:
            async with _connection() as connection:
                async with connection.begin():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO rag_medication_product (
                                id, source_snapshot_id, source_record_key, code_system,
                                canonical_code, product_name, normalized_product_name, product_status
                            )
                            VALUES (
                                :stale_product_id, :stale_snapshot_id, 'ITEM_SEQ:200000002',
                                'MFDS_ITEM_SEQ', '200000002', '다른스냅샷제품',
                                '다른스냅샷제품', 'ACTIVE'
                            )
                            """
                        ),
                        {**ids, "stale_snapshot_id": stale_snapshot_id, "stale_product_id": stale_product_id},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO rag_medication_ingredient (
                                id, source_snapshot_id, source_record_key, ingredient_code_system,
                                ingredient_code, ingredient_name, normalized_ingredient_name
                            )
                            VALUES (
                                :stale_ingredient_id, :stale_snapshot_id, 'INGREDIENT:IBUPROFEN',
                                'MFDS_INGREDIENT', 'I0002', '이부프로펜', '이부프로펜'
                            )
                            """
                        ),
                        {
                            **ids,
                            "stale_snapshot_id": stale_snapshot_id,
                            "stale_ingredient_id": stale_ingredient_id,
                        },
                    )

        asyncio.run(create_stale_catalog_rows())

        asyncio.run(
            _execute_expect_db_error(
                """
                INSERT INTO rag_medication_product_component (
                    id, source_snapshot_id, product_id, ingredient_id,
                    component_role, display_order
                )
                VALUES (
                    :bad_component_id, :stale_snapshot_id, :product_id, :stale_ingredient_id,
                    'EXCIPIENT', 2
                )
                """,
                {
                    **ids,
                    "bad_component_id": str(uuid4()),
                    "stale_snapshot_id": stale_snapshot_id,
                    "stale_ingredient_id": stale_ingredient_id,
                },
                expected_text="fk_rag_medication_component_product_snapshot",
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
                    :bad_component_id, :stale_snapshot_id, :stale_product_id, :ingredient_id,
                    'UNKNOWN', 3
                )
                """,
                {
                    **ids,
                    "bad_component_id": str(uuid4()),
                    "stale_snapshot_id": stale_snapshot_id,
                    "stale_product_id": stale_product_id,
                },
                expected_text="fk_rag_medication_component_ingredient_snapshot",
            )
        )
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(alembic_config, "head")


def test_verification_history_is_immutable_and_publication_requires_actor() -> None:
    alembic_config = create_alembic_config()
    ids: dict[str, str] | None = None
    try:
        command.upgrade(alembic_config, "head")
        command.downgrade(alembic_config, "165d7e6f5041")
        ids = asyncio.run(_seed_source_catalog_chain())
        for sql in (
            "UPDATE rag_source_snapshot_verification SET verification_result = 'FAILED' WHERE id = :verification_id",
            "DELETE FROM rag_source_snapshot_verification WHERE id = :verification_id",
        ):
            asyncio.run(_execute_expect_db_error(sql, ids, expected_text="append-only"))
        for actor in (None, "", "   "):
            asyncio.run(
                _execute_expect_db_error(
                    "INSERT INTO rag_source_snapshot_verification "
                    "(id, snapshot_id, check_name, verification_result, verified_by, verified_at) "
                    "VALUES (:new_id, :snapshot_id, 'snapshot-publication-approval', 'PASSED', :actor, :now)",
                    {**ids, "new_id": str(uuid4()), "actor": actor, "now": datetime.now(UTC)},
                    expected_text="chk_rag_snapshot_publication_approver",
                )
            )

        async def named_approval_is_accepted() -> None:
            async with _connection() as connection:
                transaction = await connection.begin()
                try:
                    await connection.execute(
                        text(
                            "INSERT INTO rag_source_snapshot_verification "
                            "(id, snapshot_id, check_name, verification_result, verified_by, verified_at) "
                            "VALUES (:new_id, :snapshot_id, 'snapshot-publication-approval', 'PASSED', 'synthetic-reviewer', :now)"
                        ),
                        {**ids, "new_id": str(uuid4()), "now": datetime.now(UTC)},
                    )
                finally:
                    await transaction.rollback()

        asyncio.run(named_approval_is_accepted())
        with pytest.raises(RuntimeError, match="Cannot downgrade revision 165d7e6f5041"):
            command.downgrade(alembic_config, "165c6d5e4f30")
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))


@pytest.mark.parametrize("status", ["PENDING", "FAILED"])
def test_runtime_cannot_write_snapshot_publication_state_directly(status: str) -> None:
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_source_catalog_chain(status=status))

    async def verify() -> None:
        async with _connection() as connection:
            transaction = await connection.begin()
            try:
                role = f"synthetic_snapshot_{uuid4().hex}"
                await connection.execute(text(f"CREATE ROLE {role} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"))
                await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
                await connection.execute(
                    text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
                )
                permitted = await connection.execute(
                    text(
                        "SELECT has_function_privilege(:role, 'transition_rag_source_snapshot(text,text,text,timestamptz,timestamptz,text)', 'EXECUTE')"
                    ),
                    {"role": role},
                )
                assert permitted.scalar_one() is False
                # This isolated Runtime role is created after migration.
                # Execute the actual provisioning query for roles created after migration.
                provisioning = (PROJECT_ROOT / "infra/docker/postgres/configure-app-role.sql").read_text()
                grant_query = provisioning.split("-- Migration 이후", 1)[1].split("SELECT format(", 1)[1]
                grant_query = "SELECT format(" + grant_query.split("\\gexec", 1)[0]
                grant = await connection.execute(
                    text(grant_query.replace(":'app_user'", "CAST(:app_user AS text)")), {"app_user": role}
                )
                await connection.execute(text(grant.scalar_one()))
                await connection.execute(text(f"SET LOCAL ROLE {role}"))
                # Custom session flags must never confer transition authority.
                await connection.execute(text("SET LOCAL app.snapshot_transition = 'allowed'"))
                for assignment in ("verification_status = 'CURRENT', effective_at = now()", "verified_at = now()"):
                    async with connection.begin_nested() as savepoint:
                        with pytest.raises(DBAPIError, match="DB-owned transition"):
                            await connection.execute(
                                text(f"UPDATE rag_source_snapshot SET {assignment} WHERE id = :snapshot_id"), ids
                            )
                        await savepoint.rollback()
                async with connection.begin_nested() as savepoint:
                    with pytest.raises(DBAPIError, match="must start PENDING"):
                        await connection.execute(
                            text("""
                            INSERT INTO rag_source_snapshot
                                (id, operation_id, source_version, raw_manifest_checksum, canonical_checksum,
                                 schema_version, parser_version, normalization_version, canonicalization_spec_version,
                                 record_count, rejected_record_count, verification_status, collected_at)
                            SELECT :new_id, operation_id, :new_version, raw_manifest_checksum, canonical_checksum,
                                schema_version, parser_version, normalization_version, canonicalization_spec_version,
                                record_count, rejected_record_count, 'CURRENT', collected_at
                            FROM rag_source_snapshot WHERE id = :snapshot_id
                        """),
                            {**ids, "new_id": str(uuid4()), "new_version": uuid4().hex},
                        )
                    await savepoint.rollback()
                for expected in ("FAILED", None):
                    if status != "FAILED":
                        continue
                    async with connection.begin_nested() as savepoint:
                        if expected is None:
                            result = await connection.execute(
                                text(
                                    "SELECT transition_rag_source_snapshot(:snapshot_id, NULL, 'CURRENT', now(), now(), 'synthetic')"
                                ),
                                ids,
                            )
                            assert result.scalar_one() is False
                        else:
                            with pytest.raises(DBAPIError, match="Invalid Snapshot transition"):
                                await connection.execute(
                                    text(
                                        "SELECT transition_rag_source_snapshot(:snapshot_id, 'FAILED', 'CURRENT', now(), now(), 'synthetic')"
                                    ),
                                    ids,
                                )
                        await savepoint.rollback()
            finally:
                await transaction.rollback()

    try:
        asyncio.run(verify())
    finally:
        asyncio.run(_cleanup_source_catalog_chain(ids))


@pytest.mark.parametrize("approved", [False, True])
def test_runtime_publication_function_requires_approval_and_appends_immutable_selection(approved: bool) -> None:
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_source_catalog_chain(status="PENDING", rejected_count=1))

    async def verify() -> None:
        from sqlalchemy.ext.asyncio import AsyncSession

        from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
        from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import select_current_snapshot

        async with _connection() as connection:
            transaction = await connection.begin()
            try:
                if approved:
                    await connection.execute(
                        text(
                            "INSERT INTO rag_source_snapshot_verification (id, snapshot_id, check_name, verification_result, verified_at, verified_by) VALUES (:approval_id, :snapshot_id, 'snapshot-publication-approval', 'PASSED', now(), 'synthetic-reviewer')"
                        ),
                        {**ids, "approval_id": str(uuid4())},
                    )
                role = f"synthetic_snapshot_{uuid4().hex}"
                await connection.execute(text(f"CREATE ROLE {role} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"))
                await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
                await connection.execute(
                    text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
                )
                permitted = await connection.execute(
                    text(
                        "SELECT has_function_privilege(:role, 'transition_rag_source_snapshot(text,text,text,timestamptz,timestamptz,text)', 'EXECUTE')"
                    ),
                    {"role": role},
                )
                assert permitted.scalar_one() is False
                # This isolated Runtime role is created after migration.
                # Execute the actual provisioning query for roles created after migration.
                provisioning = (PROJECT_ROOT / "infra/docker/postgres/configure-app-role.sql").read_text()
                grant_query = provisioning.split("-- Migration 이후", 1)[1].split("SELECT format(", 1)[1]
                grant_query = "SELECT format(" + grant_query.split("\\gexec", 1)[0]
                grant = await connection.execute(
                    text(grant_query.replace(":'app_user'", "CAST(:app_user AS text)")), {"app_user": role}
                )
                await connection.execute(text(grant.scalar_one()))
                await connection.execute(text(f"SET LOCAL ROLE {role}"))
                if not approved:
                    async with connection.begin_nested() as savepoint:
                        with pytest.raises(DBAPIError, match="Publication approval required"):
                            await connection.execute(
                                text(
                                    "SELECT transition_rag_source_snapshot(:snapshot_id, 'PENDING', 'CURRENT', now(), now(), 'synthetic')"
                                ),
                                ids,
                            )
                        await savepoint.rollback()
                    return
                # If the immutable evidence cannot be written, CURRENT must roll back too.
                async with connection.begin_nested() as savepoint:
                    with pytest.raises(DBAPIError):
                        await connection.execute(
                            text(
                                "SELECT transition_rag_source_snapshot(:snapshot_id, 'PENDING', 'CURRENT', now(), now(), repeat('x', 1000))"
                            ),
                            ids,
                        )
                    await savepoint.rollback()
                unchanged = await connection.execute(
                    text("SELECT verification_status FROM rag_source_snapshot WHERE id = :snapshot_id"), ids
                )
                assert unchanged.scalar_one() == "PENDING"
                no_evidence = await connection.execute(
                    text(
                        "SELECT count(*) FROM rag_source_snapshot_verification WHERE snapshot_id = :snapshot_id AND check_name = 'snapshot-current-selection'"
                    ),
                    ids,
                )
                assert no_evidence.scalar_one() == 0
                async with AsyncSession(bind=connection) as session:
                    from uuid import UUID

                    await select_current_snapshot(
                        repository=SqlAlchemySourceSnapshotRepository(session),
                        snapshot_id=UUID(ids["snapshot_id"]),
                        selected_at=datetime.now(UTC),
                        selected_by="synthetic-selector",
                    )
                    state = await session.execute(
                        text("SELECT verification_status FROM rag_source_snapshot WHERE id = :snapshot_id"), ids
                    )
                    assert state.scalar_one() == "CURRENT"
                    evidence = await session.execute(
                        text(
                            "SELECT id, verified_by, details_summary FROM rag_source_snapshot_verification WHERE snapshot_id = :snapshot_id AND check_name = 'snapshot-current-selection' AND verification_result = 'PASSED'"
                        ),
                        ids,
                    )
                    row = evidence.one()
                    assert row.verified_by == "synthetic-selector"
                    assert row.details_summary.startswith("DB-owned transition; session=")
                    for sql in (
                        "UPDATE rag_source_snapshot_verification SET verification_result = 'FAILED' WHERE id = :id",
                        "DELETE FROM rag_source_snapshot_verification WHERE id = :id",
                    ):
                        async with connection.begin_nested() as savepoint:
                            with pytest.raises(DBAPIError, match="append-only"):
                                await connection.execute(text(sql), {"id": row.id})
                            await savepoint.rollback()
            finally:
                await transaction.rollback()

    try:
        asyncio.run(verify())
    finally:
        asyncio.run(_cleanup_source_catalog_chain(ids))


def test_snapshot_state_protection_downgrade_preserves_existing_snapshots() -> None:
    configuration = create_alembic_config()
    command.upgrade(configuration, "head")
    ids = asyncio.run(_seed_source_catalog_chain(status="PENDING"))
    try:

        async def remove_non_reversible_alias() -> None:
            async with _connection() as connection:
                async with connection.begin():
                    await connection.execute(text("DELETE FROM rag_medication_alias WHERE id = :alias_id"), ids)

        asyncio.run(remove_non_reversible_alias())
        with pytest.raises(RuntimeError, match="Cannot downgrade revision 165e8f706152"):
            command.downgrade(configuration, "165d7e6f5041")
        assert asyncio.run(_count_table("rag_source_snapshot")) >= 1
    finally:
        asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(configuration, "head")


@pytest.mark.parametrize(
    ("run_status", "has_snapshot", "allowed"),
    [
        ("FAILED", True, False),
        ("FAILED", False, True),
        ("NO_CHANGE", False, False),
        ("NO_CHANGE", True, True),
        ("SUCCEEDED", True, True),
        ("SUCCEEDED_WITH_REJECTIONS", True, True),
    ],
)
def test_ingestion_run_snapshot_status_check(run_status: str, has_snapshot: bool, allowed: bool) -> None:
    command.upgrade(create_alembic_config(), "head")
    ids = asyncio.run(_seed_source_catalog_chain())

    async def verify() -> None:
        async with _connection() as connection:
            transaction = await connection.begin()
            try:
                statement = text("""
                    INSERT INTO rag_source_ingestion_run
                        (id, operation_id, run_group_key, snapshot_id, run_status, attempt_number, started_at)
                    VALUES (:id, :operation_id, 'synthetic-status-check', :snapshot_id, :status, 1, now())
                """)
                parameters = {
                    "id": str(uuid4()),
                    "operation_id": ids["operation_id"],
                    "snapshot_id": ids["snapshot_id"] if has_snapshot else None,
                    "status": run_status,
                }
                if allowed:
                    await connection.execute(statement, parameters)
                else:
                    with pytest.raises(DBAPIError, match="chk_rag_ingestion_run_snapshot_status"):
                        await connection.execute(statement, parameters)
            finally:
                await transaction.rollback()

    try:
        asyncio.run(verify())
    finally:
        asyncio.run(_cleanup_source_catalog_chain(ids))


async def _catalog_migration_inventory() -> dict[str, int]:
    query = (PROJECT_ROOT / "scripts/rag/catalog_migration_preflight.sql").read_text()
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(text("SET TRANSACTION READ ONLY"))
            await connection.execute(text("SET LOCAL row_security = off"))
            result = (await connection.execute(text(query))).scalar_one()
            assert isinstance(result, dict)
            assert all(type(value) is int and value >= 0 for value in result.values())
            return result


@pytest.mark.parametrize(
    ("scenario", "extra_counts"),
    [
        ("unchanged", {}),
        (
            "missing_identity",
            {"ingredient_missing_identity_rows": 1, "alias_missing_target_identity_rows": 1, "alias_rows": 1},
        ),
        ("numeric_only", {"component_numeric_without_text_rows": 1}),
        ("multiple_roles", {"component_multiple_role_pairs": 1, "component_rows": 1}),
        ("shared_identity", {"product_rows": 1, "product_identity_across_snapshots_groups": 1}),
    ],
)
def test_catalog_migration_inventory_is_read_only_and_counts_legacy_gaps(
    scenario: str, extra_counts: dict[str, int]
) -> None:
    configuration = create_alembic_config()
    command.downgrade(configuration, CATALOG_IDENTITY_BASE_REVISION)

    async def check() -> None:
        baseline = await _catalog_migration_inventory()
        ids = await _seed_source_catalog_chain()
        try:
            stale_snapshot = (
                await _create_stale_snapshot_for_same_operation(ids) if scenario == "shared_identity" else None
            )
            async with _connection() as connection:
                async with connection.begin():
                    await connection.execute(
                        text("UPDATE rag_medication_product SET canonical_code = :product_id WHERE id = :product_id"),
                        ids,
                    )
                    if scenario == "shared_identity":
                        await connection.execute(
                            text("""
                                INSERT INTO rag_medication_product (
                                    id, source_snapshot_id, source_record_key, code_system, canonical_code,
                                    product_name, normalized_product_name, product_status
                                ) VALUES (:new_id, :stale_snapshot, 'synthetic-shared', 'MFDS_ITEM_SEQ',
                                          :product_id, 'synthetic-product', 'synthetic-product', 'ACTIVE')
                            """),
                            {**ids, "new_id": str(uuid4()), "stale_snapshot": stale_snapshot},
                        )
                    elif scenario == "missing_identity":
                        await connection.execute(
                            text(
                                "UPDATE rag_medication_ingredient SET ingredient_code = NULL WHERE id = :ingredient_id"
                            ),
                            ids,
                        )
                        await connection.execute(
                            text("""
                                INSERT INTO rag_medication_alias (
                                    id, source_snapshot_id, ingredient_id, target_type,
                                    alias_text, normalized_alias_text, is_approved
                                ) VALUES (:new_id, :snapshot_id, :ingredient_id, 'INGREDIENT',
                                          'synthetic-private-alias', 'synthetic-private-alias', false)
                            """),
                            {**ids, "new_id": str(uuid4())},
                        )
                    elif scenario == "numeric_only":
                        await connection.execute(
                            text("""
                                UPDATE rag_medication_product_component
                                SET amount_value = 2.5, amount_text = NULL WHERE id = :component_id
                            """),
                            ids,
                        )
                    elif scenario == "multiple_roles":
                        await connection.execute(
                            text("""
                                INSERT INTO rag_medication_product_component (
                                    id, source_snapshot_id, product_id, ingredient_id, component_role, display_order
                                ) VALUES (:new_id, :snapshot_id, :product_id, :ingredient_id, 'EXCIPIENT', 2)
                            """),
                            {**ids, "new_id": str(uuid4())},
                        )
            expected = dict(baseline)
            for key in (
                "product_rows",
                "ingredient_rows",
                "alias_rows",
                "component_rows",
                "alias_approved_boolean_rows",
            ):
                expected[key] += 1
            for key, count in extra_counts.items():
                expected[key] += count
            assert await _catalog_migration_inventory() == expected
            assert await _catalog_migration_inventory() == expected
        finally:
            await _cleanup_source_catalog_chain(ids)
        assert await _catalog_migration_inventory() == baseline

    asyncio.run(check())
    command.upgrade(configuration, "head")


def test_catalog_identity_migration_backfills_coded_legacy_members() -> None:
    configuration = create_alembic_config()
    ids: dict[str, str] | None = None
    try:
        command.downgrade(configuration, CATALOG_IDENTITY_BASE_REVISION)
        ids = asyncio.run(_seed_source_catalog_chain())

        async def remove_ambiguous_legacy_alias() -> None:
            async with _connection() as connection:
                async with connection.begin():
                    await connection.execute(text("DELETE FROM rag_medication_alias WHERE id = :alias_id"), ids)

        asyncio.run(remove_ambiguous_legacy_alias())
        command.upgrade(configuration, CATALOG_IDENTITY_REVISION)

        async def verify() -> None:
            async with _connection() as connection:
                identities = await connection.execute(
                    text(
                        """
                        SELECT entity_type, code_system, canonical_code
                        FROM rag_entity_identity
                        WHERE (entity_type = 'PRODUCT' AND code_system = 'MFDS_ITEM_SEQ'
                               AND canonical_code = '200000001')
                           OR (entity_type = 'INGREDIENT' AND code_system = 'MFDS_INGREDIENT'
                               AND canonical_code = 'I0001')
                        ORDER BY entity_type
                        """
                    ),
                )
                assert identities.all() == [
                    ("INGREDIENT", "MFDS_INGREDIENT", "I0001"),
                    ("PRODUCT", "MFDS_ITEM_SEQ", "200000001"),
                ]
                linked = await connection.execute(
                    text(
                        """
                        SELECT
                            p.entity_identity_id IS NOT NULL,
                            i.entity_identity_id IS NOT NULL,
                            p.identity_entity_type,
                            i.identity_entity_type
                        FROM rag_medication_product p
                        JOIN rag_medication_ingredient i ON i.id = :ingredient_id
                        WHERE p.id = :product_id
                        """
                    ),
                    ids,
                )
                assert linked.one() == (True, True, "PRODUCT", "INGREDIENT")

        asyncio.run(verify())
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(configuration, "head")


@pytest.mark.parametrize("legacy_gap", ["alias", "ingredient_identity"])
def test_catalog_identity_migration_refuses_unprovable_legacy_conversion(legacy_gap: str) -> None:
    configuration = create_alembic_config()
    ids: dict[str, str] | None = None
    try:
        command.downgrade(configuration, CATALOG_IDENTITY_BASE_REVISION)
        ids = asyncio.run(_seed_source_catalog_chain())

        if legacy_gap == "ingredient_identity":

            async def create_gap() -> None:
                async with _connection() as connection:
                    async with connection.begin():
                        await connection.execute(text("DELETE FROM rag_medication_alias WHERE id = :alias_id"), ids)
                        await connection.execute(
                            text(
                                "UPDATE rag_medication_ingredient SET ingredient_code = NULL WHERE id = :ingredient_id"
                            ),
                            ids,
                        )

            asyncio.run(create_gap())

        with pytest.raises(RuntimeError, match="Cannot infer"):
            command.upgrade(configuration, CATALOG_IDENTITY_REVISION)
    finally:
        if ids is not None:
            asyncio.run(_cleanup_source_catalog_chain(ids))
        command.upgrade(configuration, "head")


def test_catalog_alias_and_search_entry_constraints_bind_stable_product_identity_across_snapshots() -> None:
    configuration = create_alembic_config()
    command.upgrade(configuration, "head")
    ids = asyncio.run(_seed_source_catalog_chain())
    try:
        alias_snapshot_id = asyncio.run(_create_stale_snapshot_for_same_operation(ids))

        async def verify() -> None:
            async with _connection() as connection:
                async with connection.begin():
                    cross_snapshot_alias_id = str(uuid4())
                    await connection.execute(
                        text(
                            """
                            INSERT INTO rag_medication_alias (
                                id, source_snapshot_id, target_identity_id, target_type, alias_text,
                                normalized_alias_text, alias_source, review_status, record_status, is_effective
                            ) VALUES (
                                :alias_id, :alias_snapshot_id, :product_identity_id, 'PRODUCT',
                                '교차 스냅샷 별칭', '교차스냅샷별칭', 'SYNTHETIC', 'APPROVED', 'ACTIVE', true
                            )
                            """
                        ),
                        {**ids, "alias_id": cross_snapshot_alias_id, "alias_snapshot_id": alias_snapshot_id},
                    )
                    await connection.execute(
                        text(
                            """
                            INSERT INTO rag_medication_search_entry (
                                id, entry_type, product_id, product_identity_id, alias_id, normalized_text
                            ) VALUES (
                                :entry_id, 'APPROVED_ALIAS', :product_id, :product_identity_id,
                                :alias_id, '교차스냅샷별칭'
                            )
                            """
                        ),
                        {**ids, "entry_id": str(uuid4()), "alias_id": cross_snapshot_alias_id},
                    )

            await _execute_expect_db_error(
                """
                INSERT INTO rag_medication_search_entry (
                    id, entry_type, product_id, product_identity_id, alias_id, normalized_text
                ) VALUES (:entry_id, 'PRODUCT_NAME', :product_id, :product_identity_id, :alias_id, 'invalid')
                """,
                {**ids, "entry_id": str(uuid4()), "alias_id": cross_snapshot_alias_id},
            )
            await _execute_expect_db_error(
                """
                INSERT INTO rag_medication_search_entry (
                    id, entry_type, product_id, product_identity_id, alias_id, normalized_text
                ) VALUES (:entry_id, 'APPROVED_ALIAS', :product_id, :ingredient_identity_id, :alias_id, 'invalid')
                """,
                {**ids, "entry_id": str(uuid4()), "alias_id": cross_snapshot_alias_id},
            )

        asyncio.run(verify())
    finally:
        asyncio.run(_cleanup_source_catalog_chain(ids))


def test_catalog_set_schema_binds_sources_members_and_hashes() -> None:
    configuration = create_alembic_config()
    command.upgrade(configuration, "head")
    ids = asyncio.run(_seed_source_catalog_chain())

    async def verify() -> None:
        set_id = str(uuid4())
        async with _connection() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_catalog_set (
                            id, catalog_version, schema_version, normalization_version,
                            manifest_spec_version, envelope_hash, manifest_json
                        ) VALUES (
                            :set_id, 'catalog-v1', 'medication-catalog-v2', 'normalization-v1',
                            'catalog-manifest-envelope-v2', :envelope_hash, :manifest_json
                        )
                        """
                    ),
                    {"set_id": set_id, "envelope_hash": "c" * 64, "manifest_json": b"{}"},
                )
                with pytest.raises(DBAPIError):
                    async with connection.begin_nested():
                        await connection.execute(
                            text(
                                """
                                INSERT INTO rag_catalog_set_source (set_id, source_snapshot_id, source_version)
                                VALUES (:set_id, :snapshot_id, 'wrong-version')
                                """
                            ),
                            {**ids, "set_id": set_id},
                        )
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_catalog_set_source (set_id, source_snapshot_id, source_version)
                        VALUES (:set_id, :snapshot_id, :source_version)
                        """
                    ),
                    {**ids, "set_id": set_id},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_catalog_set_member (
                            set_id, member_kind, member_ref, source_snapshot_id, product_id
                        ) VALUES (:set_id, 'PRODUCT', 'product-ref', :snapshot_id, :product_id)
                        """
                    ),
                    {**ids, "set_id": set_id},
                )
                await connection.execute(
                    text(
                        """
                        INSERT INTO rag_catalog_set_hash (
                            set_id, hash_kind, schema_version, contract_spec_version,
                            digest, target, canonical_bytes
                        ) VALUES (
                            :set_id, 'EXPORT_CHECKSUM', 'medication-catalog-v2',
                            'catalog-manifest-envelope-v2', :digest, 'catalog_jsonl', :canonical_bytes
                        )
                        """
                    ),
                    {"set_id": set_id, "digest": "d" * 64, "canonical_bytes": b""},
                )

                result = await connection.execute(
                    text(
                        """
                        SELECT
                            (SELECT count(*) FROM rag_catalog_set WHERE id = :set_id),
                            (SELECT count(*) FROM rag_catalog_set_source WHERE set_id = :set_id),
                            (SELECT count(*) FROM rag_catalog_set_member WHERE set_id = :set_id),
                            (SELECT count(*) FROM rag_catalog_set_hash WHERE set_id = :set_id)
                        """
                    ),
                    {"set_id": set_id},
                )
                assert result.one() == (1, 1, 1, 1)
            finally:
                await transaction.rollback()

    try:
        asyncio.run(verify())
    finally:
        asyncio.run(_cleanup_source_catalog_chain(ids))
