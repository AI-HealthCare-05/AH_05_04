"""#526 Phase 2: Catalog 승인 발급·철회 one-shot command 및 Concurrency Race 검증."""

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.catalog_approval_advisory_lock import acquire_catalog_approval_advisory_locks
from ai_worker.adapters.local_private_source_artifact_finalizer import LocalPrivateSourceArtifactReader
from ai_worker.adapters.sqlalchemy_catalog_approval_verifier import (
    SqlAlchemyCatalogApprovalVerifier,
)
from ai_worker.adapters.sqlalchemy_catalog_write_support import (
    CatalogDatabaseBindingError,
    SqlAlchemyCatalogBuildRepository,
)
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.admin.catalog_approval import (
    AUDIT_GRANT_PERMISSION,
    AUDIT_ISSUE_CATALOG,
    AUDIT_ISSUE_SOURCE,
    AUDIT_REVOKE_CATALOG,
    AUDIT_REVOKE_PERMISSION,
    AUDIT_REVOKE_SOURCE,
    ApprovalRequest,
    CatalogApprovalCommandError,
    ProductCatalogTarget,
    approval_url,
    issue_product_catalog,
    revoke_approval,
    set_permission,
)
from ai_worker.admin.catalog_writer import _execute_catalog_build, validate_catalog_writer
from ai_worker.tasks.rag.catalog.approval import approval_binding_from_manifest
from ai_worker.tasks.rag.catalog.build import CatalogProductInput, build_catalog_members
from ai_worker.tasks.rag.catalog.export import create_catalog_export
from ai_worker.tasks.rag.catalog.mfds_product_source import ProductSourceBindingError, read_product_input
from ai_worker.tasks.rag.catalog.types import (
    CandidateCatalogSourceRef,
    CandidateRecordStatus,
    CatalogVerificationStatus,
)
from app.models import (
    CatalogApprovalAudit,
    CatalogApprovalPermission,
    CatalogSourceApproval,
    RagCatalogSet,
    RagSource,
    RagSourceEndpoint,
    RagSourceIngestionArtifact,
    RagSourceIngestionRun,
    RagSourceOperation,
    RagSourceSnapshot,
    RagSourceSnapshotVerification,
    User,
)
from app.models.rag_source import (
    RagIngestionRunStatus,
    RagSourceIngestionArtifactKind,
    RagVerificationResultStatus,
)
from tests.integration.rag.test_catalog_storage_roundtrip import (
    approved_build,
    seed_catalog_approval_receipt,
    seed_catalog_approvals,
)
from tests.integration.rag.test_catalog_storage_roundtrip import unseeded_database as _database

database = _database

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _restore_tmp_permissions(tmp_path):
    yield
    try:
        resolved = tmp_path.resolve()
        os.chmod(resolved, 0o700)
        for p in resolved.rglob("*"):
            if p.is_dir():
                os.chmod(p, 0o700)
            else:
                os.chmod(p, 0o600)
    except OSError:
        pass


async def _create_user(factory, *, name: str = "합성 승인자") -> User:
    async with factory.begin() as session:
        user = User(
            email=f"operator-{uuid4().hex[:8]}@example.test",
            hashed_password="synthetic-only",
            name=name,
        )
        session.add(user)
        await session.flush()
        return user


async def _wait_for_advisory_lock_conflict(factory) -> None:
    for _ in range(50):
        async with factory() as session:
            statuses = (
                await session.execute(
                    text(
                        "SELECT granted FROM pg_locks "
                        "WHERE locktype = 'advisory' "
                        "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
                    )
                )
            ).scalars()
            observed = set(statuses)
            if observed == {False, True}:
                return
        await asyncio.sleep(0.02)
    raise AssertionError("Timed out waiting for the approval advisory-lock conflict")


async def _wait_until_expired(expires_at: datetime) -> None:
    while datetime.now(UTC) < expires_at:
        await asyncio.sleep(0.01)


async def _seed_product_source_fixture(
    factory,
    tmp_path: Path,
    *,
    snapshot_id: UUID | None = None,
    item_seq: str = "200000001",
    item_name: str = "합성 타이레놀정500밀리그람",
) -> tuple[UUID, UUID, LocalPrivateSourceArtifactReader]:
    snap_id = snapshot_id or UUID("00000000-0000-4000-8000-000000000099")
    run_id = uuid4()
    seal_id = uuid4()
    now = datetime.now(UTC)

    resolved_root = tmp_path.resolve()
    payload = {
        "header": {"resultCode": "00"},
        "body": {
            "items": [
                {
                    "ITEM_SEQ": item_seq,
                    "ITEM_NAME": item_name,
                    "ENTP_NAME": "(주)한국얀센",
                    "FORM_CODE_NAME": "정제",
                    "ITEM_PERMIT_DATE": "20200101",
                }
            ],
            "pageNo": 1,
            "numOfRows": 100,
            "totalCount": 1,
        },
    }
    raw_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    raw_checksum = hashlib.sha256(raw_bytes).hexdigest()
    object_key = f"mfds/{raw_checksum}.json"
    file_path = resolved_root / object_key
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(raw_bytes)

    async with factory.begin() as session:
        source = (
            await session.execute(select(RagSource).where(RagSource.source_code == "MFDS_PRODUCT_APPROVAL"))
        ).scalar_one_or_none()
        if source is None:
            source = RagSource(source_code="MFDS_PRODUCT_APPROVAL", display_name="의약품 품목 허가")
            session.add(source)
            await session.flush()

        endpoint = (
            await session.execute(
                select(RagSourceEndpoint).where(
                    RagSourceEndpoint.source_id == source.id,
                    RagSourceEndpoint.endpoint_code == "MFDS_PRODUCT_APPROVAL_API",
                )
            )
        ).scalar_one_or_none()
        if endpoint is None:
            endpoint = RagSourceEndpoint(
                source_id=source.id,
                endpoint_code="MFDS_PRODUCT_APPROVAL_API",
                display_name="의약품 품목허가 API",
            )
            session.add(endpoint)
            await session.flush()

        operation = (
            await session.execute(
                select(RagSourceOperation).where(
                    RagSourceOperation.endpoint_id == endpoint.id,
                    RagSourceOperation.operation_code == "LIST_APPROVED_PRODUCTS",
                )
            )
        ).scalar_one_or_none()
        if operation is None:
            operation = RagSourceOperation(
                endpoint_id=endpoint.id,
                operation_code="LIST_APPROVED_PRODUCTS",
                display_name="품목 목록 조회",
            )
            session.add(operation)
            await session.flush()

        snap_existing = await session.get(RagSourceSnapshot, snap_id)
        if snap_existing is None:
            session.add(
                RagSourceSnapshot(
                    id=snap_id,
                    operation_id=operation.id,
                    source_version="external:20260920",
                    external_version="20260920",
                    endpoint_receipt_hash="d" * 64,
                    raw_manifest_checksum="e" * 64,
                    canonical_checksum="f" * 64,
                    schema_version="mfds-product-v1",
                    parser_version="mfds-parser-v1",
                    normalization_version="mfds-norm-v1",
                    canonicalization_spec_version="mfds-spec-v1",
                    record_count=1,
                    rejected_record_count=0,
                    collected_at=now,
                    verification_status="PENDING",
                )
            )
            await session.flush()
            session.add(
                RagSourceSnapshotVerification(
                    id=seal_id,
                    snapshot_id=snap_id,
                    check_name="snapshot-publication-approval",
                    verification_result=RagVerificationResultStatus.PASSED,
                    verified_by="synthetic-operator",
                    verified_at=now,
                )
            )
            await session.flush()
            await session.execute(
                text(
                    "UPDATE rag_source_snapshot SET verification_status = 'CURRENT', verification_seal_id = :seal_id, "
                    "verified_at = :now, effective_at = :now WHERE id = :snapshot_id"
                ),
                {"seal_id": str(seal_id), "snapshot_id": str(snap_id), "now": now},
            )

        ingestion_run = RagSourceIngestionRun(
            id=run_id,
            operation_id=operation.id,
            snapshot_id=snap_id,
            run_status=RagIngestionRunStatus.SUCCEEDED,
            run_group_key=f"group-{run_id.hex[:8]}",
            attempt_number=1,
            started_at=now - timedelta(minutes=5),
            finished_at=now,
        )
        session.add(ingestion_run)
        await session.flush()

        artifact = RagSourceIngestionArtifact(
            id=uuid4(),
            ingestion_run_id=run_id,
            page_number=1,
            artifact_kind=RagSourceIngestionArtifactKind.RAW_RESPONSE,
            artifact_key="item.json",
            storage_backend="LOCAL_PRIVATE",
            object_key=object_key,
            raw_checksum=raw_checksum,
            byte_size=len(raw_bytes),
            content_type="application/json",
        )
        session.add(artifact)
        await session.flush()

    for p in resolved_root.rglob("*"):
        if p.is_dir():
            os.chmod(p, 0o500)
        else:
            os.chmod(p, 0o400)
    os.chmod(resolved_root, 0o500)

    reader = LocalPrivateSourceArtifactReader(resolved_root)
    return snap_id, run_id, reader


async def test_catalog_writer_reconciled_policy_reads_product_source_and_revalidates_approval(database, tmp_path):
    """The restricted Catalog Writer must execute its real Product Source read and save boundary.

    This deliberately uses the Writer login after the canonical policy helper;
    using the migration/admin login here would hide the production ACL defect.
    """
    from app.core import config
    from infra.python.catalog_role_policy import apply_catalog_role_policy

    engine, factory = database
    snapshot_id, ingestion_run_id, artifact_reader = await _seed_product_source_fixture(factory, tmp_path)
    source_ref = CandidateCatalogSourceRef(str(snapshot_id), "external:20260920")
    template_product = CatalogProductInput(
        source_snapshot_id=str(snapshot_id),
        source_record_key="ITEM_SEQ:200000001",
        code_system="MFDS_ITEM_SEQ",
        canonical_code="200000001",
        product_name="합성 타이레놀정500밀리그람",
        manufacturer_name="(주)한국얀센",
        product_status=CandidateRecordStatus.ACTIVE,
    )
    draft = create_catalog_export(
        catalog_version="catalog-writer-acl-v1",
        source_refs=(source_ref,),
        members=build_catalog_members(products=(template_product,)),
    )
    await seed_catalog_approval_receipt(
        factory,
        catalog_version="catalog-writer-acl-v1",
        export_checksum=draft.export_checksum,
        source_refs=(source_ref,),
    )

    suffix = uuid4().hex[:12]
    runtime, source_writer, catalog_writer = (
        f"catalog_acl_{part}_{suffix}" for part in ("runtime", "source", "writer")
    )
    password = "synthetic-catalog-acl-only"
    writer_engine = create_async_engine(
        engine.url.set(username=catalog_writer, password=password), hide_parameters=True
    )
    writer_factory = async_sessionmaker(writer_engine, expire_on_commit=False)
    environment = {
        "CATALOG_WRITER_HOST": engine.url.host or "127.0.0.1",
        "CATALOG_WRITER_PORT": str(engine.url.port),
        "CATALOG_WRITER_NAME": engine.url.database,
        "CATALOG_WRITER_USER": catalog_writer,
        "CATALOG_WRITER_PASSWORD": password,
        "CATALOG_SOURCE_ARTIFACT_READER_ROOT": str(tmp_path.resolve()),
    }
    try:
        async with engine.begin() as connection:
            for role in (runtime, source_writer, catalog_writer):
                await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'"))
            await apply_catalog_role_policy(
                connection,
                owner=config.DB_USER,
                runtime=runtime,
                writer=catalog_writer,
                source_writer=source_writer,
            )

        async with writer_engine.connect() as connection:
            await validate_catalog_writer(connection)

        # Each Product Source receipt query executes through the restricted role.
        async with writer_factory() as session:
            source_repository = SqlAlchemySourceSnapshotRepository(session)
            snapshot = await source_repository.get_snapshot_receipt(snapshot_id=snapshot_id)
            attempt = await source_repository.get_attempt_receipt(ingestion_run_id=ingestion_run_id)
            artifacts = await source_repository.get_ingestion_artifact_receipts(ingestion_run_id=ingestion_run_id)
            product, receipt = await read_product_input(
                repository=source_repository,
                artifact_reader=artifact_reader,
                source_snapshot_id=str(snapshot_id),
                ingestion_run_id=str(ingestion_run_id),
                item_seq="200000001",
            )
        assert snapshot is not None
        assert attempt is not None
        assert len(artifacts) == 1
        assert receipt.source_snapshot_id == snapshot_id
        assert product.canonical_code == "200000001"

        # The approval verifier and the full Writer path both use the same
        # restricted credentials. The latter revalidates approval under its
        # persistence transaction before acquiring existing lock markers.
        verifier = SqlAlchemyCatalogApprovalVerifier(writer_factory)
        approval = await verifier.verify(
            catalog_version="catalog-writer-acl-v1",
            export_checksum=draft.export_checksum,
            source_refs=(source_ref,),
        )
        assert approval is not None
        assert approval.verification_status is CatalogVerificationStatus.APPROVED
        result = await _execute_catalog_build(
            environment=environment,
            source_snapshot_id=str(snapshot_id),
            ingestion_run_id=str(ingestion_run_id),
            item_seq="200000001",
            catalog_version="catalog-writer-acl-v1",
        )
        assert result == {"execution_status": "ACTIVATION_CANDIDATE"}
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(RagCatalogSet)) == 1
    finally:
        await writer_engine.dispose()
        async with engine.begin() as connection:
            for role in (runtime, source_writer, catalog_writer):
                if await connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
                    await connection.execute(text(f'DROP OWNED BY "{role}"'))
                    await connection.execute(text(f'DROP ROLE "{role}"'))


# 1. Permission missing/disabled fail
async def test_permission_missing_or_disabled_blocks_operator(database, tmp_path):
    _, factory = database
    operator = await _create_user(factory)
    snap_id, run_id, reader = await _seed_product_source_fixture(factory, tmp_path)
    req = ApprovalRequest(actor_id=operator.id, request_id=uuid4(), evidence_ref="synthetic://test")
    target = ProductCatalogTarget(
        source_snapshot_id=str(snap_id),
        ingestion_run_id=str(run_id),
        item_seq="200000001",
        catalog_version="test-cat-v1",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    # 1a: Missing permission row
    async with factory() as session, session.begin():
        with pytest.raises(CatalogApprovalCommandError) as exc_info:
            await issue_product_catalog(session, request=req, artifact_reader=reader, target=target)
        assert exc_info.value.code == "BLOCKED_BY_APPROVAL_PERMISSION"

    # 1b: Disabled permission row
    async with factory() as session, session.begin():
        await set_permission(session, request=req, user_id=operator.id, enabled=False)

    async with factory() as session, session.begin():
        with pytest.raises(CatalogApprovalCommandError) as exc_info:
            await issue_product_catalog(session, request=req, artifact_reader=reader, target=target)
        assert exc_info.value.code == "BLOCKED_BY_APPROVAL_PERMISSION"


# 2. Grant / revoke permission
async def test_grant_and_revoke_permission_updates_revision_and_audits(database):
    _, factory = database
    admin_user = await _create_user(factory, name="관리자")
    subject_user = await _create_user(factory, name="대상 운영자")

    # Grant permission
    req_grant = ApprovalRequest(actor_id=admin_user.id, request_id=uuid4(), evidence_ref="synthetic://grant")
    async with factory() as session, session.begin():
        res_grant = await set_permission(session, request=req_grant, user_id=subject_user.id, enabled=True)
    assert res_grant["status"] == "APPLIED"
    assert res_grant["event_kind"] == AUDIT_GRANT_PERMISSION
    assert res_grant["revision"] == 1

    # Revoke permission
    req_revoke = ApprovalRequest(actor_id=admin_user.id, request_id=uuid4(), evidence_ref="synthetic://revoke")
    async with factory() as session, session.begin():
        res_revoke = await set_permission(session, request=req_revoke, user_id=subject_user.id, enabled=False)
    assert res_revoke["status"] == "APPLIED"
    assert res_revoke["event_kind"] == AUDIT_REVOKE_PERMISSION
    assert res_revoke["revision"] == 2

    # Check permission table
    async with factory() as session:
        perm = (
            await session.execute(
                select(CatalogApprovalPermission).where(CatalogApprovalPermission.user_id == subject_user.id)
            )
        ).scalar_one()
        assert perm.enabled is False
        assert perm.revision == 2


# 3. Six distinct audit events separation
async def test_six_audit_events_separation_and_full_lifecycle(database, tmp_path):
    _, factory = database
    admin_user = await _create_user(factory)
    operator = await _create_user(factory)
    snap_id, run_id, reader = await _seed_product_source_fixture(factory, tmp_path)

    # 1. GRANT_PERMISSION
    async with factory() as session, session.begin():
        await set_permission(
            session,
            request=ApprovalRequest(admin_user.id, uuid4(), "synthetic://grant"),
            user_id=operator.id,
            enabled=True,
        )

    # 2. ISSUE_SOURCE and 3. ISSUE_CATALOG
    target = ProductCatalogTarget(
        source_snapshot_id=str(snap_id),
        ingestion_run_id=str(run_id),
        item_seq="200000001",
        catalog_version="test-cat-v2",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    async with factory() as session, session.begin():
        issue_res = await issue_product_catalog(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://issue"),
            artifact_reader=reader,
            target=target,
        )
    src_approval_id = UUID(str(issue_res["source_approval_id"]))
    build_approval_id = UUID(str(issue_res["build_approval_id"]))

    # 4. REVOKE_SOURCE
    async with factory() as session, session.begin():
        await revoke_approval(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://revoke-src"),
            approval_id=src_approval_id,
            event_kind=AUDIT_REVOKE_SOURCE,
        )

    # 5. REVOKE_CATALOG
    async with factory() as session, session.begin():
        await revoke_approval(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://revoke-build"),
            approval_id=build_approval_id,
            event_kind=AUDIT_REVOKE_CATALOG,
        )

    # 6. REVOKE_PERMISSION
    async with factory() as session, session.begin():
        await set_permission(
            session,
            request=ApprovalRequest(admin_user.id, uuid4(), "synthetic://revoke-perm"),
            user_id=operator.id,
            enabled=False,
        )

    # Verify all 6 distinct audit events exist
    async with factory() as session:
        events = set((await session.execute(select(CatalogApprovalAudit.event_kind))).scalars().all())
    expected_events = {
        AUDIT_GRANT_PERMISSION,
        AUDIT_REVOKE_PERMISSION,
        AUDIT_ISSUE_SOURCE,
        AUDIT_ISSUE_CATALOG,
        AUDIT_REVOKE_SOURCE,
        AUDIT_REVOKE_CATALOG,
    }
    assert expected_events <= events


# 4. Audit table immutability / append-only
async def test_audit_table_append_only(database):
    _, factory = database
    user = await _create_user(factory)
    req = ApprovalRequest(user.id, uuid4(), "synthetic://audit")
    async with factory() as session, session.begin():
        res = await set_permission(session, request=req, user_id=user.id, enabled=True)
    audit_id = UUID(res["audit_id"])

    async with factory() as session:
        audit_row = await session.get(CatalogApprovalAudit, audit_id)
        assert audit_row is not None


# 5. Idempotent replay & fingerprint conflict fail
async def test_idempotent_replay_and_fingerprint_conflict(database):
    _, factory = database
    user = await _create_user(factory)
    req_id = uuid4()
    req = ApprovalRequest(user.id, req_id, "synthetic://replay")

    # First run: APPLIED
    async with factory() as session, session.begin():
        res1 = await set_permission(session, request=req, user_id=user.id, enabled=True)
    assert res1["status"] == "APPLIED"

    # Second run with exact same payload: REPLAYED
    async with factory() as session, session.begin():
        res2 = await set_permission(session, request=req, user_id=user.id, enabled=True)
    assert res2["status"] == "REPLAYED"
    assert res2["audit_id"] == res1["audit_id"]

    # Third run with different payload (conflict): BLOCKED_BY_REQUEST_FINGERPRINT_CONFLICT
    conflicting_req = ApprovalRequest(user.id, req_id, "synthetic://different-evidence")
    async with factory() as session, session.begin():
        with pytest.raises(CatalogApprovalCommandError) as exc_info:
            await set_permission(session, request=conflicting_req, user_id=user.id, enabled=True)
        assert exc_info.value.code == "BLOCKED_BY_REQUEST_FINGERPRINT_CONFLICT"


# 6. Checksum self-computation & authority validation fail
async def test_checksum_self_computation_and_authority_validation(database, tmp_path):
    _, factory = database
    operator = await _create_user(factory)
    async with factory() as session, session.begin():
        await set_permission(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://grant"),
            user_id=operator.id,
            enabled=True,
        )

    snap_id, run_id, reader = await _seed_product_source_fixture(factory, tmp_path)

    # 6a: Valid target computes checksum without user providing it
    target = ProductCatalogTarget(
        source_snapshot_id=str(snap_id),
        ingestion_run_id=str(run_id),
        item_seq="200000001",
        catalog_version="test-self-compute-v1",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    async with factory() as session, session.begin():
        res = await issue_product_catalog(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://issue"),
            artifact_reader=reader,
            target=target,
        )
    assert res["status"] == "APPLIED"
    assert len(str(res["export_checksum"])) == 64

    # 6b: Invalid snapshot authority fails closed
    bad_target = ProductCatalogTarget(
        source_snapshot_id=str(uuid4()),
        ingestion_run_id=str(run_id),
        item_seq="200000001",
        catalog_version="test-self-compute-v1",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    async with factory() as session, session.begin():
        with pytest.raises(ProductSourceBindingError):
            await issue_product_catalog(
                session,
                request=ApprovalRequest(operator.id, uuid4(), "synthetic://issue"),
                artifact_reader=reader,
                target=bad_target,
            )


# 7. Active duplicate fail & 8. Re-issuance after revoke/expiry
async def test_active_duplicate_fail_and_reissuance_after_revoke(database, tmp_path):
    _, factory = database
    operator = await _create_user(factory)
    async with factory() as session, session.begin():
        await set_permission(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://grant"),
            user_id=operator.id,
            enabled=True,
        )

    snap_id, run_id, reader = await _seed_product_source_fixture(factory, tmp_path)
    target = ProductCatalogTarget(
        source_snapshot_id=str(snap_id),
        ingestion_run_id=str(run_id),
        item_seq="200000001",
        catalog_version="test-active-duplicate-v1",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    # First issue succeeds
    async with factory() as session, session.begin():
        res1 = await issue_product_catalog(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://issue-1"),
            artifact_reader=reader,
            target=target,
        )
    assert res1["status"] == "APPLIED"

    # Duplicate issue while active fails with BLOCKED_BY_ACTIVE_SOURCE_APPROVAL or BLOCKED_BY_ACTIVE_CATALOG_APPROVAL
    async with factory() as session, session.begin():
        with pytest.raises(CatalogApprovalCommandError) as exc_info:
            await issue_product_catalog(
                session,
                request=ApprovalRequest(operator.id, uuid4(), "synthetic://issue-dup"),
                artifact_reader=reader,
                target=target,
            )
        assert exc_info.value.code in ("BLOCKED_BY_ACTIVE_SOURCE_APPROVAL", "BLOCKED_BY_ACTIVE_CATALOG_APPROVAL")

    # Revoke both approvals
    async with factory() as session, session.begin():
        await revoke_approval(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://rev-src"),
            approval_id=UUID(str(res1["source_approval_id"])),
            event_kind=AUDIT_REVOKE_SOURCE,
        )
        await revoke_approval(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://rev-cat"),
            approval_id=UUID(str(res1["build_approval_id"])),
            event_kind=AUDIT_REVOKE_CATALOG,
        )

    # Re-issuance after revocation succeeds and increments revision
    async with factory() as session, session.begin():
        res2 = await issue_product_catalog(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://issue-reissue"),
            artifact_reader=reader,
            target=target,
        )
    assert res2["status"] == "APPLIED"


# 9. Credential isolation (forbidden keys)
async def test_credential_isolation_rejects_forbidden_keys():
    for forbidden in (
        "DB_PASSWORD",
        "DB_APP_PASSWORD",
        "DB_ADMIN_PASSWORD",
        "DB_MIGRATION_PASSWORD",
        "SOURCE_WRITER_PASSWORD",
        "CATALOG_WRITER_PASSWORD",
        "CANDIDATE_INDEX_BUILDER_PASSWORD",
    ):
        env = {
            forbidden: "secret",
            "CATALOG_APPROVAL_HOST": "127.0.0.1",
            "CATALOG_APPROVAL_PORT": "5432",
            "CATALOG_APPROVAL_NAME": "testdb",
            "CATALOG_APPROVAL_USER": "appr",
            "CATALOG_APPROVAL_PASSWORD": "pass",
        }
        with pytest.raises(CatalogApprovalCommandError) as exc_info:
            approval_url(env)
        assert exc_info.value.code == "BLOCKED_BY_CREDENTIAL_ISOLATION"


# 10. Unknown operator fail
async def test_unknown_operator_rejected(database):
    _, factory = database
    unknown_id = uuid4()
    req = ApprovalRequest(unknown_id, uuid4(), "synthetic://unknown")
    async with factory() as session, session.begin():
        with pytest.raises(CatalogApprovalCommandError) as exc_info:
            await set_permission(session, request=req, user_id=unknown_id, enabled=True)
        assert exc_info.value.code == "BLOCKED_BY_UNKNOWN_OPERATOR"


# 11. Unknown approval fail on revoke
async def test_unknown_approval_revocation_rejected(database):
    _, factory = database
    operator = await _create_user(factory)
    async with factory() as session, session.begin():
        await set_permission(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://grant"),
            user_id=operator.id,
            enabled=True,
        )
    async with factory() as session, session.begin():
        with pytest.raises(CatalogApprovalCommandError) as exc_info:
            await revoke_approval(
                session,
                request=ApprovalRequest(operator.id, uuid4(), "synthetic://revoke"),
                approval_id=uuid4(),
                event_kind=AUDIT_REVOKE_SOURCE,
            )
        assert exc_info.value.code == "BLOCKED_BY_UNKNOWN_APPROVAL"


# 12. Already revoked approval fail
async def test_already_revoked_approval_rejected(database, tmp_path):
    _, factory = database
    operator = await _create_user(factory)
    async with factory() as session, session.begin():
        await set_permission(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://grant"),
            user_id=operator.id,
            enabled=True,
        )

    snap_id, run_id, reader = await _seed_product_source_fixture(factory, tmp_path)
    target = ProductCatalogTarget(
        source_snapshot_id=str(snap_id),
        ingestion_run_id=str(run_id),
        item_seq="200000001",
        catalog_version="test-already-revoked-v1",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    async with factory() as session, session.begin():
        issue_res = await issue_product_catalog(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://issue"),
            artifact_reader=reader,
            target=target,
        )
    src_id = UUID(str(issue_res["source_approval_id"]))

    # First revoke succeeds
    async with factory() as session, session.begin():
        await revoke_approval(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://rev1"),
            approval_id=src_id,
            event_kind=AUDIT_REVOKE_SOURCE,
        )

    # Second revoke fails with BLOCKED_BY_ALREADY_REVOKED
    async with factory() as session, session.begin():
        with pytest.raises(CatalogApprovalCommandError) as exc_info:
            await revoke_approval(
                session,
                request=ApprovalRequest(operator.id, uuid4(), "synthetic://rev2"),
                approval_id=src_id,
                event_kind=AUDIT_REVOKE_SOURCE,
            )
        assert exc_info.value.code == "BLOCKED_BY_ALREADY_REVOKED"


# 13. Validity window boundary fail (expired expires_at)
async def test_validity_window_boundary_rejected_if_expired(database, tmp_path):
    _, factory = database
    operator = await _create_user(factory)
    async with factory() as session, session.begin():
        await set_permission(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://grant"),
            user_id=operator.id,
            enabled=True,
        )

    snap_id, run_id, reader = await _seed_product_source_fixture(factory, tmp_path)
    expired_target = ProductCatalogTarget(
        source_snapshot_id=str(snap_id),
        ingestion_run_id=str(run_id),
        item_seq="200000001",
        catalog_version="test-expired-v1",
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    async with factory() as session, session.begin():
        with pytest.raises(CatalogApprovalCommandError) as exc_info:
            await issue_product_catalog(
                session,
                request=ApprovalRequest(operator.id, uuid4(), "synthetic://issue"),
                artifact_reader=reader,
                target=expired_target,
            )
        assert exc_info.value.code == "BLOCKED_BY_APPROVAL_VALIDITY_WINDOW"


# 14. Revoke source stops consumption in verifier
async def test_revoke_source_stops_verifier(database, tmp_path):
    _, factory = database
    operator = await _create_user(factory)
    async with factory() as session, session.begin():
        await set_permission(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://grant"),
            user_id=operator.id,
            enabled=True,
        )

    snap_id, run_id, reader = await _seed_product_source_fixture(factory, tmp_path)
    target = ProductCatalogTarget(
        source_snapshot_id=str(snap_id),
        ingestion_run_id=str(run_id),
        item_seq="200000001",
        catalog_version="test-verifier-revoke-v1",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    async with factory() as session, session.begin():
        issue_res = await issue_product_catalog(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://issue"),
            artifact_reader=reader,
            target=target,
        )

    verifier = SqlAlchemyCatalogApprovalVerifier(factory)
    async with factory() as session:
        src_row = await session.get(CatalogSourceApproval, UUID(str(issue_res["source_approval_id"])))
        ref = CandidateCatalogSourceRef(str(snap_id), src_row.source_version)

    receipt = await verifier.verify(
        catalog_version=target.catalog_version,
        export_checksum=str(issue_res["export_checksum"]),
        source_refs=(ref,),
    )
    assert receipt is not None
    assert receipt.verification_status is CatalogVerificationStatus.APPROVED

    # Revoke the source approval
    async with factory() as session, session.begin():
        await revoke_approval(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://rev-src"),
            approval_id=UUID(str(issue_res["source_approval_id"])),
            event_kind=AUDIT_REVOKE_SOURCE,
        )

    # Verifier now refuses
    receipt_after = await verifier.verify(
        catalog_version=target.catalog_version,
        export_checksum=str(issue_res["export_checksum"]),
        source_refs=(ref,),
    )
    assert receipt_after is None


# 15. Source set mismatch / validation error
async def test_source_set_mismatch_fails_verifier(database, tmp_path):
    _, factory = database
    operator = await _create_user(factory)
    async with factory() as session, session.begin():
        await set_permission(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://grant"),
            user_id=operator.id,
            enabled=True,
        )

    snap_id, run_id, reader = await _seed_product_source_fixture(factory, tmp_path)
    target = ProductCatalogTarget(
        source_snapshot_id=str(snap_id),
        ingestion_run_id=str(run_id),
        item_seq="200000001",
        catalog_version="test-mismatch-v1",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    async with factory() as session, session.begin():
        issue_res = await issue_product_catalog(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://issue"),
            artifact_reader=reader,
            target=target,
        )

    verifier = SqlAlchemyCatalogApprovalVerifier(factory)
    async with factory() as session:
        src_row = await session.get(CatalogSourceApproval, UUID(str(issue_res["source_approval_id"])))
        ref = CandidateCatalogSourceRef(str(snap_id), src_row.source_version)

    # Mismatch: extra source snapshot
    extra_ref = CandidateCatalogSourceRef(str(uuid4()), "v99")
    receipt = await verifier.verify(
        catalog_version=target.catalog_version,
        export_checksum=str(issue_res["export_checksum"]),
        source_refs=(ref, extra_ref),
    )
    assert receipt is None


# 16. Grant permission revision idempotency replay
async def test_grant_permission_replay_and_status(database):
    _, factory = database
    admin_user = await _create_user(factory)
    subject = await _create_user(factory)
    req_id = uuid4()
    req = ApprovalRequest(admin_user.id, req_id, "synthetic://idemp")

    async with factory() as session, session.begin():
        res1 = await set_permission(session, request=req, user_id=subject.id, enabled=True)
    assert res1["status"] == "APPLIED"

    async with factory() as session, session.begin():
        res2 = await set_permission(session, request=req, user_id=subject.id, enabled=True)
    assert res2["status"] == "REPLAYED"


# 17. PostgreSQL Race Test Case A:
# Revoke lock first -> save waits -> revoke commit -> save revalidation fails.
async def test_race_case_a_revoke_serializes_before_save_revalidation_fails(database, monkeypatch):
    _, factory = database
    operator = await _create_user(factory)

    members, artifacts, _ = approved_build()
    await seed_catalog_approvals(factory, [artifacts])
    binding = approval_binding_from_manifest(artifacts.manifest_json)
    b_id = UUID(binding.build_approval_id)

    # Enable operator permission
    async with factory() as session, session.begin():
        await set_permission(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://grant"),
            user_id=operator.id,
            enabled=True,
        )

    event_revoke_locked = asyncio.Event()
    event_release_revoke = asyncio.Event()
    event_save_attempting = asyncio.Event()

    import ai_worker.admin.catalog_approval as admin_approval_mod

    orig_admin_locks = admin_approval_mod.acquire_catalog_approval_advisory_locks

    async def _hooked_admin_locks(session, approval_ids):
        keys = await orig_admin_locks(session, approval_ids)
        event_revoke_locked.set()
        await asyncio.wait_for(event_release_revoke.wait(), timeout=5.0)
        return keys

    monkeypatch.setattr(admin_approval_mod, "acquire_catalog_approval_advisory_locks", _hooked_admin_locks)

    import ai_worker.adapters.sqlalchemy_catalog_approval_verifier as verifier_mod

    orig_verifier_locks = verifier_mod.acquire_catalog_approval_advisory_locks

    async def _hooked_verifier_locks(session, approval_ids):
        event_save_attempting.set()
        return await orig_verifier_locks(session, approval_ids)

    monkeypatch.setattr(verifier_mod, "acquire_catalog_approval_advisory_locks", _hooked_verifier_locks)

    # 1. Task 1: Start revoke_approval (acquires lock, waits on event_release_revoke)
    revoke_req = ApprovalRequest(operator.id, uuid4(), "synthetic://race-revoke")

    async def _run_revoke():
        async with factory() as session, session.begin():
            return await revoke_approval(
                session,
                request=revoke_req,
                approval_id=b_id,
                event_kind=AUDIT_REVOKE_CATALOG,
            )

    revoke_task = asyncio.create_task(_run_revoke())
    await asyncio.wait_for(event_revoke_locked.wait(), timeout=5.0)

    # 2. Task 2: Start save_build concurrently for the same build
    repo = SqlAlchemyCatalogBuildRepository(factory)

    async def _run_save():
        await repo.save_build(members=members, artifacts=artifacts)

    save_task = asyncio.create_task(_run_save())

    try:
        await asyncio.wait_for(event_save_attempting.wait(), timeout=5.0)
        # Give save_task a moment to enter the blocking pg_advisory_xact_lock in postgres
        await asyncio.sleep(0.05)

        # 3. Observe conflict on pg_locks
        for _ in range(50):
            async with factory() as check_session:
                res = await check_session.execute(text("SELECT granted FROM pg_locks WHERE locktype = 'advisory'"))
                statuses = [r[0] for r in res.fetchall()]
                if True in statuses and False in statuses:
                    break
            await asyncio.sleep(0.02)
    finally:
        event_release_revoke.set()

    # 4. Revoke commits and releases advisory lock
    revoke_res = await asyncio.wait_for(revoke_task, timeout=5.0)
    assert revoke_res["status"] == "APPLIED"

    # 5. Save task unblocks, revalidation detects revoked_at is not None, and fails!
    with pytest.raises(CatalogDatabaseBindingError):
        await asyncio.wait_for(save_task, timeout=5.0)

    # Verify Catalog 저장 0건 (FAIL CLOSED)
    async with factory() as session:
        sets = (await session.execute(select(RagCatalogSet))).scalars().all()
        assert len(sets) == 0


# 18. PostgreSQL Race Test Case B:
# Save lock first -> revoke waits -> save commit -> revoke -> subsequent load fails.
async def test_race_case_b_save_serializes_before_revoke_subsequent_load_fails(database, monkeypatch):
    _, factory = database
    operator = await _create_user(factory)

    members, artifacts, _ = approved_build()
    await seed_catalog_approvals(factory, [artifacts])
    binding = approval_binding_from_manifest(artifacts.manifest_json)
    b_id = UUID(binding.build_approval_id)

    # Enable operator permission
    async with factory() as session, session.begin():
        await set_permission(
            session,
            request=ApprovalRequest(operator.id, uuid4(), "synthetic://grant"),
            user_id=operator.id,
            enabled=True,
        )

    event_save_locked = asyncio.Event()
    event_release_save = asyncio.Event()
    event_revoke_attempting = asyncio.Event()

    import ai_worker.adapters.sqlalchemy_catalog_approval_verifier as verifier_mod

    orig_verifier_locks = verifier_mod.acquire_catalog_approval_advisory_locks

    async def _hooked_verifier_locks(session, approval_ids):
        keys = await orig_verifier_locks(session, approval_ids)
        event_save_locked.set()
        await asyncio.wait_for(event_release_save.wait(), timeout=5.0)
        return keys

    monkeypatch.setattr(verifier_mod, "acquire_catalog_approval_advisory_locks", _hooked_verifier_locks)

    import ai_worker.admin.catalog_approval as admin_approval_mod

    orig_admin_locks = admin_approval_mod.acquire_catalog_approval_advisory_locks

    async def _hooked_admin_locks(session, approval_ids):
        event_revoke_attempting.set()
        return await orig_admin_locks(session, approval_ids)

    monkeypatch.setattr(admin_approval_mod, "acquire_catalog_approval_advisory_locks", _hooked_admin_locks)

    # 1. Task 1: Start save_build (acquires lock, waits on event_release_save)
    repo = SqlAlchemyCatalogBuildRepository(factory)

    async def _run_save():
        await repo.save_build(members=members, artifacts=artifacts)

    save_task = asyncio.create_task(_run_save())
    await asyncio.wait_for(event_save_locked.wait(), timeout=5.0)

    # 2. Task 2: Start revoke concurrently (will block on advisory lock)
    revoke_req = ApprovalRequest(operator.id, uuid4(), "synthetic://race-b-revoke")

    async def _run_revoke():
        async with factory() as session, session.begin():
            return await revoke_approval(
                session,
                request=revoke_req,
                approval_id=b_id,
                event_kind=AUDIT_REVOKE_CATALOG,
            )

    revoke_task = asyncio.create_task(_run_revoke())

    try:
        await asyncio.wait_for(event_revoke_attempting.wait(), timeout=5.0)
        await asyncio.sleep(0.05)

        # 3. Observe conflict on pg_locks
        for _ in range(50):
            async with factory() as check_session:
                res = await check_session.execute(text("SELECT granted FROM pg_locks WHERE locktype = 'advisory'"))
                statuses = [r[0] for r in res.fetchall()]
                if True in statuses and False in statuses:
                    break
            await asyncio.sleep(0.02)
    finally:
        event_release_save.set()

    # 4. Save completes and commits
    await asyncio.wait_for(save_task, timeout=5.0)

    # 5. Revoke unblocks, acquires lock, and commits
    revoke_res = await asyncio.wait_for(revoke_task, timeout=5.0)
    assert revoke_res["status"] == "APPLIED"

    # 6. Retrieve set_id from saved catalog set
    async with factory() as session:
        set_id = await session.scalar(select(RagCatalogSet.id))
    assert set_id is not None

    # 7. Subsequent load_build fails because approval is now revoked!
    with pytest.raises(CatalogDatabaseBindingError):
        await repo.load_build(set_id, approval_verifier=None)


@pytest.mark.parametrize("operation", ["save", "load"])
async def test_expiry_after_advisory_lock_wait_fails_closed(database, monkeypatch, operation):
    """Exact approval time is evaluated after a real PostgreSQL lock wait, for save and load."""
    _, factory = database
    members, artifacts, _ = approved_build()
    binding = approval_binding_from_manifest(artifacts.manifest_json)
    repository = SqlAlchemyCatalogBuildRepository(factory)
    await seed_catalog_approvals(factory, [artifacts])

    set_id = None
    if operation == "load":
        await repository.save_build(members=members, artifacts=artifacts)
        async with factory() as session:
            set_id = await session.scalar(select(RagCatalogSet.id))
        assert set_id is not None

    expires_at = datetime.now(UTC) + timedelta(seconds=2)
    async with factory.begin() as session:
        await session.execute(
            text("UPDATE catalog_build_approval SET expires_at=:expires_at WHERE id=:approval_id"),
            {"expires_at": expires_at, "approval_id": binding.build_approval_id},
        )
        await session.execute(
            text("UPDATE catalog_source_approval SET expires_at=:expires_at WHERE id = ANY(:approval_ids)"),
            {"expires_at": expires_at, "approval_ids": list(binding.source_approval_ids)},
        )

    lock_acquired = asyncio.Event()
    release_lock = asyncio.Event()
    verification_attempted = asyncio.Event()

    async def _hold_approval_locks():
        async with factory() as session, session.begin():
            await acquire_catalog_approval_advisory_locks(session, binding.approval_ids)
            lock_acquired.set()
            await asyncio.wait_for(release_lock.wait(), timeout=5.0)

    import ai_worker.adapters.sqlalchemy_catalog_approval_verifier as verifier_mod

    original_verifier_locks = verifier_mod.acquire_catalog_approval_advisory_locks

    async def _observe_verifier_wait(session, approval_ids):
        verification_attempted.set()
        return await original_verifier_locks(session, approval_ids)

    monkeypatch.setattr(verifier_mod, "acquire_catalog_approval_advisory_locks", _observe_verifier_wait)

    holder_task = asyncio.create_task(_hold_approval_locks())
    await asyncio.wait_for(lock_acquired.wait(), timeout=5.0)
    assert datetime.now(UTC) < expires_at
    if operation == "save":
        operation_task = asyncio.create_task(repository.save_build(members=members, artifacts=artifacts))
    else:
        operation_task = asyncio.create_task(repository.load_build(set_id, approval_verifier=None))

    try:
        await asyncio.wait_for(verification_attempted.wait(), timeout=5.0)
        await asyncio.wait_for(_wait_for_advisory_lock_conflict(factory), timeout=2.0)
        await asyncio.wait_for(_wait_until_expired(expires_at), timeout=3.0)
    finally:
        release_lock.set()

    await asyncio.wait_for(holder_task, timeout=5.0)
    with pytest.raises(CatalogDatabaseBindingError):
        await asyncio.wait_for(operation_task, timeout=5.0)

    if operation == "save":
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(RagCatalogSet)) == 0
