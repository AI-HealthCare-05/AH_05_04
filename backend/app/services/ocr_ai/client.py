from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from pydantic import ValidationError

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

    def __init__(self, client: Any) -> None:
        self._client = client

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderOcrStructureResponse:
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
        except APITimeoutError as error:
            raise OcrProviderTimeoutError("OCR 구조화 AI 응답 제한시간을 초과했습니다.") from error
        except (APIConnectionError, RateLimitError) as error:
            raise OcrProviderUnavailableError("OCR 구조화 AI 서비스를 사용할 수 없습니다.") from error
        except (APIResponseValidationError, ValidationError) as error:
            raise OcrProcessingError("OCR 구조화 AI 응답 형식이 올바르지 않습니다.") from error
        except APIStatusError as error:
            self._raise_for_status_error(error)

        return self._parse_response(response)

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
