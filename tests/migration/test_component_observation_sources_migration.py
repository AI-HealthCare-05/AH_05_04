"""Observation source migration preserves old rows and refuses lossy rollback."""

import importlib.util

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.integration.rag.test_catalog_storage_roundtrip import ROOT, saved
from tests.integration.rag.test_catalog_storage_roundtrip import database as _catalog_database  # noqa: F401


@pytest.fixture(name="database")
def component_database(request):
    return request.getfixturevalue("_catalog_database")


def migrate(connection, direction):
    spec = importlib.util.spec_from_file_location(
        "d04_observation_migration", ROOT / "backend/alembic/versions/166f30415263_component_observation_sources.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


async def test_legacy_sources_survive_observation_upgrade_and_downgrade(database):
    engine, factory = database
    await saved(factory)
    async with engine.begin() as connection:
        before = (
            await connection.execute(
                text("SELECT id, source_snapshot_id, product_id, ingredient_id FROM rag_medication_product_component")
            )
        ).all()
        await connection.run_sync(migrate, "downgrade")
        await connection.run_sync(migrate, "upgrade")
        after = (
            await connection.execute(
                text("SELECT id, source_snapshot_id, product_id, ingredient_id FROM rag_medication_product_component")
            )
        ).all()
        assert before == after
        column_types = (
            (
                await connection.execute(
                    text(
                        "SELECT data_type FROM information_schema.columns WHERE table_name='rag_medication_product_component' AND column_name IN ('product_source_snapshot_id', 'ingredient_source_snapshot_id')"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert column_types == ["character", "character"]
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM rag_medication_product_component WHERE product_source_snapshot_id <> source_snapshot_id OR ingredient_source_snapshot_id <> source_snapshot_id"
                )
            )
            == 0
        )


async def test_observation_payload_blocks_lossy_downgrade(database):
    engine, factory = database
    await saved(factory)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE rag_medication_product_component SET observation_json=:payload"),
            {"payload": b'{"synthetic":"preserve"}'},
        )
        with pytest.raises(RuntimeError, match="would lose"):
            await connection.run_sync(migrate, "downgrade")
        assert (
            await connection.scalar(text("SELECT observation_json FROM rag_medication_product_component LIMIT 1"))
            == b'{"synthetic":"preserve"}'
        )
