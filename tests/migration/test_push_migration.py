"""Real PostgreSQL migration and rollback guard in an isolated database."""

import asyncio
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from tests.migration.test_medication_checkin_migration import _alembic_config, _connection


@pytest.fixture
def push_database(monkeypatch):
    database = "push469_" + uuid4().hex[:12]
    cluster_url = config.database_url

    async def database_action(create):
        engine = create_async_engine(cluster_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.execute(
                    text(f'CREATE DATABASE "{database}"' if create else f'DROP DATABASE "{database}" WITH (FORCE)')
                )
        finally:
            await engine.dispose()

    asyncio.run(database_action(True))
    monkeypatch.setattr(config, "DB_NAME", database)
    try:
        yield
    finally:
        asyncio.run(database_action(False))


def test_upgrade_constraints_and_downgrade_preserves_history(push_database):
    alembic = _alembic_config()
    command.upgrade(alembic, "469a1b2c3d4e")

    async def check_and_seed():
        async with _connection() as connection:
            constraints = set(
                await connection.scalars(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conrelid IN ('push_subscription'::regclass, 'push_delivery'::regclass)"
                    )
                )
            )
            assert {
                "uq_push_subscription_endpoint",
                "uq_push_delivery_generation",
                "chk_push_delivery_claim",
                "chk_push_subscription_revocation",
            } <= constraints
            await connection.rollback()
            async with connection.begin():
                user_id, profile_id = str(uuid4()), str(uuid4())
                await connection.execute(
                    text(
                        "INSERT INTO \"user\" (id,email,hashed_password,name,is_active,is_admin,account_status,token_version) VALUES (:id,'push@synthetic.test','synthetic','synthetic',true,false,'ACTIVE',0)"
                    ),
                    {"id": user_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO profile (id,user_id,profile_type,display_name) VALUES (:id,:user_id,'SELF','synthetic')"
                    ),
                    {"id": profile_id, "user_id": user_id},
                )
                await connection.execute(
                    text(
                        "INSERT INTO push_subscription (id,profile_id,token_version,generation,endpoint_hmac,ciphertext,key_id,activated_at,revoked_at) VALUES (:id,:profile_id,0,:generation,:digest,NULL,'synthetic',now(),now())"
                    ),
                    {"id": str(uuid4()), "profile_id": profile_id, "generation": str(uuid4()), "digest": "0" * 64},
                )

    asyncio.run(check_and_seed())
    with pytest.raises(RuntimeError, match="history blocks downgrade"):
        command.downgrade(alembic, "469a1b2c3d4e-1")

    async def clear_subscriptions():
        async with _connection() as connection:
            async with connection.begin():
                await connection.execute(text("DELETE FROM push_subscription"))

    asyncio.run(clear_subscriptions())
    command.downgrade(alembic, "469a1b2c3d4e-1")
    command.upgrade(alembic, "469a1b2c3d4e")
