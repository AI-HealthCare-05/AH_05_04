"""Least-privilege role for the isolated Source/Catalog management process."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from infra.python.source_role_policy import _revoke_column_grants, _validate_role_boundary, quoted_identifier

MANAGEMENT_COLUMNS = {
    "rag_source": ("display_name", "owner_name", "license_name", "attribution_text", "purpose", "updated_at"),
    "rag_source_endpoint": ("display_name", "updated_at"),
    "rag_source_operation": ("display_name", "updated_at"),
    # SELECT FOR UPDATE needs UPDATE on one column. The API never edits this column.
    "rag_source_snapshot": ("verified_at",),
    "rag_medication_product": ("product_name", "manufacturer_name", "strength_text", "dosage_form"),
    "rag_medication_ingredient": ("ingredient_name",),
    "rag_medication_alias": ("alias_text",),
    "rag_medication_product_component": ("amount_text",),
}
CATALOG_TABLES = frozenset(name for name in MANAGEMENT_COLUMNS if name.startswith("rag_medication_"))


async def apply_management_role_policy(
    connection: AsyncConnection, *, owner: str, runtime: str, writer: str, management: str
) -> None:
    if len({owner, runtime, writer, management}) != 4:
        raise ValueError("Management requires a separate database role")
    await _validate_role_boundary(connection, schema="public", runtime=runtime, writer=management)
    role_sql, owner_sql = quoted_identifier(management), quoted_identifier(owner)
    await connection.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role_sql}"))
    await connection.execute(text(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role_sql}"))
    await _revoke_column_grants(connection, schema="public", runtime=runtime, writer=management)
    await connection.execute(text(f"REVOKE CREATE ON SCHEMA public FROM {role_sql}"))
    await connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {role_sql}"))
    for object_type in ("TABLES", "SEQUENCES"):
        for scope in ("", "IN SCHEMA public"):
            await connection.execute(
                text(
                    f"ALTER DEFAULT PRIVILEGES FOR ROLE {owner_sql} {scope} REVOKE ALL ON {object_type} FROM {role_sql}"
                )
            )
    for table, columns in MANAGEMENT_COLUMNS.items():
        target = f"public.{quoted_identifier(table)}"
        await connection.execute(text(f"GRANT SELECT, DELETE ON {target} TO {role_sql}"))
        names = ", ".join(quoted_identifier(column) for column in columns)
        await connection.execute(text(f"GRANT UPDATE ({names}) ON {target} TO {role_sql}"))
    await connection.execute(
        text(f'GRANT SELECT (id, is_active, account_status, token_version) ON public."user" TO {role_sql}')
    )
    await connection.execute(
        text(f"GRANT SELECT, UPDATE (lock_version) ON public.source_management_permission TO {role_sql}")
    )
    await connection.execute(text(f"GRANT SELECT, INSERT ON public.source_management_audit TO {role_sql}"))
    # Only the referencing columns are needed to reject edits to referenced resources.
    # This also covers new migrations after provisioning is rerun, without exposing PII.
    statements = await connection.scalars(
        text(
            "SELECT DISTINCT format('GRANT SELECT (%I) ON TABLE %I.%I TO %I', a.attname, n.nspname, t.relname, CAST(:role AS text)) "
            "FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
            "JOIN pg_namespace n ON n.oid=t.relnamespace "
            "JOIN pg_class parent ON parent.oid=c.confrelid "
            "JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=ANY(c.conkey) "
            "WHERE c.contype='f' AND n.nspname='public' AND parent.relname=ANY(:tables)"
        ),
        {"role": management, "tables": list(MANAGEMENT_COLUMNS)},
    )
    for statement in statements:
        await connection.execute(text(statement))


async def validate_management_connection(connection: AsyncConnection) -> None:
    unsafe = await connection.scalar(
        text(
            "SELECT r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls OR r.rolreplication "
            "OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid) "
            "OR EXISTS (SELECT 1 FROM pg_class WHERE relowner=r.oid) "
            "OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner=r.oid) "
            "OR EXISTS (SELECT 1 FROM pg_database WHERE datdba=r.oid) "
            "FROM pg_roles r WHERE r.rolname=current_user"
        )
    )
    if unsafe is not False:
        raise ValueError("Management connection must not own objects or inherit administrative rights")
    forbidden = await connection.scalar(
        text(
            "SELECT has_column_privilege(current_user, 'source_management_permission', 'enabled', 'UPDATE') "
            "OR has_table_privilege(current_user, 'source_management_permission', 'INSERT,DELETE,TRUNCATE') "
            "OR has_table_privilege(current_user, 'source_management_audit', 'UPDATE,DELETE,TRUNCATE') "
            "OR has_column_privilege(current_user, 'user', 'is_admin', 'UPDATE') "
            "OR has_column_privilege(current_user, 'user', 'hashed_password', 'SELECT') "
            "OR has_schema_privilege(current_user, 'public', 'CREATE')"
        )
    )
    required = await connection.scalar(
        text(
            "SELECT has_table_privilege(current_user, 'source_management_audit', 'SELECT') "
            "AND has_table_privilege(current_user, 'source_management_audit', 'INSERT') "
            "AND has_column_privilege(current_user, 'source_management_permission', 'lock_version', 'UPDATE')"
        )
    )
    if forbidden or not required:
        raise ValueError("Management role policy is missing or grants forbidden permissions")
    tables = await connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    for table in tables:
        for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "TRIGGER", "REFERENCES"):
            allowed = privilege == "INSERT" and table == "source_management_audit"
            allowed |= privilege == "DELETE" and table in MANAGEMENT_COLUMNS
            if not allowed and await connection.scalar(
                text("SELECT has_table_privilege(current_user, :table, :privilege)"),
                {"table": f'public."{table}"', "privilege": privilege},
            ):
                raise ValueError("Management role has unexpected table privileges")
        columns = await connection.scalars(
            text(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table"
            ),
            {"table": table},
        )
        allowed_updates = set(MANAGEMENT_COLUMNS.get(table, ()))
        if table == "source_management_permission":
            allowed_updates.add("lock_version")
        for column in columns:
            if column not in allowed_updates and await connection.scalar(
                text("SELECT has_column_privilege(current_user, :table, :column, 'UPDATE')"),
                {"table": f'public."{table}"', "column": column},
            ):
                raise ValueError("Management role has unexpected column update privileges")
