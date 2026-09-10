"""RAG Evidence/Citation Alembic schema and guard tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
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
RAG_EVIDENCE_CITATION_REVISION = "164c5d6e7f8a"
RAG_EVIDENCE_CITATION_BASE_REVISION = "199a1b2c3d4e"


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


async def _table_exists(table_name: str) -> bool:
    async with _connection() as connection:
        result = await connection.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = :table_name
                )
            """),
            {"table_name": table_name},
        )
        return bool(result.scalar_one())


async def _clear_evidence_citation_tables() -> None:
    async with _connection() as connection:
        async with connection.begin():
            for table_name in (
                "rag_citation",
                "rag_evidence_guideline",
                "rag_evidence_rule",
                "rag_evidence",
                "rag_evidence_knowledge",
            ):
                if await _table_exists(table_name):
                    await connection.execute(text(f"TRUNCATE TABLE {table_name} CASCADE"))


async def _drop_evidence_citation_tables() -> None:
    async with _connection() as connection:
        async with connection.begin():
            for table_name in (
                "rag_citation",
                "rag_evidence_guideline",
                "rag_evidence_rule",
                "rag_evidence",
                "rag_evidence_knowledge",
            ):
                await connection.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
            if await _table_exists("rag_source_snapshot"):
                await connection.execute(
                    text("ALTER TABLE rag_source_snapshot DROP CONSTRAINT IF EXISTS uq_rag_source_snapshot_id_version")
                )
            await connection.execute(text("DROP FUNCTION IF EXISTS prevent_rag_evidence_citation_mutation() CASCADE"))


async def _trigger_exists(table_name: str, trigger_name: str) -> bool:
    async with _connection() as connection:
        result = await connection.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.triggers
                    WHERE event_object_schema = 'public'
                      AND event_object_table = :table_name
                      AND trigger_name = :trigger_name
                )
            """),
            {"table_name": table_name, "trigger_name": trigger_name},
        )
        return bool(result.scalar_one())


async def _set_source_cleanup_triggers(*, enabled: bool) -> None:
    trigger_action = "ENABLE" if enabled else "DISABLE"
    triggers = (
        ("rag_source_snapshot", "trg_rag_source_snapshot_prevent_delete"),
        ("rag_source_snapshot", "trg_rag_source_snapshot_prevent_update"),
        ("rag_source_snapshot_verification", "trg_rag_snapshot_verification_immutable"),
    )
    async with _connection() as connection:
        async with connection.begin():
            for table_name, trigger_name in triggers:
                if await _table_exists(table_name) and await _trigger_exists(table_name, trigger_name):
                    await connection.execute(text(f"ALTER TABLE {table_name} {trigger_action} TRIGGER {trigger_name}"))


async def _clear_evidence_seed_source_catalog_tables() -> None:
    if not await _table_exists("rag_source"):
        return
    await _set_source_cleanup_triggers(enabled=False)
    try:
        async with _connection() as connection:
            async with connection.begin():
                source_filter = """
                    SELECT s.id
                    FROM rag_source s
                    WHERE s.source_code LIKE 'EVIDENCE_CITATION_%'
                """
                snapshot_filter = f"""
                    SELECT rs.id
                    FROM rag_source_snapshot rs
                    JOIN rag_source_operation ro ON ro.id = rs.operation_id
                    JOIN rag_source_endpoint re ON re.id = ro.endpoint_id
                    WHERE re.source_id IN ({source_filter})
                """
                operation_filter = f"""
                    SELECT ro.id
                    FROM rag_source_operation ro
                    JOIN rag_source_endpoint re ON re.id = ro.endpoint_id
                    WHERE re.source_id IN ({source_filter})
                """
                endpoint_filter = f"SELECT re.id FROM rag_source_endpoint re WHERE re.source_id IN ({source_filter})"

                for table_name in (
                    "rag_medication_product_component",
                    "rag_medication_alias",
                    "rag_medication_ingredient",
                    "rag_medication_product",
                ):
                    if await _table_exists(table_name):
                        await connection.execute(
                            text(f"DELETE FROM {table_name} WHERE source_snapshot_id IN ({snapshot_filter})")
                        )
                if await _table_exists("rag_source_snapshot_verification"):
                    await connection.execute(
                        text(f"DELETE FROM rag_source_snapshot_verification WHERE snapshot_id IN ({snapshot_filter})")
                    )
                if await _table_exists("rag_source_ingestion_artifact"):
                    await connection.execute(
                        text(
                            f"""
                            DELETE FROM rag_source_ingestion_artifact
                            WHERE ingestion_run_id IN (
                                SELECT id FROM rag_source_ingestion_run WHERE operation_id IN ({operation_filter})
                            )
                            """
                        )
                    )
                if await _table_exists("rag_source_ingestion_run"):
                    await connection.execute(
                        text(f"DELETE FROM rag_source_ingestion_run WHERE operation_id IN ({operation_filter})")
                    )
                if await _table_exists("rag_source_snapshot"):
                    await connection.execute(text(f"DELETE FROM rag_source_snapshot WHERE id IN ({snapshot_filter})"))
                if await _table_exists("rag_source_operation"):
                    await connection.execute(text(f"DELETE FROM rag_source_operation WHERE id IN ({operation_filter})"))
                if await _table_exists("rag_source_endpoint"):
                    await connection.execute(text(f"DELETE FROM rag_source_endpoint WHERE id IN ({endpoint_filter})"))
                await connection.execute(text(f"DELETE FROM rag_source WHERE id IN ({source_filter})"))
    finally:
        await _set_source_cleanup_triggers(enabled=True)


@pytest.fixture(autouse=True)
def reset_evidence_citation_revision() -> Iterator[None]:
    alembic_config = create_alembic_config()
    try:
        command.downgrade(alembic_config, RAG_EVIDENCE_CITATION_BASE_REVISION)
    except Exception:
        asyncio.run(_drop_evidence_citation_tables())
        command.stamp(alembic_config, RAG_EVIDENCE_CITATION_BASE_REVISION)
    asyncio.run(_drop_evidence_citation_tables())
    asyncio.run(_clear_evidence_seed_source_catalog_tables())
    yield
    asyncio.run(_clear_evidence_citation_tables())
    asyncio.run(_clear_evidence_seed_source_catalog_tables())


async def _fetch_constraint_and_trigger_names() -> set[str]:
    async with _connection() as connection:
        constraints = await connection.execute(
            text("""
                SELECT constraint_name
                FROM information_schema.table_constraints
                WHERE table_schema = 'public'
                  AND table_name IN (
                    'rag_evidence_knowledge', 'rag_evidence', 'rag_evidence_rule',
                    'rag_evidence_guideline', 'rag_citation'
                  )
            """)
        )
        indexes = await connection.execute(
            text("""
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename IN ('rag_evidence_knowledge', 'rag_evidence', 'rag_citation')
            """)
        )
        triggers = await connection.execute(
            text("""
                SELECT trigger_name
                FROM information_schema.triggers
                WHERE event_object_schema = 'public'
                  AND event_object_table IN (
                    'rag_evidence_knowledge', 'rag_evidence', 'rag_evidence_rule',
                    'rag_evidence_guideline', 'rag_citation'
                  )
            """)
        )
        return {*(row[0] for row in constraints), *(row[0] for row in indexes), *(row[0] for row in triggers)}


async def _seed_source_catalog_chain() -> dict[str, str]:
    ids = {
        "source_id": str(uuid4()),
        "endpoint_id": str(uuid4()),
        "operation_id": str(uuid4()),
        "snapshot_id": str(uuid4()),
        "other_snapshot_id": str(uuid4()),
        "product_id": str(uuid4()),
        "ingredient_id": str(uuid4()),
    }
    collected_at = datetime.now(UTC)
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    "INSERT INTO rag_source (id, source_code, display_name, lifecycle_status) VALUES (:source_id, :source_code, 'MFDS Product Approval', 'ACTIVE')"
                ),
                {"source_id": ids["source_id"], "source_code": f"EVIDENCE_CITATION_{uuid4().hex[:10]}"},
            )
            await connection.execute(
                text("""
                    INSERT INTO rag_source_endpoint (
                        id, source_id, endpoint_code, display_name,
                        lifecycle_status, runtime_status, acquisition_status
                    )
                    VALUES (:endpoint_id, :source_id, 'PRODUCT_LIST', 'Product List', 'VERIFIED', 'DISABLED', 'APPROVED')
                """),
                ids,
            )
            await connection.execute(
                text("""
                    INSERT INTO rag_source_operation (
                        id, endpoint_id, operation_code, display_name, runtime_status, acquisition_status
                    )
                    VALUES (:operation_id, :endpoint_id, 'LIST_PRODUCTS', 'List Products', 'DISABLED', 'APPROVED')
                """),
                ids,
            )
            for snapshot_key, version_suffix in (("snapshot_id", "a"), ("other_snapshot_id", "b")):
                await connection.execute(
                    text("""
                        INSERT INTO rag_source_snapshot (
                            id, operation_id, source_version, raw_manifest_checksum, canonical_checksum,
                            schema_version, parser_version, normalization_version, canonicalization_spec_version,
                            record_count, rejected_record_count, verification_status, collected_at
                        )
                        VALUES (
                            :snapshot_id, :operation_id, :source_version, :raw_checksum, :canonical_checksum,
                            'schema-v1', 'parser-v1', 'normalization-v1', 'canonical-v1',
                            1, 0, 'PENDING', :collected_at
                        )
                    """),
                    {
                        **ids,
                        "snapshot_id": ids[snapshot_key],
                        "source_version": f"api:2026-09-08:{version_suffix}:{uuid4().hex[:8]}",
                        "raw_checksum": "a" * 64,
                        "canonical_checksum": "b" * 64,
                        "collected_at": collected_at,
                    },
                )
            await connection.execute(
                text("""
                    INSERT INTO rag_medication_product (
                        id, source_snapshot_id, source_record_key, code_system,
                        canonical_code, product_name, normalized_product_name, product_status
                    )
                    VALUES (
                        :product_id, :snapshot_id, 'ITEM_SEQ:200000001', 'MFDS_ITEM_SEQ',
                        '200000001', '테스트정', '테스트정', 'ACTIVE'
                    )
                """),
                ids,
            )
            await connection.execute(
                text("""
                    INSERT INTO rag_medication_ingredient (
                        id, source_snapshot_id, source_record_key, ingredient_code_system,
                        ingredient_code, ingredient_name, normalized_ingredient_name
                    )
                    VALUES (
                        :ingredient_id, :snapshot_id, 'INGREDIENT:ACETAMINOPHEN',
                        'MFDS_INGREDIENT', 'ACETAMINOPHEN', '아세트아미노펜', '아세트아미노펜'
                    )
                """),
                ids,
            )
    return ids


async def _seed_evidence_chain() -> dict[str, str]:
    ids = await _seed_source_catalog_chain()
    ids.update({"knowledge_id": str(uuid4()), "evidence_id": str(uuid4()), "citation_id": str(uuid4())})
    async with _connection() as connection:
        async with connection.begin():
            await connection.execute(
                text("""
                    INSERT INTO rag_evidence_knowledge (
                        id, source_snapshot_id, knowledge_key, knowledge_type, title, source_locator, content_digest
                    )
                    VALUES (
                        :knowledge_id, :snapshot_id, 'mfds-product:200000001', 'SOURCE_RECORD',
                        'MFDS product record', 'ITEM_SEQ=200000001', :digest
                    )
                """),
                {**ids, "digest": "c" * 64},
            )
            await connection.execute(
                text("""
                    INSERT INTO rag_evidence (
                        id, source_snapshot_id, knowledge_id, product_id, ingredient_id,
                        evidence_key, evidence_type, evidence_status, source_locator, evidence_digest
                    )
                    VALUES (
                        :evidence_id, :snapshot_id, :knowledge_id, :product_id, :ingredient_id,
                        'evidence:200000001:ingredient', 'PRODUCT_FACT', 'APPROVED', 'ITEM_SEQ=200000001', :digest
                    )
                """),
                {**ids, "digest": "d" * 64},
            )
    return ids


def test_rag_evidence_citation_upgrade_and_downgrade() -> None:
    alembic_config = create_alembic_config()
    command.downgrade(alembic_config, RAG_EVIDENCE_CITATION_BASE_REVISION)
    command.upgrade(alembic_config, RAG_EVIDENCE_CITATION_REVISION)

    assert asyncio.run(_table_exists("rag_evidence_knowledge"))
    assert asyncio.run(_table_exists("rag_evidence"))
    assert asyncio.run(_table_exists("rag_evidence_rule"))
    assert asyncio.run(_table_exists("rag_evidence_guideline"))
    assert asyncio.run(_table_exists("rag_citation"))

    names = asyncio.run(_fetch_constraint_and_trigger_names())
    assert "fk_rag_evidence_product_snapshot" in names
    assert "fk_rag_evidence_ingredient_snapshot" in names
    assert "chk_rag_evidence_knowledge_chunk_has_knowledge" in names
    assert "chk_rag_evidence_product_fact_has_product" in names
    assert "chk_rag_evidence_ingredient_fact_has_ingredient" in names
    assert "fk_rag_citation_evidence_snapshot_status" in names
    assert "fk_rag_citation_snapshot_version" in names
    assert "chk_rag_citation_public_guard_deferred" in names
    assert "chk_rag_citation_public_excerpt_guard_deferred" in names
    assert "chk_rag_citation_medical_not_partially_supported" in names
    assert "trg_rag_citation_append_only_update" in names
    assert "trg_rag_evidence_append_only_delete" in names

    command.downgrade(alembic_config, RAG_EVIDENCE_CITATION_BASE_REVISION)
    assert not asyncio.run(_table_exists("rag_citation"))
    assert not asyncio.run(_table_exists("rag_evidence"))
    command.upgrade(alembic_config, "398b2c3d4e5f")


def test_rag_evidence_rejects_cross_snapshot_product() -> None:
    command.upgrade(create_alembic_config(), RAG_EVIDENCE_CITATION_REVISION)
    ids = asyncio.run(_seed_source_catalog_chain())

    async def run_insert() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("""
                        INSERT INTO rag_evidence (
                            id, source_snapshot_id, product_id, evidence_key,
                            evidence_type, evidence_status, evidence_digest
                        )
                        VALUES (
                            :id, :other_snapshot_id, :product_id, 'bad-cross-snapshot-product',
                            'PRODUCT_FACT', 'APPROVED', :digest
                        )
                    """),
                    {**ids, "id": str(uuid4()), "digest": "e" * 64},
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(run_insert())
    assert "fk_rag_evidence_product_snapshot" in str(exc_info.value.orig)


def test_rag_evidence_rejects_fact_without_required_catalog_reference() -> None:
    command.upgrade(create_alembic_config(), RAG_EVIDENCE_CITATION_REVISION)
    ids = asyncio.run(_seed_source_catalog_chain())

    async def insert_product_fact_without_product() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("""
                        INSERT INTO rag_evidence (
                            id, source_snapshot_id, evidence_key,
                            evidence_type, evidence_status, evidence_digest
                        )
                        VALUES (
                            :id, :snapshot_id, 'missing-product-reference',
                            'PRODUCT_FACT', 'APPROVED', :digest
                        )
                    """),
                    {**ids, "id": str(uuid4()), "digest": "f" * 64},
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(insert_product_fact_without_product())
    assert "chk_rag_evidence_product_fact_has_product" in str(exc_info.value.orig)

    async def insert_ingredient_fact_without_ingredient() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("""
                        INSERT INTO rag_evidence (
                            id, source_snapshot_id, evidence_key,
                            evidence_type, evidence_status, evidence_digest
                        )
                        VALUES (
                            :id, :snapshot_id, 'missing-ingredient-reference',
                            'INGREDIENT_FACT', 'APPROVED', :digest
                        )
                    """),
                    {**ids, "id": str(uuid4()), "digest": "g" * 64},
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(insert_ingredient_fact_without_ingredient())
    assert "chk_rag_evidence_ingredient_fact_has_ingredient" in str(exc_info.value.orig)


def test_rag_evidence_rejects_knowledge_chunk_without_knowledge() -> None:
    command.upgrade(create_alembic_config(), RAG_EVIDENCE_CITATION_REVISION)
    ids = asyncio.run(_seed_source_catalog_chain())

    async def run_insert() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("""
                        INSERT INTO rag_evidence (
                            id, source_snapshot_id, evidence_key,
                            evidence_type, evidence_status, evidence_digest
                        )
                        VALUES (
                            :id, :snapshot_id, 'missing-knowledge-reference',
                            'KNOWLEDGE_CHUNK', 'APPROVED', :digest
                        )
                    """),
                    {**ids, "id": str(uuid4()), "digest": "h" * 64},
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(run_insert())
    assert "chk_rag_evidence_knowledge_chunk_has_knowledge" in str(exc_info.value.orig)


def test_rag_citation_rejects_cross_snapshot_evidence_provenance() -> None:
    command.upgrade(create_alembic_config(), RAG_EVIDENCE_CITATION_REVISION)
    ids = asyncio.run(_seed_evidence_chain())

    async def run_insert() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("""
                        INSERT INTO rag_citation (
                            id, evidence_id, source_snapshot_id, evidence_status,
                            target_type, target_id, claim_key, claim_kind,
                            support_status, authorization_status, release_status, display_order,
                            source_title, source_version, source_locator
                        )
                        VALUES (
                            :citation_id, :evidence_id, :other_snapshot_id, 'APPROVED',
                            'GUIDE', :target_id, 'claim:cross-snapshot', 'AUXILIARY',
                            'SUPPORTED', 'PENDING', 'NOT_PUBLIC', 1,
                            'MFDS product record', 'api:2026-09-08', 'ITEM_SEQ=200000001'
                        )
                    """),
                    {**ids, "target_id": str(uuid4())},
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(run_insert())
    assert "fk_rag_citation_evidence_snapshot_status" in str(exc_info.value.orig)


def test_rag_citation_rejects_public_release_until_guard_connected() -> None:
    command.upgrade(create_alembic_config(), RAG_EVIDENCE_CITATION_REVISION)
    ids = asyncio.run(_seed_source_catalog_chain())
    ids.update({"evidence_id": str(uuid4()), "citation_id": str(uuid4())})

    async def run_insert() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("""
                        INSERT INTO rag_evidence (
                            id, source_snapshot_id, evidence_key,
                            evidence_type, evidence_status, evidence_digest
                        )
                        VALUES (
                            :evidence_id, :snapshot_id, 'draft-public-evidence',
                            'SAFETY_POLICY', 'DRAFT', :digest
                        )
                    """),
                    {**ids, "digest": "i" * 64},
                )
                await connection.execute(
                    text("""
                        INSERT INTO rag_citation (
                            id, evidence_id, source_snapshot_id, evidence_status,
                            target_type, target_id, claim_key, claim_kind,
                            support_status, authorization_status, release_status, display_order,
                            source_title, source_version, source_locator
                        )
                        VALUES (
                            :citation_id, :evidence_id, :snapshot_id, 'DRAFT',
                            'GUIDE', :target_id, 'claim:draft-public', 'AUXILIARY',
                            'SUPPORTED', 'PENDING', 'PUBLIC', 1,
                            'MFDS product record', 'api:2026-09-08', 'ITEM_SEQ=200000001'
                        )
                    """),
                    {**ids, "target_id": str(uuid4())},
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(run_insert())
    assert "chk_rag_citation_public_guard_deferred" in str(exc_info.value.orig)


def test_rag_citation_rejects_public_release_even_for_unsupported_claim() -> None:
    command.upgrade(create_alembic_config(), RAG_EVIDENCE_CITATION_REVISION)
    ids = asyncio.run(_seed_evidence_chain())

    async def run_insert() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("""
                        INSERT INTO rag_citation (
                            id, evidence_id, source_snapshot_id, evidence_status,
                            target_type, target_id, claim_key, claim_kind,
                            support_status, authorization_status, release_status, display_order,
                            source_title, source_version, source_locator
                        )
                        VALUES (
                            :citation_id, :evidence_id, :snapshot_id, 'APPROVED',
                            'CHAT_MESSAGE', :target_id, 'claim:1', 'MEDICAL',
                            'NOT_SUPPORTED', 'PENDING', 'PUBLIC', 1,
                            'MFDS product record', 'api:2026-09-08', 'ITEM_SEQ=200000001'
                        )
                    """),
                    {**ids, "target_id": str(uuid4())},
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(run_insert())
    assert "chk_rag_citation_public_guard_deferred" in str(exc_info.value.orig)


def test_rag_citation_rejects_pass_authorization_until_guard_connected() -> None:
    command.upgrade(create_alembic_config(), RAG_EVIDENCE_CITATION_REVISION)
    ids = asyncio.run(_seed_evidence_chain())

    async def run_insert() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("""
                        INSERT INTO rag_citation (
                            id, evidence_id, source_snapshot_id, evidence_status,
                            target_type, target_id, claim_key, claim_kind,
                            support_status, authorization_status, release_status, display_order,
                            source_title, source_version, source_locator
                        )
                        VALUES (
                            :citation_id, :evidence_id, :snapshot_id, 'APPROVED',
                            'GUIDE', :target_id, 'claim:pass-before-guard', 'AUXILIARY',
                            'SUPPORTED', 'PASS', 'NOT_PUBLIC', 1,
                            'MFDS product record', 'api:2026-09-08', 'ITEM_SEQ=200000001'
                        )
                    """),
                    {**ids, "target_id": str(uuid4())},
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(run_insert())
    assert "chk_rag_citation_authorization_status" in str(exc_info.value.orig)


def test_rag_citation_rejects_public_excerpt_until_guard_connected() -> None:
    command.upgrade(create_alembic_config(), RAG_EVIDENCE_CITATION_REVISION)
    ids = asyncio.run(_seed_evidence_chain())

    async def run_insert() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("""
                        INSERT INTO rag_citation (
                            id, evidence_id, source_snapshot_id, evidence_status,
                            target_type, target_id, claim_key, claim_kind,
                            support_status, authorization_status, release_status, display_order,
                            source_title, source_version, source_locator, public_excerpt
                        )
                        VALUES (
                            :citation_id, :evidence_id, :snapshot_id, 'APPROVED',
                            'GUIDE', :target_id, 'claim:excerpt-before-guard', 'AUXILIARY',
                            'SUPPORTED', 'PENDING', 'NOT_PUBLIC', 1,
                            'MFDS product record', 'api:2026-09-08', 'ITEM_SEQ=200000001', 'unverified excerpt'
                        )
                    """),
                    {**ids, "target_id": str(uuid4())},
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(run_insert())
    assert "chk_rag_citation_public_excerpt_guard_deferred" in str(exc_info.value.orig)


def test_rag_citation_rejects_snapshot_source_version_mismatch() -> None:
    command.upgrade(create_alembic_config(), RAG_EVIDENCE_CITATION_REVISION)
    ids = asyncio.run(_seed_evidence_chain())

    async def run_insert() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("""
                        INSERT INTO rag_citation (
                            id, evidence_id, source_snapshot_id, evidence_status,
                            target_type, target_id, claim_key, claim_kind,
                            support_status, authorization_status, release_status, display_order,
                            source_title, source_version, source_locator
                        )
                        VALUES (
                            :citation_id, :evidence_id, :snapshot_id, 'APPROVED',
                            'GUIDE', :target_id, 'claim:wrong-version', 'AUXILIARY',
                            'SUPPORTED', 'PENDING', 'NOT_PUBLIC', 1,
                            'MFDS product record', 'api:wrong-version', 'ITEM_SEQ=200000001'
                        )
                    """),
                    {**ids, "target_id": str(uuid4())},
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(run_insert())
    assert "fk_rag_citation_snapshot_version" in str(exc_info.value.orig)


def test_rag_citation_rejects_partially_supported_medical_claim() -> None:
    command.upgrade(create_alembic_config(), RAG_EVIDENCE_CITATION_REVISION)
    ids = asyncio.run(_seed_evidence_chain())

    async def run_insert() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("""
                        INSERT INTO rag_citation (
                            id, evidence_id, source_snapshot_id, evidence_status,
                            target_type, target_id, claim_key, claim_kind,
                            support_status, authorization_status, release_status, display_order,
                            source_title, source_version, source_locator
                        )
                        VALUES (
                            :citation_id, :evidence_id, :snapshot_id, 'APPROVED',
                            'GUIDE', :target_id, 'claim:1', 'MEDICAL',
                            'PARTIALLY_SUPPORTED', 'PENDING', 'NOT_PUBLIC', 1,
                            'MFDS product record', 'api:2026-09-08', 'ITEM_SEQ=200000001'
                        )
                    """),
                    {**ids, "target_id": str(uuid4())},
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(run_insert())
    assert "chk_rag_citation_medical_not_partially_supported" in str(exc_info.value.orig)


def test_rag_evidence_citation_rows_are_append_only() -> None:
    command.upgrade(create_alembic_config(), RAG_EVIDENCE_CITATION_REVISION)
    ids = asyncio.run(_seed_evidence_chain())

    async def run_update() -> None:
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(
                    text("UPDATE rag_evidence SET evidence_status = 'DRAFT' WHERE id = :evidence_id"), ids
                )

    with pytest.raises(DBAPIError) as exc_info:
        asyncio.run(run_update())
    assert "RAG Evidence/Citation rows are append-only" in str(exc_info.value.orig)
