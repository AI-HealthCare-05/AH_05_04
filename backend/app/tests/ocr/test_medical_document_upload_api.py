from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from starlette import status

from app.core import config
from app.main import app
from app.services.medical_documents import MAX_DOCUMENT_SIZE_BYTES
from app.tests.helpers.auth import signup_verified_user
from app.tests.helpers.images import synthetic_jpeg

JPEG_SIGNATURE = b"\xff\xd8\xff"


async def _signup_and_login(client: AsyncClient, *, email: str) -> str:
    signup_data = {
        "email": email,
        "password": "Password123!",
        "name": "업로드API테스터",
    }
    await signup_verified_user(client, signup_data)
    login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    access_token: str = login_response.json()["access_token"]
    return access_token


class TestCreatePrescriptionDocumentAPI:
    async def test_requires_authentication(self):
        files = {"file": ("prescription.jpg", synthetic_jpeg(), "image/jpeg")}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/documents", files=files)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_upload_success(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            access_token = await _signup_and_login(client, email="upload-success@example.com")

            files = {"file": ("prescription.jpg", synthetic_jpeg(), "image/jpeg")}
            response = await client.post(
                "/api/v1/documents",
                files=files,
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["data"]["upload_status"] == "UPLOADED"
        assert "document_id" in body["data"]

    async def test_upload_rejects_invalid_file_type(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            access_token = await _signup_and_login(client, email="upload-invalid-type@example.com")

            files = {"file": ("prescription.exe", b"not-an-image", "application/octet-stream")}
            response = await client.post(
                "/api/v1/documents",
                files=files,
                headers={"Authorization": f"Bearer {access_token}"},
            )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert body["code"] == "UPLOAD_FILE_INVALID_TYPE"
        assert "trace_id" in body


@pytest.mark.asyncio
@pytest.mark.parametrize("extra_bytes", [0, 1])
async def test_upload_api_size_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_bytes: int,
) -> None:
    storage_dir = tmp_path / "uploaded"
    storage_dir.mkdir()
    monkeypatch.setattr(config, "STORAGE_DIR", str(storage_dir))

    jpeg = synthetic_jpeg()
    content = jpeg + b"x" * (MAX_DOCUMENT_SIZE_BYTES - len(jpeg) + extra_bytes)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        access_token = await _signup_and_login(
            client,
            email=f"upload-boundary-{extra_bytes}@example.com",
        )
        response = await client.post(
            "/api/v1/documents",
            files={
                "file": (
                    "synthetic-boundary.jpg",
                    content,
                    "image/jpeg",
                )
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

    body = response.json()
    stored_files = list(storage_dir.iterdir())

    if extra_bytes == 0:
        assert response.status_code == status.HTTP_201_CREATED
        assert body["data"]["upload_status"] == "UPLOADED"
        assert len(stored_files) == 2
        original = storage_dir / f"{body['data']['document_id']}.jpg"
        assert original.stat().st_size == MAX_DOCUMENT_SIZE_BYTES
        assert (storage_dir / f"{body['data']['document_id']}.normalized.png").exists()
    else:
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert body["code"] == "UPLOAD_FILE_TOO_LARGE"
        assert "trace_id" in body
        assert stored_files == []


@pytest.mark.asyncio
async def test_normalized_file_preserves_original_and_requires_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STORAGE_DIR", str(tmp_path))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        token = await _signup_and_login(client, email="normalized-owner@example.com")
        headers = {"Authorization": f"Bearer {token}"}
        original = synthetic_jpeg()
        upload = await client.post(
            "/api/v1/documents", files={"file": ("rx.jpg", original, "image/jpeg")}, headers=headers
        )
        assert upload.status_code == 201
        document_id = upload.json()["data"]["document_id"]
        url = f"/api/v1/documents/{document_id}/normalized-file"
        normalized = await client.get(url, headers=headers)
        assert normalized.status_code == 200
        assert normalized.headers["content-type"] == "image/png"
        assert normalized.headers["cache-control"] == "no-store"
        assert normalized.content.startswith(b"\x89PNG\r\n\x1a\n")
        raw = await client.get(f"/api/v1/documents/{document_id}/file", headers=headers)
        assert raw.content == original
        other = await _signup_and_login(client, email="normalized-other@example.com")
        denied = await client.get(url, headers={"Authorization": f"Bearer {other}"})
        assert denied.status_code == 404
        assert (await client.get(url)).status_code == 401
