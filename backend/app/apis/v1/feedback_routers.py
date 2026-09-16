from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.config import Env
from app.core.db.databases import get_db_session
from app.core.errors import ApiError
from app.dependencies.security import get_request_user
from app.dtos.feedback import FeedbackRequest, FeedbackResponse
from app.models.users import User
from app.repositories.feedback_repository import FeedbackRepository
from app.services.feedback import FeedbackService


def require_local_feedback() -> None:
    # PD-633: actual-user processing and Production publication remain unapproved.
    if config.ENV is not Env.LOCAL:
        raise ApiError(status_code=404, code="NOT_FOUND", message="대상을 찾을 수 없습니다.")


def get_feedback_service(session: Annotated[AsyncSession, Depends(get_db_session)]) -> FeedbackService:
    return FeedbackService(FeedbackRepository(session))


feedback_router = APIRouter(tags=["feedback"], dependencies=[Depends(require_local_feedback)])
CurrentUser = Annotated[User, Depends(get_request_user)]
Service = Annotated[FeedbackService, Depends(get_feedback_service)]


@feedback_router.post(
    "/guides/{guide_id}/feedback",
    response_model=FeedbackResponse,
    status_code=201,
    responses={200: {"model": FeedbackResponse}},
)
async def submit_guide_feedback(
    guide_id: UUID, request: FeedbackRequest, user: CurrentUser, service: Service
) -> Response:
    data, created = await service.submit(user_id=user.id, target_id=guide_id, request=request)
    return JSONResponse(FeedbackResponse(data=data).model_dump(mode="json"), status_code=201 if created else 200)


@feedback_router.post(
    "/chat-sessions/{session_id}/messages/{message_id}/feedback",
    response_model=FeedbackResponse,
    status_code=201,
    responses={200: {"model": FeedbackResponse}},
)
async def submit_chat_feedback(
    session_id: UUID, message_id: UUID, request: FeedbackRequest, user: CurrentUser, service: Service
) -> Response:
    data, created = await service.submit(user_id=user.id, target_id=message_id, session_id=session_id, request=request)
    return JSONResponse(FeedbackResponse(data=data).model_dump(mode="json"), status_code=201 if created else 200)


@feedback_router.delete("/guides/{guide_id}/feedback", status_code=204)
async def delete_guide_feedback(guide_id: UUID, user: CurrentUser, service: Service) -> Response:
    await service.remove(user_id=user.id, target_id=guide_id)
    return Response(status_code=204)


@feedback_router.delete("/chat-sessions/{session_id}/messages/{message_id}/feedback", status_code=204)
async def delete_chat_feedback(session_id: UUID, message_id: UUID, user: CurrentUser, service: Service) -> Response:
    await service.remove(user_id=user.id, target_id=message_id, session_id=session_id)
    return Response(status_code=204)
