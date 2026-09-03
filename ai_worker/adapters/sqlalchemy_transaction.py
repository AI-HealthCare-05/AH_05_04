"""SQLAlchemy AsyncSession 기반 Consumer transaction 어댑터입니다."""

from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyTransaction:
    """Consumer가 소유한 AsyncSession의 transaction을 제어합니다."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        """현재 session의 변경사항을 commit합니다."""

        await self._session.commit()

    async def rollback(self) -> None:
        """현재 session의 미완료 변경사항을 rollback합니다."""

        await self._session.rollback()
