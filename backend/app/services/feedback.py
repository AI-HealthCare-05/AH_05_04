from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import ApiError
from app.dtos.feedback import FeedbackData, FeedbackRequest
from app.models.feedback import ChatMessageFeedback, GuideFeedback
from app.repositories.feedback_repository import FEEDBACK_RETENTION, FeedbackRepository


class FeedbackService:
    def __init__(self, repository: FeedbackRepository) -> None:
        self.repository = repository

    async def _lock_target(self, user_id: UUID, target_id: UUID, session_id: UUID | None) -> bool:
        if session_id is None:
            status = await self.repository.lock_guide(user_id, target_id)
            found, ready = status is not None, status == "COMPLETED"
        else:
            result = await self.repository.lock_message(user_id, session_id, target_id)
            found, ready = result is not None, result == ("ASSISTANT", "COMPLETED")
        if not found:
            raise ApiError(status_code=404, code="NOT_FOUND", message="대상을 찾을 수 없습니다.")
        return ready

    async def submit(
        self, *, user_id: UUID, target_id: UUID, request: FeedbackRequest, session_id: UUID | None = None
    ) -> tuple[FeedbackData, bool]:
        session = self.repository.session
        async with session.begin_nested():
            if not await self._lock_target(user_id, target_id, session_id):
                raise ApiError(
                    status_code=409,
                    code="FEEDBACK_TARGET_NOT_READY",
                    message="완료된 AI 응답에만 의견을 남길 수 있습니다.",
                )
            now = datetime.now(UTC)
            row = await self.repository.get(target_id, chat=session_id is not None)
            if row is not None and row.created_at <= now - FEEDBACK_RETENTION:
                await session.delete(row)
                await session.flush()
                row = None
            created = row is None
            if row is None:
                row = (
                    GuideFeedback(guide_id=target_id)
                    if session_id is None
                    else ChatMessageFeedback(chat_message_id=target_id)
                )
                row.rating, row.comment = request.rating, request.comment
                row.created_at = row.updated_at = now
                session.add(row)
            elif (row.rating, row.comment) != (request.rating, request.comment):
                row.rating, row.comment, row.updated_at = request.rating, request.comment, now
            await session.flush()
            data = FeedbackData.model_validate(row)
        await session.commit()
        return data, created

    async def remove(self, *, user_id: UUID, target_id: UUID, session_id: UUID | None = None) -> None:
        session = self.repository.session
        async with session.begin_nested():
            await self._lock_target(user_id, target_id, session_id)
            row = await self.repository.get(target_id, chat=session_id is not None)
            if row is not None:
                await session.delete(row)
                await session.flush()
        await session.commit()
