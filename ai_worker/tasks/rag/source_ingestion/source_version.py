"""Production Source version 생성·검증 계약입니다."""

import re
import unicodedata
from datetime import UTC, datetime
from enum import StrEnum

SOURCE_VERSION_MAX_LENGTH = 200
EXTERNAL_VERSION_MAX_LENGTH = 191

_RESERVED_SOURCE_VERSION_PREFIXES = (
    "external:",
    "api:",
    "internal:",
)

_CHECKSUM_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_API_VERSION_PATTERN = re.compile(
    r"api:"
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z):"
    r"(?P<checksum>[0-9a-f]{64})\Z"
)
_INTERNAL_VERSION_PATTERN = re.compile(
    r"internal:"
    r"(?P<fixture_version>[^:]+):"
    r"(?P<checksum>[0-9a-f]{64})\Z"
)


class SourceVersionKind(StrEnum):
    EXTERNAL = "EXTERNAL"
    API = "API"
    INTERNAL = "INTERNAL"


class SourceVersionValidationError(ValueError):
    """Source version이 Production 계약을 충족하지 않습니다."""


def build_external_source_version(*, external_version: str) -> str:
    """Provider의 불변 version을 byte-for-byte 보존합니다."""
    _validate_external_version(external_version)
    source_version = f"external:{external_version}"
    _validate_common_source_version(source_version)
    return source_version


def build_api_source_version(
    *,
    collected_at: datetime,
    canonical_checksum: str,
) -> str:
    """외부 불변 version이 없는 API Source version을 생성합니다."""
    _validate_checksum(canonical_checksum)

    if collected_at.tzinfo is None or collected_at.utcoffset() is None:
        raise SourceVersionValidationError("API source_version 생성 시 timezone-aware collected_at이 필요합니다.")

    timestamp = collected_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    source_version = f"api:{timestamp}:{canonical_checksum}"
    _validate_common_source_version(source_version)
    return source_version


def build_internal_source_version(
    *,
    fixture_version: str,
    canonical_checksum: str,
) -> str:
    """승인된 불변 Fixture version으로 Internal Source version을 생성합니다."""
    _validate_token("fixture_version", fixture_version)

    if ":" in fixture_version:
        raise SourceVersionValidationError("fixture_version에는 구분자 ':'를 사용할 수 없습니다.")

    _validate_checksum(canonical_checksum)
    source_version = f"internal:{fixture_version}:{canonical_checksum}"
    _validate_common_source_version(source_version)
    return source_version


def validate_source_version(
    *,
    source_version: str,
    external_version: str | None,
    canonical_checksum: str,
) -> SourceVersionKind:
    """문법과 external/checksum 결속을 검증합니다."""
    _validate_common_source_version(source_version)
    _validate_checksum(canonical_checksum)

    if source_version.startswith("external:"):
        payload = source_version.removeprefix("external:")
        _validate_external_version(payload)

        if external_version is None:
            raise SourceVersionValidationError("external source_version에는 external_version이 필요합니다.")
        if payload != external_version:
            raise SourceVersionValidationError("source_version payload와 external_version이 일치하지 않습니다.")
        return SourceVersionKind.EXTERNAL

    api_match = _API_VERSION_PATTERN.fullmatch(source_version)
    if api_match is not None:
        _require_null_external_version(external_version)
        _validate_utc_timestamp(api_match.group("timestamp"))

        if api_match.group("checksum") != canonical_checksum:
            raise SourceVersionValidationError("API source_version checksum이 canonical_checksum과 일치하지 않습니다.")
        return SourceVersionKind.API

    internal_match = _INTERNAL_VERSION_PATTERN.fullmatch(source_version)
    if internal_match is not None:
        _require_null_external_version(external_version)
        _validate_token(
            "fixture_version",
            internal_match.group("fixture_version"),
        )

        if internal_match.group("checksum") != canonical_checksum:
            raise SourceVersionValidationError(
                "Internal source_version checksum이 canonical_checksum과 일치하지 않습니다."
            )
        return SourceVersionKind.INTERNAL

    raise SourceVersionValidationError("source_version이 external, api, internal 문법 중 하나와 일치해야 합니다.")


def _validate_common_source_version(source_version: str) -> None:
    _validate_token("source_version", source_version)

    if len(source_version) > SOURCE_VERSION_MAX_LENGTH:
        raise SourceVersionValidationError(f"source_version은 {SOURCE_VERSION_MAX_LENGTH}자를 초과할 수 없습니다.")


def _validate_external_version(external_version: str) -> None:
    _validate_token("external_version", external_version)

    if external_version.startswith(_RESERVED_SOURCE_VERSION_PREFIXES):
        raise SourceVersionValidationError("external_version은 source_version 예약 접두사로 시작할 수 없습니다.")

    if len(external_version) > EXTERNAL_VERSION_MAX_LENGTH:
        raise SourceVersionValidationError(f"external_version은 {EXTERNAL_VERSION_MAX_LENGTH}자를 초과할 수 없습니다.")


def _validate_token(field_name: str, value: str) -> None:
    if not value:
        raise SourceVersionValidationError(f"{field_name}은 비어 있을 수 없습니다.")

    if unicodedata.normalize("NFC", value) != value:
        raise SourceVersionValidationError(f"{field_name}은 NFC 문자열이어야 합니다.")

    if any(character.isspace() for character in value):
        raise SourceVersionValidationError(f"{field_name}에는 공백 문자를 사용할 수 없습니다.")

    if any(unicodedata.category(character).startswith("C") for character in value):
        raise SourceVersionValidationError(f"{field_name}에는 제어 문자를 사용할 수 없습니다.")


def _validate_checksum(canonical_checksum: str) -> None:
    if _CHECKSUM_PATTERN.fullmatch(canonical_checksum) is None:
        raise SourceVersionValidationError("canonical_checksum은 64자리 lowercase SHA-256이어야 합니다.")


def _require_null_external_version(external_version: str | None) -> None:
    if external_version is not None:
        raise SourceVersionValidationError("API와 Internal source_version의 external_version은 null이어야 합니다.")


def _validate_utc_timestamp(timestamp: str) -> None:
    try:
        datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        raise SourceVersionValidationError(
            "API source_version 시각은 UTC RFC3339 6자리 소수초 형식이어야 합니다."
        ) from None
