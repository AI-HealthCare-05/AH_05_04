from typing import Any, Protocol

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from pydantic import ValidationError

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
    def __init__(self, client: Any) -> None:
        self._client = client

    async def generate(
        self,
        *,
        model: str,
        instructions: str,
        input_json: str,
        max_output_tokens: int,
    ) -> ProviderGuideResponse:
        try:
            response = await self._client.responses.parse(
                model=model,
                instructions=instructions,
                input=[{"role": "user", "content": input_json}],
                text_format=GeneratedGuideDraft,
                max_output_tokens=max_output_tokens,
                store=False,
            )
        except APITimeoutError as error:
            raise GuideGenerationTimeoutError("Guide provider call timed out") from error
        except (APIConnectionError, RateLimitError) as error:
            raise GuideGenerationUnavailableError("Guide provider is unavailable") from error
        except APIResponseValidationError as error:
            raise GuideGenerationInvalidResponseError("Guide provider response validation failed") from error
        except ValidationError as error:
            raise GuideGenerationInvalidResponseError("Guide provider structured output is invalid") from error
        except APIStatusError as error:
            self._raise_for_status_error(error)

        return self._parse_response(response)

    @staticmethod
    def _raise_for_status_error(error: APIStatusError) -> None:
        if error.status_code in {408, 409, 429} or error.status_code >= 500:
            raise GuideGenerationUnavailableError("Guide provider is unavailable") from error
        raise GuideGenerationConfigurationError("Guide provider configuration is invalid") from error

    @staticmethod
    def _parse_response(response: Any) -> ProviderGuideResponse:
        output = getattr(response, "output", None)
        OpenAIResponsesClient._validate_response_status(response, output)
        message = OpenAIResponsesClient._get_completed_message(output)
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            raise GuideGenerationInvalidResponseError("Guide provider returned an unexpected content structure")

        parsed_items = [
            getattr(item, "parsed", None) for item in content if getattr(item, "type", None) == "output_text"
        ]
        if len(content) != 1 or len(parsed_items) != 1 or not isinstance(parsed_items[0], GeneratedGuideDraft):
            raise GuideGenerationInvalidResponseError("Guide provider returned no single parsed draft")

        model_name = getattr(response, "model", None)
        if not isinstance(model_name, str):
            raise GuideGenerationInvalidResponseError("Guide provider returned no model identifier")

        return ProviderGuideResponse(
            draft=parsed_items[0],
            model_name=model_name,
        )

    @staticmethod
    def _validate_response_status(response: Any, output: Any) -> None:
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
    def _get_completed_message(output: Any) -> Any:
        if not isinstance(output, list) or len(output) != 1 or getattr(output[0], "type", None) != "message":
            raise GuideGenerationInvalidResponseError("Guide provider returned an unexpected output structure")
        if getattr(output[0], "status", None) != "completed":
            raise GuideGenerationInvalidResponseError("Guide provider message was incomplete")
        return output[0]
