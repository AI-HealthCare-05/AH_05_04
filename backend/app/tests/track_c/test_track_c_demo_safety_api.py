"""Real HTTP/transaction tests with synthetic accounts and the opt-in demo policy."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core import config
from app.core.config import Env
from app.main import fastapi_app
from app.models.track_c import BarrierResponse, SafetyAssessment, SupportActionPlan
from app.services import track_c_demo_safety as demo
from app.tests.track_c.test_track_c_api import ApiCase, assert_error, case, safety_body  # noqa: F401


@pytest.fixture
async def enabled_demo(case: ApiCase, monkeypatch: pytest.MonkeyPatch) -> ApiCase:  # noqa: F811
    now = datetime.now(UTC)
    monkeypatch.setattr(config, "ENV", Env.LOCAL)
    monkeypatch.setattr(config, "TRACK_C_SAFETY_DEMO_ENABLED", True)
    monkeypatch.setattr(config, "TRACK_C_SAFETY_DEMO_STARTS_AT", now - timedelta(hours=1))
    monkeypatch.setattr(config, "TRACK_C_SAFETY_DEMO_EXPIRES_AT", now + timedelta(days=6))
    monkeypatch.setattr(config, "TRACK_C_SAFETY_DEMO_USER_IDS", frozenset({case.owner.id}))
    return case


@pytest.mark.parametrize(
    ("codes", "level", "disposition"),
    [
        (["SEVERE_BREATHING_DIFFICULTY", "OTHER_SYMPTOM"], "EMERGENCY", "EMERGENCY_ROUTED"),
        (["SUDDEN_OTHER_BODY_SWELLING", "UNSURE_ABOUT_SYMPTOMS"], "URGENT", "URGENT_ROUTED"),
        (["OTHER_SYMPTOM"], "UNKNOWN", "UNKNOWN_RISK"),
        (["SYNTHETIC_UNREGISTERED"], "UNKNOWN", "UNKNOWN_RISK"),
    ],
)
async def test_demo_results_persist_and_block_barrier(
    enabled_demo: ApiCase,
    codes: list[str],
    level: str,
    disposition: str,
) -> None:
    c = enabled_demo
    response = await c.safety(safety_body(c, symptoms=codes))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["response_level"] == level
    assert data["safety_disposition"] == disposition
    assert data["copy_version"] == "track-c-safety-demo-ko-2026-09-16.1"
    assert response.headers["cache-control"] == "no-store"
    row = (await c.session.scalars(select(SafetyAssessment))).one()
    assert row.symptom_codes == codes
    assert row.source_version == data["source_version"]
    assert row.message_code == data["message_code"]
    assert_error(
        await c.barrier(
            {
                "response_status": "ANSWERED",
                "barrier_code": "FORGOT",
                "checkin_revision": 1,
                "expected_revision": 0,
            }
        ),
        409,
        "SAFETY_FLOW_PRECEDES_BARRIER",
    )
    assert await c.session.scalar(select(func.count()).select_from(BarrierResponse)) == 0


@pytest.mark.parametrize("failure", ["expired", "not_started", "not_allowlisted", "nonlocal", "missing_artifact"])
async def test_disabled_demo_mutation_leaves_no_rows(
    enabled_demo: ApiCase,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    failure: str,
) -> None:
    c = enabled_demo
    if failure == "expired":
        monkeypatch.setattr(config, "TRACK_C_SAFETY_DEMO_EXPIRES_AT", datetime.now(UTC) - timedelta(seconds=1))
    elif failure == "not_started":
        monkeypatch.setattr(config, "TRACK_C_SAFETY_DEMO_STARTS_AT", datetime.now(UTC) + timedelta(hours=1))
    elif failure == "not_allowlisted":
        monkeypatch.setattr(config, "TRACK_C_SAFETY_DEMO_USER_IDS", frozenset({uuid4()}))
    elif failure == "nonlocal":
        monkeypatch.setattr(config, "ENV", Env.PRODUCTION)
    else:
        monkeypatch.setattr(demo, "DEMO_ARTIFACT_PATH", tmp_path / "missing.json")
    assert_error(await c.safety(safety_body(c)), 503, "SAFETY_DEMO_UNAVAILABLE")
    assert await c.session.scalar(select(func.count()).select_from(SafetyAssessment)) == 0


async def test_first_snapshot_replays_after_expiry_but_new_mutation_is_blocked(
    enabled_demo: ApiCase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = enabled_demo
    body = safety_body(c, symptoms=["CURRENT_OR_RECENT_SPEECH_CHANGE"])
    initial = await c.safety(body)
    assert initial.status_code == 200
    monkeypatch.setattr(config, "TRACK_C_SAFETY_DEMO_EXPIRES_AT", datetime.now(UTC) - timedelta(seconds=1))
    replay = await c.safety(body)
    assert replay.status_code == 200
    assert replay.json() == initial.json()
    assert_error(
        await c.safety(safety_body(c, expected_revision=1), key="new-after-expiry"), 503, "SAFETY_DEMO_UNAVAILABLE"
    )
    assert await c.session.scalar(select(func.count()).select_from(SafetyAssessment)) == 1


@pytest.mark.parametrize("snapshot_failure", [False, True])
async def test_emergency_correction_cancels_plan_atomically_and_blocks_support(
    enabled_demo: ApiCase,
    monkeypatch: pytest.MonkeyPatch,
    snapshot_failure: bool,
) -> None:
    c = enabled_demo
    assert (await c.safety(safety_body(c))).status_code == 200
    barrier = await c.barrier(
        {
            "response_status": "ANSWERED",
            "barrier_code": "FORGOT",
            "checkin_revision": 1,
            "expected_revision": 0,
        }
    )
    assert barrier.status_code == 200
    barrier_id = barrier.json()["data"]["barrier_response_id"]
    url = f"/api/v1/barrier-responses/{barrier_id}/supports"
    offered = await c.client.get(url)
    assert offered.status_code == 200
    offer = offered.json()["data"]["supports"][0]
    created = await c.client.post(
        "/api/v1/support-action-plans",
        headers={"Idempotency-Key": "demo-plan-create-key"},
        json={
            "barrier_response_id": barrier_id,
            "confirmed": True,
            "support_code": offer["support_code"],
            "rule_version": offer["rule_version"],
            "copy_version": offer["copy_version"],
        },
    )
    assert created.status_code == 200, created.text
    if snapshot_failure:
        monkeypatch.setattr("app.services.idempotency.SNAPSHOT_SIZE_CAP_BYTES", 1)
    changed = await c.safety(
        safety_body(c, symptoms=["SUDDEN_AIRWAY_SWELLING"], expected_revision=1), key="emergency-change"
    )
    if snapshot_failure:
        assert_error(changed, 503, "IDEMPOTENCY_RESPONSE_TOO_LARGE")
    else:
        assert changed.status_code == 200
        assert_error(await c.client.get(url), 409, "SAFETY_FLOW_PRECEDES_SUPPORT")
    plan = (await c.session.scalars(select(SupportActionPlan))).one()
    assert plan.status == ("ACTIVE" if snapshot_failure else "CANCELLED")
    assert (plan.cancelled_at is None) is snapshot_failure
    assert await c.session.scalar(select(func.count()).select_from(SafetyAssessment)) == (1 if snapshot_failure else 2)
    assert await c.session.scalar(select(func.count()).select_from(BarrierResponse)) == 1


async def test_unknown_checkin_remains_404_before_demo_policy(enabled_demo: ApiCase) -> None:
    c = enabled_demo
    body = safety_body(c)
    body["medication_checkin_id"] = str(uuid4())
    assert_error(await c.safety(body), 404, "MEDICATION_CHECKIN_NOT_FOUND")


def test_openapi_describes_demo_unavailability_without_changing_dto() -> None:
    schema = fastapi_app.openapi()
    response = schema["paths"]["/api/v1/safety-assessments"]["post"]["responses"]["503"]
    assert "SAFETY_DEMO_UNAVAILABLE" in response["description"]
    assert set(schema["components"]["schemas"]["SafetyAssessmentData"]["properties"]) == {
        "assessment_id",
        "medication_checkin_id",
        "checkin_revision",
        "response_level",
        "safety_disposition",
        "message_code",
        "copy_version",
        "source_version",
        "revision",
    }
