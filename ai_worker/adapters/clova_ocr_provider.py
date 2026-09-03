"""기존 CLOVA OCR Engine을 Worker Provider 계약에 연결합니다."""

import math

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

    def __init__(self, engine: OcrEngine) -> None:
        self._engine = engine

    async def recognize(
        self,
        *,
        object_key: str,
        file_mime_type: str,
        deadline: float,
    ) -> OcrProviderResult:
        """최소 입력만 전달하고 원문을 제외한 결과를 반환합니다."""

        if not object_key.strip() or file_mime_type not in self._SUPPORTED_FILE_MIME_TYPES:
            raise OcrProviderInputError()

        engine_result: OcrRecognitionResult | None = None
        normalized_error: Exception | None = None

        try:
            engine_result = await self._engine.recognize(
                object_key=object_key,
                file_mime_type=file_mime_type,
                deadline=OcrDeadline(
                    provider_path_deadline=deadline,
                ),
            )
        except (
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

        # 활성 Provider 예외 처리 구간 밖에서 새 오류를 발생시켜
        # Provider 응답·Secret·object key의 예외 연결을 제거합니다.
        if normalized_error is not None:
            raise normalized_error

        if engine_result is None:
            raise OcrProviderSchemaError()

        return self._normalize_result(engine_result)

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
