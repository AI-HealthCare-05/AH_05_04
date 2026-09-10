from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.password_reset import PasswordResetToken


class PasswordResetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_token(
        self,
        *,
        user_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> PasswordResetToken:
        token = PasswordResetToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        self.session.add(token)
        await self.session.flush()
        return token

    async def find_recent_token_for_user(
        self,
        *,
        user_id: UUID,
        since: datetime,
    ) -> PasswordResetToken | None:
        """요청 재발급 쿨다운 확인용입니다(잠금 없음)."""
        result = await self.session.execute(
            select(PasswordResetToken)
            .where(PasswordResetToken.user_id == user_id, PasswordResetToken.created_at >= since)
            .order_by(PasswordResetToken.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_by_hash(self, token_hash: str) -> PasswordResetToken | None:
        """PD-206 결정 3: 제출된 token의 candidate를 잠금 없이 먼저 조회해 대상
        `user_id`만 얻습니다. 실제 유효성 재확인은 user row 잠금 이후
        `lock_unused_unexpired_tokens_for_user()`가 담당합니다."""
        result = await self.session.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def lock_unused_unexpired_tokens_for_user(self, user_id: UUID) -> list[PasswordResetToken]:
        """호출 전 해당 사용자의 `User` row를 먼저 `FOR UPDATE`로 잠가야 합니다(lock 순서
        고정). 같은 사용자의 미사용·미만료 token 전체를 `id` 오름차순으로 잠가, 서로 다른
        유효 token이 동시에 제출돼도 단일 순서로 직렬화합니다."""
        result = await self.session.execute(
            select(PasswordResetToken)
            .where(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > datetime.now(config.TIMEZONE),
            )
            .order_by(PasswordResetToken.id)
            .with_for_update()
        )
        return list(result.scalars().all())

    async def mark_tokens_used(
        self,
        tokens: list[PasswordResetToken],
        *,
        used_at: datetime,
    ) -> None:
        for token in tokens:
            token.used_at = used_at
