"""Synthetic integration coverage for explicit schedule/travel support selection."""

from typing import get_args
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.dtos.track_c_support import TravelSituation
from app.main import fastapi_app
from app.models.track_c import SupportActionPlan
from app.services.track_c_support import TRAVEL_SUPPORT_CODES
from app.tests.track_c.test_track_c_api import ApiCase, assert_error, safety_body
from app.tests.track_c.test_track_c_api import case as track_c_case
from app.tests.track_c.test_track_c_support_api import create, plan_body, prepare

case = track_c_case


async def offers(case: ApiCase, barrier_id: str, situation: str):
    return await case.client.get(
        f"/api/v1/barrier-responses/{barrier_id}/supports", params={"travel_situation": situation}
    )


@pytest.mark.parametrize(
    "situation,code",
    [("SCHEDULE_CHANGED", "REMINDER_SETUP"), ("MEDICATION_NOT_WITH_ME", "ROUTINE_OR_TRAVEL_PLAN")],
)
async def test_travel_choice_creation_replay_and_explicit_completion(case: ApiCase, situation: str, code: str) -> None:
    barrier_id = await prepare(case, "SCHEDULE_OR_TRAVEL")
    response = await offers(case, barrier_id, situation)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    (support,) = response.json()["data"]["supports"]
    assert support["support_code"] == code
    assert await case.session.scalar(select(func.count()).select_from(SupportActionPlan)) == 0
    body = {**plan_body(barrier_id, support), "travel_situation": situation}
    response = await create(case, body)
    assert response.status_code == 200, response.text
    plan = response.json()["data"]
    assert plan["status"] == "ACTIVE"
    assert plan["completed_at"] is None
    assert plan["action_config_snapshot"] == support["action_config"]
    assert (await create(case, body)).json() == response.json()
    other = "SCHEDULE_CHANGED" if situation == "MEDICATION_NOT_WITH_ME" else "MEDICATION_NOT_WITH_ME"
    assert_error(await create(case, {**body, "travel_situation": other}), 409, "IDEMPOTENCY_KEY_CONFLICT")
    path = f"/api/v1/support-action-plans/{plan['support_action_plan_id']}"
    assert (await case.client.get(path)).json()["data"]["status"] == "ACTIVE"
    response = await case.client.patch(
        path, json={"status": "COMPLETED", "confirmed": True}, headers={"Idempotency-Key": "travel-completion-key"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["status"] == "COMPLETED"


@pytest.mark.parametrize(
    "barrier_code", ["FORGOT", "INSTRUCTIONS_UNCLEAR", "NEED_DOUBT", "MEDICATION_CONCERN", "ACCESS_OR_COST", None]
)
async def test_travel_choice_rejected_for_other_barriers(case: ApiCase, barrier_code: str | None) -> None:
    barrier_id = await prepare(case, barrier_code)
    assert_error(await offers(case, barrier_id, "MEDICATION_NOT_WITH_ME"), 422, "VALIDATION_FAILED")
    legacy = (await case.client.get(f"/api/v1/barrier-responses/{barrier_id}/supports")).json()["data"]["supports"]
    if legacy:
        assert_error(
            await create(case, {**plan_body(barrier_id, legacy[0]), "travel_situation": "MEDICATION_NOT_WITH_ME"}),
            422,
            "VALIDATION_FAILED",
        )
    assert await case.session.scalar(select(func.count()).select_from(SupportActionPlan)) == 0


async def test_cannot_adopt_different_support_or_omit_packing_choice(case: ApiCase) -> None:
    barrier_id = await prepare(case, "SCHEDULE_OR_TRAVEL")
    support = (await offers(case, barrier_id, "MEDICATION_NOT_WITH_ME")).json()["data"]["supports"][0]
    body = plan_body(barrier_id, support)
    assert_error(await create(case, body), 409, "SUPPORT_NOT_OFFERED")
    assert_error(await create(case, {**body, "travel_situation": "SCHEDULE_CHANGED"}), 409, "SUPPORT_NOT_OFFERED")
    assert_error(await create(case, {**body, "travel_situation": "invented"}), 422, "VALIDATION_FAILED")
    assert_error(await offers(case, barrier_id, "invented"), 422, "VALIDATION_FAILED")


async def test_travel_choice_cannot_bypass_ownership_or_stale_safety(case: ApiCase) -> None:
    assert_error(await offers(case, str(uuid4()), "MEDICATION_NOT_WITH_ME"), 404, "BARRIER_RESPONSE_NOT_FOUND")
    barrier_id = await prepare(case, "SCHEDULE_OR_TRAVEL")
    support = (await offers(case, barrier_id, "MEDICATION_NOT_WITH_ME")).json()["data"]["supports"][0]
    # A new Safety revision invalidates the old Barrier before selection/creation.
    assert (
        await case.safety(safety_body(case, expected_revision=1), key="travel-safety-correction")
    ).status_code == 200
    assert_error(await offers(case, barrier_id, "MEDICATION_NOT_WITH_ME"), 409, "BARRIER_FLOW_STALE")
    assert_error(
        await create(case, {**plan_body(barrier_id, support), "travel_situation": "MEDICATION_NOT_WITH_ME"}),
        409,
        "BARRIER_FLOW_STALE",
    )


def test_openapi_documents_optional_choice_for_get_and_create_only() -> None:
    schema = fastapi_app.openapi()
    params = schema["paths"]["/api/v1/barrier-responses/{id}/supports"]["get"]["parameters"]
    choice = next(item for item in params if item["name"] == "travel_situation")
    assert choice["in"] == "query" and not choice["required"]
    dto = schema["components"]["schemas"]["CreateSupportActionPlanRequest"]
    assert "travel_situation" in dto["properties"]
    assert "travel_situation" not in dto["required"]
    assert "travel_situation" not in schema["components"]["schemas"]["PatchSupportActionPlanRequest"]["properties"]


async def test_omitted_and_null_choice_keep_legacy_idempotency_fingerprint(case: ApiCase) -> None:
    barrier_id = await prepare(case, "SCHEDULE_OR_TRAVEL")
    response = await case.client.get(f"/api/v1/barrier-responses/{barrier_id}/supports")
    support = response.json()["data"]["supports"][0]
    assert support["support_code"] == "REMINDER_SETUP"
    body = plan_body(barrier_id, support)
    created = await create(case, body)
    assert created.status_code == 200, created.text
    replayed = await create(case, {**body, "travel_situation": None})
    assert replayed.status_code == 200, replayed.text
    assert replayed.json() == created.json()


def test_travel_support_mapping_covers_every_situation() -> None:
    assert set(TRAVEL_SUPPORT_CODES) == set(get_args(TravelSituation))
