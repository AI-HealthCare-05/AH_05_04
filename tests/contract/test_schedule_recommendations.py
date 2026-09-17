"""Synthetic arithmetic and OpenAPI contract regression for PD-670; no Provider calls."""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core import config
from app.core.config import Env
from app.core.errors import ApiError
from app.dtos.medication_schedules import PutMedicationScheduleRequest
from app.dtos.schedule_recommendations import RecommendationInput
from app.main import fastapi_app
from app.models.prescriptions import PrescriptionVersionMedication
from app.services.schedule_recommendations import (
    RULE_VERSION,
    recommend_times,
    require_local_recommendations,
    validate_recommendation,
)


def medication(timing: str | None = "저녁 식후 30분", frequency: int | None = 1) -> PrescriptionVersionMedication:
    return PrescriptionVersionMedication(
        id=uuid4(),
        prescription_version_id=uuid4(),
        timing_text=timing,
        medication_name="합성검증약",
        frequency_per_day=frequency,
        display_order=0,
        medication_count=1,
    )


def inputs(**anchors: str) -> RecommendationInput:
    return RecommendationInput.model_validate({"meal_end_times": anchors, "same_times_every_day": True})


def test_explicit_instruction_only_and_multiple_meals() -> None:
    result = recommend_times(medication(), inputs(DINNER="19:30"))
    assert result.local_times == ["20:00"]
    assert result.reason == "EXPLICIT_AFTER_MEAL"
    assert result.rule_version == RULE_VERSION
    result = recommend_times(
        medication("아침, 점심·저녁 식후 30분", 3), inputs(BREAKFAST="07:30", LUNCH="12:00", DINNER="18:30")
    )
    assert result.local_times == ["08:00", "12:30", "19:00"]


@pytest.mark.parametrize(
    "timing",
    [
        None,
        "",
        "저녁 식후",
        "식후 30분",
        "저녁 식후 0분",
        "저녁 식후 030분",
        "저녁 식후 30~60분",
        "저녁 식후 30분 필요시",
        "격일 저녁 식후 30분",
        "저녁 식후 30분, 취침 전",
        "저녁,저녁 식후 30분",
        "저녁 식후 30분 이상",
        "저녁 식후 30분 이하",
        "저녁 식후 30분 또는 식전",
        "저녁 식후 30분 복용하지 마세요",
    ],
)
def test_ambiguous_qualified_or_missing_instruction_has_no_candidate(timing: str | None) -> None:
    result = recommend_times(medication(timing), inputs(DINNER="19:30"))
    assert result.local_times == []
    assert result.reason == "UNSUPPORTED_INSTRUCTION"


@pytest.mark.parametrize(
    ("timing", "frequency", "anchors", "reason"),
    [
        ("저녁 식후 30분", None, {"DINNER": "19:30"}, "FREQUENCY_MISMATCH"),
        ("저녁 식후 30분", 2, {"DINNER": "19:30"}, "FREQUENCY_MISMATCH"),
        ("저녁 식후 30분", 1, {}, "MISSING_MEAL_END"),
        ("저녁 식후 30분", 1, {"DINNER": "23:30"}, "DAY_BOUNDARY"),
        ("저녁 식후 30분", 1, {"DINNER": "23:50"}, "DAY_BOUNDARY"),
        ("아침,점심 식후 30분", 2, {"BREAKFAST": "12:00", "LUNCH": "12:00"}, "DUPLICATE_TIME"),
    ],
)
def test_missing_conflicting_or_cross_day_inputs_fail_closed(
    timing: str, frequency: int | None, anchors: dict, reason: str
) -> None:
    result = recommend_times(medication(timing, frequency), inputs(**anchors))
    assert result.reason == reason
    assert result.local_times == []


@pytest.mark.parametrize(
    "body",
    [
        {"meal_end_times": {"DINNER": "24:00"}, "same_times_every_day": True},
        {"meal_end_times": {"DINNER": "19:3"}, "same_times_every_day": True},
        {"meal_end_times": {"DINNER": "19:30"}, "same_times_every_day": False},
        {"meal_end_times": {}, "same_times_every_day": 1},
        {"meal_end_times": {}, "same_times_every_day": "true"},
        {"meal_end_times": {}, "same_times_every_day": True, "timing_text": "저녁 식후 30분"},
        {"meal_end_times": {"OTHER": "19:30"}, "same_times_every_day": True},
    ],
)
def test_untrusted_fields_invalid_time_and_weekday_variation_rejected(body: dict) -> None:
    with pytest.raises(ValidationError):
        RecommendationInput.model_validate(body)


def test_save_recomputes_and_rejects_edits_rule_changes_and_existing_schedules() -> None:
    med = medication()
    base = dict(
        start_local_date=date(2026, 9, 17),
        end_mode="OPEN_ENDED",
        local_times=["20:00"],
        expected_revision=0,
        recommendation_context={
            "meal_end_times": {"DINNER": "19:30"},
            "same_times_every_day": True,
            "rule_version": RULE_VERSION,
        },
    )
    validate_recommendation(med, PutMedicationScheduleRequest.model_validate(base))
    for delta in [
        {"local_times": ["21:00"]},
        {"expected_revision": 1},
        {"recommendation_context": {**base["recommendation_context"], "rule_version": "stale"}},
        {"recommendation_context": {**base["recommendation_context"], "meal_end_times": {"DINNER": "19:45"}}},
    ]:
        with pytest.raises(ApiError) as exc:
            validate_recommendation(med, PutMedicationScheduleRequest.model_validate({**base, **delta}))
        assert exc.value.code == "SCHEDULE_RECOMMENDATION_CONFLICT"


@pytest.mark.parametrize("environment", [env for env in Env if env is not Env.LOCAL])
def test_no_nonlocal_publication(monkeypatch: pytest.MonkeyPatch, environment: Env) -> None:
    monkeypatch.setattr(config, "ENV", environment)
    with pytest.raises(ApiError) as exc:
        require_local_recommendations()
    assert exc.value.status_code == 404


def test_openapi_request_and_response_are_explicit() -> None:
    schema = fastapi_app.openapi()
    endpoint = schema["paths"][
        "/api/v1/prescription-version-medications/{prescription_version_medication_id}/schedule-recommendation"
    ]["post"]
    assert endpoint["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("RecommendationInput")
    assert endpoint["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "RecommendationResponse"
    )
    request = schema["components"]["schemas"]["RecommendationInput"]
    assert request["additionalProperties"] is False
    assert set(request["required"]) == {"meal_end_times", "same_times_every_day"}
    assert "recommendation_context" not in schema["components"]["schemas"]["PutMedicationScheduleRequest"]["required"]
    assert "recommendation_context" not in schema["components"]["schemas"]["MedicationScheduleData"]["properties"]
