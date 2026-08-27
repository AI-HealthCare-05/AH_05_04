from httpx import ASGITransport, AsyncClient
from starlette import status

from app.main import app

JPEG_SIGNATURE = b"\xff\xd8\xff"


async def _signup_and_login(client: AsyncClient, *, email: str) -> str:
    signup_data = {
        "email": email,
        "password": "Password123!",
        "name": "업로드API테스터",
    }
    await client.post("/api/v1/auth/signup", json=signup_data)
    login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    access_token: str = login_response.json()["access_token"]
    return access_token


class TestCreatePrescriptionDocumentAPI:
    async def test_requires_authentication(self):
        files = {"file": ("prescription.jpg", JPEG_SIGNATURE + b"fake-jpeg", "image/jpeg")}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/documents", files=files)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_upload_success(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            access_token = await _signup_and_login(client, email="upload-success@example.com")

            files = {"file": ("prescription.jpg", JPEG_SIGNATURE + b"fake-jpeg", "image/jpeg")}
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
