from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.dependencies.security import get_request_user
from app.main import fastapi_app
from app.models.async_jobs import IdempotencyRecord
from app.models.medication_schedules import MedicationCheckinStatus
from app.models.track_c import (
    BarrierCode,
    BarrierResponse,
    BarrierResponseStatus,
    SafetyAssessment,
    SafetyDisposition,
    SafetyResponseLevel,
    SupportActionPlan,
    SupportCode,
)
from app.repositories.track_c_storage_repository import TrackCStorageRepository
from app.services import track_c_support
from app.services.track_c_handler_config import HandlerConfigError, load_active_handler_config
from app.services.track_c_support import eligible_supports
from app.tests.track_c.test_track_c_api import ApiCase, assert_error, safety_body
from app.tests.track_c.test_track_c_api import case as track_c_case

case = track_c_case


async def prepare(case: ApiCase, code: str | None = "FORGOT") -> str:
    response = await case.safety(safety_body(case))
    assert response.status_code == 200, response.text
    response = await case.barrier(
        {
            "response_status": "ANSWERED" if code else "DECLINED",
            "barrier_code": code,
            "checkin_revision": 1,
            "expected_revision": 0,
        }
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]["barrier_response_id"]


def plan_body(barrier_id: str, support: dict) -> dict:
    return {
        "barrier_response_id": barrier_id,
        "support_code": support["support_code"],
        "rule_version": support["rule_version"],
        "copy_version": support["copy_version"],
        "confirmed": True,
    }


async def create(case: ApiCase, body: dict, key: str = "support-plan-create-key"):
    return await case.client.post("/api/v1/support-action-plans", json=body, headers={"Idempotency-Key": key})


async def offer(case: ApiCase, barrier_id: str):
    return await case.client.get(f"/api/v1/barrier-responses/{barrier_id}/supports")


@pytest.mark.parametrize(
    "barrier_code,support_code",
    [
        ("FORGOT", "REMINDER_SETUP"),
        ("SCHEDULE_OR_TRAVEL", "REMINDER_SETUP"),
        ("INSTRUCTIONS_UNCLEAR", "INSTRUCTION_REVIEW"),
        ("NEED_DOUBT", "PURPOSE_REVIEW"),
        ("MEDICATION_CONCERN", "MEDICATION_CONCERN_GUIDANCE"),
        ("ACCESS_OR_COST", "ACCESS_SUPPORT"),
    ],
)
async def test_single_offer_and_confirmed_plan_snapshot_without_provider(
    case: ApiCase, monkeypatch: pytest.MonkeyPatch, barrier_code: str, support_code: str
) -> None:
    generator = AsyncMock(side_effect=AssertionError("external generator must not be called"))
    retriever = AsyncMock(side_effect=AssertionError("external embedding must not be called"))
    monkeypatch.setattr("openai.resources.responses.AsyncResponses.create", generator)
    monkeypatch.setattr("openai.resources.embeddings.AsyncEmbeddings.create", retriever)
    barrier_id = await prepare(case, barrier_code)
    offered = await offer(case, barrier_id)
    assert offered.status_code == 200, offered.text
    assert offered.headers["cache-control"] == "no-store"
    data = offered.json()["data"]
    assert data["reason_code"] is None
    assert len(data["supports"]) == 1
    support = data["supports"][0]
    assert support["support_code"] == support_code
    assert support["support_copy"]["confirmation_prompt"]
    assert await case.session.scalar(select(func.count()).select_from(SupportActionPlan)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 2
    body = plan_body(barrier_id, support)
    response = await create(case, body)
    assert response.status_code == 200, response.text
    plan = response.json()["data"]
    assert plan["status"] == "ACTIVE"
    assert plan["completed_at"] is plan["cancelled_at"] is None
    assert plan["action_config_snapshot"] == support["action_config"]
    stored = await case.session.get(SupportActionPlan, UUID(plan["support_action_plan_id"]))
    assert stored is not None
    assert stored.action_config_snapshot == support["action_config"]
    assert stored.rule_version == support["rule_version"]
    assert stored.copy_version == support["copy_version"]
    if support_code == "REMINDER_SETUP":
        parent = await TrackCStorageRepository(case.session).get_barrier_medication_owned(
            barrier_id=UUID(barrier_id), user_id=case.owner.id
        )
        assert parent is not None
        assert support["action_config"]["parameters"]["prescription_version_medication_id"] == str(parent[1])
    assert (await create(case, body)).json() == response.json()
    assert_error(await create(case, body, "different-confirmation-key"), 409, "ACTION_PLAN_ALREADY_ACTIVE")
    assert await case.session.scalar(select(func.count()).select_from(SupportActionPlan)) == 1
    generator.assert_not_called()
    retriever.assert_not_called()


async def test_declined_returns_zero_without_fabricated_support(case: ApiCase) -> None:
    barrier_id = await prepare(case, None)
    response = await offer(case, barrier_id)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["supports"] == []
    assert response.json()["data"]["reason_code"] == "NO_ELIGIBLE_SUPPORT"
    config = load_active_handler_config()
    body = {
        "barrier_response_id": barrier_id,
        "support_code": "REMINDER_SETUP",
        "rule_version": config.rule_version,
        "copy_version": config.supports[SupportCode.REMINDER_SETUP].copy_version,
        "confirmed": True,
    }
    assert_error(await create(case, body), 409, "SUPPORT_NOT_OFFERED")
    assert await case.session.scalar(select(func.count()).select_from(SupportActionPlan)) == 0


def test_order_is_stable_including_code_tiebreak_and_empty_candidates() -> None:
    config = load_active_handler_config()
    barrier = BarrierResponse(response_status=BarrierResponseStatus.ANSWERED, barrier_code=BarrierCode.FORGOT)
    reversed_config = replace(config, supports=dict(reversed(list(config.supports.items()))))
    assert eligible_supports(config, barrier) == eligible_supports(reversed_config, barrier)
    assert eligible_supports(config, barrier)[0].support_code == SupportCode.REMINDER_SETUP
    tied_config = replace(config, supports={key: replace(rule, priority=10) for key, rule in config.supports.items()})
    assert eligible_supports(tied_config, barrier)[0].support_code == SupportCode.REMINDER_SETUP
    assert eligible_supports(replace(config, supports={}), barrier) == []


@pytest.mark.parametrize("value", [False, 1, "true", None])
async def test_confirmation_is_required_and_strict(case: ApiCase, value: object) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    body = {**plan_body(barrier_id, support), "confirmed": value}
    assert_error(await create(case, body), 422, "VALIDATION_FAILED")
    body.pop("confirmed")
    assert_error(await create(case, body), 422, "VALIDATION_FAILED")
    assert await case.session.scalar(select(func.count()).select_from(SupportActionPlan)) == 0


@pytest.mark.parametrize("field", ["action_config", "rationale_code", "priority", "prescription_version_medication_id"])
async def test_client_cannot_overwrite_server_snapshot(case: ApiCase, field: str) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    assert_error(await create(case, {**plan_body(barrier_id, support), field: "SYNTHETIC"}), 422, "VALIDATION_FAILED")


async def test_second_eligible_support_is_not_offered_and_versions_must_match(case: ApiCase) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    body = plan_body(barrier_id, support)
    assert_error(await create(case, {**body, "support_code": "ROUTINE_OR_TRAVEL_PLAN"}), 409, "SUPPORT_NOT_OFFERED")
    for field in ("rule_version", "copy_version"):
        assert_error(await create(case, {**body, field: "synthetic-old-version"}), 409, "SUPPORT_VERSION_CONFLICT")
    assert (await create(case, body)).status_code == 200
    assert_error(
        await create(case, {**body, "rule_version": "synthetic-other-version"}), 409, "IDEMPOTENCY_KEY_CONFLICT"
    )


@pytest.mark.parametrize("change", ["checkin_status", "checkin_revision", "safety", "barrier"])
async def test_old_offer_cannot_create_after_flow_changes(case: ApiCase, change: str) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    if change.startswith("checkin"):
        if change == "checkin_status":
            case.checkin.status = MedicationCheckinStatus.UNCONFIRMED
        else:
            case.checkin.revision = 2
        await case.session.commit()
        code = "CHECKIN_FLOW_STALE"
    elif change == "safety":
        assert (
            await case.safety(safety_body(case, expected_revision=1), key="new-safety-confirmation-key")
        ).status_code == 200
        code = "BARRIER_FLOW_STALE"
    else:
        assert (
            await case.barrier(
                {"response_status": "DECLINED", "checkin_revision": 1, "expected_revision": 1},
                key="new-barrier-confirmation-key",
            )
        ).status_code == 200
        code = "BARRIER_FLOW_STALE"
    assert_error(await offer(case, barrier_id), 409, code)
    assert_error(await create(case, plan_body(barrier_id, support)), 409, code)
    assert await case.session.scalar(select(func.count()).select_from(SupportActionPlan)) == 0


@pytest.mark.parametrize(
    "level,disposition",
    [
        (SafetyResponseLevel.URGENT, SafetyDisposition.URGENT_ROUTED),
        (SafetyResponseLevel.EMERGENCY, SafetyDisposition.EMERGENCY_ROUTED),
        (SafetyResponseLevel.UNKNOWN, SafetyDisposition.UNKNOWN_RISK),
        (SafetyResponseLevel.ROUTINE, SafetyDisposition.BLOCKED_ACTION),
    ],
)
async def test_non_normal_safety_blocks_offer_and_plan(case: ApiCase, level, disposition) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    safety = await case.session.scalar(select(SafetyAssessment))
    assert safety is not None
    safety.response_level = level
    safety.safety_disposition = disposition
    await case.session.commit()
    assert_error(await offer(case, barrier_id), 409, "SAFETY_FLOW_PRECEDES_SUPPORT")
    assert_error(await create(case, plan_body(barrier_id, support)), 409, "SAFETY_FLOW_PRECEDES_SUPPORT")


async def test_replay_preserves_original_after_safety_cancels_plan(case: ApiCase) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    body = plan_body(barrier_id, support)
    first = await create(case, body)
    assert first.status_code == 200
    assert (
        await case.safety(
            safety_body(case, symptoms=["SYNTHETIC"], expected_revision=1), key="new-risk-confirmation-key"
        )
    ).status_code == 200
    assert (await create(case, body)).json() == first.json()
    stored = await case.session.get(SupportActionPlan, UUID(first.json()["data"]["support_action_plan_id"]))
    assert stored is not None
    await case.session.refresh(stored)
    assert stored.status == "CANCELLED"
    assert stored.action_config_snapshot == support["action_config"]
    assert_error(await create(case, body, "new-unsafe-plan-key"), 409, "SAFETY_FLOW_PRECEDES_SUPPORT")


@pytest.mark.parametrize("status", ["TAKEN", "NOT_TAKEN"])
async def test_actual_checkin_correction_cancels_created_plan_and_blocks_old_offer(case: ApiCase, status: str) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    body = plan_body(barrier_id, support)
    first = await create(case, body)
    assert first.status_code == 200, first.text
    response = await case.client.put(
        f"/api/v1/medication-occurrences/{case.checkin.occurrence_id}/check-in",
        json={"status": status, "expected_revision": 1},
        headers={"Idempotency-Key": "checkin-after-support-confirmation"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["revision"] == 2
    stored = await case.session.get(SupportActionPlan, UUID(first.json()["data"]["support_action_plan_id"]))
    assert stored is not None
    await case.session.refresh(stored)
    assert stored.status == "CANCELLED"
    assert stored.cancelled_at is not None
    assert stored.action_config_snapshot == support["action_config"]
    assert (await create(case, body)).json() == first.json()
    assert_error(await create(case, body, "new-plan-after-checkin-correction"), 409, "CHECKIN_FLOW_STALE")
    assert_error(await offer(case, barrier_id), 409, "CHECKIN_FLOW_STALE")


async def test_ownership_checked_even_on_replay_and_unknown_id_same_error(case: ApiCase) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    body = plan_body(barrier_id, support)
    assert (await create(case, body)).status_code == 200
    fastapi_app.dependency_overrides[get_request_user] = lambda: SimpleNamespace(id=uuid4())
    assert_error(await offer(case, barrier_id), 404, "BARRIER_RESPONSE_NOT_FOUND")
    assert_error(await create(case, body), 404, "BARRIER_RESPONSE_NOT_FOUND")
    missing_id = str(uuid4())
    assert_error(await offer(case, missing_id), 404, "BARRIER_RESPONSE_NOT_FOUND")
    assert_error(await create(case, {**body, "barrier_response_id": missing_id}), 404, "BARRIER_RESPONSE_NOT_FOUND")


async def test_snapshot_cap_failure_rolls_back_plan_and_can_retry(
    case: ApiCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    body = plan_body(barrier_id, support)
    with monkeypatch.context() as patch:
        patch.setattr("app.services.idempotency.SNAPSHOT_SIZE_CAP_BYTES", 1)
        assert_error(await create(case, body), 503, "IDEMPOTENCY_RESPONSE_TOO_LARGE")
    assert await case.session.scalar(select(func.count()).select_from(SupportActionPlan)) == 0
    assert await case.session.scalar(select(func.count()).select_from(IdempotencyRecord)) == 2
    assert (await create(case, body)).status_code == 200


async def test_broken_config_is_not_empty_offer_and_replay_needs_no_active_config(
    case: ApiCase, monkeypatch: pytest.MonkeyPatch
) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    body = plan_body(barrier_id, support)
    first = await create(case, body)

    def broken_config():
        raise HandlerConfigError("SYNTHETIC_SECRET_MUST_NOT_LEAK")

    monkeypatch.setattr(track_c_support, "load_active_handler_config", broken_config)
    response = await offer(case, barrier_id)
    assert_error(response, 503, "SUPPORT_CONFIG_UNAVAILABLE")
    assert "SYNTHETIC_SECRET" not in response.text
    assert (await create(case, body)).json() == first.json()
    assert_error(await create(case, body, "broken-config-new-key"), 503, "SUPPORT_CONFIG_UNAVAILABLE")


async def test_authentication_and_idempotency_header(case: ApiCase) -> None:
    barrier_id = await prepare(case)
    support = (await offer(case, barrier_id)).json()["data"]["supports"][0]
    body = plan_body(barrier_id, support)
    assert_error(await case.client.post("/api/v1/support-action-plans", json=body), 400, "IDEMPOTENCY_KEY_REQUIRED")
    fastapi_app.dependency_overrides.pop(get_request_user)
    assert (await offer(case, barrier_id)).status_code == 401
    assert (await create(case, body)).status_code == 401


def test_openapi_contains_only_scoped_routes_and_strict_confirmation() -> None:
    schema = fastapi_app.openapi()
    path = schema["paths"]["/api/v1/barrier-responses/{id}/supports"]["get"]
    assert path["operationId"] == "barrier-response.supports"
    assert not any(p["name"] == "Idempotency-Key" for p in path["parameters"])
    path = schema["paths"]["/api/v1/support-action-plans"]["post"]
    assert path["operationId"] == "support-action-plan.create"
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in path["parameters"])
    request = schema["components"]["schemas"]["CreateSupportActionPlanRequest"]
    assert request["additionalProperties"] is False
    assert set(request["required"]) == {
        "barrier_response_id",
        "support_code",
        "rule_version",
        "copy_version",
        "confirmed",
    }
    assert request["properties"]["confirmed"]["const"] is True
    assert schema["components"]["schemas"]["SupportOfferData"]["properties"]["supports"]["maxItems"] == 1
    assert "/api/v1/support-action-plans/{id}" not in schema["paths"]
    assert "/api/v1/support-action-plans/{id}/followups" not in schema["paths"]
