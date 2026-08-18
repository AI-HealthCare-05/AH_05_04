from collections.abc import AsyncIterator
from urllib.parse import quote_plus

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core import config
from app.core.db.databases import Base, get_db_session
from app.main import app

# Base.metadata 등록을 위한 모델 import
from app.models.users import User  # noqa: F401

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

TestSessionFactory = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def override_get_db_session() -> AsyncIterator[AsyncSession]:
    async with TestSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@pytest_asyncio.fixture(
    scope="session",
    autouse=True,
)
async def initialize_database() -> AsyncIterator[None]:
    app.dependency_overrides[get_db_session] = override_get_db_session

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    yield

    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)

    app.dependency_overrides.clear()
    await test_engine.dispose()
