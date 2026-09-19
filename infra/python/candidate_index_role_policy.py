"""Candidate Index Builder login policy; invoked in the provisioning administrator transaction."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from infra.python.source_role_policy import _validate_role_boundary, quoted_identifier

CANDIDATE_INDEX_WRITE_TABLES = frozenset(
    {
        "rag_candidate_index_version",
        "rag_candidate_index_member",
    }
)
# Candidate Builder's independent least-privilege read scope.
# Traced strictly from SqlAlchemyCatalogBuildRepository.load_build(),
# SqlAlchemySourceSnapshotRepository.get_snapshot_receipt(), and
# SqlAlchemyCatalogApprovalVerifier.verify().
# Future additions to Catalog read tables will not widen Candidate Builder grants.
CANDIDATE_INDEX_CATALOG_READ_TABLES = frozenset(
    {
        # Catalog persistence tables
        "rag_entity_identity",
        "rag_medication_product",
        "rag_medication_ingredient",
        "rag_medication_alias",
        "rag_medication_product_component",
        "rag_medication_search_entry",
        "rag_catalog_set",
        "rag_catalog_set_source",
        "rag_catalog_set_member",
        "rag_catalog_set_hash",
        # Source provenance tables
        "rag_source",
        "rag_source_endpoint",
        "rag_source_operation",
        "rag_source_snapshot",
        "rag_source_snapshot_verification",
        # Catalog approval tables
        "catalog_source_approval",
        "catalog_build_approval",
        "catalog_build_approval_source",
    }
)
CANDIDATE_INDEX_UPDATE_COLUMNS = {
    "rag_candidate_index_version": ("status",),
}


async def _revoke_builder_column_grants(connection: AsyncConnection, *, schema: str, builder: str) -> None:
    """Revoke column privileges granted specifically to the builder role, leaving runtime intact."""
    statements = await connection.scalars(
        text(
            "SELECT DISTINCT format('REVOKE ALL PRIVILEGES (%I) ON TABLE %I.%I FROM %I', "
            "a.attname, n.nspname, c.relname, r.rolname) "
            "FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "CROSS JOIN LATERAL aclexplode(a.attacl) acl "
            "JOIN pg_roles r ON r.oid=acl.grantee "
            "WHERE n.nspname=:schema AND a.attnum>0 AND NOT a.attisdropped "
            "AND r.rolname=:builder"
        ),
        {"schema": schema, "builder": builder},
    )
    for statement in statements:
        await connection.execute(text(statement))


async def apply_candidate_index_role_policy(
    connection: AsyncConnection,
    *,
    owner: str,
    runtime: str,
    builder: str,
    catalog_writer: str | None = None,
    source_writer: str | None = None,
    management: str | None = None,
) -> None:
    """Apply least-privilege policies for the Candidate Index Builder role.

    Grants:
      - rag_candidate_index_version: SELECT, INSERT, UPDATE(status)
      - rag_candidate_index_member: SELECT, INSERT
      - CANDIDATE_INDEX_CATALOG_READ_TABLES: SELECT only
    Prohibits:
      - Candidate Builder: UPDATE(candidate_index_lock_marker), DELETE, TRUNCATE,
        UPDATE on provenance/business columns, schema CREATE.
      - Runtime #780 ACLs are not modified or regranted here.
    """
    roles = [owner, runtime, builder]
    for opt in (catalog_writer, source_writer, management):
        if opt:
            roles.append(opt)
    if len(set(roles)) != len(roles):
        raise ValueError("Candidate Index Builder must be separate from other database roles")

    owner_sql, builder_sql = (quoted_identifier(v) for v in (owner, builder))
    await _validate_role_boundary(connection, schema="public", runtime=runtime, writer=builder)

    defaults = await connection.scalar(
        text(
            "SELECT count(*) FROM pg_default_acl d CROSS JOIN LATERAL aclexplode(d.defaclacl) a "
            "WHERE d.defaclnamespace=0 AND d.defaclrole=(SELECT oid FROM pg_roles WHERE rolname=:owner) "
            "AND (a.grantee=0 OR a.grantee=(SELECT oid FROM pg_roles WHERE rolname=:builder))"
        ),
        {"owner": owner, "builder": builder},
    )
    if defaults:
        raise ValueError("Remove global default grants before Candidate Index Builder cutover")

    present = set(await connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")))
    required_tables = CANDIDATE_INDEX_WRITE_TABLES | CANDIDATE_INDEX_CATALOG_READ_TABLES
    if not required_tables <= present:
        raise ValueError("Apply Candidate and Catalog migrations before Candidate Index Builder provisioning")

    await connection.execute(text(f"REVOKE CREATE ON SCHEMA public FROM PUBLIC, {builder_sql}"))
    await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {builder_sql}"))

    for kind in ("TABLES", "SEQUENCES"):
        await connection.execute(text(f"REVOKE ALL ON ALL {kind} IN SCHEMA public FROM {builder_sql}"))
        await connection.execute(
            text(
                f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_sql} IN SCHEMA public "
                f"REVOKE ALL ON {kind} FROM PUBLIC, {builder_sql}"
            )
        )

    await _revoke_builder_column_grants(connection, schema="public", builder=builder)

    # Candidate Index write tables
    await connection.execute(text(f"GRANT SELECT, INSERT ON TABLE public.rag_candidate_index_version TO {builder_sql}"))
    await connection.execute(
        text(f"GRANT UPDATE (status) ON TABLE public.rag_candidate_index_version TO {builder_sql}")
    )
    await connection.execute(text(f"GRANT SELECT, INSERT ON TABLE public.rag_candidate_index_member TO {builder_sql}"))

    # Catalog & Source & Approval read tables
    for table in sorted(CANDIDATE_INDEX_CATALOG_READ_TABLES):
        await connection.execute(text(f"GRANT SELECT ON TABLE public.{quoted_identifier(table)} TO {builder_sql}"))
