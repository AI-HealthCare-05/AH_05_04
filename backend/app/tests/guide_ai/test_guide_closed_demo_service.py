from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.core import config as app_config
from app.core.errors import ApiError
from app.dtos.guides import CreateGuideRequest
from app.models.guides import Guide, GuideGenerationStatus
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.rag_candidate import (
    MedicationIdentification,
    MedicationIdentificationSource,
    MedicationIdentificationStatus,
)
from app.models.users import User
from app.repositories.guide_repository import GuideRepository
from app.services.guide_ai import GuideGenerationResult, GuideGenerator
from app.services.guide_ai.closed_demo_generator import (
    CLOSED_DEMO_GUIDE_PROMPT_VERSION,
    GuideClosedDemoGenerator,
)
from app.services.guide_ai.exceptions import GuideGenerationTimeoutError
from app.services.guides import GuideService
from app.services.user_consents import ConsentGateService
from app.tests.fixtures.prescription_fingerprint import fingerprint_values


def _user(user_id: uuid.UUID | None = None) -> User:
    return User(
        id=user_id or uuid.uuid4(),
        email="test@example.com",
        hashed_password="secret",
    )


def _medication(
    name: str = "노바스크정",
    strength: str = "5mg",
    *,
    ident_code: str | None = "200610660",
) -> PrescriptionVersionMedication:
    med_id = uuid.uuid4()
    med = PrescriptionVersionMedication(
        id=med_id,
        medication_count=1,
        medication_name=name,
        strength_text=strength,
        dose_value=Decimal("1"),
        dose_unit="정",
        frequency_per_day=1,
        timing_text="아침 식후",
        duration_days=30,
        display_order=1,
    )
    if ident_code is not None:
        ident = MedicationIdentification(
            id=uuid.uuid4(),
            prescription_version_medication_id=med_id,
            candidate_search_id=uuid.uuid4(),
            status=MedicationIdentificationStatus.MATCHED,
            source=MedicationIdentificationSource.USER_SELECTED,
            code_system="MFDS_ITEM_SEQ",
            canonical_code=ident_code,
        )
        med.identifications = [ident]
    else:
        med.identifications = []
    return med


def _prescription(
    user_id: uuid.UUID, med_name: str = "노바스크정", ident_code: str | None = "200610660"
) -> Prescription:
    prescription_id = uuid.uuid4()
    med = _medication(med_name, ident_code=ident_code)
    version_id = uuid.uuid4()
    med.prescription_version_id = version_id
    prescribed_date = datetime.now(UTC).date()

    version = PrescriptionVersion(
        **fingerprint_values(
            prescribed_date,
            [
                {
                    "medication_name": med.medication_name,
                    "strength_text": med.strength_text,
                    "dose_value": med.dose_value,
                    "dose_unit": med.dose_unit,
                    "frequency_per_day": med.frequency_per_day,
                    "timing_text": med.timing_text,
                    "duration_days": med.duration_days,
                    "display_order": med.display_order,
                }
            ],
        ),
        id=version_id,
        prescription_id=prescription_id,
        version_number=1,
        prescribed_date=prescribed_date,
        medications=[med],
    )
    return Prescription(
        id=prescription_id,
        profile_id=user_id,
        active_version_id=version.id,
        active_version=version,
    )


def _guide(prescription_id: uuid.UUID, version_id: uuid.UUID) -> Guide:
    return Guide(
        id=uuid.uuid4(),
        prescription_id=prescription_id,
        prescription_version_id=version_id,
        generation_status=GuideGenerationStatus.GENERATING,
        citations=[],
        requested_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_closed_demo_user_routes_to_demo_rag_and_preserves_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    user = _user(user_id)
    prescription = _prescription(user_id)

    # Enable closed demo gate for user_id
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_ENABLED", True)
    monkeypatch.setattr(app_config, "PUBLIC_TRACK_F_ENABLED", False)
    monkeypatch.setattr(app_config, "GUIDE_RUNTIME_ENABLED", False)
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_USER_IDS", frozenset({user_id}))
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_STARTS_AT", now - timedelta(hours=1))
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT", now + timedelta(days=2))

    guide_obj = _guide(prescription.id, prescription.active_version_id)
    repo = AsyncMock(spec=GuideRepository)
    repo.get_prescription_owned.return_value = prescription
    repo.create.return_value = guide_obj
    repo.lock_if_current_version.return_value = True

    async def fake_mark_completed(
        g: Guide, *, content: str, model_name: str, prompt_version: str, completed_at: datetime
    ) -> Guide:
        g.generation_status = GuideGenerationStatus.COMPLETED
        g.content = content
        g.model_name = model_name
        g.prompt_version = prompt_version
        g.completed_at = completed_at
        return g

    repo.mark_completed.side_effect = fake_mark_completed

    legacy_generator = AsyncMock(spec=GuideGenerator)
    closed_demo_generator = AsyncMock(spec=GuideClosedDemoGenerator)
    closed_demo_generator.generate.return_value = GuideGenerationResult(
        content="복약 가이드\n\n[1] 노바스크정 5mg\n복용 시 주의해야 할 점: 매일 정해진 시간",
        model_name="gpt-4o",
        prompt_version=CLOSED_DEMO_GUIDE_PROMPT_VERSION,
    )

    consent_gate = AsyncMock(spec=ConsentGateService)

    service = GuideService(
        repository=cast(GuideRepository, repo),
        generator=cast(GuideGenerator, legacy_generator),
        consent_gate=cast(ConsentGateService, consent_gate),
        closed_demo_generator=cast(GuideClosedDemoGenerator, closed_demo_generator),
    )

    result = await service.create_guide(user=user, request=CreateGuideRequest(prescription_id=prescription.id))

    # Assert closed demo generator was called and legacy was NOT called
    closed_demo_generator.generate.assert_awaited_once()
    legacy_generator.generate.assert_not_awaited()

    assert result.prompt_version == CLOSED_DEMO_GUIDE_PROMPT_VERSION
    assert result.content is not None and "복용 시 주의해야 할 점" in result.content
    assert result.release_decision is None
    assert result.fallback_code is None
    assert result.citations == []


@pytest.mark.asyncio
async def test_non_demo_user_falls_back_to_legacy_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    demo_user_id = uuid.uuid4()
    non_demo_user_id = uuid.uuid4()
    now = datetime.now(UTC)
    user = _user(non_demo_user_id)
    prescription = _prescription(non_demo_user_id)

    # Allowlist has demo_user_id, but current user is non_demo_user_id
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_ENABLED", True)
    monkeypatch.setattr(app_config, "PUBLIC_TRACK_F_ENABLED", False)
    monkeypatch.setattr(app_config, "GUIDE_RUNTIME_ENABLED", False)
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_USER_IDS", frozenset({demo_user_id}))
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_STARTS_AT", now - timedelta(hours=1))
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT", now + timedelta(days=2))

    guide_obj = _guide(prescription.id, prescription.active_version_id)
    repo = AsyncMock(spec=GuideRepository)
    repo.get_prescription_owned.return_value = prescription
    repo.create.return_value = guide_obj
    repo.lock_if_current_version.return_value = True

    async def fake_mark_completed(
        g: Guide, *, content: str, model_name: str, prompt_version: str, completed_at: datetime
    ) -> Guide:
        g.generation_status = GuideGenerationStatus.COMPLETED
        g.content = content
        g.model_name = model_name
        g.prompt_version = prompt_version
        g.completed_at = completed_at
        return g

    repo.mark_completed.side_effect = fake_mark_completed

    legacy_generator = AsyncMock(spec=GuideGenerator)
    legacy_generator.generate.return_value = GuideGenerationResult(
        content="레거시 가이드 내용",
        model_name="gpt-4o",
        prompt_version="guide-prompt-v2",
    )
    closed_demo_generator = AsyncMock(spec=GuideClosedDemoGenerator)
    consent_gate = AsyncMock(spec=ConsentGateService)

    service = GuideService(
        repository=cast(GuideRepository, repo),
        generator=cast(GuideGenerator, legacy_generator),
        consent_gate=cast(ConsentGateService, consent_gate),
        closed_demo_generator=cast(GuideClosedDemoGenerator, closed_demo_generator),
    )

    result = await service.create_guide(user=user, request=CreateGuideRequest(prescription_id=prescription.id))

    # Assert legacy was called and closed demo was NOT called
    legacy_generator.generate.assert_awaited_once()
    closed_demo_generator.generate.assert_not_awaited()
    assert result.prompt_version == "guide-prompt-v2"
    assert result.content == "레거시 가이드 내용"


@pytest.mark.asyncio
async def test_closed_demo_fails_closed_on_unresolvable_medication(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    user = _user(user_id)
    # Medication with no identification and unresolvable name
    prescription = _prescription(user_id, med_name="식별불가_미등록약품", ident_code=None)

    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_ENABLED", True)
    monkeypatch.setattr(app_config, "PUBLIC_TRACK_F_ENABLED", False)
    monkeypatch.setattr(app_config, "GUIDE_RUNTIME_ENABLED", False)
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_USER_IDS", frozenset({user_id}))
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_STARTS_AT", now - timedelta(hours=1))
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT", now + timedelta(days=2))

    guide_obj = _guide(prescription.id, prescription.active_version_id)
    repo = AsyncMock(spec=GuideRepository)
    repo.get_prescription_owned.return_value = prescription
    repo.create.return_value = guide_obj

    legacy_generator = AsyncMock(spec=GuideGenerator)
    closed_demo_generator = AsyncMock(spec=GuideClosedDemoGenerator)
    consent_gate = AsyncMock(spec=ConsentGateService)

    service = GuideService(
        repository=cast(GuideRepository, repo),
        generator=cast(GuideGenerator, legacy_generator),
        consent_gate=cast(ConsentGateService, consent_gate),
        closed_demo_generator=cast(GuideClosedDemoGenerator, closed_demo_generator),
    )

    with pytest.raises(ApiError) as exc_info:
        await service.create_guide(user=user, request=CreateGuideRequest(prescription_id=prescription.id))

    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "GUIDE_GENERATION_FAILED"
    repo.mark_failed.assert_awaited_once()
    closed_demo_generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_closed_demo_maps_timeout_to_504(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    user = _user(user_id)
    prescription = _prescription(user_id)

    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_ENABLED", True)
    monkeypatch.setattr(app_config, "PUBLIC_TRACK_F_ENABLED", False)
    monkeypatch.setattr(app_config, "GUIDE_RUNTIME_ENABLED", False)
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_USER_IDS", frozenset({user_id}))
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_STARTS_AT", now - timedelta(hours=1))
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT", now + timedelta(days=2))

    guide_obj = _guide(prescription.id, prescription.active_version_id)
    repo = AsyncMock(spec=GuideRepository)
    repo.get_prescription_owned.return_value = prescription
    repo.create.return_value = guide_obj

    legacy_generator = AsyncMock(spec=GuideGenerator)
    closed_demo_generator = AsyncMock(spec=GuideClosedDemoGenerator)
    closed_demo_generator.generate.side_effect = GuideGenerationTimeoutError("timeout")
    consent_gate = AsyncMock(spec=ConsentGateService)

    service = GuideService(
        repository=cast(GuideRepository, repo),
        generator=cast(GuideGenerator, legacy_generator),
        consent_gate=cast(ConsentGateService, consent_gate),
        closed_demo_generator=cast(GuideClosedDemoGenerator, closed_demo_generator),
    )

    with pytest.raises(ApiError) as exc_info:
        await service.create_guide(user=user, request=CreateGuideRequest(prescription_id=prescription.id))

    assert exc_info.value.status_code == 504
    assert exc_info.value.code == "GATEWAY_TIMEOUT"
    repo.mark_failed.assert_awaited_once()
