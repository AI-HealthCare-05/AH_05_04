from collections.abc import Mapping
from typing import Any

from httpx import AsyncClient
from starlette import status


async def verify_signup_email(client: AsyncClient, *, email: str) -> None:
    request_response = await client.post(
        "/api/v1/auth/email-verification/request",
        json={"email": email},
    )
    assert request_response.status_code == status.HTTP_200_OK, request_response.text
    verification_token = request_response.json()["verification_token"]
    assert verification_token

    confirm_response = await client.post(
        "/api/v1/auth/email-verification/confirm",
        json={"email": email, "token": verification_token},
    )
    assert confirm_response.status_code == status.HTTP_200_OK, confirm_response.text


async def signup_verified_user(client: AsyncClient, signup_data: Mapping[str, Any]) -> None:
    email = str(signup_data["email"])
    await verify_signup_email(client, email=email)
    signup_response = await client.post("/api/v1/auth/signup", json=dict(signup_data))
    assert signup_response.status_code == status.HTTP_201_CREATED, signup_response.text
