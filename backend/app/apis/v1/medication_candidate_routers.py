from typing import Annotated, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse as Response

from app.core import config
from app.core.errors import ApiError, ErrorDetail, ErrorResponse
from app.core.utils.idempotency import (
    IdempotencyKeyFormatError,
    build_idempotency_key_openapi_parameter,
    validate_idempotency_key_format,
)
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
from app.services.medication_candidates import (
    MEDICATION_CANDIDATE_CONFIRM_OPERATION_ID,
    MEDICATION_CANDIDATE_REJECT_OPERATION_ID,
    MedicationCandidateService,
)

medication_candidate_router = APIRouter(tags=["medication-candidates"])

_VALIDATION_ERROR_RESPONSE = {
    "model": ErrorResponse,
    "description": "ErrorResponse envelope로 반환되는 입력 검증 오류입니다.",
}
_SERVICE_UNAVAILABLE_RESPONSE = {
    "model": ErrorResponse,
    "description": "Candidate 기능이 아직 공개되지 않았거나 선행 연결이 완료되지 않은 상태입니다.",
}
_NOT_FOUND_RESPONSE = {
    "model": ErrorResponse,
    "description": "대상 약제 또는 Candidate Search/Result가 없거나 인증 사용자의 SELF Profile 소유가 아닙니다.",
}
_CONFLICT_RESPONSE = {
    "model": ErrorResponse,
    "description": "Candidate가 현재 상태와 맞지 않거나 같은 Idempotency-Key로 다른 요청 지문이 접수되었습니다.",
}


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


def _ensure_public_track_f_enabled() -> None:
    # medication-identification-v1.md "공개 게이트": RAG-11 UI·RAG-12 Preflight·E2E·외부 승인 전에는
    # 실제 사용자 트래픽에 공개하지 않습니다. 도메인 조회·조회 전용 side effect 이전에 먼저 차단합니다.
    if not config.PUBLIC_TRACK_F_ENABLED:
        raise ApiError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="SERVICE_UNAVAILABLE",
            message="약품 후보 확인 기능은 아직 공개되지 않았습니다.",
            details=[
                ErrorDetail(
                    field="medication_candidate",
                    reason="PUBLIC_TRACK_F_DISABLED",
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
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_ERROR_RESPONSE,
        status.HTTP_503_SERVICE_UNAVAILABLE: _SERVICE_UNAVAILABLE_RESPONSE,
    },
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
    operation_id="medication-candidate.search.get",
    responses={
        status.HTTP_404_NOT_FOUND: _NOT_FOUND_RESPONSE,
        status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_ERROR_RESPONSE,
        status.HTTP_503_SERVICE_UNAVAILABLE: _SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def get_medication_candidate_search(
    prescription_version_medication_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[MedicationCandidateService, Depends(get_medication_candidate_service)],
) -> Response:
    _ensure_public_track_f_enabled()
    result = await service.get_candidate_search(
        user=user,
        prescription_version_medication_id=prescription_version_medication_id,
    )
    return Response(
        content=MedicationCandidateSearchResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )


_IDEMPOTENCY_KEY_OPENAPI_PARAMETER = build_idempotency_key_openapi_parameter(
    description="Candidate 확인·거절 멱등성 키입니다. 원문 값은 저장하지 않습니다."
)


@medication_candidate_router.post(
    "/medication-candidates/confirm",
    response_model=ConfirmMedicationCandidateResponse,
    status_code=status.HTTP_200_OK,
    operation_id=MEDICATION_CANDIDATE_CONFIRM_OPERATION_ID,
    responses={
        status.HTTP_400_BAD_REQUEST: _VALIDATION_ERROR_RESPONSE,
        status.HTTP_404_NOT_FOUND: _NOT_FOUND_RESPONSE,
        status.HTTP_409_CONFLICT: _CONFLICT_RESPONSE,
        status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_ERROR_RESPONSE,
        status.HTTP_503_SERVICE_UNAVAILABLE: _SERVICE_UNAVAILABLE_RESPONSE,
    },
    openapi_extra={"parameters": [_IDEMPOTENCY_KEY_OPENAPI_PARAMETER]},
)
async def confirm_medication_candidate(
    request: ConfirmMedicationCandidateRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[MedicationCandidateService, Depends(get_medication_candidate_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)] = None,
) -> Response:
    _ensure_public_track_f_enabled()
    _validate_idempotency_header(idempotency_key)
    assert idempotency_key is not None
    result = await service.confirm_candidate(user=user, request=request, idempotency_key=idempotency_key)
    return Response(
        content=ConfirmMedicationCandidateResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )


@medication_candidate_router.post(
    "/medication-candidates/reject",
    response_model=RejectMedicationCandidateResponse,
    status_code=status.HTTP_200_OK,
    operation_id=MEDICATION_CANDIDATE_REJECT_OPERATION_ID,
    responses={
        status.HTTP_400_BAD_REQUEST: _VALIDATION_ERROR_RESPONSE,
        status.HTTP_404_NOT_FOUND: _NOT_FOUND_RESPONSE,
        status.HTTP_409_CONFLICT: _CONFLICT_RESPONSE,
        status.HTTP_422_UNPROCESSABLE_CONTENT: _VALIDATION_ERROR_RESPONSE,
        status.HTTP_503_SERVICE_UNAVAILABLE: _SERVICE_UNAVAILABLE_RESPONSE,
    },
    openapi_extra={"parameters": [_IDEMPOTENCY_KEY_OPENAPI_PARAMETER]},
)
async def reject_medication_candidate(
    request: RejectMedicationCandidateRequest,
    user: Annotated[User, Depends(get_request_user)],
    service: Annotated[MedicationCandidateService, Depends(get_medication_candidate_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)] = None,
) -> Response:
    _ensure_public_track_f_enabled()
    _validate_idempotency_header(idempotency_key)
    assert idempotency_key is not None
    result = await service.reject_candidate(user=user, request=request, idempotency_key=idempotency_key)
    return Response(
        content=RejectMedicationCandidateResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )
