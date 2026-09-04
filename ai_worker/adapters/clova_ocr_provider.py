"""기존 CLOVA OCR Engine을 Worker Provider 계약에 연결합니다."""

import asyncio
import math
import time
from collections.abc import Callable

from ai_worker.tasks.ocr.handler import (
    OcrProviderInputError,
    OcrProviderResult,
    OcrProviderSafetyError,
    OcrProviderSchemaError,
    OcrRecognizedField,
)
from ai_worker.tasks.ocr.handler import (
    OcrProviderTimeoutError as WorkerOcrProviderTimeoutError,
)
from ai_worker.tasks.ocr.handler import (
    OcrProviderUnavailableError as WorkerOcrProviderUnavailableError,
)
from provider_contracts.ocr import (
    OcrDeadline,
    OcrDeadlineExceededError,
    OcrEngine,
    OcrProcessingError,
    OcrProviderConnectionError,
    OcrProviderTimeoutError,
    OcrProviderUnavailableError,
    OcrRecognitionResult,
)

OcrEngineFactory = Callable[[str], OcrEngine]


class ClovaOcrProviderAdapter:
    """기존 OCR Engine 결과와 오류를 Worker 계약으로 변환합니다."""

    _SUPPORTED_FILE_MIME_TYPES = frozenset(
        {
            "image/jpeg",
            "image/png",
            "application/pdf",
        }
    )
    _SUPPORTED_FIELD_TYPES = frozenset(
        {
            "MEDICATION_NAME",
            "MEDICATION_STRENGTH",
            "DOSE_VALUE",
            "DOSE_UNIT",
            "FREQUENCY_PER_DAY",
            "TIMING",
            "PRESCRIBED_DATE",
            "DURATION_DAYS",
        }
    )

    def __init__(
        self,
        engine: OcrEngine | None = None,
        *,
        engine_factory: OcrEngineFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if (engine is None) == (engine_factory is None):
            raise ValueError("engine과 engine_factory 중 정확히 하나가 필요합니다.")

        self._engine = engine
        self._engine_factory = engine_factory
        self._clock = clock

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        trace_id: str,
        deadline: float,
    ) -> OcrProviderResult:
        """최소 입력만 전달하고 원문을 제외한 결과를 반환합니다."""

        if not object_key.strip() or file_mime_type not in self._SUPPORTED_FILE_MIME_TYPES:
            raise OcrProviderInputError()

        engine = self._resolve_engine(trace_id)

        engine_result: OcrRecognitionResult | None = None
        normalized_error: Exception | None = None

        try:
            engine_result = await self._recognize_before_deadline(
                engine=engine,
                object_key=object_key,
                file_mime_type=file_mime_type,
                deadline=deadline,
            )
        except (
            TimeoutError,
            OcrDeadlineExceededError,
            OcrProviderTimeoutError,
        ):
            normalized_error = WorkerOcrProviderTimeoutError()
        except (
            OcrProviderConnectionError,
            OcrProviderUnavailableError,
        ):
            normalized_error = WorkerOcrProviderUnavailableError()
        except OcrProcessingError:
            normalized_error = OcrProviderSchemaError()

        # Provider 예외의 원문, Secret, object key가 예외 체인에 남지 않게 합니다.
        if normalized_error is not None:
            raise normalized_error

        if engine_result is None:
            raise OcrProviderSchemaError()

        return self._normalize_result(engine_result)

    async def _recognize_before_deadline(
        self,
        *,
        engine: OcrEngine,
        object_key: str,
        file_mime_type: str,
        deadline: float,
    ) -> OcrRecognitionResult:
        """Engine 전체 실행을 absolute deadline 안으로 제한합니다."""

        remaining_seconds = deadline - self._clock()

        if remaining_seconds <= 0:
            raise TimeoutError

        async with asyncio.timeout(remaining_seconds):
            return await engine.recognize(
                object_key=object_key,
                file_mime_type=file_mime_type,
                deadline=OcrDeadline(
                    provider_path_deadline=deadline,
                ),
            )

    def _resolve_engine(self, trace_id: str) -> OcrEngine:
        if self._engine is not None:
            return self._engine

        if self._engine_factory is None:
            raise RuntimeError("OCR engine factory가 설정되지 않았습니다.")

        return self._engine_factory(trace_id)

    @classmethod
    def _normalize_result(
        cls,
        result: OcrRecognitionResult,
    ) -> OcrProviderResult:
        """Provider 결과를 검증하고 저장 가능한 형태로 변환합니다."""

        identities: set[tuple[int, str]] = set()
        normalized_fields: list[OcrRecognizedField] = []

        for field in result.fields:
            cls._validate_field(
                medication_index=field.medication_index,
                field_type=field.field_type,
                confidence_score=field.confidence_score,
                identities=identities,
            )
            normalized_fields.append(
                OcrRecognizedField(
                    medication_index=field.medication_index,
                    field_type=field.field_type,
                    raw_value=field.raw_value,
                    confidence_score=field.confidence_score,
                    normalized_value=field.normalized_value,
                    normalization_version=field.normalization_version,
                )
            )

        return OcrProviderResult(
            fields=tuple(normalized_fields),
            engine_name=result.engine_name,
            model_version=result.model_version,
            prompt_version=result.prompt_version,
        )

    @classmethod
    def _validate_field(
        cls,
        *,
        medication_index: int,
        field_type: str,
        confidence_score: float | None,
        identities: set[tuple[int, str]],
    ) -> None:
        """DB 제약에 맞지 않는 OCR 결과를 저장 전에 차단합니다."""

        if isinstance(medication_index, bool) or not isinstance(
            medication_index,
            int,
        ):
            raise OcrProviderSchemaError()

        if field_type not in cls._SUPPORTED_FIELD_TYPES:
            raise OcrProviderSchemaError()

        if field_type == "PRESCRIBED_DATE":
            if medication_index != 0:
                raise OcrProviderSchemaError()
        elif medication_index <= 0:
            raise OcrProviderSchemaError()

        if confidence_score is not None and (
            isinstance(confidence_score, bool)
            or not isinstance(confidence_score, int | float)
            or not math.isfinite(float(confidence_score))
            or not 0 <= confidence_score <= 1
        ):
            raise OcrProviderSafetyError()

        identity = (medication_index, field_type)

        if identity in identities:
            raise OcrProviderSchemaError()

        identities.add(identity)
