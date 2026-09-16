import hashlib
import unicodedata
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from ai_worker.adapters.local_private_source_artifact_finalizer import ArtifactObjectKeyError
from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.tasks.rag.knowledge_evidence_index import KnowledgeChunkIdentity
from ai_worker.tasks.rag.knowledge_materialization import (
    KnowledgeMaterializationError,
    KnowledgeMaterializationFailureReason,
    KnowledgeMaterializationRequest,
    MaterializationSourceDocument,
    materialize_documents,
)
from ai_worker.tasks.rag.mfds_label_chunk_policy import CHUNK_POLICY_VERSION
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    RawArtifactIntegrityError,
    RawArtifactMetadata,
    RawArtifactUnavailableError,
)
from ai_worker.tasks.rag.source_ingestion.mfds_label import (
    CANONICALIZATION_SPEC_VERSION,
    LOCAL_PRIVATE_STORAGE_BACKEND,
    NN_ARTICLE_TITLES,
    NORMALIZATION_VERSION,
    OBSERVED_CONTENT_TYPE,
    PARSER_VERSION,
    SCHEMA_VERSION,
)

ITEM_SEQ = "200610660"
SNAPSHOT_ID = UUID("00000000-0000-4000-8000-000000000001")
_SECTION_TITLES = {"EE": "효능효과", "UD": "용법용량", "NB": "사용상의주의사항", "NN": "e약은요 정보"}


class FakeArtifactReader:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.call_count = 0

    def read_verified(self, *, object_key: str, metadata: RawArtifactMetadata) -> bytes:
        self.call_count += 1
        if object_key in self.payloads:
            return self.payloads[object_key]
        raise RawArtifactUnavailableError("Raw artifact could not be read.")


def _make_xml(section: str, title: str | None, text: str) -> bytes:
    doc_title = title if title is not None else _SECTION_TITLES.get(section, f"{section}제목")
    return (
        f'<DOC type="{section}" title="{doc_title}"><ARTICLE title="개요"><PARAGRAPH>{text}</PARAGRAPH></ARTICLE></DOC>'
    ).encode()


def _make_source_doc(
    *,
    section: str,
    raw_bytes: bytes,
    item_seq: str = ITEM_SEQ,
    snapshot_id: UUID = SNAPSHOT_ID,
    member_id: UUID | None = None,
) -> MaterializationSourceDocument:
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()
    object_key = LocalPrivateSourceArtifactStore.object_key_for_checksum(raw_sha)
    section_order = ("EE", "UD", "NB", "NN")
    page_number = section_order.index(section) + 1 if section in section_order else 99
    op_id = UUID("00000000-0000-4000-8000-000000000030")
    return MaterializationSourceDocument(
        source_id=UUID("00000000-0000-4000-8000-000000000010"),
        source_code="MFDS",
        source_lifecycle_status="ACTIVE",
        endpoint_id=UUID("00000000-0000-4000-8000-000000000020"),
        endpoint_code="LABEL",
        endpoint_lifecycle_status="APPROVED",
        endpoint_runtime_status="ENABLED",
        endpoint_acquisition_status="VERIFIED",
        operation_id=op_id,
        operation_code="GET_LABEL",
        operation_runtime_status="ENABLED",
        operation_acquisition_status="APPROVED",
        snapshot_id=snapshot_id,
        source_version="api:2026-09-15T06:00:00Z",
        canonical_checksum="b" * 64,
        raw_manifest_checksum="c" * 64,
        schema_version=SCHEMA_VERSION,
        parser_version=PARSER_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        canonicalization_spec_version=CANONICALIZATION_SPEC_VERSION,
        snapshot_verification_status="CURRENT",
        ingestion_run_id=UUID("00000000-0000-4000-8000-000000000040"),
        ingestion_run_status="SUCCEEDED",
        member_id=member_id or uuid4(),
        member_kind="LABEL",
        locator=f"mfds-label/{item_seq}/{section}",
        content_sha256=raw_sha,
        ingestion_artifact_id=uuid4(),
        artifact_key=f"mfds-label/{item_seq}/{section}.xml",
        section=section,
        artifact_kind="RAW_RESPONSE",
        page_number=page_number,
        storage_backend=LOCAL_PRIVATE_STORAGE_BACKEND,
        reject_code=None,
        parser_location=None,
        raw_checksum=raw_sha,
        byte_size=len(raw_bytes),
        content_type=OBSERVED_CONTENT_TYPE,
        object_key=object_key,
        ingestion_run_snapshot_id=snapshot_id,
        ingestion_run_operation_id=op_id,
    )


def _triple_fixture() -> tuple[
    KnowledgeMaterializationRequest,
    tuple[MaterializationSourceDocument, ...],
    FakeArtifactReader,
]:
    ee_bytes = _make_xml("EE", "효능효과", "고혈압 치료")
    ud_bytes = _make_xml("UD", "용법용량", "1일 1회 복용")
    nb_bytes = _make_xml("NB", "사용상의주의사항", "부작용 주의")

    ee_doc = _make_source_doc(section="EE", raw_bytes=ee_bytes)
    ud_doc = _make_source_doc(section="UD", raw_bytes=ud_bytes)
    nb_doc = _make_source_doc(section="NB", raw_bytes=nb_bytes)

    source_docs = (ee_doc, ud_doc, nb_doc)
    request = KnowledgeMaterializationRequest(
        snapshot_id=SNAPSHOT_ID,
        member_ids=tuple(d.member_id for d in source_docs),
        expected_item_seq=ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
    )
    reader = FakeArtifactReader(
        {
            ee_doc.object_key: ee_bytes,
            ud_doc.object_key: ud_bytes,
            nb_doc.object_key: nb_bytes,
        }
    )
    return request, source_docs, reader


# =========================================================================
# 1. Request Validation
# =========================================================================


@pytest.mark.parametrize("invalid_item_seq", ["", "123", "20061066", "2006106600", "20061066A", "abcdefghi"])
def test_request_validation_item_seq(invalid_item_seq: str) -> None:
    request, source_docs, reader = _triple_fixture()
    bad_req = replace(request, expected_item_seq=invalid_item_seq)
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(bad_req, source_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.REQUEST_INVALID
    assert reader.call_count == 0


def test_request_validation_duplicate_member_ids() -> None:
    request, source_docs, reader = _triple_fixture()
    m0 = request.member_ids[0]
    bad_req = replace(request, member_ids=(m0, m0, request.member_ids[1]))
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(bad_req, source_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.REQUEST_INVALID
    assert reader.call_count == 0


def test_request_validation_empty_member_ids() -> None:
    request, source_docs, reader = _triple_fixture()
    bad_req = replace(request, member_ids=())
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(bad_req, source_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.REQUEST_INVALID
    assert reader.call_count == 0


def test_request_validation_chunk_policy() -> None:
    request, source_docs, reader = _triple_fixture()
    bad_req = replace(request, chunk_policy_version="unsupported-policy@9")
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(bad_req, source_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.CHUNK_POLICY_UNSUPPORTED
    assert reader.call_count == 0


def test_request_structure_error_is_not_chunk_policy_unsupported() -> None:
    request, source_docs, reader = _triple_fixture()
    bad_req = replace(request, expected_item_seq="bad", chunk_policy_version="unsupported@9")
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(bad_req, source_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.REQUEST_INVALID


# =========================================================================
# 2. Section Set Validation
# =========================================================================


def test_request_section_set_required_triple_accepted() -> None:
    request, source_docs, reader = _triple_fixture()
    drafts = materialize_documents(request, source_docs, reader)
    assert len(drafts) == 3
    assert [d.chunks[0].chunk_text for d in drafts]  # non-empty


def test_request_section_set_nn_alone_accepted() -> None:
    articles_xml = "".join(f'<ARTICLE title="{t}"><PARAGRAPH>내용</PARAGRAPH></ARTICLE>' for t in NN_ARTICLE_TITLES)
    nn_bytes = f'<DOC type="NN" title="e약은요 정보">{articles_xml}</DOC>'.encode()
    nn_doc = _make_source_doc(section="NN", raw_bytes=nn_bytes)
    request = KnowledgeMaterializationRequest(
        snapshot_id=SNAPSHOT_ID,
        member_ids=(nn_doc.member_id,),
        expected_item_seq=ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
    )
    reader = FakeArtifactReader({nn_doc.object_key: nn_bytes})
    drafts = materialize_documents(request, (nn_doc,), reader)
    assert len(drafts) == 1
    assert drafts[0].chunks


def test_request_section_set_nn_official_empty_article_fail_closed() -> None:
    # 7개 중 1개 항목에 공백 PARAGRAPH만 존재하는 공식 빈 ARTICLE (PARTIAL_OFFICIAL)
    articles_xml = []
    for i, t in enumerate(NN_ARTICLE_TITLES):
        if i == 2:
            articles_xml.append(f'<ARTICLE title="{t}"><PARAGRAPH>   </PARAGRAPH></ARTICLE>')
        else:
            articles_xml.append(f'<ARTICLE title="{t}"><PARAGRAPH>정상 본문 내용</PARAGRAPH></ARTICLE>')
    nn_bytes = f'<DOC type="NN" title="e약은요 정보">{"".join(articles_xml)}</DOC>'.encode()
    nn_doc = _make_source_doc(section="NN", raw_bytes=nn_bytes)
    request = KnowledgeMaterializationRequest(
        snapshot_id=SNAPSHOT_ID,
        member_ids=(nn_doc.member_id,),
        expected_item_seq=ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
    )
    reader = FakeArtifactReader({nn_doc.object_key: nn_bytes})
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, (nn_doc,), reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.CHUNK_POLICY_UNSUPPORTED
    # materialization draft 미생성 및 silent skip 금지 검증 (reader는 호출되었으나 draft 반환 없이 예외 발생)
    assert reader.call_count == 1


def test_request_section_set_nn_official_empty_article_without_paragraph_fail_closed() -> None:
    # 7개 중 1개 항목에 PARAGRAPH 태그 자체가 없는 공식 빈 ARTICLE
    articles_xml = []
    for i, t in enumerate(NN_ARTICLE_TITLES):
        if i == 0:
            articles_xml.append(f'<ARTICLE title="{t}"></ARTICLE>')
        else:
            articles_xml.append(f'<ARTICLE title="{t}"><PARAGRAPH>정상 본문 내용</PARAGRAPH></ARTICLE>')
    nn_bytes = f'<DOC type="NN" title="e약은요 정보">{"".join(articles_xml)}</DOC>'.encode()
    nn_doc = _make_source_doc(section="NN", raw_bytes=nn_bytes)
    request = KnowledgeMaterializationRequest(
        snapshot_id=SNAPSHOT_ID,
        member_ids=(nn_doc.member_id,),
        expected_item_seq=ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
    )
    reader = FakeArtifactReader({nn_doc.object_key: nn_bytes})
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, (nn_doc,), reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.CHUNK_POLICY_UNSUPPORTED
    assert reader.call_count == 1


@pytest.mark.parametrize(
    "sections",
    [
        ("EE", "UD"),
        ("NB",),
        ("UD", "NB"),
        ("EE", "EE", "UD", "NB"),
        ("EE", "UD", "NB", "NN"),
        ("EE", "NN"),
        ("EE", "UD", "XX"),
    ],
)
def test_request_section_set_rejections(sections: tuple[str, ...]) -> None:
    docs = []
    payloads = {}
    for sec in sections:
        raw = _make_xml(sec, None, "본문")
        doc = _make_source_doc(section=sec, raw_bytes=raw)
        docs.append(doc)
        payloads[doc.object_key] = raw

    request = KnowledgeMaterializationRequest(
        snapshot_id=SNAPSHOT_ID,
        member_ids=tuple(d.member_id for d in docs),
        expected_item_seq=ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
    )
    reader = FakeArtifactReader(payloads)
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, tuple(docs), reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.REQUEST_INVALID
    assert reader.call_count == 0


def test_request_section_set_mixed_item_seq_rejected() -> None:
    request, source_docs, reader = _triple_fixture()
    bad_ud = replace(source_docs[1], locator="mfds-label/999999999/UD")
    bad_docs = (source_docs[0], bad_ud, source_docs[2])
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, bad_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.REQUEST_INVALID
    assert reader.call_count == 0


def test_section_set_rejection_happens_before_artifact_read() -> None:
    request, source_docs, reader = _triple_fixture()
    bad_docs = (source_docs[0], source_docs[1])  # missing NB
    bad_req = replace(request, member_ids=(source_docs[0].member_id, source_docs[1].member_id))
    with pytest.raises(KnowledgeMaterializationError):
        materialize_documents(bad_req, bad_docs, reader)
    assert reader.call_count == 0


# =========================================================================
# 3. Pre-reader Checks & Binding Validations
# =========================================================================


def test_object_key_mismatch_rejected_before_reader_call() -> None:
    request, source_docs, reader = _triple_fixture()
    tampered_doc = replace(source_docs[0], object_key="sha256/00/tampered.artifact")
    tampered_docs = (tampered_doc, source_docs[1], source_docs[2])
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, tampered_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID
    assert reader.call_count == 0


def test_raw_artifact_metadata_construction_failure_maps_to_source_binding_invalid() -> None:
    request, source_docs, reader = _triple_fixture()
    invalid_doc = replace(source_docs[0], byte_size=-5)  # negative byte_size invalidates RawArtifactMetadata
    tampered_docs = (invalid_doc, source_docs[1], source_docs[2])
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, tampered_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID
    assert reader.call_count == 0


def test_snapshot_id_mismatch_rejected() -> None:
    request, source_docs, reader = _triple_fixture()
    mismatch_doc = replace(source_docs[0], snapshot_id=uuid4())
    tampered_docs = (mismatch_doc, source_docs[1], source_docs[2])
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, tampered_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID
    assert reader.call_count == 0


@pytest.mark.parametrize(
    "invalid_backend",
    ["S3_PRIVATE", "local_private_artifact", "LOCAL", "DISABLED", ""],
)
def test_storage_backend_not_local_private_rejected_before_reader_call(invalid_backend: str) -> None:
    request, source_docs, reader = _triple_fixture()
    tampered_doc = replace(source_docs[0], storage_backend=invalid_backend)
    tampered_docs = (tampered_doc, source_docs[1], source_docs[2])
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, tampered_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID
    assert reader.call_count == 0


# =========================================================================
# 4. Typed Reader Error Mapping
# =========================================================================


class ErrorInjectingReader:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.call_count = 0

    def read_verified(self, *, object_key: str, metadata: RawArtifactMetadata) -> bytes:
        self.call_count += 1
        raise self.exc


def test_reader_raw_artifact_integrity_error_maps_to_artifact_integrity_mismatch() -> None:
    request, source_docs, _ = _triple_fixture()
    reader = ErrorInjectingReader(RawArtifactIntegrityError("Raw artifact checksum mismatch."))
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, source_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH
    assert exc_info.value.__cause__ is reader.exc


def test_reader_raw_artifact_unavailable_error_maps_to_dependency_error() -> None:
    request, source_docs, _ = _triple_fixture()
    reader = ErrorInjectingReader(RawArtifactUnavailableError("Raw artifact could not be read."))
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, source_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.DEPENDENCY_ERROR
    assert exc_info.value.__cause__ is reader.exc


def test_reader_artifact_object_key_error_maps_to_source_binding_invalid() -> None:
    request, source_docs, _ = _triple_fixture()
    reader = ErrorInjectingReader(
        ArtifactObjectKeyError("Final Source artifact path must be read-only for the writer account.")
    )
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, source_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID
    assert exc_info.value.__cause__ is reader.exc


# =========================================================================
# 5. Parser Error Mapping
# =========================================================================


@pytest.mark.parametrize(
    "corrupt_xml",
    [
        b"not xml at all",
        '<DOC type="EE" title=""><ARTICLE><PARAGRAPH>내용</PARAGRAPH></ARTICLE></DOC>'.encode(),  # empty title
        '<DOC type="UD" title="효능"><ARTICLE><PARAGRAPH>내용</PARAGRAPH></ARTICLE></DOC>'.encode(),  # type mismatch
        '<DOC type="EE" title="효능"></DOC>'.encode(),  # body empty
    ],
)
def test_parser_rejection_mapping(corrupt_xml: bytes) -> None:
    request, source_docs, _ = _triple_fixture()
    reader = FakeArtifactReader({doc.object_key: corrupt_xml for doc in source_docs})
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, source_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.PARSER_REJECTED


# =========================================================================
# 6. Chunk Policy Error Mapping
# =========================================================================


def test_chunk_policy_rejection_mapping_empty_chunk() -> None:
    # Article with empty title and whitespace content produces CHUNK_POLICY_UNSUPPORTED
    ws_xml = '<DOC type="EE" title="효능효과"><ARTICLE title="효능"><PARAGRAPH>본문</PARAGRAPH></ARTICLE><ARTICLE title=""> </ARTICLE></DOC>'.encode()
    request, source_docs, _ = _triple_fixture()
    reader = FakeArtifactReader({doc.object_key: ws_xml for doc in source_docs})
    with pytest.raises(KnowledgeMaterializationError) as exc_info:
        materialize_documents(request, source_docs, reader)
    assert exc_info.value.reason is KnowledgeMaterializationFailureReason.CHUNK_POLICY_UNSUPPORTED


# =========================================================================
# 7. Hash Domain Separation
# =========================================================================


def test_document_content_hash_copies_member_content_sha256() -> None:
    request, source_docs, reader = _triple_fixture()
    drafts = materialize_documents(request, source_docs, reader)
    for draft, doc in zip(drafts, source_docs, strict=True):
        assert draft.document_content_hash == doc.content_sha256


def test_document_content_hash_follows_raw_bytes_not_normalization() -> None:
    # NFD version vs NFC version produce DIFFERENT document_content_hash
    text_nfc = "효능"
    text_nfd = unicodedata.normalize("NFD", text_nfc)

    articles_nfc = "".join(
        f'<ARTICLE title="{t}"><PARAGRAPH>{text_nfc}</PARAGRAPH></ARTICLE>' for t in NN_ARTICLE_TITLES
    )
    articles_nfd = "".join(
        f'<ARTICLE title="{t}"><PARAGRAPH>{text_nfd}</PARAGRAPH></ARTICLE>' for t in NN_ARTICLE_TITLES
    )

    xml_nn_nfc = f'<DOC type="NN" title="e약은요 정보">{articles_nfc}</DOC>'.encode()
    xml_nn_nfd = f'<DOC type="NN" title="e약은요 정보">{articles_nfd}</DOC>'.encode()

    doc_nn_nfc = _make_source_doc(section="NN", raw_bytes=xml_nn_nfc)
    doc_nn_nfd = _make_source_doc(section="NN", raw_bytes=xml_nn_nfd)

    req_nn_nfc = KnowledgeMaterializationRequest(
        snapshot_id=SNAPSHOT_ID,
        member_ids=(doc_nn_nfc.member_id,),
        expected_item_seq=ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
    )
    req_nn_nfd = KnowledgeMaterializationRequest(
        snapshot_id=SNAPSHOT_ID,
        member_ids=(doc_nn_nfd.member_id,),
        expected_item_seq=ITEM_SEQ,
        chunk_policy_version=CHUNK_POLICY_VERSION,
    )

    draft_nfc = materialize_documents(
        req_nn_nfc, (doc_nn_nfc,), FakeArtifactReader({doc_nn_nfc.object_key: xml_nn_nfc})
    )[0]
    draft_nfd = materialize_documents(
        req_nn_nfd, (doc_nn_nfd,), FakeArtifactReader({doc_nn_nfd.object_key: xml_nn_nfd})
    )[0]

    # document_content_hash follows raw bytes -> DIFFERENT
    assert draft_nfc.document_content_hash != draft_nfd.document_content_hash
    # chunk text and chunk content_hash are normalized -> IDENTICAL
    assert [c.chunk_text for c in draft_nfc.chunks] == [c.chunk_text for c in draft_nfd.chunks]
    assert [c.content_hash for c in draft_nfc.chunks] == [c.content_hash for c in draft_nfd.chunks]


# =========================================================================
# 8. Deterministic Order & Replay
# =========================================================================


def test_shuffled_source_document_order_yields_identical_drafts() -> None:
    request, source_docs, reader = _triple_fixture()
    ee, ud, nb = source_docs

    shuffled_docs = (nb, ee, ud)
    shuffled_req = replace(request, member_ids=(nb.member_id, ee.member_id, ud.member_id))

    drafts1 = materialize_documents(request, source_docs, reader)
    drafts2 = materialize_documents(shuffled_req, shuffled_docs, reader)

    assert drafts1 == drafts2
    assert [d.external_document_id for d in drafts1] == [
        f"mfds-label:{ITEM_SEQ}:EE",
        f"mfds-label:{ITEM_SEQ}:UD",
        f"mfds-label:{ITEM_SEQ}:NB",
    ]


def test_repeated_execution_yields_identical_drafts() -> None:
    request, source_docs, reader = _triple_fixture()
    drafts1 = materialize_documents(request, source_docs, reader)
    drafts2 = materialize_documents(request, source_docs, reader)
    assert drafts1 == drafts2


# =========================================================================
# 9. Safe Error Contracts (Reason-only)
# =========================================================================


def test_error_exposes_only_reason_value() -> None:
    err = KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
    assert str(err) == "SOURCE_BINDING_INVALID"
    assert err.reason is KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID


def test_error_sanitization_no_leak() -> None:
    secret_path = "/private/secret/path/to/artifact.xml"
    err = KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.DEPENDENCY_ERROR)
    assert secret_path not in str(err)
    assert secret_path not in repr(err)
    assert "confidential" not in str(err)
    assert "confidential" not in repr(err)


# =========================================================================
# 10. Downstream Identity Compatibility
# =========================================================================


def test_draft_provides_all_non_db_index_identity_fields() -> None:
    request, source_docs, reader = _triple_fixture()
    drafts = materialize_documents(request, source_docs, reader)
    doc = source_docs[0]
    draft = drafts[0]
    chunk = draft.chunks[0]

    # Verify that with a dummy chunk_id, KnowledgeChunkIdentity can be built and is valid
    identity = KnowledgeChunkIdentity(
        knowledge_chunk_id=uuid4(),
        source_snapshot_id=doc.snapshot_id,
        source_snapshot_member_id=draft.source_snapshot_member_id,
        source_code=doc.source_code,
        source_version=doc.source_version,
        canonical_checksum=doc.canonical_checksum,
        external_document_id=draft.external_document_id,
        chunk_index=chunk.chunk_index,
        content_hash=chunk.content_hash,
        locator=doc.locator,
    )
    assert identity.external_document_id == f"mfds-label:{ITEM_SEQ}:EE"
    assert identity.stable_coordinate == (
        doc.source_code,
        doc.source_version,
        draft.external_document_id,
        chunk.chunk_index,
    )
    assert unicodedata.normalize("NFC", identity.external_document_id) == identity.external_document_id
