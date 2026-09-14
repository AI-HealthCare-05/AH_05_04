from dataclasses import dataclass
from uuid import UUID

from app.core.errors import ApiError, ErrorDetail
from app.models.async_jobs import AiJob, AiJobType, DomainType
from app.models.guides import Guide
from app.models.prescriptions import Prescription
from app.models.users import User
from app.repositories.guide_repository import GuideRepository
from app.repositories.rag_runtime_repository import (
    AiJobExecutionContextCreate,
    AiJobExecutionIdentificationCreate,
    RagRuntimeRepository,
)
from app.services.job_intake import DomainReference, JobIntakeService
from app.services.rag_preflight import RagPreflightService

GUIDE_JOB_INTAKE_OPERATION_ID = "guide.create_job"


@dataclass(frozen=True, slots=True)
class GuideRuntimeContextSnapshot:
    runtime_environment_id: UUID
    runtime_environment_revision: int
    runtime_release_bundle_id: UUID
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    runtime_guard_decision_ref: str
    patient_context_digest: str | None = None
    source_scope_manifest_hash: str | None = None


@dataclass(frozen=True, slots=True)
class GuideJobIntakeResult:
    job: AiJob
    guide: Guide
    is_duplicate: bool


class GuideJobIntakeTransactionAdapter:
    """Guide 202 접수 전환에서 사용할 단일 transaction adapter입니다.

    현재 /guides API는 기존 동기 201 흐름을 유지합니다. 이 adapter는 후속 PR에서
    라우터가 202 + JobStatusResponse로 전환될 때 재사용할 저장 경계를 먼저 고정합니다.
    """

    def __init__(
        self,
        *,
        guide_repository: GuideRepository,
        preflight_service: RagPreflightService,
        runtime_repository: RagRuntimeRepository,
        job_intake_service: JobIntakeService,
    ) -> None:
        self._guide_repository = guide_repository
        self._preflight_service = preflight_service
        self._runtime_repository = runtime_repository
        self._job_intake_service = job_intake_service

    async def accept_guide_job(
        self,
        *,
        user: User,
        prescription_id: UUID,
        idempotency_key: str,
        trace_id: str,
        runtime_context: GuideRuntimeContextSnapshot,
    ) -> GuideJobIntakeResult:
        prescription = await self._get_owned_prescription(user=user, prescription_id=prescription_id)
        expected_prescription_version_id = self._require_active_version_id(prescription)

        async def create_domain_placeholder(ai_job_id: UUID) -> DomainReference:
            return await self._create_domain_reference(
                user=user,
                prescription=prescription,
                ai_job_id=ai_job_id,
                expected_prescription_version_id=expected_prescription_version_id,
                runtime_context=runtime_context,
            )

        intake = await self._job_intake_service.accept_job(
            user_id=user.id,
            job_type=AiJobType.GUIDE,
            operation_id=GUIDE_JOB_INTAKE_OPERATION_ID,
            idempotency_key=idempotency_key,
            fingerprint=self._request_fingerprint(
                prescription_id=prescription_id,
                prescription_version_id=expected_prescription_version_id,
            ),
            create_domain_placeholder=create_domain_placeholder,
            trace_id=trace_id,
            prescription_version_id=expected_prescription_version_id,
        )
        guide = await self._get_created_guide(ai_job_id=intake.job.id)
        return GuideJobIntakeResult(job=intake.job, guide=guide, is_duplicate=intake.is_duplicate)

    async def _get_owned_prescription(self, *, user: User, prescription_id: UUID) -> Prescription:
        prescription = await self._guide_repository.get_prescription_owned(
            prescription_id=prescription_id,
            user_id=user.id,
        )
        if prescription is None:
            raise ApiError(
                status_code=404,
                code="PRESCRIPTION_NOT_FOUND",
                message="처방 정보를 찾을 수 없습니다.",
                details=[
                    ErrorDetail(
                        field="prescription_id",
                        reason="NOT_FOUND",
                        rejected_value=str(prescription_id),
                    )
                ],
            )
        return prescription

    @staticmethod
    def _require_active_version_id(prescription: Prescription) -> UUID:
        if prescription.active_version_id is None:
            raise ApiError(
                status_code=409,
                code="PRESCRIPTION_VERSION_UNAVAILABLE",
                message="활성 처방 버전 정보를 사용할 수 없습니다.",
                details=[ErrorDetail(field="prescription_id", reason="INVALID_VERSION_GRAPH")],
            )
        return prescription.active_version_id

    async def _create_domain_reference(
        self,
        *,
        user: User,
        prescription: Prescription,
        ai_job_id: UUID,
        expected_prescription_version_id: UUID,
        runtime_context: GuideRuntimeContextSnapshot,
    ) -> DomainReference:
        preflight = await self._preflight_service.ensure_all_active_medications_matched(
            prescription_id=prescription.id,
            user_id=user.id,
            expected_prescription_version_id=expected_prescription_version_id,
        )
        guide = await self._guide_repository.create_async_placeholder(
            prescription=prescription,
            ai_job_id=ai_job_id,
        )
        execution_context = await self._runtime_repository.create_execution_context(
            AiJobExecutionContextCreate(
                ai_job_id=ai_job_id,
                guide_id=guide.id,
                prescription_version_id=preflight.prescription_version_id,
                runtime_environment_id=runtime_context.runtime_environment_id,
                runtime_environment_revision=runtime_context.runtime_environment_revision,
                runtime_release_bundle_id=runtime_context.runtime_release_bundle_id,
                runtime_release_bundle_manifest_hash=runtime_context.runtime_release_bundle_manifest_hash,
                runtime_execution_manifest_id=runtime_context.runtime_execution_manifest_id,
                runtime_execution_manifest_hash=runtime_context.runtime_execution_manifest_hash,
                runtime_guard_decision_ref=runtime_context.runtime_guard_decision_ref,
                patient_context_digest=runtime_context.patient_context_digest,
                source_scope_manifest_hash=runtime_context.source_scope_manifest_hash,
            )
        )
        for matched in preflight.matched_medications:
            await self._runtime_repository.create_execution_identification(
                AiJobExecutionIdentificationCreate(
                    execution_context_id=execution_context.id,
                    medication_identification_id=matched.medication_identification_id,
                    prescription_version_medication_id=matched.prescription_version_medication_id,
                )
            )
        return DomainReference(domain_type=DomainType.GUIDE, domain_id=guide.id)

    @staticmethod
    def _request_fingerprint(
        *,
        prescription_id: UUID,
        prescription_version_id: UUID,
    ) -> dict[str, object]:
        return {
            "job_type": AiJobType.GUIDE.value,
            "prescription_id": str(prescription_id),
            "prescription_version_id": str(prescription_version_id),
        }

    async def _get_created_guide(self, *, ai_job_id: UUID) -> Guide:
        guide = await self._guide_repository.get_by_ai_job_id(ai_job_id=ai_job_id)
        if guide is None:
            raise RuntimeError(f"Guide job {ai_job_id} has no guide domain reference")
        return guide
