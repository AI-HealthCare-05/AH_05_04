"""Deterministic Track C subreason and consultation-question policy."""

from types import MappingProxyType
from typing import TYPE_CHECKING

from app.models.track_c import BarrierCode, SupportCode

if TYPE_CHECKING:
    from app.services.track_c_handler_config import SupportCopyCatalog

SUBREASONS = MappingProxyType(
    {
        BarrierCode.FORGOT: ("MISSED_ALERT", "POSTPONED", "MEDICATION_CONFUSION"),
        BarrierCode.SCHEDULE_OR_TRAVEL: ("SCHEDULE_CHANGED", "MEDICATION_NOT_WITH_ME", "PREPARATION_DIFFICULT"),
        BarrierCode.INSTRUCTIONS_UNCLEAR: (
            "DOSE_AMOUNT_UNCLEAR",
            "TIMING_OR_FOOD_UNCLEAR",
            "MEDICATION_IDENTITY_UNCLEAR",
            "LANGUAGE_TOO_COMPLEX",
        ),
        BarrierCode.NEED_DOUBT: ("NO_SYMPTOMS", "NO_NOTICEABLE_EFFECT", "VALUES_IMPROVED", "NEED_UNCLEAR"),
        BarrierCode.MEDICATION_CONCERN: (
            "LONG_TERM_USE",
            "DEPENDENCE_OR_TOLERANCE",
            "BODY_HARM",
            "PILL_BURDEN",
            "CONFLICTING_INFORMATION",
        ),
        BarrierCode.ACCESS_OR_COST: ("RUNNING_LOW", "REFILL_MISSED", "VISIT_DIFFICULT", "COST_BURDEN"),
    }
)


def validate_subreason(barrier_code: BarrierCode, subreason_code: str | None) -> str | None:
    if subreason_code is None:
        return None
    if subreason_code not in SUBREASONS[barrier_code]:
        raise ValueError("subreason does not match barrier")
    return subreason_code


def questions_for_support(
    catalog: "SupportCopyCatalog", support_code: SupportCode, subreason_code: str | None = None
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (question.question_id, question.text) for question in catalog.questions_for(support_code, subreason_code)
    )


def validate_questions(
    catalog: "SupportCopyCatalog", support_code: SupportCode, subreason_code: str | None, question_ids: list[str]
) -> tuple[str, ...]:
    allowed = {question_id for question_id, _ in questions_for_support(catalog, support_code, subreason_code)}
    if (
        len(question_ids) != len(set(question_ids))
        or len(question_ids) > 3
        or any(item not in allowed for item in question_ids)
    ):
        raise ValueError("invalid support question selection")
    if not allowed and question_ids:
        raise ValueError("questions are not available for this support")
    return tuple(question_ids)


def question_texts(
    catalog: "SupportCopyCatalog", question_ids: list[str] | tuple[str, ...]
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (question_id, catalog.questions[question_id].text)
        for question_id in question_ids
        if question_id in catalog.questions
    )
