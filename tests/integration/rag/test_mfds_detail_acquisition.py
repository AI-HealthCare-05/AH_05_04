"""Synthetic HTTP acquisition with real PostgreSQL Snapshot and Candidate handoff."""

from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select, text

from ai_worker.adapters.sqlalchemy_catalog_write_support import SqlAlchemyCatalogBuildRepository
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.tasks.rag.candidate_index import CandidateIndexBuildSuccess
from ai_worker.tasks.rag.catalog.build import CatalogIngredientInput
from ai_worker.tasks.rag.catalog.mfds_component import inspect_mfds_component_rows
from ai_worker.tasks.rag.catalog.mfds_loader import load_mfds_catalog
from ai_worker.tasks.rag.source_client.endpoints import MFDS_DETAIL_IDENTITY
from ai_worker.tasks.rag.source_ingestion.mfds_detail import (
    acquire_mfds_detail,
    build_detail_ingestion_result,
    ingest_and_persist_mfds_detail,
)
from ai_worker.tests.rag.catalog.test_hash_contract_v2 import candidate
from ai_worker.tests.rag.source_ingestion.test_mfds_detail import (
    envelope,
    metadata,
    receipt_file,
    resolver,
    row,
    store,
)
from app.models.rag_source import RagSourceIngestionArtifact, RagSourceIngestionRun, RagSourceSnapshot
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
)
from tests.integration.rag.test_catalog_storage_roundtrip import (
    PRODUCT_SNAPSHOT,  # noqa: F401 -- reusable disposable migrated database fixture
    _product,
    seed_catalog_approval_receipt,
)
from tests.integration.rag.test_catalog_storage_roundtrip import database as _database

database = _database

pytestmark = pytest.mark.asyncio


async def seed(factory):
    async with factory.begin() as session:
        repo = RagSourceCatalogRepository(session)
        source = await repo.create_source(
            RagSourceCreate(source_code=MFDS_DETAIL_IDENTITY.source_code, display_name="Synthetic detail")
        )
        endpoint = await repo.create_endpoint(
            RagSourceEndpointCreate(
                source_id=source.id, endpoint_code=MFDS_DETAIL_IDENTITY.endpoint_code, display_name="Synthetic detail"
            )
        )
        operation = await repo.create_operation(
            RagSourceOperationCreate(
                endpoint_id=endpoint.id,
                operation_code=MFDS_DETAIL_IDENTITY.operation_code,
                display_name="Synthetic detail",
            )
        )
        return operation.id


async def collect_and_store(factory, tmp_path, records, *, rollback=False):
    evidence = receipt_file(tmp_path)
    async with factory.begin() as session:
        repo = SqlAlchemySourceSnapshotRepository(session)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=envelope(records)))
        ) as client:
            acquisition = await acquire_mfds_detail(
                gate=repo, client=client, secret_value="synthetic-only", spool_parent=tmp_path, resolver=resolver
            )
        try:
            ingestion, canonical = build_detail_ingestion_result(
                acquisition=acquisition, receipt_path=evidence, repository_root=tmp_path
            )
            digest = ingestion.canonical_checksum
        except ValueError:
            canonical, digest = b"", "a" * 64
        result = await ingest_and_persist_mfds_detail(
            acquisition=acquisition,
            repository=repo,
            artifact_store=store(tmp_path),
            metadata=metadata(digest),
            receipt_path=evidence,
            repository_root=tmp_path,
        )
        if rollback:
            raise RuntimeError("synthetic failure before commit")
    return result, canonical


async def test_real_snapshot_receipt_reaches_catalog_and_candidate(database, tmp_path):
    _, factory = database
    await seed(factory)
    rows = [dict(row(), ITEM_SEQ="P-001", MTRAL_CODE="I-001"), dict(row("002"), ITEM_SEQ="P-001", MTRAL_CODE="I-001")]
    result, canonical = await collect_and_store(factory, tmp_path, rows)
    assert result.snapshot_id
    async with factory() as session:
        repo = SqlAlchemySourceSnapshotRepository(session)
        detail_receipt = await repo.get_snapshot_receipt(snapshot_id=result.snapshot_id)
        product_receipt = await repo.get_snapshot_receipt(snapshot_id=UUID(PRODUCT_SNAPSHOT))
        assert detail_receipt
        detail_receipt.validate_provenance()
        assert detail_receipt.rejected_record_count == 0
        assert await session.scalar(select(func.count()).select_from(RagSourceIngestionArtifact)) == 1
    verifier = AsyncMock()

    async def approve(*, catalog_version, export_checksum, source_refs):
        return await seed_catalog_approval_receipt(
            factory,
            catalog_version=catalog_version,
            export_checksum=export_checksum,
            source_refs=source_refs,
        )

    verifier.verify.side_effect = approve
    repository = SqlAlchemyCatalogBuildRepository(factory)
    loaded = await load_mfds_catalog(
        catalog_version="synthetic-acquisition-v1",
        detail_receipt=detail_receipt,
        detail_json=canonical,
        product_receipt=product_receipt,
        products=(replace(_product("P-001"), source_snapshot_id=PRODUCT_SNAPSHOT),),
        ingredients_by_material={
            "I-001": CatalogIngredientInput(
                str(result.snapshot_id), "synthetic-material-record", "MFDS_INGREDIENT_CODE", "I-001", "합성 성분"
            )
        },
        orders_by_observation={
            r.source_record_key: i for i, r in enumerate(inspect_mfds_component_rows(tuple(rows)).observations, 1)
        },
        order_spec_version="synthetic-order-v1",
        repository=repository,
        approval_verifier=verifier,
    )
    assert loaded.build
    assert isinstance(candidate(loaded.build.export), CandidateIndexBuildSuccess)
    assert len(loaded.build.export.catalog.components) == 2


async def test_blank_rows_still_produce_a_detail_snapshot(database, tmp_path):
    """#525: 빈 주성분 행이 섞여도 Snapshot이 생성되고 제외 receipt가 남는다."""
    _, factory = database
    await seed(factory)
    rows = [dict(row(), ITEM_SEQ="P-001", MTRAL_CODE="I-001"), {"ITEM_SEQ": "P-001"}]

    result, _ = await collect_and_store(factory, tmp_path, rows)

    assert result.failure_code is None
    assert result.snapshot_id
    async with factory() as session:
        exclusions = (
            await session.execute(
                text(
                    "SELECT reason, source_row_count, excluded_row_count, retained_row_count "
                    "FROM rag_source_snapshot_exclusion WHERE source_snapshot_id = :snapshot_id"
                ),
                {"snapshot_id": str(result.snapshot_id)},
            )
        ).all()
    assert [tuple(item) for item in exclusions] == [("EMPTY_COMPONENT_FIELDS", 2, 1, 1)]


@pytest.mark.parametrize("rollback", [False, True])
async def test_failed_or_rolled_back_run_has_no_detail_snapshot(database, tmp_path, rollback):
    _, factory = database
    operation_id = await seed(factory)
    # 비어 있지 않은 행의 키 누락은 #525 이후에도 전체를 차단한다. 빈 행은 더 이상 실패
    # 사례가 아니므로 test_blank_rows_still_produce_a_detail_snapshot이 따로 덮는다.
    records = [row()] if rollback else [row(), dict(row("002"), MTRAL_SN=None)]
    if rollback:
        with pytest.raises(RuntimeError, match="before commit"):
            await collect_and_store(factory, tmp_path, records, rollback=True)
    else:
        result, _ = await collect_and_store(factory, tmp_path, records)
        assert result.failure_code
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceSnapshot)
                .where(RagSourceSnapshot.operation_id == operation_id)
            )
            == 0
        )
        assert await session.scalar(
            select(func.count())
            .select_from(RagSourceIngestionRun)
            .where(RagSourceIngestionRun.operation_id == operation_id)
        ) == (0 if rollback else 1)
        assert await session.scalar(select(func.count()).select_from(RagSourceIngestionArtifact)) == (
            0 if rollback else 1
        )
