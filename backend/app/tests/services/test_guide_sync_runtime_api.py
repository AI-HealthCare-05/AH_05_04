from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.security import get_request_user
from app.dependencies.services import (
    get_consent_gate_service,
    get_guide_generator,
    get_guide_sync_runtime_lifecycle,
)
from app.main import fastapi_app
from app.models.async_jobs import AiJobType
from app.models.guides import Guide
from app.models.rag_source import RagSourceSnapshot, RagSourceSnapshotMember
from app.models.users import User
from app.repositories.async_job_repository import AsyncJobRepository
from app.services.guide_ai.generator import GuideGenerator
from app.services.guide_runtime_request import GuideRuntimeRequestCarrier
from app.services.guide_sync_runtime_lifecycle import (
    GuideSyncRuntimeAuthority,
    GuideSyncRuntimeLifecycleProducer,
    GuideSyncRuntimePreparation,
)
from app.services.user_consents import ConsentGateService
from app.tests.repositories.test_guide_repository import _create_source_snapshot_members
from app.tests.services.test_guides import (
    _create_confirmed_prescription,
    _create_user,
    _UnusedGuideProvider,
)
from rag_runtime.guide_release_projection import (
    GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
    GuideRuntimeApprovedAnswer,
    GuideRuntimeCitationSourceType,
    GuideRuntimeReleaseDecision,
    GuideRuntimeReleaseProjectionCarrier,
    GuideRuntimeVerifiedCitation,
)
from rag_runtime.guide_runtime_execution import (
    GuideRuntimeExecutionRequest,
    GuideRuntimeExecutionResult,
    GuideRuntimeExecutorFactoryPort,
    GuideRuntimeExecutorPort,
    GuideRuntimeProviderProvenance,
)
from rag_runtime.request_guard_runtime_binding import RequestGuardRuntimeBindingRef


class _AuthorityProvider:
    async def resolve(self, *, user: User, guide: Guide) -> GuideSyncRuntimeAuthority:
        _ = (user, guide)
        return GuideSyncRuntimeAuthority(
            runtime_environment_id=uuid4(),
            runtime_environment_revision=1,
            runtime_release_bundle_id=uuid4(),
            runtime_release_bundle_manifest_hash="a" * 64,
            runtime_execution_manifest_id=uuid4(),
            runtime_execution_manifest_hash="b" * 64,
            guide_retrieval_binding_manifest_id=uuid4(),
            guide_retrieval_binding_manifest_hash="c" * 64,
            request_guard_runtime_binding_ref=RequestGuardRuntimeBindingRef(
                artifact_code="guide-sync-closed-demo",
                version="v1",
                content_sha256="d" * 64,
            ),
        )


class _Lifecycle:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def prepare(
        self,
        *,
        user: User,
        guide: Guide,
        authority: GuideSyncRuntimeAuthority,
    ) -> GuideSyncRuntimePreparation:
        _ = authority
        job = await AsyncJobRepository(self._session).create_job(
            user_id=user.id,
            job_type=AiJobType.GUIDE,
            prescription_version_id=guide.prescription_version_id,
        )
        guide.ai_job_id = job.id
        await self._session.flush()
        return GuideSyncRuntimePreparation(
            job=job,
            carrier=cast(GuideRuntimeRequestCarrier, object()),
        )


def _projection(
    *,
    snapshot: RagSourceSnapshot,
    member: RagSourceSnapshotMember,
) -> GuideRuntimeReleaseProjectionCarrier:
    return GuideRuntimeReleaseProjectionCarrier(
        contract_version=GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
        release_decision=GuideRuntimeReleaseDecision.PASS,
        is_current=True,
        answer=GuideRuntimeApprovedAnswer(
            claim_action_texts=("합성 근거에 따라 정해진 복용법을 확인하세요.",),
            uncertainty_text="변경 사항은 의료진에게 확인하세요.",
            consultation_text="이상 반응이 있으면 의료진과 상담하세요.",
        ),
        fallback=None,
        citations=(
            GuideRuntimeVerifiedCitation(
                card_target_ref="card:closed-demo",
                claim_key="claim-1",
                evidence_key="evidence-1",
                source_type=GuideRuntimeCitationSourceType.LIFESTYLE_GUIDELINE,
                source_snapshot_id=snapshot.id,
                source_snapshot_member_id=member.id,
                source_code="MFDS_PRODUCT_LABEL",
                source_version=snapshot.source_version,
                locator=member.locator,
                content_sha256=member.content_sha256,
                display_order=1,
            ),
        ),
    )


class _Executor(GuideRuntimeExecutorPort):
    def __init__(self, projection: GuideRuntimeReleaseProjectionCarrier) -> None:
        self._projection = projection

    async def execute(self, request: GuideRuntimeExecutionRequest) -> GuideRuntimeExecutionResult:
        _ = request
        return GuideRuntimeExecutionResult.succeeded(
            self._projection,
            GuideRuntimeProviderProvenance(
                model_name="closed-demo-model",
                prompt_version="closed-demo-guide-prompt-v1",
            ),
        )


class _Factory(GuideRuntimeExecutorFactoryPort):
    def __init__(self, projection: GuideRuntimeReleaseProjectionCarrier) -> None:
        self._projection = projection

    def create(self) -> GuideRuntimeExecutorPort:
        return _Executor(self._projection)


async def test_post_guide_runtime_result_is_persisted_and_rediscovered(
    db_session: AsyncSession,
) -> None:
    user = await _create_user(db_session, email="guide-sync-runtime-api@example.com")
    prescription = await _create_confirmed_prescription(db_session, user=user)
    snapshot, members = await _create_source_snapshot_members(db_session)
    lifecycle = cast(GuideSyncRuntimeLifecycleProducer, _Lifecycle(db_session))
    factory = _Factory(_projection(snapshot=snapshot, member=members[0]))
    consent_gate = AsyncMock(spec=ConsentGateService)
    generator = GuideGenerator(provider=_UnusedGuideProvider(), model="legacy-model", timeout_seconds=1.0)

    async def override_user() -> User:
        return user

    def override_lifecycle() -> GuideSyncRuntimeLifecycleProducer:
        return lifecycle

    def override_consent_gate() -> ConsentGateService:
        return consent_gate

    def override_generator() -> GuideGenerator:
        return generator

    fastapi_app.state.guide_runtime_executor_factory = factory
    fastapi_app.state.guide_sync_runtime_authority_provider = _AuthorityProvider()
    fastapi_app.dependency_overrides[get_request_user] = override_user
    fastapi_app.dependency_overrides[get_guide_sync_runtime_lifecycle] = override_lifecycle
    fastapi_app.dependency_overrides[get_consent_gate_service] = override_consent_gate
    fastapi_app.dependency_overrides[get_guide_generator] = override_generator
    try:
        async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as client:
            created = await client.post(
                "/api/v1/guides",
                json={"prescription_id": str(prescription.id)},
            )
            assert created.status_code == 201
            created_data = created.json()["data"]
            rediscovered = await client.get(f"/api/v1/guides/{created_data['guide_id']}")
            assert rediscovered.status_code == 200
    finally:
        fastapi_app.dependency_overrides.pop(get_request_user, None)
        fastapi_app.dependency_overrides.pop(get_guide_sync_runtime_lifecycle, None)
        fastapi_app.dependency_overrides.pop(get_consent_gate_service, None)
        fastapi_app.dependency_overrides.pop(get_guide_generator, None)
        del fastapi_app.state.guide_runtime_executor_factory
        del fastapi_app.state.guide_sync_runtime_authority_provider

    rediscovered_data = rediscovered.json()["data"]
    assert created_data == rediscovered_data
    assert created_data["generation_status"] == "COMPLETED"
    assert created_data["release_decision"] == "PASS"
    assert created_data["model_name"] == "closed-demo-model"
    assert created_data["prompt_version"] == "closed-demo-guide-prompt-v1"
    assert created_data["citations"] == [
        {
            "source_type": "LIFESTYLE_GUIDELINE",
            "source_code": "MFDS_PRODUCT_LABEL",
            "source_version": snapshot.source_version,
            "locator": members[0].locator,
            "display_order": 1,
        }
    ]
    assert "legacy-model" not in str(created_data)
    assert "evidence-1" not in str(created_data)
    assert "card:closed-demo" not in str(created_data)
