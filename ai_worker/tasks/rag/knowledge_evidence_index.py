"""Versioned Knowledge Evidence Index contracts and canonical receipts.

This module owns no provider calls and no database schema.  It validates a
complete, precomputed index build request before a repository can persist it.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ai_worker.tasks.rag.source_ingestion.normalize import canonical_json_bytes

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EMBEDDING_PROJECTION_VERSION = "knowledge-evidence-embedding@1"
_CORPUS_PROJECTION_VERSION = "knowledge-evidence-corpus-manifest@1"
_EMBEDDING_MANIFEST_PROJECTION_VERSION = "knowledge-evidence-embedding-manifest@1"
_CONFIGURATION_PROJECTION_VERSION = "knowledge-evidence-index-configuration@1"


class DistanceMetric(StrEnum):
    COSINE = "COSINE"


class KnowledgeEvidenceIndexFailureReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    SOURCE_BINDING_INVALID = "SOURCE_BINDING_INVALID"
    CONTENT_HASH_MISMATCH = "CONTENT_HASH_MISMATCH"
    EMBEDDING_INVALID = "EMBEDDING_INVALID"
    RECEIPT_MISMATCH = "RECEIPT_MISMATCH"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


class KnowledgeEvidenceIndexValidationError(ValueError):
    def __init__(self, reason: KnowledgeEvidenceIndexFailureReason) -> None:
        self.reason = reason
        super().__init__(reason.value)

    def __repr__(self) -> str:
        return f"KnowledgeEvidenceIndexValidationError({self.reason.value})"


class SensitiveEvidenceText:
    __slots__ = ("_value",)

    _value: str

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.REQUEST_INVALID)
        object.__setattr__(self, "_value", value)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("SensitiveEvidenceText is immutable")

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SensitiveEvidenceText) and other._value == self._value

    def __hash__(self) -> int:
        return hash(self._value)


@dataclass(frozen=True, slots=True)
class KnowledgeChunkIdentity:
    knowledge_chunk_id: UUID
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    canonical_checksum: str
    external_document_id: str
    chunk_index: int
    content_hash: str
    locator: str = field(repr=False)

    def __post_init__(self) -> None:
        if not all(
            (
                _bounded_nfc(self.source_code, 100),
                _bounded_nfc(self.source_version, 200),
                _bounded_nfc(self.external_document_id, 300),
                _bounded_locator(self.locator, 500),
            )
        ):
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.SOURCE_BINDING_INVALID)
        if type(self.chunk_index) is not int or self.chunk_index < 0:
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.SOURCE_BINDING_INVALID)
        if _SHA256_RE.fullmatch(self.canonical_checksum) is None or _SHA256_RE.fullmatch(self.content_hash) is None:
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.SOURCE_BINDING_INVALID)

    @property
    def stable_coordinate(self) -> tuple[str, str, str, int]:
        return self.source_code, self.source_version, self.external_document_id, self.chunk_index


@dataclass(frozen=True, slots=True)
class KnowledgeIndexMemberDraft:
    identity: KnowledgeChunkIdentity
    content_text: SensitiveEvidenceText
    embedding: tuple[float, ...] = field(repr=False)

    def __post_init__(self) -> None:
        observed = hashlib.sha256(self.content_text.reveal().encode("utf-8")).hexdigest()
        if observed != self.identity.content_hash:
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.CONTENT_HASH_MISMATCH)
        canonical_embedding_sha256(self.embedding)


@dataclass(frozen=True, slots=True)
class KnowledgeIndexBuildRequest:
    index_code: str
    index_version: str
    embedding_model_ref: str
    embedding_model_version: str
    embedding_dimension: int
    distance_metric: DistanceMetric
    members: tuple[KnowledgeIndexMemberDraft, ...]

    def __post_init__(self) -> None:
        if not all(
            (
                _bounded_nfc(self.index_code, 120),
                _bounded_nfc(self.index_version, 80),
                _bounded_nfc(self.embedding_model_ref, 255),
                _bounded_nfc(self.embedding_model_version, 80),
            )
        ):
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.REQUEST_INVALID)
        if type(self.embedding_dimension) is not int or not 1 <= self.embedding_dimension <= 2000:
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.EMBEDDING_INVALID)
        if self.distance_metric is not DistanceMetric.COSINE or not self.members:
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.REQUEST_INVALID)

        coordinates: set[tuple[str, str, str, int]] = set()
        chunk_ids: set[UUID] = set()
        for member in self.members:
            if len(member.embedding) != self.embedding_dimension:
                raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.EMBEDDING_INVALID)
            coordinate = member.identity.stable_coordinate
            if coordinate in coordinates or member.identity.knowledge_chunk_id in chunk_ids:
                raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.REQUEST_INVALID)
            coordinates.add(coordinate)
            chunk_ids.add(member.identity.knowledge_chunk_id)


@dataclass(frozen=True, slots=True)
class KnowledgeIndexReceipt:
    index_code: str
    index_version: str
    corpus_manifest_hash: str
    embedding_manifest_hash: str
    index_configuration_hash: str
    embedding_model_ref: str
    embedding_model_version: str
    embedding_dimension: int
    distance_metric: DistanceMetric
    member_count: int


class KnowledgeEvidenceIndexRepository(Protocol):
    async def persist_complete_index(
        self,
        request: KnowledgeIndexBuildRequest,
        receipt: KnowledgeIndexReceipt,
    ) -> KnowledgeIndexReceipt: ...


def canonical_embedding_sha256(embedding: tuple[float, ...]) -> str:
    if not isinstance(embedding, tuple) or not embedding:
        raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.EMBEDDING_INVALID)
    encoded = bytearray(_EMBEDDING_PROJECTION_VERSION.encode("utf-8") + b"\n")
    encoded.extend(struct.pack(">I", len(embedding)))
    squared_norm = 0.0
    for item in embedding:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.EMBEDDING_INVALID)
        value = float(item)
        if not math.isfinite(value) or (value == 0.0 and math.copysign(1.0, value) < 0):
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.EMBEDDING_INVALID)
        try:
            packed = struct.pack(">f", value)
        except (OverflowError, struct.error):
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.EMBEDDING_INVALID) from None
        stored_value = struct.unpack(">f", packed)[0]
        if stored_value == 0.0 and math.copysign(1.0, stored_value) < 0:
            raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.EMBEDDING_INVALID)
        encoded.extend(packed)
        squared_norm += stored_value * stored_value
    if squared_norm == 0.0 or not math.isfinite(squared_norm):
        raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.EMBEDDING_INVALID)
    return hashlib.sha256(encoded).hexdigest()


def canonical_corpus_manifest_hash(members: tuple[KnowledgeChunkIdentity, ...]) -> str:
    payload = [
        {
            "source_code": item.source_code,
            "source_version": item.source_version,
            "external_document_id": item.external_document_id,
            "chunk_index": item.chunk_index,
            "content_hash": item.content_hash,
        }
        for item in sorted(members, key=_stable_coordinate_sort_key)
    ]
    return _canonical_sha256(payload)


def create_knowledge_index_receipt(request: KnowledgeIndexBuildRequest) -> KnowledgeIndexReceipt:
    identities = tuple(item.identity for item in request.members)
    corpus_hash = canonical_corpus_manifest_hash(identities)
    embedding_entries = [
        {
            "source_code": item.identity.source_code,
            "source_version": item.identity.source_version,
            "external_document_id": item.identity.external_document_id,
            "chunk_index": item.identity.chunk_index,
            "embedding_sha256": canonical_embedding_sha256(item.embedding),
        }
        for item in sorted(request.members, key=lambda value: _stable_coordinate_sort_key(value.identity))
    ]
    embedding_manifest_hash = _canonical_sha256(
        {"projection_version": _EMBEDDING_MANIFEST_PROJECTION_VERSION, "members": embedding_entries}
    )
    configuration_hash = _canonical_sha256(
        {
            "projection_version": _CONFIGURATION_PROJECTION_VERSION,
            "corpus_projection_version": _CORPUS_PROJECTION_VERSION,
            "embedding_projection_version": _EMBEDDING_PROJECTION_VERSION,
            "embedding_model_ref": request.embedding_model_ref,
            "embedding_model_version": request.embedding_model_version,
            "embedding_dimension": request.embedding_dimension,
            "distance_metric": request.distance_metric.value,
            "corpus_manifest_hash": corpus_hash,
            "embedding_manifest_hash": embedding_manifest_hash,
        }
    )
    return KnowledgeIndexReceipt(
        request.index_code,
        request.index_version,
        corpus_hash,
        embedding_manifest_hash,
        configuration_hash,
        request.embedding_model_ref,
        request.embedding_model_version,
        request.embedding_dimension,
        request.distance_metric,
        len(request.members),
    )


async def build_knowledge_evidence_index(
    request: KnowledgeIndexBuildRequest,
    *,
    repository: KnowledgeEvidenceIndexRepository,
) -> KnowledgeIndexReceipt:
    expected = create_knowledge_index_receipt(request)
    try:
        observed = await repository.persist_complete_index(request, expected)
    except KnowledgeEvidenceIndexValidationError:
        raise
    except Exception:
        raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.DEPENDENCY_ERROR) from None
    if observed != expected:
        raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.RECEIPT_MISMATCH)
    return observed


def _stable_coordinate_sort_key(value: KnowledgeChunkIdentity) -> tuple[bytes, bytes, bytes, int]:
    return (
        value.source_code.encode("utf-8"),
        value.source_version.encode("utf-8"),
        value.external_document_id.encode("utf-8"),
        value.chunk_index,
    )


def _canonical_sha256(value: object) -> str:
    try:
        payload = canonical_json_bytes(value)
    except (TypeError, ValueError, UnicodeError):
        raise KnowledgeEvidenceIndexValidationError(KnowledgeEvidenceIndexFailureReason.REQUEST_INVALID) from None
    return hashlib.sha256(payload).hexdigest()


def _bounded_nfc(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and len(value) <= maximum
        and unicodedata.normalize("NFC", value) == value
        and not any(character.isspace() or unicodedata.category(character) == "Cc" for character in value)
    )


def _bounded_locator(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= maximum
        and unicodedata.normalize("NFC", value) == value
        and not any(unicodedata.category(character).startswith("C") for character in value)
    )


__all__ = [
    "DistanceMetric",
    "KnowledgeChunkIdentity",
    "KnowledgeEvidenceIndexFailureReason",
    "KnowledgeEvidenceIndexRepository",
    "KnowledgeEvidenceIndexValidationError",
    "KnowledgeIndexBuildRequest",
    "KnowledgeIndexMemberDraft",
    "KnowledgeIndexReceipt",
    "SensitiveEvidenceText",
    "build_knowledge_evidence_index",
    "canonical_corpus_manifest_hash",
    "canonical_embedding_sha256",
    "create_knowledge_index_receipt",
]
