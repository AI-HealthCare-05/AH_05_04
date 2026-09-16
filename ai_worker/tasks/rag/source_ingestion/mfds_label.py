"""MFDS 제품별 허가사항 XML을 검증해 기존 Snapshot 저장 경계에 연결합니다."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast
from uuid import UUID
from xml.etree import ElementTree

from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.acquire import preserve_raw_artifacts
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactMetadata,
    RawArtifactStore,
    read_verified_raw_artifact,
)
from ai_worker.tasks.rag.source_ingestion.checksums import raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.normalize import canonical_json_bytes
from ai_worker.tasks.rag.source_ingestion.result import SourceIngestionResult
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotIngestionDecision,
    SnapshotIngestionMetadata,
    SnapshotLifecycleRepository,
    SnapshotPersistenceResult,
    SnapshotProvenanceReceipt,
    SourceSnapshotMemberCreate,
    SourceSnapshotMemberKind,
    SourceSnapshotMemberReceipt,
    append_snapshot_member,
    persist_source_ingestion_result,
)
from ai_worker.tasks.rag.source_ingestion.source_version import build_api_source_version

SECTION_TITLES = {
    "EE": ("효능효과",),
    "UD": ("용법용량",),
    "NB": ("사용상의주의사항", "사용상주의사항"),
    "NN": ("e약은요 정보",),
}
NN_ARTICLE_TITLES = (
    "이 약의 효능은 무엇입니까?",
    "이 약은 어떻게 사용합니까?",
    "이 약을 사용하기 전에 반드시 알아야 할 내용은 무엇입니까?",
    "이 약의 사용상 주의사항은 무엇입니까?",
    "이 약을 사용하는 동안 주의해야 할 약 또는 음식은 무엇입니까?",
    "이 약은 어떤 이상반응이 나타날 수 있습니까?",
    "이 약은 어떻게 보관해야 합니까?",
)
REQUIRED_SECTIONS = ("EE", "UD", "NB")
SECTION_ORDER = (*REQUIRED_SECTIONS, "NN")
MAX_XML_BYTES = 2 * 1024 * 1024
SCHEMA_VERSION = "mfds-label-selected-product@1"
PARSER_VERSION = "mfds-label-xml@1"
NORMALIZATION_VERSION = "mfds-label-xml-structure@1"
CANONICALIZATION_SPEC_VERSION = "mfds-label-selected-product@1"
OBSERVED_CONTENT_TYPE = "application/download; UTF-8; charset=UTF-8"
LOCAL_PRIVATE_STORAGE_BACKEND = "LOCAL_PRIVATE"
ACQUISITION_EVIDENCE_VERSION = "mfds-label-acquisition-evidence@1"
ACQUISITION_EVIDENCE_FILE = "acquisition-manifest.json"
MAX_ACQUISITION_EVIDENCE_BYTES = 64 * 1024

_ITEM_SEQ_PATTERN = re.compile(r"[0-9]{9}\Z")
_CHECKSUM_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class MfdsLabelDocument:
    section: str
    document_title: str
    file_path: Path = field(repr=False)
    metadata: RawArtifactMetadata
    canonical_structure: dict[str, object] = field(repr=False)
    content_status: str
    empty_article_titles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MfdsLabelArtifactEvidence:
    document_type: str
    file_name: str
    source_url: str
    raw_sha256: str
    byte_size: int
    content_type: str


@dataclass(frozen=True, slots=True)
class MfdsLabelAcquisitionEvidence:
    collected_at: datetime
    endpoint_receipt_hash: str
    documents: tuple[MfdsLabelArtifactEvidence, ...]


@dataclass(frozen=True, slots=True)
class MfdsLabelIngestionPlan:
    item_seq: str
    identity: SourceOperationIdentity
    collected_at: datetime
    documents: tuple[MfdsLabelDocument, ...] = field(repr=False)
    ingestion: SourceIngestionResult
    source_version: str


@dataclass(frozen=True, slots=True)
class IngestionArtifactReceipt:
    ingestion_artifact_id: UUID
    ingestion_run_id: UUID
    page_number: int
    artifact_key: str
    storage_backend: str
    object_key: str = field(repr=False)
    raw_checksum: str
    byte_size: int
    content_type: str

    def __post_init__(self) -> None:
        if self.page_number < 1 or not self.artifact_key.strip() or len(self.artifact_key) > 500:
            raise ValueError("INGESTION_ARTIFACT_RECEIPT_INVALID")
        if not self.storage_backend.strip() or not self.object_key.strip():
            raise ValueError("INGESTION_ARTIFACT_RECEIPT_INVALID")
        if _CHECKSUM_PATTERN.fullmatch(self.raw_checksum) is None or self.byte_size < 0:
            raise ValueError("INGESTION_ARTIFACT_RECEIPT_INVALID")
        if not self.content_type.strip() or len(self.content_type) > 255:
            raise ValueError("INGESTION_ARTIFACT_RECEIPT_INVALID")


@dataclass(frozen=True, slots=True)
class SnapshotMemberBinding:
    source_snapshot_member_id: UUID
    source_snapshot_id: UUID
    member_kind: SourceSnapshotMemberKind
    ingestion_artifact_id: UUID | None
    locator: str = field(repr=False)
    content_sha256: str

    def __post_init__(self) -> None:
        if not self.locator or self.locator != self.locator.strip() or len(self.locator) > 500:
            raise ValueError("SNAPSHOT_MEMBER_BINDING_INVALID")
        if _CHECKSUM_PATTERN.fullmatch(self.content_sha256) is None:
            raise ValueError("SNAPSHOT_MEMBER_BINDING_INVALID")


class MfdsLabelRepository(SnapshotLifecycleRepository, Protocol):
    async def get_snapshot_receipt(self, *, snapshot_id: UUID) -> SnapshotProvenanceReceipt | None: ...

    async def get_ingestion_artifact_receipts(
        self, *, ingestion_run_id: UUID
    ) -> tuple[IngestionArtifactReceipt, ...]: ...

    async def get_ingestion_artifact_receipt(
        self, *, ingestion_artifact_id: UUID
    ) -> IngestionArtifactReceipt | None: ...

    async def get_snapshot_member_bindings(self, *, snapshot_id: UUID) -> tuple[SnapshotMemberBinding, ...]: ...

    async def append_snapshot_member(self, request: SourceSnapshotMemberCreate) -> SourceSnapshotMemberReceipt: ...


class MfdsLabelArtifactReader(Protocol):
    def read_verified(self, *, object_key: str, metadata: RawArtifactMetadata) -> bytes: ...


@dataclass(frozen=True, slots=True)
class MfdsLabelPersistenceReceipt:
    persistence: SnapshotPersistenceResult
    source_version: str
    canonical_checksum: str
    member_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class MfdsLabelRequeryReceipt:
    snapshot_id: UUID
    source_version: str
    canonical_checksum: str
    member_count: int


@dataclass(frozen=True, slots=True)
class ParsedMfdsLabelDocument:
    section: str
    document_title: str
    article_count: int
    paragraph_count: int
    nonempty_paragraph_count: int
    content_status: str
    empty_article_titles: tuple[str, ...]
    root: ElementTree.Element = field(repr=False)


def parse_mfds_label_artifact(raw_bytes: bytes, section: str) -> ParsedMfdsLabelDocument:
    """원시 XML 바이트열과 섹션 코드를 입력받아 검증된 구조화 문서를 반환합니다.

    기존 parser 오류 계약(ValueError: XML_SECTION_MISMATCH, XML_INVALID,
    XML_BODY_EMPTY, XML_ARTICLE_SET_INVALID 등)을 온전히 유지합니다.
    canonical structure는 이 seam에서 만들지 않습니다. 기존에 canonicalization을
    수행하던 `_load_document()` 경로만 `_canonical_element()`를 호출하므로
    `inspect_xml()`의 기존 구조 검사 동작이 그대로 유지됩니다.
    """
    root = _parse_xml(raw_bytes, section)
    body = _inspect_body(root, section)
    return ParsedMfdsLabelDocument(
        section=section,
        document_title=root.get("title") or "",
        article_count=cast(int, body["article_count"]),
        paragraph_count=cast(int, body["paragraph_count"]),
        nonempty_paragraph_count=cast(int, body["nonempty_paragraph_count"]),
        content_status=str(body["content_status"]),
        empty_article_titles=tuple(cast(list[str], body["empty_article_titles"])),
        root=root,
    )


def inspect_xml(raw: bytes, section: str) -> dict[str, object]:
    """원문을 바꾸지 않고 구조와 완전성 상태만 검사합니다."""
    parsed = parse_mfds_label_artifact(raw, section)
    return {
        "section": section,
        "byte_size": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "document_title": parsed.document_title,
        "article_count": parsed.article_count,
        "paragraph_count": parsed.paragraph_count,
        "nonempty_paragraph_count": parsed.nonempty_paragraph_count,
        "empty_article_titles": list(parsed.empty_article_titles),
        "content_status": parsed.content_status,
        "table_element_count": sum(node.tag.lower() == "table" for node in parsed.root.iter()),
    }


def load_mfds_label_plan(
    *,
    item_seq: str,
    input_dir: Path,
    identity: SourceOperationIdentity,
    endpoint_receipt_hash: str,
    collected_at: datetime,
    include_e_drug: bool,
) -> MfdsLabelIngestionPlan:
    """승인된 private 입력 폴더를 제품 단위 원자적 적재 계획으로 변환합니다."""
    _validate_item_seq(item_seq)
    _validate_identity(identity)
    if _CHECKSUM_PATTERN.fullmatch(endpoint_receipt_hash) is None:
        raise ValueError("ENDPOINT_RECEIPT_HASH_INVALID")
    if collected_at.tzinfo is None or collected_at.utcoffset() is None:
        raise ValueError("COLLECTED_AT_INVALID")
    if input_dir.is_symlink() or not input_dir.is_dir():
        raise ValueError("INPUT_DIRECTORY_INVALID")

    sections = SECTION_ORDER if include_e_drug else REQUIRED_SECTIONS
    evidence = _load_acquisition_evidence(input_dir, item_seq, sections)
    if evidence.endpoint_receipt_hash != endpoint_receipt_hash or evidence.collected_at != collected_at:
        raise ValueError("ACQUISITION_EVIDENCE_BINDING_MISMATCH")
    documents = tuple(
        _load_document(input_dir, item_seq, section, artifact_evidence)
        for section, artifact_evidence in zip(sections, evidence.documents, strict=True)
    )
    canonical_checksum = _canonical_checksum(item_seq, documents)
    ingestion = SourceIngestionResult(
        identity=identity,
        endpoint_receipt_hash=endpoint_receipt_hash,
        raw_manifest_checksum=raw_manifest_checksum(document.metadata for document in documents),
        canonical_checksum=canonical_checksum,
        canonicalization_spec_version=CANONICALIZATION_SPEC_VERSION,
        record_count=len(documents),
        artifact_count=len(documents),
    )
    return MfdsLabelIngestionPlan(
        item_seq=item_seq,
        identity=identity,
        collected_at=collected_at,
        documents=documents,
        ingestion=ingestion,
        source_version=build_api_source_version(collected_at=collected_at, canonical_checksum=canonical_checksum),
    )


async def persist_mfds_label_plan(
    *,
    plan: MfdsLabelIngestionPlan,
    repository: MfdsLabelRepository,
    artifact_store: RawArtifactStore,
    metadata: SnapshotIngestionMetadata,
) -> MfdsLabelPersistenceReceipt:
    """원본·Snapshot·member를 호출자 transaction 안에서 검증하고 저장합니다."""
    _validate_plan(plan)
    _validate_metadata(plan, metadata)
    raw_artifacts = tuple(
        (index, document.file_path, document.metadata) for index, document in enumerate(plan.documents, start=1)
    )
    stored_artifacts = preserve_raw_artifacts(artifacts=raw_artifacts, store=artifact_store)
    persistence = await persist_source_ingestion_result(
        repository=repository,
        ingestion=plan.ingestion,
        metadata=metadata,
        artifacts=stored_artifacts,
    )
    if persistence.decision not in {SnapshotIngestionDecision.CREATED, SnapshotIngestionDecision.NO_CHANGE}:
        return MfdsLabelPersistenceReceipt(persistence, plan.source_version, plan.ingestion.canonical_checksum, ())
    if persistence.snapshot_id is None:
        raise RuntimeError("SNAPSHOT_RECEIPT_MISSING")

    artifacts = await _verified_run_artifacts(plan, persistence, repository)
    if persistence.decision is SnapshotIngestionDecision.CREATED:
        await _append_created_members(plan, persistence, artifacts, repository)
    members = await _verified_members(
        plan,
        persistence.snapshot_id,
        repository,
        require_current_raw=persistence.decision is SnapshotIngestionDecision.CREATED,
    )
    provenance = await _verified_provenance(plan, persistence.snapshot_id, repository)
    return MfdsLabelPersistenceReceipt(
        persistence=persistence,
        source_version=provenance.source_version,
        canonical_checksum=plan.ingestion.canonical_checksum,
        member_ids=tuple(member.source_snapshot_member_id for member in members),
    )


async def requery_mfds_label_persistence(
    *,
    plan: MfdsLabelIngestionPlan,
    receipt: MfdsLabelPersistenceReceipt,
    repository: MfdsLabelRepository,
    artifact_reader: MfdsLabelArtifactReader,
) -> MfdsLabelRequeryReceipt:
    """commit 후 새 session에서 Snapshot·member·원문 hash를 다시 검증합니다."""
    snapshot_id = receipt.persistence.snapshot_id
    if snapshot_id is None:
        raise ValueError("SNAPSHOT_REQUERY_UNAVAILABLE")
    provenance = await _verified_provenance(plan, snapshot_id, repository)
    await _verified_run_artifacts(plan, receipt.persistence, repository)
    members = await _verified_members(
        plan,
        snapshot_id,
        repository,
        require_current_raw=receipt.persistence.decision is SnapshotIngestionDecision.CREATED,
    )
    documents = {f"mfds-label/{plan.item_seq}/{document.section}": document for document in plan.documents}
    for member in members:
        document = documents.get(member.locator)
        assert document is not None
        assert member.ingestion_artifact_id is not None
        artifact = await repository.get_ingestion_artifact_receipt(ingestion_artifact_id=member.ingestion_artifact_id)
        if artifact is None:
            raise ValueError("INGESTION_ARTIFACT_RECEIPT_MISSING")
        artifact_metadata = _member_artifact_metadata(member, artifact)
        if receipt.persistence.decision is SnapshotIngestionDecision.CREATED:
            _validate_artifact_document(artifact, document)
        raw = artifact_reader.read_verified(object_key=artifact.object_key, metadata=artifact_metadata)
        if hashlib.sha256(raw).hexdigest() != member.content_sha256:
            raise ValueError("ARTIFACT_MEMBER_CHECKSUM_MISMATCH")
    return MfdsLabelRequeryReceipt(
        snapshot_id=snapshot_id,
        source_version=provenance.source_version,
        canonical_checksum=provenance.canonical_checksum,
        member_count=len(members),
    )


def _load_document(
    input_dir: Path,
    item_seq: str,
    section: str,
    evidence: MfdsLabelArtifactEvidence,
) -> MfdsLabelDocument:
    path = input_dir / f"{section}.xml"
    if path.is_symlink() or not path.is_file() or path.parent != input_dir:
        raise ValueError("XML_FILE_INVALID")
    metadata = RawArtifactMetadata(
        artifact_key=f"mfds-label/{item_seq}/{section}.xml",
        raw_checksum=evidence.raw_sha256,
        byte_size=evidence.byte_size,
        content_type=evidence.content_type,
    )
    raw = read_verified_raw_artifact(file_path=path, metadata=metadata)
    parsed = parse_mfds_label_artifact(raw, section)
    return MfdsLabelDocument(
        section=section,
        document_title=parsed.document_title,
        file_path=path,
        metadata=metadata,
        canonical_structure=_canonical_element(parsed.root),
        content_status=parsed.content_status,
        empty_article_titles=parsed.empty_article_titles,
    )


def _load_acquisition_evidence(
    input_dir: Path,
    item_seq: str,
    sections: tuple[str, ...],
) -> MfdsLabelAcquisitionEvidence:
    path = input_dir / ACQUISITION_EVIDENCE_FILE
    if path.is_symlink() or not path.is_file() or path.parent != input_dir:
        raise ValueError("ACQUISITION_EVIDENCE_INVALID")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_ACQUISITION_EVIDENCE_BYTES + 1)
        if not raw or len(raw) > MAX_ACQUISITION_EVIDENCE_BYTES:
            raise ValueError("ACQUISITION_EVIDENCE_INVALID")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("ACQUISITION_EVIDENCE_INVALID") from None
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "item_seq",
        "collected_at",
        "endpoint_receipt_hash",
        "documents",
    }:
        raise ValueError("ACQUISITION_EVIDENCE_INVALID")
    if payload["schema_version"] != ACQUISITION_EVIDENCE_VERSION or payload["item_seq"] != item_seq:
        raise ValueError("ACQUISITION_EVIDENCE_BINDING_MISMATCH")
    try:
        collected_at = datetime.fromisoformat(str(payload["collected_at"]).replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("ACQUISITION_EVIDENCE_INVALID") from None
    if collected_at.tzinfo is None or collected_at.utcoffset() is None:
        raise ValueError("ACQUISITION_EVIDENCE_INVALID")
    receipt_hash = payload["endpoint_receipt_hash"]
    if not isinstance(receipt_hash, str) or _CHECKSUM_PATTERN.fullmatch(receipt_hash) is None:
        raise ValueError("ACQUISITION_EVIDENCE_INVALID")
    documents = _parse_artifact_evidence(payload["documents"], item_seq, sections)
    return MfdsLabelAcquisitionEvidence(collected_at, receipt_hash, documents)


def _parse_artifact_evidence(
    value: object,
    item_seq: str,
    sections: tuple[str, ...],
) -> tuple[MfdsLabelArtifactEvidence, ...]:
    if not isinstance(value, list) or len(value) != len(sections):
        raise ValueError("ACQUISITION_EVIDENCE_INVALID")
    documents = []
    for entry, section in zip(value, sections, strict=True):
        if not isinstance(entry, dict) or set(entry) != {
            "document_type",
            "file_name",
            "source_url",
            "raw_sha256",
            "byte_size",
            "content_type",
        }:
            raise ValueError("ACQUISITION_EVIDENCE_INVALID")
        expected_url = f"https://nedrug.mfds.go.kr/pbp/cmn/xml/drb/{item_seq}/{section}"
        if (
            entry["document_type"] != section
            or entry["file_name"] != f"{section}.xml"
            or entry["source_url"] != expected_url
            or not isinstance(entry["raw_sha256"], str)
            or _CHECKSUM_PATTERN.fullmatch(entry["raw_sha256"]) is None
            or type(entry["byte_size"]) is not int
            or not 0 < entry["byte_size"] <= MAX_XML_BYTES
            or not isinstance(entry["content_type"], str)
            or entry["content_type"].partition(";")[0].strip().lower() != "application/download"
            or len(entry["content_type"]) > 255
        ):
            raise ValueError("ACQUISITION_EVIDENCE_BINDING_MISMATCH")
        documents.append(MfdsLabelArtifactEvidence(**entry))
    return tuple(documents)


def _parse_xml(raw: bytes, section: str) -> ElementTree.Element:
    if section not in SECTION_TITLES:
        raise ValueError("UNSUPPORTED_SECTION")
    if not raw or len(raw) > MAX_XML_BYTES:
        raise ValueError("XML_SIZE_INVALID")
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("XML_ENCODING_UNSUPPORTED") from None
    if "\x00" in source or re.search(r"<!\s*(DOCTYPE|ENTITY)\b", source, re.IGNORECASE):
        raise ValueError("XML_DECLARATION_UNSAFE")
    declaration = re.match(r"<\?xml\b[^?]*\?>", source)
    if declaration:
        encoding = re.search(r"encoding\s*=\s*['\"]([^'\"]+)['\"]", declaration[0])
        if encoding and encoding[1].lower() not in ("utf-8", "utf8"):
            raise ValueError("XML_ENCODING_UNSUPPORTED")
    try:
        root = ElementTree.fromstring(source)
    except (ElementTree.ParseError, ValueError):
        raise ValueError("XML_INVALID") from None
    if root.tag != "DOC" or root.get("type") != section or root.get("title") not in SECTION_TITLES[section]:
        raise ValueError("XML_SECTION_MISMATCH")
    return root


def _inspect_body(root: ElementTree.Element, section: str) -> dict[str, object]:
    articles = list(root.iter("ARTICLE"))
    paragraphs = list(root.iter("PARAGRAPH"))
    nonempty_paragraphs = [node for node in paragraphs if "".join(node.itertext()).strip()]
    nonempty_article_titles = [title for node in articles if (title := (node.get("title") or "").strip())]
    if section == "NN":
        article_titles = tuple((node.get("title") or "").strip() for node in articles)
        if article_titles != NN_ARTICLE_TITLES:
            raise ValueError("XML_ARTICLE_SET_INVALID")
    if not nonempty_paragraphs and not nonempty_article_titles:
        raise ValueError("XML_BODY_EMPTY")
    empty_article_titles: list[str] = []
    if section == "NN":
        empty_article_titles = [
            title
            for node in articles
            if (title := (node.get("title") or "").strip())
            and not any("".join(paragraph.itertext()).strip() for paragraph in node.iter("PARAGRAPH"))
        ]
    return {
        "article_count": len(articles),
        "paragraph_count": len(paragraphs),
        "nonempty_paragraph_count": len(nonempty_paragraphs),
        "empty_article_titles": empty_article_titles,
        "content_status": "PARTIAL_OFFICIAL" if empty_article_titles else "COMPLETE",
    }


def _canonical_element(element: ElementTree.Element) -> dict[str, object]:
    attributes = {
        key: _canonical_text(value) for key, value in element.attrib.items() if not key.lower().startswith("data-")
    }
    return {
        "tag": element.tag,
        "attributes": attributes,
        "text": _canonical_text(element.text or ""),
        "children": [_canonical_element(child) for child in element],
        "tail": _canonical_text(element.tail or ""),
    }


def _canonical_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def canonicalize_label_xml(raw: bytes, section: str) -> tuple[dict[str, object], dict[str, object]]:
    """Receipt와 ingestion이 같은 XML 검증·정규화 결과를 사용합니다."""
    parsed = parse_mfds_label_artifact(raw, section)
    return {
        "document_type": section,
        "document_title": parsed.document_title,
        "structure": _canonical_element(parsed.root),
    }, {
        "article_count": parsed.article_count,
        "paragraph_count": parsed.paragraph_count,
        "nonempty_paragraph_count": parsed.nonempty_paragraph_count,
        "empty_article_titles": list(parsed.empty_article_titles),
        "content_status": parsed.content_status,
    }


def label_canonical_checksum(item_seq: str, documents: list[dict[str, object]]) -> str:
    """기존 제품 전체 canonical manifest 계산을 공유합니다."""
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "canonicalization_spec_version": CANONICALIZATION_SPEC_VERSION,
                "item_seq": item_seq,
                "documents": documents,
            }
        )
    ).hexdigest()


def _canonical_checksum(item_seq: str, documents: tuple[MfdsLabelDocument, ...]) -> str:
    return label_canonical_checksum(
        item_seq,
        [
            {
                "document_type": document.section,
                "document_title": document.document_title,
                "structure": document.canonical_structure,
            }
            for document in documents
        ],
    )


def _validate_item_seq(item_seq: str) -> None:
    if _ITEM_SEQ_PATTERN.fullmatch(item_seq) is None:
        raise ValueError("ITEM_SEQ_INVALID")


def _validate_identity(identity: SourceOperationIdentity) -> None:
    for value in (identity.source_code, identity.endpoint_code, identity.operation_code):
        if re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", value) is None:
            raise ValueError("SOURCE_OPERATION_IDENTITY_INVALID")


def _validate_metadata(plan: MfdsLabelIngestionPlan, metadata: SnapshotIngestionMetadata) -> None:
    expected = (
        plan.source_version,
        SCHEMA_VERSION,
        PARSER_VERSION,
        NORMALIZATION_VERSION,
        0,
        None,
        plan.collected_at,
    )
    observed = (
        metadata.source_version,
        metadata.schema_version,
        metadata.parser_version,
        metadata.normalization_version,
        metadata.rejected_record_count,
        metadata.external_version,
        metadata.collected_at,
    )
    if observed != expected:
        raise ValueError("MFDS_LABEL_METADATA_MISMATCH")


def _validate_plan(plan: MfdsLabelIngestionPlan) -> None:
    _validate_item_seq(plan.item_seq)
    _validate_identity(plan.identity)
    sections = tuple(document.section for document in plan.documents)
    if sections not in (REQUIRED_SECTIONS, SECTION_ORDER):
        raise ValueError("MFDS_LABEL_DOCUMENT_SET_MISMATCH")
    expected_artifact_keys = tuple(f"mfds-label/{plan.item_seq}/{section}.xml" for section in sections)
    artifact_keys = tuple(document.metadata.artifact_key for document in plan.documents)
    expected_checksum = _canonical_checksum(plan.item_seq, plan.documents)
    expected_version = build_api_source_version(collected_at=plan.collected_at, canonical_checksum=expected_checksum)
    if (
        artifact_keys != expected_artifact_keys
        or plan.ingestion.identity != plan.identity
        or plan.ingestion.canonicalization_spec_version != CANONICALIZATION_SPEC_VERSION
        or plan.ingestion.record_count != len(plan.documents)
        or plan.ingestion.artifact_count != len(plan.documents)
        or plan.ingestion.raw_manifest_checksum
        != raw_manifest_checksum(document.metadata for document in plan.documents)
        or plan.ingestion.canonical_checksum != expected_checksum
        or plan.source_version != expected_version
    ):
        raise ValueError("MFDS_LABEL_PLAN_MISMATCH")


async def _append_created_members(
    plan: MfdsLabelIngestionPlan,
    persistence: SnapshotPersistenceResult,
    artifacts: tuple[IngestionArtifactReceipt, ...],
    repository: MfdsLabelRepository,
) -> None:
    assert persistence.snapshot_id is not None
    provenance = await repository.get_snapshot_receipt(snapshot_id=persistence.snapshot_id)
    if provenance is None:
        raise RuntimeError("SNAPSHOT_PROVENANCE_MISSING")
    by_page = {artifact.page_number: artifact for artifact in artifacts}
    for page_number, document in enumerate(plan.documents, start=1):
        artifact = by_page[page_number]
        await append_snapshot_member(
            SourceSnapshotMemberCreate(
                provenance=provenance,
                member_kind=SourceSnapshotMemberKind.ARTIFACT,
                endpoint_id=None,
                operation_id=None,
                ingestion_artifact_id=artifact.ingestion_artifact_id,
                locator=f"mfds-label/{plan.item_seq}/{document.section}",
                content_sha256=document.metadata.raw_checksum,
            ),
            repository=repository,
        )


def _validate_artifact_receipt(
    artifact: IngestionArtifactReceipt,
    document: MfdsLabelDocument,
    ingestion_run_id: UUID,
) -> None:
    expected = (
        ingestion_run_id,
        document.metadata.artifact_key,
        LOCAL_PRIVATE_STORAGE_BACKEND,
        document.metadata.raw_checksum,
        document.metadata.byte_size,
        document.metadata.content_type,
    )
    observed = (
        artifact.ingestion_run_id,
        artifact.artifact_key,
        artifact.storage_backend,
        artifact.raw_checksum,
        artifact.byte_size,
        artifact.content_type,
    )
    if observed != expected:
        raise ValueError("INGESTION_ARTIFACT_RECEIPT_MISMATCH")


def _validate_artifact_document(artifact: IngestionArtifactReceipt, document: MfdsLabelDocument) -> None:
    expected = (
        document.metadata.artifact_key,
        LOCAL_PRIVATE_STORAGE_BACKEND,
        document.metadata.raw_checksum,
        document.metadata.byte_size,
        document.metadata.content_type,
    )
    observed = (
        artifact.artifact_key,
        artifact.storage_backend,
        artifact.raw_checksum,
        artifact.byte_size,
        artifact.content_type,
    )
    if observed != expected:
        raise ValueError("INGESTION_ARTIFACT_RECEIPT_MISMATCH")


async def _verified_run_artifacts(
    plan: MfdsLabelIngestionPlan,
    persistence: SnapshotPersistenceResult,
    repository: MfdsLabelRepository,
) -> tuple[IngestionArtifactReceipt, ...]:
    artifacts = await repository.get_ingestion_artifact_receipts(ingestion_run_id=persistence.ingestion_run_id)
    by_page = {artifact.page_number: artifact for artifact in artifacts}
    if len(by_page) != len(artifacts) or set(by_page) != set(range(1, len(plan.documents) + 1)):
        raise ValueError("INGESTION_ARTIFACT_SET_MISMATCH")
    for page_number, document in enumerate(plan.documents, start=1):
        _validate_artifact_receipt(by_page[page_number], document, persistence.ingestion_run_id)
    return artifacts


async def _verified_members(
    plan: MfdsLabelIngestionPlan,
    snapshot_id: UUID,
    repository: MfdsLabelRepository,
    *,
    require_current_raw: bool,
) -> tuple[SnapshotMemberBinding, ...]:
    members = await repository.get_snapshot_member_bindings(snapshot_id=snapshot_id)
    documents = {f"mfds-label/{plan.item_seq}/{document.section}": document for document in plan.documents}
    if {member.locator for member in members} != set(documents) or any(
        member.source_snapshot_id != snapshot_id
        or member.member_kind is not SourceSnapshotMemberKind.ARTIFACT
        or member.ingestion_artifact_id is None
        for member in members
    ):
        raise ValueError("SNAPSHOT_MEMBER_SET_MISMATCH")
    for member in members:
        assert member.ingestion_artifact_id is not None
        artifact = await repository.get_ingestion_artifact_receipt(ingestion_artifact_id=member.ingestion_artifact_id)
        if artifact is None:
            raise ValueError("INGESTION_ARTIFACT_RECEIPT_MISSING")
        _member_artifact_metadata(member, artifact)
        if require_current_raw and member.content_sha256 != documents[member.locator].metadata.raw_checksum:
            raise ValueError("SNAPSHOT_MEMBER_SET_MISMATCH")
    return members


def _member_artifact_metadata(
    member: SnapshotMemberBinding,
    artifact: IngestionArtifactReceipt,
) -> RawArtifactMetadata:
    if (
        artifact.storage_backend != LOCAL_PRIVATE_STORAGE_BACKEND
        or artifact.artifact_key != f"{member.locator}.xml"
        or artifact.raw_checksum != member.content_sha256
        or artifact.content_type != OBSERVED_CONTENT_TYPE
    ):
        raise ValueError("INGESTION_ARTIFACT_RECEIPT_MISMATCH")
    return RawArtifactMetadata(
        artifact_key=artifact.artifact_key,
        raw_checksum=artifact.raw_checksum,
        byte_size=artifact.byte_size,
        content_type=artifact.content_type,
    )


async def _verified_provenance(
    plan: MfdsLabelIngestionPlan,
    snapshot_id: UUID,
    repository: MfdsLabelRepository,
) -> SnapshotProvenanceReceipt:
    provenance = await repository.get_snapshot_receipt(snapshot_id=snapshot_id)
    if provenance is None:
        raise RuntimeError("SNAPSHOT_PROVENANCE_MISSING")
    observed = (
        provenance.source_code,
        provenance.canonical_checksum,
        provenance.canonicalization_spec_version,
        provenance.endpoint_receipt_hash,
    )
    expected = (
        plan.identity.source_code,
        plan.ingestion.canonical_checksum,
        CANONICALIZATION_SPEC_VERSION,
        plan.ingestion.endpoint_receipt_hash,
    )
    if observed != expected:
        raise ValueError("SNAPSHOT_PROVENANCE_MISMATCH")
    return provenance
