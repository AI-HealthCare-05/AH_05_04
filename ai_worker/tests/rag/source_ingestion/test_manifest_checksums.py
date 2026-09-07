import hashlib
import unicodedata
from dataclasses import replace

import pytest

from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum


def _artifact(key: str = "synthetic/a.json") -> RawArtifactMetadata:
    return RawArtifactMetadata(
        artifact_key=key,
        raw_checksum="a" * 64,
        byte_size=10,
        content_type="application/json",
    )


def test_manifest_checksum_matches_explicit_serialization() -> None:
    expected_bytes = ('[["synthetic/a.json","' + "a" * 64 + '",10,"application/json"]]').encode("utf-8")

    assert raw_manifest_checksum([_artifact()]) == (hashlib.sha256(expected_bytes).hexdigest())


def test_manifest_checksum_ignores_enumeration_order() -> None:
    artifacts = [
        _artifact("synthetic/b.json"),
        _artifact("synthetic/a.json"),
    ]

    assert raw_manifest_checksum(artifacts) == (raw_manifest_checksum(reversed(artifacts)))


@pytest.mark.parametrize(
    "changed",
    [
        replace(_artifact(), artifact_key="synthetic/other.json"),
        replace(_artifact(), raw_checksum="b" * 64),
        replace(_artifact(), byte_size=11),
        replace(_artifact(), content_type="application/xml"),
    ],
)
def test_manifest_checksum_detects_metadata_changes(
    changed: RawArtifactMetadata,
) -> None:
    assert raw_manifest_checksum([_artifact()]) != (raw_manifest_checksum([changed]))


def test_rejects_duplicate_artifact_keys() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        raw_manifest_checksum(
            [
                _artifact(),
                replace(_artifact(), raw_checksum="b" * 64),
            ]
        )


def test_rejects_artifact_key_collision_after_nfc() -> None:
    composed = "synthetic/합성.json"
    decomposed = unicodedata.normalize("NFD", composed)

    with pytest.raises(ValueError, match="Duplicate"):
        raw_manifest_checksum(
            [
                _artifact(composed),
                _artifact(decomposed),
            ]
        )


def test_rejects_empty_manifest() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        raw_manifest_checksum([])


@pytest.mark.parametrize(
    "case",
    [
        "empty_key",
        "invalid_checksum",
        "negative_size",
        "boolean_size",
        "empty_content_type",
    ],
)
def test_rejects_invalid_artifact_metadata(case: str) -> None:
    with pytest.raises(ValueError):
        if case == "empty_key":
            replace(_artifact(), artifact_key="")
        elif case == "invalid_checksum":
            replace(_artifact(), raw_checksum="invalid")
        elif case == "negative_size":
            replace(_artifact(), byte_size=-1)
        elif case == "boolean_size":
            replace(_artifact(), byte_size=True)
        else:
            replace(_artifact(), content_type="")
