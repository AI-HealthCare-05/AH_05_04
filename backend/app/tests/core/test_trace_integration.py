from collections.abc import Mapping

import httpx

from app.main import app, fastapi_app


async def _raise_unhandled_error() -> None:
    raise RuntimeError("synthetic unhandled error")


fastapi_app.add_api_route("/__tests__/unhandled-error", _raise_unhandled_error, methods=["GET"])


async def _request(method: str, path: str, *, headers: Mapping[str, str] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        return await client.request(method, path, headers=headers)


async def test_not_found_and_method_not_allowed_share_body_and_header_trace() -> None:
    for method, path, expected_status in (
        ("GET", "/synthetic-not-found", 404),
        ("POST", "/api/openapi.json", 405),
    ):
        response = await _request(method, path)

        assert response.status_code == expected_status
        assert len(response.headers["X-Trace-Id"]) == 32
        assert response.json()["trace_id"] == response.headers["X-Trace-Id"]


async def test_unapproved_validation_header_is_rejected_with_trace() -> None:
    response = await _request(
        "GET",
        "/api/v1/health",
        headers={"X-Validation-Run-Id": "61a10000-0000-4000-8000-000000000003"},
    )

    assert response.status_code == 403
    assert response.json()["message"] == "Validation run is not allowed."
    assert response.json()["trace_id"] == response.headers["X-Trace-Id"]


async def test_validation_rejection_passes_through_cors_and_no_store_boundaries() -> None:
    response = await _request(
        "GET",
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:5173",
            "X-Validation-Run-Id": "61a10000-0000-4000-8000-000000000003",
        },
    )

    assert response.status_code == 403
    assert response.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert "x-trace-id" in response.headers["Access-Control-Expose-Headers"].lower()
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["trace_id"] == response.headers["X-Trace-Id"]


async def test_unhandled_error_shares_body_and_header_trace_without_exposing_exception() -> None:
    response = await _request("GET", "/__tests__/unhandled-error")

    assert response.status_code == 500
    assert response.json()["trace_id"] == response.headers["X-Trace-Id"]
    assert "synthetic unhandled error" not in response.text


async def test_cors_exposes_trace_header() -> None:
    response = await _request(
        "GET",
        "/api/openapi.json",
        headers={"Origin": "http://localhost:5173"},
    )

    exposed = {value.strip().lower() for value in response.headers["Access-Control-Expose-Headers"].split(",")}
    assert "x-trace-id" in exposed


async def test_cors_preflight_also_receives_server_trace_header() -> None:
    response = await _request(
        "OPTIONS",
        "/api/openapi.json",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Validation-Run-Id",
        },
    )

    assert response.status_code == 200
    assert len(response.headers["X-Trace-Id"]) == 32
