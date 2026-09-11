"""Isolated Catalog Writer composition; caller supplies the existing v2 build and verifier."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_catalog_write_support import SqlAlchemyCatalogBuildRepository
from infra.python.catalog_role_policy import CATALOG_LOCK_COLUMNS, CATALOG_READ_TABLES, CATALOG_WRITE_TABLES


def writer_url(environment: Mapping[str, str]) -> URL:
    if any(
        environment.get(key)
        for key in (
            "DB_PASSWORD",
            "DB_APP_PASSWORD",
            "DB_ADMIN_PASSWORD",
            "DB_MIGRATION_PASSWORD",
            "SOURCE_WRITER_PASSWORD",
            "SOURCE_MANAGEMENT_PASSWORD",
        )
    ):
        raise ValueError("Catalog Writer requires an isolated credential environment")
    values = {key: environment.get(f"CATALOG_WRITER_{key}", "") for key in ("HOST", "PORT", "NAME", "USER", "PASSWORD")}
    if any(not value.strip() for value in values.values()):
        raise ValueError("Catalog Writer configuration is incomplete")
    port = int(values["PORT"])
    if not 1 <= port <= 65535:
        raise ValueError("Catalog Writer port is invalid")
    return URL.create(
        "postgresql+asyncpg",
        username=values["USER"],
        password=values["PASSWORD"],
        host=values["HOST"],
        port=port,
        database=values["NAME"],
    )


async def validate_catalog_writer(connection: AsyncConnection) -> None:
    safe = await connection.scalar(
        text(
            "SELECT NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls OR r.rolreplication "
            "OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid) "
            "OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=r.oid) "
            "OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='public' AND nspowner=r.oid) "
            "OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relowner=r.oid) "
            "OR has_schema_privilege(current_user, 'public', 'CREATE')) "
            "AND current_user=session_user AND current_schema()='public' FROM pg_roles r WHERE rolname=current_user"
        )
    )
    if safe is not True:
        raise ValueError("Catalog Writer requires a non-owner login without administrative privileges or memberships")
    rows = (
        await connection.execute(
            text(
                "SELECT c.relname, p.privilege FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),('REFERENCES'),('TRIGGER')) p(privilege) "
                "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') "
                "AND has_table_privilege(current_user,c.oid,p.privilege)"
            )
        )
    ).all()
    expected = {(name, "SELECT") for name in CATALOG_READ_TABLES} | {(name, "INSERT") for name in CATALOG_WRITE_TABLES}
    if set(rows) != expected:
        raise ValueError("Catalog Writer table privileges do not match the allowlist")
    # Include effective column grants so PUBLIC and independently granted privileges cannot bypass table REVOKE.
    columns = (
        await connection.execute(
            text(
                "SELECT c.relname,a.attname,p.privilege FROM pg_attribute a "
                "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('REFERENCES')) p(privilege) "
                "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped "
                "AND has_column_privilege(current_user,c.oid,a.attnum,p.privilege)"
            )
        )
    ).all()
    for table, column, privilege in columns:
        if (table, privilege) in expected or (privilege == "UPDATE" and CATALOG_LOCK_COLUMNS.get(table) == column):
            continue
        raise ValueError("Catalog Writer column privileges do not match the allowlist")
    if not {(table, column, "UPDATE") for table, column in CATALOG_LOCK_COLUMNS.items()} <= set(columns):
        raise ValueError("Catalog Writer row-lock privileges are missing")


@asynccontextmanager
async def catalog_writer_repository(environment: Mapping[str, str]) -> AsyncIterator[SqlAlchemyCatalogBuildRepository]:
    engine = create_async_engine(writer_url(environment), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await validate_catalog_writer(connection)
        yield SqlAlchemyCatalogBuildRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
