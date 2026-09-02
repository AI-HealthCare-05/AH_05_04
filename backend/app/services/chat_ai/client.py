import asyncio
from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

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
from app.services.chat_ai.exceptions import (
    ChatGenerationConfigurationError,
    ChatGenerationInvalidResponseError,
    ChatGenerationTimeoutError,
    ChatGenerationUnavailableError,
)
from app.services.chat_ai.schemas import ProviderChatResponse


class ChatProvider(Protocol):
    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderChatResponse: ...


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
    ) -> ProviderChatResponse:
        span = self._observer.start(requested_model=model)
        try:
            response = await self._client.responses.create(
                model=model,
                instructions=instructions,
                input=[{"role": "user", "content": input_json}],
                max_output_tokens=max_output_tokens,
                store=False,
                stream=False,
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
            raise ChatGenerationTimeoutError("Chat provider call timed out") from error
        except APIConnectionError as error:
            self._observer.failed(
                span,
                ProviderFailurePhase.TRANSPORT_CONNECTION,
                ProviderErrorCode.PROVIDER_CONNECTION_FAILED,
            )
            raise ChatGenerationUnavailableError("Chat provider is unavailable") from error
        except RateLimitError as error:
            self._observer.failed_http_status(span, error, ProviderErrorCode.PROVIDER_RATE_LIMITED)
            raise ChatGenerationUnavailableError("Chat provider is unavailable") from error
        except APIResponseValidationError as error:
            self._observer.failed_response_validation(span, error)
            raise ChatGenerationInvalidResponseError("Chat provider response validation failed") from error
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

        result = self._parse_observed_response(span, response)
        self._observer.succeeded(span, response=response, model_name=result.model_name)
        return result

    def _parse_observed_response(
        self,
        span: ProviderCallSpan | None,
        response: Any,
    ) -> ProviderChatResponse:
        try:
            return self._parse_response(response)
        except ChatGenerationUnavailableError:
            self._observer.failed(
                span,
                ProviderFailurePhase.RESPONSE_VALIDATION,
                ProviderErrorCode.PROVIDER_UNAVAILABLE,
                response=response,
                provider_response_received=True,
            )
            raise
        except ChatGenerationInvalidResponseError:
            refusal = self._contains_refusal(getattr(response, "output", None))
            incomplete = getattr(response, "incomplete_details", None)
            filtered = getattr(incomplete, "reason", None) == "content_filter"
            self._observer.failed(
                span,
                ProviderFailurePhase.PROVIDER_POLICY
                if refusal or filtered
                else ProviderFailurePhase.RESPONSE_VALIDATION,
                (
                    ProviderErrorCode.PROVIDER_REFUSAL
                    if refusal
                    else (
                        ProviderErrorCode.PROVIDER_SAFETY_FILTERED
                        if filtered
                        else ProviderErrorCode.PROVIDER_RESPONSE_INVALID
                    )
                ),
                response=response,
                provider_response_received=True,
            )
            raise
        except Exception:
            self._observer.failed(
                span,
                ProviderFailurePhase.UNKNOWN_INTERNAL,
                ProviderErrorCode.PROVIDER_INTERNAL_FAILURE,
                response=response,
                provider_response_received=True,
            )
            raise

    @staticmethod
    def _raise_for_status_error(error: APIStatusError) -> None:
        if error.status_code in {408, 409, 429} or error.status_code >= 500:
            raise ChatGenerationUnavailableError("Chat provider is unavailable") from error
        raise ChatGenerationConfigurationError("Chat provider configuration is invalid") from error

    @staticmethod
    def _parse_response(response: Any) -> ProviderChatResponse:
        output = getattr(response, "output", None)
        if OpenAIResponsesClient._contains_refusal(output):
            raise ChatGenerationInvalidResponseError("Chat provider refused the response")

        provider_error = getattr(response, "error", None)
        if getattr(provider_error, "code", None) in {"server_error", "rate_limit_exceeded"}:
            raise ChatGenerationUnavailableError("Chat provider is unavailable")

        if getattr(response, "status", None) != "completed":
            raise ChatGenerationInvalidResponseError("Chat provider response was incomplete")

        content = getattr(response, "output_text", None)
        if not isinstance(content, str) or not content.strip():
            raise ChatGenerationInvalidResponseError("Chat provider returned no answer text")

        model_name = getattr(response, "model", None)
        if not isinstance(model_name, str):
            raise ChatGenerationInvalidResponseError("Chat provider returned no model identifier")

        return ProviderChatResponse(content=content.strip(), model_name=model_name)

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
