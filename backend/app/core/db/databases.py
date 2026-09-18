from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core import config


class Base(DeclarativeBase):
    pass


def _create_engine(database_url: str):
    return create_async_engine(
        database_url,
        echo=config.SQLALCHEMY_ECHO,
        hide_parameters=True,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=config.DB_CONNECTION_POOL_MAXSIZE,
    )


engine = _create_engine(config.database_url)

account_withdrawal_cleanup_engine = None
if config.ACCOUNT_WITHDRAWAL_CLEANUP_DB_ROLE and config.ACCOUNT_WITHDRAWAL_CLEANUP_DB_PASSWORD:
    account_withdrawal_cleanup_engine = _create_engine(config.account_withdrawal_cleanup_database_url)


AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

AccountWithdrawalCleanupSessionFactory = (
    async_sessionmaker(
        bind=account_withdrawal_cleanup_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    if account_withdrawal_cleanup_engine is not None
    else None
)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_database() -> None:
    await engine.dispose()
    if account_withdrawal_cleanup_engine is not None:
        await account_withdrawal_cleanup_engine.dispose()
