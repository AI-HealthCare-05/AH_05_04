"""승인된 Source Snapshot Member로부터 KnowledgeDocument 및 KnowledgeChunk draft를 구체화합니다.

이 모듈은 순수 변환/검증 계층입니다. DB 세션, 트랜잭션, SQL 실행, 영속 receipt 발급을
수행하지 않으며, 검증된 draft 컬렉션만 반환합니다.

에러 계약은 reason-only 형태이며, 오류 메시지나 repr에 파일 경로, object key,
SQL문, 원문 XML 등 민감정보를 노출하지 않습니다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ai_worker.adapters.local_private_source_artifact_finalizer import ArtifactObjectKeyError
from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.tasks.rag.mfds_label_chunk_policy import (
    CHUNK_POLICY_VERSION,
    ChunkPolicyError,
    KnowledgeChunkDraft,
    build_chunk_drafts,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactIntegrityError,
    RawArtifactMetadata,
    RawArtifactUnavailableError,
)
from ai_worker.tasks.rag.source_ingestion.mfds_label import (
    CANONICALIZATION_SPEC_VERSION,
    LOCAL_PRIVATE_STORAGE_BACKEND,
    NORMALIZATION_VERSION,
    OBSERVED_CONTENT_TYPE,
    PARSER_VERSION,
    SCHEMA_VERSION,
    parse_mfds_label_artifact,
)

SECTION_ORDER = ("EE", "UD", "NB", "NN")
_ALLOWED_SECTION_SETS = (
    frozenset({"EE", "UD", "NB"}),
    frozenset({"NN"}),
)
_ITEM_SEQ_PATTERN = re.compile(r"[0-9]{9}\Z")
_EXPECTED_STORAGE_BACKEND = LOCAL_PRIVATE_STORAGE_BACKEND
_EXPECTED_ARTIFACT_KIND = "RAW_RESPONSE"
_EXPECTED_CONTENT_TYPE = OBSERVED_CONTENT_TYPE


class KnowledgeMaterializationFailureReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    SOURCE_BINDING_INVALID = "SOURCE_BINDING_INVALID"
    SOURCE_NOT_ELIGIBLE = "SOURCE_NOT_ELIGIBLE"
    ARTIFACT_INTEGRITY_MISMATCH = "ARTIFACT_INTEGRITY_MISMATCH"
    PARSER_REJECTED = "PARSER_REJECTED"
    CHUNK_POLICY_UNSUPPORTED = "CHUNK_POLICY_UNSUPPORTED"
    CONTENT_CONFLICT = "CONTENT_CONFLICT"
    RECEIPT_MISMATCH = "RECEIPT_MISMATCH"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


class KnowledgeMaterializationError(Exception):
    """reason-only 공개 오류. 기존 KnowledgeEvidenceIndexValidationError 관례와 동일."""

    def __init__(self, reason: KnowledgeMaterializationFailureReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True, slots=True)
class KnowledgeMaterializationRequest:
    snapshot_id: UUID
    member_ids: tuple[UUID, ...]
    expected_item_seq: str
    chunk_policy_version: str


@dataclass(frozen=True, slots=True)
class MaterializationSourceDocument:
    source_id: UUID
    source_code: str
    source_lifecycle_status: str
    endpoint_id: UUID
    endpoint_code: str
    endpoint_lifecycle_status: str
    endpoint_runtime_status: str
    endpoint_acquisition_status: str
    operation_id: UUID
    operation_code: str
    operation_runtime_status: str
    operation_acquisition_status: str
    snapshot_id: UUID
    source_version: str
    canonical_checksum: str
    raw_manifest_checksum: str
    schema_version: str
    parser_version: str
    normalization_version: str
    canonicalization_spec_version: str
    snapshot_verification_status: str
    ingestion_run_id: UUID
    ingestion_run_status: str
    member_id: UUID
    member_kind: str
    locator: str
    content_sha256: str
    ingestion_artifact_id: UUID
    artifact_key: str
    section: str
    artifact_kind: str
    page_number: int | None
    storage_backend: str
    reject_code: str | None
    parser_location: str | None
    raw_checksum: str
    byte_size: int
    content_type: str
    object_key: str = field(repr=False)
    ingestion_run_snapshot_id: UUID | None = None
    ingestion_run_operation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeDocumentDraft:
    source_snapshot_member_id: UUID
    external_document_id: str
    document_content_hash: str
    canonicalization_spec_version: str
    title: str
    chunks: tuple[KnowledgeChunkDraft, ...]


@dataclass(frozen=True, slots=True)
class MaterializedChunkReceipt:
    knowledge_chunk_id: UUID
    chunk_index: int
    content_hash: str


@dataclass(frozen=True, slots=True)
class MaterializedDocumentReceipt:
    knowledge_document_id: UUID
    source_snapshot_member_id: UUID
    external_document_id: str
    document_content_hash: str
    locator: str = field(repr=False)
    chunks: tuple[MaterializedChunkReceipt, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeMaterializationReceipt:
    snapshot_id: UUID
    source_code: str
    source_version: str
    snapshot_canonical_checksum: str
    canonicalization_spec_version: str
    item_seq: str
    chunk_policy_version: str
    documents: tuple[MaterializedDocumentReceipt, ...]


class MaterializationOutcome(StrEnum):
    CREATED = "CREATED"
    EXACT_REPLAY = "EXACT_REPLAY"


@dataclass(frozen=True, slots=True)
class KnowledgeMaterializationResult:
    receipt: KnowledgeMaterializationReceipt
    outcome: MaterializationOutcome

    @property
    def is_exact_replay(self) -> bool:
        return self.outcome == MaterializationOutcome.EXACT_REPLAY


class MaterializationArtifactReader(Protocol):
    def read_verified(self, *, object_key: str, metadata: RawArtifactMetadata) -> bytes: ...


def _validate_request(request: KnowledgeMaterializationRequest) -> None:
    if not isinstance(request.expected_item_seq, str) or not _ITEM_SEQ_PATTERN.fullmatch(request.expected_item_seq):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.REQUEST_INVALID)

    if not request.member_ids or len(request.member_ids) != len(set(request.member_ids)):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.REQUEST_INVALID)

    if request.chunk_policy_version != CHUNK_POLICY_VERSION:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.CHUNK_POLICY_UNSUPPORTED)


def _validate_source_documents_set(
    request: KnowledgeMaterializationRequest,
    source_docs: Sequence[MaterializationSourceDocument],
) -> None:
    if not source_docs or len(source_docs) != len(request.member_ids):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.REQUEST_INVALID)

    if set(d.member_id for d in source_docs) != set(request.member_ids):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.REQUEST_INVALID)

    if any(d.snapshot_id != request.snapshot_id for d in source_docs):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)

    sections = tuple(d.section for d in source_docs)
    if len(sections) != len(set(sections)):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.REQUEST_INVALID)

    if frozenset(sections) not in _ALLOWED_SECTION_SETS:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.REQUEST_INVALID)

    for doc in source_docs:
        expected_locator = f"mfds-label/{request.expected_item_seq}/{doc.section}"
        if doc.locator != expected_locator:
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.REQUEST_INVALID)


def _validate_source_document_versions_and_run(doc: MaterializationSourceDocument) -> None:
    if (
        doc.schema_version != SCHEMA_VERSION
        or doc.parser_version != PARSER_VERSION
        or doc.normalization_version != NORMALIZATION_VERSION
        or doc.canonicalization_spec_version != CANONICALIZATION_SPEC_VERSION
    ):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)

    if doc.ingestion_run_status == "SUCCEEDED":
        if doc.ingestion_run_snapshot_id is not None and doc.ingestion_run_snapshot_id != doc.snapshot_id:
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
        if doc.ingestion_run_operation_id is not None and doc.ingestion_run_operation_id != doc.operation_id:
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)


def _validate_source_document_storage(doc: MaterializationSourceDocument) -> None:
    if doc.storage_backend != _EXPECTED_STORAGE_BACKEND:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    if doc.artifact_kind != _EXPECTED_ARTIFACT_KIND:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    if doc.content_type != _EXPECTED_CONTENT_TYPE:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    if doc.reject_code is not None or doc.parser_location is not None:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)


def _validate_source_document_binding(
    doc: MaterializationSourceDocument,
) -> RawArtifactMetadata:
    _validate_source_document_storage(doc)

    expected_page = SECTION_ORDER.index(doc.section) + 1
    if doc.page_number != expected_page:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)

    if doc.artifact_key != f"{doc.locator}.xml":
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)

    if doc.content_sha256 != doc.raw_checksum:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH)

    try:
        expected_object_key = LocalPrivateSourceArtifactStore.object_key_for_checksum(doc.raw_checksum)
    except ValueError as exc:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID) from exc

    if doc.object_key != expected_object_key:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)

    _validate_source_document_versions_and_run(doc)

    try:
        return RawArtifactMetadata(
            artifact_key=doc.artifact_key,
            raw_checksum=doc.raw_checksum,
            byte_size=doc.byte_size,
            content_type=doc.content_type,
        )
    except ValueError as exc:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID) from exc


def _read_and_materialize_draft(
    request: KnowledgeMaterializationRequest,
    doc: MaterializationSourceDocument,
    metadata: RawArtifactMetadata,
    artifact_reader: MaterializationArtifactReader,
) -> KnowledgeDocumentDraft:
    try:
        raw_bytes = artifact_reader.read_verified(object_key=doc.object_key, metadata=metadata)
    except RawArtifactIntegrityError as exc:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH) from exc
    except RawArtifactUnavailableError as exc:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.DEPENDENCY_ERROR) from exc
    except ArtifactObjectKeyError as exc:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID) from exc
    except ValueError as exc:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID) from exc

    try:
        parsed = parse_mfds_label_artifact(raw_bytes, doc.section)
    except ValueError as exc:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.PARSER_REJECTED) from exc

    if parsed.empty_article_titles:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.CHUNK_POLICY_UNSUPPORTED)

    try:
        chunks = build_chunk_drafts(parsed, chunk_policy_version=request.chunk_policy_version)
    except ChunkPolicyError as exc:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.CHUNK_POLICY_UNSUPPORTED) from exc

    return KnowledgeDocumentDraft(
        source_snapshot_member_id=doc.member_id,
        external_document_id=f"mfds-label:{request.expected_item_seq}:{doc.section}",
        document_content_hash=doc.content_sha256,
        canonicalization_spec_version=doc.canonicalization_spec_version,
        title=parsed.document_title,
        chunks=chunks,
    )


def materialize_documents(
    request: KnowledgeMaterializationRequest,
    source_docs: Sequence[MaterializationSourceDocument],
    artifact_reader: MaterializationArtifactReader,
) -> tuple[KnowledgeDocumentDraft, ...]:
    """Source documents로부터 검증된 KnowledgeDocumentDraft 및 chunk draft를 생성합니다."""
    _validate_request(request)
    _validate_source_documents_set(request, source_docs)

    sorted_docs = sorted(source_docs, key=lambda d: SECTION_ORDER.index(d.section))
    drafts: list[KnowledgeDocumentDraft] = []
    for doc in sorted_docs:
        metadata = _validate_source_document_binding(doc)
        draft = _read_and_materialize_draft(request, doc, metadata, artifact_reader)
        drafts.append(draft)

    return tuple(drafts)
