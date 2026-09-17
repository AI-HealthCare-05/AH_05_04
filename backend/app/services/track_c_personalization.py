"""Deterministic Track C subreason and consultation-question policy."""

from types import MappingProxyType

from app.models.track_c import BarrierCode, SupportCode

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

QUESTION_TEXT = MappingProxyType(
    {
        "INSTRUCTION_AMOUNT": "이 약은 한 번에 몇 개를 복용해야 하나요?",
        "INSTRUCTION_TIMING": "이 약은 언제, 식사와 어떤 관계로 복용해야 하나요?",
        "INSTRUCTION_IDENTITY": "이 약을 다른 약과 어떻게 구분하면 되나요?",
        "INSTRUCTION_SUMMARY": "제가 꼭 기억해야 할 복용 방법을 짧게 설명해 주실 수 있나요?",
        "PURPOSE_REASON": "이 약을 복용하는 목적은 무엇인가요?",
        "PURPOSE_EXPECTATION": "어떤 변화를 언제 확인하면 되나요?",
        "PURPOSE_REVIEW": "다음 진료에서 확인할 사항은 무엇인가요?",
        "CONCERN_DURATION": "이 약을 얼마나 오래 복용할 계획인가요?",
        "CONCERN_MONITORING": "장기 복용 중 확인해야 할 사항이 있나요?",
        "CONCERN_DEPENDENCE": "이 약의 의존성이나 내성에 대해 확인할 점이 있나요?",
        "CONCERN_BURDEN": "복용하는 약이 많아 부담될 때 함께 검토할 수 있는 점이 있나요?",
        "CONCERN_CONFLICT": "제가 본 정보와 처방 안내가 다른데 무엇을 기준으로 확인하면 될까요?",
    }
)

QUESTIONS_BY_SUPPORT = MappingProxyType(
    {
        SupportCode.INSTRUCTION_REVIEW: (
            "INSTRUCTION_AMOUNT",
            "INSTRUCTION_TIMING",
            "INSTRUCTION_IDENTITY",
            "INSTRUCTION_SUMMARY",
        ),
        SupportCode.PURPOSE_REVIEW: ("PURPOSE_REASON", "PURPOSE_EXPECTATION", "PURPOSE_REVIEW"),
        SupportCode.MEDICATION_CONCERN_GUIDANCE: (
            "CONCERN_DURATION",
            "CONCERN_MONITORING",
            "CONCERN_DEPENDENCE",
            "CONCERN_BURDEN",
            "CONCERN_CONFLICT",
        ),
    }
)

QUESTIONS_BY_SUBREASON = MappingProxyType(
    {
        "DOSE_AMOUNT_UNCLEAR": ("INSTRUCTION_AMOUNT", "INSTRUCTION_SUMMARY"),
        "TIMING_OR_FOOD_UNCLEAR": ("INSTRUCTION_TIMING", "INSTRUCTION_SUMMARY"),
        "MEDICATION_IDENTITY_UNCLEAR": ("INSTRUCTION_IDENTITY", "INSTRUCTION_SUMMARY"),
        "LANGUAGE_TOO_COMPLEX": ("INSTRUCTION_SUMMARY",),
        "NO_SYMPTOMS": ("PURPOSE_REASON", "PURPOSE_EXPECTATION"),
        "NO_NOTICEABLE_EFFECT": ("PURPOSE_EXPECTATION", "PURPOSE_REVIEW"),
        "VALUES_IMPROVED": ("PURPOSE_REASON", "PURPOSE_REVIEW"),
        "NEED_UNCLEAR": ("PURPOSE_REASON", "PURPOSE_REVIEW"),
        "LONG_TERM_USE": ("CONCERN_DURATION", "CONCERN_MONITORING"),
        "DEPENDENCE_OR_TOLERANCE": ("CONCERN_DEPENDENCE", "CONCERN_DURATION"),
        "BODY_HARM": ("CONCERN_MONITORING",),
        "PILL_BURDEN": ("CONCERN_BURDEN",),
        "CONFLICTING_INFORMATION": ("CONCERN_CONFLICT",),
    }
)


def validate_subreason(barrier_code: BarrierCode, subreason_code: str | None) -> str | None:
    if subreason_code is None:
        return None
    if subreason_code not in SUBREASONS[barrier_code]:
        raise ValueError("subreason does not match barrier")
    return subreason_code


def questions_for_support(support_code: SupportCode, subreason_code: str | None = None) -> tuple[tuple[str, str], ...]:
    question_ids = (
        QUESTIONS_BY_SUBREASON.get(subreason_code, QUESTIONS_BY_SUPPORT.get(support_code, ()))
        if subreason_code is not None
        else QUESTIONS_BY_SUPPORT.get(support_code, ())
    )
    allowed = set(QUESTIONS_BY_SUPPORT.get(support_code, ()))
    return tuple((question_id, QUESTION_TEXT[question_id]) for question_id in question_ids if question_id in allowed)


def validate_questions(
    support_code: SupportCode, subreason_code: str | None, question_ids: list[str]
) -> tuple[str, ...]:
    allowed = {question_id for question_id, _ in questions_for_support(support_code, subreason_code)}
    if (
        len(question_ids) != len(set(question_ids))
        or len(question_ids) > 3
        or any(item not in allowed for item in question_ids)
    ):
        raise ValueError("invalid support question selection")
    if not allowed and question_ids:
        raise ValueError("questions are not available for this support")
    return tuple(question_ids)


def question_texts(question_ids: list[str] | tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (question_id, QUESTION_TEXT[question_id]) for question_id in question_ids if question_id in QUESTION_TEXT
    )
