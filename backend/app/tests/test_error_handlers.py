from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.errors import register_exception_handlers
from app.core.validation_trace_middleware import RequestTraceMiddleware
from app.services.job_intake import IdempotencyKeyConflictError


def _build_test_app() -> RequestTraceMiddleware:
    """`IdempotencyKeyConflictError` → `409` 매핑은 아직 이 예외를 실제로 발생시키는 라우트가
    없어서(OCR·Guide·Chat 접수가 `JobIntakeService.accept_job()`에 연결되기 전) 최소 앱으로
    핸들러 자체만 검증합니다. 접수 API가 연결되면 그 라우트의 통합 테스트가 이 경로를 다시
    exercise하게 됩니다. `trace_id`가 필요해 `RequestTraceMiddleware`로 감쌉니다."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-idempotency-conflict")
    async def raise_conflict() -> None:
        raise IdempotencyKeyConflictError("IDEMPOTENCY_KEY_CONFLICT")

    return RequestTraceMiddleware(app)


async def test_idempotency_key_conflict_maps_to_409() -> None:
    async with AsyncClient(transport=ASGITransport(app=_build_test_app()), base_url="http://test") as client:
        response = await client.get("/raise-idempotency-conflict")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert body["details"] == []
    assert body["trace_id"] == response.headers["X-Trace-Id"]
