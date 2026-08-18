from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core import config
from app.core.config import Env


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    config.database_url,
    echo=config.ENV == Env.LOCAL,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_size=config.DB_CONNECTION_POOL_MAXSIZE,
)


AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
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
