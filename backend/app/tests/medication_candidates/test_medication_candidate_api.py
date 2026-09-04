from collections.abc import AsyncIterator
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.dependencies.security import get_request_user
from app.main import app, fastapi_app

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as test_client:
            yield test_client
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)


async def test_candidate_search_is_fail_closed_before_ownership_chain(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/medication-candidate-searches",
        json={"prescription_version_medication_id": str(uuid4())},
    )

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
    assert response.json()["details"] == [
        {
            "field": "medication_candidate",
            "reason": "PRESCRIPTION_VERSION_MEDICATION_OWNERSHIP_NOT_CONNECTED",
            "rejected_value": None,
        }
    ]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/v1/medication-candidates/confirm",
            {
                "prescription_version_medication_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
        (
            "/api/v1/medication-candidates/reject",
            {
                "search_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
    ],
)
async def test_confirm_and_reject_require_idempotency_key(
    client: AsyncClient,
    path: str,
    body: dict[str, str],
) -> None:
    response = await client.post(path, json=body)

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert response.json()["details"] == [
        {
            "field": "Idempotency-Key",
            "reason": "IDEMPOTENCY_KEY_REQUIRED",
            "rejected_value": None,
        }
    ]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/v1/medication-candidates/confirm",
            {
                "prescription_version_medication_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
        (
            "/api/v1/medication-candidates/reject",
            {
                "search_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
    ],
)
async def test_confirm_and_reject_validate_idempotency_key_format(
    client: AsyncClient,
    path: str,
    body: dict[str, str],
) -> None:
    response = await client.post(path, headers={"Idempotency-Key": "short"}, json=body)

    assert response.status_code == 400
    assert response.json()["code"] == "IDEMPOTENCY_KEY_INVALID"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (
            "/api/v1/medication-candidates/confirm",
            {
                "prescription_version_medication_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
        (
            "/api/v1/medication-candidates/reject",
            {
                "search_id": str(uuid4()),
                "candidate_search_result_id": str(uuid4()),
            },
        ),
    ],
)
async def test_confirm_and_reject_are_fail_closed_after_idempotency_validation(
    client: AsyncClient,
    path: str,
    body: dict[str, str],
) -> None:
    response = await client.post(
        path,
        headers={"Idempotency-Key": "candidate-key-20260904-001"},
        json=body,
    )

    assert response.status_code == 503
    assert response.json()["code"] == "SERVICE_UNAVAILABLE"
