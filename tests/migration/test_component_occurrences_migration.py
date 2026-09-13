"""D-04 occurrence migration guards on an isolated database.

Reuse the Catalog integration fixture; it creates and drops a dedicated database
without advancing the migration lane's shared historical database.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from ai_worker.adapters.sqlalchemy_catalog_write_support import SqlAlchemyCatalogBuildRepository
from tests.integration.rag.test_catalog_storage_roundtrip import (
    ROOT,
    approved_build,
    saved,
)
from tests.integration.rag.test_catalog_storage_roundtrip import database as _catalog_database  # noqa: F401


@pytest.fixture(name="database")
def component_database(request):
    return request.getfixturevalue("_catalog_database")


def run_component_migration(connection, direction):
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = ROOT / "backend/alembic/versions/e8c41a09d652_align_component_occurrences.py"
    spec = importlib.util.spec_from_file_location("d04_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        getattr(module, direction)()


@pytest.mark.parametrize("mode", ["legacy", "repeated", "release"])
async def test_component_downgrade_preserves_legacy_and_refuses_new_data(database, mode):
    engine, factory = database
    members, artifacts, _ = approved_build(repeated=mode == "repeated")
    await SqlAlchemyCatalogBuildRepository(factory).save_build(members=members, artifacts=artifacts)
    async with engine.connect() as connection:
        async with connection.begin():
            if mode != "legacy":
                await connection.execute(
                    text("UPDATE rag_medication_product_component SET release_profile=:profile"),
                    {"profile": "SYNTHETIC_EXTENDED" if mode == "release" else None},
                )
                with pytest.raises(RuntimeError, match="would lose"):
                    await connection.run_sync(run_component_migration, "downgrade")
                assert await connection.scalar(text("SELECT count(*) FROM rag_medication_product_component")) == (
                    2 if mode == "repeated" else 1
                )
            else:
                await connection.run_sync(run_component_migration, "downgrade")
                await connection.run_sync(run_component_migration, "upgrade")
                assert await connection.scalar(text("SELECT count(*) FROM rag_medication_product_component")) == 1


async def test_component_upgrade_refuses_existing_order_conflicts_without_repair(database):
    engine, factory = database
    await saved(factory)
    async with engine.begin() as connection:
        await connection.run_sync(run_component_migration, "downgrade")
        await connection.execute(
            text(
                "INSERT INTO rag_medication_product_component "
                "(id, source_snapshot_id, product_id, ingredient_id, component_role, display_order) "
                "SELECT :id, source_snapshot_id, product_id, ingredient_id, 'EXCIPIENT', display_order "
                "FROM rag_medication_product_component LIMIT 1"
            ),
            {"id": str(uuid4())},
        )
    async with engine.begin() as connection:
        with pytest.raises(RuntimeError, match="order conflicts"):
            await connection.run_sync(run_component_migration, "upgrade")
        assert await connection.scalar(text("SELECT count(*) FROM rag_medication_product_component")) == 2
