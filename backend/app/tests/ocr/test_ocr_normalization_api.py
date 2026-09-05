from httpx import ASGITransport, AsyncClient
from starlette import status

from app.main import app

JPEG_SIGNATURE = b"\xff\xd8\xff"


async def _signup_and_login(
    client: AsyncClient,
    *,
    label: str = "normalization",
) -> str:
    signup_data = {
        "email": f"ocr-{label}-api@example.com",
        "password": "Password123!",
        "name": "OCR정규화테스터",
    }

    await client.post(
        "/api/v1/auth/signup",
        json=signup_data,
    )

    login_response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": signup_data["email"],
            "password": signup_data["password"],
        },
    )

    access_token: str = login_response.json()["access_token"]
    return access_token


async def _upload_document(client: AsyncClient, *, access_token: str) -> str:
    upload_response = await client.post(
        "/api/v1/documents",
        files={
            "file": (
                "prescription.jpg",
                JPEG_SIGNATURE + b"fake-jpeg",
                "image/jpeg",
            ),
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert upload_response.status_code == status.HTTP_201_CREATED, upload_response.text
    return upload_response.json()["data"]["document_id"]


async def test_ocr_intake_returns_job_status_without_running_provider() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        access_token = await _signup_and_login(client)
        document_id = await _upload_document(client, access_token=access_token)

        execute_response = await client.post(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            json={"force_reprocess": False},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Idempotency-Key": "ocr-intake-normalization-0001",
            },
        )

        assert execute_response.status_code == status.HTTP_202_ACCEPTED, execute_response.text
        assert execute_response.headers["location"].startswith("/api/v1/jobs/")

        data = execute_response.json()["data"]
        assert data["job_type"] == "OCR"
        assert data["status"] == "PENDING"
        assert data["domain_type"] == "OCR_JOB"
        assert data["status_url"] == execute_response.headers["location"]
        assert data["result_url"] is None
        assert data["error"] is None

        status_response = await client.get(
            data["status_url"],
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert status_response.status_code == status.HTTP_200_OK
    assert status_response.json()["data"]["job_id"] == data["job_id"]


async def test_ocr_job_result_returns_404_for_another_user() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        owner_token = await _signup_and_login(client, label="owner")
        other_token = await _signup_and_login(client, label="other")
        document_id = await _upload_document(client, access_token=owner_token)

        execute_response = await client.post(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            json={"force_reprocess": False},
            headers={
                "Authorization": f"Bearer {owner_token}",
                "Idempotency-Key": "ocr-intake-owner-only-0001",
            },
        )

        assert execute_response.status_code == status.HTTP_202_ACCEPTED, execute_response.text
        ocr_job_id = execute_response.json()["data"]["domain_id"]

        other_response = await client.get(
            f"/api/v1/ocr-jobs/{ocr_job_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )

    assert other_response.status_code == status.HTTP_404_NOT_FOUND
    assert other_response.json()["code"] == "OCR_JOB_NOT_FOUND"


async def test_ocr_intake_requires_idempotency_key() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        access_token = await _signup_and_login(client, label="missing-key")
        document_id = await _upload_document(client, access_token=access_token)

        response = await client.post(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            json={"force_reprocess": False},
            headers={"Authorization": f"Bearer {access_token}"},
        )

    body = response.json()
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert body["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert body["details"][0]["field"] == "Idempotency-Key"


async def test_ocr_intake_reuses_same_idempotency_key_for_same_request() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        access_token = await _signup_and_login(client, label="same-key")
        document_id = await _upload_document(client, access_token=access_token)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Idempotency-Key": "ocr-intake-same-request-0001",
        }

        first_response = await client.post(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            json={"force_reprocess": False},
            headers=headers,
        )
        second_response = await client.post(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            json={"force_reprocess": False},
            headers=headers,
        )

    assert first_response.status_code == status.HTTP_202_ACCEPTED, first_response.text
    assert second_response.status_code == status.HTTP_202_ACCEPTED, second_response.text
    assert second_response.json()["data"]["job_id"] == first_response.json()["data"]["job_id"]
    assert second_response.json()["data"]["domain_id"] == first_response.json()["data"]["domain_id"]


async def test_ocr_intake_rejects_same_idempotency_key_with_different_request() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        access_token = await _signup_and_login(client, label="key-conflict")
        document_id = await _upload_document(client, access_token=access_token)
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Idempotency-Key": "ocr-intake-conflict-0001",
        }

        first_response = await client.post(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            json={"force_reprocess": False},
            headers=headers,
        )
        conflict_response = await client.post(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            json={"force_reprocess": True},
            headers=headers,
        )

    assert first_response.status_code == status.HTTP_202_ACCEPTED, first_response.text
    assert conflict_response.status_code == status.HTTP_409_CONFLICT
    assert conflict_response.json()["code"] == "IDEMPOTENCY_KEY_CONFLICT"


async def test_ocr_intake_rejects_second_active_job_with_different_key() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        access_token = await _signup_and_login(client, label="active-conflict")
        document_id = await _upload_document(client, access_token=access_token)

        first_response = await client.post(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            json={"force_reprocess": False},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Idempotency-Key": "ocr-intake-active-first-0001",
            },
        )
        second_response = await client.post(
            f"/api/v1/documents/{document_id}/ocr-jobs",
            json={"force_reprocess": False},
            headers={
                "Authorization": f"Bearer {access_token}",
                "Idempotency-Key": "ocr-intake-active-second-0001",
            },
        )

    assert first_response.status_code == status.HTTP_202_ACCEPTED, first_response.text
    assert second_response.status_code == status.HTTP_409_CONFLICT
    assert second_response.json()["code"] == "OCR_JOB_ALREADY_PROCESSING"
