import pytest
from httpx import AsyncClient
from starlette import status


@pytest.mark.asyncio
async def test_get_user_me_success(client: AsyncClient):
    # 사용자 등록 및 로그인
    email = "me@example.com"
    signup_data = {
        "email": email,
        "password": "Password123!",
        "name": "내정보테스터",
        "gender": "FEMALE",
        "birth_date": "1992-02-02",
        "phone_number": "01055556666",
    }
    await client.post("/api/v1/auth/signup", json=signup_data)

    login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    access_token = login_response.json()["access_token"]

    # 내 정보 조회
    headers = {"Authorization": f"Bearer {access_token}"}
    response = await client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["email"] == email
    assert response.json()["name"] == "내정보테스터"


@pytest.mark.asyncio
async def test_update_user_me_success(client: AsyncClient):
    # 사용자 등록 및 로그인
    email = "update_me@example.com"
    signup_data = {
        "email": email,
        "password": "Password123!",
        "name": "수정전",
        "gender": "MALE",
        "birth_date": "1990-10-10",
        "phone_number": "01077778888",
    }
    await client.post("/api/v1/auth/signup", json=signup_data)

    login_response = await client.post("/api/v1/auth/login", json={"email": email, "password": "Password123!"})
    access_token = login_response.json()["access_token"]

    # 내 정보 수정
    headers = {"Authorization": f"Bearer {access_token}"}
    update_data = {"name": "수정후"}
    response = await client.patch("/api/v1/users/me", json=update_data, headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["name"] == "수정후"


@pytest.mark.asyncio
async def test_get_user_me_unauthorized(client: AsyncClient):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
