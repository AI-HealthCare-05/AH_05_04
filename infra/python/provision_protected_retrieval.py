"""Operational provisioning and validation CLI for Issue #368 Protected Retrieval.

Provisions protected database roles, applies exact least-privilege column policies,
and verifies connection boundaries without mutating application state.
Does NOT directly insert protected_identity, protected_dataset, or authorization records.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote_plus

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from infra.python.protected_retrieval_role_policy import (
    PROTECTED_BACKUP_RELATIONS,
    apply_protected_backup_role_policy,
    apply_protected_retrieval_role_policy,
    quoted_identifier,
    validate_protected_backup_connection,
    validate_protected_control_connection,
    validate_protected_data_connection,
)

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def validate_safe_identifier(name: str, label: str) -> str:
    cleaned = name.strip()
    if not _IDENTIFIER_PATTERN.fullmatch(cleaned):
        raise ValueError(f"{label} must be a safe PostgreSQL identifier, got {name!r}")
    return cleaned


def validate_distinct_roles(*roles: str | None, labels: list[str] | None = None) -> None:
    non_empty = [r.strip() for r in roles if r and r.strip()]
    if len(non_empty) != len(set(non_empty)):
        duplicates = [r for r in set(non_empty) if non_empty.count(r) > 1]
        raise ValueError(f"Database roles must be strictly distinct. Found duplicates: {duplicates}")


def run_protected_migrations(
    *,
    admin_url: str,
    schema: str,
    owner_role: str,
    access_role: str,
    control_role: str,
    alembic_ini_path: Path,
) -> None:
    """Run protected retrieval Alembic migrations to head synchronously."""
    if not alembic_ini_path.is_file():
        raise FileNotFoundError(f"Protected Alembic configuration not found: {alembic_ini_path}")

    env_overrides = {
        "PROTECTED_DATABASE_URL": admin_url,
        "PROTECTED_DB_SCHEMA": schema,
        "PROTECTED_DB_OWNER_ROLE": owner_role,
        "PROTECTED_DB_ACCESS_ROLE": access_role,
        "PROTECTED_DB_CONTROL_ROLE": control_role,
    }
    prev_env = {k: os.environ.get(k) for k in env_overrides}
    os.environ.update(env_overrides)
    try:
        cfg = AlembicConfig(str(alembic_ini_path))
        command.upgrade(cfg, "head")
    finally:
        for k, v in prev_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def _verify_login_role_attributes(
    connection: AsyncConnection,
    role_name: str,
    password: str,
) -> None:
    quoted_name = quoted_identifier(role_name)
    escaped_password = password.replace("'", "''")

    row = (
        (
            await connection.execute(
                text(
                    "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, "
                    "rolcreaterole, rolreplication, rolbypassrls, rolinherit "
                    "FROM pg_roles WHERE rolname = :name"
                ),
                {"name": role_name},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        await connection.exec_driver_sql(
            f"CREATE ROLE {quoted_name} WITH LOGIN INHERIT NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
            f"PASSWORD '{escaped_password}'"
        )
    else:
        if (
            not row["rolcanlogin"]
            or row["rolsuper"]
            or row["rolcreatedb"]
            or row["rolcreaterole"]
            or row["rolreplication"]
            or row["rolbypassrls"]
        ):
            raise ValueError(f"Existing role {role_name} has unsafe or superuser privileges")
        await connection.exec_driver_sql(
            f"ALTER ROLE {quoted_name} WITH LOGIN INHERIT NOSUPERUSER "
            "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
            f"PASSWORD '{escaped_password}'"
        )


async def _validate_existing_backup_login_before_password_mutation(
    connection: AsyncConnection,
    *,
    backup_login: str,
    schema: str,
) -> None:
    row = (
        (
            await connection.execute(
                text(
                    "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, "
                    "rolcreaterole, rolreplication, rolbypassrls, rolinherit "
                    "FROM pg_roles WHERE rolname = :name"
                ),
                {"name": backup_login},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return
    if (
        not row["rolcanlogin"]
        or row["rolsuper"]
        or row["rolcreatedb"]
        or row["rolcreaterole"]
        or row["rolreplication"]
        or row["rolbypassrls"]
    ):
        raise ValueError(f"Existing role {backup_login} has unsafe or superuser privileges")

    backup_unsafe = await connection.scalar(
        text(
            "SELECT EXISTS ("
            "SELECT 1 FROM pg_auth_members membership "
            "JOIN pg_roles member ON member.oid = membership.member "
            "WHERE member.rolname = :backup_login"
            ") OR EXISTS ("
            "SELECT 1 FROM pg_roles role WHERE role.rolname = :backup_login AND ("
            "EXISTS (SELECT 1 FROM pg_class WHERE relowner = role.oid) OR "
            "EXISTS (SELECT 1 FROM pg_namespace WHERE nspowner = role.oid) OR "
            "EXISTS (SELECT 1 FROM pg_database WHERE datdba = role.oid)"
            ")"
            ")"
        ),
        {"backup_login": backup_login},
    )
    if backup_unsafe:
        raise ValueError("Protected backup login must have no memberships or ownership")

    privilege_query = text(
        "SELECT EXISTS ("
        "SELECT 1 FROM pg_class class "
        "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
        "WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND namespace.nspname NOT LIKE 'pg_toast%' "
        "AND class.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "AND ((namespace.nspname <> :schema AND ("
        "has_table_privilege(:backup_login, class.oid, 'SELECT') "
        "OR has_table_privilege(:backup_login, class.oid, 'INSERT') "
        "OR has_table_privilege(:backup_login, class.oid, 'UPDATE') "
        "OR has_table_privilege(:backup_login, class.oid, 'DELETE') "
        "OR has_table_privilege(:backup_login, class.oid, 'TRUNCATE') "
        "OR has_table_privilege(:backup_login, class.oid, 'TRIGGER') "
        "OR has_table_privilege(:backup_login, class.oid, 'REFERENCES'))) "
        "OR (namespace.nspname = :schema AND ("
        "(class.relname NOT IN :protected_relations "
        "AND has_table_privilege(:backup_login, class.oid, 'SELECT')) "
        "OR has_table_privilege(:backup_login, class.oid, 'INSERT') "
        "OR has_table_privilege(:backup_login, class.oid, 'UPDATE') "
        "OR has_table_privilege(:backup_login, class.oid, 'DELETE') "
        "OR has_table_privilege(:backup_login, class.oid, 'TRUNCATE') "
        "OR has_table_privilege(:backup_login, class.oid, 'TRIGGER') "
        "OR has_table_privilege(:backup_login, class.oid, 'REFERENCES'))))"
        ") OR EXISTS ("
        "SELECT 1 FROM pg_class class "
        "JOIN pg_namespace namespace ON namespace.oid = class.relnamespace "
        "WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND namespace.nspname NOT LIKE 'pg_toast%' "
        "AND class.relkind = 'S' "
        "AND (has_sequence_privilege(:backup_login, class.oid, 'SELECT') "
        "OR has_sequence_privilege(:backup_login, class.oid, 'UPDATE') "
        "OR has_sequence_privilege(:backup_login, class.oid, 'USAGE'))"
        ") OR EXISTS ("
        "SELECT 1 FROM pg_namespace namespace "
        "WHERE namespace.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND namespace.nspname NOT LIKE 'pg_toast%' "
        "AND has_schema_privilege(:backup_login, namespace.oid, 'CREATE')"
        ")"
    ).bindparams(bindparam("protected_relations", expanding=True))
    outside_scope_access = await connection.scalar(
        privilege_query,
        {
            "backup_login": backup_login,
            "schema": schema,
            "protected_relations": tuple(PROTECTED_BACKUP_RELATIONS),
        },
    )
    if outside_scope_access:
        raise ValueError("Protected backup login has privileges outside the exact scope")


async def provision_protected_database_roles(
    *,
    admin_connection: AsyncConnection,
    admin_login: str,
    schema: str,
    owner_role: str,
    access_role: str,
    control_role: str,
    data_login: str,
    data_password: str,
    control_login: str,
    control_password: str,
    backup_login: str,
    backup_password: str,
) -> None:
    """Create operational logins and configure exact column-level role policies."""
    validate_distinct_roles(
        owner_role,
        access_role,
        control_role,
        data_login,
        control_login,
        backup_login,
        admin_login,
    )

    # 1. Verify existence of NOLOGIN roles created by migration bootstrap
    for nologin_role in (owner_role, access_role, control_role):
        exists = await admin_connection.scalar(
            text("SELECT 1 FROM pg_roles WHERE rolname = :role AND NOT rolcanlogin"),
            {"role": nologin_role},
        )
        if not exists:
            raise ValueError(f"Bootstrap NOLOGIN role {nologin_role} does not exist or has login enabled")

    # 2. Validate an existing backup login before any credential mutation.
    await _validate_existing_backup_login_before_password_mutation(
        admin_connection,
        backup_login=backup_login,
        schema=schema,
    )

    # 3. Provision operational logins
    await _verify_login_role_attributes(admin_connection, data_login, data_password)
    await _verify_login_role_attributes(admin_connection, control_login, control_password)
    await _verify_login_role_attributes(admin_connection, backup_login, backup_password)

    # 4. Clean and assign strictly isolated memberships
    quoted_access = quoted_identifier(access_role)
    quoted_control = quoted_identifier(control_role)
    quoted_data_login = quoted_identifier(data_login)
    quoted_control_login = quoted_identifier(control_login)

    # Prevent cross-membership
    await admin_connection.exec_driver_sql(f"REVOKE {quoted_control} FROM {quoted_data_login}")
    await admin_connection.exec_driver_sql(f"REVOKE {quoted_access} FROM {quoted_control_login}")

    # Grant primary plane group roles
    await admin_connection.exec_driver_sql(f"GRANT {quoted_access} TO {quoted_data_login}")
    await admin_connection.exec_driver_sql(f"GRANT {quoted_control} TO {quoted_control_login}")

    # 5. Apply explicit column-level least-privilege role policy
    await apply_protected_retrieval_role_policy(
        admin_connection,
        schema=schema,
        owner=owner_role,
        data_access=access_role,
        control=control_role,
    )
    await apply_protected_backup_role_policy(
        admin_connection,
        schema=schema,
        owner=owner_role,
        backup_login=backup_login,
    )


async def verify_protected_connections(
    *,
    db_host: str,
    db_port: int,
    db_name: str,
    schema: str,
    access_role: str,
    control_role: str,
    data_login: str,
    data_password: str,
    control_login: str,
    control_password: str,
    backup_login: str,
    backup_password: str,
) -> None:
    """Validate that limited logins can connect and satisfy exact policy boundaries."""
    data_url = (
        f"postgresql+asyncpg://{quote_plus(data_login)}:{quote_plus(data_password)}@{db_host}:{db_port}/{db_name}"
    )
    control_url = (
        f"postgresql+asyncpg://{quote_plus(control_login)}:{quote_plus(control_password)}@{db_host}:{db_port}/{db_name}"
    )

    data_engine = create_async_engine(data_url)
    try:
        async with data_engine.connect() as conn:
            await validate_protected_data_connection(
                conn,
                schema=schema,
                data_access=access_role,
                control=control_role,
            )
    finally:
        await data_engine.dispose()

    control_engine = create_async_engine(control_url)
    try:
        async with control_engine.connect() as conn:
            await validate_protected_control_connection(
                conn,
                schema=schema,
                data_access=access_role,
                control=control_role,
            )
    finally:
        await control_engine.dispose()

    backup_url = (
        f"postgresql+asyncpg://{quote_plus(backup_login)}:{quote_plus(backup_password)}@{db_host}:{db_port}/{db_name}"
    )
    backup_engine = create_async_engine(backup_url)
    try:
        async with backup_engine.connect() as conn:
            await validate_protected_backup_connection(conn, schema=schema)
    finally:
        await backup_engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--provision",
        action="store_true",
        help="Run migrations, configure logins and memberships, and apply role policies.",
    )
    mode.add_argument(
        "--verify-only",
        action="store_true",
        help="Verify connection boundaries with limited logins without performing mutations.",
    )
    return parser.parse_args()


async def async_main(args: argparse.Namespace) -> int:
    db_host = os.getenv("DB_HOST", "postgres").strip()
    db_port = int(os.getenv("DB_PORT", "5432").strip())
    db_name = os.getenv("DB_NAME", "").strip()

    schema = validate_safe_identifier(os.getenv("PROTECTED_DB_SCHEMA", "").strip(), "PROTECTED_DB_SCHEMA")
    owner_role = validate_safe_identifier(os.getenv("PROTECTED_DB_OWNER_ROLE", "").strip(), "PROTECTED_DB_OWNER_ROLE")
    access_role = validate_safe_identifier(
        os.getenv("PROTECTED_DB_ACCESS_ROLE", "").strip(), "PROTECTED_DB_ACCESS_ROLE"
    )
    control_role = validate_safe_identifier(
        os.getenv("PROTECTED_DB_CONTROL_ROLE", "").strip(), "PROTECTED_DB_CONTROL_ROLE"
    )

    data_login = validate_safe_identifier(os.getenv("PROTECTED_DB_USER", "").strip(), "PROTECTED_DB_USER")
    data_password = os.getenv("PROTECTED_DB_PASSWORD", "").strip()
    control_login = validate_safe_identifier(
        os.getenv("PROTECTED_DB_CONTROL_USER", "").strip(), "PROTECTED_DB_CONTROL_USER"
    )
    control_password = os.getenv("PROTECTED_DB_CONTROL_PASSWORD", "").strip()
    backup_login = validate_safe_identifier(
        os.getenv("PROTECTED_DB_BACKUP_USER", "").strip(), "PROTECTED_DB_BACKUP_USER"
    )
    backup_password = os.getenv("PROTECTED_DB_BACKUP_PASSWORD", "").strip()

    if not db_name or not data_password or not control_password or not backup_password:
        sys.stderr.write("Missing required DB connection or password environment variables\n")
        return 1

    other_roles = [
        os.getenv("DB_ADMIN_USER", "").strip(),
        os.getenv("DB_MIGRATION_USER", "").strip(),
        os.getenv("DB_APP_USER", "").strip(),
        os.getenv("SOURCE_WRITER_USER", "").strip(),
        os.getenv("SOURCE_MANAGEMENT_USER", "").strip(),
        os.getenv("CATALOG_WRITER_USER", "").strip(),
        os.getenv("CATALOG_APPROVAL_USER", "").strip(),
        os.getenv("KNOWLEDGE_INDEX_BUILDER_USER", "").strip(),
        os.getenv("CANDIDATE_INDEX_BUILDER_USER", "").strip(),
        os.getenv("ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE", "").strip(),
    ]
    validate_distinct_roles(
        owner_role,
        access_role,
        control_role,
        data_login,
        control_login,
        backup_login,
        *other_roles,
    )

    if args.provision:
        admin_user = os.getenv("DB_ADMIN_USER", "").strip()
        admin_password = os.getenv("DB_ADMIN_PASSWORD", "").strip()
        if not admin_user or not admin_password:
            sys.stderr.write("DB_ADMIN_USER and DB_ADMIN_PASSWORD required for --provision\n")
            return 1

        admin_url = (
            f"postgresql+asyncpg://{quote_plus(admin_user)}:{quote_plus(admin_password)}@{db_host}:{db_port}/{db_name}"
        )
        alembic_ini = Path(__file__).resolve().parents[1] / "protected_retrieval" / "alembic.ini"

        # 1. Run synchronous Alembic migrations in a worker thread outside this event loop.
        await asyncio.to_thread(
            run_protected_migrations,
            admin_url=admin_url,
            schema=schema,
            owner_role=owner_role,
            access_role=access_role,
            control_role=control_role,
            alembic_ini_path=alembic_ini,
        )

        # 2. Roles and Policy via admin connection
        admin_engine = create_async_engine(admin_url)
        try:
            async with admin_engine.begin() as conn:
                await provision_protected_database_roles(
                    admin_connection=conn,
                    admin_login=admin_user,
                    schema=schema,
                    owner_role=owner_role,
                    access_role=access_role,
                    control_role=control_role,
                    data_login=data_login,
                    data_password=data_password,
                    control_login=control_login,
                    control_password=control_password,
                    backup_login=backup_login,
                    backup_password=backup_password,
                )
        finally:
            await admin_engine.dispose()

    # Both --provision (post-check) and --verify-only run connection verification
    await verify_protected_connections(
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        schema=schema,
        access_role=access_role,
        control_role=control_role,
        data_login=data_login,
        data_password=data_password,
        control_login=control_login,
        control_password=control_password,
        backup_login=backup_login,
        backup_password=backup_password,
    )

    sys.stdout.write("PROTECTED_DATABASE_PROVISIONING_VERIFIED: PASS\n")
    return 0


def main() -> None:
    try:
        args = parse_args()
        exit_code = asyncio.run(async_main(args))
    except Exception:
        sys.stderr.write("PROTECTED_DATABASE_PROVISIONING_FAILED\n")
        exit_code = 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
