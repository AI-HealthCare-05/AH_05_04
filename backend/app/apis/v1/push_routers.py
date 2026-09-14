from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.core.db.databases import get_db_session
from app.core.errors import ErrorResponse
from app.core.push import PushSettings, get_push_settings
from app.dependencies.security import get_request_user
from app.dtos.push import PushConfigData, PushConfigResponse, PushSubscriptionRequest, PushSubscriptionResponse
from app.models.users import User
from app.repositories.push_repository import PushRepository
from app.services.push import PushSubscriptionService, unavailable

push_router = APIRouter(
    prefix="/push", tags=["push"], responses={status: {"model": ErrorResponse} for status in (401, 404, 409, 422, 503)}
)


def push_settings() -> PushSettings:
    try:
        settings = get_push_settings()
    except Exception:
        raise unavailable() from None
    if config.ENV == "production":
        return settings.model_copy(update={"enabled": False})
    return settings


def get_push_service(
    session: Annotated[AsyncSession, Depends(get_db_session)], settings: Annotated[PushSettings, Depends(push_settings)]
) -> PushSubscriptionService:
    return PushSubscriptionService(PushRepository(session), settings)


@push_router.get("/config", operation_id="push.config", response_model=PushConfigResponse)
async def get_config(
    user: Annotated[User, Depends(get_request_user)], settings: Annotated[PushSettings, Depends(push_settings)]
) -> PushConfigResponse:
    if not settings.enabled:
        raise unavailable()
    return PushConfigResponse(data=PushConfigData(public_key=settings.vapid_public_key))


@push_router.put("/subscriptions", operation_id="push-subscription.upsert", response_model=PushSubscriptionResponse)
async def upsert_subscription(
    request: PushSubscriptionRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[PushSubscriptionService, Depends(get_push_service)],
) -> PushSubscriptionResponse:
    return await service.upsert(user.id, user.token_version, request)


@push_router.delete("/subscriptions/{subscription_id}", operation_id="push-subscription.delete", status_code=204)
async def delete_subscription(
    subscription_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    # Revocation remains available even when provider/secret configuration is broken.
    service = PushSubscriptionService(PushRepository(session), PushSettings.model_construct(enabled=False))
    await service.revoke(user.id, user.token_version, subscription_id)
    return Response(status_code=204)
