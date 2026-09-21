from datetime import UTC, datetime
from uuid import UUID

from app.core import config
from app.core.config import is_guide_closed_demo_active
from app.core.errors import ApiError, ErrorDetail
from app.core.logger import default_logger
from app.dtos.guides import CreateGuideRequest, GuideCitationData, GuideData, GuideStatus
from app.models.guides import Guide, GuideCitation, GuideGenerationStatus
from app.models.prescriptions import PrescriptionVersion
from app.models.user_consents import ConsentPurpose
from app.models.users import User
from app.repositories.guide_repository import GuideRepository
from app.repositories.prescription_integrity import verify_loaded_version
from app.services.guide_ai import GuideGenerationInput, GuideGenerationResult, GuideGenerator, MedicationInput
from app.services.guide_ai.closed_demo_generator import (
    GuideClosedDemoGenerator,
    resolve_medication_item_seq,
)
from app.services.guide_ai.exceptions import (
    GuideGenerationSafetyError,
    GuideGenerationTimeoutError,
    GuideGenerationUnavailableError,
)
from app.services.guide_runtime_execution import GuideRuntimeExecutionUnavailableError
from app.services.guide_sync_runtime_execution import (
    GuideSyncRuntimeExecution,
    GuideSyncRuntimeVersionConflictError,
)
from app.services.guide_sync_runtime_lifecycle import GuideSyncRuntimePreparationError
from app.services.user_consents import ConsentGateService
from rag_runtime.guide_closed_demo_product_map import (
    GuideClosedDemoProductMap,
    load_guide_closed_demo_product_map,
)
from rag_runtime.guide_release_projection import (
    GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
    GuideRuntimeCitationSourceType,
    GuideRuntimeFallbackCode,
    GuideRuntimeReleaseDecision,
)

# OpenAI SDK/도메인 예외 메시지를 그대로 저장하면 요청 payload(약물 정보 등)가 노출될 수 있어
# 고정된 문구만 DB에 저장합니다.
_TIMEOUT_ERROR_MESSAGE = "OpenAI 호출이 제한 시간 내에 완료되지 않았습니다."
_UNAVAILABLE_ERROR_MESSAGE = "OpenAI 서비스 호출에 실패했습니다."
_GENERATION_FAILED_ERROR_MESSAGE = "가이드 생성 처리 중 오류가 발생했습니다."
_RUNTIME_UNAVAILABLE_ERROR_MESSAGE = "검증된 가이드 생성 결과를 준비하지 못했습니다."


def _to_guide_data(guide: Guide) -> GuideData:
    return GuideData(
        guide_id=guide.id,
        prescription_id=guide.prescription_id,
        prescription_version_id=guide.prescription_version_id,
        generation_status=GuideStatus(guide.generation_status),
        content=guide.content,
        model_name=guide.model_name,
        prompt_version=guide.prompt_version,
        release_decision=(
            GuideRuntimeReleaseDecision(guide.release_decision) if guide.release_decision is not None else None
        ),
        release_is_current=guide.release_is_current,
        fallback_code=GuideRuntimeFallbackCode(guide.fallback_code) if guide.fallback_code is not None else None,
        fallback_text=guide.fallback_text,
        citations=[_to_public_citation(citation) for citation in guide.citations if citation.source_type is not None],
        requested_at=guide.requested_at,
        completed_at=guide.completed_at,
    )


def _to_public_citation(citation: GuideCitation) -> GuideCitationData:
    source_type = citation.source_type
    source_code = citation.source_code
    source_version = citation.source_version
    locator = citation.locator
    if source_type is None or source_code is None or source_version is None or locator is None:
        raise ValueError("incomplete runtime Guide citation")
    return GuideCitationData(
        source_type=GuideRuntimeCitationSourceType(source_type),
        source_code=source_code,
        source_version=source_version,
        locator=locator,
        display_order=citation.display_order,
    )


def _ensure_current_version(guide: Guide) -> None:
    if guide.prescription_version_id != guide.prescription.active_version_id:
        raise ApiError(
            status_code=409,
            code="PRESCRIPTION_VERSION_CONFLICT",
            message="처방 정보가 변경되어 이전 가이드를 현재 결과로 사용할 수 없습니다.",
            details=[ErrorDetail(field="guide_id", reason="ACTIVE_VERSION_MISMATCH")],
        )


def _is_terminal_runtime_stale(guide: Guide) -> bool:
    return (
        guide.release_projection_version == GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION
        and guide.release_decision == GuideRuntimeReleaseDecision.STALE.value
        and guide.release_is_current is False
        and guide.generation_status == GuideGenerationStatus.COMPLETED
        and guide.completed_at is not None
    )


def _ensure_rediscoverable_version(guide: Guide) -> None:
    if not _is_terminal_runtime_stale(guide):
        _ensure_current_version(guide)


def _to_generation_input(version: PrescriptionVersion) -> GuideGenerationInput:
    return GuideGenerationInput(
        medications=[
            MedicationInput(
                medication_name=medication.medication_name,
                strength_text=medication.strength_text,
                dose_value=medication.dose_value,
                dose_unit=medication.dose_unit,
                frequency_per_day=medication.frequency_per_day,
                timing_text=medication.timing_text,
                duration_days=medication.duration_days,
            )
            for medication in version.medications
        ]
    )


class GuideService:
    def __init__(
        self,
        repository: GuideRepository,
        generator: GuideGenerator,
        consent_gate: ConsentGateService,
        runtime_execution: GuideSyncRuntimeExecution | None = None,
        closed_demo_generator: GuideClosedDemoGenerator | None = None,
        closed_demo_product_map: GuideClosedDemoProductMap | None = None,
    ) -> None:
        self._repo = repository
        self._generator = generator
        self._consent_gate = consent_gate
        self._runtime_execution = runtime_execution
        self._closed_demo_generator = closed_demo_generator
        self._closed_demo_product_map = closed_demo_product_map

    @property
    def product_map(self) -> GuideClosedDemoProductMap:
        if self._closed_demo_product_map is None:
            self._closed_demo_product_map = load_guide_closed_demo_product_map()
        return self._closed_demo_product_map

    async def _generate_guide_result(
        self,
        *,
        version: PrescriptionVersion,
        is_closed_demo: bool,
    ) -> GuideGenerationResult:
        if is_closed_demo:
            assert self._closed_demo_generator is not None
            item_seqs = {
                index: resolve_medication_item_seq(medication, self.product_map)
                for index, medication in enumerate(version.medications)
            }
            return await self._closed_demo_generator.generate(
                _to_generation_input(version),
                medication_item_seqs=item_seqs,
            )
        return await self._generator.generate(_to_generation_input(version))

    async def create_guide(
        self,
        *,
        user: User,
        request: CreateGuideRequest,
    ) -> GuideData:
        # 복약 가이드 생성 Backend 계약(one-cycle, 동기):
        # 확정 처방과 소속 약물을 조회해 같은 요청 안에서 OpenAI 가이드 생성을 완료하고 GUIDE에 저장합니다.
        prescription = await self._repo.get_prescription_owned(
            prescription_id=request.prescription_id,
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
                        rejected_value=str(request.prescription_id),
                    )
                ],
            )

        await self._consent_gate.require_for_intake(user=user, purpose=ConsentPurpose.GUIDE)

        version = prescription.active_version
        if version is None or not version.medications:
            raise ApiError(
                status_code=409,
                code="PRESCRIPTION_VERSION_UNAVAILABLE",
                message="활성 처방 버전 정보를 사용할 수 없습니다.",
                details=[ErrorDetail(field="prescription_id", reason="INVALID_VERSION_GRAPH")],
            )

        verify_loaded_version(version, version.medications)

        closed_demo_requested = is_guide_closed_demo_active(config, user.id)
        if closed_demo_requested and self._closed_demo_generator is None:
            raise ApiError(
                status_code=503,
                code="SERVICE_UNAVAILABLE",
                message="복약 가이드 데모 서비스를 현재 사용할 수 없습니다.",
                details=[ErrorDetail(field="closed_demo_generator", reason="SERVICE_UNAVAILABLE")],
            )

        guide = await self._repo.create(prescription=prescription)

        if self._runtime_execution is not None:
            return await self._create_runtime_guide(
                user=user,
                guide=guide,
                runtime_execution=self._runtime_execution,
            )

        failure_error: ApiError
        try:
            result = await self._generate_guide_result(version=version, is_closed_demo=closed_demo_requested)
        except GuideGenerationTimeoutError:
            await self._repo.mark_failed(
                guide,
                error_code="OPENAI_API_TIMEOUT",
                error_message=_TIMEOUT_ERROR_MESSAGE,
                completed_at=datetime.now(UTC),
            )
            failure_error = ApiError(
                status_code=504,
                code="GATEWAY_TIMEOUT",
                message="외부 처리 시간이 초과되었습니다. 다시 시도해 주세요.",
                details=[ErrorDetail(field="openai_api", reason="OPENAI_API_TIMEOUT")],
            )
        except GuideGenerationUnavailableError:
            await self._repo.mark_failed(
                guide,
                error_code="OPENAI_API_ERROR",
                error_message=_UNAVAILABLE_ERROR_MESSAGE,
                completed_at=datetime.now(UTC),
            )
            failure_error = ApiError(
                status_code=503,
                code="SERVICE_UNAVAILABLE",
                message="현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                details=[ErrorDetail(field="openai_api", reason="OPENAI_API_ERROR")],
            )
        except Exception as err:
            # Provider payload와 예외 본문은 기록하지 않고 분류명만 남겨 live 진단을 가능하게 합니다.
            rule_id = err.rule_id if isinstance(err, GuideGenerationSafetyError) else "NOT_APPLICABLE"
            default_logger.warning(
                "guide_generation_failed error_type=%s rule_id=%s",
                type(err).__name__,
                rule_id,
            )
            await self._repo.mark_failed(
                guide,
                error_code="GENERATION_REQUEST_FAILED",
                error_message=_GENERATION_FAILED_ERROR_MESSAGE,
                completed_at=datetime.now(UTC),
            )
            failure_error = ApiError(
                status_code=500,
                code="GUIDE_GENERATION_FAILED",
                message="복약 가이드 생성에 실패했습니다. 다시 시도해 주세요.",
                details=[ErrorDetail(field="guide", reason="GENERATION_REQUEST_FAILED")],
            )
        else:
            if not await self._repo.lock_if_current_version(guide=guide):
                await self._repo.mark_failed(
                    guide,
                    error_code="PRESCRIPTION_VERSION_STALE",
                    error_message="처방 정보가 변경되어 생성 결과를 현재 결과로 사용할 수 없습니다.",
                    completed_at=datetime.now(UTC),
                )
                raise ApiError(
                    status_code=409,
                    code="PRESCRIPTION_VERSION_CONFLICT",
                    message="처방 정보가 변경되었습니다. 최신 처방으로 다시 생성해 주세요.",
                    details=[ErrorDetail(field="prescription_id", reason="ACTIVE_VERSION_MISMATCH")],
                )
            guide = await self._repo.mark_completed(
                guide,
                content=result.content,
                model_name=result.model_name,
                prompt_version=result.prompt_version,
                completed_at=datetime.now(UTC),
            )
            return _to_guide_data(guide)

        # except handler 밖에서 raise해야 비식별 API 오류가 원본 예외를 __context__로 보유하지 않습니다.
        raise failure_error

    async def _create_runtime_guide(
        self,
        *,
        user: User,
        guide: Guide,
        runtime_execution: GuideSyncRuntimeExecution,
    ) -> GuideData:
        failure_error: ApiError
        try:
            completed = await runtime_execution.execute(user=user, guide=guide)
        except GuideSyncRuntimeVersionConflictError:
            await self._repo.mark_failed(
                guide,
                error_code="PRESCRIPTION_VERSION_STALE",
                error_message="처방 정보가 변경되어 생성 결과를 현재 결과로 사용할 수 없습니다.",
                completed_at=datetime.now(UTC),
            )
            failure_error = ApiError(
                status_code=409,
                code="PRESCRIPTION_VERSION_CONFLICT",
                message="처방 정보가 변경되었습니다. 최신 처방으로 다시 생성해 주세요.",
                details=[ErrorDetail(field="prescription_id", reason="ACTIVE_VERSION_MISMATCH")],
            )
        except (GuideSyncRuntimePreparationError, GuideRuntimeExecutionUnavailableError) as error:
            default_logger.warning("guide_runtime_unavailable error_type=%s", type(error).__name__)
            await self._repo.mark_failed(
                guide,
                error_code="GUIDE_RUNTIME_UNAVAILABLE",
                error_message=_RUNTIME_UNAVAILABLE_ERROR_MESSAGE,
                completed_at=datetime.now(UTC),
            )
            failure_error = ApiError(
                status_code=503,
                code="SERVICE_UNAVAILABLE",
                message="현재 서비스를 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
                details=[ErrorDetail(field="guide", reason="GUIDE_RUNTIME_UNAVAILABLE")],
            )
        except Exception as error:
            default_logger.warning("guide_runtime_failed error_type=%s", type(error).__name__)
            await self._repo.mark_failed(
                guide,
                error_code="GENERATION_REQUEST_FAILED",
                error_message=_GENERATION_FAILED_ERROR_MESSAGE,
                completed_at=datetime.now(UTC),
            )
            failure_error = ApiError(
                status_code=500,
                code="GUIDE_GENERATION_FAILED",
                message="복약 가이드 생성에 실패했습니다. 다시 시도해 주세요.",
                details=[ErrorDetail(field="guide", reason="GENERATION_REQUEST_FAILED")],
            )
        else:
            return _to_guide_data(completed)

        raise failure_error

    async def get_guide_detail(self, *, user: User, guide_id: UUID) -> GuideData:
        # 지원 API: 새로고침·재조회용. one-cycle 최초 생성 흐름에는 필요하지 않습니다.
        guide = await self._repo.get_owned(guide_id=guide_id, user_id=user.id)
        if guide is None:
            raise ApiError(
                status_code=404,
                code="GUIDE_NOT_FOUND",
                message="가이드를 찾을 수 없습니다.",
                details=[ErrorDetail(field="guide_id", reason="NOT_FOUND", rejected_value=str(guide_id))],
            )
        _ensure_rediscoverable_version(guide)
        return _to_guide_data(guide)

    async def get_latest_guide_for_prescription(self, *, user: User, prescription_id: UUID) -> GuideData:
        # 재접속 복구 지원 API: guide_id를 잃어버린 Frontend(로그아웃·재로그인 등)가
        # 처방 소유권만으로 가장 최근 Guide를 다시 찾을 수 있게 합니다. Guide 생성이
        # 아직 JobIntakeService.accept_job()에 연결되지 않은 동기(one-cycle) 흐름이라
        # AiJob 상태와 무관하게 단순 조회로 처리합니다 — Job 기반 rediscovery(#148)와는 별개입니다.
        guide = await self._repo.get_latest_for_prescription_owned(
            prescription_id=prescription_id,
            user_id=user.id,
        )
        if guide is None:
            raise ApiError(
                status_code=404,
                code="GUIDE_NOT_FOUND",
                message="가이드를 찾을 수 없습니다.",
                details=[ErrorDetail(field="prescription_id", reason="NOT_FOUND", rejected_value=str(prescription_id))],
            )
        _ensure_rediscoverable_version(guide)
        return _to_guide_data(guide)
