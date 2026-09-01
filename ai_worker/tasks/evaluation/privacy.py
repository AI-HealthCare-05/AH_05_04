from __future__ import annotations

import re

from ai_worker.tasks.evaluation.canonical import JsonValue
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError

_FORBIDDEN_KEYS = frozenset(
    {
        "ocrraw",
        "rawvalue",
        "ocrnormalized",
        "normalizedvalue",
        "ocrdraft",
        "draftvalue",
        "structuredoutputdraft",
        "insurancecode",
        "insurancecodedigest",
        "internalidentifierdigest",
        "providerpayload",
        "providerresponsepayload",
        "credential",
        "credentials",
        "secret",
        "secrets",
        "apikey",
        "clientsecret",
        "secretkey",
        "accesstoken",
        "refreshtoken",
        "authorization",
        "password",
    }
)
_VALUE_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.-])"),
    re.compile(r"(?<!\d)(?:(?:\+?82[- ]?)?0?1[016789])[- ]?\d{3,4}[- ]?\d{4}(?!\d)"),
    re.compile(r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})"
    ),
)


def _normalize_key(key: str) -> str:
    return key.casefold().replace("-", "").replace("_", "")


def _pointer_segment(value: str) -> str:
    if len(value) > 80 or any(ord(character) < 0x20 for character in value):
        return "*"
    return value.replace("~", "~0").replace("/", "~1")


def _child_path(path: str, segment: str) -> str:
    return f"{path}/{_pointer_segment(segment)}"


def _validate(value: JsonValue, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            item_path = _child_path(path, key)
            if _normalize_key(key) in _FORBIDDEN_KEYS:
                raise EvaluationValidationError(EvaluationErrorCode.PRIVACY_FIELD_FORBIDDEN, item_path)
            _validate(item, item_path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate(item, _child_path(path, str(index)))
        return
    if isinstance(value, str) and any(pattern.search(value) for pattern in _VALUE_PATTERNS):
        raise EvaluationValidationError(EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN, path or "/")


def validate_privacy_boundary(value: JsonValue) -> None:
    _validate(value, "")
