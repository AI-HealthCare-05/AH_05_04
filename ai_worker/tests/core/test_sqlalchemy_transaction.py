"""SQLAlchemy Consumer transaction 어댑터 테스트입니다."""

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_transaction import (
    SqlAlchemyTransaction,
)
from ai_worker.core.consumer_execution import Transaction


@pytest.mark.asyncio
async def test_commit_delegates_to_async_session() -> None:
    session = MagicMock(spec=AsyncSession)
    transaction: Transaction = SqlAlchemyTransaction(
        cast(AsyncSession, session),
    )

    await transaction.commit()

    cast(AsyncMock, session.commit).assert_awaited_once_with()


@pytest.mark.asyncio
async def test_rollback_delegates_to_async_session() -> None:
    session = MagicMock(spec=AsyncSession)
    transaction: Transaction = SqlAlchemyTransaction(
        cast(AsyncSession, session),
    )

    await transaction.rollback()

    cast(AsyncMock, session.rollback).assert_awaited_once_with()
