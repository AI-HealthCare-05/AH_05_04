from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile, status
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse as Response

from app.dependencies.security import get_request_user
from app.dependencies.services import (
    get_job_status_service,
    get_medical_document_service,
    get_ocr_service,
    get_prescription_service,
)
from app.dtos.jobs import JobStatusResponse
from app.dtos.medical_documents import (
    MedicalDocumentType,
    PrescriptionDocumentUploadData,
    PrescriptionDocumentUploadResponse,
    UploadStatus,
)
from app.dtos.ocr import ExecuteOcrRequest, OcrJobResponse
from app.dtos.prescriptions import PrescriptionResponse
from app.models.users import User
from app.services.job_status import JobStatusService
from app.services.medical_documents import MedicalDocumentService
from app.services.ocr import OcrService
from app.services.prescriptions import PrescriptionService

medical_document_router = APIRouter(prefix="/documents", tags=["medical-documents"])


@medical_document_router.post(
    "",
    response_model=PrescriptionDocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_prescription_document(
    user: Annotated[User, Depends(get_request_user)],
    medical_document_service: Annotated[MedicalDocumentService, Depends(get_medical_document_service)],
    file: Annotated[UploadFile, File()],
    document_type: Annotated[MedicalDocumentType, Form()] = MedicalDocumentType.PRESCRIPTION,
) -> Response:
    # 의료문서 업로드 Backend 계약: JPG/JPEG/PNG/PDF 처방전 한 장 업로드. OCR 실행은 별도 API에서 처리합니다.
    # Cache-Control: no-store는 NoStoreMiddleware가 /api/v1/* 전체에 일괄 적용합니다.
    result = await medical_document_service.create_prescription_document(
        user=user,
        file=file,
        document_type=document_type,
    )
    content = PrescriptionDocumentUploadResponse(
        data=PrescriptionDocumentUploadData(
            document_id=result.document_id,
            upload_status=UploadStatus(result.upload_status),
            uploaded_at=result.uploaded_at,
        ),
        message="처방전 업로드가 완료되었습니다.",
    ).model_dump(mode="json")
    return Response(
        content=content,
        status_code=status.HTTP_201_CREATED,
    )


@medical_document_router.post(
    "/{document_id}/ocr-jobs",
    response_model=OcrJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def execute_ocr(
    document_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    ocr_service: Annotated[OcrService, Depends(get_ocr_service)],
    request: Annotated[ExecuteOcrRequest, Body(default_factory=ExecuteOcrRequest)],
) -> Response:
    # OCR 실행 Backend 계약: 실제 CLOVA OCR 호출을 같은 요청 안에서 수행하고 결과를 저장합니다.
    result = await ocr_service.execute_ocr(user=user, document_id=document_id, request=request)

    return Response(
        content=OcrJobResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_202_ACCEPTED,
    )


@medical_document_router.get(
    "/{document_id}/ocr-jobs",
    response_model=JobStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def rediscover_ocr_job(
    document_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    job_status_service: Annotated[JobStatusService, Depends(get_job_status_service)],
) -> Response:
    # async-job-v1.md "공통 화면 재접속 복구": 화면 재진입 시 이 문서의 가장 최근 OCR Job으로
    # polling을 재개합니다. Cache-Control: no-store는 NoStoreMiddleware가 일괄 적용합니다.
    result = await job_status_service.rediscover_ocr_job(user=user, document_id=document_id)

    headers = {"Retry-After": str(result.retry_after_seconds)} if result.retry_after_seconds is not None else None
    return Response(
        content=JobStatusResponse(data=result.data).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
        headers=headers,
    )


@medical_document_router.post(
    "/{document_id}/prescription",
    response_model=PrescriptionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_prescription(
    document_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    prescription_service: Annotated[PrescriptionService, Depends(get_prescription_service)],
) -> Response:
    # 처방 최종 확정 Backend 계약(MVP): 문서의 최신 완료 OCR 결과를 기준으로 처방을 생성합니다.
    result = await prescription_service.confirm_prescription(user=user, document_id=document_id)

    return Response(
        content=PrescriptionResponse(data=result).model_dump(mode="json"),
        status_code=status.HTTP_201_CREATED,
    )


@medical_document_router.get(
    "/{document_id}/file",
    response_class=FileResponse,
    status_code=status.HTTP_200_OK,
)
async def get_prescription_document_file(
    document_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    medical_document_service: Annotated[MedicalDocumentService, Depends(get_medical_document_service)],
) -> FileResponse:
    # 처방전 원본 파일 조회 Backend 계약: 권한 확인 후 원본 파일 스트림을 반환합니다.
    # Cache-Control: no-store는 NoStoreMiddleware가 /api/v1/* 전체에 일괄 적용합니다.
    result = await medical_document_service.get_prescription_document_file(
        user=user,
        document_id=document_id,
    )
    return FileResponse(
        path=result.file_path,
        filename=result.filename,
        media_type=result.media_type,
    )
