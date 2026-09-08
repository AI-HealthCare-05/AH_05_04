"""원본 값을 보존하며 Catalog 검색·표시용 문자열을 파생합니다."""

import re
import unicodedata
from dataclasses import dataclass

CATALOG_NORMALIZATION_VERSION = "catalog-text-nfc-whitespace-v1"
_WHITESPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class NormalizedCatalogText:
    raw_value: str
    normalized_value: str
    normalization_version: str = CATALOG_NORMALIZATION_VERSION


def _require_utf8_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{field_name} must be valid Unicode text.") from None
    return value


def normalize_catalog_text(value: object, *, field_name: str) -> NormalizedCatalogText:
    """원문과 분리된 NFC·공백 정리 문자열을 반환합니다."""

    raw_value = _require_utf8_text(value, field_name=field_name)
    normalized_value = _WHITESPACE_PATTERN.sub(
        " ",
        unicodedata.normalize("NFC", raw_value).strip(),
    )
    if not normalized_value:
        raise ValueError(f"{field_name} must not be blank.")
    return NormalizedCatalogText(
        raw_value=raw_value,
        normalized_value=normalized_value,
    )


def normalize_optional_catalog_text(
    value: object | None,
    *,
    field_name: str,
) -> NormalizedCatalogText | None:
    if value is None:
        return None
    return normalize_catalog_text(value, field_name=field_name)


def require_official_identity_text(value: object, *, field_name: str) -> str:
    """공식 code system·code를 변환 없이 검증합니다."""

    text = _require_utf8_text(value, field_name=field_name)
    if not text or text != text.strip():
        raise ValueError(f"{field_name} must be nonblank and already trimmed.")
    if not unicodedata.is_normalized("NFC", text):
        raise ValueError(f"{field_name} must use NFC Unicode normalization.")
    return text
