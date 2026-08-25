from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.dtos.guides import CreateGuideRequest
from app.models.guides import Guide, GuideGenerationStatus
from app.models.prescriptions import Medication, Prescription
from app.models.users import User
from app.repositories.guide_repository import GuideRepository
from app.services.guide_ai.exceptions import (
    GuideGenerationConfigurationError,
    GuideGenerationInvalidResponseError,
    GuideGenerationSafetyError,
    GuideGenerationTimeoutError,
    GuideGenerationUnavailableError,
)
from app.services.guide_ai.generator import GuideGenerator
from app.services.guide_ai.schemas import GuideGenerationResult
from app.services.guides import GuideService


def _prescription(*, medication_name: str = "합성약 A") -> Prescription:
    return Prescription(
        id=uuid4(),
        medications=[
            Medication(
                medication_name=medication_name,
                dose_value=Decimal("1.250"),
                dose_unit="mg",
                frequency_per_day=2,
                timing_text="아침 식후",
                duration_days=7,
                display_order=1,
            )
        ],
    )


def _prescription_with_ordered_medications() -> Prescription:
    return Prescription(
        id=uuid4(),
        medications=[
            Medication(medication_name="첫번째 약", display_order=1),
            Medication(medication_name="두번째 약", display_order=2),
            Medication(medication_name="세번째 약", display_order=3),
        ],
    )


def _guide(prescription_id: UUID, *, completed: bool = False) -> Guide:
    now = datetime.now(UTC)
    return Guide(
        id=uuid4(),
        prescription_id=prescription_id,
        generation_status=GuideGenerationStatus.COMPLETED if completed else GuideGenerationStatus.GENERATING,
        content="검증된 최종 평문" if completed else None,
        model_name="gpt-4o-mini-2024-07-18" if completed else None,
        prompt_version="guide-prompt-v1" if completed else None,
        requested_at=now,
        completed_at=now if completed else None,
    )


def _service(
    prescription: Prescription,
) -> tuple[GuideService, AsyncMock, AsyncMock]:
    repository = AsyncMock(spec=GuideRepository)
    generator = AsyncMock(spec=GuideGenerator)
    repository.get_prescription_owned.return_value = prescription
    repository.create.return_value = _guide(prescription.id)
    repository.mark_failed.return_value = repository.create.return_value
    return (
        GuideService(
            repository=cast(GuideRepository, repository),
            generator=cast(GuideGenerator, generator),
        ),
        repository,
        generator,
    )


async def test_backend_contract_stores_and_returns_exact_generation_result() -> None:
    prescription = _prescription()
    service, repository, generator = _service(prescription)
    result = GuideGenerationResult(
        content="검증된 최종 평문",
        model_name="gpt-4o-mini-2024-07-18",
        prompt_version="guide-prompt-v1",
    )
    generator.generate.return_value = result
    repository.mark_completed.return_value = _guide(prescription.id, completed=True)

    response = await service.create_guide(
        user=User(id=uuid4()),
        request=CreateGuideRequest(prescription_id=prescription.id),
    )

    generation_input = generator.generate.await_args.args[0]
    assert generation_input.medications[0].model_dump() == {
        "medication_name": "합성약 A",
        "dose_value": Decimal("1.250"),
        "dose_unit": "mg",
        "frequency_per_day": 2,
        "timing_text": "아침 식후",
        "duration_days": 7,
    }
    assert repository.mark_completed.await_args.kwargs["content"] == result.content
    assert repository.mark_completed.await_args.kwargs["model_name"] == result.model_name
    assert repository.mark_completed.await_args.kwargs["prompt_version"] == result.prompt_version
    assert response.content == result.content
    assert response.model_name == result.model_name
    assert response.prompt_version == result.prompt_version


async def test_backend_contract_preserves_medication_order_in_generation_input() -> None:
    prescription = _prescription_with_ordered_medications()
    service, repository, generator = _service(prescription)
    generator.generate.return_value = GuideGenerationResult(
        content="검증된 최종 평문",
        model_name="gpt-4o-mini-2024-07-18",
        prompt_version="guide-prompt-v1",
    )
    repository.mark_completed.return_value = _guide(prescription.id, completed=True)

    await service.create_guide(
        user=User(id=uuid4()),
        request=CreateGuideRequest(prescription_id=prescription.id),
    )

    generation_input = generator.generate.await_args.args[0]
    assert [medication.medication_name for medication in generation_input.medications] == [
        "첫번째 약",
        "두번째 약",
        "세번째 약",
    ]


@pytest.mark.parametrize(
    ("generation_error", "status_code", "api_code", "stored_error_code"),
    [
        (GuideGenerationTimeoutError("timeout"), 504, "GATEWAY_TIMEOUT", "OPENAI_API_TIMEOUT"),
        (GuideGenerationUnavailableError("unavailable"), 503, "SERVICE_UNAVAILABLE", "OPENAI_API_ERROR"),
        (
            GuideGenerationConfigurationError("configuration"),
            500,
            "GUIDE_GENERATION_FAILED",
            "GENERATION_REQUEST_FAILED",
        ),
        (
            GuideGenerationInvalidResponseError("response"),
            500,
            "GUIDE_GENERATION_FAILED",
            "GENERATION_REQUEST_FAILED",
        ),
        (
            GuideGenerationSafetyError("TEST_RULE"),
            500,
            "GUIDE_GENERATION_FAILED",
            "GENERATION_REQUEST_FAILED",
        ),
    ],
)
async def test_backend_contract_maps_generation_errors_and_marks_failed(
    generation_error: Exception,
    status_code: int,
    api_code: str,
    stored_error_code: str,
) -> None:
    prescription = _prescription()
    service, repository, generator = _service(prescription)
    generator.generate.side_effect = generation_error

    with pytest.raises(ApiError) as caught:
        await service.create_guide(
            user=User(id=uuid4()),
            request=CreateGuideRequest(prescription_id=prescription.id),
        )

    assert caught.value.status_code == status_code
    assert caught.value.code == api_code
    assert repository.mark_failed.await_args.kwargs["error_code"] == stored_error_code
    if api_code == "GUIDE_GENERATION_FAILED":
        assert caught.value.message == "복약 가이드 생성에 실패했습니다. 다시 시도해 주세요."


async def test_backend_contract_does_not_call_provider_when_prescription_input_is_invalid() -> None:
    prescription = _prescription(medication_name="   ")
    service, repository, generator = _service(prescription)

    with pytest.raises(ApiError) as caught:
        await service.create_guide(
            user=User(id=uuid4()),
            request=CreateGuideRequest(prescription_id=prescription.id),
        )

    assert caught.value.status_code == 500
    assert caught.value.code == "GUIDE_GENERATION_FAILED"
    generator.generate.assert_not_awaited()
    assert repository.mark_failed.await_args.kwargs["error_code"] == "GENERATION_REQUEST_FAILED"
