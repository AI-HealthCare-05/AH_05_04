from collections.abc import AsyncIterator
from urllib.parse import quote_plus

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core import config
from app.core.db.databases import Base, get_db_session
from app.main import fastapi_app

TEST_DATABASE_URL = (
    "mysql+asyncmy://"
    f"{quote_plus(config.DB_USER)}:"
    f"{quote_plus(config.DB_PASSWORD)}"
    f"@127.0.0.1:{config.DB_EXPOSE_PORT}/test"
    "?charset=utf8mb4"
)

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
)


@pytest_asyncio.fixture(
    scope="session",
    autouse=True,
)
async def initialize_database() -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    yield

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)

    fastapi_app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def isolate_database() -> AsyncIterator[None]:
    async with test_engine.connect() as connection:
        transaction = await connection.begin()

        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )

        async def override_get_db_session() -> AsyncIterator[AsyncSession]:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

        fastapi_app.dependency_overrides[get_db_session] = override_get_db_session

        try:
            yield
        finally:
            fastapi_app.dependency_overrides.pop(get_db_session, None)
            await session.close()

            if transaction.is_active:
                await transaction.rollback()
