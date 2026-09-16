from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse

from app.core.errors import ErrorResponse
from app.core.utils.idempotency import build_idempotency_key_openapi_parameter, validate_idempotency_key_format
from app.dependencies.security import get_request_user
from app.dependencies.services import get_track_c_api_service, get_track_c_support_service
from app.dtos.track_c import (
    BarrierResponseEnvelope,
    CreateSafetyAssessmentRequest,
    PutBarrierResponseRequest,
    SafetyAssessmentResponse,
)
from app.dtos.track_c_support import (
    ActionPlanFollowupEnvelope,
    ActionPlanFollowupReadEnvelope,
    CreateSupportActionPlanRequest,
    PatchSupportActionPlanRequest,
    SubmitActionPlanFollowupRequest,
    SupportActionPlanResponse,
    SupportOfferResponse,
)
from app.models.users import User
from app.services.track_c_api import (
    BARRIER_RESPONSE_PUT_OPERATION_ID,
    SAFETY_ASSESSMENT_POST_OPERATION_ID,
    TrackCApiService,
)
from app.services.track_c_support import (
    ACTION_PLAN_FOLLOWUP_GET_OPERATION_ID,
    ACTION_PLAN_FOLLOWUP_POST_OPERATION_ID,
    SUPPORT_ACTION_PLAN_GET_OPERATION_ID,
    SUPPORT_ACTION_PLAN_PATCH_OPERATION_ID,
    SUPPORT_ACTION_PLAN_POST_OPERATION_ID,
    SUPPORT_OFFER_GET_OPERATION_ID,
    TrackCSupportService,
)

track_c_router = APIRouter(tags=["track-c"])

_SUPPORT_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "인증 필요"},
    404: {"model": ErrorResponse, "description": "BARRIER_RESPONSE_NOT_FOUND — 미존재·타인 동일 응답"},
    409: {
        "model": ErrorResponse,
        "description": "CHECKIN_FLOW_STALE, SAFETY_FLOW_PRECEDES_SUPPORT, BARRIER_FLOW_STALE",
    },
    422: {"model": ErrorResponse, "description": "VALIDATION_FAILED"},
    503: {"model": ErrorResponse, "description": "SUPPORT_CONFIG_UNAVAILABLE"},
}


@track_c_router.get(
    "/barrier-responses/{id}/supports",
    response_model=SupportOfferResponse,
    operation_id=SUPPORT_OFFER_GET_OPERATION_ID,
    responses=_SUPPORT_ERRORS,
)
async def get_support_offers(
    id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[TrackCSupportService, Depends(get_track_c_support_service)],
) -> SupportOfferResponse:
    return await service.get_supports(user_id=user.id, barrier_id=id)


@track_c_router.post(
    "/support-action-plans",
    response_model=SupportActionPlanResponse,
    status_code=200,
    operation_id=SUPPORT_ACTION_PLAN_POST_OPERATION_ID,
    responses={
        **_SUPPORT_ERRORS,
        400: {"model": ErrorResponse, "description": "Idempotency-Key 누락 또는 형식 오류"},
        409: {
            "model": ErrorResponse,
            "description": "CHECKIN_FLOW_STALE, SAFETY_FLOW_PRECEDES_SUPPORT, BARRIER_FLOW_STALE, "
            "SUPPORT_VERSION_CONFLICT, SUPPORT_NOT_OFFERED, ACTION_PLAN_ALREADY_ACTIVE, IDEMPOTENCY_KEY_CONFLICT",
        },
        503: {
            "model": ErrorResponse,
            "description": "SUPPORT_CONFIG_UNAVAILABLE, IDEMPOTENCY_RESPONSE_TOO_LARGE",
        },
    },
    openapi_extra={
        "parameters": [
            build_idempotency_key_openapi_parameter(description="사용자가 확정한 지원 계획 생성의 멱등성 키")
        ]
    },
)
async def create_support_action_plan(
    request: CreateSupportActionPlanRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[TrackCSupportService, Depends(get_track_c_support_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)] = None,
) -> JSONResponse:
    validate_idempotency_key_format(idempotency_key or "")
    assert idempotency_key is not None
    result = await service.create_plan(user_id=user.id, request=request, idempotency_key=idempotency_key)
    return JSONResponse(content=result.response_body, status_code=result.response_status)


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


@track_c_router.get(
    "/support-action-plans/{id}",
    response_model=SupportActionPlanResponse,
    operation_id=SUPPORT_ACTION_PLAN_GET_OPERATION_ID,
    responses={
        401: {"model": ErrorResponse, "description": "인증 필요"},
        404: {"model": ErrorResponse, "description": "ACTION_PLAN_NOT_FOUND — 미존재·타인 동일 응답"},
        422: {"model": ErrorResponse, "description": "VALIDATION_FAILED"},
    },
)
async def get_support_action_plan(
    id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[TrackCSupportService, Depends(get_track_c_support_service)],
) -> SupportActionPlanResponse:
    return await service.get_plan(user_id=user.id, plan_id=id)


@track_c_router.patch(
    "/support-action-plans/{id}",
    response_model=SupportActionPlanResponse,
    operation_id=SUPPORT_ACTION_PLAN_PATCH_OPERATION_ID,
    responses={
        400: {"model": ErrorResponse, "description": "Idempotency-Key 누락 또는 형식 오류"},
        401: {"model": ErrorResponse, "description": "인증 필요"},
        404: {"model": ErrorResponse, "description": "ACTION_PLAN_NOT_FOUND — 미존재·타인 동일 응답"},
        409: {
            "model": ErrorResponse,
            "description": "ACTION_PLAN_STATE_CONFLICT, CHECKIN_FLOW_STALE, SAFETY_FLOW_PRECEDES_SUPPORT, "
            "BARRIER_FLOW_STALE, IDEMPOTENCY_KEY_CONFLICT",
        },
        422: {"model": ErrorResponse, "description": "VALIDATION_FAILED"},
        503: {"model": ErrorResponse, "description": "IDEMPOTENCY_RESPONSE_TOO_LARGE"},
    },
    openapi_extra={
        "parameters": [
            build_idempotency_key_openapi_parameter(description="사용자가 확인한 지원 계획 완료·취소 멱등성 키")
        ]
    },
)
async def patch_support_action_plan(
    id: UUID,
    request: PatchSupportActionPlanRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[TrackCSupportService, Depends(get_track_c_support_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)] = None,
) -> JSONResponse:
    validate_idempotency_key_format(idempotency_key or "")
    assert idempotency_key is not None
    result = await service.patch_plan(user_id=user.id, plan_id=id, request=request, idempotency_key=idempotency_key)
    return JSONResponse(content=result.response_body, status_code=result.response_status)


@track_c_router.get(
    "/support-action-plans/{id}/followups",
    response_model=ActionPlanFollowupReadEnvelope,
    operation_id=ACTION_PLAN_FOLLOWUP_GET_OPERATION_ID,
    responses={
        401: {"model": ErrorResponse, "description": "인증 필요"},
        404: {"model": ErrorResponse, "description": "ACTION_PLAN_NOT_FOUND — 미존재·타인 동일 응답"},
        422: {"model": ErrorResponse, "description": "VALIDATION_FAILED"},
    },
)
async def get_action_plan_followup(
    id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[TrackCSupportService, Depends(get_track_c_support_service)],
) -> ActionPlanFollowupReadEnvelope:
    return await service.get_followup(user_id=user.id, plan_id=id)


@track_c_router.post(
    "/support-action-plans/{id}/followups",
    response_model=ActionPlanFollowupEnvelope,
    status_code=200,
    operation_id=ACTION_PLAN_FOLLOWUP_POST_OPERATION_ID,
    responses={
        400: {"model": ErrorResponse, "description": "Idempotency-Key 누락 또는 형식 오류"},
        401: {"model": ErrorResponse, "description": "인증 필요"},
        404: {"model": ErrorResponse, "description": "ACTION_PLAN_NOT_FOUND — 미존재·타인 동일 응답"},
        409: {
            "model": ErrorResponse,
            "description": "ACTION_PLAN_STATE_CONFLICT, ACTION_PLAN_FOLLOWUP_REVISION_CONFLICT, "
            "IDEMPOTENCY_KEY_CONFLICT",
        },
        422: {"model": ErrorResponse, "description": "VALIDATION_FAILED"},
        503: {"model": ErrorResponse, "description": "IDEMPOTENCY_RESPONSE_TOO_LARGE"},
    },
    openapi_extra={
        "parameters": [
            build_idempotency_key_openapi_parameter(description="완료한 지원 계획 평가 제출·정정의 멱등성 키")
        ]
    },
)
async def submit_action_plan_followup(
    id: UUID,
    request: SubmitActionPlanFollowupRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[TrackCSupportService, Depends(get_track_c_support_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)] = None,
) -> JSONResponse:
    validate_idempotency_key_format(idempotency_key or "")
    assert idempotency_key is not None
    result = await service.submit_followup(
        user_id=user.id, plan_id=id, request=request, idempotency_key=idempotency_key
    )
    return JSONResponse(content=result.response_body, status_code=result.response_status)
