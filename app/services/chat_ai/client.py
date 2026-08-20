from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
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
    def __init__(self, client: Any) -> None:
        self._client = client

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderChatResponse:
        try:
            response = await self._client.responses.create(
                model=model,
                instructions=instructions,
                input=[{"role": "user", "content": input_json}],
                max_output_tokens=max_output_tokens,
                store=False,
                stream=False,
            )
        except APITimeoutError as error:
            raise ChatGenerationTimeoutError("Chat provider call timed out") from error
        except (APIConnectionError, RateLimitError) as error:
            raise ChatGenerationUnavailableError("Chat provider is unavailable") from error
        except APIResponseValidationError as error:
            raise ChatGenerationInvalidResponseError("Chat provider response validation failed") from error
        except APIStatusError as error:
            self._raise_for_status_error(error)

        return self._parse_response(response)

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
