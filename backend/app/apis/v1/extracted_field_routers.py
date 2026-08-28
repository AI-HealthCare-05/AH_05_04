from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse as Response

from app.dependencies.security import get_request_user
from app.dependencies.services import get_ocr_service
from app.dtos.ocr import ExtractedFieldResponse
from app.dtos.prescriptions import UpdateExtractedFieldRequest
from app.models.users import User
from app.services.ocr import OcrService

extracted_field_router = APIRouter(prefix="/extracted-fields", tags=["ocr"])


@extracted_field_router.patch(
    "/{field_id}",
    response_model=ExtractedFieldResponse,
    status_code=status.HTTP_200_OK,
)
async def update_extracted_field(
    field_id: UUID,
    request: UpdateExtractedFieldRequest,
    user: Annotated[User, Depends(get_request_user)],
    ocr_service: Annotated[OcrService, Depends(get_ocr_service)],
) -> Response:
    # OCR 추출 필드 확인/수정 Backend 계약: 사용자가 인식 결과를 검토·수정합니다.
    # Cache-Control: no-store는 NoStoreMiddleware가 /api/v1/* 전체에 일괄 적용합니다.
    result = await ocr_service.update_extracted_field(user=user, field_id=field_id, request=request)

    return Response(
        content=ExtractedFieldResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
    )
