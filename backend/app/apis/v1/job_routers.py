from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse as Response

from app.dependencies.security import get_request_user
from app.dependencies.services import get_job_status_service
from app.dtos.jobs import JobStatusResponse
from app.models.users import User
from app.services.job_status import JobStatusService

job_router = APIRouter(prefix="/jobs", tags=["jobs"])


@job_router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def get_job_status(
    job_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    job_status_service: Annotated[JobStatusService, Depends(get_job_status_service)],
) -> Response:
    # async-job-v1.md "공통 조회 응답": Cache-Control: no-store는 NoStoreMiddleware가
    # /api/v1/* 전체에 일괄 적용합니다. RETRY_WAIT에서만 Retry-After를 추가로 설정합니다.
    result = await job_status_service.get_job_status(user=user, job_id=job_id)

    headers = None
    if result.retry_after_seconds is not None:
        headers = {"Retry-After": str(result.retry_after_seconds)}

    return Response(
        content=JobStatusResponse(data=result.data).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
        headers=headers,
    )
