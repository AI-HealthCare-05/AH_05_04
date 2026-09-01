from __future__ import annotations

import re

from ai_worker.tasks.evaluation.canonical import JsonValue
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError

_FORBIDDEN_KEYS = frozenset(
    {
        "userid",
        "profileid",
        "patientid",
        "patientname",
        "patientbirth",
        "birthdate",
        "residentregistrationnumber",
        "rrn",
        "phone",
        "email",
        "address",
        "medicaldocumentid",
        "prescriptionid",
        "prescriptionversionid",
        "ocrraw",
        "rawvalue",
        "ocrnormalized",
        "normalizedvalue",
        "ocrdraft",
        "draftvalue",
        "structuredoutputdraft",
        "llmdraft",
        "structuredoutput",
        "insurancecode",
        "insurancecodedigest",
        "internalidentifierdigest",
        "identifierdigest",
        "providerrequest",
        "providerresponse",
        "providerbody",
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
    re.compile(
        r"(?<![A-Za-z0-9])(?:(?:\+82[ .-]?10)|(?:\(01[016789]\)|01[016789]))[ .-]\d{3,4}[ .-]\d{4}(?![A-Za-z0-9])"
    ),
    re.compile(r"(?<![A-Za-z0-9_-])01[016789]\d{7,8}(?![A-Za-z0-9_-])"),
    re.compile(r"(?<![A-Za-z0-9_-])\+821[016789]\d{7,8}(?![A-Za-z0-9_-])"),
    re.compile(r"(?<![A-Za-z0-9])\d{6}-?[1-4]\d{6}(?![A-Za-z0-9])"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(
        r"(?:sk-(?:proj-)?[A-Za-z0-9_-]{16,}|AKIA[A-Z0-9]{16}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})"
    ),
)
_SAFE_POINTER_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _normalize_key(key: str) -> str:
    return key.casefold().replace("-", "").replace("_", "")


def _pointer_segment(value: str) -> str:
    if _SAFE_POINTER_SEGMENT.fullmatch(value) is None:
        return "*"
    return value.replace("~", "~0").replace("/", "~1")


def _child_path(path: str, segment: str) -> str:
    return f"{path}/{_pointer_segment(segment)}"


def _contains_forbidden_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _VALUE_PATTERNS)


def _validate(value: JsonValue, path: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _normalize_key(key) in _FORBIDDEN_KEYS:
                raise EvaluationValidationError(EvaluationErrorCode.PRIVACY_FIELD_FORBIDDEN, path or "/")
            if _contains_forbidden_value(key):
                raise EvaluationValidationError(EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN, path or "/")
            item_path = _child_path(path, key)
            _validate(item, item_path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate(item, _child_path(path, str(index)))
        return
    if isinstance(value, str) and _contains_forbidden_value(value):
        raise EvaluationValidationError(EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN, path or "/")


def validate_privacy_boundary(value: JsonValue) -> None:
    _validate(value, "")
