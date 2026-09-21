from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import config as app_config
from app.dependencies.security import get_request_user
from app.dependencies.services import (
    get_consent_gate_service,
    get_guide_closed_demo_generator,
    get_guide_generator,
    get_guide_repository,
    get_openai_client,
)
from app.main import fastapi_app
from app.models.guides import Guide, GuideGenerationStatus
from app.models.prescriptions import Prescription, PrescriptionVersion, PrescriptionVersionMedication
from app.models.rag_candidate import (
    MedicationIdentification,
    MedicationIdentificationSource,
    MedicationIdentificationStatus,
)
from app.models.users import User
from app.services.guide_ai.closed_demo_generator import (
    CLOSED_DEMO_GUIDE_PROMPT_VERSION,
    GuideClosedDemoGenerator,
)
from app.services.guide_ai.schemas import GuideGenerationResult
from app.services.user_consents import ConsentGateService
from app.tests.fixtures.prescription_fingerprint import fingerprint_values


class _InMemoryGuideRepository:
    """Lightweight in-memory persistence verifying DI -> GuideService -> Repository -> API projection."""

    def __init__(self, prescription: Prescription) -> None:
        self._prescription = prescription
        self._guides: dict[uuid.UUID, Guide] = {}

    async def get_prescription_owned(self, *, prescription_id: uuid.UUID, user_id: uuid.UUID) -> Prescription | None:
        if self._prescription.id == prescription_id and self._prescription.profile_id == user_id:
            return self._prescription
        return None

    async def create(self, *, prescription: Prescription) -> Guide:
        guide_id = uuid.uuid4()
        guide = Guide(
            id=guide_id,
            prescription_id=prescription.id,
            prescription_version_id=prescription.active_version_id,
            profile_id=prescription.profile_id,
            generation_status=GuideGenerationStatus.GENERATING,
            citations=[],
            requested_at=datetime.now(UTC),
        )
        guide.prescription = prescription
        self._guides[guide_id] = guide
        return guide

    async def lock_if_current_version(self, guide: Guide) -> bool:
        return True

    async def mark_completed(
        self,
        guide: Guide,
        *,
        content: str,
        model_name: str,
        prompt_version: str,
        completed_at: datetime,
    ) -> Guide:
        guide.generation_status = GuideGenerationStatus.COMPLETED
        guide.content = content
        guide.model_name = model_name
        guide.prompt_version = prompt_version
        guide.completed_at = completed_at
        return guide

    async def get_owned(self, *, guide_id: uuid.UUID, user_id: uuid.UUID) -> Guide | None:
        guide = self._guides.get(guide_id)
        if guide is not None and guide.profile_id == user_id:
            return guide
        return None


def _prescription(user_id: uuid.UUID) -> Prescription:
    prescription_id = uuid.uuid4()
    med_id = uuid.uuid4()
    med = PrescriptionVersionMedication(
        id=med_id,
        medication_count=1,
        medication_name="노바스크정",
        strength_text="5mg",
        dose_value=Decimal("1"),
        dose_unit="정",
        frequency_per_day=1,
        timing_text="아침 식후",
        duration_days=30,
        display_order=1,
    )
    ident = MedicationIdentification(
        id=uuid.uuid4(),
        prescription_version_medication_id=med_id,
        candidate_search_id=uuid.uuid4(),
        status=MedicationIdentificationStatus.MATCHED,
        source=MedicationIdentificationSource.USER_SELECTED,
        code_system="MFDS_ITEM_SEQ",
        canonical_code="200610660",
    )
    med.identifications = [ident]

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


@pytest.mark.asyncio
async def test_closed_demo_route_wiring_and_rediscovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route-level ASGI test verifying FastAPI DI -> GuideService -> persistence -> API projection."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    user = User(id=user_id, email="closed-demo@example.com", hashed_password="secret")
    prescription = _prescription(user_id)
    repo = _InMemoryGuideRepository(prescription)

    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_ENABLED", True)
    monkeypatch.setattr(app_config, "PUBLIC_TRACK_F_ENABLED", False)
    monkeypatch.setattr(app_config, "GUIDE_RUNTIME_ENABLED", False)
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_USER_IDS", frozenset({user_id}))
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_STARTS_AT", now - timedelta(hours=1))
    monkeypatch.setattr(app_config, "GUIDE_CLOSED_DEMO_RAG_EXPIRES_AT", now + timedelta(days=2))

    consent_gate = AsyncMock(spec=ConsentGateService)

    rag_content = (
        "복약 가이드\n\n"
        "[1] 노바스크정 5mg\n"
        "1회량: 1 정\n"
        "하루 횟수: 하루 1회\n"
        "복용 시점: 아침 식후\n"
        "복용 기간: 30일\n"
        "복용 시 주의해야 할 점: 매일 같은 시간에 복용하세요.\n\n"
        "공통 안내: 처방된 용법을 지켜 복용하세요.\n"
        "안전 안내: 이상 증상이 나타나면 의사 또는 약사와 상담하십시오."
    )
    fake_generator = AsyncMock(spec=GuideClosedDemoGenerator)
    fake_generator.generate.return_value = GuideGenerationResult(
        content=rag_content,
        model_name="gpt-4o",
        prompt_version=CLOSED_DEMO_GUIDE_PROMPT_VERSION,
    )

    fastapi_app.dependency_overrides[get_request_user] = lambda: user
    fastapi_app.dependency_overrides[get_consent_gate_service] = lambda: consent_gate
    fastapi_app.dependency_overrides[get_guide_repository] = lambda: repo
    fastapi_app.dependency_overrides[get_guide_generator] = lambda: MagicMock()
    fastapi_app.dependency_overrides[get_guide_closed_demo_generator] = lambda: fake_generator
    fastapi_app.dependency_overrides[get_openai_client] = lambda: AsyncMock()

    try:
        async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/guides",
                json={"prescription_id": str(prescription.id)},
            )
            assert created.status_code == 201
            created_data = created.json()["data"]

            assert created_data["prompt_version"] == "guide-closed-demo-rag-v1"
            assert created_data["release_decision"] is None
            assert created_data["citations"] == []
            assert "복용 시 주의해야 할 점" in created_data["content"]

            guide_id = created_data["guide_id"]
            rediscovered = await client.get(f"/api/v1/guides/{guide_id}")
            assert rediscovered.status_code == 200
            rediscovered_data = rediscovered.json()["data"]

            assert rediscovered_data == created_data
            assert rediscovered_data["content"] == created_data["content"]
            assert "복용 시 주의해야 할 점" in rediscovered_data["content"]
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        fastapi_app.dependency_overrides.pop(get_consent_gate_service, None)
        fastapi_app.dependency_overrides.pop(get_guide_repository, None)
        fastapi_app.dependency_overrides.pop(get_guide_generator, None)
        fastapi_app.dependency_overrides.pop(get_guide_closed_demo_generator, None)
        fastapi_app.dependency_overrides.pop(get_openai_client, None)
