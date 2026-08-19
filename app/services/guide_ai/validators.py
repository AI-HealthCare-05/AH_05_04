import re
import unicodedata

from app.services.guide_ai.exceptions import GuideGenerationSafetyError
from app.services.guide_ai.schemas import GeneratedGuideDraft

RULE_PRESCRIPTION_MISMATCH = "PRESCRIPTION_MISMATCH"
RULE_NUMERIC_IN_AI_TEXT = "RX_NUMERIC_IN_AI_TEXT"
RULE_CHANGE_DIRECTIVE = "RX_CHANGE_DIRECTIVE"
RULE_MEDICAL_CLAIM = "RX_MEDICAL_CLAIM"
RULE_UNSAFE_MARKUP = "UNSAFE_MARKUP"

_ZERO_WIDTH_OR_BIDI = re.compile("[\u200b-\u200d\u202a-\u202e\u2066-\u2069\ufeff]")
_HTML_TAG = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_PRESCRIPTION_UNIT_PATTERN = r"(?:mcg|μg|㎍|mg|㎎|kg|g|mL|ml|L|정|알|캡슐|포|병|방울|스푼|회|번|일|주|개월)"
_NUMERIC_UNIT = re.compile(
    rf"(?:\d+(?:[.,]\d+)?\s*{_PRESCRIPTION_UNIT_PATTERN}|"
    rf"(?<![가-힣])(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|일|이|삼|사|오|육|칠|팔|구|십)"
    rf"\s+{_PRESCRIPTION_UNIT_PATTERN})",
    re.IGNORECASE,
)
_CHANGE_TERM = re.compile(r"중단|끊(?:기|어|으)|증량|감량|늘리|줄(?:이|여)|횟수\s*변경|용량\s*변경|복용\s*변경")
_SAFE_NEGATION = re.compile(r"(?:하지\s*마세요|하지\s*말고|해서는\s*안\s*됩니다|하지\s*않(?:습니다|도록))")
_DIRECTIVE = re.compile(
    r"(?:하세요|해\s*주세요|드세요|십시오|해도\s*됩니다|(?:할|해도)\s*수\s*있습니다|가능합니다|권(?:합니다|해요))"
)
_MEDICAL_CLAIM = re.compile(
    r"효능|치료|예방|부작용|상호작용|"
    r"(?:통증|두통|발열|기침|혈압|혈당|어지럼|구토|설사).*(?:낫|완화|개선|생기|유발|없앱|낮춥|높입|조절|관리)"
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|[\r\n]+")


def _contains_control_character(text: str) -> bool:
    return any(unicodedata.category(character) == "Cc" for character in text)


def _validate_text(text: str) -> None:
    normalized = unicodedata.normalize("NFC", text)
    if (
        _ZERO_WIDTH_OR_BIDI.search(normalized)
        or _HTML_TAG.search(normalized)
        or _MARKDOWN_LINK.search(normalized)
        or _URL.search(normalized)
        or _contains_control_character(normalized)
    ):
        raise GuideGenerationSafetyError(RULE_UNSAFE_MARKUP)
    if _NUMERIC_UNIT.search(normalized):
        raise GuideGenerationSafetyError(RULE_NUMERIC_IN_AI_TEXT)
    if _MEDICAL_CLAIM.search(normalized):
        raise GuideGenerationSafetyError(RULE_MEDICAL_CLAIM)

    for sentence in filter(None, _SENTENCE_SPLIT.split(normalized)):
        if _CHANGE_TERM.search(sentence) and (_DIRECTIVE.search(sentence) or not _SAFE_NEGATION.search(sentence)):
            raise GuideGenerationSafetyError(RULE_CHANGE_DIRECTIVE)


def validate_generated_draft(draft: GeneratedGuideDraft, *, medication_count: int) -> None:
    indexes = [item.source_index for item in draft.medications]
    if len(indexes) != medication_count or sorted(indexes) != list(range(medication_count)):
        raise GuideGenerationSafetyError(RULE_PRESCRIPTION_MISMATCH)

    for item in draft.medications:
        _validate_text(item.guidance)
    _validate_text(draft.general_notice)
