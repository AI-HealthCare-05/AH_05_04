from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse as Response

from app.core.errors import ApiError, ErrorDetail
from app.core.utils.idempotency import IdempotencyKeyFormatError, validate_idempotency_key_format
from app.dependencies.security import get_request_user
from app.dtos.medication_candidates import (
    ConfirmMedicationCandidateRequest,
    ConfirmMedicationCandidateResponse,
    CreateMedicationCandidateSearchRequest,
    MedicationCandidateSearchResponse,
    RejectMedicationCandidateRequest,
    RejectMedicationCandidateResponse,
)
from app.models.users import User

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


@medication_candidate_router.post(
    "/medication-candidates/confirm",
    response_model=ConfirmMedicationCandidateResponse,
    status_code=status.HTTP_200_OK,
)
async def confirm_medication_candidate(
    request: ConfirmMedicationCandidateRequest,
    user: Annotated[User, Depends(get_request_user)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    del request, user
    _validate_idempotency_header(idempotency_key)
    _raise_candidate_feature_unavailable()


@medication_candidate_router.post(
    "/medication-candidates/reject",
    response_model=RejectMedicationCandidateResponse,
    status_code=status.HTTP_200_OK,
)
async def reject_medication_candidate(
    request: RejectMedicationCandidateRequest,
    user: Annotated[User, Depends(get_request_user)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    del request, user
    _validate_idempotency_header(idempotency_key)
    _raise_candidate_feature_unavailable()
