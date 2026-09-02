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
    ProviderCallSpan,
    ProviderErrorCode,
    ProviderFailurePhase,
    provider_call_logger,
)
from app.services.guide_ai.exceptions import (
    GuideGenerationConfigurationError,
    GuideGenerationInvalidResponseError,
    GuideGenerationSafetyError,
    GuideGenerationTimeoutError,
    GuideGenerationUnavailableError,
)
from app.services.guide_ai.schemas import GeneratedGuideDraft, ProviderGuideResponse


class GuideProvider(Protocol):
    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderGuideResponse: ...


class OpenAIResponsesClient:
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
    ) -> ProviderGuideResponse:
        span = self._observer.start(requested_model=model)
        response = await self._request(
            span=span,
            model=model,
            instructions=instructions,
            input_json=input_json,
            max_output_tokens=max_output_tokens,
        )

        try:
            result = self._parse_response(response)
        except GuideGenerationSafetyError as error:
            error_code = (
                ProviderErrorCode.PROVIDER_SAFETY_FILTERED
                if error.rule_id == "PROVIDER_SAFETY_FILTER"
                else ProviderErrorCode.PROVIDER_REFUSAL
            )
            self._observer.failed(
                span,
                ProviderFailurePhase.PROVIDER_POLICY,
                error_code,
                response=response,
                provider_response_received=True,
            )
            raise
        except GuideGenerationUnavailableError:
            self._observer.failed(
                span,
                ProviderFailurePhase.RESPONSE_VALIDATION,
                ProviderErrorCode.PROVIDER_UNAVAILABLE,
                response=response,
                provider_response_received=True,
            )
            raise
        except GuideGenerationInvalidResponseError:
            self._observer.failed(
                span,
                ProviderFailurePhase.RESPONSE_VALIDATION,
                ProviderErrorCode.PROVIDER_RESPONSE_INVALID,
                response=response,
                provider_response_received=True,
            )
            raise
        self._observer.succeeded(span, response=response, model_name=result.model_name)
        return result

    async def _request(
        self,
        *,
        span: ProviderCallSpan | None,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> Any:
        try:
            return await self._client.responses.parse(
                model=model,
                instructions=instructions,
                input=[{"role": "user", "content": input_json}],
                text_format=GeneratedGuideDraft,
                max_output_tokens=max_output_tokens,
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
            raise GuideGenerationTimeoutError("Guide provider call timed out") from error
        except APIConnectionError as error:
            self._observer.failed(
                span,
                ProviderFailurePhase.TRANSPORT_CONNECTION,
                ProviderErrorCode.PROVIDER_CONNECTION_FAILED,
            )
            raise GuideGenerationUnavailableError("Guide provider is unavailable") from error
        except RateLimitError as error:
            self._observer.failed_http_status(span, error, ProviderErrorCode.PROVIDER_RATE_LIMITED)
            raise GuideGenerationUnavailableError("Guide provider is unavailable") from error
        except APIResponseValidationError as error:
            self._observer.failed_response_validation(span, error)
            raise GuideGenerationInvalidResponseError("Guide provider response validation failed") from error
        except ValidationError as error:
            self._observer.failed(
                span,
                ProviderFailurePhase.RESPONSE_VALIDATION,
                ProviderErrorCode.PROVIDER_RESPONSE_INVALID,
                provider_response_received=True,
            )
            raise GuideGenerationInvalidResponseError("Guide provider structured output is invalid") from error
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

    @staticmethod
    def _raise_for_status_error(error: APIStatusError) -> None:
        if error.status_code in {408, 409, 429} or error.status_code >= 500:
            raise GuideGenerationUnavailableError("Guide provider is unavailable") from error
        raise GuideGenerationConfigurationError("Guide provider configuration is invalid") from error

    @staticmethod
    def _parse_response(response: Any) -> ProviderGuideResponse:
        output = getattr(response, "output", None)
        OpenAIResponsesClient._require_completed_response(response, output)
        message = OpenAIResponsesClient._require_single_completed_message(output)
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            raise GuideGenerationInvalidResponseError("Guide provider returned an unexpected content structure")
        if len(content) != 1 or getattr(content[0], "type", None) != "output_text":
            raise GuideGenerationInvalidResponseError("Guide provider returned no single parsed draft")
        draft = getattr(content[0], "parsed", None)
        if not isinstance(draft, GeneratedGuideDraft):
            raise GuideGenerationInvalidResponseError("Guide provider returned no single parsed draft")

        model_name = getattr(response, "model", None)
        if not isinstance(model_name, str):
            raise GuideGenerationInvalidResponseError("Guide provider returned no model identifier")

        return ProviderGuideResponse(
            draft=draft,
            model_name=model_name,
        )

    @staticmethod
    def _require_completed_response(response: Any, output: Any) -> None:
        if OpenAIResponsesClient._contains_refusal(output):
            raise GuideGenerationSafetyError("PROVIDER_REFUSAL")
        if getattr(response, "status", None) == "completed":
            return

        provider_error = getattr(response, "error", None)
        if getattr(provider_error, "code", None) in {"server_error", "rate_limit_exceeded"}:
            raise GuideGenerationUnavailableError("Guide provider is unavailable")
        incomplete_details = getattr(response, "incomplete_details", None)
        if getattr(incomplete_details, "reason", None) == "content_filter":
            raise GuideGenerationSafetyError("PROVIDER_SAFETY_FILTER")
        raise GuideGenerationInvalidResponseError("Guide provider response was incomplete")

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

    @staticmethod
    def _require_single_completed_message(output: Any) -> Any:
        if not isinstance(output, list) or len(output) != 1 or getattr(output[0], "type", None) != "message":
            raise GuideGenerationInvalidResponseError("Guide provider returned an unexpected output structure")
        if getattr(output[0], "status", None) != "completed":
            raise GuideGenerationInvalidResponseError("Guide provider message was incomplete")
        return output[0]
