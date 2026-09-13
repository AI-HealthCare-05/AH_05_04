"""Catalog-only login policy; invoked in the provisioning administrator transaction."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from infra.python.source_role_policy import _revoke_column_grants, _validate_role_boundary, quoted_identifier

CATALOG_WRITE_TABLES = frozenset(
    "rag_entity_identity rag_medication_product rag_medication_ingredient rag_medication_alias "
    "rag_medication_product_component rag_medication_search_entry rag_catalog_set "
    "rag_catalog_set_source rag_catalog_set_member rag_catalog_set_hash".split()
)
CATALOG_LOCK_COLUMNS = {
    "rag_source_snapshot": "management_lock_marker",
    "rag_medication_product": "catalog_lock_marker",
    "rag_medication_alias": "catalog_lock_marker",
}
CATALOG_SOURCE_TABLES = frozenset(
    "rag_source rag_source_endpoint rag_source_operation rag_source_snapshot rag_source_snapshot_verification".split()
)
CATALOG_READ_TABLES = CATALOG_WRITE_TABLES | CATALOG_SOURCE_TABLES


async def apply_catalog_role_policy(
    connection: AsyncConnection,
    *,
    owner: str,
    runtime: str,
    writer: str,
    source_writer: str,
    management: str | None = None,
) -> None:
    roles = [owner, runtime, writer, source_writer] + ([management] if management else [])
    if len(set(roles)) != len(roles):
        raise ValueError("Catalog Writer must be separate from other database roles")
    owner_sql, runtime_sql, writer_sql = (quoted_identifier(v) for v in (owner, runtime, writer))
    await _validate_role_boundary(connection, schema="public", runtime=runtime, writer=writer)
    defaults = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a "
            "WHERE d.defaclnamespace=0 AND d.defaclrole=(SELECT oid FROM pg_roles WHERE rolname=:owner) "
            "AND (a.grantee=0 OR a.grantee=(SELECT oid FROM pg_roles WHERE rolname=:writer))"
        ),
        {"owner": owner, "writer": writer},
    )
    if defaults:
        raise ValueError("Remove global default grants before Catalog Writer cutover")
    present = set(await connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
    if not CATALOG_READ_TABLES <= present:
        raise ValueError("Apply Catalog migrations before Writer provisioning")
    await connection.execute(text(f"REVOKE CREATE ON SCHEMA public FROM PUBLIC, {writer_sql}"))
    await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {writer_sql}"))
    for kind in ("TABLES", "SEQUENCES"):
        await connection.execute(text(f"REVOKE ALL ON ALL {kind} IN SCHEMA public FROM {writer_sql}"))
        await connection.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_sql} IN SCHEMA public "
                f"REVOKE ALL ON {kind} FROM PUBLIC, {writer_sql}"
            )
        )
    await _revoke_column_grants(connection, schema="public", runtime=runtime, writer=writer)
    for table in sorted(CATALOG_WRITE_TABLES):
        target = f"public.{quoted_identifier(table)}"
        await connection.execute(text(f"REVOKE ALL ON TABLE {target} FROM PUBLIC, {runtime_sql}"))
        # Column grants are independent of table grants (including legacy runtime INSERT).
        columns = list(
            await connection.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table"
                ),
                {"table": table},
            )
        )
        names = ", ".join(quoted_identifier(c) for c in columns)
        await connection.execute(text(f"REVOKE ALL ({names}) ON TABLE {target} FROM PUBLIC, {runtime_sql}"))
        await connection.execute(text(f"GRANT SELECT ON TABLE {target} TO {runtime_sql}"))
        await connection.execute(text(f"GRANT SELECT, INSERT ON TABLE {target} TO {writer_sql}"))
    for table in sorted(CATALOG_SOURCE_TABLES):
        await connection.execute(text(f"GRANT SELECT ON TABLE public.{quoted_identifier(table)} TO {writer_sql}"))
    for table, column in CATALOG_LOCK_COLUMNS.items():
        await connection.execute(
            text(
                f"GRANT UPDATE ({quoted_identifier(column)}) ON TABLE public.{quoted_identifier(table)} TO {writer_sql}"
            )
        )
