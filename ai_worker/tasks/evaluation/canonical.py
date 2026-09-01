from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable

from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError

type JsonScalar = None | bool | int | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]

_MIN_SAFE_INTEGER = -(2**53) + 1
_MAX_SAFE_INTEGER = (2**53) - 1
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


def _validate_string(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise EvaluationValidationError(EvaluationErrorCode.JSON_UNICODE_INVALID)


def _validated_json_value(value: JsonValue) -> JsonValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not _MIN_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            raise EvaluationValidationError(EvaluationErrorCode.JSON_NUMBER_INVALID)
        return value
    if isinstance(value, float):
        raise EvaluationValidationError(EvaluationErrorCode.JSON_NUMBER_INVALID)
    if isinstance(value, str):
        _validate_string(value)
        return value
    if isinstance(value, list):
        return [_validated_json_value(item) for item in value]
    if isinstance(value, dict):
        validated: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise EvaluationValidationError(EvaluationErrorCode.JSON_TYPE_INVALID)
            _validate_string(key)
            validated[key] = _validated_json_value(item)
        return validated
    raise EvaluationValidationError(EvaluationErrorCode.JSON_TYPE_INVALID)


def _order_objects(value: JsonValue, *, key: Callable[[str], bytes]) -> JsonValue:
    if isinstance(value, list):
        return [_order_objects(item, key=key) for item in value]
    if isinstance(value, dict):
        return {object_key: _order_objects(value[object_key], key=key) for object_key in sorted(value, key=key)}
    return value


def canonical_json_bytes(value: JsonValue) -> bytes:
    validated = _validated_json_value(value)
    ordered = _order_objects(validated, key=lambda item: item.encode("utf-16-be"))
    return json.dumps(ordered, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(
    value: JsonValue,
    *,
    excluded_top_level_keys: frozenset[str] = frozenset(),
) -> str:
    if excluded_top_level_keys and isinstance(value, dict):
        value = {key: item for key, item in value.items() if key not in excluded_top_level_keys}
    return sha256_hex(canonical_json_bytes(value))


def normalize_resource_path(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    segments = normalized.split("/")
    if (
        not normalized
        or normalized.startswith("/")
        or _WINDOWS_DRIVE.match(normalized)
        or "\\" in normalized
        or "\x00" in normalized
        or any(segment in {"", ".", ".."} for segment in segments)
    ):
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    _validate_string(normalized)
    return normalized
