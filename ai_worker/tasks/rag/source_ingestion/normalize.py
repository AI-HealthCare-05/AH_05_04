"""원본을 변경하지 않고 checksum용 JSON 바이트를 생성합니다."""

import json
import unicodedata


def _validate_json_value(value: object) -> None:
    """JSON 타입과 NFC 적용 후 객체 key의 유일성을 확인합니다."""
    if value is None or type(value) in (str, bool, int, float):
        return

    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return

    if isinstance(value, dict):
        normalized_keys: set[str] = set()

        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings.")

            normalized_key = unicodedata.normalize("NFC", key)

            if normalized_key in normalized_keys:
                raise ValueError("JSON object keys collide after NFC normalization.")

            normalized_keys.add(normalized_key)
            _validate_json_value(item)

        return

    raise ValueError("Unsupported JSON value type.")


def canonical_json_bytes(value: object) -> bytes:
    """Key 정렬·compact JSON·NFC·UTF-8 규칙으로 직렬화합니다."""
    _validate_json_value(value)

    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

    return unicodedata.normalize("NFC", serialized).encode("utf-8")
