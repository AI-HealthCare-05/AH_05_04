from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.responses import ORJSONResponse
from httpx import ASGITransport, AsyncClient

from app.core.errors import ApiError, ErrorDetail
from app.main import app, fastapi_app

TEST_ORIGIN = "http://localhost:5173"
UNEXPECTED_ERROR_PATH = "/api/v1/_test/unexpected-error"
HTTP_EXCEPTION_PATH = "/api/v1/_test/http-exception"
API_ERROR_PATH = "/api/v1/_test/api-error"
API_ERROR_HEADERS_PATH = "/api/v1/_test/api-error-headers"
METHOD_NOT_ALLOWED_PATH = "/api/v1/_test/method-not-allowed"


async def _raise_unexpected_error() -> None:
    raise RuntimeError("test-only unexpected error")


async def _raise_http_exception() -> None:
    raise HTTPException(
        status_code=401,
        detail="인증이 필요합니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _raise_api_error() -> None:
    raise ApiError(
        status_code=422,
        code="PRESCRIPTION_REQUIRED_FIELD_MISSING",
        message="처방 확정에 필요한 항목이 누락되었습니다.",
        details=[ErrorDetail(field="medications", reason="REQUIRED")],
    )


async def _raise_api_error_with_headers() -> None:
    raise ApiError(
        status_code=401,
        code="INVALID_TOKEN",
        message="인증 정보가 유효하지 않습니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _method_not_allowed_target() -> ORJSONResponse:
    return ORJSONResponse({"data": "ok"})


@pytest.fixture(scope="module", autouse=True)
def register_test_routes() -> Iterator[None]:
    # 실제 기능 라우터를 변경하지 않고, CORS와 공통 예외 핸들러만 통합 검증합니다.
    fastapi_app.add_api_route(UNEXPECTED_ERROR_PATH, _raise_unexpected_error, methods=["GET"])
    fastapi_app.add_api_route(HTTP_EXCEPTION_PATH, _raise_http_exception, methods=["GET"])
    fastapi_app.add_api_route(API_ERROR_PATH, _raise_api_error, methods=["GET"])
    fastapi_app.add_api_route(API_ERROR_HEADERS_PATH, _raise_api_error_with_headers, methods=["GET"])
    fastapi_app.add_api_route(METHOD_NOT_ALLOWED_PATH, _method_not_allowed_target, methods=["GET"])
    try:
        yield
    finally:
        fastapi_app.router.routes[:] = [
            route
            for route in fastapi_app.router.routes
            if getattr(route, "path", None)
            not in {
                UNEXPECTED_ERROR_PATH,
                HTTP_EXCEPTION_PATH,
                API_ERROR_PATH,
                API_ERROR_HEADERS_PATH,
                METHOD_NOT_ALLOWED_PATH,
            }
        ]


class TestCorsAndErrorResponses:
    async def test_unexpected_error_keeps_cors_header(self):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get(UNEXPECTED_ERROR_PATH, headers={"Origin": TEST_ORIGIN})

        assert response.status_code == 500
        assert response.headers["access-control-allow-origin"] == TEST_ORIGIN
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["code"] == "INTERNAL_SERVER_ERROR"

    async def test_http_exception_preserves_response_headers(self):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get(HTTP_EXCEPTION_PATH, headers={"Origin": TEST_ORIGIN})

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["access-control-allow-origin"] == TEST_ORIGIN
        assert response.headers["cache-control"] == "no-store"

    async def test_api_error_returns_common_error_response(self):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get(API_ERROR_PATH)

        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "PRESCRIPTION_REQUIRED_FIELD_MISSING"
        assert body["message"] == "처방 확정에 필요한 항목이 누락되었습니다."
        assert body["details"] == [{"field": "medications", "reason": "REQUIRED", "rejected_value": None}]
        assert body["trace_id"]
        assert response.headers["cache-control"] == "no-store"

    async def test_api_error_preserves_response_headers(self):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.get(API_ERROR_HEADERS_PATH)

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert response.headers["cache-control"] == "no-store"

    @pytest.mark.parametrize(
        ("method", "path", "expected_status"),
        [
            ("GET", "/api/v1/_test/not-found", 404),
            ("POST", METHOD_NOT_ALLOWED_PATH, 405),
        ],
    )
    async def test_default_404_and_405_return_common_error_response(
        self,
        method: str,
        path: str,
        expected_status: int,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.request(method, path)

        assert response.status_code == expected_status
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert set(body) == {"code", "message", "details", "trace_id"}
        assert body["code"] == "HTTP_ERROR"
        assert isinstance(body["message"], str)
        assert body["details"] == []
        assert body["trace_id"]
