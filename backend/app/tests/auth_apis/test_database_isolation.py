from httpx import ASGITransport, AsyncClient

from app.main import app
from app.tests.helpers.auth import signup_verified_user

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
        await signup_verified_user(client, SIGNUP_DATA)


async def test_database_isolation_second_signup():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await signup_verified_user(client, SIGNUP_DATA)
