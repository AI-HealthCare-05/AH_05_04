import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_SPACE_PATTERN = re.compile(r"\s+")
_BRACKET_PATTERN = re.compile(r"[()\[\]{}<>]")
_SLASH_PATTERN = re.compile(r"\s*/\s*")

_STRENGTH_PATTERN = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>mg|mcg|μg|ug|g|ml|mL|%)"
    r"(?!\d)",
    re.IGNORECASE,
)

_CONCENTRATION_UNIT_PATTERN = re.compile(
    r"(?<=/)(?P<unit>mg|mcg|μg|ug|g|ml|mL)"
    r"(?!\d)",
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

        matches = list(_STRENGTH_PATTERN.finditer(text))

        # 함량을 이동하지 않고 원래 위치에서 표기만 정리합니다.
        normalized_value = _STRENGTH_PATTERN.sub(
            self._normalize_strength_match,
            text,
        )

        # 복합 함량과 농도 표기의 슬래시 주변 공백을 제거합니다.
        normalized_value = _SLASH_PATTERN.sub(
            "/",
            normalized_value,
        )

        # 1mg/ML처럼 슬래시 뒤에 숫자 없이 단위만 오는 경우입니다.
        normalized_value = _CONCENTRATION_UNIT_PATTERN.sub(
            self._normalize_concentration_unit,
            normalized_value,
        )

        normalized_value = _SPACE_PATTERN.sub(
            " ",
            normalized_value,
        ).strip()

        product_name = normalized_value
        strength_value: Decimal | None = None
        strength_unit: str | None = None

        # 문자열 끝에 단일 함량만 있을 때 기존 메타데이터를 유지합니다.
        # 복합 함량과 농도는 단일 strength_value로 표현하지 않습니다.
        if len(matches) == 1:
            match = matches[0]
            after_strength = text[match.end() :].strip()

            if not after_strength:
                product_name = text[: match.start()].strip()
                strength_value = self._parse_decimal(
                    match.group("value"),
                )
                strength_unit = self._normalize_unit(
                    match.group("unit"),
                )

        return NormalizedMedicationName(
            raw_value=raw_value,
            normalized_value=normalized_value,
            product_name=product_name,
            strength_value=strength_value,
            strength_unit=strength_unit,
            comparison_key=self._comparison_key(normalized_value),
        )

    def _normalize_strength_match(
        self,
        match: re.Match[str],
    ) -> str:
        value = match.group("value")
        unit = self._normalize_unit(match.group("unit"))

        return f"{value}{unit}"

    def _normalize_concentration_unit(
        self,
        match: re.Match[str],
    ) -> str:
        return self._normalize_unit(match.group("unit"))

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
            raise ValueError(
                "약품 함량을 숫자로 변환할 수 없습니다.",
            ) from error

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
