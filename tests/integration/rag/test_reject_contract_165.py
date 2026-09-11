"""Synthetic raw MFDS run → real PostgreSQL failed/successful receipt integration."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.tasks.rag.source_client.contracts import (
    PrimaryKeyValidationResult,
    ProviderPage,
    RetryDisposition,
    SourceClientFailure,
    SourceFailureCode,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import IngestionArtifactKind, RawArtifactMetadata, StoredRawArtifact
from ai_worker.tasks.rag.source_ingestion.failure_runs import (
    FailedIngestionRunMetadata,
    IngestionProcessingFailureCode,
    record_processing_failure,
)
from ai_worker.tasks.rag.source_ingestion.persistence import (
    RejectionArtifactInput,
    ingest_and_persist_product_run,
    preserve_and_persist_product_ingestion_result,
)
from ai_worker.tasks.rag.source_ingestion.reject_codes import (
    PRODUCT_REJECT_IDENTITY,
    PRODUCT_REJECT_PARSER_VERSION,
    REJECT_CODE_CONTRACT_VERSION,
)
from ai_worker.tasks.rag.source_ingestion.result import build_product_ingestion_result
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotIngestionDecision, SnapshotIngestionMetadata
from ai_worker.tasks.rag.source_ingestion.snapshot_policy import SourceSnapshotPolicy
from app.core import config
from app.core.db.databases import Base
from app.models.rag_source import RagSourceIngestionArtifact, RagSourceIngestionRun, RagSourceSnapshot
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
)

ROOT = Path(__file__).resolve().parents[3]
RECEIPT = ROOT / "docs/validation/rag/endpoints/LIST_APPROVED_PRODUCTS.json"
NOW = datetime(2026, 9, 11, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def database():
    schema = "reject165_" + uuid4().hex
    admin = create_async_engine(config.database_url, poolclass=NullPool)
    engine = create_async_engine(
        config.database_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},test_extensions"}},
        execution_options={"schema_translate_map": {None: schema}},
    )
    async with admin.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS test_extensions"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA test_extensions"))
        await conn.execute(text("ALTER EXTENSION pg_trgm SET SCHEMA test_extensions"))
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as session:
            repo = RagSourceCatalogRepository(session)
            source = await repo.create_source(
                RagSourceCreate(
                    source_code=PRODUCT_REJECT_IDENTITY.source_code,
                    display_name="Synthetic product source",
                    max_rejected_records=100,
                    max_rejection_rate=Decimal("1"),
                )
            )
            endpoint = await repo.create_endpoint(
                RagSourceEndpointCreate(
                    source_id=source.id,
                    endpoint_code=PRODUCT_REJECT_IDENTITY.endpoint_code,
                    display_name="Synthetic endpoint",
                )
            )
            operation = await repo.create_operation(
                RagSourceOperationCreate(
                    endpoint_id=endpoint.id,
                    operation_code=PRODUCT_REJECT_IDENTITY.operation_code,
                    display_name="Synthetic operation",
                )
            )
        yield factory, operation.id
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def metadata(version="external:synthetic-v1"):
    return SnapshotIngestionMetadata(
        source_version=version,
        external_version=version.removeprefix("external:"),
        schema_version="mfds-product-response@1",
        parser_version=PRODUCT_REJECT_PARSER_VERSION,
        normalization_version="synthetic-normalization@1",
        rejected_record_count=0,
        run_group_key="synthetic-" + uuid4().hex,
        attempt_number=1,
        started_at=NOW,
        finished_at=NOW,
        collected_at=NOW,
        snapshot_policy=SourceSnapshotPolicy(100, Decimal("1")),
        reject_code_contract_version=REJECT_CODE_CONTRACT_VERSION,
    )


def raw_run(tmp_path, record_pages, *, failed_pk=False):
    total = sum(map(len, record_pages))
    pages, artifacts = [], []
    for number, records in enumerate(record_pages, 1):
        content = json.dumps(
            {
                "header": {"resultCode": "00"},
                "body": {"items": {"item": records}, "pageNo": number, "numOfRows": 100, "totalCount": total},
            },
            ensure_ascii=False,
        ).encode()
        path = tmp_path / f"page-{number}.json"
        path.write_bytes(content)
        checksum = hashlib.sha256(content).hexdigest()
        artifacts.append(
            (number, path, RawArtifactMetadata(f"page-{number}.json", checksum, len(content), "application/json"))
        )
        pages.append(ProviderPage(number, tuple(records), checksum, "application/json", total))
    validation = PrimaryKeyValidationResult(not failed_pk, total, 1 if failed_pk else 0, 0)
    run = SourceRunResult(
        PRODUCT_REJECT_IDENTITY,
        SourceRunStatus.SCHEMA_DRIFT if failed_pk else SourceRunStatus.SUCCEEDED,
        () if failed_pk else tuple(pages),
        SourceClientFailure(
            SourceFailureCode.SCHEMA_DRIFT, RetryDisposition.NOT_RETRYABLE, "Synthetic primary key failure"
        )
        if failed_pk
        else None,
        primary_key_validation=validation,
        full_scan_completed=not failed_pk,
    )
    return run, tuple(artifacts)


async def execute(factory, store, run, entries, meta):
    async with factory.begin() as session:
        return await ingest_and_persist_product_run(
            repository=SqlAlchemySourceSnapshotRepository(session),
            artifact_store=store,
            result=run,
            metadata=meta,
            raw_artifacts=entries,
            receipt_path=RECEIPT,
            repository_root=ROOT,
        )


@pytest.mark.parametrize("failed_pk", [False, True])
async def test_actual_pipeline_records_all_rejections_even_with_positive_limits(database, tmp_path, caplog, failed_pk):
    factory, _ = database
    records = [
        [{}, {"ITEM_SEQ": "SYNTHETIC_SECRET"}, {"ITEM_SEQ": False}],
        [{"ITEM_SEQ": "SYNTHETIC_SECRET"}, {"ITEM_SEQ": "  "}, {"ITEM_SEQ": "unique"}],
    ]
    run, entries = raw_run(tmp_path, records, failed_pk=failed_pk)
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    result = await execute(factory, store, run, entries, metadata())
    assert result.decision == SnapshotIngestionDecision.VALIDATION_FAILED
    assert result.failure_code == "PARSER_VALIDATION_FAILED"
    assert result.snapshot_id is None
    async with factory() as session:
        receipt = await SqlAlchemySourceSnapshotRepository(session).get_attempt_receipt(
            ingestion_run_id=result.ingestion_run_id
        )
        assert receipt.decision == SnapshotIngestionDecision.VALIDATION_FAILED
        assert receipt.reject_code_contract_version == REJECT_CODE_CONTRACT_VERSION
        rows = (
            await session.scalars(
                select(RagSourceIngestionArtifact).where(RagSourceIngestionArtifact.artifact_kind == "REJECTS")
            )
        ).all()
        assert {r.parser_location: r.reject_code for r in rows} == {
            "page[1].record[0]": "ITEM_SEQ_REQUIRED",
            "page[1].record[1]": "DUPLICATE_ITEM_SEQ",
            "page[1].record[2]": "INVALID_ITEM_SEQ_TYPE",
            "page[2].record[0]": "DUPLICATE_ITEM_SEQ",
            "page[2].record[1]": "ITEM_SEQ_REQUIRED",
        }
        assert await session.scalar(select(func.count()).select_from(RagSourceSnapshot)) == 0
        saved_run = await session.get(RagSourceIngestionRun, result.ingestion_run_id)
        assert saved_run.failure_message is None
        assert "SYNTHETIC_SECRET" not in repr(receipt)
        for row in rows:
            content = (tmp_path / "private" / row.object_key).read_bytes()
            assert hashlib.sha256(content).hexdigest() == row.raw_checksum
            assert row.byte_size == len(content)
    assert "SYNTHETIC_SECRET" not in caplog.text


async def test_clean_and_no_change_runs_preserve_version(database, tmp_path):
    factory, _ = database
    run, entries = raw_run(tmp_path, [[{"ITEM_SEQ": "SYNTH_A"}]])
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    first = await execute(factory, store, run, entries, metadata())
    again = await execute(factory, store, run, entries, metadata("external:synthetic-v2"))
    assert first.decision == SnapshotIngestionDecision.CREATED
    assert again.decision == SnapshotIngestionDecision.NO_CHANGE
    async with factory() as session:
        runs = (await session.scalars(select(RagSourceIngestionRun))).all()
        assert len(runs) == 2
        assert all(r.reject_code_contract_version == REJECT_CODE_CONTRACT_VERSION for r in runs)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RagSourceIngestionArtifact)
                .where(RagSourceIngestionArtifact.artifact_kind == "REJECTS")
            )
            == 0
        )


@pytest.mark.parametrize("change", ["version", "parser", "operation", "code"])
async def test_unknown_contract_fails_before_artifact_store(database, tmp_path, caplog, change):
    factory, _ = database
    run, entries = raw_run(tmp_path, [[{"ITEM_SEQ": "SYNTH_A"}]])
    meta = metadata()
    store = AsyncMock()
    if change == "code":
        ingestion = build_product_ingestion_result(
            result=run, artifacts=entries, receipt_path=RECEIPT, repository_root=ROOT
        )
        artifact = RejectionArtifactInput(
            entries[0][1], replace(entries[0][2], artifact_key="reject.json"), "SYNTHETIC_SECRET", "page[1].record[0]"
        )
        async with factory.begin() as session:
            result = await preserve_and_persist_product_ingestion_result(
                repository=SqlAlchemySourceSnapshotRepository(session),
                artifact_store=store,
                ingestion=ingestion,
                metadata=replace(meta, rejected_record_count=1),
                raw_artifacts=entries,
                rejection_artifacts=(artifact,),
            )
    else:
        if change == "version":
            meta = replace(meta, reject_code_contract_version="SYNTHETIC_SECRET")
        if change == "parser":
            meta = replace(meta, parser_version="SYNTHETIC_SECRET")
        if change == "operation":
            # Existing but out-of-scope Operation; failure has no falsely asserted contract version.
            async with factory.begin() as session:
                await session.execute(text("UPDATE rag_source_operation SET operation_code='OTHER'"))
            run = replace(run, operation=replace(run.operation, operation_code="OTHER"))
        result = await execute(factory, store, run, entries, meta)
    assert result.failure_code == "PARSER_VALIDATION_FAILED"
    assert result.snapshot_id is None
    store.put_verified.assert_not_called()
    async with factory() as session:
        row = await session.get(RagSourceIngestionRun, result.ingestion_run_id)
        assert "SYNTHETIC_SECRET" not in str(row.failure_message)
        assert (
            row.reject_code_contract_version is None
            if change in ("version", "operation")
            else row.reject_code_contract_version == REJECT_CODE_CONTRACT_VERSION
        )
    assert "SYNTHETIC_SECRET" not in caplog.text


async def test_incomplete_failed_artifact_set_never_generates_rejects(database, tmp_path):
    factory, _ = database
    run, entries = raw_run(tmp_path, [[{}], [{}]], failed_pk=True)
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    result = await execute(factory, store, run, entries[:1], metadata())
    assert result.failure_code == "PARSER_VALIDATION_FAILED"
    assert not tuple((tmp_path / "private").rglob("*.json"))


async def test_database_failure_rolls_back_and_retry_reuses_private_objects(database, tmp_path):
    factory, _ = database
    run, entries = raw_run(tmp_path, [[{}]], failed_pk=True)
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    async with factory() as session:
        with pytest.raises(RuntimeError, match="Synthetic DB failure"):
            async with session.begin():
                repo = SqlAlchemySourceSnapshotRepository(session)
                repo.create_artifacts = AsyncMock(side_effect=RuntimeError("Synthetic DB failure"))
                await ingest_and_persist_product_run(
                    repository=repo,
                    artifact_store=store,
                    result=run,
                    metadata=metadata(),
                    raw_artifacts=entries,
                    receipt_path=RECEIPT,
                    repository_root=ROOT,
                )
    objects = {p.relative_to(tmp_path / "private") for p in (tmp_path / "private").rglob("*") if p.is_file()}
    assert objects
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(RagSourceIngestionRun)) == 0
    outcome = await execute(factory, store, run, entries, metadata())
    assert outcome.failure_code == "PARSER_VALIDATION_FAILED"
    assert objects == {p.relative_to(tmp_path / "private") for p in (tmp_path / "private").rglob("*") if p.is_file()}


async def test_repository_blocks_rejects_on_legacy_run(database):
    factory, operation = database
    async with factory.begin() as session:
        legacy = RagSourceIngestionRun(
            operation_id=operation,
            run_group_key="legacy",
            run_status="FAILED",
            started_at=NOW,
            finished_at=NOW,
            failure_code="PARSER_VALIDATION_FAILED",
        )
        session.add(legacy)
        await session.flush()
        artifact = StoredRawArtifact(
            None,
            RawArtifactMetadata("reject.json", "a" * 64, 10, "application/json"),
            "LOCAL_PRIVATE",
            "synthetic-object",
            IngestionArtifactKind.REJECTS,
            "ITEM_SEQ_REQUIRED",
            "page[1].record[0]",
        )
        with pytest.raises(ValueError, match="reject contract"):
            await SqlAlchemySourceSnapshotRepository(session).create_artifacts(
                ingestion_run_id=legacy.id, artifacts=(artifact,)
            )
        assert legacy.reject_code_contract_version is None


@pytest.mark.parametrize("value", [None, "", " \t", 0, True, 1.25, [], {"nested": 1.25}])
async def test_each_invalid_value_gets_one_artifact(database, tmp_path, value):
    factory, _ = database
    run, entries = raw_run(tmp_path, [[{"ITEM_SEQ": value}]], failed_pk=True)
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    result = await execute(factory, store, run, entries, metadata())
    assert result.failure_code == "PARSER_VALIDATION_FAILED"
    async with factory() as session:
        rows = (
            await session.scalars(
                select(RagSourceIngestionArtifact).where(RagSourceIngestionArtifact.artifact_kind == "REJECTS")
            )
        ).all()
        assert len(rows) == 1
        expected = (
            "ITEM_SEQ_REQUIRED"
            if value is None or (isinstance(value, str) and not value.strip())
            else "INVALID_ITEM_SEQ_TYPE"
        )
        assert rows[0].reject_code == expected
        assert json.loads((tmp_path / "private" / rows[0].object_key).read_bytes()) == {"ITEM_SEQ": value}


async def test_store_failure_rolls_back_database_and_keeps_retryable_objects(database, tmp_path):
    factory, _ = database
    run, entries = raw_run(tmp_path, [[{}]], failed_pk=True)
    backing = LocalPrivateSourceArtifactStore(tmp_path / "private")

    class FailingStore:
        def put_verified(self, **kwargs):
            if kwargs.get("artifact_kind") is IngestionArtifactKind.REJECTS:
                raise OSError("SYNTHETIC_SECRET storage failure")
            return backing.put_verified(**kwargs)

    with pytest.raises(RuntimeError, match="Artifact preservation failed") as error:
        await execute(factory, FailingStore(), run, entries, metadata())
    assert "SYNTHETIC_SECRET" not in str(error.value)
    assert error.value.__suppress_context__
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(RagSourceIngestionRun)) == 0
    retried = await execute(factory, backing, run, entries, metadata())
    assert retried.failure_code == "PARSER_VALIDATION_FAILED"


async def test_parser_contract_upgrade_is_not_legacy_no_change(database, tmp_path):
    factory, _ = database
    run, entries = raw_run(tmp_path, [[{"ITEM_SEQ": "SYNTH_A"}]])
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    ingestion = build_product_ingestion_result(
        result=run, artifacts=entries, receipt_path=RECEIPT, repository_root=ROOT
    )
    async with factory.begin() as session:
        legacy = await preserve_and_persist_product_ingestion_result(
            repository=SqlAlchemySourceSnapshotRepository(session),
            artifact_store=store,
            ingestion=ingestion,
            metadata=replace(metadata(), parser_version="mfds-product-parser@1", reject_code_contract_version=None),
            raw_artifacts=entries,
        )
    conflict = await execute(factory, store, run, entries, metadata())
    assert conflict.decision == SnapshotIngestionDecision.SOURCE_VERSION_CONFLICT
    new = await execute(factory, store, run, entries, metadata("external:synthetic-v2"))
    assert new.decision == SnapshotIngestionDecision.CREATED
    assert new.snapshot_id != legacy.snapshot_id
    async with factory() as session:
        previous = await session.get(RagSourceIngestionRun, legacy.ingestion_run_id)
        assert previous.reject_code_contract_version is None


@pytest.mark.parametrize("location", ["page[1].record[0]", "page[01].record[00]"])
async def test_duplicate_rejection_location_cannot_be_appended_again(database, tmp_path, location):
    factory, _ = database
    run, entries = raw_run(tmp_path, [[{}]], failed_pk=True)
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    result = await execute(factory, store, run, entries, metadata())
    async with factory.begin() as session:
        saved = (
            await session.scalars(
                select(RagSourceIngestionArtifact).where(RagSourceIngestionArtifact.artifact_kind == "REJECTS")
            )
        ).one()
        artifact = StoredRawArtifact(
            None,
            RawArtifactMetadata("different-key.json", saved.raw_checksum, saved.byte_size, saved.content_type),
            saved.storage_backend,
            saved.object_key,
            IngestionArtifactKind.REJECTS,
            saved.reject_code,
            location,
        )
        with pytest.raises(ValueError, match="failed parser run"):
            await SqlAlchemySourceSnapshotRepository(session).create_artifacts(
                ingestion_run_id=result.ingestion_run_id, artifacts=(artifact,)
            )


async def test_invalid_run_does_not_replace_existing_current_snapshot(database, tmp_path):
    from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import select_current_snapshot

    factory, _ = database
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    clean, entries = raw_run(tmp_path, [[{"ITEM_SEQ": "SYNTH_A"}]])
    first = await execute(factory, store, clean, entries, metadata())
    async with factory.begin() as session:
        await select_current_snapshot(
            repository=SqlAlchemySourceSnapshotRepository(session),
            snapshot_id=first.snapshot_id,
            selected_at=NOW,
            selected_by="synthetic-reviewer",
        )
    bad, bad_entries = raw_run(tmp_path, [[{}]], failed_pk=True)
    failed = await execute(factory, store, bad, bad_entries, metadata("external:bad"))
    assert failed.snapshot_id is None
    async with factory() as session:
        rows = (await session.scalars(select(RagSourceSnapshot))).all()
        assert len(rows) == 1
        assert rows[0].id == first.snapshot_id
        assert rows[0].verification_status == "CURRENT"


async def test_failed_client_result_is_not_mutated_or_promoted(database, tmp_path):
    factory, _ = database
    failed, entries = raw_run(tmp_path, [[{"ITEM_SEQ": "SYNTH_VALID"}]], failed_pk=True)
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    result = await execute(factory, store, failed, entries, metadata())
    assert result.failure_code == "PARSER_VALIDATION_FAILED"
    assert result.snapshot_id is None
    assert failed.pages == ()
    assert not failed.snapshot_candidate_allowed
    assert failed.status == SourceRunStatus.SCHEMA_DRIFT
    assert not any(p.is_file() for p in (tmp_path / "private").rglob("*"))


@pytest.mark.parametrize("code", [SourceFailureCode.TIMEOUT, SourceFailureCode.EMPTY_RESULT])
async def test_transport_failure_keeps_existing_failure_type_and_code(database, tmp_path, code):
    from ai_worker.tasks.rag.source_ingestion.failure_runs import FailedIngestionRunResult

    factory, _ = database
    failed = SourceRunResult(
        PRODUCT_REJECT_IDENTITY,
        SourceRunStatus.FAILED,
        (),
        SourceClientFailure(code, RetryDisposition.BACKOFF, "Synthetic collection failure"),
    )
    store = AsyncMock()
    outcome = await execute(factory, store, failed, (), metadata())
    assert isinstance(outcome, FailedIngestionRunResult)
    assert outcome.failure_code == code.value
    store.put_verified.assert_not_called()
    async with factory() as session:
        row = await session.get(RagSourceIngestionRun, outcome.ingestion_run_id)
        assert row.reject_code_contract_version == REJECT_CODE_CONTRACT_VERSION
        assert row.snapshot_id is None
        receipt = await SqlAlchemySourceSnapshotRepository(session).get_attempt_receipt(
            ingestion_run_id=outcome.ingestion_run_id
        )
        assert receipt is not None
        assert receipt.decision is SnapshotIngestionDecision.COLLECTION_FAILED
        assert receipt.failure_code == code.value
        assert receipt.reject_code_contract_version == REJECT_CODE_CONTRACT_VERSION
        assert receipt.validation_reason_code == (
            "COLLECTION_EMPTY_RESULT" if code is SourceFailureCode.EMPTY_RESULT else None
        )


@pytest.mark.parametrize("failure_code", list(IngestionProcessingFailureCode))
async def test_each_processing_failure_code_roundtrips_through_database(database, failure_code):
    """두 처리 실패 코드가 각각 저장되고 같은 값으로 조회되어야 한다."""
    factory, _ = database
    artifacts = (
        StoredRawArtifact(
            1,
            RawArtifactMetadata("page-0001.json", "b" * 64, 20, "application/json"),
            "LOCAL_PRIVATE",
            f"synthetic-raw-{failure_code.value}",
        ),
        StoredRawArtifact(
            None,
            RawArtifactMetadata("reject.json", "a" * 64, 10, "application/json"),
            "LOCAL_PRIVATE",
            f"synthetic-reject-{failure_code.value}",
            IngestionArtifactKind.REJECTS,
            "ITEM_SEQ_REQUIRED",
            "page[1].record[0]",
        ),
    )

    async with factory.begin() as session:
        recorded = await record_processing_failure(
            repository=SqlAlchemySourceSnapshotRepository(session),
            identity=PRODUCT_REJECT_IDENTITY,
            metadata=FailedIngestionRunMetadata(
                run_group_key="synthetic-" + uuid4().hex,
                attempt_number=1,
                started_at=NOW,
                finished_at=NOW,
                duration_ms=1000,
                reject_code_contract_version=REJECT_CODE_CONTRACT_VERSION,
            ),
            failure_code=failure_code,
            artifacts=artifacts,
        )

    assert recorded.failure_code == failure_code.value
    async with factory() as session:
        row = await session.get(RagSourceIngestionRun, recorded.ingestion_run_id)
        assert row.failure_code == failure_code.value
        assert row.snapshot_id is None
        receipt = await SqlAlchemySourceSnapshotRepository(session).get_attempt_receipt(
            ingestion_run_id=recorded.ingestion_run_id
        )
        assert receipt is not None
        assert receipt.decision is SnapshotIngestionDecision.VALIDATION_FAILED
        assert receipt.failure_code == failure_code.value
        assert receipt.reject_code_contract_version == REJECT_CODE_CONTRACT_VERSION


async def test_successful_source_run_reaches_catalog_and_candidate(database, tmp_path):
    """Real #444 ingestion/Receipt and DB handoff; approval verifier remains a synthetic double."""
    from ai_worker.adapters.sqlalchemy_catalog_write_support import SqlAlchemyCatalogBuildRepository
    from ai_worker.tasks.rag.candidate_index import CandidateIndexBuildSuccess
    from ai_worker.tasks.rag.catalog import (
        CandidateCatalogSourceRef,
        CandidateRecordStatus,
        CatalogApprovalReceipt,
        CatalogFreshnessStatus,
        CatalogProductInput,
        CatalogSourceApproval,
        CatalogVerificationStatus,
        build_catalog_members,
        create_catalog_export,
    )
    from ai_worker.tasks.rag.catalog.approval import CatalogApprovalVerifier
    from ai_worker.tests.rag.catalog.test_hash_contract_v2 import candidate
    from app.models.rag_catalog import RagCatalogSet

    factory, _ = database
    record = {"ITEM_SEQ": "SYNTH_A", "ITEM_NAME": "합성 수집 제품"}
    run, entries = raw_run(tmp_path, [[record]])
    store = LocalPrivateSourceArtifactStore(tmp_path / "private")
    ingested = await execute(factory, store, run, entries, metadata())
    assert ingested.decision == SnapshotIngestionDecision.CREATED
    async with factory() as session:
        receipt = await SqlAlchemySourceSnapshotRepository(session).get_snapshot_receipt(
            snapshot_id=ingested.snapshot_id
        )
        receipt.validate_provenance()
    ref = CandidateCatalogSourceRef(str(receipt.source_snapshot_id), receipt.source_version)
    members = build_catalog_members(
        products=(
            CatalogProductInput(
                source_snapshot_id=ref.snapshot_id,
                source_record_key=record["ITEM_SEQ"],
                code_system="MFDS_ITEM_SEQ",
                canonical_code=record["ITEM_SEQ"],
                product_name=record["ITEM_NAME"],
                product_status=CandidateRecordStatus.ACTIVE,
            ),
        ),
        ingredients=(),
        components=(),
        aliases=(),
    )
    initial = create_catalog_export(catalog_version="synthetic-source-handoff", source_refs=(ref,), members=members)
    approval = CatalogApprovalReceipt(
        "synthetic-only-approval",
        "synthetic-source-handoff",
        initial.export_checksum,
        CatalogVerificationStatus.APPROVED,
        True,
        (
            CatalogSourceApproval(
                ref, "synthetic-source-approval", CatalogVerificationStatus.APPROVED, CatalogFreshnessStatus.CURRENT
            ),
        ),
    )
    artifacts = create_catalog_export(
        catalog_version="synthetic-source-handoff", source_refs=(ref,), members=members, approval_receipt=approval
    )
    verifier = AsyncMock(spec=CatalogApprovalVerifier)
    verifier.verify.return_value = approval
    repository = SqlAlchemyCatalogBuildRepository(factory)
    await repository.save_build(members=members, artifacts=artifacts)
    async with factory() as session:
        set_id = await session.scalar(select(RagCatalogSet.id))
    restored = await repository.load_build(set_id, approval_verifier=verifier)
    assert restored == artifacts
    outcome = candidate(restored)
    assert isinstance(outcome, CandidateIndexBuildSuccess)
    assert outcome.manifest.product_identity_count == 1
    assert outcome.manifest.approved_alias_count == 0
    assert restored.catalog.source_refs == (ref,)
    assert verifier.verify.await_count == 1
