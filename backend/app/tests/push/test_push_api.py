from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.apis.v1 import push_routers
from app.core import config
from app.dependencies.security import get_request_user
from app.main import app, fastapi_app
from app.models.push import PushSubscription
from app.tests.push.conftest import NOW, register
from app.tests.repositories.test_medication_schedule_repository_integration import _create_user_with_self_profile


async def test_upsert_encryption_idempotency_and_multiple_devices(case, db_session, subscription_request):
    first = await register(case, subscription_request)
    assert await register(case, subscription_request) == first
    subscription_request.endpoint += "-second-device"
    second = await register(case, subscription_request)
    assert first["id"] != second["id"]
    rows = list(await db_session.scalars(select(PushSubscription)))
    assert len(rows) == 2
    assert all(
        b"https://" not in row.ciphertext and subscription_request.keys.auth.encode() not in row.ciphertext
        for row in rows
    )
    assert set(first) == {"id", "generation"}
    config = await case.client.get("/api/v1/push/config")
    assert set(config.json()["data"]) == {"public_key"}


async def test_delete_replay_and_reregister_rotates_generation(case, db_session, subscription_request):
    first = await register(case, subscription_request)
    for _ in range(2):
        response = await case.client.delete(f"/api/v1/push/subscriptions/{first['id']}")
        assert response.status_code == 204 and not response.content
    row = await db_session.scalar(select(PushSubscription))
    assert row.ciphertext is None and row.revoked_at >= NOW
    case.service.clock = lambda: NOW + timedelta(seconds=1)
    second = await register(case, subscription_request)
    assert second["id"] == first["id"] and second["generation"] != first["generation"]
    assert row.activated_at == NOW + timedelta(seconds=1)


async def test_other_owner_cannot_claim_or_delete_subscription(case, db_session, subscription_request):
    first = await register(case, subscription_request)
    other, _ = await _create_user_with_self_profile(db_session, label="push-other")
    await db_session.commit()
    case.identity.id = other.id
    for id_value in (first["id"], str(uuid4())):
        response = await case.client.delete(f"/api/v1/push/subscriptions/{id_value}")
        assert response.status_code == 404
        assert response.json()["code"] == "PUSH_SUBSCRIPTION_NOT_FOUND"
    response = await case.client.put("/api/v1/push/subscriptions", json=subscription_request.model_dump())
    assert response.status_code == 409
    assert response.json()["code"] == "PUSH_SUBSCRIPTION_CONFLICT"


async def test_stale_token_rechecked_under_user_lock(case, db_session, subscription_request):
    case.owner.token_version += 1
    await db_session.flush()
    response = await case.client.put("/api/v1/push/subscriptions", json=subscription_request.model_dump())
    assert response.status_code == 401
    assert await db_session.scalar(select(func.count()).select_from(PushSubscription)) == 0


async def test_disabled_registration_but_revocation_available(case, subscription_request):
    first = await register(case, subscription_request)
    case.service.settings.enabled = False
    response = await case.client.put("/api/v1/push/subscriptions", json=subscription_request.model_dump())
    assert response.status_code == 503
    assert (await case.client.get("/api/v1/push/config")).status_code == 503
    assert (await case.client.delete(f"/api/v1/push/subscriptions/{first['id']}")).status_code == 204


async def test_production_gate_is_enforced_through_http_dependencies(
    db_session, push_settings, subscription_request, monkeypatch
):
    monkeypatch.setattr(config, "ENV", "production")
    monkeypatch.setattr("app.services.push.resolve_endpoint", lambda *args: ("push.example.test", "8.8.8.8"))
    owner, _ = await _create_user_with_self_profile(db_session, label="push-router-production")
    identity = SimpleNamespace(id=owner.id, token_version=owner.token_version)
    settings_ref = SimpleNamespace(value=push_settings.model_copy(update={"production_enabled": True}))
    monkeypatch.setattr(push_routers, "get_push_settings", lambda: settings_ref.value)
    fastapi_app.dependency_overrides[get_request_user] = lambda: identity
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            config_response = await client.get("/api/v1/push/config")
            assert config_response.status_code == 200
            registered = await client.put("/api/v1/push/subscriptions", json=subscription_request.model_dump())
            assert registered.status_code == 200, registered.text

            settings_ref.value = push_settings.model_copy(update={"enabled": False, "production_enabled": True})
            assert (await client.get("/api/v1/push/config")).status_code == 503
            response = await client.put("/api/v1/push/subscriptions", json=subscription_request.model_dump())
            assert response.status_code == 503

            settings_ref.value = push_settings.model_copy(update={"production_enabled": False})
            assert (await client.get("/api/v1/push/config")).status_code == 503
            response = await client.put("/api/v1/push/subscriptions", json=subscription_request.model_dump())
            assert response.status_code == 503
            deleted = await client.delete(f"/api/v1/push/subscriptions/{registered.json()['data']['id']}")
            assert deleted.status_code == 204
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)


async def test_revocation_survives_malformed_secret_environment(case, subscription_request, monkeypatch):
    first = await register(case, subscription_request)
    monkeypatch.setenv("WEB_PUSH_ENCRYPTION_KEYS", "invalid-json")
    assert (await case.client.delete(f"/api/v1/push/subscriptions/{first['id']}")).status_code == 204


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", ""),
        ("keys", {"p256dh": "SENSITIVE_SENTINEL", "auth": "SENSITIVE_SENTINEL"}),
        ("extra", "SENSITIVE_SENTINEL"),
    ],
)
async def test_invalid_body_never_echoes_input(case, subscription_request, field, value):
    body = subscription_request.model_dump()
    body[field] = value
    response = await case.client.put("/api/v1/push/subscriptions", json=body)
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert "SENSITIVE_SENTINEL" not in response.text


async def test_real_auth_required(case, subscription_request):
    fastapi_app.dependency_overrides.pop(get_request_user)
    for method, path, body in [
        ("GET", "/api/v1/push/config", None),
        ("PUT", "/api/v1/push/subscriptions", subscription_request.model_dump()),
        ("DELETE", f"/api/v1/push/subscriptions/{uuid4()}", None),
    ]:
        response = await case.client.request(method, path, json=body)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"


async def test_openapi_contract(case):
    schema = fastapi_app.openapi()
    assert schema["paths"]["/api/v1/push/subscriptions"]["put"]["operationId"] == "push-subscription.upsert"
    assert set(schema["components"]["schemas"]["PushSubscriptionRequest"]["required"]) == {"endpoint", "keys"}
    assert schema["components"]["schemas"]["PushSubscriptionRequest"]["additionalProperties"] is False
