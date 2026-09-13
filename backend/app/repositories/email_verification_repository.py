from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.email_verification import EmailVerificationPurpose, EmailVerificationToken


class EmailVerificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_token(
        self,
        *,
        email: str,
        purpose: EmailVerificationPurpose,
        token_hash: str,
        expires_at: datetime,
    ) -> EmailVerificationToken:
        token = EmailVerificationToken(email=email, purpose=purpose, token_hash=token_hash, expires_at=expires_at)
        self.session.add(token)
        await self.session.flush()
        return token

    async def find_recent_token(
        self,
        *,
        email: str,
        purpose: EmailVerificationPurpose,
        since: datetime,
    ) -> EmailVerificationToken | None:
        result = await self.session.execute(
            select(EmailVerificationToken)
            .where(
                EmailVerificationToken.email == email,
                EmailVerificationToken.purpose == purpose,
                EmailVerificationToken.created_at >= since,
            )
            .order_by(EmailVerificationToken.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_by_hash(self, token_hash: str) -> EmailVerificationToken | None:
        result = await self.session.execute(
            select(EmailVerificationToken).where(EmailVerificationToken.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def lock_unverified_unexpired_tokens(
        self,
        *,
        email: str,
        purpose: EmailVerificationPurpose,
    ) -> list[EmailVerificationToken]:
        result = await self.session.execute(
            select(EmailVerificationToken)
            .where(
                EmailVerificationToken.email == email,
                EmailVerificationToken.purpose == purpose,
                EmailVerificationToken.verified_at.is_(None),
                EmailVerificationToken.expires_at > datetime.now(config.TIMEZONE),
            )
            .order_by(EmailVerificationToken.id)
            .with_for_update()
        )
        return list(result.scalars().all())

    async def mark_tokens_verified(
        self,
        tokens: list[EmailVerificationToken],
        *,
        verified_at: datetime,
    ) -> None:
        for token in tokens:
            token.verified_at = verified_at

    async def latest_verified_token(
        self,
        *,
        email: str,
        purpose: EmailVerificationPurpose,
    ) -> EmailVerificationToken | None:
        result = await self.session.execute(
            select(EmailVerificationToken)
            .where(
                EmailVerificationToken.email == email,
                EmailVerificationToken.purpose == purpose,
                EmailVerificationToken.verified_at.is_not(None),
            )
            .order_by(EmailVerificationToken.verified_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
