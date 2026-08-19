from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel


class ErrorDetail(BaseModel):
    field: str
    reason: str
    rejected_value: Any | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []
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


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> ORJSONResponse:
        _ = request
        body = ErrorResponse(code=exc.code, message=exc.message, details=exc.details, trace_id=uuid4().hex)
        return ORJSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> ORJSONResponse:
        _ = request
        body = ErrorResponse(
            code="VALIDATION_FAILED",
            message="입력값을 확인해 주세요.",
            details=[_validation_error_detail(error) for error in exc.errors()],
            trace_id=uuid4().hex,
        )
        return ORJSONResponse(status_code=422, content=body.model_dump(mode="json"))
