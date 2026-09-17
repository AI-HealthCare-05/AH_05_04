"""Local-only arithmetic on a narrowly supported confirmed instruction; no medical inference."""

import re

from app.core import config
from app.core.config import Env
from app.core.errors import ApiError
from app.dtos.medication_schedules import PutMedicationScheduleRequest
from app.dtos.schedule_recommendations import Meal, RecommendationData, RecommendationInput
from app.models.prescriptions import PrescriptionVersionMedication

RULE_VERSION = "explicit-after-meal-v1"
_MEALS: dict[str, Meal] = {"아침": "BREAKFAST", "점심": "LUNCH", "저녁": "DINNER"}
# Full match deliberately rejects qualifiers, ranges, PRN, alternating days and extra instructions.
_INSTRUCTION = re.compile(r"(아침|점심|저녁)((?:\s*[,·]\s*(?:아침|점심|저녁))*)\s+식후\s+([1-9][0-9]{0,2})분")


def require_local_recommendations() -> None:
    if config.ENV is not Env.LOCAL:
        raise ApiError(status_code=404, code="NOT_FOUND", message="대상을 찾을 수 없습니다.")


def recommend_times(medication: PrescriptionVersionMedication, request: RecommendationInput) -> RecommendationData:
    data = RecommendationData(
        prescription_version_medication_id=medication.id,
        prescription_version_id=medication.prescription_version_id,
        rule_version=RULE_VERSION,
        timing_text=medication.timing_text,
        local_times=[],
        reason="UNSUPPORTED_INSTRUCTION",
    )
    match = _INSTRUCTION.fullmatch((medication.timing_text or "").strip())
    if match is None:
        return data
    meals = re.split(r"\s*[,·]\s*", match[1] + match[2])
    if len(meals) != len(set(meals)):
        return data
    if medication.frequency_per_day != len(meals):
        data.reason = "FREQUENCY_MISMATCH"
        return data
    times = []
    for meal in meals:
        anchor = request.meal_end_times.get(_MEALS[meal])
        if anchor is None:
            data.reason = "MISSING_MEAL_END"
            return data
        hour, minute = map(int, anchor.split(":"))
        total = hour * 60 + minute + int(match[3])
        if total >= 24 * 60:
            data.reason = "DAY_BOUNDARY"
            return data
        times.append(f"{total // 60:02d}:{total % 60:02d}")
    if len(times) != len(set(times)):
        data.reason = "DUPLICATE_TIME"
        return data
    data.local_times = sorted(times)
    data.reason = "EXPLICIT_AFTER_MEAL"
    return data


def validate_recommendation(medication: PrescriptionVersionMedication, request: PutMedicationScheduleRequest) -> None:
    context = request.recommendation_context
    if context is None:
        return
    candidate = recommend_times(medication, context)
    if (
        context.rule_version != RULE_VERSION
        or not candidate.local_times
        or request.local_times != candidate.local_times
        or request.expected_revision != 0
    ):
        raise ApiError(
            status_code=409,
            code="SCHEDULE_RECOMMENDATION_CONFLICT",
            message="식사 시각과 처방을 확인한 뒤 후보를 다시 계산해 주세요.",
        )
