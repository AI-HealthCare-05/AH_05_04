"""Synthetic HTTP product acquisition against a real PostgreSQL Snapshot boundary.

외부 MFDS actual API는 호출하지 않는다. 모든 응답은 합성 transport에서 나온다.
"""

import json

import httpx
import pytest
from sqlalchemy import func, select

from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.tasks.rag.source_ingestion.mfds_product import (
    acquire_mfds_product,
    build_product_snapshot_metadata,
    ingest_and_persist_mfds_product,
    load_product_receipt,
    observed_canonical_checksum,
    write_product_report,
)
from ai_worker.tasks.rag.source_ingestion.reject_codes import REJECT_CODE_CONTRACT_VERSION
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotIngestionDecision
from ai_worker.tests.rag.source_ingestion.test_mfds_product import (
    SECRET,
    envelope,
    product,
    receipt_file,
    resolver,
    store,
)
from app.models.rag_source import RagSourceIngestionArtifact, RagSourceIngestionRun, RagSourceSnapshot
from tests.integration.rag.test_reject_contract_165 import database as _database

# 165 통합 테스트의 일회용 schema fixture를 그대로 재사용한다.
database = _database

pytestmark = pytest.mark.asyncio


async def collect_and_store(factory, tmp_path, pages, *, receipt_path, rollback=False):
    def handler(request):
        return httpx.Response(200, json=pages[int(request.url.params["pageNo"]) - 1])

    async with factory.begin() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            acquisition = await acquire_mfds_product(
                gate=repository,
                client=client,
                secret_value=SECRET,
                spool_parent=tmp_path,
                resolver=resolver,
            )
        report = write_product_report(
            acquisition, receipt=load_product_receipt(receipt_path=receipt_path, repository_root=tmp_path)
        )
        result = await ingest_and_persist_mfds_product(
            acquisition=acquisition,
            repository=repository,
            artifact_store=store(tmp_path),
            metadata=build_product_snapshot_metadata(
                acquisition=acquisition, run_group_key="synthetic-product", verified_by="synthetic-actor"
            ),
            receipt_path=receipt_path,
            repository_root=tmp_path,
        )
        if rollback:
            raise RuntimeError("synthetic failure before commit")
    return result, acquisition, report


async def assert_nothing_persisted(factory, operation_id):
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceIngestionRun)
                .where(RagSourceIngestionRun.operation_id == operation_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceSnapshot)
                .where(RagSourceSnapshot.operation_id == operation_id)
            )
            == 0
        )


async def test_verified_acquisition_commits_a_snapshot_with_provenance(database, tmp_path):
    factory, operation_id = database
    rows = [product("P-001"), product("P-002")]
    receipt_path = receipt_file(tmp_path, validated_record_count=2)

    result, acquisition, report = await collect_and_store(
        factory,
        tmp_path,
        [envelope([rows[0]], total=2), envelope([rows[1]], page=2, total=2)],
        receipt_path=receipt_path,
    )

    assert result.decision is SnapshotIngestionDecision.CREATED
    assert result.snapshot_id
    checksum = observed_canonical_checksum(acquisition)
    async with factory() as session:
        repository = SqlAlchemySourceSnapshotRepository(session)
        provenance = await repository.get_snapshot_receipt(snapshot_id=result.snapshot_id)
        assert provenance is not None
        # source_version 결속과 Endpoint Receipt hash가 실제로 저장됐는지 확인한다.
        provenance.validate_provenance()
        assert provenance.canonical_checksum == checksum
        assert provenance.external_version is None
        assert provenance.source_version.endswith(f":{checksum}")
        assert provenance.rejected_record_count == 0
        assert await session.scalar(select(func.count()).select_from(RagSourceIngestionArtifact)) == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceSnapshot)
                .where(RagSourceSnapshot.operation_id == operation_id)
            )
            == 1
        )
    assert SECRET not in report.read_text()


async def test_identity_rejections_commit_a_failed_run_with_reject_artifacts(database, tmp_path):
    factory, operation_id = database
    receipt_path = receipt_file(tmp_path, validated_record_count=2)

    result, _, report = await collect_and_store(
        factory, tmp_path, [envelope([product("P-001"), {}])], receipt_path=receipt_path
    )

    assert result.snapshot_id is None
    assert result.failure_code == "PARSER_VALIDATION_FAILED"
    async with factory() as session:
        receipt = await SqlAlchemySourceSnapshotRepository(session).get_attempt_receipt(
            ingestion_run_id=result.ingestion_run_id
        )
        assert receipt is not None
        assert receipt.decision is SnapshotIngestionDecision.VALIDATION_FAILED
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceSnapshot)
                .where(RagSourceSnapshot.operation_id == operation_id)
            )
            == 0
        )
        # 원본 1페이지와 거부 레코드 1건이 함께 감사용으로 남는다.
        assert await session.scalar(select(func.count()).select_from(RagSourceIngestionArtifact)) == 2
    assert json.loads(report.read_text())["canonical_checksum"] is None


async def test_rolled_back_transaction_leaves_no_run_or_snapshot(database, tmp_path):
    factory, operation_id = database
    receipt_path = receipt_file(tmp_path)

    with pytest.raises(RuntimeError, match="before commit"):
        await collect_and_store(
            factory, tmp_path, [envelope([product("P-001")])], receipt_path=receipt_path, rollback=True
        )

    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceIngestionRun)
                .where(RagSourceIngestionRun.operation_id == operation_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceSnapshot)
                .where(RagSourceSnapshot.operation_id == operation_id)
            )
            == 0
        )


async def test_stale_receipt_record_count_blocks_the_snapshot(database, tmp_path):
    """저장소의 과거 건수 Receipt는 전수 수집이 성공해도 Snapshot을 만들지 못한다.

    보고에 그치지 않고 persistence 직전에 닫히며, DB에 Run도 Snapshot도 남지 않는다.
    """
    factory, operation_id = database

    with pytest.raises(ValueError, match="Receipt record count"):
        await collect_and_store(
            factory,
            tmp_path,
            [envelope([product("P-001"), product("P-002")])],
            receipt_path=receipt_file(tmp_path, validated_record_count=1),
        )

    await assert_nothing_persisted(factory, operation_id)


async def test_missing_receipt_is_not_replaced_by_the_repository_default(database, tmp_path):
    """호출자가 지정한 Receipt가 없으면 저장소 Receipt로 조용히 넘어가지 않는다."""
    factory, operation_id = database

    with pytest.raises(ValueError, match="Endpoint receipt"):
        await collect_and_store(
            factory, tmp_path, [envelope([product("P-001")])], receipt_path=tmp_path / "absent-receipt.json"
        )

    await assert_nothing_persisted(factory, operation_id)


async def test_persisted_metadata_records_the_reject_contract_version(database, tmp_path):
    factory, _ = database
    receipt_path = receipt_file(tmp_path, validated_record_count=1)

    result, acquisition, _ = await collect_and_store(
        factory, tmp_path, [envelope([product("P-001")])], receipt_path=receipt_path
    )

    metadata = build_product_snapshot_metadata(acquisition=acquisition, run_group_key="synthetic-product")
    assert metadata.reject_code_contract_version == REJECT_CODE_CONTRACT_VERSION
    assert result.snapshot_id
