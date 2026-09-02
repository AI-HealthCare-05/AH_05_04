import asyncio
from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from pydantic import ValidationError

from app.core.provider_observability import (
    ProviderCallContext,
    ProviderCallDescriptor,
    ProviderCallLogger,
    ProviderCallObserver,
    ProviderErrorCode,
    ProviderFailurePhase,
    provider_call_logger,
)
from app.services.ocr_ai.schemas import (
    GeneratedPrescriptionDraft,
    ProviderOcrStructureResponse,
)
from app.services.ocr_engine import (
    OcrProcessingError,
    OcrProviderTimeoutError,
    OcrProviderUnavailableError,
)


class OcrStructureProvider(Protocol):
    """OCR token을 구조화하는 외부 AI 제공자의 공통 인터페이스입니다."""

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderOcrStructureResponse: ...


class OpenAIOcrStructureClient:
    """OpenAI Responses API Structured Outputs 호출만 담당합니다."""

    def __init__(
        self,
        client: Any,
        *,
        context: ProviderCallContext | None = None,
        descriptor: ProviderCallDescriptor | None = None,
        call_logger: ProviderCallLogger = provider_call_logger,
    ) -> None:
        self._client = client
        self._observer = ProviderCallObserver(
            context=context,
            descriptor=descriptor,
            call_logger=call_logger,
        )

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderOcrStructureResponse:
        span = self._observer.start(requested_model=model)
        try:
            response = await self._client.responses.parse(
                model=model,
                instructions=instructions,
                input=[
                    {
                        "role": "user",
                        "content": input_json,
                    }
                ],
                text_format=GeneratedPrescriptionDraft,
                max_output_tokens=max_output_tokens,
                # 의료문서 OCR 결과를 OpenAI 응답 저장소에 저장하지 않습니다.
                store=False,
            )
        except asyncio.CancelledError:
            self._observer.failed(
                span,
                ProviderFailurePhase.APPLICATION_DEADLINE,
                ProviderErrorCode.PROVIDER_CALL_ABORTED,
            )
            raise
        except APITimeoutError as error:
            self._observer.failed(span, ProviderFailurePhase.TRANSPORT_TIMEOUT, ProviderErrorCode.PROVIDER_TIMEOUT)
            raise OcrProviderTimeoutError("OCR 구조화 AI 응답 제한시간을 초과했습니다.") from error
        except APIConnectionError as error:
            self._observer.failed(
                span,
                ProviderFailurePhase.TRANSPORT_CONNECTION,
                ProviderErrorCode.PROVIDER_CONNECTION_FAILED,
            )
            raise OcrProviderUnavailableError("OCR 구조화 AI 서비스를 사용할 수 없습니다.") from error
        except RateLimitError as error:
            self._observer.failed_http_status(span, error, ProviderErrorCode.PROVIDER_RATE_LIMITED)
            raise OcrProviderUnavailableError("OCR 구조화 AI 서비스를 사용할 수 없습니다.") from error
        except APIResponseValidationError as error:
            self._observer.failed_response_validation(span, error)
            raise OcrProcessingError("OCR 구조화 AI 응답 형식이 올바르지 않습니다.") from error
        except ValidationError as error:
            self._observer.failed(
                span,
                ProviderFailurePhase.RESPONSE_VALIDATION,
                ProviderErrorCode.PROVIDER_RESPONSE_INVALID,
                provider_response_received=True,
            )
            raise OcrProcessingError("OCR 구조화 AI 응답 형식이 올바르지 않습니다.") from error
        except APIStatusError as error:
            self._observer.failed_http_status(
                span,
                error,
                self._observer.error_code_for_http_status(error.status_code),
            )
            self._raise_for_status_error(error)
        except Exception:
            self._observer.failed(
                span,
                ProviderFailurePhase.UNKNOWN_INTERNAL,
                ProviderErrorCode.PROVIDER_INTERNAL_FAILURE,
            )
            raise

        try:
            result = self._parse_response(response)
        except OcrProcessingError:
            refusal = self._contains_refusal(getattr(response, "output", None))
            self._observer.failed(
                span,
                ProviderFailurePhase.PROVIDER_POLICY if refusal else ProviderFailurePhase.RESPONSE_VALIDATION,
                ProviderErrorCode.PROVIDER_REFUSAL if refusal else ProviderErrorCode.PROVIDER_RESPONSE_INVALID,
                response=response,
                provider_response_received=True,
            )
            raise
        self._observer.succeeded(span, response=response, model_name=result.model_name)
        return result

    @staticmethod
    def _raise_for_status_error(error: APIStatusError) -> None:
        if error.status_code in {408, 409, 429} or error.status_code >= 500:
            raise OcrProviderUnavailableError("OCR 구조화 AI 서비스를 사용할 수 없습니다.") from error

        raise OcrProcessingError("OCR 구조화 AI 설정 또는 요청이 올바르지 않습니다.") from error

    @staticmethod
    def _parse_response(
        response: Any,
    ) -> ProviderOcrStructureResponse:
        output = getattr(response, "output", None)

        if OpenAIOcrStructureClient._contains_refusal(output):
            raise OcrProcessingError("OCR 구조화 AI가 응답 생성을 거부했습니다.")

        if getattr(response, "status", None) != "completed":
            raise OcrProcessingError("OCR 구조화 AI 응답이 완료되지 않았습니다.")

        # responses.parse()가 Pydantic 모델로 변환한 결과입니다.
        draft = getattr(response, "output_parsed", None)

        if not isinstance(draft, GeneratedPrescriptionDraft):
            raise OcrProcessingError("OCR 구조화 AI가 유효한 구조화 결과를 반환하지 않았습니다.")

        model_name = getattr(response, "model", None)

        if not isinstance(model_name, str) or not model_name.strip():
            raise OcrProcessingError("OCR 구조화 AI 모델 식별자가 없습니다.")

        return ProviderOcrStructureResponse(
            draft=draft,
            model_name=model_name,
        )

    @staticmethod
    def _contains_refusal(output: Any) -> bool:
        if not isinstance(output, list):
            return False

        for output_item in output:
            content_items = getattr(output_item, "content", None)

            if isinstance(content_items, list) and any(
                getattr(content_item, "type", None) == "refusal" for content_item in content_items
            ):
                return True

        return False
