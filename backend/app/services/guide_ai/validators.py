import re
import unicodedata
from collections.abc import Mapping

from app.services.guide_ai.exceptions import GuideGenerationSafetyError
from app.services.guide_ai.prompt import APPROVED_GENERAL_NOTICES, APPROVED_GUIDANCE_BY_INTENT
from app.services.guide_ai.schemas import GeneratedGuideDraft, GuideGuidanceIntent

RULE_PRESCRIPTION_MISMATCH = "PRESCRIPTION_MISMATCH"
RULE_NUMERIC_IN_AI_TEXT = "RX_NUMERIC_IN_AI_TEXT"
RULE_CHANGE_DIRECTIVE = "RX_CHANGE_DIRECTIVE"
RULE_MEDICAL_CLAIM = "RX_MEDICAL_CLAIM"
RULE_UNSAFE_MARKUP = "UNSAFE_MARKUP"
RULE_GUIDANCE_INTENT_MISMATCH = "GUIDANCE_INTENT_MISMATCH"
RULE_UNAPPROVED_GUIDANCE = "UNAPPROVED_GUIDANCE"
RULE_UNAPPROVED_GENERAL_NOTICE = "UNAPPROVED_GENERAL_NOTICE"

_ZERO_WIDTH_OR_BIDI = re.compile("[\u200b-\u200d\u202a-\u202e\u2066-\u2069\ufeff]")
_HTML_TAG = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_PRESCRIPTION_UNIT_PATTERN = r"(?:mcg|μg|㎍|mg|㎎|kg|g|mL|ml|L|정|알|캡슐|포|병|방울|스푼|회|번|일|주|개월)"
_NATIVE_KOREAN_ONES = r"(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉)"
_NATIVE_KOREAN_TENS = (
    rf"(?:스무|스물(?:{_NATIVE_KOREAN_ONES})?|(?:서른|마흔|쉰|예순|일흔|여든|아흔)(?:{_NATIVE_KOREAN_ONES})?)"
)
_NATIVE_KOREAN_NUMBER = rf"(?:{_NATIVE_KOREAN_ONES}|열(?:{_NATIVE_KOREAN_ONES})?|{_NATIVE_KOREAN_TENS})"
_SINO_KOREAN_ONES = r"(?:일|이|삼|사|오|육|칠|팔|구)"
_SINO_KOREAN_TENS = rf"(?:십(?:{_SINO_KOREAN_ONES})?|{_SINO_KOREAN_ONES}십(?:{_SINO_KOREAN_ONES})?)"
_SINO_KOREAN_NUMBER = rf"(?:{_SINO_KOREAN_ONES}|{_SINO_KOREAN_TENS}|(?:{_SINO_KOREAN_ONES})?백)"
_KOREAN_QUANTITY = rf"(?:반|{_NATIVE_KOREAN_NUMBER}|{_SINO_KOREAN_NUMBER})"
_KOREAN_PARTICLE = r"(?:에게|에서|에는|부터|까지|마다|보다|처럼|조차|으로|은|는|이|가|을|를|만|도|에|로|와|과|의)"
_PRESCRIPTION_POSTFIX = rf"(?:(?:씩|간)(?:{_KOREAN_PARTICLE})?|{_KOREAN_PARTICLE})?"
_ARABIC_NUMERIC_UNIT = re.compile(
    rf"\d+(?:[.,]\d+)?\s*{_PRESCRIPTION_UNIT_PATTERN}{_PRESCRIPTION_POSTFIX}(?![가-힣])",
    re.IGNORECASE,
)
_KOREAN_NUMERIC_UNIT = re.compile(
    rf"(?<![가-힣]){_KOREAN_QUANTITY}\s*{_PRESCRIPTION_UNIT_PATTERN}{_PRESCRIPTION_POSTFIX}(?![가-힣])"
)
_AMBIGUOUS_KOREAN_WORDS = frozenset({"일정", "한정", "이번", "한번"})
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


def _contains_prescription_quantity(text: str) -> bool:
    if _ARABIC_NUMERIC_UNIT.search(text):
        return True
    return any(match.group() not in _AMBIGUOUS_KOREAN_WORDS for match in _KOREAN_NUMERIC_UNIT.finditer(text))


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
    if _contains_prescription_quantity(normalized):
        raise GuideGenerationSafetyError(RULE_NUMERIC_IN_AI_TEXT)
    if _MEDICAL_CLAIM.search(normalized):
        raise GuideGenerationSafetyError(RULE_MEDICAL_CLAIM)

    for sentence in filter(None, _SENTENCE_SPLIT.split(normalized)):
        if _CHANGE_TERM.search(sentence) and (_DIRECTIVE.search(sentence) or not _SAFE_NEGATION.search(sentence)):
            raise GuideGenerationSafetyError(RULE_CHANGE_DIRECTIVE)


def _normalize_for_membership(text: str) -> str:
    return unicodedata.normalize("NFC", text).strip()


def validate_generated_draft(
    draft: GeneratedGuideDraft,
    *,
    expected_intents: Mapping[int, GuideGuidanceIntent],
) -> None:
    indexes = [item.source_index for item in draft.medications]
    if len(indexes) != len(expected_intents) or set(indexes) != set(expected_intents):
        raise GuideGenerationSafetyError(RULE_PRESCRIPTION_MISMATCH)

    for item in draft.medications:
        expected_intent = expected_intents[item.source_index]
        if item.guidance_intent is not expected_intent:
            raise GuideGenerationSafetyError(RULE_GUIDANCE_INTENT_MISMATCH)
        _validate_text(item.guidance)
        if _normalize_for_membership(item.guidance) not in APPROVED_GUIDANCE_BY_INTENT[expected_intent]:
            raise GuideGenerationSafetyError(RULE_UNAPPROVED_GUIDANCE)
    _validate_text(draft.general_notice)
    if _normalize_for_membership(draft.general_notice) not in APPROVED_GENERAL_NOTICES:
        raise GuideGenerationSafetyError(RULE_UNAPPROVED_GENERAL_NOTICE)
