from httpx import ASGITransport, AsyncClient
from starlette import status

from app.main import app

SIGNUP_DATA = {
    "email": "isolation@example.com",
    "password": "Password123!",
    "name": "격리테스트",
}


async def test_database_isolation_first_signup():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/auth/signup",
            json=SIGNUP_DATA,
        )

    assert response.status_code == status.HTTP_201_CREATED


async def test_database_isolation_second_signup():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/auth/signup",
            json=SIGNUP_DATA,
        )

    assert response.status_code == status.HTTP_201_CREATED
