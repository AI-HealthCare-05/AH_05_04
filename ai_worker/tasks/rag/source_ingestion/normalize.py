"""원본 값을 변경하지 않고 checksum용 JSON 바이트를 생성합니다."""

import json

_MIN_SAFE_INTEGER = -(2**53) + 1
_MAX_SAFE_INTEGER = (2**53) - 1


def _validate_string(value: str) -> None:
    """UTF-8로 안전하게 표현할 수 없는 lone surrogate를 거부합니다."""
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("JSON string contains an invalid Unicode surrogate.")


def _validated_json_value(value: object) -> object:
    """Source canonical JSON에서 허용하는 값만 복사해 반환합니다."""
    if value is None or isinstance(value, bool):
        return value

    if isinstance(value, int):
        if not _MIN_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            raise ValueError("JSON integer is outside the safe range.")

        return value

    if isinstance(value, float):
        raise ValueError("Floating-point JSON numbers are not supported.")

    if isinstance(value, str):
        _validate_string(value)
        return value

    if isinstance(value, list):
        return [_validated_json_value(item) for item in value]

    if isinstance(value, dict):
        validated: dict[str, object] = {}

        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object keys must be strings.")

            _validate_string(key)
            validated[key] = _validated_json_value(item)

        return validated

    raise ValueError("Unsupported JSON value type.")


def _order_json_objects(value: object) -> object:
    """중첩 객체 key를 Evaluation과 같은 UTF-16 기준으로 정렬합니다."""
    if isinstance(value, list):
        return [_order_json_objects(item) for item in value]

    if isinstance(value, dict):
        return {
            key: _order_json_objects(value[key])
            for key in sorted(
                value,
                key=lambda item: item.encode("utf-16-be"),
            )
        }

    return value


def canonical_json_bytes(value: object) -> bytes:
    """원문을 보존하며 UTF-16 key 정렬·compact JSON·UTF-8로 직렬화합니다."""
    validated = _validated_json_value(value)
    ordered = _order_json_objects(validated)

    try:
        serialized = json.dumps(
            ordered,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("Value cannot be serialized as canonical JSON.") from None

    return serialized.encode("utf-8")
