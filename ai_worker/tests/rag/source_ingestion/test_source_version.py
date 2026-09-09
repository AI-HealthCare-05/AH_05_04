from datetime import UTC, datetime, timedelta, timezone

import pytest

from ai_worker.tasks.rag.source_ingestion.source_version import (
    SourceVersionKind,
    SourceVersionValidationError,
    build_api_source_version,
    build_external_source_version,
    build_internal_source_version,
    validate_source_version,
)

_CHECKSUM = "a" * 64


def test_builds_and_validates_external_source_version() -> None:
    source_version = build_external_source_version(external_version="2026-09-09")

    assert source_version == "external:2026-09-09"
    assert (
        validate_source_version(
            source_version=source_version,
            external_version="2026-09-09",
            canonical_checksum=_CHECKSUM,
        )
        is SourceVersionKind.EXTERNAL
    )


def test_external_payload_allows_191_characters() -> None:
    payload = "a" * 191

    assert len(build_external_source_version(external_version=payload)) == 200


def test_external_payload_rejects_192_characters() -> None:
    with pytest.raises(SourceVersionValidationError, match="191"):
        build_external_source_version(external_version="a" * 192)


def test_external_payload_must_match_preserved_value_exactly() -> None:
    with pytest.raises(SourceVersionValidationError, match="일치하지 않습니다"):
        validate_source_version(
            source_version="external:v2",
            external_version="v1",
            canonical_checksum=_CHECKSUM,
        )


def test_builds_api_version_in_utc_with_six_fractional_digits() -> None:
    collected_at = datetime(
        2026,
        9,
        9,
        10,
        2,
        3,
        123456,
        tzinfo=timezone(timedelta(hours=9)),
    )

    source_version = build_api_source_version(
        collected_at=collected_at,
        canonical_checksum=_CHECKSUM,
    )

    assert source_version == f"api:2026-09-09T01:02:03.123456Z:{_CHECKSUM}"


def test_api_checksum_must_match_canonical_checksum() -> None:
    with pytest.raises(SourceVersionValidationError, match="canonical_checksum"):
        validate_source_version(
            source_version=f"api:2026-09-09T01:02:03.123456Z:{'b' * 64}",
            external_version=None,
            canonical_checksum=_CHECKSUM,
        )


def test_builds_internal_version_from_fixture_version() -> None:
    source_version = build_internal_source_version(
        fixture_version="commit-0123456789abcdef",
        canonical_checksum=_CHECKSUM,
    )

    assert source_version == f"internal:commit-0123456789abcdef:{_CHECKSUM}"
    assert (
        validate_source_version(
            source_version=source_version,
            external_version=None,
            canonical_checksum=_CHECKSUM,
        )
        is SourceVersionKind.INTERNAL
    )


@pytest.mark.parametrize(
    "source_version",
    [
        "v1",
        "external:",
        "external:version with space",
        "external:e\u0301",
        f"api:2026-09-09T01:02:03Z:{_CHECKSUM}",
        f"internal::{_CHECKSUM}",
    ],
)
def test_rejects_invalid_source_version(source_version: str) -> None:
    with pytest.raises(SourceVersionValidationError):
        validate_source_version(
            source_version=source_version,
            external_version=None,
            canonical_checksum=_CHECKSUM,
        )


def test_api_builder_requires_timezone_aware_datetime() -> None:
    with pytest.raises(SourceVersionValidationError, match="timezone-aware"):
        build_api_source_version(
            collected_at=datetime(2026, 9, 9, 1, 2, 3),
            canonical_checksum=_CHECKSUM,
        )


def test_api_and_internal_require_null_external_version() -> None:
    source_version = build_api_source_version(
        collected_at=datetime(2026, 9, 9, 1, 2, 3, tzinfo=UTC),
        canonical_checksum=_CHECKSUM,
    )

    with pytest.raises(SourceVersionValidationError, match="null"):
        validate_source_version(
            source_version=source_version,
            external_version="unexpected",
            canonical_checksum=_CHECKSUM,
        )


@pytest.mark.parametrize(
    "external_version",
    [
        "external:nested-version",
        "api:2026-09-09T00:00:00.000000Z:" + "a" * 64,
        "internal:fixture-v1:" + "a" * 64,
    ],
)
def test_external_version_rejects_reserved_source_version_prefix(
    external_version: str,
) -> None:
    with pytest.raises(
        SourceVersionValidationError,
        match="예약 접두사",
    ):
        build_external_source_version(
            external_version=external_version,
        )

    with pytest.raises(
        SourceVersionValidationError,
        match="예약 접두사",
    ):
        validate_source_version(
            source_version=f"external:{external_version}",
            external_version=external_version,
            canonical_checksum=_CHECKSUM,
        )
