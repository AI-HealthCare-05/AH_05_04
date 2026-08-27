from decimal import Decimal

from app.services.guide_ai.exceptions import GuideGenerationInvalidResponseError
from app.services.guide_ai.schemas import GeneratedGuideDraft, GuideGenerationInput

INCOMPLETE_DOSE_NOTICE = "용량 정보는 처방전 또는 의료진 안내를 확인해 주세요."
SAFETY_NOTICE = "임의로 복용을 중단하거나 변경하지 말고 의료진 또는 약사와 상담해 주세요."
MAX_CONTENT_LENGTH = 10_000


def _format_decimal(value: Decimal) -> str:
    formatted = format(value, "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def render_plaintext_guide(guide_input: GuideGenerationInput, draft: GeneratedGuideDraft) -> str:
    guidance_by_index = {item.source_index: item.guidance for item in draft.medications}
    sections = ["복약 가이드"]

    for index, medication in enumerate(guide_input.medications):
        # 제품명과 제품 함량은 저장 단계에서는 분리하지만
        # 환자용 가이드에서는 함께 읽을 수 있도록 표시합니다.
        display_name = medication.medication_name
        if medication.strength_text is not None:
            display_name = f"{display_name} {medication.strength_text}"
        lines = [f"[{index + 1}] {display_name}"]
        if medication.dose_value is not None and medication.dose_unit is not None:
            lines.append(f"용량: {_format_decimal(medication.dose_value)} {medication.dose_unit}")
        elif medication.dose_value is not None or medication.dose_unit is not None:
            lines.append(INCOMPLETE_DOSE_NOTICE)
        if medication.frequency_per_day is not None:
            lines.append(f"복용 횟수: 하루 {medication.frequency_per_day}회")
        if medication.timing_text is not None:
            lines.append(f"복용 시점: {medication.timing_text}")
        if medication.duration_days is not None:
            lines.append(f"복용 기간: {medication.duration_days}일")
        lines.append(f"복약 안내: {guidance_by_index[index]}")
        sections.append("\n".join(lines))

    sections.append(f"공통 안내: {draft.general_notice}\n안전 안내: {SAFETY_NOTICE}")
    content = "\n\n".join(sections)
    if not content or len(content) > MAX_CONTENT_LENGTH:
        raise GuideGenerationInvalidResponseError("Rendered guide content has an invalid length")
    return content
