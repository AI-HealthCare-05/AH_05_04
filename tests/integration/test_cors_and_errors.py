from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.main import app, fastapi_app

TEST_ORIGIN = "http://localhost:5173"
UNEXPECTED_ERROR_PATH = "/_test/unexpected-error"
HTTP_EXCEPTION_PATH = "/_test/http-exception"


async def _raise_unexpected_error() -> None:
    raise RuntimeError("test-only unexpected error")


async def _raise_http_exception() -> None:
    raise HTTPException(
        status_code=401,
        detail="인증이 필요합니다.",
        headers={"WWW-Authenticate": "Bearer"},
    )


@pytest.fixture(scope="module", autouse=True)
def register_test_routes() -> Iterator[None]:
    # 실제 기능 라우터를 변경하지 않고, CORS와 공통 예외 핸들러만 통합 검증합니다.
    fastapi_app.add_api_route(UNEXPECTED_ERROR_PATH, _raise_unexpected_error, methods=["GET"])
    fastapi_app.add_api_route(HTTP_EXCEPTION_PATH, _raise_http_exception, methods=["GET"])
    try:
        yield
    finally:
        fastapi_app.router.routes[:] = [
            route
            for route in fastapi_app.router.routes
            if getattr(route, "path", None) not in {UNEXPECTED_ERROR_PATH, HTTP_EXCEPTION_PATH}
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
