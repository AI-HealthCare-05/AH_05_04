from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core import config
from app.core.db.databases import Base, get_db_session
from app.main import fastapi_app

TEST_DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=config.DB_USER,
    password=config.DB_PASSWORD,
    host="127.0.0.1",
    port=config.DB_EXPOSE_PORT,
    database="test",
)

# pg_trgm은 테이블이 없는 전용 schema에 두고 조회 경로에만 더한다. public이나 테스트 schema에
# 두면 create_all의 존재 검사가 다른 schema의 동명 테이블을 보고 생성을 건너뛴다.
EXTENSION_SCHEMA = "test_extensions"

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={"server_settings": {"search_path": f"public,{EXTENSION_SCHEMA}"}},
)


async def _ensure_trigram_extension(connection, schema: str) -> None:
    """pg_trgm을 테이블이 없는 전용 schema에 둔다.

    public이나 테스트 schema에 두면 `create_all`의 존재 검사가 다른 schema의 동명 테이블을
    보고 생성을 건너뛴다. 확장은 DB당 하나뿐이라 이미 다른 schema에 있으면 옮긴다.
    """
    await connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    await connection.execute(text(f"CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA {schema}"))
    current = await connection.scalar(
        text(
            "SELECT n.nspname FROM pg_extension e "
            "JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'pg_trgm'"
        )
    )
    if current != schema:
        await connection.execute(text(f"ALTER EXTENSION pg_trgm SET SCHEMA {schema}"))


@pytest_asyncio.fixture(
    scope="session",
    autouse=True,
)
async def initialize_database() -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await _ensure_trigram_extension(connection, EXTENSION_SCHEMA)
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
        fastapi_app.state._test_db_session = session

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
            if hasattr(fastapi_app.state, "_test_db_session"):
                delattr(fastapi_app.state, "_test_db_session")
            await session.close()

            if transaction.is_active:
                await transaction.rollback()


@pytest_asyncio.fixture
async def db_session(isolate_database: None) -> AsyncSession:
    """API tests can seed data through the same session used by request dependency overrides."""

    _ = isolate_database
    return fastapi_app.state._test_db_session
