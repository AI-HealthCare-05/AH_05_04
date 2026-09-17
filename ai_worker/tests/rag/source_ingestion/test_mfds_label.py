"""#591 MFDS 허가사항 parser와 제품 단위 Snapshot 적재 검증."""

import hashlib
import json
import unicodedata
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.mfds_label import (
    ACQUISITION_EVIDENCE_FILE,
    ACQUISITION_EVIDENCE_VERSION,
    NN_ARTICLE_TITLES,
    NORMALIZATION_VERSION,
    OBSERVED_CONTENT_TYPE,
    PARSER_VERSION,
    SCHEMA_VERSION,
    IngestionArtifactReceipt,
    MfdsLabelIngestionPlan,
    ParsedMfdsLabelDocument,
    SnapshotMemberBinding,
    inspect_xml,
    load_mfds_label_plan,
    parse_mfds_label_artifact,
    persist_mfds_label_plan,
    requery_mfds_label_persistence,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotCreateRequest,
    SnapshotIngestionDecision,
    SnapshotIngestionMetadata,
    SnapshotProvenanceReceipt,
    SnapshotVerificationStatus,
    SourceSnapshotMemberCreate,
    SourceSnapshotMemberReceipt,
)
from ai_worker.tests.rag.source_ingestion.test_snapshot_lifecycle import FakeSnapshotRepository

_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000101")
_ENDPOINT_ID = UUID("00000000-0000-4000-8000-000000000102")
_IDENTITY = SourceOperationIdentity("SYNTHETIC_MFDS_LABEL", "SYNTHETIC_LABEL_XML", "GET_SELECTED_LABELS")
_RECEIPT_HASH = "a" * 64
_COLLECTED_AT = datetime(2026, 9, 15, 6, 0, tzinfo=UTC)


def _xml(section: str, *, data_value: str = "ignored") -> bytes:
    titles = {"EE": "효능효과", "UD": "용법용량", "NB": "사용상의주의사항"}
    return (
        f'<DOC type="{section}" title="{titles[section]}" data-hwpjson="{data_value}">'
        '<ARTICLE title="합성 제목"><PARAGRAPH>합성 본문<table><tr><td>셀</td></tr></table></PARAGRAPH>'
        "</ARTICLE></DOC>"
    ).encode()


def _nn_xml(*, empty_index: int | None = None) -> bytes:
    articles = "".join(
        f'<ARTICLE title="{title}"><PARAGRAPH>{"" if index == empty_index else "합성 본문"}</PARAGRAPH></ARTICLE>'
        for index, title in enumerate(NN_ARTICLE_TITLES)
    )
    return f'<DOC type="NN" title="e약은요 정보">{articles}</DOC>'.encode()


def _input_directory(
    tmp_path: Path,
    *,
    include_e_drug: bool = False,
    data_value: str = "ignored",
    collected_at: datetime = _COLLECTED_AT,
) -> Path:
    directory = tmp_path / f"input-{uuid4().hex}"
    directory.mkdir()
    for section in ("EE", "UD", "NB"):
        (directory / f"{section}.xml").write_bytes(_xml(section, data_value=data_value))
    if include_e_drug:
        (directory / "NN.xml").write_bytes(_nn_xml(empty_index=2))
    sections = ("EE", "UD", "NB", "NN") if include_e_drug else ("EE", "UD", "NB")
    documents = []
    for section in sections:
        raw = (directory / f"{section}.xml").read_bytes()
        documents.append(
            {
                "document_type": section,
                "file_name": f"{section}.xml",
                "source_url": f"https://nedrug.mfds.go.kr/pbp/cmn/xml/drb/200610660/{section}",
                "raw_sha256": hashlib.sha256(raw).hexdigest(),
                "byte_size": len(raw),
                "content_type": OBSERVED_CONTENT_TYPE,
            }
        )
    (directory / ACQUISITION_EVIDENCE_FILE).write_text(
        json.dumps(
            {
                "schema_version": ACQUISITION_EVIDENCE_VERSION,
                "item_seq": "200610660",
                "collected_at": collected_at.isoformat(),
                "endpoint_receipt_hash": _RECEIPT_HASH,
                "documents": documents,
            }
        )
    )
    return directory


def _plan(tmp_path: Path, *, collected_at: datetime = _COLLECTED_AT) -> MfdsLabelIngestionPlan:
    return load_mfds_label_plan(
        item_seq="200610660",
        input_dir=_input_directory(tmp_path, collected_at=collected_at),
        identity=_IDENTITY,
        endpoint_receipt_hash=_RECEIPT_HASH,
        collected_at=collected_at,
        include_e_drug=False,
    )


def _metadata(plan: MfdsLabelIngestionPlan) -> SnapshotIngestionMetadata:
    return SnapshotIngestionMetadata(
        source_version=plan.source_version,
        schema_version=SCHEMA_VERSION,
        parser_version=PARSER_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        rejected_record_count=0,
        run_group_key=f"synthetic-{uuid4().hex[:12]}",
        attempt_number=1,
        started_at=_COLLECTED_AT,
        finished_at=_COLLECTED_AT + timedelta(seconds=1),
        collected_at=plan.collected_at,
        verified_by="synthetic-reviewer",
    )


class FakeMfdsLabelRepository(FakeSnapshotRepository):
    def __init__(self) -> None:
        super().__init__()
        self.requests_by_snapshot: dict[UUID, SnapshotCreateRequest] = {}
        self.artifact_receipts: dict[UUID, IngestionArtifactReceipt] = {}
        self.artifacts_by_run: dict[UUID, tuple[IngestionArtifactReceipt, ...]] = {}
        self.members: dict[UUID, list[SnapshotMemberBinding]] = {}

    async def create_snapshot(self, request: SnapshotCreateRequest) -> UUID:
        snapshot_id = await super().create_snapshot(request)
        self.requests_by_snapshot[snapshot_id] = request
        return snapshot_id

    async def create_artifacts(self, *, ingestion_run_id, artifacts) -> None:
        await super().create_artifacts(ingestion_run_id=ingestion_run_id, artifacts=artifacts)
        receipts = tuple(
            IngestionArtifactReceipt(
                ingestion_artifact_id=uuid4(),
                ingestion_run_id=ingestion_run_id,
                page_number=artifact.page_number or 0,
                artifact_key=artifact.metadata.artifact_key,
                storage_backend=artifact.storage_backend,
                object_key=artifact.object_key,
                raw_checksum=artifact.metadata.raw_checksum,
                byte_size=artifact.metadata.byte_size,
                content_type=artifact.metadata.content_type,
            )
            for artifact in artifacts
        )
        self.artifacts_by_run[ingestion_run_id] = receipts
        self.artifact_receipts.update((receipt.ingestion_artifact_id, receipt) for receipt in receipts)

    async def get_ingestion_artifact_receipts(self, *, ingestion_run_id):
        return self.artifacts_by_run.get(ingestion_run_id, ())

    async def get_ingestion_artifact_receipt(self, *, ingestion_artifact_id):
        return self.artifact_receipts.get(ingestion_artifact_id)

    async def get_snapshot_receipt(self, *, snapshot_id):
        request = self.requests_by_snapshot.get(snapshot_id)
        if request is None:
            return None
        return SnapshotProvenanceReceipt(
            source_id=_SOURCE_ID,
            source_code=request.ingestion.identity.source_code,
            endpoint_id=_ENDPOINT_ID,
            operation_id=request.operation_id,
            source_snapshot_id=snapshot_id,
            source_version=request.metadata.source_version,
            external_version=request.metadata.external_version,
            canonical_checksum=request.ingestion.canonical_checksum,
            canonicalization_spec_version=request.ingestion.canonicalization_spec_version,
            endpoint_receipt_hash=request.ingestion.endpoint_receipt_hash,
            verification_seal_id=None,
            verification_status=SnapshotVerificationStatus.PENDING,
            rejected_record_count=0,
            publication_verification_id=None,
        )

    async def append_snapshot_member(self, request: SourceSnapshotMemberCreate) -> SourceSnapshotMemberReceipt:
        existing = next(
            (
                member
                for member in self.members.setdefault(request.provenance.source_snapshot_id, [])
                if (member.locator, member.content_sha256) == (request.locator, request.content_sha256)
            ),
            None,
        )
        if existing is None:
            existing = SnapshotMemberBinding(
                source_snapshot_member_id=uuid4(),
                source_snapshot_id=request.provenance.source_snapshot_id,
                member_kind=request.member_kind,
                ingestion_artifact_id=request.ingestion_artifact_id,
                locator=request.locator,
                content_sha256=request.content_sha256,
            )
            self.members[request.provenance.source_snapshot_id].append(existing)
        return SourceSnapshotMemberReceipt(
            source_snapshot_member_id=existing.source_snapshot_member_id,
            source_snapshot_id=existing.source_snapshot_id,
            member_kind=existing.member_kind,
            endpoint_id=None,
            operation_id=None,
            ingestion_artifact_id=existing.ingestion_artifact_id,
            content_sha256=existing.content_sha256,
        )

    async def get_snapshot_member_bindings(self, *, snapshot_id):
        return tuple(self.members.get(snapshot_id, ()))


def test_parser_preserves_order_and_table_but_excludes_editor_metadata() -> None:
    first = inspect_xml(_xml("EE", data_value="one"), "EE")
    second = inspect_xml(_xml("EE", data_value="two"), "EE")
    assert first["table_element_count"] == 1
    assert first["raw_sha256"] != second["raw_sha256"]


@pytest.mark.parametrize(
    "raw,section,reason",
    [
        (
            b'<!DOCTYPE x><DOC type="EE" title="\xed\x9a\xa8\xeb\x8a\xa5\xed\x9a\xa8\xea\xb3\xbc"/>',
            "EE",
            "XML_DECLARATION_UNSAFE",
        ),
        (_xml("UD"), "EE", "XML_SECTION_MISMATCH"),
        (b'<DOC type="EE" title="\xed\x9a\xa8\xeb\x8a\xa5\xed\x9a\xa8\xea\xb3\xbc"/>', "EE", "XML_BODY_EMPTY"),
    ],
)
def test_parser_fails_closed(raw: bytes, section: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        inspect_xml(raw, section)


def test_nn_exact_shape_reports_official_blank_without_filling_it() -> None:
    result = inspect_xml(_nn_xml(empty_index=2), "NN")
    assert result["content_status"] == "PARTIAL_OFFICIAL"
    assert len(cast(list[str], result["empty_article_titles"])) == 1


def test_plan_is_deterministic_and_requires_the_selected_member_set(tmp_path: Path) -> None:
    first = _plan(tmp_path)
    second = load_mfds_label_plan(
        item_seq="200610660",
        input_dir=_input_directory(
            tmp_path,
            data_value="changed-decoration",
            collected_at=_COLLECTED_AT + timedelta(hours=1),
        ),
        identity=_IDENTITY,
        endpoint_receipt_hash=_RECEIPT_HASH,
        collected_at=_COLLECTED_AT + timedelta(hours=1),
        include_e_drug=False,
    )
    assert first.ingestion.canonical_checksum == second.ingestion.canonical_checksum
    assert first.ingestion.raw_manifest_checksum != second.ingestion.raw_manifest_checksum
    assert [document.section for document in first.documents] == ["EE", "UD", "NB"]
    assert first.source_version != second.source_version

    missing = _input_directory(tmp_path)
    (missing / "NB.xml").unlink()
    with pytest.raises(ValueError, match="XML_FILE_INVALID"):
        load_mfds_label_plan(
            item_seq="200610660",
            input_dir=missing,
            identity=_IDENTITY,
            endpoint_receipt_hash=_RECEIPT_HASH,
            collected_at=_COLLECTED_AT,
            include_e_drug=False,
        )


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("item_seq", "199999999", "ACQUISITION_EVIDENCE_BINDING_MISMATCH"),
        ("endpoint_receipt_hash", "b" * 64, "ACQUISITION_EVIDENCE_BINDING_MISMATCH"),
    ],
)
def test_plan_rejects_acquisition_evidence_bound_to_another_run(
    tmp_path: Path,
    field: str,
    value: str,
    reason: str,
) -> None:
    directory = _input_directory(tmp_path)
    manifest_path = directory / ACQUISITION_EVIDENCE_FILE
    manifest = json.loads(manifest_path.read_text())
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match=reason):
        load_mfds_label_plan(
            item_seq="200610660",
            input_dir=directory,
            identity=_IDENTITY,
            endpoint_receipt_hash=_RECEIPT_HASH,
            collected_at=_COLLECTED_AT,
            include_e_drug=False,
        )


@pytest.mark.parametrize("field", ["source_url", "raw_sha256", "byte_size", "content_type"])
def test_plan_rejects_artifact_evidence_mismatch(tmp_path: Path, field: str) -> None:
    directory = _input_directory(tmp_path)
    manifest_path = directory / ACQUISITION_EVIDENCE_FILE
    manifest = json.loads(manifest_path.read_text())
    replacements = {
        "source_url": "https://example.invalid/wrong-product",
        "raw_sha256": "b" * 64,
        "byte_size": 1,
        "content_type": "text/html",
    }
    manifest["documents"][0][field] = replacements[field]
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(
        ValueError,
        match="ACQUISITION_EVIDENCE_BINDING_MISMATCH|Raw artifact (checksum|byte size) mismatch",
    ):
        load_mfds_label_plan(
            item_seq="200610660",
            input_dir=directory,
            identity=_IDENTITY,
            endpoint_receipt_hash=_RECEIPT_HASH,
            collected_at=_COLLECTED_AT,
            include_e_drug=False,
        )


async def test_persist_commit_boundary_and_requery_are_complete_and_idempotent(tmp_path: Path) -> None:
    repository = FakeMfdsLabelRepository()
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    first_plan = _plan(tmp_path)
    first = await persist_mfds_label_plan(
        plan=first_plan,
        repository=repository,
        artifact_store=store,
        metadata=_metadata(first_plan),
    )
    assert first.persistence.decision is SnapshotIngestionDecision.CREATED
    assert len(first.member_ids) == 3
    checked = await requery_mfds_label_persistence(
        plan=first_plan,
        receipt=first,
        repository=repository,
        artifact_reader=store,
    )
    assert checked.member_count == 3

    second_plan = load_mfds_label_plan(
        item_seq="200610660",
        input_dir=_input_directory(
            tmp_path,
            data_value="changed-editor-metadata",
            collected_at=_COLLECTED_AT + timedelta(hours=1),
        ),
        identity=_IDENTITY,
        endpoint_receipt_hash=_RECEIPT_HASH,
        collected_at=_COLLECTED_AT + timedelta(hours=1),
        include_e_drug=False,
    )
    second = await persist_mfds_label_plan(
        plan=second_plan,
        repository=repository,
        artifact_store=store,
        metadata=_metadata(second_plan),
    )
    assert second.persistence.decision is SnapshotIngestionDecision.NO_CHANGE
    assert second.member_ids == first.member_ids
    assert second.source_version == first.source_version
    assert second_plan.ingestion.raw_manifest_checksum != first_plan.ingestion.raw_manifest_checksum
    assert len(repository.artifacts_by_run[second.persistence.ingestion_run_id]) == 3
    checked_again = await requery_mfds_label_persistence(
        plan=second_plan,
        receipt=second,
        repository=repository,
        artifact_reader=store,
    )
    assert checked_again.snapshot_id == checked.snapshot_id
    assert checked_again.member_count == 3


async def test_no_change_with_missing_member_fails_closed(tmp_path: Path) -> None:
    repository = FakeMfdsLabelRepository()
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    first_plan = _plan(tmp_path)
    first = await persist_mfds_label_plan(
        plan=first_plan,
        repository=repository,
        artifact_store=store,
        metadata=_metadata(first_plan),
    )
    assert first.persistence.snapshot_id is not None
    repository.members[first.persistence.snapshot_id].pop()
    second_plan = replace(first_plan, source_version=first_plan.source_version)
    with pytest.raises(ValueError, match="SNAPSHOT_MEMBER_SET_MISMATCH"):
        await persist_mfds_label_plan(
            plan=second_plan,
            repository=repository,
            artifact_store=store,
            metadata=_metadata(second_plan),
        )


async def test_requery_rejects_artifact_from_another_storage_backend(tmp_path: Path) -> None:
    repository = FakeMfdsLabelRepository()
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    plan = _plan(tmp_path)
    receipt = await persist_mfds_label_plan(
        plan=plan,
        repository=repository,
        artifact_store=store,
        metadata=_metadata(plan),
    )
    artifact_id = next(iter(repository.artifact_receipts))
    repository.artifact_receipts[artifact_id] = replace(
        repository.artifact_receipts[artifact_id],
        storage_backend="S3_PRIVATE",
    )

    with pytest.raises(ValueError, match="INGESTION_ARTIFACT_RECEIPT_MISMATCH"):
        await requery_mfds_label_persistence(
            plan=plan,
            receipt=receipt,
            repository=repository,
            artifact_reader=store,
        )


async def test_metadata_mismatch_is_rejected_before_storage(tmp_path: Path) -> None:
    repository = FakeMfdsLabelRepository()
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    plan = _plan(tmp_path)
    with pytest.raises(ValueError, match="MFDS_LABEL_METADATA_MISMATCH"):
        await persist_mfds_label_plan(
            plan=plan,
            repository=repository,
            artifact_store=store,
            metadata=replace(_metadata(plan), parser_version="wrong"),
        )
    assert not repository.snapshots


def test_parse_mfds_label_artifact_public_seam() -> None:
    raw = _xml("EE")
    parsed = parse_mfds_label_artifact(raw, "EE")
    assert isinstance(parsed, ParsedMfdsLabelDocument)
    assert parsed.section == "EE"
    assert parsed.document_title == "효능효과"
    assert parsed.content_status == "COMPLETE"
    assert parsed.empty_article_titles == ()
    assert parsed.root.tag == "DOC"
    assert parsed.article_count == 1
    assert parsed.paragraph_count == 1
    assert parsed.nonempty_paragraph_count == 1
    assert not hasattr(parsed, "canonical_structure")


def test_parse_mfds_label_artifact_empty_title_rejected() -> None:
    raw = b'<DOC type="EE" title=""><ARTICLE title="\xed\x95\xa9\xec\x84\xb1"><PARAGRAPH>\xeb\xb3\xb8\xeb\xac\xb8</PARAGRAPH></ARTICLE></DOC>'
    with pytest.raises(ValueError, match="XML_SECTION_MISMATCH"):
        parse_mfds_label_artifact(raw, "EE")


def test_parse_mfds_label_artifact_section_mismatch() -> None:
    raw = _xml("UD")
    with pytest.raises(ValueError, match="XML_SECTION_MISMATCH"):
        parse_mfds_label_artifact(raw, "EE")


def test_parse_mfds_label_artifact_empty_body() -> None:
    raw = ('<DOC type="EE" title="효능효과"><ARTICLE title=""><PARAGRAPH></PARAGRAPH></ARTICLE></DOC>').encode()
    with pytest.raises(ValueError, match="XML_BODY_EMPTY"):
        parse_mfds_label_artifact(raw, "EE")


def test_parse_mfds_label_artifact_nn_article_set_invalid() -> None:
    raw = (
        '<DOC type="NN" title="e약은요 정보"><ARTICLE title="잘못된 제목"><PARAGRAPH>본문</PARAGRAPH></ARTICLE></DOC>'
    ).encode()
    with pytest.raises(ValueError, match="XML_ARTICLE_SET_INVALID"):
        parse_mfds_label_artifact(raw, "NN")


def test_parsed_document_repr_hides_source_body() -> None:
    parsed = parse_mfds_label_artifact(_xml("EE"), "EE")
    rendered = repr(parsed)
    assert "합성 제목" not in rendered
    assert "합성 본문" not in rendered
    assert "<table>" not in rendered
    assert "root=" not in rendered
    assert "canonical_structure" not in rendered
    assert "section='EE'" in rendered
    assert "content_status='COMPLETE'" in rendered


def _deeply_nested_xml(depth: int) -> bytes:
    inner = "합성 본문" + "<SPAN>" * depth + "심층" + "</SPAN>" * depth
    return (
        f'<DOC type="EE" title="효능효과"><ARTICLE title="합성 제목"><PARAGRAPH>{inner}</PARAGRAPH></ARTICLE></DOC>'
    ).encode()


def test_inspect_xml_accepts_deeply_nested_valid_document() -> None:
    raw = _deeply_nested_xml(1_100)
    assert len(raw) < 2 * 1024 * 1024
    report = inspect_xml(raw, "EE")
    assert report["content_status"] == "COMPLETE"
    assert report["article_count"] == 1
    assert report["paragraph_count"] == 1
    assert report["nonempty_paragraph_count"] == 1
    assert report["table_element_count"] == 0


def test_parse_mfds_label_artifact_accepts_deeply_nested_valid_document() -> None:
    parsed = parse_mfds_label_artifact(_deeply_nested_xml(1_100), "EE")
    assert parsed.content_status == "COMPLETE"
    assert parsed.nonempty_paragraph_count == 1


def test_inspect_xml_report_keys_and_values_are_preserved() -> None:
    raw = _xml("EE")
    report = inspect_xml(raw, "EE")
    assert list(report) == [
        "section",
        "byte_size",
        "raw_sha256",
        "document_title",
        "article_count",
        "paragraph_count",
        "nonempty_paragraph_count",
        "empty_article_titles",
        "content_status",
        "table_element_count",
    ]
    assert report == {
        "section": "EE",
        "byte_size": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "document_title": "효능효과",
        "article_count": 1,
        "paragraph_count": 1,
        "nonempty_paragraph_count": 1,
        "empty_article_titles": [],
        "content_status": "COMPLETE",
        "table_element_count": 1,
    }


def test_inspect_xml_nn_partial_report_is_preserved() -> None:
    report = inspect_xml(_nn_xml(empty_index=2), "NN")
    assert report["content_status"] == "PARTIAL_OFFICIAL"
    assert report["empty_article_titles"] == [NN_ARTICLE_TITLES[2]]
    assert report["article_count"] == len(NN_ARTICLE_TITLES)


def test_parse_mfds_label_artifact_preserves_raw_parsed_structure() -> None:
    """seam은 parsed 원문 구조를 보존하고 NFC 정규화·공백 정리를 수행하지 않는다.

    NFC 정규화, trailing space 제거, 빈 줄 축약, text projection은 Task 2
    renderer/materialization의 책임이다. (XML 1.0이 요구하는 line-ending 정규화는
    expat의 동작이며 이 seam의 책임 경계가 아니다.)
    """
    decomposed = unicodedata.normalize("NFD", "효능")
    assert decomposed != unicodedata.normalize("NFC", decomposed)
    raw = (
        '<DOC type="EE" title="효능효과">'
        f'<ARTICLE title="합성 제목"><PARAGRAPH>{decomposed}\n\n\n둘째 줄  </PARAGRAPH></ARTICLE>'
        "</DOC>"
    ).encode()

    parsed = parse_mfds_label_artifact(raw, "EE")
    paragraph = next(iter(parsed.root.iter("PARAGRAPH")))
    text = "".join(paragraph.itertext())

    assert decomposed in text
    assert unicodedata.normalize("NFC", decomposed) not in text
    assert "\n\n\n" in text
    assert text.endswith("둘째 줄  ")
