"""Concrete OpenAI adapter implementing GuidelineGeneratorPort.

Consumes GuidelineGenerationRequest, minimizes and projects medication/evidence into
opaque slots, calls OpenAI Responses API with strict Structured Output, deterministic
dual timeout, max_retries=0 enforcement, and standard provider_runtime observability.
"""

from __future__ import annotations

import asyncio
from typing import Any

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from pydantic import ValidationError

from ai_worker.tasks.rag.guideline_card import (
    GuidelineGenerationFailure,
    GuidelineGenerationProvenance,
)
from ai_worker.tasks.rag.guideline_generator import (
    GuidelineGenerationRequest,
    GuidelineGenerationResult,
    GuidelineGeneratorPort,
)
from ai_worker.tasks.rag.guideline_generator_prompt import (
    GUIDELINE_GENERATOR_SYSTEM_INSTRUCTIONS,
    GuidelineStructuredSelection,
    build_candidate_provenance,
    build_guideline_generation_input_projection,
    parse_guideline_structured_output,
)
from ai_worker.tasks.rag.guideline_production_evidence import ProductionGuidelineEvidenceSet
from provider_contracts.observability import (
    Provider,
    ProviderCallContext,
    ProviderCallDescriptor,
    ProviderErrorCode,
    ProviderFailurePhase,
    ProviderOperation,
)
from provider_runtime.observability import (
    ProviderCallLogger,
    ProviderCallObserver,
    provider_call_logger,
)


class OpenAIGuidelineGeneratorAdapter(GuidelineGeneratorPort):
    """Production Guideline Generator Adapter using OpenAI Responses API Structured Output."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        timeout_seconds: float,
        context: ProviderCallContext,
        max_output_tokens: int = 2048,
        call_logger: ProviderCallLogger = provider_call_logger,
        observability_disabled: bool = False,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        max_retries = getattr(client, "max_retries", None)
        if type(max_retries) is not int or max_retries != 0:
            raise ValueError("OpenAI client must have max_retries=0")
        with_options = getattr(client, "with_options", None)
        if not callable(with_options):
            raise ValueError("OpenAI client must support with_options for bounded timeout configuration")

        self._client = client
        self._model = model.strip()
        self._timeout_seconds = timeout_seconds
        self._max_output_tokens = max_output_tokens

        # Enforce SDK-level timeout and max_retries=0 on request client
        self._request_client = client.with_options(
            max_retries=0,
            timeout=timeout_seconds,
        )

        # Self-compute candidate provenance bound to runtime execution artifacts
        self._provenance = build_candidate_provenance(model=self._model)

        descriptor = ProviderCallDescriptor(
            provider=Provider.OPENAI,
            operation=ProviderOperation.GUIDE_GENERATION,
            prompt_version=self._provenance.prompt_ref.version,
        )
        self._observer = ProviderCallObserver(
            context=context,
            descriptor=descriptor,
            call_logger=call_logger,
            observability_disabled=observability_disabled,
        )

    @property
    def provenance(self) -> GuidelineGenerationProvenance:
        return self._provenance

    @staticmethod
    def _is_valid_evidence_precondition(request: GuidelineGenerationRequest) -> bool:
        """Fail closed before any Provider call if the production request is unusable.

        The #760 handoff already decided evidence authority and sufficiency, so there
        is no legacy Gate status to consult here. What the adapter checks is that it
        was actually handed production evidence with at least one selection and one
        medication, and a policy that allows at least one claim.
        """
        evidence = request.evidence
        return (
            type(evidence) is ProductionGuidelineEvidenceSet
            and bool(evidence.selections)
            and bool(request.medication_identities)
            and request.policy.maximum_claims >= 1
        )

    async def _invoke_provider_raw(
        self,
        input_json: str,
        span: Any,
    ) -> tuple[Any | None, GuidelineGenerationFailure | None]:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                response = await self._request_client.responses.parse(
                    model=self._model,
                    instructions=GUIDELINE_GENERATOR_SYSTEM_INSTRUCTIONS,
                    input=[{"role": "user", "content": input_json}],
                    text_format=GuidelineStructuredSelection,
                    max_output_tokens=self._max_output_tokens,
                    store=False,
                    temperature=0,
                    timeout=self._timeout_seconds,
                )
                return response, None
        except asyncio.CancelledError:
            self._observer.failed(
                span,
                ProviderFailurePhase.APPLICATION_DEADLINE,
                ProviderErrorCode.PROVIDER_CALL_ABORTED,
            )
            raise
        except (APITimeoutError, TimeoutError):
            self._observer.failed(
                span,
                ProviderFailurePhase.TRANSPORT_TIMEOUT,
                ProviderErrorCode.PROVIDER_TIMEOUT,
            )
            return None, GuidelineGenerationFailure.PROVIDER_TIMEOUT
        except APIConnectionError:
            self._observer.failed(
                span,
                ProviderFailurePhase.TRANSPORT_CONNECTION,
                ProviderErrorCode.PROVIDER_CONNECTION_FAILED,
            )
            return None, GuidelineGenerationFailure.DEPENDENCY_UNAVAILABLE
        except RateLimitError as error:
            self._observer.failed_http_status(span, error, ProviderErrorCode.PROVIDER_RATE_LIMITED)
            return None, GuidelineGenerationFailure.DEPENDENCY_UNAVAILABLE
        except (APIResponseValidationError, ValidationError):
            self._observer.failed(
                span,
                ProviderFailurePhase.RESPONSE_VALIDATION,
                ProviderErrorCode.PROVIDER_RESPONSE_INVALID,
                provider_response_received=True,
            )
            return None, GuidelineGenerationFailure.VALIDATION_FAILED
        except APIStatusError as error:
            if error.status_code in {408, 409, 429} or error.status_code >= 500:
                self._observer.failed_http_status(span, error, ProviderErrorCode.PROVIDER_UNAVAILABLE)
                return None, GuidelineGenerationFailure.DEPENDENCY_UNAVAILABLE
            self._observer.failed_http_status(span, error, ProviderErrorCode.PROVIDER_REQUEST_REJECTED)
            return None, GuidelineGenerationFailure.VALIDATION_FAILED
        except Exception:
            self._observer.failed(
                span,
                ProviderFailurePhase.UNKNOWN_INTERNAL,
                ProviderErrorCode.PROVIDER_INTERNAL_FAILURE,
            )
            return None, GuidelineGenerationFailure.VALIDATION_FAILED

    def _validate_response_status(
        self,
        response: Any,
        span: Any,
    ) -> GuidelineGenerationFailure | None:
        if self._contains_refusal(response):
            self._observer.failed(
                span,
                ProviderFailurePhase.PROVIDER_POLICY,
                ProviderErrorCode.PROVIDER_REFUSAL,
                response=response,
                provider_response_received=True,
            )
            return GuidelineGenerationFailure.VALIDATION_FAILED

        if self._contains_safety_filter(response):
            self._observer.failed(
                span,
                ProviderFailurePhase.PROVIDER_POLICY,
                ProviderErrorCode.PROVIDER_SAFETY_FILTERED,
                response=response,
                provider_response_received=True,
            )
            return GuidelineGenerationFailure.VALIDATION_FAILED

        status = getattr(response, "status", None)
        if status is not None and status != "completed":
            error_code = getattr(getattr(response, "error", None), "code", None)
            if error_code in {"server_error", "rate_limit_exceeded"}:
                self._observer.failed(
                    span,
                    ProviderFailurePhase.RESPONSE_VALIDATION,
                    ProviderErrorCode.PROVIDER_UNAVAILABLE,
                    response=response,
                    provider_response_received=True,
                )
                return GuidelineGenerationFailure.DEPENDENCY_UNAVAILABLE
            self._observer.failed(
                span,
                ProviderFailurePhase.RESPONSE_VALIDATION,
                ProviderErrorCode.PROVIDER_RESPONSE_INVALID,
                response=response,
                provider_response_received=True,
            )
            return GuidelineGenerationFailure.VALIDATION_FAILED

        return None

    async def generate(self, request: GuidelineGenerationRequest) -> GuidelineGenerationResult:
        """Generates a GuidelineCardDraft by selecting claims from provided evidence."""
        # 1. Defensive production evidence precondition boundary: fail closed before Provider call
        if not self._is_valid_evidence_precondition(request):
            return GuidelineGenerationFailure.VALIDATION_FAILED

        # 2. Build minimal projection & slot lookup tables
        input_json, slot_to_med, slot_to_ev = build_guideline_generation_input_projection(request)

        # 3. Start observability span
        span = self._observer.start(requested_model=self._model)

        # 4. Invoke Provider with true dual timeout
        response, failure = await self._invoke_provider_raw(input_json, span)
        if failure is not None:
            return failure

        # 5. Check refusal, safety filter, and completion status
        status_failure = self._validate_response_status(response, span)
        if status_failure is not None:
            return status_failure

        # 6. Extract structured output
        structured_selection = self._extract_parsed_selection(response)
        if structured_selection is None:
            self._observer.failed(
                span,
                ProviderFailurePhase.RESPONSE_VALIDATION,
                ProviderErrorCode.PROVIDER_RESPONSE_INVALID,
                response=response,
                provider_response_received=True,
            )
            return GuidelineGenerationFailure.VALIDATION_FAILED

        # 7. Strict deterministic parser & production evidence provenance restoration
        draft = parse_guideline_structured_output(
            structured_selection,
            slot_to_medication=slot_to_med,
            slot_to_evidence=slot_to_ev,
            maximum_claims=request.policy.maximum_claims,
        )
        if draft is None:
            self._observer.failed(
                span,
                ProviderFailurePhase.RESPONSE_VALIDATION,
                ProviderErrorCode.PROVIDER_RESPONSE_INVALID,
                response=response,
                provider_response_received=True,
            )
            return GuidelineGenerationFailure.VALIDATION_FAILED

        # 8. Provider call succeeded: emit provider model evidence on span
        model_name = getattr(response, "model", None) or self._model
        self._observer.succeeded(span, response=response, model_name=model_name)
        return draft

    @staticmethod
    def _contains_refusal(response: Any) -> bool:
        output = getattr(response, "output", None)
        if isinstance(output, list):
            for item in output:
                content = getattr(item, "content", None)
                if isinstance(content, list):
                    for part in content:
                        refusal = getattr(part, "refusal", None)
                        if isinstance(refusal, str) and bool(refusal.strip()):
                            return True
        return False

    @staticmethod
    def _contains_safety_filter(response: Any) -> bool:
        incomplete_details = getattr(response, "incomplete_details", None)
        reason = getattr(incomplete_details, "reason", None)
        return isinstance(reason, str) and reason == "content_filter"

    @staticmethod
    def _extract_parsed_selection(response: Any) -> GuidelineStructuredSelection | None:
        output_parsed = getattr(response, "output_parsed", None)
        if isinstance(output_parsed, GuidelineStructuredSelection):
            return output_parsed

        output = getattr(response, "output", None)
        if isinstance(output, list):
            for item in output:
                content = getattr(item, "content", None)
                if isinstance(content, list):
                    for part in content:
                        parsed = getattr(part, "parsed", None)
                        if isinstance(parsed, GuidelineStructuredSelection):
                            return parsed
        return None
