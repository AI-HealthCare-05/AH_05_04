import base64
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport, AsyncClient

from app.apis.v1.push_routers import get_push_service
from app.apis.v1.push_routers import push_settings as settings_dependency
from app.core.push import PushSettings
from app.dependencies.security import get_request_user
from app.dtos.push import PushSubscriptionRequest
from app.main import app, fastapi_app
from app.repositories.push_repository import PushRepository
from app.services.push import PushSubscriptionService
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile

NOW = datetime(2026, 9, 13, 4, tzinfo=UTC)


def encoded_public(key):
    return (
        base64.urlsafe_b64encode(
            key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        )
        .decode()
        .rstrip("=")
    )


@pytest.fixture
def push_settings():
    key = ec.generate_private_key(ec.SECP256R1())
    return PushSettings(
        enabled=True,
        allowed_hosts=["push.example.test"],
        vapid_private_key=key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
        vapid_public_key=encoded_public(key),
        vapid_subject="mailto:synthetic@example.test",
        encryption_keys={"test-v1": Fernet.generate_key().decode()},
        active_key_id="test-v1",
        endpoint_hmac_key="synthetic-hmac-key-for-local-tests-only",
    )


@pytest.fixture
def subscription_request():
    # Ephemeral synthetic receiver key; no real endpoint, patient data, or credentials.
    return PushSubscriptionRequest(
        endpoint="https://push.example.test/synthetic-subscription",
        keys={
            "p256dh": encoded_public(ec.generate_private_key(ec.SECP256R1())),
            "auth": base64.urlsafe_b64encode(b"synthetic-auth16").decode().rstrip("="),
        },
    )


@dataclass
class Case:
    client: AsyncClient
    identity: SimpleNamespace
    owner: object
    profile: object
    service: PushSubscriptionService


@pytest.fixture
async def case(db_session, push_settings, monkeypatch):
    monkeypatch.setattr("app.services.push.resolve_endpoint", lambda *args: ("push.example.test", "8.8.8.8"))
    owner, profile = await _create_user_with_self_profile(db_session, label="push-owner")
    identity = SimpleNamespace(id=owner.id, token_version=owner.token_version)
    service = PushSubscriptionService(PushRepository(db_session), push_settings, clock=lambda: NOW)
    fastapi_app.dependency_overrides[get_request_user] = lambda: identity
    fastapi_app.dependency_overrides[get_push_service] = lambda: service
    fastapi_app.dependency_overrides[settings_dependency] = lambda: push_settings
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield Case(client, identity, owner, profile, service)
    finally:
        for dep in (get_request_user, get_push_service, settings_dependency):
            fastapi_app.dependency_overrides.pop(dep, None)


async def register(case, request):
    response = await case.client.put("/api/v1/push/subscriptions", json=request.model_dump())
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()["data"]
