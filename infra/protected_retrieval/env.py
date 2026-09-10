from __future__ import annotations

import asyncio
import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncConnection, async_engine_from_config

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_REQUIRED = (
    "PROTECTED_DATABASE_URL",
    "PROTECTED_DB_SCHEMA",
    "PROTECTED_DB_OWNER_ROLE",
    "PROTECTED_DB_ACCESS_ROLE",
)


def _required_environment() -> dict[str, str]:
    values: dict[str, str] = {}
    for name in _REQUIRED:
        value = os.getenv(name, "").strip()
        if not value:
            raise RuntimeError(f"{name} is required")
        values[name] = value
    for name in _REQUIRED[1:]:
        if _IDENTIFIER.fullmatch(values[name]) is None:
            raise RuntimeError(f"{name} must be a safe PostgreSQL identifier")
    if len({values[name] for name in _REQUIRED[1:]}) != 3:
        raise RuntimeError("protected schema and role identifiers must be distinct")
    return values


settings = _required_environment()
alembic_config = context.config
if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)
alembic_config.set_main_option("sqlalchemy.url", settings["PROTECTED_DATABASE_URL"].replace("%", "%%"))
for key, value in settings.items():
    alembic_config.attributes[key] = value


def _quoted(identifier: str) -> str:
    return f'"{identifier}"'


async def _bootstrap(connection: AsyncConnection) -> None:
    owner = settings["PROTECTED_DB_OWNER_ROLE"]
    access = settings["PROTECTED_DB_ACCESS_ROLE"]
    schema = settings["PROTECTED_DB_SCHEMA"]
    for role in (owner, access):
        await connection.exec_driver_sql(
            "DO $bootstrap$ BEGIN "
            f"IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{role}') THEN "
            f"CREATE ROLE {_quoted(role)} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS; "
            "END IF; END $bootstrap$"
        )
        await connection.exec_driver_sql(
            f"ALTER ROLE {_quoted(role)} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
            "NOINHERIT NOREPLICATION NOBYPASSRLS"
        )
    await connection.exec_driver_sql(f"CREATE SCHEMA IF NOT EXISTS {_quoted(schema)} AUTHORIZATION {_quoted(owner)}")
    await connection.exec_driver_sql(f"ALTER SCHEMA {_quoted(schema)} OWNER TO {_quoted(owner)}")
    await connection.exec_driver_sql(f"REVOKE ALL ON SCHEMA {_quoted(schema)} FROM PUBLIC, {_quoted(access)}")
    await connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA {_quoted(schema)} TO {_quoted(access)}")
    await connection.commit()


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=None,
        version_table="alembic_version",
        version_table_schema=settings["PROTECTED_DB_SCHEMA"],
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = async_engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        echo=False,
    )
    try:
        async with engine.connect() as connection:
            await _bootstrap(connection)
            await connection.run_sync(_configure)
    finally:
        await engine.dispose()


def run_migrations_offline() -> None:
    raise RuntimeError("protected retrieval migrations require an online connection")


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(_run_online())
