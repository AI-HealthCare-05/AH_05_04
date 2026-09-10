from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_session import RefreshSession


class RefreshSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(
        self,
        *,
        session_id: UUID,
        user_id: UUID,
        jti: str,
    ) -> None:
        """로그인마다 독립된 row를 만든다 — 다른 기기의 로그인과 재사용 탐지가
        섞이지 않도록, `User` 컬럼이 아니라 이 세션 전용 row로 jti를 추적한다."""
        self.session.add(RefreshSession(id=session_id, user_id=user_id, active_jti=jti))
        await self.session.flush()

    async def rotate_jti(
        self,
        *,
        session_id: UUID,
        expected_jti: str,
        new_jti: str,
    ) -> bool:
        """`expected_jti`가 이 세션의 현재 `active_jti`와 일치할 때만 원자적으로
        교체한다. 일치하지 않으면(이미 rotation된 refresh token 재사용, 또는 동시
        rotation 경쟁) 영향받은 row가 0건이라 `False`를 반환하며, 호출자는 이를
        재사용으로 간주해야 한다. `session_id`로만 scope하므로 같은 사용자의 다른
        세션(다른 기기 로그인)에는 영향을 주지 않는다."""
        updated_id = await self.session.scalar(
            update(RefreshSession)
            .where(RefreshSession.id == session_id, RefreshSession.active_jti == expected_jti)
            .values(active_jti=new_jti)
            .returning(RefreshSession.id)
        )
        return updated_id is not None
