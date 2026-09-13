"""SELF-owned UNCONFIRMED backlog for Check-in correction."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db.databases import get_db_session
from app.core.errors import ErrorResponse
from app.dependencies.security import get_request_user
from app.dtos.medication_checkin_backlog import UnconfirmedCheckinResponse
from app.models.users import User
from app.repositories.medication_checkin_repository import MedicationCheckinRepository
from app.services.medication_checkin_backlog import MedicationCheckinBacklogService

medication_checkin_backlog_router = APIRouter(prefix="/medication-checkins", tags=["medication-checkins"])


def get_backlog_service(session: Annotated[AsyncSession, Depends(get_db_session)]) -> MedicationCheckinBacklogService:
    return MedicationCheckinBacklogService(MedicationCheckinRepository(session))


@medication_checkin_backlog_router.get(
    "/unconfirmed",
    response_model=UnconfirmedCheckinResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def list_unconfirmed_checkins(
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[MedicationCheckinBacklogService, Depends(get_backlog_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: UUID | None = None,
) -> UnconfirmedCheckinResponse:
    return UnconfirmedCheckinResponse(data=await service.list_owned(user_id=user.id, limit=limit, cursor=cursor))
