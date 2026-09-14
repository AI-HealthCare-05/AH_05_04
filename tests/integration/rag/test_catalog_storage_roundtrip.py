"""Real commits and read-only Candidate v2 handoff on disposable PostgreSQL databases."""

import asyncio
import hashlib
import json
import unicodedata
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_catalog_write_support import (
    CatalogDatabaseBindingError,
    SqlAlchemyCatalogBuildRepository,
    SqlAlchemyCatalogWriteSupport,
)
from ai_worker.tasks.rag.candidate_index import CandidateIndexBuildSuccess
from ai_worker.tasks.rag.catalog import (
    CandidateCatalogSourceRef,
    CatalogApprovalReceipt,
    CatalogComponentInput,
    CatalogComponentRole,
    CatalogFreshnessStatus,
    CatalogIngredientInput,
    CatalogSourceApproval,
    CatalogVerificationStatus,
    build_catalog_members,
    create_catalog_export,
)
from ai_worker.tasks.rag.catalog.approval import CatalogApprovalVerifier
from ai_worker.tasks.rag.catalog.restore import CatalogStorageRestoreError
from ai_worker.tests.rag.catalog.test_export import _alias, _product
from ai_worker.tests.rag.catalog.test_hash_contract_v2 import candidate
from app.core import config
from app.models.rag_catalog import RagCatalogSet
from app.models.rag_source import RagSource, RagSourceEndpoint, RagSourceOperation, RagSourceSnapshot

ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / "tests/fixtures/rag/catalog/db-receipt-v2"
PRODUCT_SNAPSHOT = "00000000-0000-4000-8000-000000000001"
ALIAS_SNAPSHOT = "00000000-0000-4000-8000-000000000002"


def approved_build(*, changed=False, repeated=False):
    refs = (
        CandidateCatalogSourceRef(PRODUCT_SNAPSHOT, "external:v1"),
        CandidateCatalogSourceRef(ALIAS_SNAPSHOT, "external:v2"),
    )
    products = (
        replace(
            _product("P-001"), source_snapshot_id=PRODUCT_SNAPSHOT, product_name="변경 제품" if changed else "합성 제품"
        ),
        replace(
            _product("P-002"),
            source_snapshot_id=PRODUCT_SNAPSHOT,
            product_name=unicodedata.normalize("NFD", "다른 제품"),
        ),
    )
    aliases = (
        replace(
            _alias("P-001", "alias-cross", "합성 별칭"),
            source_snapshot_id=ALIAS_SNAPSHOT,
            target_source_snapshot_id=PRODUCT_SNAPSHOT,
        ),
    )
    ingredients = (
        CatalogIngredientInput(
            source_snapshot_id=PRODUCT_SNAPSHOT,
            source_record_key="INGREDIENT:I001",
            code_system="MFDS_INGREDIENT_CODE",
            canonical_code="I001",
            ingredient_name="합성 성분",
        ),
    )
    components = (
        CatalogComponentInput(
            source_snapshot_id=PRODUCT_SNAPSHOT,
            product_code_system="MFDS_ITEM_SEQ",
            product_canonical_code="P-001",
            ingredient_code_system="MFDS_INGREDIENT_CODE",
            ingredient_canonical_code="I001",
            component_role=CatalogComponentRole.ACTIVE_INGREDIENT,
            component_order=1,
            strength_value="010.00",
            strength_unit="mg",
        ),
    )
    if repeated:
        first = replace(components[0], source_record_key="synthetic:1:1")
        components = (
            first,
            replace(
                first,
                source_record_key="synthetic:1:2",
                component_order=2,
                strength_value="020.00",
                release_profile="SYNTHETIC_EXTENDED",
            ),
        )
    members = build_catalog_members(products=products, ingredients=ingredients, components=components, aliases=aliases)
    initial = create_catalog_export(catalog_version="synthetic-db-v2", source_refs=refs, members=members)
    receipt = CatalogApprovalReceipt(
        "synthetic-db-approval",
        "synthetic-db-v2",
        initial.export_checksum,
        CatalogVerificationStatus.APPROVED,
        True,
        tuple(
            CatalogSourceApproval(
                ref, f"synthetic-source-{i}", CatalogVerificationStatus.APPROVED, CatalogFreshnessStatus.CURRENT
            )
            for i, ref in enumerate(refs)
        ),
    )
    artifacts = create_catalog_export(
        catalog_version="synthetic-db-v2", source_refs=refs, members=members, approval_receipt=receipt
    )
    verifier = AsyncMock(spec=CatalogApprovalVerifier)
    verifier.verify.return_value = receipt
    return members, artifacts, verifier


@pytest_asyncio.fixture
async def database(monkeypatch):
    name = "catalog166_roundtrip_" + uuid4().hex
    original = config.database_url
    cluster = create_async_engine(original, isolation_level="AUTOCOMMIT", hide_parameters=True)
    engine = create_async_engine(make_url(original).set(database=name), hide_parameters=True)
    try:
        async with cluster.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        monkeypatch.setattr(config, "DB_NAME", name)
        await asyncio.to_thread(command.upgrade, Config(str(ROOT / "backend/alembic.ini")), "head")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory.begin() as session:
            source = RagSource(source_code="SYNTHETIC", display_name="합성 출처")
            session.add(source)
            await session.flush()
            endpoint = RagSourceEndpoint(source_id=source.id, endpoint_code="SYNTHETIC", display_name="합성 API")
            session.add(endpoint)
            await session.flush()
            operation = RagSourceOperation(
                endpoint_id=endpoint.id, operation_code="SYNTHETIC", display_name="합성 수집"
            )
            session.add(operation)
            await session.flush()
            for snapshot_id, version in ((PRODUCT_SNAPSHOT, "v1"), (ALIAS_SNAPSHOT, "v2")):
                session.add(
                    RagSourceSnapshot(
                        id=UUID(snapshot_id),
                        operation_id=operation.id,
                        source_version="external:" + version,
                        external_version=version,
                        endpoint_receipt_hash="c" * 64,
                        raw_manifest_checksum="a" * 64,
                        canonical_checksum="b" * 64,
                        schema_version="synthetic-v1",
                        parser_version="synthetic-v1",
                        normalization_version="synthetic-v1",
                        canonicalization_spec_version="synthetic-v1",
                        record_count=2,
                        rejected_record_count=0,
                        collected_at=datetime.now(UTC),
                        verification_status="PENDING",
                    )
                )
        yield engine, factory
    finally:
        await engine.dispose()
        async with cluster.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await cluster.dispose()


async def saved(factory):
    members, artifacts, verifier = approved_build()
    repository = SqlAlchemyCatalogBuildRepository(factory)
    await repository.save_build(members=members, artifacts=artifacts)
    async with factory() as session:
        set_id = await session.scalar(select(RagCatalogSet.id))
    return repository, set_id, artifacts, verifier


async def test_committed_database_bytes_reach_public_candidate_without_writes(database):
    engine, factory = database
    repository, set_id, artifacts, verifier = await saved(factory)
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        restored = await repository.load_build(set_id, approval_verifier=verifier)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)
    assert restored == artifacts
    expected = json.loads((GOLDEN / "expected.json").read_bytes())
    assert restored.catalog_jsonl == (GOLDEN / "catalog.jsonl").read_bytes()
    assert restored.manifest_json == (GOLDEN / "manifest.json").read_bytes()
    assert hashlib.sha256(restored.catalog_jsonl).hexdigest() == expected["export_checksum"]
    assert restored.catalog.catalog_manifest_hash == expected["catalog_manifest_hash"]
    assert hashlib.sha256(restored.manifest_json).hexdigest() == expected["manifest_file_checksum"]
    assert restored.catalog.components[0].strength_value == "010.00"
    assert all(statement.lstrip().upper().startswith(("SELECT", "SET TRANSACTION")) for statement in statements)
    result = candidate(restored)
    assert isinstance(result, CandidateIndexBuildSuccess)
    assert result.manifest.catalog_manifest_hash == expected["catalog_manifest_hash"]
    assert result.manifest.approved_alias_count == 1
    assert result.manifest.product_identity_count == 2
    assert verifier.verify.await_count == 1


@pytest.mark.parametrize(
    "damage",
    [
        "jsonl",
        "manifest",
        "hash-kind",
        "missing-member",
        "product",
        "alias",
        "missing-source",
        "identity",
        "release-profile",
    ],
)
async def test_readback_rejects_corruption_without_repairing_rows(database, damage):
    engine, factory = database
    repository, set_id, _, verifier = await saved(factory)
    queries = {
        "release-profile": "UPDATE rag_medication_product_component SET release_profile='tampered'",
        "jsonl": "UPDATE rag_catalog_set_hash SET canonical_bytes = decode('00', 'hex') WHERE hash_kind='EXPORT_CHECKSUM'",
        "manifest": "UPDATE rag_catalog_set SET manifest_json = decode('00', 'hex')",
        "hash-kind": "DELETE FROM rag_catalog_set_hash WHERE hash_kind='CATALOG_ENVELOPE'",
        "missing-member": "DELETE FROM rag_catalog_set_member WHERE member_kind='COMPONENT'",
        "product": "UPDATE rag_medication_product SET product_name='tampered'",
        "alias": "UPDATE rag_medication_alias SET review_status='REJECTED'",
        "missing-source": "DELETE FROM rag_catalog_set_member WHERE source_snapshot_id='" + ALIAS_SNAPSHOT + "'",
        "identity": "UPDATE rag_entity_identity SET canonical_code='tampered' WHERE entity_type='INGREDIENT'",
    }
    async with engine.begin() as connection:
        await connection.execute(text(queries[damage]))
        if damage == "missing-source":
            await connection.execute(
                text("DELETE FROM rag_catalog_set_source WHERE source_snapshot_id='" + ALIAS_SNAPSHOT + "'")
            )
    with pytest.raises(CatalogDatabaseBindingError):
        await repository.load_build(set_id, approval_verifier=verifier)
    verifier.verify.assert_not_awaited()


async def test_current_approval_required_on_every_read(database):
    _, factory = database
    repository, set_id, _, verifier = await saved(factory)
    with pytest.raises(CatalogStorageRestoreError):
        await repository.load_build(set_id, approval_verifier=None)
    await repository.load_build(set_id, approval_verifier=verifier)
    verifier.verify.return_value = None
    with pytest.raises(CatalogStorageRestoreError):
        await repository.load_build(set_id, approval_verifier=verifier)
    assert verifier.verify.await_count == 2


async def test_failed_commit_rolls_back_identities_and_retries_one_complete_set(database):
    _, factory = database
    members, artifacts, verifier = approved_build()
    repository = SqlAlchemyCatalogBuildRepository(factory)

    def fail_commit(session):
        if not session.in_nested_transaction():
            raise RuntimeError("synthetic commit failure")

    event.listen(AsyncSession.sync_session_class, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="synthetic commit failure"):
            await repository.save_build(members=members, artifacts=artifacts)
    finally:
        event.remove(AsyncSession.sync_session_class, "before_commit", fail_commit)
    async with factory() as session:
        for table in ("rag_entity_identity", "rag_medication_product", "rag_catalog_set", "rag_catalog_set_hash"):
            assert await session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    await repository.save_build(members=members, artifacts=artifacts)
    await repository.save_build(members=members, artifacts=artifacts)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(RagCatalogSet)) == 1
        set_id = await session.scalar(select(RagCatalogSet.id))
    assert await repository.load_build(set_id, approval_verifier=verifier) == artifacts
    changed_members, changed_artifacts, _ = approved_build(changed=True)
    with pytest.raises(CatalogDatabaseBindingError):
        await repository.save_build(members=changed_members, artifacts=changed_artifacts)
    assert await repository.load_build(set_id, approval_verifier=verifier) == artifacts


async def test_committed_but_unconfirmed_save_is_replayed_without_duplicate(database, monkeypatch):
    _, factory = database
    members, artifacts, verifier = approved_build()
    repository = SqlAlchemyCatalogBuildRepository(factory)
    original = SqlAlchemyCatalogWriteSupport.verify_set
    calls = 0

    async def fail_after_commit(self, *args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise CatalogDatabaseBindingError()
        await original(self, *args)

    with monkeypatch.context() as patch:
        patch.setattr(SqlAlchemyCatalogWriteSupport, "verify_set", fail_after_commit)
        with pytest.raises(CatalogDatabaseBindingError):
            await repository.save_build(members=members, artifacts=artifacts)
    await repository.save_build(members=members, artifacts=artifacts)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(RagCatalogSet)) == 1
        set_id = await session.scalar(select(RagCatalogSet.id))
    assert await repository.load_build(set_id, approval_verifier=verifier) == artifacts


async def test_concurrent_identical_builds_reuse_one_set(database):
    _, factory = database
    members, artifacts, verifier = approved_build()
    repository = SqlAlchemyCatalogBuildRepository(factory)
    await asyncio.gather(*(repository.save_build(members=members, artifacts=artifacts) for _ in range(2)))
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(RagCatalogSet)) == 1
        set_id = await session.scalar(select(RagCatalogSet.id))
        for table, count in (("rag_entity_identity", 3), ("rag_medication_product", 2), ("rag_catalog_set_hash", 2)):
            assert await session.scalar(text(f"SELECT count(*) FROM {table}")) == count
    assert await repository.load_build(set_id, approval_verifier=verifier) == artifacts


@pytest.mark.parametrize("phase", ["save", "load"])
@pytest.mark.parametrize("damage", ["external-version", "endpoint-receipt", "canonicalization"])
async def test_source_receipt_provenance_is_required_at_storage_and_readback(database, phase, damage):
    engine, factory = database
    members, artifacts, verifier = approved_build()
    repository = SqlAlchemyCatalogBuildRepository(factory)
    set_id = None
    if phase == "load":
        await repository.save_build(members=members, artifacts=artifacts)
        async with factory() as session:
            set_id = await session.scalar(select(RagCatalogSet.id))
    updates = {
        "external-version": "external_version='SYNTHETIC_PRIVATE_SENTINEL'",
        "endpoint-receipt": "endpoint_receipt_hash=NULL",
        "canonicalization": "canonicalization_spec_version=''",
    }
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE rag_source_snapshot SET " + updates[damage] + " WHERE id=:id"),
            {"id": PRODUCT_SNAPSHOT},
        )
    with pytest.raises(CatalogDatabaseBindingError) as error:
        if phase == "save":
            await repository.save_build(members=members, artifacts=artifacts)
        else:
            await repository.load_build(set_id, approval_verifier=verifier)
    assert str(error.value) == "Catalog database binding failed"
    verifier.verify.assert_not_awaited()
    async with factory() as session:
        for table in ("rag_entity_identity", "rag_catalog_set"):
            expected = (3 if table == "rag_entity_identity" else 1) if phase == "load" else 0
            assert await session.scalar(text(f"SELECT count(*) FROM {table}")) == expected


async def test_post_commit_confirmation_checks_member_rows_before_reporting_success(database, monkeypatch):
    engine, factory = database
    members, artifacts, _ = approved_build()
    repository = SqlAlchemyCatalogBuildRepository(factory)
    original = repository._read_plan

    async def corrupt_committed_product(set_id):
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE rag_medication_product SET product_name='tampered'"))
        return await original(set_id)

    monkeypatch.setattr(repository, "_read_plan", corrupt_committed_product)
    with pytest.raises(CatalogDatabaseBindingError):
        await repository.save_build(members=members, artifacts=artifacts)
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(RagCatalogSet)) == 1


async def test_catalog_writer_login_saves_and_reuses_without_payload_update(database):
    from sqlalchemy.exc import DBAPIError

    from ai_worker.admin.catalog_writer import catalog_writer_repository
    from infra.python.provision_database_roles import provision_roles

    engine, factory = database
    suffix = uuid4().hex[:12]
    runtime, source_writer, writer = (f"catalog372_{part}_{suffix}" for part in ("runtime", "source", "writer"))
    password = "synthetic-catalog372-only"
    environment = {
        "CATALOG_WRITER_HOST": engine.url.host,
        "CATALOG_WRITER_PORT": str(engine.url.port),
        "CATALOG_WRITER_NAME": engine.url.database,
        "CATALOG_WRITER_USER": writer,
        "CATALOG_WRITER_PASSWORD": password,
    }
    producer = create_async_engine(engine.url.set(username=writer, password=password))
    reader = create_async_engine(engine.url.set(username=runtime, password=password))
    try:
        async with engine.begin() as connection:
            for role in (runtime, source_writer, writer):
                await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
            # Redeployment must preserve the final Catalog cutover, not restore legacy Runtime INSERT.
            for _ in range(2):
                await provision_roles(
                    connection, owner=config.DB_USER, runtime=runtime, writer=source_writer, catalog_writer=writer
                )
            await connection.execute(text("CREATE TABLE future_catalog_fixture (id integer)"))
        members, artifacts, verifier = approved_build()
        async with catalog_writer_repository(environment) as repository:
            await repository.save_build(members=members, artifacts=artifacts)
            await repository.save_build(members=members, artifacts=artifacts)
            async with factory() as session:
                assert await session.scalar(select(func.count()).select_from(RagCatalogSet)) == 1
                set_id = await session.scalar(select(RagCatalogSet.id))
            assert await repository.load_build(set_id, approval_verifier=verifier) == artifacts
        runtime_repository = SqlAlchemyCatalogBuildRepository(async_sessionmaker(reader, expire_on_commit=False))
        assert await runtime_repository.load_build(set_id, approval_verifier=verifier) == artifacts
        denied = [
            (producer, "UPDATE rag_medication_product SET product_name='changed'"),
            (producer, "UPDATE rag_medication_alias SET review_status='REJECTED'"),
            (producer, "UPDATE rag_source_snapshot SET verification_status='VERIFIED'"),
            (producer, "DELETE FROM rag_catalog_set"),
            (producer, "TRUNCATE rag_catalog_set_hash"),
            (producer, "INSERT INTO rag_source_snapshot_verification DEFAULT VALUES"),
            (producer, "INSERT INTO future_catalog_fixture VALUES (1)"),
            (reader, "INSERT INTO rag_medication_product DEFAULT VALUES"),
            (reader, "INSERT INTO rag_catalog_set DEFAULT VALUES"),
            (reader, f'SET ROLE "{writer}"'),
        ]
        for login, statement in denied:
            with pytest.raises(DBAPIError) as error:
                async with login.begin() as connection:
                    await connection.execute(text(statement))
            assert error.value.orig.sqlstate == "42501"
        with pytest.raises(DBAPIError) as error:
            async with producer.begin() as connection:
                await connection.execute(text("UPDATE rag_medication_product SET catalog_lock_marker=1"))
        assert error.value.orig.sqlstate == "23514"
        async with engine.begin() as connection:
            await connection.execute(text(f'GRANT UPDATE (product_name) ON rag_medication_product TO "{writer}"'))
        with pytest.raises(ValueError, match="column privileges"):
            async with catalog_writer_repository(environment):
                pytest.fail("An overprivileged Writer must not be exposed to the caller")
    finally:
        await producer.dispose()
        await reader.dispose()
        async with engine.begin() as connection:
            for role in (runtime, source_writer, writer):
                if await connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
                    await connection.execute(text(f'DROP OWNED BY "{role}"'))
                    await connection.execute(text(f'DROP ROLE "{role}"'))


async def test_repeated_component_roundtrip_preserves_occurrences_and_release_profile(database):
    _, factory = database
    members, artifacts, verifier = approved_build(repeated=True)
    repository = SqlAlchemyCatalogBuildRepository(factory)
    await repository.save_build(members=members, artifacts=artifacts)
    await repository.save_build(members=members, artifacts=artifacts)
    async with factory() as session:
        set_id = await session.scalar(select(RagCatalogSet.id))
        rows = (
            await session.execute(
                text(
                    "SELECT display_order, release_profile FROM rag_medication_product_component ORDER BY display_order"
                )
            )
        ).all()
    assert rows == [(1, None), (2, "SYNTHETIC_EXTENDED")]
    restored = await repository.load_build(set_id, approval_verifier=verifier)
    assert restored == artifacts
    assert len({c.component_ref for c in restored.catalog.components}) == 2
    assert {c.strength_value for c in restored.catalog.components} == {"010.00", "020.00"}
    assert isinstance(candidate(restored), CandidateIndexBuildSuccess)


async def test_mfds_loader_preserves_groups_sources_and_candidate_handoff(database):
    from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
    from ai_worker.tasks.rag.catalog.mfds_component import inspect_mfds_component_rows
    from ai_worker.tasks.rag.catalog.mfds_loader import DETAIL_CANONICALIZATION_SPEC, load_mfds_catalog

    _, factory = database
    rows = [
        {
            "ITEM_SEQ": "P-001",
            "TAMT_SEQ": "01",
            "MTRAL_SN": "001",
            "MTRAL_CODE": "I-001",
            "QNT": "010.00",
            "INGD_UNIT_CD": "mg",
        },
        {
            "ITEM_SEQ": "P-001",
            "TAMT_SEQ": "02",
            "MTRAL_SN": "001",
            "MTRAL_CODE": "I-001",
            "QNT": "020.00",
            "INGD_UNIT_CD": "mg",
        },
    ]
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()
    async with factory.begin() as session:
        await session.execute(
            text(
                "UPDATE rag_source_snapshot SET canonical_checksum=:digest, canonicalization_spec_version=:spec WHERE id=:id"
            ),
            {"digest": hashlib.sha256(encoded).hexdigest(), "spec": DETAIL_CANONICALIZATION_SPEC, "id": ALIAS_SNAPSHOT},
        )
    async with factory() as session:
        source_repo = SqlAlchemySourceSnapshotRepository(session)
        product_receipt = await source_repo.get_snapshot_receipt(snapshot_id=UUID(PRODUCT_SNAPSHOT))
        detail_receipt = await source_repo.get_snapshot_receipt(snapshot_id=UUID(ALIAS_SNAPSHOT))
    inspection = inspect_mfds_component_rows(tuple(rows))
    verifier = AsyncMock(spec=CatalogApprovalVerifier)

    def approve(*, catalog_version, export_checksum, source_refs):
        return CatalogApprovalReceipt(
            "synthetic-mfds-approval",
            catalog_version,
            export_checksum,
            CatalogVerificationStatus.APPROVED,
            True,
            tuple(
                CatalogSourceApproval(
                    ref, "synthetic-source", CatalogVerificationStatus.APPROVED, CatalogFreshnessStatus.CURRENT
                )
                for ref in source_refs
            ),
        )

    verifier.verify.side_effect = approve
    repository = SqlAlchemyCatalogBuildRepository(factory)
    kwargs = dict(
        catalog_version="synthetic-mfds-observation-v1",
        detail_receipt=detail_receipt,
        detail_json=encoded,
        product_receipt=product_receipt,
        products=(replace(_product("P-001"), source_snapshot_id=PRODUCT_SNAPSHOT),),
        ingredients_by_material={
            "I-001": CatalogIngredientInput(
                ALIAS_SNAPSHOT, "synthetic-material-record", "MFDS_INGREDIENT_CODE", "I-001", "합성 성분"
            )
        },
        orders_by_observation={row.source_record_key: i for i, row in enumerate(inspection.observations, 1)},
        order_spec_version="synthetic-explicit-order-v1",
        repository=repository,
        approval_verifier=verifier,
    )
    loaded = await load_mfds_catalog(**kwargs)
    assert loaded.build.export.catalog.schema_version == "medication-catalog-v3"
    await load_mfds_catalog(**kwargs)
    async with factory() as session:
        set_id = await session.scalar(select(RagCatalogSet.id))
        stored = (
            await session.execute(
                text(
                    "SELECT source_snapshot_id, product_source_snapshot_id, ingredient_source_snapshot_id, observation_json FROM rag_medication_product_component"
                )
            )
        ).all()
        assert await session.scalar(select(func.count()).select_from(RagCatalogSet)) == 1
    assert len(stored) == 2
    assert all(row[:3] == (ALIAS_SNAPSHOT, PRODUCT_SNAPSHOT, ALIAS_SNAPSHOT) for row in stored)
    restored = await repository.load_build(set_id, approval_verifier=verifier)
    assert restored == loaded.build.export
    indexed = candidate(restored)
    assert isinstance(indexed, CandidateIndexBuildSuccess)
    assert indexed.components == restored.catalog.components
    assert {c.observation.tamt_seq for c in indexed.components} == {"01", "02"}
    assert {json.loads(row.observation_json)["quantity"] for row in stored} == {"010.00", "020.00"}
    # A damaged canonical artifact must never reach save_build.
    blocked = AsyncMock()
    with pytest.raises(ValueError, match="checksum mismatch"):
        await load_mfds_catalog(**dict(kwargs, detail_json=encoded + b" ", repository=blocked))
    blocked.save_build.assert_not_awaited()
    # A self-consistent fabricated receipt/artifact cannot replace the real Snapshot checksum.
    forged_json = json.dumps([dict(row, QNT="999.00") for row in rows], sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(CatalogDatabaseBindingError):
        await load_mfds_catalog(
            **dict(
                kwargs,
                detail_json=forged_json,
                detail_receipt=replace(detail_receipt, canonical_checksum=hashlib.sha256(forged_json).hexdigest()),
            )
        )
    assert await repository.load_build(set_id, approval_verifier=verifier) == restored
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(RagCatalogSet)) == 1
    # Recollection uses a new detail Snapshot, keeping the Product observation and old Set.
    new_detail_id = uuid4()
    new_rows = [dict(row, QNT="030.00") for row in rows]
    new_json = json.dumps(new_rows, sort_keys=True, separators=(",", ":")).encode()
    async with factory.begin() as session:
        prior = await session.get(RagSourceSnapshot, UUID(ALIAS_SNAPSHOT))
        values = {column.key: getattr(prior, column.key) for column in RagSourceSnapshot.__table__.columns}
        values.update(
            id=new_detail_id,
            source_version="external:v3",
            external_version="v3",
            canonical_checksum=hashlib.sha256(new_json).hexdigest(),
        )
        session.add(RagSourceSnapshot(**values))
    async with factory() as session:
        new_receipt = await SqlAlchemySourceSnapshotRepository(session).get_snapshot_receipt(snapshot_id=new_detail_id)
    next_load = await load_mfds_catalog(
        **dict(
            kwargs,
            catalog_version="synthetic-mfds-observation-v2",
            detail_receipt=new_receipt,
            detail_json=new_json,
            ingredients_by_material={
                "I-001": replace(kwargs["ingredients_by_material"]["I-001"], source_snapshot_id=str(new_detail_id))
            },
        )
    )
    assert next_load.build.export.export_checksum != loaded.build.export.export_checksum
    assert await repository.load_build(set_id, approval_verifier=verifier) == restored
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(RagCatalogSet)) == 2
        assert await session.scalar(text("SELECT count(*) FROM rag_medication_product_component")) == 4
        assert await session.scalar(text("SELECT count(*) FROM rag_medication_product")) == 1
    # Blank rows never become a partial successful Catalog; the original row remains in the report.
    blank_rows = [*rows, {"ITEM_SEQ": "P-001", "MTRAL_CODE": None}]
    blank_json = json.dumps(blank_rows, sort_keys=True, separators=(",", ":")).encode()
    not_saved = AsyncMock()
    excluded = await load_mfds_catalog(
        **dict(
            kwargs,
            detail_receipt=replace(detail_receipt, canonical_checksum=hashlib.sha256(blank_json).hexdigest()),
            detail_json=blank_json,
            repository=not_saved,
        )
    )
    assert excluded.build is None
    assert excluded.inspection.input_count == 3
    assert json.loads(excluded.inspection.exclusions[0].record_json) == blank_rows[-1]
    not_saved.save_build.assert_not_awaited()
