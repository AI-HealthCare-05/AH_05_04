from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse as Response

from app.core.errors import ApiError, ErrorDetail
from app.core.utils.idempotency import IdempotencyKeyFormatError, validate_idempotency_key_format
from app.dependencies.security import get_request_user
from app.dependencies.services import get_medication_candidate_service
from app.dtos.medication_candidates import (
    ConfirmMedicationCandidateRequest,
    ConfirmMedicationCandidateResponse,
    CreateMedicationCandidateSearchRequest,
    MedicationCandidateSearchResponse,
    RejectMedicationCandidateRequest,
    RejectMedicationCandidateResponse,
)
from app.models.users import User
from app.services.medication_candidates import MedicationCandidateService

medication_candidate_router = APIRouter(tags=["medication-candidates"])


def _raise_candidate_feature_unavailable() -> NoReturn:
    raise ApiError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="SERVICE_UNAVAILABLE",
        message="약품 후보 확인 기능은 아직 사용할 수 없습니다.",
        details=[
            ErrorDetail(
                field="medication_candidate",
                reason="PRESCRIPTION_VERSION_MEDICATION_OWNERSHIP_NOT_CONNECTED",
            )
        ],
    )


def _validate_idempotency_header(idempotency_key: str | None) -> None:
    try:
        validate_idempotency_key_format(idempotency_key or "")
    except IdempotencyKeyFormatError as exc:
        raise ApiError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=str(exc),
            message="Idempotency-Key 헤더를 확인해 주세요.",
            details=[ErrorDetail(field="Idempotency-Key", reason=str(exc))],
        ) from exc


@medication_candidate_router.post(
    "/medication-candidate-searches",
    response_model=MedicationCandidateSearchResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_medication_candidate_search(
    request: CreateMedicationCandidateSearchRequest,
    user: Annotated[User, Depends(get_request_user)],
) -> Response:
    del request, user
    _raise_candidate_feature_unavailable()


@medication_candidate_router.get(
    "/medication-candidate-searches/{prescription_version_medication_id}",
    response_model=MedicationCandidateSearchResponse,
    status_code=status.HTTP_200_OK,
)
async def get_medication_candidate_search(
    prescription_version_medication_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[MedicationCandidateService, Depends(get_medication_candidate_service)],
) -> Response:
    result = await service.get_candidate_search(
        user=user,
        prescription_version_medication_id=prescription_version_medication_id,
    )
    return Response(
        content=MedicationCandidateSearchResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )


@medication_candidate_router.post(
    "/medication-candidates/confirm",
    response_model=ConfirmMedicationCandidateResponse,
    status_code=status.HTTP_200_OK,
)
async def confirm_medication_candidate(
    request: ConfirmMedicationCandidateRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[MedicationCandidateService, Depends(get_medication_candidate_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    _validate_idempotency_header(idempotency_key)
    result = await service.confirm_candidate(user=user, request=request)
    return Response(
        content=ConfirmMedicationCandidateResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )


@medication_candidate_router.post(
    "/medication-candidates/reject",
    response_model=RejectMedicationCandidateResponse,
    status_code=status.HTTP_200_OK,
)
async def reject_medication_candidate(
    request: RejectMedicationCandidateRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[MedicationCandidateService, Depends(get_medication_candidate_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    _validate_idempotency_header(idempotency_key)
    result = await service.reject_candidate(user=user, request=request)
    return Response(
        content=RejectMedicationCandidateResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )
