from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse

from app.core.errors import ErrorResponse
from app.core.utils.idempotency import build_idempotency_key_openapi_parameter, validate_idempotency_key_format
from app.dependencies.security import get_request_user
from app.dependencies.services import get_lifestyle_times_service
from app.dtos.lifestyle_times import LifestyleTimesResponse, PutLifestyleTimesRequest
from app.models.users import User
from app.services.lifestyle_times import LifestyleTimesService

lifestyle_times_router = APIRouter(tags=["lifestyle-times"])
Service = Annotated[LifestyleTimesService, Depends(get_lifestyle_times_service)]
AuthenticatedUser = Annotated[User, Depends(get_request_user)]
Key = Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)]
ERRORS: dict[int | str, dict[str, Any]] = {code: {"model": ErrorResponse} for code in (400, 401, 404, 409, 422, 503)}
KEY_PARAMETER = {"parameters": [build_idempotency_key_openapi_parameter(description="생활 시간 전체 교체 멱등성 키")]}


@lifestyle_times_router.get(
    "/lifestyle-times",
    response_model=LifestyleTimesResponse,
    operation_id="lifestyle-times.get",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_lifestyle_times(user: AuthenticatedUser, service: Service) -> LifestyleTimesResponse:
    return await service.get(user_id=user.id)


@lifestyle_times_router.put(
    "/lifestyle-times",
    response_model=LifestyleTimesResponse,
    operation_id="lifestyle-times.put",
    responses=ERRORS,
    openapi_extra=KEY_PARAMETER,
)
async def put_lifestyle_times(
    request: PutLifestyleTimesRequest,
    user: AuthenticatedUser,
    service: Service,
    idempotency_key: Key = None,
) -> JSONResponse:
    validate_idempotency_key_format(idempotency_key or "")
    assert idempotency_key is not None
    result = await service.put(
        user_id=user.id,
        request=request,
        idempotency_key=idempotency_key,
    )
    return JSONResponse(content=result.response_body, status_code=result.response_status)
