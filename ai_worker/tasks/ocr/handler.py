"""OCR WorkerMessage를 Provider 실행 결과로 변환하는 Handler입니다."""

import math
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from ai_worker.core.errors import WorkerError
from ai_worker.core.handler import HandlerExecutionContext
from ai_worker.core.results import HandlerSuccess
from ai_worker.core.retry import FailureCode
from ai_worker.schemas.messages import DomainType, JobType, WorkerMessage


@dataclass(frozen=True, slots=True)
class OcrDomainInput:
    """OCR Provider 실행에 필요한 최소 도메인 입력입니다."""

    object_key: str
    file_mime_type: str


@dataclass(frozen=True, slots=True)
class OcrRecognizedField:
    """Worker 결과 저장 계층에 전달할 정규화된 OCR 필드입니다."""

    medication_index: int
    field_type: str
    raw_value: str | None
    confidence_score: float | None
    normalized_value: str | None
    normalization_version: str | None


@dataclass(frozen=True, slots=True)
class OcrProviderResult:
    """OCR Provider Adapter가 반환하는 정규화된 결과입니다."""

    fields: tuple[OcrRecognizedField, ...]
    engine_name: str | None
    model_version: str | None
    prompt_version: str | None


class OcrProviderError(Exception):
    """OCR Provider Adapter가 노출하는 내부 오류의 기반 타입입니다."""


class OcrProviderTimeoutError(OcrProviderError):
    """OCR Provider 실행 제한시간을 초과했습니다."""


class OcrProviderUnavailableError(OcrProviderError):
    """연결 실패·rate limit·5xx 등 일시적인 Provider 장애입니다."""


class OcrProviderInputError(OcrProviderError):
    """지원하지 않는 파일 형식 등 영구 입력 오류입니다."""


class OcrProviderSchemaError(OcrProviderError):
    """Provider 응답 schema를 안전하게 해석할 수 없습니다."""


class OcrProviderSafetyError(OcrProviderError):
    """OCR 결과가 승인된 안전성 검증을 통과하지 못했습니다."""


@dataclass(frozen=True, slots=True)
class OcrHandlerSuccess(HandlerSuccess):
    """OCR 결과 저장 계층에 전달하는 성공 결과입니다."""

    domain_id: UUID
    fields: tuple[OcrRecognizedField, ...]
    engine_name: str | None
    model_version: str | None
    prompt_version: str | None


class OcrInputRepository(Protocol):
    """OCR Job에서 Provider 입력을 조회하는 저장소 계약입니다."""

    async def get_input(
        self,
        *,
        domain_id: UUID,
    ) -> OcrDomainInput | None:
        """OCR Job과 연결된 문서의 최소 실행 입력을 반환합니다."""
        ...


class OcrProvider(Protocol):
    """Worker가 사용하는 OCR Provider Adapter 계약입니다."""

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: float,
    ) -> OcrProviderResult:
        """monotonic absolute deadline 안에서 OCR을 실행합니다."""
        ...


class MonotonicClock(Protocol):
    """테스트 가능한 monotonic clock 계약입니다."""

    def __call__(self) -> float:
        """현재 monotonic 시각을 반환합니다."""
        ...


class OcrHandler:
    """OCR Job 입력을 조회하고 Provider 결과를 반환합니다."""

    handler_type = JobType.OCR

    def __init__(
        self,
        *,
        input_repository: OcrInputRepository,
        provider: OcrProvider,
        clock: MonotonicClock,
        provider_budget_seconds: float,
        completion_budget_seconds: float = 5.0,
    ) -> None:
        if (
            isinstance(provider_budget_seconds, bool)
            or not isinstance(provider_budget_seconds, int | float)
            or not math.isfinite(provider_budget_seconds)
            or provider_budget_seconds <= 0
        ):
            raise ValueError("provider_budget_seconds는 유한한 양수여야 합니다.")

        if (
            isinstance(completion_budget_seconds, bool)
            or not isinstance(completion_budget_seconds, int | float)
            or not math.isfinite(completion_budget_seconds)
            or completion_budget_seconds < 0
        ):
            raise ValueError("completion_budget_seconds는 유한한 0 이상의 값이어야 합니다.")

        self._input_repository = input_repository
        self._provider = provider
        self._clock = clock
        self._provider_budget_seconds = float(provider_budget_seconds)
        self._completion_budget_seconds = float(completion_budget_seconds)

    async def handle(
        self,
        message: WorkerMessage,
        *,
        context: HandlerExecutionContext | None = None,
    ) -> HandlerSuccess:
        """OCR 입력을 조회하고 정규화된 Provider 결과를 반환합니다."""

        if message.job_type is not JobType.OCR or message.domain_type is not DomainType.OCR_JOB:
            raise WorkerError(failure_code="UNSUPPORTED_SCHEMA")

        if context is None:
            # #233 runtime은 모든 실행에 context를 전달해야 합니다.
            raise WorkerError(failure_code="INTERNAL_ERROR")

        domain_input = await self._input_repository.get_input(
            domain_id=message.domain_id,
        )
        if domain_input is None:
            raise WorkerError(failure_code="INVALID_INPUT")

        now = self._clock()
        provider_deadline = min(
            now + self._provider_budget_seconds,
            context.worker_deadline - self._completion_budget_seconds,
        )
        if provider_deadline <= now:
            raise WorkerError(failure_code="TIMEOUT")

        provider_result = await self._recognize(
            domain_input=domain_input,
            provider_deadline=provider_deadline,
        )

        return OcrHandlerSuccess(
            event_id=message.event_id,
            job_id=message.job_id,
            handler_type=self.handler_type,
            domain_id=message.domain_id,
            fields=provider_result.fields,
            engine_name=provider_result.engine_name,
            model_version=provider_result.model_version,
            prompt_version=provider_result.prompt_version,
        )

    async def _recognize(
        self,
        *,
        domain_input: OcrDomainInput,
        provider_deadline: float,
    ) -> OcrProviderResult:
        """Provider 오류를 승인된 Worker failure code로 정규화합니다."""

        provider_result: OcrProviderResult | None = None
        failure_code: FailureCode | None = None

        try:
            provider_result = await self._provider.recognize(
                object_key=domain_input.object_key,
                file_mime_type=domain_input.file_mime_type,
                deadline=provider_deadline,
            )
        except OcrProviderTimeoutError:
            failure_code = "TIMEOUT"
        except OcrProviderUnavailableError:
            failure_code = "DEPENDENCY_UNAVAILABLE"
        except OcrProviderInputError:
            failure_code = "INVALID_INPUT"
        except OcrProviderSchemaError:
            failure_code = "UNSUPPORTED_SCHEMA"
        except OcrProviderSafetyError:
            failure_code = "SAFETY_VALIDATION_FAILED"

        # 활성 Provider 예외 처리 구간 밖에서 새 오류를 만들어
        # Provider 응답·object key·내부 예외 문구를 연결하지 않습니다.
        if failure_code is not None:
            raise WorkerError(failure_code=failure_code)

        if provider_result is None:
            raise WorkerError(failure_code="INTERNAL_ERROR")

        return provider_result
