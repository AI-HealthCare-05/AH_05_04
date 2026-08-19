from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    field: str
    reason: str
    rejected_value: Any | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
    trace_id: str


class ApiError(Exception):
    # 공통 오류 응답 계약: code/message/details/trace_id 형식으로 응답합니다.
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: list[ErrorDetail] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []
        super().__init__(message)


def _validation_error_detail(error: dict[str, Any]) -> ErrorDetail:
    field = ".".join(str(part) for part in error["loc"] if part != "body") or "body"
    reason = "REQUIRED" if error["type"] in {"missing", "value_error.missing"} else "INVALID_FORMAT"
    return ErrorDetail(field=field, reason=reason)


def _get_trace_id(request: Request) -> str:
    # trace_id 미들웨어가 request.state에 저장한 값을 재사용합니다.
    # 미들웨어를 거치지 않은 예외적인 상황을 대비해 없으면 새로 생성합니다.
    trace_id = getattr(request.state, "trace_id", None)
    return trace_id if isinstance(trace_id, str) else uuid4().hex


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> ORJSONResponse:
        body = ErrorResponse(code=exc.code, message=exc.message, details=exc.details, trace_id=_get_trace_id(request))
        return ORJSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> ORJSONResponse:
        body = ErrorResponse(
            code="VALIDATION_FAILED",
            message="입력값을 확인해 주세요.",
            details=[_validation_error_detail(error) for error in exc.errors()],
            trace_id=_get_trace_id(request),
        )
        return ORJSONResponse(status_code=422, content=body.model_dump(mode="json"))

    @app.exception_handler(HTTPException)
    async def handle_http_exception(request: Request, exc: HTTPException) -> ORJSONResponse:
        body = ErrorResponse(
            code="HTTP_ERROR",
            message=str(exc.detail),
            details=[],
            trace_id=_get_trace_id(request),
        )
        return ORJSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> ORJSONResponse:
        body = ErrorResponse(
            code="INTERNAL_SERVER_ERROR",
            message="서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            details=[],
            trace_id=_get_trace_id(request),
        )
        return ORJSONResponse(status_code=500, content=body.model_dump(mode="json"))
