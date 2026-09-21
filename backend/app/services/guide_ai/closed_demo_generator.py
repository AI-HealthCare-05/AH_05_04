"""Guide CLOSED_DEMO RAG Generator with Structured Outputs and Plaintext Envelope Renderer.

This module implements the isolated internal CLOSED_DEMO RAG generation path:
- Exact 6-field structured output (medication_caution, food_and_drink, alcohol_and_smoking,
  possible_discomfort, seek_medical_care, pregnancy_and_breastfeeding).
- Strict evidence slot binding and safety validation.
- Faithful plaintext envelope rendering compatible with the existing Frontend GuidePage.tsx parser.
- Preservation of existing GuideGenerationResult and error contracts.
"""

from __future__ import annotations

import asyncio
import json
import math
import unicodedata
from decimal import Decimal
from typing import Any

from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.guide_closed_demo_retrieval import (
    GuideClosedDemoEvidence,
    GuideClosedDemoRetrievalService,
)
from app.core.provider_observability import (
    ProviderCallContext,
    ProviderCallDescriptor,
    ProviderCallLogger,
    ProviderCallObserver,
    ProviderErrorCode,
    ProviderFailurePhase,
    provider_call_logger,
)
from app.models.prescriptions import PrescriptionVersionMedication
from app.models.rag_candidate import MedicationIdentificationStatus
from app.services.guide_ai.exceptions import (
    GuideGenerationConfigurationError,
    GuideGenerationInvalidResponseError,
    GuideGenerationSafetyError,
    GuideGenerationTimeoutError,
    GuideGenerationUnavailableError,
)
from app.services.guide_ai.schemas import GuideGenerationInput, GuideGenerationResult, MedicationInput
from app.services.guide_ai.validators import (
    _CHANGE_TERM,
    _DIRECTIVE,
    _HTML_TAG,
    _MARKDOWN_LINK,
    _SAFE_NEGATION,
    _SENTENCE_SPLIT,
    _URL,
    _ZERO_WIDTH_OR_BIDI,
    RULE_CHANGE_DIRECTIVE,
    RULE_PRESCRIPTION_MISMATCH,
    RULE_UNSAFE_MARKUP,
    _contains_control_character,
)
from rag_runtime.guide_closed_demo_product_map import (
    GuideClosedDemoProductMap,
)


class GuideClosedDemoProductIdentityError(RuntimeError):
    """Unable to deterministically resolve medication to exact MFDS_ITEM_SEQ."""


def resolve_medication_item_seq(
    medication: PrescriptionVersionMedication,
    product_map: GuideClosedDemoProductMap,
) -> str:
    """Resolve medication to MFDS_ITEM_SEQ using 1st: MedicationIdentification, 2nd: 17p-product-map.

    Fails closed if undetermined. Never guesses or uses substring/fuzzy matching.
    """
    identifications = getattr(medication, "identifications", None) or ()
    for ident in identifications:
        if (
            getattr(ident, "status", None) == MedicationIdentificationStatus.MATCHED
            and getattr(ident, "code_system", None) == "MFDS_ITEM_SEQ"
            and getattr(ident, "canonical_code", None)
        ):
            return str(ident.canonical_code).strip()

    item_seq = product_map.resolve(medication.medication_name, medication.strength_text)
    if item_seq is not None:
        return item_seq

    raise GuideClosedDemoProductIdentityError(
        f"Unable to resolve exact MFDS product identity for medication: {medication.medication_name!r}"
    )


CLOSED_DEMO_GUIDE_PROMPT_VERSION = "guide-closed-demo-rag-v1"
INCOMPLETE_DOSE_NOTICE = "용량 정보는 처방전 또는 의료진 안내를 확인해 주세요."
SAFETY_NOTICE = "임의로 복용을 중단하거나 변경하지 말고 의료진 또는 약사와 상담해 주세요."
MAX_CONTENT_LENGTH = 10_000

RULE_EVIDENCE_SLOT_MISMATCH = "EVIDENCE_SLOT_MISMATCH"
RULE_EVIDENCE_BINDING_REQUIRED = "EVIDENCE_BINDING_REQUIRED"

GUIDE_CLOSED_DEMO_SYSTEM_INSTRUCTIONS = """\
당신은 대한민국 전문 복약 안내 AI입니다.
반드시 제공된 각 의약품의 식약처(MFDS) 공식 허가사항 근거(evidence)에 명시된 내용만을 바탕으로 환자용 복약 가이드를 작성하십시오.

[작성 원칙 및 제약 사항]
1. 각 약물별 6개 필드(medication_caution, food_and_drink, alcohol_and_smoking, possible_discomfort, seek_medical_care, pregnancy_and_breastfeeding)를 작성하십시오.
2. 제공된 근거(evidence)에 해당 내용이 명시되어 있는 경우에만 간결하고 명확한 한국어 안내 문장을 text에 작성하고, 참고한 근거의 slot 번호를 evidence_slots에 정수 리스트로 기록하십시오.
3. 제공된 근거에 해당 내용이 없거나 부족한 경우 절대로 추측하여 작성하지 말고 반드시 text를 null로, evidence_slots를 빈 리스트([])로 두십시오.
4. 의사의 처방을 임의로 중단하거나 변경하도록 지시하지 마십시오(복용 중단, 끊기, 용량 증감, 복용 횟수 변경, 임의 복용 시점 추가 절대 금지).
5. 질병 진단, 치료 결과 확언, 응급 여부 판단을 내리지 마십시오.
6. 해외 의료기관, 해외 전화번호, URL 링크, HTML 태그, 마크다운 링크를 포함하지 마십시오.
7. general_notice에는 처방된 복약 시간과 일정을 준수하라는 취지의 공통 복약 안내 1문장을 작성하십시오.
"""


class ClosedDemoGuidanceField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = None
    evidence_slots: list[int] = Field(default_factory=list)


class ClosedDemoMedicationGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_index: int
    medication_caution: ClosedDemoGuidanceField
    food_and_drink: ClosedDemoGuidanceField
    alcohol_and_smoking: ClosedDemoGuidanceField
    possible_discomfort: ClosedDemoGuidanceField
    seek_medical_care: ClosedDemoGuidanceField
    pregnancy_and_breastfeeding: ClosedDemoGuidanceField


class ClosedDemoGuideDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    medications: list[ClosedDemoMedicationGuidance]
    general_notice: str


def _format_decimal(value: Decimal) -> str:
    formatted = format(value, "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def _validate_closed_demo_text(text: str) -> None:
    normalized = unicodedata.normalize("NFC", text)
    if (
        _ZERO_WIDTH_OR_BIDI.search(normalized)
        or _HTML_TAG.search(normalized)
        or _MARKDOWN_LINK.search(normalized)
        or _URL.search(normalized)
        or _contains_control_character(normalized)
    ):
        raise GuideGenerationSafetyError(RULE_UNSAFE_MARKUP)

    for sentence in filter(None, _SENTENCE_SPLIT.split(normalized)):
        if _CHANGE_TERM.search(sentence) and (_DIRECTIVE.search(sentence) or not _SAFE_NEGATION.search(sentence)):
            raise GuideGenerationSafetyError(RULE_CHANGE_DIRECTIVE)


def validate_closed_demo_draft(
    draft: ClosedDemoGuideDraft,
    *,
    expected_count: int,
    evidences_by_index: dict[int, tuple[GuideClosedDemoEvidence, ...]],
) -> None:
    """Validate structured output: slot binding coherence and safety rules."""
    if len(draft.medications) != expected_count:
        raise GuideGenerationSafetyError(RULE_PRESCRIPTION_MISMATCH)

    seen_indices: set[int] = set()
    for med in draft.medications:
        if med.source_index in seen_indices or med.source_index not in evidences_by_index:
            raise GuideGenerationSafetyError(RULE_PRESCRIPTION_MISMATCH)
        seen_indices.add(med.source_index)

        valid_slots = {e.slot for e in evidences_by_index[med.source_index]}
        fields = (
            med.medication_caution,
            med.food_and_drink,
            med.alcohol_and_smoking,
            med.possible_discomfort,
            med.seek_medical_care,
            med.pregnancy_and_breastfeeding,
        )
        for field in fields:
            if field.text is not None:
                cleaned = field.text.strip()
                if not cleaned:
                    raise GuideGenerationSafetyError(RULE_EVIDENCE_BINDING_REQUIRED)
                if not field.evidence_slots:
                    raise GuideGenerationSafetyError(RULE_EVIDENCE_BINDING_REQUIRED)
                if not set(field.evidence_slots).issubset(valid_slots):
                    raise GuideGenerationSafetyError(RULE_EVIDENCE_SLOT_MISMATCH)
                _validate_closed_demo_text(cleaned)
            else:
                if field.evidence_slots:
                    raise GuideGenerationSafetyError(RULE_EVIDENCE_SLOT_MISMATCH)

    _validate_closed_demo_text(draft.general_notice)


def _render_medication_section(
    index: int,
    medication: MedicationInput,
    med_guidance: ClosedDemoMedicationGuidance,
) -> str:
    display_name = medication.medication_name
    if medication.strength_text is not None:
        display_name = f"{display_name} {medication.strength_text}"
    lines = [f"[{index + 1}] {display_name}"]

    if medication.dose_value is not None and medication.dose_unit is not None:
        lines.append(f"1회량: {_format_decimal(medication.dose_value)} {medication.dose_unit}")
    elif medication.dose_value is not None or medication.dose_unit is not None:
        lines.append(INCOMPLETE_DOSE_NOTICE)

    if medication.frequency_per_day is not None:
        lines.append(f"하루 횟수: 하루 {medication.frequency_per_day}회")
    if medication.timing_text is not None:
        lines.append(f"복용 시점: {medication.timing_text}")
    if medication.duration_days is not None:
        lines.append(f"복용 기간: {medication.duration_days}일")

    field_mappings = [
        ("복용 시 주의해야 할 점", med_guidance.medication_caution.text),
        ("주의해야 할 음식·음료", med_guidance.food_and_drink.text),
        ("음주/흡연 안내", med_guidance.alcohol_and_smoking.text),
        ("나타날 수 있는 불편감", med_guidance.possible_discomfort.text),
        ("이런 증상은 병원에 가세요", med_guidance.seek_medical_care.text),
        ("임신·수유 중 안내", med_guidance.pregnancy_and_breastfeeding.text),
    ]
    for label, val in field_mappings:
        if val is not None and val.strip():
            lines.append(f"{label}: {val.strip()}")

    return "\n".join(lines)


def render_closed_demo_plaintext_guide(
    guide_input: GuideGenerationInput,
    draft: ClosedDemoGuideDraft,
) -> str:
    """Render the exact plaintext envelope expected by GuidePage.tsx parser.

    Any section where text is null is omitted entirely from the output.
    """
    guidance_by_index = {item.source_index: item for item in draft.medications}
    sections = ["복약 가이드"]

    for index, medication in enumerate(guide_input.medications):
        sections.append(_render_medication_section(index, medication, guidance_by_index[index]))

    sections.append(f"공통 안내: {draft.general_notice.strip()}\n안전 안내: {SAFETY_NOTICE}")
    content = "\n\n".join(sections)
    if not content or len(content) > MAX_CONTENT_LENGTH:
        raise GuideGenerationInvalidResponseError("Rendered guide content has an invalid length")
    return content


class GuideClosedDemoGenerator:
    """Thin generator for Guide CLOSED_DEMO RAG generation."""

    def __init__(
        self,
        *,
        client: AsyncOpenAI,
        model: str,
        timeout_seconds: float,
        retriever: GuideClosedDemoRetrievalService,
        context: ProviderCallContext | None = None,
        descriptor: ProviderCallDescriptor | None = None,
        call_logger: ProviderCallLogger = provider_call_logger,
        observability_disabled: bool = False,
    ) -> None:
        if not model.strip() or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise GuideGenerationConfigurationError("Guide generation configuration is invalid")
        self._client = client
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._retriever = retriever
        if context is None and descriptor is None:
            observability_disabled = True
        self._observer = ProviderCallObserver(
            context=context,
            descriptor=descriptor,
            call_logger=call_logger,
            observability_disabled=observability_disabled,
        )

    async def _retrieve_all_evidences(
        self,
        guide_input: GuideGenerationInput,
        medication_item_seqs: dict[int, str],
    ) -> dict[int, tuple[GuideClosedDemoEvidence, ...]]:
        evidences_by_index: dict[int, tuple[GuideClosedDemoEvidence, ...]] = {}
        for index, med in enumerate(guide_input.medications):
            item_seq = medication_item_seqs.get(index)
            if not item_seq:
                raise GuideGenerationConfigurationError(f"Missing item_seq for medication at index {index}")
            query_text = f"{med.medication_name} {med.strength_text or ''}".strip()
            evidences = await self._retriever.retrieve_exact_evidence(
                query_text=query_text,
                expected_item_seq=item_seq,
            )
            evidences_by_index[index] = evidences
        return evidences_by_index

    async def _call_provider_parse(
        self,
        span: Any,
        input_payload: str,
        medication_count: int,
    ) -> Any:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._client.responses.parse(
                    model=self._model,
                    instructions=GUIDE_CLOSED_DEMO_SYSTEM_INSTRUCTIONS,
                    input=[{"role": "user", "content": input_payload}],
                    text_format=ClosedDemoGuideDraft,
                    max_output_tokens=600 + 400 * medication_count,
                    store=False,
                    temperature=0,
                )
        except TimeoutError as error:
            self._observer.failed(span, ProviderFailurePhase.TRANSPORT_TIMEOUT, ProviderErrorCode.PROVIDER_TIMEOUT)
            raise GuideGenerationTimeoutError("Guide provider call timed out") from error
        except APITimeoutError as error:
            self._observer.failed(span, ProviderFailurePhase.TRANSPORT_TIMEOUT, ProviderErrorCode.PROVIDER_TIMEOUT)
            raise GuideGenerationTimeoutError("Guide provider call timed out") from error
        except (APIConnectionError, RateLimitError) as error:
            self._observer.failed(
                span,
                ProviderFailurePhase.TRANSPORT_CONNECTION,
                ProviderErrorCode.PROVIDER_CONNECTION_FAILED,
            )
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
            if error.status_code in {408, 409, 429} or error.status_code >= 500:
                raise GuideGenerationUnavailableError("Guide provider is unavailable") from error
            raise GuideGenerationConfigurationError("Guide provider configuration is invalid") from error
        except Exception:
            self._observer.failed(
                span,
                ProviderFailurePhase.UNKNOWN_INTERNAL,
                ProviderErrorCode.PROVIDER_INTERNAL_FAILURE,
            )
            raise

    async def generate(
        self,
        guide_input: GuideGenerationInput,
        medication_item_seqs: dict[int, str],
    ) -> GuideGenerationResult:
        """Retrieve exact-product evidence for each medication and generate structured guide."""
        # 1. Retrieve exact product evidence for each medication
        evidences_by_index = await self._retrieve_all_evidences(guide_input, medication_item_seqs)

        # 2. Build provider input payload
        input_payload = self._build_provider_input(guide_input, evidences_by_index)

        # 3. Call OpenAI responses.parse with ClosedDemoGuideDraft structured output
        span = self._observer.start(requested_model=self._model)
        response = await self._call_provider_parse(span, input_payload, len(guide_input.medications))
        draft = self._extract_draft(response)
        model_name = getattr(response, "model", self._model)

        # 4. Validate evidence binding and safety rules
        validate_closed_demo_draft(
            draft,
            expected_count=len(guide_input.medications),
            evidences_by_index=evidences_by_index,
        )

        # 5. Render plaintext guide envelope for Frontend
        content = render_closed_demo_plaintext_guide(guide_input, draft)
        return GuideGenerationResult(
            content=content,
            model_name=model_name,
            prompt_version=CLOSED_DEMO_GUIDE_PROMPT_VERSION,
        )

    @staticmethod
    def _extract_draft(response: Any) -> ClosedDemoGuideDraft:
        output = getattr(response, "output", None)
        if not output:
            raise GuideGenerationInvalidResponseError("Guide provider returned no output")
        content = getattr(output[0], "content", None) if isinstance(output, list) else None
        if not content or not isinstance(content, list):
            raise GuideGenerationInvalidResponseError("Guide provider returned unexpected content structure")
        parsed = getattr(content[0], "parsed", None)
        if not isinstance(parsed, ClosedDemoGuideDraft):
            raise GuideGenerationInvalidResponseError("Guide provider returned no valid parsed draft")
        return parsed

    @staticmethod
    def _build_provider_input(
        guide_input: GuideGenerationInput,
        evidences_by_index: dict[int, tuple[GuideClosedDemoEvidence, ...]],
    ) -> str:
        medications_data = []
        for index, med in enumerate(guide_input.medications):
            med_evidences = evidences_by_index.get(index, ())
            medications_data.append(
                {
                    "source_index": index,
                    "medication_name": med.medication_name,
                    "strength_text": med.strength_text,
                    "dose_value": str(med.dose_value) if med.dose_value is not None else None,
                    "dose_unit": med.dose_unit,
                    "frequency_per_day": med.frequency_per_day,
                    "timing_text": med.timing_text,
                    "duration_days": med.duration_days,
                    "evidence": [
                        {
                            "slot": ev.slot,
                            "external_document_id": ev.external_document_id,
                            "locator": ev.locator,
                            "content": ev.content.reveal(),
                        }
                        for ev in med_evidences
                    ],
                }
            )
        return json.dumps({"medications": medications_data}, ensure_ascii=False)
