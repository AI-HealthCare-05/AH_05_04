from decimal import Decimal
from uuid import uuid4

from app.services.chat_ai import (
    ChatGenerationFailedError,
    ChatReplyInput,
)
from app.services.chat_ai import (
    ChatMedicationInput as ChatReplyMedicationInput,
)
from app.services.chat_ai.schemas import ChatMedicationInput as SchemaChatMedicationInput


def test_public_contract_preserves_backend_medication_input_at_package_root() -> None:
    assert ChatReplyMedicationInput is not SchemaChatMedicationInput

    generation_medication = SchemaChatMedicationInput(
        medication_name="합성약",
        dose_value=Decimal("1.5"),
        dose_unit="mg",
        frequency_per_day=1,
        timing_text="저녁 식후",
        duration_days=7,
    )
    reply_medication = ChatReplyMedicationInput(
        medication_name="합성약",
        dose_value=Decimal("1.500"),
        dose_unit="mg",
        frequency_per_day=1,
        timing_text="저녁 식후",
        duration_days=7,
    )

    reply_input = ChatReplyInput(
        prescription_id=uuid4(),
        medications=[reply_medication],
        content="질문",
    )

    assert generation_medication.duration_days == 7
    assert reply_medication.dose_value == Decimal("1.500")
    assert reply_medication.duration_days == 7
    assert issubclass(ChatGenerationFailedError, Exception)
    assert reply_input.medications == [reply_medication]
