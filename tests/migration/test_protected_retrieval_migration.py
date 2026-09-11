"""Isolated protected-retrieval Alembic boundary tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import URL, make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from infra.python.protected_retrieval_role_policy import (
    apply_protected_retrieval_role_policy,
    validate_protected_control_connection,
    validate_protected_data_connection,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG = PROJECT_ROOT / "infra" / "protected_retrieval" / "alembic.ini"


@dataclass(frozen=True)
class _ProtectedDatabase:
    url: str = field(repr=False)
    schema: str
    owner: str
    access: str
    control: str
    actor_login: str
    control_login: str
    denied_login: str
    password: str


def _quote(identifier: str) -> str:
    return f'"{identifier}"'


def test_protected_migration_refuses_missing_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "PROTECTED_DATABASE_URL",
        "PROTECTED_DB_SCHEMA",
        "PROTECTED_DB_OWNER_ROLE",
        "PROTECTED_DB_ACCESS_ROLE",
        "PROTECTED_DB_CONTROL_ROLE",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(RuntimeError, match="PROTECTED_DATABASE_URL is required"):
        command.current(Config(str(ALEMBIC_CONFIG)))


@pytest.fixture(scope="module")
def protected_database() -> Iterator[_ProtectedDatabase]:
    database_url = os.getenv("PROTECTED_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("PROTECTED_TEST_DATABASE_URL is unavailable")

    suffix = uuid4().hex[:10]
    database = _ProtectedDatabase(
        url=database_url,
        schema=f"pr368_{suffix}",
        owner=f"pr368_owner_{suffix}",
        access=f"pr368_access_{suffix}",
        control=f"pr368_control_{suffix}",
        actor_login=f"pr368_actor_{suffix}",
        control_login=f"pr368_controller_{suffix}",
        denied_login=f"pr368_denied_{suffix}",
        password="synthetic-only-password",
    )
    environment = {
        "PROTECTED_DATABASE_URL": database.url,
        "PROTECTED_DB_SCHEMA": database.schema,
        "PROTECTED_DB_OWNER_ROLE": database.owner,
        "PROTECTED_DB_ACCESS_ROLE": database.access,
        "PROTECTED_DB_CONTROL_ROLE": database.control,
    }
    previous = {name: os.environ.get(name) for name in environment}
    os.environ.update(environment)
    migration_config = Config(str(ALEMBIC_CONFIG))
    command.upgrade(migration_config, "head")
    command.downgrade(migration_config, "base")
    command.upgrade(migration_config, "head")

    async def provision_logins() -> None:
        engine = create_async_engine(database.url)
        try:
            async with engine.begin() as connection:
                for login in (database.actor_login, database.control_login, database.denied_login):
                    await connection.exec_driver_sql(
                        f"CREATE ROLE {_quote(login)} LOGIN PASSWORD '{database.password}' "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                    )
                await connection.exec_driver_sql(f"GRANT {_quote(database.access)} TO {_quote(database.actor_login)}")
                await connection.exec_driver_sql(
                    f"GRANT {_quote(database.control)} TO {_quote(database.control_login)}"
                )
                await apply_protected_retrieval_role_policy(
                    connection,
                    schema=database.schema,
                    owner=database.owner,
                    data_access=database.access,
                    control=database.control,
                )
                await connection.execute(
                    text(
                        f"""
                        INSERT INTO {_quote(database.schema)}.protected_identity (
                            database_login, actor_id, actor_namespace, principal_role
                        ) VALUES (:database_login, 'synthetic-author', 'SERVICE_IDENTITY', 'HOLDOUT_AUTHOR')
                        """
                    ),
                    {"database_login": database.actor_login},
                )
        finally:
            await engine.dispose()

    import asyncio

    asyncio.run(provision_logins())
    try:
        yield database
    finally:

        async def cleanup() -> None:
            engine = create_async_engine(database.url)
            try:
                async with engine.begin() as connection:
                    await connection.exec_driver_sql(f"DROP SCHEMA IF EXISTS {_quote(database.schema)} CASCADE")
                    await connection.exec_driver_sql(f"DROP ROLE IF EXISTS {_quote(database.actor_login)}")
                    await connection.exec_driver_sql(f"DROP ROLE IF EXISTS {_quote(database.control_login)}")
                    await connection.exec_driver_sql(f"DROP ROLE IF EXISTS {_quote(database.denied_login)}")
                    await connection.exec_driver_sql(f"DROP ROLE IF EXISTS {_quote(database.access)}")
                    await connection.exec_driver_sql(f"DROP ROLE IF EXISTS {_quote(database.control)}")
                    await connection.exec_driver_sql(f"DROP ROLE IF EXISTS {_quote(database.owner)}")
            finally:
                await engine.dispose()

        asyncio.run(cleanup())
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@pytest.mark.asyncio
async def test_protected_schema_uses_only_ordinary_relations_and_constraints(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    engine = create_async_engine(database.url)
    try:
        async with engine.connect() as connection:
            role_rows = await connection.execute(
                text(
                    """
                    SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication,
                           rolcanlogin, rolbypassrls
                    FROM pg_catalog.pg_roles
                    WHERE rolname = ANY(:roles)
                    """
                ),
                {"roles": [database.owner, database.access, database.control]},
            )
            assert {tuple(row) for row in role_rows} == {
                (database.owner, False, False, False, False, False, False),
                (database.access, False, False, False, False, False, False),
                (database.control, False, False, False, False, False, False),
            }

            functions = await connection.execute(
                text(
                    """
                    SELECT procedure.oid, procedure.proname, owner.rolname,
                           procedure.prosecdef, procedure.proconfig
                    FROM pg_catalog.pg_proc AS procedure
                    JOIN pg_catalog.pg_namespace AS namespace
                      ON namespace.oid = procedure.pronamespace
                    JOIN pg_catalog.pg_roles AS owner ON owner.oid = procedure.proowner
                    WHERE namespace.nspname = :schema
                    """
                ),
                {"schema": database.schema},
            )
            assert list(functions) == []

            relation_names = set(
                await connection.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = :schema"),
                    {"schema": database.schema},
                )
            )
            assert relation_names == {
                "alembic_version",
                "protected_identity",
                "protected_dataset",
                "protected_artifact",
                "approval_evidence",
                "authorization_grant",
                "operation_capability",
                "audit_entry",
                "audit_head",
            }
            lock_markers = set(
                await connection.scalars(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.columns
                        WHERE table_schema = :schema AND column_name = 'lock_marker'
                        """
                    ),
                    {"schema": database.schema},
                )
            )
            assert lock_markers == {"protected_dataset", "authorization_grant"}

            public_default_privileges = await connection.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM pg_catalog.pg_default_acl AS defaults
                    JOIN pg_catalog.pg_roles AS owner ON owner.oid = defaults.defaclrole
                    JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = defaults.defaclnamespace
                    CROSS JOIN LATERAL aclexplode(defaults.defaclacl) AS acl
                    WHERE owner.rolname = :owner
                      AND namespace.nspname = :schema
                      AND defaults.defaclobjtype IN ('r', 'S', 'f')
                      AND acl.grantee = 0
                      AND acl.privilege_type IN (
                          'SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'USAGE', 'EXECUTE'
                      )
                    """
                ),
                {"owner": database.owner, "schema": database.schema},
            )
            assert public_default_privileges == 0

            for relation in ("protected_dataset", "protected_artifact", "audit_entry"):
                privilege = await connection.scalar(
                    text("SELECT has_table_privilege(:role, :relation, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE')"),
                    {
                        "role": database.access,
                        "relation": f"{_quote(database.schema)}.{_quote(relation)}",
                    },
                )
                assert privilege is False
    finally:
        await engine.dispose()


def _login_url(database: _ProtectedDatabase, login: str) -> URL:
    return make_url(database.url).set(username=login, password=database.password)


@pytest.mark.asyncio
async def test_limited_logins_have_plane_specific_column_privileges(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    actor_engine = create_async_engine(_login_url(database, database.actor_login))
    control_engine = create_async_engine(_login_url(database, database.control_login))
    denied_engine = create_async_engine(_login_url(database, database.denied_login))
    try:
        async with actor_engine.connect() as connection:
            await validate_protected_data_connection(
                connection,
                schema=database.schema,
                data_access=database.access,
                control=database.control,
            )
            principal = await connection.execute(
                text(
                    f"SELECT actor_id, actor_namespace, principal_role "
                    f"FROM {_quote(database.schema)}.protected_identity WHERE database_login = session_user"
                )
            )
            assert principal.one() == ("synthetic-author", "SERVICE_IDENTITY", "HOLDOUT_AUTHOR")

        forbidden_statements = (
            f"SELECT * FROM {_quote(database.schema)}.approval_evidence",
            f"INSERT INTO {_quote(database.schema)}.audit_head DEFAULT VALUES",
            f"UPDATE {_quote(database.schema)}.protected_dataset SET state = 'FROZEN'",
            f"DELETE FROM {_quote(database.schema)}.audit_head",
            f"TRUNCATE {_quote(database.schema)}.audit_entry",
            f"CREATE TABLE {_quote(database.schema)}.forbidden (id integer)",
        )
        for statement in forbidden_statements:
            async with actor_engine.connect() as connection:
                with pytest.raises(DBAPIError):
                    await connection.execute(text(statement))

        async with control_engine.connect() as connection:
            await validate_protected_control_connection(
                connection,
                schema=database.schema,
                data_access=database.access,
                control=database.control,
            )
            await connection.execute(
                text(
                    f"INSERT INTO {_quote(database.schema)}.approval_evidence "
                    "(source_event_id, evidence, canonical_raw_sha256, recorded_at) "
                    "VALUES ('synthetic-control-event', '{}'::jsonb, :digest, clock_timestamp())"
                ),
                {"digest": "a" * 64},
            )
            await connection.rollback()
        async with control_engine.connect() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(text(f"SELECT envelope FROM {_quote(database.schema)}.protected_artifact"))

        async with denied_engine.connect() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(text(f"SELECT * FROM {_quote(database.schema)}.protected_identity"))
    finally:
        await actor_engine.dispose()
        await control_engine.dispose()
        await denied_engine.dispose()


def test_protected_downgrade_refuses_durable_rows_without_data_loss(
    protected_database: _ProtectedDatabase,
) -> None:
    database = protected_database
    with pytest.raises(RuntimeError, match="downgrade refused while durable rows exist"):
        command.downgrade(Config(str(ALEMBIC_CONFIG)), "base")

    async def verify_preserved() -> None:
        engine = create_async_engine(database.url)
        try:
            async with engine.connect() as connection:
                identity_count = await connection.scalar(
                    text(f"SELECT count(*) FROM {_quote(database.schema)}.protected_identity")
                )
                schema_exists = await connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :schema)"),
                    {"schema": database.schema},
                )
            assert identity_count == 1
            assert schema_exists is True
        finally:
            await engine.dispose()

    import asyncio

    asyncio.run(verify_preserved())
