import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app, cors_app, fastapi_app

ERROR_PATH = "/api/v1/_internal/upload-too-large"
ALLOWED_ORIGIN = "https://upload-ui.example"
DENIED_ORIGIN = "https://untrusted.example"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [None, ALLOWED_ORIGIN, DENIED_ORIGIN],
)
async def test_proxy_upload_error_preserves_contract(
    monkeypatch: pytest.MonkeyPatch,
    origin: str | None,
) -> None:
    monkeypatch.setattr(cors_app, "allow_origins", [ALLOWED_ORIGIN])
    monkeypatch.setattr(cors_app, "allow_all_origins", False)
    monkeypatch.setattr(cors_app, "allow_origin_regex", None)

    headers = {"Origin": origin} if origin is not None else {}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            ERROR_PATH,
            headers=headers,
        )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["cache-control"] == "no-store"

    body = response.json()
    assert set(body) == {"code", "message", "details", "trace_id"}
    assert body["code"] == "UPLOAD_FILE_TOO_LARGE"
    assert body["message"] == "파일 크기는 30MB 이하만 업로드할 수 있습니다."
    assert body["details"] == [
        {
            "field": "file",
            "reason": "TOO_LARGE",
            "rejected_value": None,
        }
    ]
    assert body["trace_id"]
    assert response.headers["x-trace-id"] == body["trace_id"]

    if origin == ALLOWED_ORIGIN:
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"
        assert "origin" in response.headers["vary"].lower()
        exposed_headers = {
            value.strip().lower() for value in response.headers["access-control-expose-headers"].split(",")
        }
        assert "x-trace-id" in exposed_headers
    else:
        assert "access-control-allow-origin" not in response.headers


def test_proxy_upload_error_is_not_in_public_openapi() -> None:
    assert ERROR_PATH not in fastapi_app.openapi()["paths"]
