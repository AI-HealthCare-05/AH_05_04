from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.dependencies.security import get_request_user
from app.main import fastapi_app
from app.models.async_jobs import IdempotencyRecord
from app.models.track_c import SupportActionPlan
from app.tests.track_c.test_track_c_api import ApiCase, assert_error, safety_body
from app.tests.track_c.test_track_c_api import case as track_c_case
from app.tests.track_c.test_track_c_support_api import create, offer, plan_body, prepare

case = track_c_case


async def active_plan(case: ApiCase) -> dict:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    response = await create(case, plan_body(barrier_id, support))
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def patch(case: ApiCase, plan_id: str, status="COMPLETED", key="plan-lifecycle-confirm-key", **extra):
    return await case.client.patch(
        f"/api/v1/support-action-plans/{plan_id}",
        json={"status": status, "confirmed": True, **extra},
        headers={"Idempotency-Key": key},
    )


@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
async def test_terminal_transition_preserves_snapshot_and_replays(case: ApiCase, status: str) -> None:
    original = await active_plan(case)
    plan_id = original["support_action_plan_id"]
    before = await case.client.get(f"/api/v1/support-action-plans/{plan_id}")
    assert before.json()["data"] == original
    assert before.headers["cache-control"] == "no-store"
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 3
    response = await patch(case, plan_id, status)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    terminal = response.json()["data"]
    assert terminal["status"] == status
    replay = await create(
        case,
        {
            "barrier_response_id": original["barrier_response_id"],
            "support_code": original["support_code"],
            "rule_version": original["rule_version"],
            "copy_version": original["copy_version"],
            "confirmed": True,
        },
    )
    assert replay.json()["data"] == original
    assert terminal["completed_at" if status == "COMPLETED" else "cancelled_at"] is not None
    assert terminal["cancelled_at" if status == "COMPLETED" else "completed_at"] is None
    for field in original.keys() - {"status", "completed_at", "cancelled_at"}:
        assert terminal[field] == original[field]
    assert (await patch(case, plan_id, status)).json() == response.json()
    assert (await case.client.get(f"/api/v1/support-action-plans/{plan_id}")).json() == response.json()
    assert_error(await patch(case, plan_id, status, key="different-terminal-key"), 409, "ACTION_PLAN_STATE_CONFLICT")
    other = "CANCELLED" if status == "COMPLETED" else "COMPLETED"
    assert_error(await patch(case, plan_id, other), 409, "IDEMPOTENCY_KEY_CONFLICT")
    assert_error(await patch(case, plan_id, other, key="other-terminal-key"), 409, "ACTION_PLAN_STATE_CONFLICT")
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 4


@pytest.mark.parametrize("value", [False, 1, "true", None])
async def test_confirmation_is_strict(case: ApiCase, value) -> None:
    plan_id = (await active_plan(case))["support_action_plan_id"]
    assert_error(await patch(case, plan_id, confirmed=value), 422, "VALIDATION_FAILED")


@pytest.mark.parametrize(
    "body",
    [
        {"status": "COMPLETED"},
        {"confirmed": True},
        {"status": "ACTIVE", "confirmed": True},
        {"status": "CANCELLED", "confirmed": True, "revision": 1},
    ],
)
async def test_request_rejects_missing_fields_reactivation_and_injected_fields(case: ApiCase, body: dict) -> None:
    plan_id = (await active_plan(case))["support_action_plan_id"]
    result = await case.client.patch(
        f"/api/v1/support-action-plans/{plan_id}", json=body, headers={"Idempotency-Key": "invalid-lifecycle-key"}
    )
    assert_error(result, 422, "VALIDATION_FAILED")


async def test_unknown_and_foreign_plan_share_404_even_for_replay(case: ApiCase) -> None:
    plan_id = (await active_plan(case))["support_action_plan_id"]
    assert (await patch(case, plan_id)).status_code == 200
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    for target in (plan_id, str(uuid4())):
        assert_error(await case.client.get(f"/api/v1/support-action-plans/{target}"), 404, "ACTION_PLAN_NOT_FOUND")
        assert_error(await patch(case, target), 404, "ACTION_PLAN_NOT_FOUND")


@pytest.mark.parametrize("correction", ["checkin", "safety", "barrier", "routine_safety"])
async def test_stale_completion_blocked_history_readable_and_active_cancellation_available(
    case: ApiCase, correction: str
) -> None:
    original = await active_plan(case)
    plan_id = original["support_action_plan_id"]
    if correction == "checkin":
        response = await case.client.put(
            f"/api/v1/medication-occurrences/{case.checkin.occurrence_id}/check-in",
            json={"status": "TAKEN", "expected_revision": 1},
            headers={"Idempotency-Key": "lifecycle-checkin-correction"},
        )
    elif correction in ("safety", "routine_safety"):
        response = await case.safety(
            safety_body(case, symptoms=["SYNTHETIC"] if correction == "safety" else [], expected_revision=1),
            key="lifecycle-safety-correction",
        )
    else:
        response = await case.barrier(
            {"response_status": "DECLINED", "barrier_code": None, "checkin_revision": 1, "expected_revision": 1},
            key="lifecycle-barrier-correction",
        )
    assert response.status_code == 200, response.text
    expected = "ACTION_PLAN_STATE_CONFLICT" if correction in ("checkin", "safety") else "BARRIER_FLOW_STALE"
    assert_error(await patch(case, plan_id), 409, expected)
    history = await case.client.get(f"/api/v1/support-action-plans/{plan_id}")
    assert history.status_code == 200
    assert history.json()["data"]["action_config_snapshot"] == original["action_config_snapshot"]
    if correction in ("barrier", "routine_safety"):
        assert (await patch(case, plan_id, "CANCELLED")).status_code == 200


async def test_snapshot_failure_rolls_back_terminal_state_and_retry_succeeds(
    case: ApiCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_id = (await active_plan(case))["support_action_plan_id"]
    with monkeypatch.context() as context:
        context.setattr("app.services.idempotency.SNAPSHOT_SIZE_CAP_BYTES", 1)
        assert_error(await patch(case, plan_id), 503, "IDEMPOTENCY_RESPONSE_TOO_LARGE")
    stored = await case.session.get(SupportActionPlan, UUID(plan_id))
    assert stored is not None
    await case.session.refresh(stored)
    assert stored.status == "ACTIVE"
    assert stored.completed_at is stored.cancelled_at is None
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 3
    assert (await patch(case, plan_id)).status_code == 200


async def test_auth_and_missing_idempotency_key(case: ApiCase) -> None:
    plan_id = (await active_plan(case))["support_action_plan_id"]
    url = f"/api/v1/support-action-plans/{plan_id}"
    assert_error(
        await case.client.patch(url, json={"status": "COMPLETED", "confirmed": True}), 400, "IDEMPOTENCY_KEY_REQUIRED"
    )
    fastapi_app.dependency_overrides.pop(get_request_user)
    assert (await case.client.get(url)).status_code == 401
    assert (await patch(case, plan_id)).status_code == 401


def test_lifecycle_openapi_contract():
    schema = fastapi_app.openapi()
    routes = schema["paths"]["/api/v1/support-action-plans/{id}"]
    assert routes["get"]["operationId"] == "support-action-plan.get"
    assert routes["patch"]["operationId"] == "support-action-plan.patch"
    assert not any(p["name"] == "Idempotency-Key" for p in routes["get"]["parameters"])
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in routes["patch"]["parameters"])
    request = schema["components"]["schemas"]["PatchSupportActionPlanRequest"]
    assert set(request["required"]) == {"status", "confirmed"}
    assert request["additionalProperties"] is False
    assert request["properties"]["status"]["enum"] == ["COMPLETED", "CANCELLED"]
    assert request["properties"]["confirmed"]["const"] is True


async def test_active_plan_completion_rechecks_blocked_safety_and_cancel_remains_available(case: ApiCase) -> None:
    from app.models.track_c import SafetyAssessment, SafetyDisposition

    plan_id = (await active_plan(case))["support_action_plan_id"]
    safety = await case.session.scalar(select(SafetyAssessment))
    assert safety is not None
    safety.safety_disposition = SafetyDisposition.BLOCKED_ACTION
    await case.session.commit()
    assert_error(await patch(case, plan_id), 409, "SAFETY_FLOW_PRECEDES_SUPPORT")
    assert (await patch(case, plan_id, "CANCELLED")).status_code == 200


async def test_get_and_terminal_confirmation_use_saved_config_without_provider_calls(
    case: ApiCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from app.services.track_c_support import TrackCSupportService

    original = await active_plan(case)
    provider = AsyncMock(side_effect=AssertionError("provider must not run"))
    assets = AsyncMock(side_effect=AssertionError("active assets must not be loaded"))
    monkeypatch.setattr("openai.resources.responses.AsyncResponses.create", provider)
    monkeypatch.setattr(TrackCSupportService, "_load_config", assets)
    plan_id = original["support_action_plan_id"]
    assert (await case.client.get(f"/api/v1/support-action-plans/{plan_id}")).json()["data"] == original
    assert (await patch(case, plan_id)).status_code == 200
    provider.assert_not_called()
    assets.assert_not_called()
