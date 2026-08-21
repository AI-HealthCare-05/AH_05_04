import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_SPACE_PATTERN = re.compile(r"\s+")
_BRACKET_PATTERN = re.compile(r"[()\[\]{}<>]")

_STRENGTH_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mg|mcg|μg|ug|g|ml|mL|%)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NormalizedMedicationName:
    raw_value: str
    normalized_value: str
    product_name: str
    strength_value: Decimal | None
    strength_unit: str | None
    comparison_key: str
    normalization_version: str = "rule-v1"


class MedicationNameNormalizer:
    def normalize(
        self,
        raw_value: str,
    ) -> NormalizedMedicationName:
        text = unicodedata.normalize("NFKC", raw_value)
        text = text.strip()
        text = _BRACKET_PATTERN.sub(" ", text)
        text = _SPACE_PATTERN.sub(" ", text)

        match = _STRENGTH_PATTERN.search(text)

        if match is None:
            return NormalizedMedicationName(
                raw_value=raw_value,
                normalized_value=text,
                product_name=text,
                strength_value=None,
                strength_unit=None,
                comparison_key=self._comparison_key(text),
            )

        strength_value = self._parse_decimal(match.group("value"))
        strength_unit = self._normalize_unit(match.group("unit"))

        before_strength = text[: match.start()]
        after_strength = text[match.end() :]

        product_name = f"{before_strength} {after_strength}"
        product_name = _SPACE_PATTERN.sub(
            " ",
            product_name,
        ).strip()

        strength_text = f"{self._format_decimal(strength_value)}{strength_unit}"

        normalized_value = f"{product_name} {strength_text}".strip()

        return NormalizedMedicationName(
            raw_value=raw_value,
            normalized_value=normalized_value,
            product_name=product_name,
            strength_value=strength_value,
            strength_unit=strength_unit,
            comparison_key=self._comparison_key(normalized_value),
        )

    def _normalize_unit(self, unit: str) -> str:
        normalized = unit.lower()

        aliases = {
            "μg": "mcg",
            "ug": "mcg",
            "ml": "mL",
        }

        return aliases.get(normalized, normalized)

    def _parse_decimal(
        self,
        value: str,
    ) -> Decimal:
        try:
            return Decimal(value)
        except InvalidOperation as error:
            raise ValueError("약품 함량을 숫자로 변환할 수 없습니다.") from error

    def _format_decimal(
        self,
        value: Decimal,
    ) -> str:
        return format(value.normalize(), "f")

    def _comparison_key(
        self,
        value: str,
    ) -> str:
        normalized = unicodedata.normalize(
            "NFKC",
            value,
        ).lower()

        return re.sub(
            r"[^0-9a-z가-힣%]",
            "",
            normalized,
        )
