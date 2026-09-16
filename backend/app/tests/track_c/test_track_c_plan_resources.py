from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.dependencies.security import get_request_user
from app.main import fastapi_app
from app.models.async_jobs import IdempotencyRecord
from app.models.track_c import SupportActionPlan
from app.services.track_c_handler_config import load_active_support_copy_catalog
from app.tests.track_c.test_track_c_api import ApiCase, assert_error
from app.tests.track_c.test_track_c_api import case as track_c_case
from app.tests.track_c.test_track_c_support_api import create, offer, plan_body, prepare

case = track_c_case


@pytest.mark.parametrize(
    "barrier_code",
    ["FORGOT", "SCHEDULE_OR_TRAVEL", "INSTRUCTIONS_UNCLEAR", "NEED_DOUBT", "MEDICATION_CONCERN", "ACCESS_OR_COST"],
)
async def test_resources_bind_original_medication_and_versioned_copy_without_writes(
    case: ApiCase, barrier_code: str
) -> None:
    barrier_id = await prepare(case, barrier_code)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    plan = (await create(case, plan_body(barrier_id, support))).json()["data"]
    count = await case.session.scalar(select(func.count()).select_from(IdempotencyRecord))
    response = await case.client.get(f"/api/v1/support-action-plans/{plan['support_action_plan_id']}/resources")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()["data"]
    assert data["support_action_plan_id"] == plan["support_action_plan_id"]
    assert data["barrier_code"] == barrier_code
    assert data["occurrence_id"] == str(case.checkin.occurrence_id)
    assert data["support_copy"] == support["support_copy"]
    medication = await case.client.get(f"/api/v1/medication-occurrences/{data['occurrence_id']}/medication")
    assert medication.status_code == 200, medication.text
    assert data["prescription_version_medication_id"] == medication.json()["data"]["prescription_version_medication_id"]
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == count
    assert (await case.client.get(f"/api/v1/support-action-plans/{plan['support_action_plan_id']}")).json()["data"][
        "status"
    ] == "ACTIVE"


async def test_resources_preserve_historical_copy_and_fail_closed_for_unknown_versions(case: ApiCase) -> None:
    barrier_id = await prepare(case, "SCHEDULE_OR_TRAVEL")
    support = (
        await case.client.get(
            f"/api/v1/barrier-responses/{barrier_id}/supports?travel_situation=MEDICATION_NOT_WITH_ME"
        )
    ).json()["data"]["supports"][0]
    body = {**plan_body(barrier_id, support), "travel_situation": "MEDICATION_NOT_WITH_ME"}
    plan = (await create(case, body)).json()["data"]
    row = await case.session.get(SupportActionPlan, UUID(plan["support_action_plan_id"]))
    assert row is not None
    row.rule_version = "track-c-support-rule-2026-09-15.1"
    row.copy_version = "track-c-support-copy-ko-2026-09-15.1"
    await case.session.commit()
    path = f"/api/v1/support-action-plans/{row.id}/resources"
    response = await case.client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["support_copy"]["title"] == "일정이나 외출 전 준비 사항을 확인해 보세요"
    assert (
        response.json()["data"]["support_copy"]["title"]
        != load_active_support_copy_catalog().supports[row.support_code].title
    )
    row.copy_version = "unapproved-synthetic-copy"
    await case.session.commit()
    assert_error(await case.client.get(path), 503, "SUPPORT_CONFIG_UNAVAILABLE")


async def test_resources_hide_other_owner_and_missing_plan(case: ApiCase) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    plan = (await create(case, plan_body(barrier_id, support))).json()["data"]
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    for plan_id in [plan["support_action_plan_id"], str(uuid4())]:
        assert_error(
            await case.client.get(f"/api/v1/support-action-plans/{plan_id}/resources"), 404, "ACTION_PLAN_NOT_FOUND"
        )
