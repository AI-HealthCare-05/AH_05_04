"""Sync 201 lifecycle producer for a verified Guide runtime request carrier."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.models.async_jobs import AiJob, AiJobType
from app.models.guides import Guide
from app.models.rag_runtime import (
    RagRuntimeEnvironment,
    RagRuntimeEnvironmentStatus,
    RagRuntimeReleaseBundle,
)
from app.models.users import User
from app.repositories.async_job_repository import AsyncJobRepository
from app.repositories.rag_request_guard_runtime_binding_repository import (
    RagRequestGuardRuntimeBindingRepository,
)
from app.repositories.rag_runtime_repository import (
    AiJobExecutionContextCreate,
    AiJobExecutionIdentificationCreate,
    RagRuntimeRepository,
)
from app.services.guide_retrieval_binding import load_verified_guide_retrieval_binding_manifest
from app.services.guide_runtime_request import (
    GuideRuntimeRequestCarrier,
    load_verified_guide_runtime_request_carrier,
)
from app.services.rag_preflight import RagPreflightService
from app.services.rag_runtime_bundle_build import verify_persisted_bundle_manifest_hash
from rag_runtime.request_authority import (
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
)
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    RequestGuardRuntimeBindingRef,
)


@dataclass(frozen=True, slots=True)
class GuideSyncRuntimeAuthority:
    runtime_environment_id: UUID
    request_guard_runtime_binding_ref: RequestGuardRuntimeBindingRef


@dataclass(frozen=True, slots=True)
class GuideSyncRuntimePreparation:
    job: AiJob
    carrier: GuideRuntimeRequestCarrier


class GuideSyncRuntimePreparationError(RuntimeError):
    """The persisted authorities cannot identify one exact Sync runtime."""


class GuideSyncRuntimeLifecycleProducer:
    def __init__(
        self,
        *,
        job_repository: AsyncJobRepository,
        runtime_repository: RagRuntimeRepository,
        preflight_service: RagPreflightService,
    ) -> None:
        self._job_repository = job_repository
        self._runtime_repository = runtime_repository
        self._preflight_service = preflight_service

    async def prepare(
        self,
        *,
        user: User,
        guide: Guide,
        authority: GuideSyncRuntimeAuthority,
    ) -> GuideSyncRuntimePreparation:
        if guide.ai_job_id is not None:
            raise GuideSyncRuntimePreparationError("Guide already has a runtime execution identity")

        environment = await self._runtime_repository.get_environment_by_id(authority.runtime_environment_id)
        if (
            environment is None
            or environment.environment_status is not RagRuntimeEnvironmentStatus.ACTIVE
            or environment.active_bundle_id is None
            or environment.active_bundle_manifest_hash is None
        ):
            raise GuideSyncRuntimePreparationError("explicit runtime environment is not active")

        if not await verify_persisted_bundle_manifest_hash(
            self._runtime_repository.session,
            environment.active_bundle_id,
        ):
            raise GuideSyncRuntimePreparationError("active runtime bundle failed canonical verification")

        bundle = await self._runtime_repository.get_release_bundle_by_id(environment.active_bundle_id)
        if (
            bundle is None
            or bundle.bundle_manifest_hash != environment.active_bundle_manifest_hash
            or bundle.environment_code != environment.environment_code
        ):
            raise GuideSyncRuntimePreparationError("active runtime bundle does not match the environment pointer")

        execution_manifest = await self._runtime_repository.get_execution_manifest_by_id(bundle.execution_manifest_id)
        if execution_manifest is None:
            raise GuideSyncRuntimePreparationError("runtime execution manifest is unavailable")

        binding_rows = await self._runtime_repository.list_guide_retrieval_binding_manifests_for_runtime(
            runtime_release_bundle_id=bundle.id,
            runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
            runtime_execution_manifest_id=execution_manifest.id,
            runtime_execution_manifest_hash=execution_manifest.manifest_hash,
        )
        if len(binding_rows) != 1:
            raise GuideSyncRuntimePreparationError("exactly one Guide retrieval binding is required")
        binding_row = binding_rows[0]
        binding = await load_verified_guide_retrieval_binding_manifest(
            self._runtime_repository.session,
            binding_row.id,
            binding_row.manifest_hash,
        )
        if binding is None:
            raise GuideSyncRuntimePreparationError("Guide retrieval binding failed canonical verification")

        guard = await RagRequestGuardRuntimeBindingRepository(self._runtime_repository.session).get_exact(
            authority.request_guard_runtime_binding_ref
        )
        guard = self._require_request_guard(
            guard=guard,
            user=user,
            environment=environment,
            bundle=bundle,
        )

        preflight = await self._preflight_service.ensure_all_active_medications_matched(
            prescription_id=guide.prescription_id,
            user_id=user.id,
            expected_prescription_version_id=guide.prescription_version_id,
        )

        async with self._runtime_repository.session.begin_nested():
            job = await self._job_repository.create_job(
                user_id=user.id,
                job_type=AiJobType.GUIDE,
                prescription_version_id=guide.prescription_version_id,
            )
            guide.ai_job_id = job.id
            await self._runtime_repository.session.flush()

            context = await self._runtime_repository.create_execution_context(
                AiJobExecutionContextCreate(
                    ai_job_id=job.id,
                    guide_id=guide.id,
                    prescription_version_id=preflight.prescription_version_id,
                    runtime_environment_id=environment.id,
                    runtime_environment_revision=environment.environment_revision,
                    runtime_release_bundle_id=bundle.id,
                    runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
                    runtime_execution_manifest_id=execution_manifest.id,
                    runtime_execution_manifest_hash=execution_manifest.manifest_hash,
                    guide_retrieval_binding_manifest_id=binding_row.id,
                    guide_retrieval_binding_manifest_hash=binding_row.manifest_hash,
                    runtime_guard_decision_ref=str(guard.request_guard_decision_id),
                    request_guard_runtime_binding_artifact_code=authority.request_guard_runtime_binding_ref.artifact_code,
                    request_guard_runtime_binding_artifact_version=authority.request_guard_runtime_binding_ref.version,
                    request_guard_runtime_binding_content_sha256=authority.request_guard_runtime_binding_ref.content_sha256,
                    source_scope_manifest_hash=guard.scope_manifest_hash,
                )
            )
            for matched in preflight.matched_medications:
                await self._runtime_repository.create_execution_identification(
                    AiJobExecutionIdentificationCreate(
                        execution_context_id=context.id,
                        medication_identification_id=matched.medication_identification_id,
                        prescription_version_medication_id=matched.prescription_version_medication_id,
                    )
                )

            carrier = await load_verified_guide_runtime_request_carrier(
                self._runtime_repository.session,
                job.id,
            )
            if carrier is None:
                raise GuideSyncRuntimePreparationError("persisted Sync runtime carrier failed exact readback")

        return GuideSyncRuntimePreparation(job=job, carrier=carrier)

    @staticmethod
    def _require_request_guard(
        *,
        guard: RequestGuardRuntimeBindingObservation | None,
        user: User,
        environment: RagRuntimeEnvironment,
        bundle: RagRuntimeReleaseBundle,
    ) -> RequestGuardRuntimeBindingObservation:
        if guard is None:
            raise GuideSyncRuntimePreparationError("Request Guard authority does not match the Sync Guide request")
        actual = (
            guard.actual_decision_outcome,
            guard.decision_stage,
            guard.user_id,
            guard.request_operation_code,
            guard.environment.value,
            guard.bundle_id,
            guard.bundle_manifest_hash,
        )
        expected = (
            RequestAuthorityDecisionOutcome.PASS,
            RequestAuthorityDecisionStage.REQUEST,
            user.id,
            "GUIDE_SYNC_ANSWER",
            environment.environment_code,
            bundle.id,
            bundle.bundle_manifest_hash,
        )
        if actual != expected:
            raise GuideSyncRuntimePreparationError("Request Guard authority does not match the Sync Guide request")
        return guard
