import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError


async def test_forward_migration_constraints_cascade_and_downgrade_guard(db_session):
    migration_path = Path(__file__).parents[3] / "alembic/versions/633a1b2c3d4e_guide_chat_feedback.py"
    spec = importlib.util.spec_from_file_location("feedback_migration", migration_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = f"feedback_migration_{uuid4().hex}"
    await db_session.execute(text(f'CREATE SCHEMA "{schema}"'))
    await db_session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    for table in ("guide", "chat_message"):
        await db_session.execute(text(f"CREATE TABLE {table} (id CHAR(36) PRIMARY KEY)"))
    connection = await db_session.connection()

    def invoke(sync_connection, action):
        module.op = Operations(MigrationContext.configure(sync_connection))
        getattr(module, action)()

    await connection.run_sync(invoke, "upgrade")
    for table, parent, key in (
        ("guide_feedback", "guide", "guide_id"),
        ("chat_message_feedback", "chat_message", "chat_message_id"),
    ):
        parent_id = str(uuid4())
        await db_session.execute(text(f"INSERT INTO {parent} VALUES (:id)"), {"id": parent_id})
        statement = text(f"INSERT INTO {table} (id, {key}, rating, comment) VALUES (:id, :target, :rating, :comment)")
        for target, rating, comment in (
            (str(uuid4()), "POSITIVE", None),
            (parent_id, "INVALID", None),
            (parent_id, "POSITIVE", "x" * 1001),
        ):
            with pytest.raises(DBAPIError):
                async with db_session.begin_nested():
                    await db_session.execute(
                        statement, {"id": str(uuid4()), "target": target, "rating": rating, "comment": comment}
                    )
        values = {"id": str(uuid4()), "target": parent_id, "rating": "NEGATIVE", "comment": "synthetic"}
        await db_session.execute(statement, values)
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                await db_session.execute(statement, {**values, "id": str(uuid4())})
        with pytest.raises(RuntimeError, match="Feedback exists"):
            await connection.run_sync(invoke, "downgrade")
        await db_session.execute(text(f"DELETE FROM {parent} WHERE id = :id"), {"id": parent_id})
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    await connection.run_sync(invoke, "downgrade")
    await connection.run_sync(invoke, "upgrade")
