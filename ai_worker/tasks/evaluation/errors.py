from __future__ import annotations

from enum import StrEnum


class EvaluationErrorCode(StrEnum):
    JSON_NUMBER_INVALID = "EVAL_JSON_NUMBER_INVALID"
    JSON_UNICODE_INVALID = "EVAL_JSON_UNICODE_INVALID"
    JSON_TYPE_INVALID = "EVAL_JSON_TYPE_INVALID"
    RESOURCE_PATH_INVALID = "EVAL_RESOURCE_PATH_INVALID"
    RESOURCE_PATH_DUPLICATE = "EVAL_RESOURCE_PATH_DUPLICATE"
    PRIVACY_FIELD_FORBIDDEN = "EVAL_PRIVACY_FIELD_FORBIDDEN"
    PRIVACY_VALUE_FORBIDDEN = "EVAL_PRIVACY_VALUE_FORBIDDEN"
    RESOURCE_NOT_FOUND = "EVAL_RESOURCE_NOT_FOUND"
    RESOURCE_BYTES_INVALID = "EVAL_RESOURCE_BYTES_INVALID"
    HASH_MISMATCH = "EVAL_HASH_MISMATCH"
    SCHEMA_INVALID = "EVAL_SCHEMA_INVALID"
    MANIFEST_INVALID = "EVAL_MANIFEST_INVALID"
    LEAKAGE_DETECTED = "EVAL_LEAKAGE_DETECTED"


class EvaluationValidationError(ValueError):
    """Stable validation error that contains no input values."""

    def __init__(self, code: EvaluationErrorCode, safe_path: str | None = None) -> None:
        self.code = code
        self.safe_path = safe_path
        message = code.value if safe_path is None else f"{code.value} at {safe_path}"
        super().__init__(message)
