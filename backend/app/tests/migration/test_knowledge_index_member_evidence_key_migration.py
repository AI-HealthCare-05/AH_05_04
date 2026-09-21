"""Migration coverage for legacy Knowledge Evidence Index members."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


def _load_migration():
    path = Path(__file__).parents[3] / "alembic/versions/923a1b2c3d4e_knowledge_index_member_evidence_key.py"
    spec = importlib.util.spec_from_file_location("knowledge_index_member_evidence_key_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_upgrade_preserves_populated_legacy_member_with_null_binding(db_session) -> None:
    module = _load_migration()
    schema = f"knowledge_index_key_migration_{uuid4().hex}"
    await db_session.execute(text(f'CREATE SCHEMA "{schema}"'))
    await db_session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    await db_session.execute(
        text(
            "CREATE TABLE rag_knowledge_index_member ("
            "knowledge_index_id CHAR(36) NOT NULL, "
            "knowledge_chunk_id CHAR(36) NOT NULL, "
            "source_snapshot_id CHAR(36) NOT NULL)"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO rag_knowledge_index_member "
            "(knowledge_index_id, knowledge_chunk_id, source_snapshot_id) "
            "VALUES (:index_id, :chunk_id, :snapshot_id)"
        ),
        {"index_id": str(uuid4()), "chunk_id": str(uuid4()), "snapshot_id": str(uuid4())},
    )
    connection = await db_session.connection()

    def invoke(sync_connection) -> None:
        module.op = Operations(MigrationContext.configure(sync_connection))
        module.upgrade()

    await connection.run_sync(invoke)

    assert await db_session.scalar(text("SELECT evidence_key FROM rag_knowledge_index_member")) is None
    constraints = await db_session.execute(
        text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_schema = current_schema() AND table_name = 'rag_knowledge_index_member'"
        )
    )
    assert "uq_rag_knowledge_index_member_snapshot_evidence_key" in {row[0] for row in constraints}
