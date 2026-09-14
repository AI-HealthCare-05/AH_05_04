"""Least-privilege policy for Knowledge Evidence Index build and read access."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from infra.python.source_role_policy import (
    SOURCE_TABLES,
    _revoke_column_grants,
    _validate_role_boundary,
    quoted_identifier,
)

KNOWLEDGE_INDEX_WRITE_TABLES = frozenset(
    {"knowledge_document", "knowledge_chunk", "rag_knowledge_index", "rag_knowledge_index_member"}
)
KNOWLEDGE_INDEX_RUNTIME_READ_TABLES = KNOWLEDGE_INDEX_WRITE_TABLES
KNOWLEDGE_INDEX_SOURCE_READ_TABLES = frozenset(SOURCE_TABLES) - {"rag_source_snapshot_verification"}
# PostgreSQL row-locking clauses require UPDATE privilege on every locked table.
# CHECK=0 markers provide that privilege without making provenance or business columns mutable.
KNOWLEDGE_INDEX_LOCK_COLUMNS = {
    "rag_source": "knowledge_index_lock_marker",
    "rag_source_endpoint": "knowledge_index_lock_marker",
    "rag_source_operation": "knowledge_index_lock_marker",
    "rag_source_snapshot": "management_lock_marker",
    "rag_source_snapshot_member": "knowledge_index_lock_marker",
    "rag_source_ingestion_run": "knowledge_index_lock_marker",
    "rag_source_ingestion_artifact": "knowledge_index_lock_marker",
    "knowledge_document": "knowledge_index_lock_marker",
    "knowledge_chunk": "knowledge_index_lock_marker",
    "rag_knowledge_index": "knowledge_index_lock_marker",
}


async def apply_knowledge_index_role_policy(
    connection: AsyncConnection,
    *,
    owner: str,
    runtime: str,
    builder: str,
) -> None:
    """Grant immutable index writes and lock-only parent validation to a separate builder."""
    if len({owner, runtime, builder}) != 3:
        raise ValueError("Knowledge Index Builder must be separate from Migration and Runtime roles")
    await _validate_role_boundary(connection, schema="public", runtime=runtime, writer=builder)
    owner_sql, runtime_sql, builder_sql = (quoted_identifier(value) for value in (owner, runtime, builder))
    required = KNOWLEDGE_INDEX_WRITE_TABLES | KNOWLEDGE_INDEX_SOURCE_READ_TABLES
    present = set(await connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
    if not required <= present:
        raise ValueError("Apply Knowledge Evidence Index migrations before provisioning Builder privileges")

    await connection.execute(text(f"REVOKE CREATE ON SCHEMA public FROM PUBLIC, {builder_sql}"))
    await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {builder_sql}"))
    for object_type in ("TABLES", "SEQUENCES"):
        await connection.execute(text(f"REVOKE ALL ON ALL {object_type} IN SCHEMA public FROM {builder_sql}"))
        await connection.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_sql} IN SCHEMA public "
                f"REVOKE ALL ON {object_type} FROM PUBLIC, {builder_sql}"
            )
        )
    await _revoke_column_grants(connection, schema="public", runtime=runtime, writer=builder)

    for table in sorted(KNOWLEDGE_INDEX_WRITE_TABLES):
        target = f"public.{quoted_identifier(table)}"
        columns = list(
            await connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=:table"
                ),
                {"table": table},
            )
        )
        names = ", ".join(quoted_identifier(column) for column in columns)
        await connection.execute(text(f"REVOKE ALL ON TABLE {target} FROM PUBLIC, {runtime_sql}, {builder_sql}"))
        await connection.execute(
            text(f"REVOKE ALL ({names}) ON TABLE {target} FROM PUBLIC, {runtime_sql}, {builder_sql}")
        )
        await connection.execute(text(f"GRANT SELECT ON TABLE {target} TO {runtime_sql}"))
        await connection.execute(text(f"GRANT SELECT, INSERT ON TABLE {target} TO {builder_sql}"))

    for table in sorted(KNOWLEDGE_INDEX_SOURCE_READ_TABLES - KNOWLEDGE_INDEX_WRITE_TABLES):
        await connection.execute(text(f"GRANT SELECT ON TABLE public.{quoted_identifier(table)} TO {builder_sql}"))
    for table, column in KNOWLEDGE_INDEX_LOCK_COLUMNS.items():
        await connection.execute(
            text(
                f"GRANT UPDATE ({quoted_identifier(column)}) "
                f"ON TABLE public.{quoted_identifier(table)} TO {builder_sql}"
            )
        )
