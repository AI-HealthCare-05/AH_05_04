from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.dependencies.security import get_request_user
from app.main import fastapi_app
from app.models.async_jobs import IdempotencyRecord
from app.models.track_c import ActionPlanFollowup, ActionPlanFollowupAudit
from app.services.track_c_support import TrackCSupportService
from app.tests.track_c.test_track_c_api import ApiCase, assert_error, safety_body
from app.tests.track_c.test_track_c_api import case as track_c_case
from app.tests.track_c.test_track_c_plan_lifecycle import active_plan, patch

case = track_c_case


def followup_url(plan_id: str) -> str:
    return f"/api/v1/support-action-plans/{plan_id}/followups"


async def completed_plan(case: ApiCase) -> str:
    plan_id = (await active_plan(case))["support_action_plan_id"]
    result = await patch(case, plan_id)
    assert result.status_code == 200, result.text
    return plan_id


async def submit(case: ApiCase, plan_id: str, response="HELPED", revision=0, key="followup-first-request-key"):
    return await case.client.post(
        followup_url(plan_id),
        json={"response": response, "expected_revision": revision},
        headers={"Idempotency-Key": key},
    )


@pytest.mark.parametrize("answer", ["HELPED", "NOT_HELPED", "NOT_SURE"])
async def test_submit_correct_read_and_replay_history(case: ApiCase, answer: str) -> None:
    plan_id = await completed_plan(case)
    original_plan = (await case.client.get(f"/api/v1/support-action-plans/{plan_id}")).json()
    empty = await case.client.get(followup_url(plan_id))
    assert empty.status_code == 200
    assert empty.json() == {"data": None}
    assert empty.headers["cache-control"] == "no-store"
    assert await case.session.scalar(select(func.count()).select_from(ActionPlanFollowup)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 4

    first = await submit(case, plan_id, answer)
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    data = first.json()["data"]
    assert data["support_action_plan_id"] == plan_id
    assert data["response"] == answer
    assert data["revision"] == 1
    assert data["created_at"] == data["updated_at"]
    assert (await case.client.get(followup_url(plan_id))).json() == first.json()
    assert await case.session.scalar(select(func.count()).select_from(ActionPlanFollowupAudit)) == 0

    corrected = await submit(case, plan_id, "NOT_SURE", 1, "followup-correction-key")
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["data"]["revision"] == 2
    assert corrected.json()["data"]["followup_id"] == data["followup_id"]
    assert corrected.json()["data"]["created_at"] == data["created_at"]
    assert corrected.json()["data"]["updated_at"] >= data["updated_at"]
    audit = await case.session.scalar(select(ActionPlanFollowupAudit))
    assert audit is not None
    assert (audit.from_response, audit.to_response, audit.from_revision, audit.to_revision) == (
        answer,
        "NOT_SURE",
        1,
        2,
    )
    assert audit.changed_by == case.owner.id

    # Old replay reproduces the original acceptance, never the latest correction.
    assert (await submit(case, plan_id, answer)).json() == first.json()
    assert (await submit(case, plan_id, "NOT_SURE", 1, "followup-correction-key")).json() == corrected.json()
    assert (await case.client.get(followup_url(plan_id))).json() == corrected.json()
    assert (await case.client.get(f"/api/v1/support-action-plans/{plan_id}")).json() == original_plan
    assert await case.session.scalar(select(func.count()).select_from(ActionPlanFollowup)) == 1
    assert await case.session.scalar(select(func.count()).select_from(ActionPlanFollowupAudit)) == 1
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 6


async def test_conflicts_do_not_write_and_correct_revision_can_retry(case: ApiCase) -> None:
    plan_id = await completed_plan(case)
    assert_error(await submit(case, plan_id, revision=1), 409, "ACTION_PLAN_FOLLOWUP_REVISION_CONFLICT")
    assert (await submit(case, plan_id)).status_code == 200
    assert_error(await submit(case, plan_id, "NOT_HELPED"), 409, "IDEMPOTENCY_KEY_CONFLICT")
    assert_error(
        await submit(case, plan_id, key="followup-stale-new-key"), 409, "ACTION_PLAN_FOLLOWUP_REVISION_CONFLICT"
    )
    assert_error(
        await submit(case, plan_id, revision=2, key="followup-future-new-key"),
        409,
        "ACTION_PLAN_FOLLOWUP_REVISION_CONFLICT",
    )
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 5
    assert await case.session.scalar(select(func.count()).select_from(ActionPlanFollowupAudit)) == 0
    result = await submit(case, plan_id, "NOT_HELPED", 1, "followup-stale-new-key")
    assert result.status_code == 200
    assert result.json()["data"]["revision"] == 2


@pytest.mark.parametrize("status", ["ACTIVE", "CANCELLED"])
async def test_only_completed_plans_accept_feedback(case: ApiCase, status: str) -> None:
    plan_id = (await active_plan(case))["support_action_plan_id"]
    if status == "CANCELLED":
        assert (await patch(case, plan_id, status)).status_code == 200
    assert_error(await submit(case, plan_id), 409, "ACTION_PLAN_STATE_CONFLICT")
    assert (await case.client.get(followup_url(plan_id))).json() == {"data": None}
    assert await case.session.scalar(select(func.count()).select_from(ActionPlanFollowup)) == 0
    if status == "ACTIVE":
        assert (await patch(case, plan_id)).status_code == 200
        assert (await submit(case, plan_id)).status_code == 200


@pytest.mark.parametrize("correction", ["checkin", "safety", "barrier"])
@pytest.mark.parametrize("already_answered", [False, True])
async def test_historical_feedback_survives_parent_corrections(
    case: ApiCase, correction: str, already_answered: bool
) -> None:
    plan_id = await completed_plan(case)
    if already_answered:
        assert (await submit(case, plan_id)).status_code == 200
    if correction == "checkin":
        result = await case.client.put(
            f"/api/v1/medication-occurrences/{case.checkin.occurrence_id}/check-in",
            json={"status": "TAKEN", "expected_revision": 1},
            headers={"Idempotency-Key": "followup-checkin-correction"},
        )
    elif correction == "safety":
        result = await case.safety(
            safety_body(case, symptoms=["SYNTHETIC"], expected_revision=1), key="followup-new-safety-key"
        )
    else:
        result = await case.barrier(
            {"response_status": "DECLINED", "barrier_code": None, "checkin_revision": 1, "expected_revision": 1},
            key="followup-new-barrier-key",
        )
    assert result.status_code == 200, result.text
    feedback = await submit(case, plan_id, "NOT_HELPED", int(already_answered), "followup-after-correction")
    assert feedback.status_code == 200, feedback.text
    assert feedback.json()["data"]["revision"] == 1 + int(already_answered)
    assert (await case.client.get(followup_url(plan_id))).json() == feedback.json()
    assert (await case.client.get(f"/api/v1/support-action-plans/{plan_id}")).json()["data"]["status"] == "COMPLETED"


@pytest.mark.parametrize("revision", [-1, True, 1.0, "0", None])
async def test_revision_requires_nonnegative_json_integer(case: ApiCase, revision) -> None:
    assert_error(await submit(case, await completed_plan(case), revision=revision), 422, "VALIDATION_FAILED")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"response": "HELPED"},
        {"expected_revision": 0},
        {"response": "LATER", "expected_revision": 0},
        {"response": None, "expected_revision": 0},
        {"response": "HELPED", "expected_revision": 0, "note": "synthetic"},
    ],
)
async def test_request_rejects_missing_fields_defer_and_free_text(case: ApiCase, body: dict) -> None:
    plan_id = await completed_plan(case)
    result = await case.client.post(
        followup_url(plan_id), json=body, headers={"Idempotency-Key": "followup-invalid-body-key"}
    )
    assert_error(result, 422, "VALIDATION_FAILED")


async def test_foreign_and_missing_plan_are_indistinguishable_including_replay(case: ApiCase) -> None:
    plan_id = await completed_plan(case)
    assert (await submit(case, plan_id)).status_code == 200
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    for target in (plan_id, str(uuid4())):
        assert_error(await case.client.get(followup_url(target)), 404, "ACTION_PLAN_NOT_FOUND")
        assert_error(await submit(case, target), 404, "ACTION_PLAN_NOT_FOUND")


@pytest.mark.parametrize("already_answered", [False, True])
async def test_snapshot_failure_rolls_back_response_revision_and_audit(
    case: ApiCase, monkeypatch: pytest.MonkeyPatch, already_answered: bool
) -> None:
    plan_id = await completed_plan(case)
    if already_answered:
        assert (await submit(case, plan_id)).status_code == 200
    before = (await case.client.get(followup_url(plan_id))).json()
    with monkeypatch.context() as context:
        context.setattr("app.services.idempotency.SNAPSHOT_SIZE_CAP_BYTES", 1)
        result = await submit(case, plan_id, "NOT_HELPED", int(already_answered), "followup-rollback-key")
        assert_error(result, 503, "IDEMPOTENCY_RESPONSE_TOO_LARGE")
    assert (await case.client.get(followup_url(plan_id))).json() == before
    assert await case.session.scalar(select(func.count()).select_from(ActionPlanFollowupAudit)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 4 + int(already_answered)
    result = await submit(case, plan_id, "NOT_HELPED", int(already_answered), "followup-rollback-key")
    assert result.status_code == 200
    assert result.json()["data"]["revision"] == 1 + int(already_answered)


async def test_auth_key_validation_and_no_provider_or_config_calls(
    case: ApiCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan_id = await completed_plan(case)
    provider = AsyncMock(side_effect=AssertionError("no provider for historical feedback"))
    assets = AsyncMock(side_effect=AssertionError("no active config for historical feedback"))
    monkeypatch.setattr("openai.resources.responses.AsyncResponses.create", provider)
    monkeypatch.setattr(TrackCSupportService, "_load_config", assets)
    for key, code in [(None, "IDEMPOTENCY_KEY_REQUIRED"), ("short", "IDEMPOTENCY_KEY_INVALID")]:
        result = await case.client.post(
            followup_url(plan_id),
            json={"response": "HELPED", "expected_revision": 0},
            headers={} if key is None else {"Idempotency-Key": key},
        )
        assert_error(result, 400, code)
    assert (await submit(case, plan_id)).status_code == 200
    assert (await case.client.get(followup_url(plan_id))).status_code == 200
    provider.assert_not_called()
    assets.assert_not_called()
    fastapi_app.dependency_overrides.pop(get_request_user)
    assert (await submit(case, plan_id)).status_code == 401
    assert (await case.client.get(followup_url(plan_id))).status_code == 401


def test_followup_openapi_contract() -> None:
    schema = fastapi_app.openapi()
    routes = schema["paths"]["/api/v1/support-action-plans/{id}/followups"]
    assert set(routes) == {"get", "post"}
    assert routes["get"]["operationId"] == "support-action-plan.followup.get"
    assert routes["post"]["operationId"] == "support-action-plan.followup.submit"
    assert not any(p["name"] == "Idempotency-Key" for p in routes["get"]["parameters"])
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in routes["post"]["parameters"])
    request = schema["components"]["schemas"]["SubmitActionPlanFollowupRequest"]
    assert set(request["required"]) == {"response", "expected_revision"}
    assert request["additionalProperties"] is False
    assert request["properties"]["expected_revision"]["minimum"] == 0
    assert schema["components"]["schemas"]["ActionPlanFollowupResponse"]["enum"] == ["HELPED", "NOT_HELPED", "NOT_SURE"]
    for method in ("get", "post"):
        assert "200" in routes[method]["responses"]
        assert "404" in routes[method]["responses"]
