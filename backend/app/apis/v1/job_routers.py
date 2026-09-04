from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse as Response

from app.core.errors import ErrorResponse
from app.dependencies.security import get_request_user
from app.dependencies.services import get_job_status_service
from app.dtos.jobs import JobStatusResponse
from app.models.users import User
from app.services.job_status import JobStatusResult, JobStatusService

job_router = APIRouter(prefix="/jobs", tags=["jobs"])

# `build_job_status_response()`가 만드는 200 응답에서만 값이 있는 `Retry-After` 헤더를
# FastAPI가 자동 생성하지 못하므로(raw Response 반환) OpenAPI에 명시합니다. 오류 응답도
# 전역 예외 핸들러(`register_exception_handlers`)가 만들어 FastAPI가 자동 문서화하지 못하므로
# 실제로 나오는 형태를 여기 명시합니다(#148 리뷰 지적: 생성 타입만으로 FE error 계약을
# 고정하기 어렵다는 문제). `GET /jobs/{job_id}`와 OCR/Guide rediscovery 라우트가 이 응답
# 형태를 공유합니다.
JOB_STATUS_OPENAPI_RESPONSES: dict[int | str, dict] = {
    status.HTTP_200_OK: {
        "headers": {
            "Retry-After": {
                "description": (
                    "`RETRY_WAIT` 상태일 때만 포함되며 `data.retry_after_seconds`와 같은 값(초)입니다. "
                    "Cross-origin Frontend가 읽을 수 있도록 CORS `Access-Control-Expose-Headers`에도 포함됩니다."
                ),
                "schema": {"type": "integer"},
            }
        }
    },
    status.HTTP_401_UNAUTHORIZED: {
        "model": ErrorResponse,
        "description": (
            "인증 정보가 없거나 유효하지 않습니다. `code`는 `UNAUTHORIZED`·`INVALID_TOKEN`·`EXPIRED_TOKEN`입니다."
        ),
        "headers": {
            "WWW-Authenticate": {
                "description": "계약된 `Bearer` 고정값입니다.",
                "schema": {"type": "string"},
            }
        },
    },
    status.HTTP_404_NOT_FOUND: {
        "model": ErrorResponse,
        "description": "Job이 없거나, 있어도 다른 사용자의 Job이거나 도메인 결과 소유권이 어긋납니다. `code`는 `AI_JOB_NOT_FOUND`로 고정입니다.",
        "content": {
            "application/json": {
                "example": {
                    "code": "AI_JOB_NOT_FOUND",
                    "message": "작업 정보를 찾을 수 없습니다.",
                    "details": [],
                    "trace_id": "a1b2c3d4e5f647890123456789abcdef",
                }
            }
        },
    },
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ErrorResponse,
        "description": (
            "Path parameter(UUID 등) 형식이 올바르지 않습니다. FastAPI가 자동 생성하는 "
            "`HTTPValidationError`가 아니라, 전역 `RequestValidationError` 핸들러가 만드는 "
            "`ErrorResponse`(`code=VALIDATION_FAILED`)가 실제 응답입니다."
        ),
    },
}


def build_job_status_response(result: JobStatusResult) -> Response:
    """`GET /jobs/{job_id}`와 OCR/Guide rediscovery 라우트가 공유하는 응답 조립입니다.
    RETRY_WAIT에서만 `Retry-After` 헤더를 추가로 설정합니다."""
    headers = None
    if result.retry_after_seconds is not None:
        headers = {"Retry-After": str(result.retry_after_seconds)}

    return Response(
        content=JobStatusResponse(data=result.data).model_dump(mode="json"),
        status_code=status.HTTP_200_OK,
        headers=headers,
    )


@job_router.get(
    "/{job_id}",
    response_model=JobStatusResponse,
    status_code=status.HTTP_200_OK,
    responses=JOB_STATUS_OPENAPI_RESPONSES,
)
async def get_job_status(
    job_id: UUID,
    user: Annotated[User, Depends(get_request_user)],
    job_status_service: Annotated[JobStatusService, Depends(get_job_status_service)],
) -> Response:
    # async-job-v1.md "공통 조회 응답": Cache-Control: no-store는 NoStoreMiddleware가
    # /api/v1/* 전체에 일괄 적용합니다.
    result = await job_status_service.get_job_status(user=user, job_id=job_id)
    return build_job_status_response(result)
