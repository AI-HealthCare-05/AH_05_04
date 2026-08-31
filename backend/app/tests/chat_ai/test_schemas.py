from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.services.chat_ai import schemas
from app.services.chat_ai.schemas import ChatGenerationInput, ChatGenerationResult, ChatMedicationInput


def test_chat_input_normalizes_question_and_medication_fields() -> None:
    chat_input = ChatGenerationInput(
        question="  A\u030a약을 먹고\n졸릴 수 있나요?  ",
        medications=[
            ChatMedicationInput(
                medication_name="  합성약   정  ",
                dose_value=Decimal("0.500"),
                dose_unit="  mg ",
                frequency_per_day=2,
                timing_text="  아침   식후 ",
                duration_days=7,
            )
        ],
    )

    assert chat_input.question == "Å약을 먹고\n졸릴 수 있나요?"
    assert chat_input.medications[0].medication_name == "합성약 정"
    assert chat_input.medications[0].dose_value == Decimal("0.500")
    assert chat_input.medications[0].dose_unit == "mg"
    assert chat_input.medications[0].timing_text == "아침 식후"


@pytest.mark.parametrize("question", ["", "   ", "가" * 2001])
def test_chat_input_rejects_blank_or_oversized_question(question: str) -> None:
    with pytest.raises(ValidationError):
        ChatGenerationInput(
            question=question,
            medications=[ChatMedicationInput(medication_name="합성약")],
        )


def test_chat_input_applies_question_length_limit_after_trimming() -> None:
    chat_input = ChatGenerationInput(
        question=f"  {'가' * 2000}  ",
        medications=[ChatMedicationInput(medication_name="합성약")],
    )

    assert chat_input.question == "가" * 2000


def test_chat_input_rejects_non_string_question_as_validation_error() -> None:
    with pytest.raises(ValidationError):
        ChatGenerationInput.model_validate(
            {
                "question": 123,
                "medications": [{"medication_name": "합성약"}],
            }
        )


def test_chat_input_requires_medication_and_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ChatGenerationInput(question="질문", medications=[])

    with pytest.raises(ValidationError):
        ChatGenerationInput.model_validate(
            {
                "question": "질문",
                "medications": [{"medication_name": "합성약"}],
                "session_id": "session-1",
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [("dose_value", "0"), ("frequency_per_day", 0), ("duration_days", -1)],
)
def test_medication_rejects_non_positive_numbers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ChatMedicationInput.model_validate({"medication_name": "합성약", field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("question", "합성\x00질문"),
        ("question", "합성\u202e질문"),
        ("question", "합성\u200b질문"),
        ("medication_name", "합성\x00약"),
        ("dose_unit", "m\u2066g"),
        ("timing_text", "식\ufeff후"),
    ],
)
def test_input_rejects_forbidden_control_characters(field: str, value: str) -> None:
    data: dict[str, object] = {
        "question": "이 약은 무엇인가요?",
        "medications": [{"medication_name": "합성약"}],
    }
    if field == "question":
        data[field] = value
    else:
        data["medications"] = [{"medication_name": "합성약", field: value}]

    with pytest.raises(ValidationError):
        ChatGenerationInput.model_validate(data)


def test_optional_medication_strings_allow_missing_values() -> None:
    medication = ChatMedicationInput(
        medication_name="합성약",
        dose_unit="   ",
        timing_text="   ",
    )

    assert medication.dose_unit is None
    assert medication.timing_text is None


@pytest.mark.parametrize(
    ("field", "max_length"),
    [
        ("medication_name", 255),
        ("dose_unit", 50),
        ("timing_text", 255),
    ],
)
def test_medication_rejects_strings_longer_than_storage_contract(field: str, max_length: int) -> None:
    medication: dict[str, object] = {"medication_name": "합성약"}
    medication[field] = "가" * (max_length + 1)

    with pytest.raises(ValidationError):
        ChatMedicationInput.model_validate(medication)


def test_chat_input_accepts_up_to_thirty_medications() -> None:
    chat_input = ChatGenerationInput(
        question="이 약들을 함께 복용해도 되나요?",
        medications=[ChatMedicationInput(medication_name=f"합성약 {index}") for index in range(30)],
    )

    assert len(chat_input.medications) == 30


def test_chat_input_rejects_more_than_thirty_medications() -> None:
    with pytest.raises(ValidationError):
        ChatGenerationInput(
            question="이 약들을 함께 복용해도 되나요?",
            medications=[ChatMedicationInput(medication_name=f"합성약 {index}") for index in range(31)],
        )


def test_chat_input_accepts_empty_or_three_history_pairs_and_normalizes_text() -> None:
    empty = ChatGenerationInput(
        question="질문",
        history=[],
        medications=[ChatMedicationInput(medication_name="합성약")],
    )
    full = ChatGenerationInput(
        question="질문",
        history=[
            schemas.ChatHistoryItem(question=f"  질문 {index}  ", answer=f"  답변 {index}  ") for index in range(3)
        ],
        medications=[ChatMedicationInput(medication_name="합성약")],
    )

    assert empty.history == []
    assert [(item.question, item.answer) for item in full.history or []] == [
        (f"질문 {index}", f"답변 {index}") for index in range(3)
    ]


@pytest.mark.parametrize(
    "history",
    [
        [{"question": f"질문 {index}", "answer": "답변"} for index in range(4)],
        [
            {"question": "가" * 2000, "answer": "나" * 10_000},
            {"question": "추가", "answer": "답변"},
        ],
    ],
)
def test_chat_input_rejects_history_over_pair_or_total_character_limit(history: list[object]) -> None:
    with pytest.raises(ValidationError):
        ChatGenerationInput.model_validate(
            {
                "question": "질문",
                "history": history,
                "medications": [{"medication_name": "합성약"}],
            }
        )


@pytest.mark.parametrize("field", ["question", "answer"])
def test_history_rejects_blank_oversized_or_forbidden_text(field: str) -> None:
    valid = {"question": "과거 질문", "answer": "과거 답변"}
    invalid_values = [" ", "가" * (2001 if field == "question" else 10_001), "숨김\u202e문자"]

    for value in invalid_values:
        with pytest.raises(ValidationError):
            schemas.ChatHistoryItem.model_validate({**valid, field: value})


def test_generation_result_strips_content_and_validates_limits() -> None:
    result = ChatGenerationResult(
        content="  복용 중 졸림이 나타날 수 있습니다.  ",
        model_name="gpt-4o-mini-2024-07-18",
        prompt_version="chat-prompt-v2",
    )

    assert result.content == "복용 중 졸림이 나타날 수 있습니다."

    for invalid in ["   ", "가" * 10_001]:
        with pytest.raises(ValidationError):
            ChatGenerationResult(
                content=invalid,
                model_name="gpt-4o-mini",
                prompt_version="chat-prompt-v2",
            )

    with pytest.raises(ValidationError):
        ChatGenerationResult(
            content="답변",
            model_name="m" * 101,
            prompt_version="chat-prompt-v2",
        )


def test_generation_result_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ChatGenerationResult.model_validate(
            {
                "content": "답변",
                "model_name": "gpt-4o-mini",
                "prompt_version": "chat-prompt-v2",
                "citations": [],
            }
        )
