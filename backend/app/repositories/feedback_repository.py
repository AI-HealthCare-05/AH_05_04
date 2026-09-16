from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import ChatMessage, ChatSession
from app.models.feedback import ChatMessageFeedback, GuideFeedback
from app.models.guides import Guide
from app.models.profiles import Profile, ProfileType

FEEDBACK_RETENTION = timedelta(days=30)


class FeedbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_guide(self, user_id: UUID, target_id: UUID) -> str | None:
        return await self.session.scalar(
            select(Guide.generation_status)
            .join(Profile, Profile.id == Guide.profile_id)
            .where(Guide.id == target_id, Profile.user_id == user_id, Profile.profile_type == ProfileType.SELF)
            .with_for_update(of=Guide)
        )

    async def lock_message(self, user_id: UUID, session_id: UUID, target_id: UUID) -> tuple[str, str] | None:
        row = (
            await self.session.execute(
                select(ChatMessage.role, ChatMessage.generation_status)
                .join(ChatSession, ChatSession.id == ChatMessage.session_id)
                .join(Profile, Profile.id == ChatSession.profile_id)
                .where(
                    ChatMessage.id == target_id,
                    ChatSession.id == session_id,
                    Profile.user_id == user_id,
                    Profile.profile_type == ProfileType.SELF,
                )
                .with_for_update(of=ChatMessage)
            )
        ).one_or_none()
        return (row[0], row[1]) if row is not None else None

    async def get(self, target_id: UUID, *, chat: bool) -> GuideFeedback | ChatMessageFeedback | None:
        if chat:
            return await self.session.scalar(
                select(ChatMessageFeedback)
                .where(ChatMessageFeedback.chat_message_id == target_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        return await self.session.scalar(
            select(GuideFeedback)
            .where(GuideFeedback.guide_id == target_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def purge_expired(self, now: datetime) -> int:
        count = 0
        for model in (GuideFeedback, ChatMessageFeedback):
            result = await self.session.scalars(
                delete(model).where(model.created_at <= now - FEEDBACK_RETENTION).returning(model.id)
            )
            count += len(result.all())
        return count
