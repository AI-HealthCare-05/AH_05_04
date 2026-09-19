"""#806 append-only writer and exact reader for per-request runtime bindings."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_request_guard_runtime_binding import RagRequestGuardRuntimeBinding
from app.repositories.rag_request_authority_repository import (
    RagRequestAuthorityRepository,
    RequestAuthorityCorruptError,
    RequestAuthorityValidationError,
)
from app.repositories.rag_runtime_repository import RagRuntimeRepository
from rag_runtime.request_authority import (
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
)
from rag_runtime.request_guard_runtime_binding import (
    REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_CODE,
    REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_VERSION,
    RequestGuardRuntimeBindingObservation,
    RequestGuardRuntimeBindingRef,
    RequestGuardRuntimeBindingValidationError,
    compute_request_guard_runtime_binding_ref,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode

__all__ = [
    "RagRequestGuardRuntimeBindingRepository",
    "RequestGuardRuntimeBindingConflictError",
    "RequestGuardRuntimeBindingCorruptError",
    "RequestGuardRuntimeBindingValidationError",
]


class RequestGuardRuntimeBindingConflictError(Exception):
    """The immutable request instance already contains different semantic content."""


class RequestGuardRuntimeBindingCorruptError(Exception):
    """A persisted row cannot be represented by the shared pure contract."""


class RagRequestGuardRuntimeBindingRepository:
    """Caller-owned append-only persistence with exact artifact-reference reads."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, observation: RequestGuardRuntimeBindingObservation) -> RequestGuardRuntimeBindingRef:
        self._validate_observation(observation)
        await self._assert_legacy_guard_binding(observation)
        await self._assert_bundle_binding(observation)
        reference = compute_request_guard_runtime_binding_ref(observation)

        existing = await self._select_by_request_id(observation.request_guard_decision_id)
        if existing is not None:
            stored = self._observation_from_row(existing)
            if stored == observation:
                return reference
            raise RequestGuardRuntimeBindingConflictError(
                "같은 request_guard_decision_id에 다른 semantic content가 이미 저장되어 있습니다"
            )

        statement = (
            pg_insert(RagRequestGuardRuntimeBinding)
            .values(
                request_guard_decision_id=observation.request_guard_decision_id,
                artifact_code=reference.artifact_code,
                artifact_version=reference.version,
                artifact_content_sha256=reference.content_sha256,
                actual_decision_outcome=observation.actual_decision_outcome.value,
                user_id=observation.user_id,
                request_operation_code=observation.request_operation_code,
                decision_stage=observation.decision_stage.value,
                environment_code=observation.environment.value,
                bundle_id=observation.bundle_id,
                bundle_manifest_hash=observation.bundle_manifest_hash,
                request_scope_codes=list(observation.request_scope_codes),
                scope_manifest_hash=observation.scope_manifest_hash,
                legacy_request_guard_artifact_code=observation.legacy_request_authority_ref.artifact_code,
                legacy_request_guard_artifact_version=observation.legacy_request_authority_ref.version,
                legacy_request_guard_content_sha256=observation.legacy_request_authority_ref.content_sha256,
            )
            .on_conflict_do_nothing(index_elements=["request_guard_decision_id"])
            .returning(RagRequestGuardRuntimeBinding.request_guard_decision_id)
        )
        inserted = (await self._session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            return reference

        concurrent = await self._select_by_request_id(observation.request_guard_decision_id)
        if concurrent is None:
            raise RequestGuardRuntimeBindingConflictError("request runtime binding 기록에 실패했습니다")
        stored = self._observation_from_row(concurrent)
        if stored != observation:
            raise RequestGuardRuntimeBindingConflictError(
                "동시 retry가 같은 request_guard_decision_id에 다른 semantic content를 제출했습니다"
            )
        return reference

    async def get_exact(self, reference: RequestGuardRuntimeBindingRef) -> RequestGuardRuntimeBindingObservation | None:
        self._validate_reference(reference)
        row = await self._session.scalar(
            select(RagRequestGuardRuntimeBinding).where(
                RagRequestGuardRuntimeBinding.artifact_code == reference.artifact_code,
                RagRequestGuardRuntimeBinding.artifact_version == reference.version,
                RagRequestGuardRuntimeBinding.artifact_content_sha256 == reference.content_sha256,
            )
        )
        if row is None:
            return None
        observation = self._observation_from_row(row)
        if compute_request_guard_runtime_binding_ref(observation) != reference:
            raise RequestGuardRuntimeBindingCorruptError("저장된 row의 artifact identity가 일치하지 않습니다")
        return observation

    async def _assert_legacy_guard_binding(self, observation: RequestGuardRuntimeBindingObservation) -> None:
        reference = observation.legacy_request_authority_ref
        try:
            legacy = await RagRequestAuthorityRepository(self._session).get_request_guard_authority_by_artifact_ref(
                reference
            )
        except RequestAuthorityCorruptError as error:
            raise RequestGuardRuntimeBindingCorruptError("legacy #713 Guard ref가 손상되었습니다") from error
        except RequestAuthorityValidationError as error:
            raise RequestGuardRuntimeBindingValidationError("legacy #713 Guard ref 검증에 실패했습니다") from error
        if legacy is None or legacy.user_id != observation.user_id:
            raise RequestGuardRuntimeBindingValidationError("legacy #713 Guard ref가 존재하지 않거나 user가 다릅니다")
        if legacy.request_operation_code != observation.request_operation_code:
            raise RequestGuardRuntimeBindingValidationError("legacy #713 Guard operation이 다릅니다")
        if legacy.decision_stage is not observation.decision_stage:
            raise RequestGuardRuntimeBindingValidationError("legacy #713 Guard stage가 다릅니다")

    async def _assert_bundle_binding(self, observation: RequestGuardRuntimeBindingObservation) -> None:
        bundle = await RagRuntimeRepository(self._session).get_release_bundle_by_id(observation.bundle_id)
        if bundle is None:
            raise RequestGuardRuntimeBindingValidationError("Runtime Bundle이 존재하지 않습니다")
        if bundle.bundle_manifest_hash != observation.bundle_manifest_hash:
            raise RequestGuardRuntimeBindingValidationError("Runtime Bundle id/hash binding이 다릅니다")

    async def _select_by_request_id(self, request_id: UUID) -> RagRequestGuardRuntimeBinding | None:
        return await self._session.scalar(
            select(RagRequestGuardRuntimeBinding).where(
                RagRequestGuardRuntimeBinding.request_guard_decision_id == request_id
            )
        )

    @staticmethod
    def _validate_observation(observation: RequestGuardRuntimeBindingObservation) -> None:
        if type(observation) is not RequestGuardRuntimeBindingObservation:
            raise RequestGuardRuntimeBindingValidationError("observation 형식이 아닙니다")

    @staticmethod
    def _validate_reference(reference: RequestGuardRuntimeBindingRef) -> None:
        if type(reference) is not RequestGuardRuntimeBindingRef:
            raise RequestGuardRuntimeBindingValidationError("reference 형식이 아닙니다")
        if (
            reference.artifact_code != REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_CODE
            or reference.version != REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_VERSION
        ):
            raise RequestGuardRuntimeBindingValidationError("지원하지 않는 runtime binding reference입니다")
        if len(reference.content_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in reference.content_sha256
        ):
            raise RequestGuardRuntimeBindingValidationError("runtime binding reference hash가 올바르지 않습니다")

    @staticmethod
    def _observation_from_row(row: RagRequestGuardRuntimeBinding) -> RequestGuardRuntimeBindingObservation:
        try:
            return RequestGuardRuntimeBindingObservation(
                request_guard_decision_id=row.request_guard_decision_id,
                actual_decision_outcome=RequestAuthorityDecisionOutcome(row.actual_decision_outcome),
                user_id=row.user_id,
                request_operation_code=row.request_operation_code,
                decision_stage=RequestAuthorityDecisionStage(row.decision_stage),
                environment=RuntimeEnvironmentCode(row.environment_code),
                bundle_id=row.bundle_id,
                bundle_manifest_hash=row.bundle_manifest_hash,
                request_scope_codes=tuple(row.request_scope_codes),
                scope_manifest_hash=row.scope_manifest_hash,
                legacy_request_authority_ref=RequestAuthorityArtifactRef(
                    artifact_code=row.legacy_request_guard_artifact_code,
                    version=row.legacy_request_guard_artifact_version,
                    content_sha256=row.legacy_request_guard_content_sha256,
                ),
            )
        except (ValueError, TypeError, RequestGuardRuntimeBindingValidationError) as error:
            raise RequestGuardRuntimeBindingCorruptError("저장된 row가 pure contract와 어긋납니다") from error
