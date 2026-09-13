from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse

from app.core.errors import ErrorResponse
from app.core.utils.idempotency import build_idempotency_key_openapi_parameter, validate_idempotency_key_format
from app.dependencies.security import get_request_user
from app.dependencies.services import get_medication_schedule_api_service
from app.dtos.medication_schedules import (
    CancelMedicationScheduleRequest,
    MedicationDayResponse,
    MedicationOccurrenceMedicationResponse,
    MedicationScheduleResponse,
    PutMedicationScheduleRequest,
)
from app.models.users import User
from app.services.medication_schedule_api import MedicationScheduleApiService

medication_schedule_router = APIRouter(tags=["medication-schedules"])
Service = Annotated[MedicationScheduleApiService, Depends(get_medication_schedule_api_service)]
AuthenticatedUser = Annotated[User, Depends(get_request_user)]
Key = Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)]
ERRORS: dict[int | str, dict[str, Any]] = {code: {"model": ErrorResponse} for code in (400, 401, 404, 409, 422, 503)}
KEY_PARAMETER = {"parameters": [build_idempotency_key_openapi_parameter(description="일정 변경 멱등성 키")]}


@medication_schedule_router.get(
    "/medication-occurrences",
    response_model=MedicationDayResponse,
    operation_id="medication-occurrences.list",
    responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
async def get_medication_day(
    user: AuthenticatedUser, service: Service, day: Annotated[date, Query(alias="date")]
) -> MedicationDayResponse:
    return await service.day(user_id=user.id, day=day)


@medication_schedule_router.get(
    "/medication-occurrences/{occurrence_id}/medication",
    response_model=MedicationOccurrenceMedicationResponse,
    operation_id="medication-occurrences.medication.get",
    responses={
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "존재하지 않거나 SELF 소유가 아닌 occurrence"},
        422: {"model": ErrorResponse},
    },
)
async def get_occurrence_medication(
    occurrence_id: UUID, user: AuthenticatedUser, service: Service
) -> MedicationOccurrenceMedicationResponse:
    return await service.occurrence_medication(user_id=user.id, occurrence_id=occurrence_id)


@medication_schedule_router.put(
    "/prescription-version-medications/{prescription_version_medication_id}/schedule",
    response_model=MedicationScheduleResponse,
    operation_id="medication-schedule.put",
    responses=ERRORS,
    openapi_extra=KEY_PARAMETER,
)
async def put_medication_schedule(
    prescription_version_medication_id: UUID,
    request: PutMedicationScheduleRequest,
    user: AuthenticatedUser,
    service: Service,
    idempotency_key: Key = None,
) -> JSONResponse:
    validate_idempotency_key_format(idempotency_key or "")
    assert idempotency_key is not None
    result = await service.write(
        user_id=user.id,
        medication_id=prescription_version_medication_id,
        request=request,
        idempotency_key=idempotency_key,
    )
    return JSONResponse(content=result.response_body, status_code=result.response_status)


@medication_schedule_router.patch(
    "/prescription-version-medications/{prescription_version_medication_id}/schedule",
    response_model=MedicationScheduleResponse,
    operation_id="medication-schedule.patch",
    responses=ERRORS,
    openapi_extra=KEY_PARAMETER,
)
async def cancel_medication_schedule(
    prescription_version_medication_id: UUID,
    request: CancelMedicationScheduleRequest,
    user: AuthenticatedUser,
    service: Service,
    idempotency_key: Key = None,
) -> JSONResponse:
    validate_idempotency_key_format(idempotency_key or "")
    assert idempotency_key is not None
    result = await service.write(
        user_id=user.id,
        medication_id=prescription_version_medication_id,
        request=request,
        idempotency_key=idempotency_key,
    )
    return JSONResponse(content=result.response_body, status_code=result.response_status)
