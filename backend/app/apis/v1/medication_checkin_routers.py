from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse

from app.core.errors import ErrorResponse
from app.core.utils.idempotency import build_idempotency_key_openapi_parameter, validate_idempotency_key_format
from app.dependencies.security import get_request_user
from app.dependencies.services import get_medication_checkin_api_service
from app.dtos.medication_checkins import MedicationCheckinResponse, PutMedicationCheckinRequest
from app.models.users import User
from app.services.medication_checkin_api import MEDICATION_CHECKIN_PUT_OPERATION_ID, MedicationCheckinApiService

medication_checkin_router = APIRouter(tags=["medication-checkins"])


@medication_checkin_router.put(
    "/medication-occurrences/{occurrence_id}/check-in",
    response_model=MedicationCheckinResponse,
    status_code=200,
    operation_id=MEDICATION_CHECKIN_PUT_OPERATION_ID,
    responses={
        400: {"model": ErrorResponse, "description": "Idempotency-Key 누락 또는 형식 오류"},
        401: {"model": ErrorResponse, "description": "인증 필요"},
        404: {"model": ErrorResponse, "description": "존재하지 않거나 SELF 소유가 아닌 occurrence"},
        409: {"model": ErrorResponse, "description": "revision·Idempotency-Key 충돌 또는 취소된 occurrence"},
        422: {"model": ErrorResponse, "description": "입력 검증 오류 또는 사용자 UNCONFIRMED 제출"},
        503: {"model": ErrorResponse, "description": "멱등성 응답 snapshot 크기 초과"},
    },
    openapi_extra={
        "parameters": [
            build_idempotency_key_openapi_parameter(
                description="Check-in 생성·정정 멱등성 키. 원문은 저장하지 않습니다."
            )
        ]
    },
)
async def put_medication_checkin(
    occurrence_id: UUID,
    request: PutMedicationCheckinRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[MedicationCheckinApiService, Depends(get_medication_checkin_api_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)] = None,
) -> JSONResponse:
    validate_idempotency_key_format(idempotency_key or "")
    assert idempotency_key is not None
    result = await service.put(
        user_id=user.id,
        occurrence_id=occurrence_id,
        request=request,
        idempotency_key=idempotency_key,
    )
    return JSONResponse(content=result.response_body, status_code=result.response_status)
