from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse as Response

from app.dependencies.security import get_request_user
from app.dependencies.services import get_ocr_service
from app.dtos.ocr import OcrJobResponse
from app.models.users import User
from app.services.ocr import OcrService

ocr_router = APIRouter(prefix="/ocr-jobs", tags=["ocr"])


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
