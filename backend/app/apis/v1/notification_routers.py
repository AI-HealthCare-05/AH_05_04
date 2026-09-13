from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse

from app.core.errors import ErrorResponse
from app.core.utils.idempotency import build_idempotency_key_openapi_parameter, validate_idempotency_key_format
from app.dependencies.security import get_request_user
from app.dependencies.services import get_notification_service
from app.dtos.notifications import (
    CreateReminderRequest,
    NotificationListResponse,
    NotificationResponse,
    ReadNotificationRequest,
    ReminderResponse,
)
from app.models.users import User
from app.services.notifications import NOTIFICATION_READ_OPERATION_ID, REMINDER_CREATE_OPERATION_ID, NotificationService

notification_router = APIRouter(tags=["notifications"])
ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse} for status in (400, 401, 404, 409, 422, 503)
}
IDEMPOTENCY = {"parameters": [build_idempotency_key_openapi_parameter(description="동기 알림 변경 멱등성 키")]}


@notification_router.get(
    "/notifications",
    response_model=NotificationListResponse,
    responses={status: {"model": ErrorResponse} for status in (401, 422)},
)
async def list_notifications(
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationListResponse:
    return await service.list_owned(user_id=user.id, limit=limit, offset=offset)


@notification_router.patch(
    "/notifications/{notification_id}/read",
    response_model=NotificationResponse,
    operation_id=NOTIFICATION_READ_OPERATION_ID,
    responses=ERRORS,
    openapi_extra=IDEMPOTENCY,
)
async def read_notification(
    notification_id: UUID,
    request: ReadNotificationRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)] = None,
) -> JSONResponse:
    validate_idempotency_key_format(idempotency_key or "")
    assert idempotency_key is not None
    result = await service.read(user_id=user.id, notification_id=notification_id, idempotency_key=idempotency_key)
    return JSONResponse(content=result.response_body, status_code=result.response_status)


@notification_router.post(
    "/medication-occurrences/{occurrence_id}/reminders",
    response_model=ReminderResponse,
    status_code=201,
    operation_id=REMINDER_CREATE_OPERATION_ID,
    responses=ERRORS,
    openapi_extra=IDEMPOTENCY,
)
async def create_reminder(
    occurrence_id: UUID,
    request: CreateReminderRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)] = None,
) -> JSONResponse:
    validate_idempotency_key_format(idempotency_key or "")
    assert idempotency_key is not None
    result = await service.create_reminder(
        user_id=user.id, occurrence_id=occurrence_id, request=request, idempotency_key=idempotency_key
    )
    return JSONResponse(content=result.response_body, status_code=result.response_status)
