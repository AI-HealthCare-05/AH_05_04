from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timedelta
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from app.core import config
from app.core.db.databases import Base, get_db_session
from app.core.utils.common import normalize_email
from app.main import fastapi_app
from app.models.email_verification import EmailVerificationPurpose
from app.repositories.email_verification_repository import EmailVerificationRepository
from app.tests.db_extensions import EXTENSION_SCHEMA, ensure_trigram_extension, ensure_vector_extension

TEST_DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=config.DB_USER,
    password=config.DB_PASSWORD,
    host="127.0.0.1",
    port=config.DB_EXPOSE_PORT,
    database="test",
)


test_engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={"server_settings": {"search_path": f"public,{EXTENSION_SCHEMA}"}},
)


@pytest_asyncio.fixture(
    scope="session",
    autouse=True,
)
async def initialize_database() -> AsyncIterator[None]:
    async with test_engine.begin() as connection:
        await ensure_trigram_extension(connection, EXTENSION_SCHEMA)
        await ensure_vector_extension(connection, EXTENSION_SCHEMA)
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


async def _mark_signup_email_verified(
    db_session: AsyncSession,
    *,
    email: str,
    expires_at: datetime | None = None,
) -> None:
    verified_at = datetime.now(config.TIMEZONE)
    token = await EmailVerificationRepository(db_session).create_token(
        email=normalize_email(email),
        purpose=EmailVerificationPurpose.SIGNUP,
        token_hash=f"{uuid4().hex}{uuid4().hex}",
        expires_at=expires_at or verified_at + timedelta(minutes=config.EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES),
    )
    token.verified_at = verified_at
    await db_session.flush()


@pytest_asyncio.fixture
async def mark_signup_email_verified(db_session: AsyncSession) -> Callable[[str], Awaitable[None]]:
    async def _mark(email: str) -> None:
        await _mark_signup_email_verified(db_session, email=email)

    return _mark


@pytest_asyncio.fixture
async def mark_expired_signup_email_verified(db_session: AsyncSession) -> Callable[[str], Awaitable[None]]:
    async def _mark(email: str) -> None:
        await _mark_signup_email_verified(
            db_session,
            email=email,
            expires_at=datetime.now(config.TIMEZONE) - timedelta(minutes=1),
        )

    return _mark
