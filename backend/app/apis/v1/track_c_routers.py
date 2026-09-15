from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse

from app.core.errors import ErrorResponse
from app.core.utils.idempotency import build_idempotency_key_openapi_parameter, validate_idempotency_key_format
from app.dependencies.security import get_request_user
from app.dependencies.services import get_track_c_api_service
from app.dtos.track_c import (
    BarrierResponseEnvelope,
    CreateSafetyAssessmentRequest,
    PutBarrierResponseRequest,
    SafetyAssessmentResponse,
)
from app.models.users import User
from app.services.track_c_api import (
    BARRIER_RESPONSE_PUT_OPERATION_ID,
    SAFETY_ASSESSMENT_POST_OPERATION_ID,
    TrackCApiService,
)

track_c_router = APIRouter(tags=["track-c"])

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"model": ErrorResponse, "description": "Idempotency-Key 누락 또는 형식 오류"},
    401: {"model": ErrorResponse, "description": "인증 필요"},
    404: {"model": ErrorResponse, "description": "존재하지 않거나 SELF 소유가 아닌 Check-in"},
    409: {
        "model": ErrorResponse,
        "description": "CHECKIN_FLOW_STALE, SAFETY_ASSESSMENT_REVISION_CONFLICT, "
        "BARRIER_RESPONSE_REVISION_CONFLICT, SAFETY_FLOW_PRECEDES_BARRIER, IDEMPOTENCY_KEY_CONFLICT",
    },
    422: {"model": ErrorResponse, "description": "입력 검증 오류 또는 자유 텍스트 증상"},
    503: {"model": ErrorResponse, "description": "멱등성 응답 snapshot 크기 초과"},
}


@track_c_router.post(
    "/safety-assessments",
    response_model=SafetyAssessmentResponse,
    status_code=200,
    operation_id=SAFETY_ASSESSMENT_POST_OPERATION_ID,
    responses=_ERROR_RESPONSES,
    openapi_extra={
        "parameters": [
            build_idempotency_key_openapi_parameter(
                description="Safety assessment 저장 멱등성 키. 원문은 저장하지 않습니다."
            )
        ]
    },
)
async def create_safety_assessment(
    request: CreateSafetyAssessmentRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[TrackCApiService, Depends(get_track_c_api_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)] = None,
) -> JSONResponse:
    validate_idempotency_key_format(idempotency_key or "")
    assert idempotency_key is not None
    result = await service.create_safety(user_id=user.id, request=request, idempotency_key=idempotency_key)
    return JSONResponse(content=result.response_body, status_code=result.response_status)


@track_c_router.put(
    "/medication-checkins/{checkin_id}/barrier-response",
    response_model=BarrierResponseEnvelope,
    status_code=200,
    operation_id=BARRIER_RESPONSE_PUT_OPERATION_ID,
    responses=_ERROR_RESPONSES,
    openapi_extra={
        "parameters": [
            build_idempotency_key_openapi_parameter(
                description="Barrier 응답 생성·정정 멱등성 키. 원문은 저장하지 않습니다."
            )
        ]
    },
)
async def put_barrier_response(
    checkin_id: UUID,
    request: PutBarrierResponseRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[TrackCApiService, Depends(get_track_c_api_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)] = None,
) -> JSONResponse:
    validate_idempotency_key_format(idempotency_key or "")
    assert idempotency_key is not None
    result = await service.put_barrier(
        user_id=user.id,
        checkin_id=checkin_id,
        request=request,
        idempotency_key=idempotency_key,
    )
    return JSONResponse(content=result.response_body, status_code=result.response_status)
