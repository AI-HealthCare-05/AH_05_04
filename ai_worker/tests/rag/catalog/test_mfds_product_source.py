import hashlib
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from ai_worker.tasks.rag.catalog.mfds_product_source import (
    PRODUCT_SOURCE_AUTHORITY,
    ProductSourceBindingError,
    map_mfds_product_record_status,
    read_product_input,
)
from ai_worker.tasks.rag.catalog.types import CandidateRecordStatus
from ai_worker.tasks.rag.source_ingestion.mfds_label import IngestionArtifactReceipt
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotVerificationStatus

SNAPSHOT_ID = UUID("00000000-0000-4000-8000-000000000001")
RUN_ID = UUID("00000000-0000-4000-8000-000000000002")


def _page(records: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {
            "header": {"resultCode": "00"},
            "body": {"items": records, "pageNo": 1, "numOfRows": 100, "totalCount": len(records)},
        }
    ).encode()


class FakeRepository:
    def __init__(self, *, snapshot=None, run=None, artifacts=()):
        self.snapshot = snapshot
        self.run = run
        self.artifacts = artifacts

    async def get_snapshot_receipt(self, *, snapshot_id):
        return self.snapshot

    async def get_ingestion_run_receipt(self, *, ingestion_run_id):
        return self.run

    async def get_ingestion_artifact_receipts(self, *, ingestion_run_id):
        return self.artifacts


class FakeReader:
    def __init__(self, content: bytes):
        self.content = content
        self.calls = 0

    def read_verified(self, *, object_key, metadata):
        self.calls += 1
        return self.content


def _snapshot(**overrides):
    values = dict(
        source_snapshot_id=SNAPSHOT_ID,
        source_code="MFDS_PRODUCT_APPROVAL",
        endpoint_code="MFDS_PRODUCT_APPROVAL_API",
        operation_code="LIST_APPROVED_PRODUCTS",
        source_version="api:2026-09-19T03:34:30Z:" + "a" * 64,
        canonical_checksum="b" * 64,
        canonicalization_spec_version="mfds-product-approval@1",
        endpoint_receipt_hash="c" * 64,
        verification_status=SnapshotVerificationStatus.CURRENT,
        rejected_record_count=0,
    )
    values.update(overrides)
    return SimpleNamespace(
        **values,
        endpoint_id=uuid4(),
        operation_id=uuid4(),
        validate_provenance=lambda: None,
    )


def _run(**overrides):
    values = dict(snapshot_id=SNAPSHOT_ID, run_status="SUCCEEDED", failure_code=None, operation_id=uuid4())
    values.update(overrides)
    return SimpleNamespace(**values)


def _artifact(content: bytes, *, object_key="sha/item.json"):
    checksum = hashlib.sha256(content).hexdigest()
    return IngestionArtifactReceipt(
        ingestion_artifact_id=uuid4(),
        ingestion_run_id=RUN_ID,
        page_number=1,
        artifact_key="item.json",
        storage_backend="LOCAL_PRIVATE",
        object_key=object_key,
        raw_checksum=checksum,
        byte_size=len(content),
        content_type="application/json",
    )


@pytest.mark.asyncio
async def test_exact_product_binding_is_deterministic():
    content = _page([{"ITEM_SEQ": "P-001", "ITEM_NAME": "합성 제품", "ENTP_NAME": "제조사"}])
    product, receipt = await read_product_input(
        repository=FakeRepository(snapshot=_snapshot(), run=_run(), artifacts=(_artifact(content),)),
        artifact_reader=FakeReader(content),
        source_snapshot_id=str(SNAPSHOT_ID),
        ingestion_run_id=str(RUN_ID),
        item_seq="P-001",
    )
    assert product.canonical_code == "P-001"
    assert product.source_record_key == "ITEM_SEQ:P-001"
    assert product.product_name == "합성 제품"
    assert product.manufacturer_name == "제조사"
    assert receipt.source_snapshot_id == SNAPSHOT_ID


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"source_code": "OTHER"},
        {"endpoint_code": "OTHER"},
        {"operation_code": "OTHER"},
        {"verification_status": SnapshotVerificationStatus.PENDING},
        {"verification_status": SnapshotVerificationStatus.STALE},
        {"verification_status": SnapshotVerificationStatus.FAILED},
        {"rejected_record_count": 1},
    ],
)
async def test_snapshot_binding_fails_before_artifact_read(changes):
    content = _page([{"ITEM_SEQ": "P-001", "ITEM_NAME": "제품"}])
    reader = FakeReader(content)
    with pytest.raises(ProductSourceBindingError, match="BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY"):
        await read_product_input(
            repository=FakeRepository(snapshot=_snapshot(**changes), run=_run(), artifacts=(_artifact(content),)),
            artifact_reader=reader,
            source_snapshot_id=str(SNAPSHOT_ID),
            ingestion_run_id=str(RUN_ID),
            item_seq="P-001",
        )
    assert reader.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [{"snapshot_id": uuid4()}, {"run_status": "FAILED"}, {"failure_code": "SCHEMA_DRIFT"}],
)
async def test_ingestion_run_binding_fails_closed(changes):
    content = _page([{"ITEM_SEQ": "P-001", "ITEM_NAME": "제품"}])
    with pytest.raises(ProductSourceBindingError, match="BLOCKED_BY_PRODUCT_INGESTION_RUN"):
        await read_product_input(
            repository=FakeRepository(snapshot=_snapshot(), run=_run(**changes), artifacts=(_artifact(content),)),
            artifact_reader=FakeReader(content),
            source_snapshot_id=str(SNAPSHOT_ID),
            ingestion_run_id=str(RUN_ID),
            item_seq="P-001",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "records", [[], [{"ITEM_SEQ": "P-002", "ITEM_NAME": "다른"}, {"ITEM_SEQ": "P-002", "ITEM_NAME": "중복"}]]
)
async def test_item_seq_must_match_exactly_once(records):
    content = _page(records)
    with pytest.raises(ProductSourceBindingError, match="BLOCKED_BY_PRODUCT_ITEM_SEQ_CARDINALITY"):
        await read_product_input(
            repository=FakeRepository(snapshot=_snapshot(), run=_run(), artifacts=(_artifact(content),)),
            artifact_reader=FakeReader(content),
            source_snapshot_id=str(SNAPSHOT_ID),
            ingestion_run_id=str(RUN_ID),
            item_seq="P-001",
        )


@pytest.mark.asyncio
async def test_exact_authority_maps_product_to_active():
    content = _page([{"ITEM_SEQ": "P-001", "ITEM_NAME": "제품"}])
    product, _ = await read_product_input(
        repository=FakeRepository(snapshot=_snapshot(), run=_run(), artifacts=(_artifact(content),)),
        artifact_reader=FakeReader(content),
        source_snapshot_id=str(SNAPSHOT_ID),
        ingestion_run_id=str(RUN_ID),
        item_seq="P-001",
    )
    assert product.product_status is CandidateRecordStatus.ACTIVE


def test_status_mapping_rejects_wrong_authority():
    wrong = PRODUCT_SOURCE_AUTHORITY.__class__(
        "OTHER", PRODUCT_SOURCE_AUTHORITY.endpoint_code, PRODUCT_SOURCE_AUTHORITY.operation_code
    )
    with pytest.raises(ProductSourceBindingError, match="BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY"):
        map_mfds_product_record_status(authority=wrong, record={"ITEM_NAME": "취소된 제품"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        {"ITEM_SEQ": "P-001", "ITEM_NAME": "취소 제품", "ENTP_NAME": "제조사", "ITEM_PERMIT_DATE": ""},
        {"ITEM_SEQ": "P-001", "ITEM_NAME": "만료 캡슐 10mg", "ENTP_NAME": "다른 제조사"},
    ],
)
async def test_status_mapping_ignores_non_authoritative_product_fields(record):
    content = _page([record])
    product, _ = await read_product_input(
        repository=FakeRepository(snapshot=_snapshot(), run=_run(), artifacts=(_artifact(content),)),
        artifact_reader=FakeReader(content),
        source_snapshot_id=str(SNAPSHOT_ID),
        ingestion_run_id=str(RUN_ID),
        item_seq="P-001",
    )
    assert product.product_status is CandidateRecordStatus.ACTIVE
