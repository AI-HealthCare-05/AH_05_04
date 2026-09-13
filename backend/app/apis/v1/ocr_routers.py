from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse as Response

from app.core.utils.idempotency import build_idempotency_key_openapi_parameter
from app.dependencies.security import get_request_user
from app.dependencies.services import get_ocr_service, get_sync_mutation_idempotency_service
from app.dtos.ocr import CreateManualMedicationRequest, OcrJobResponse
from app.models.users import User
from app.services.idempotency import SyncMutationIdempotencyService
from app.services.ocr import OcrService

ocr_router = APIRouter(prefix="/ocr-jobs", tags=["ocr"])


_IDEMPOTENCY_KEY_OPENAPI_PARAMETER = build_idempotency_key_openapi_parameter(
    description="OCR 수동 약물 추가 멱등성 키입니다. 원문 값은 저장하지 않습니다."
)


@ocr_router.get(
    "/{job_id}",
    response_model=OcrJobResponse,
    status_code=status.HTTP_200_OK,
)
async def get_ocr_job_result(
    job_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    ocr_service: Annotated[OcrService, Depends(get_ocr_service)],
) -> Response:
    # OCR 결과 확인 Backend 계약(1차 구현 원사이클): 작업 상태와 추출 필드를 함께 반환합니다.
    # Cache-Control: no-store는 NoStoreMiddleware가 /api/v1/* 전체에 일괄 적용합니다.
    result = await ocr_service.get_ocr_job_result(user=user, job_id=job_id)

    return Response(
        content=OcrJobResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )


@ocr_router.post(
    "/{job_id}/manual-medications",
    response_model=OcrJobResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={"parameters": [_IDEMPOTENCY_KEY_OPENAPI_PARAMETER]},
)
async def create_manual_medication(
    job_id: UUID,
    request: CreateManualMedicationRequest,
    user: Annotated[User, Depends(get_request_user)],
    ocr_service: Annotated[OcrService, Depends(get_ocr_service)],
    idempotency_service: Annotated[SyncMutationIdempotencyService, Depends(get_sync_mutation_idempotency_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", include_in_schema=False)] = None,
) -> Response:
    # Manual values are user-confirmed data, not OCR raw/normalized output.
    result = await ocr_service.create_manual_medication(
        user=user,
        job_id=job_id,
        request=request,
        idempotency_key=idempotency_key or "",
        idempotency_service=idempotency_service,
    )

    return Response(
        content=OcrJobResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_201_CREATED,
    )
