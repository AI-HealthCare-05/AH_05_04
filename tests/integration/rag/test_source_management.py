"""Management boundaries on disposable PostgreSQL databases, never an operating DB."""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.admin.source_management_api import management_app
from app.admin.source_management_contract import ManagementCommand, ReasonCode, TargetKind, UpdateCommand
from app.admin.source_management_permissions import PermissionChange, set_permission
from app.admin.source_management_service import ManagementActor, SourceManagementService, row_hash
from app.core import config
from app.core.db.databases import Base, get_db_session
from app.core.errors import ApiError
from app.models.rag_catalog import RagMedicationProduct
from app.models.rag_source import (
    RagSource,
    RagSourceEndpoint,
    RagSourceOperation,
    RagSourceSnapshot,
    RagSourceSnapshotVerification,
)
from app.models.source_management import SourceManagementAudit, SourceManagementPermission
from app.models.users import AccountStatus, User
from app.services.jwt import JwtService
from infra.python.source_management_role_policy import apply_management_role_policy, validate_management_connection


@pytest_asyncio.fixture
async def database(request):
    suffix = uuid4().hex[:10]
    name = f"manage398_{suffix}"
    url = URL.create(
        "postgresql+asyncpg",
        username=config.DB_USER,
        password=config.DB_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_EXPOSE_PORT,
        database=config.DB_NAME,
    )
    cluster = create_async_engine(url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    engine = create_async_engine(url.set(database=name), hide_parameters=True)
    roles = tuple(f"manage398_{part}_{suffix}" for part in ("runtime", "writer", "manager"))
    try:
        async with cluster.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{name}"'))
        if getattr(request, "param", None) != "migration":
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        yield engine, url.set(database=name), roles
    finally:
        await engine.dispose()
        async with cluster.connect() as connection:
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            for role in roles:
                await connection.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
        await cluster.dispose()


async def seed(engine, *, allowed=True):
    async with async_sessionmaker(engine, expire_on_commit=False).begin() as session:
        user = User(
            email=f"{uuid4().hex[:16]}@example.test",
            hashed_password="synthetic",
            name="합성 작업자",
            is_active=True,
            account_status=AccountStatus.ACTIVE,
            token_version=0,
        )
        source = RagSource(source_code=uuid4().hex, display_name="합성 출처", lifecycle_status="DRAFT")
        session.add_all([user, source])
        await session.flush()
        if allowed:
            session.add(SourceManagementPermission(user_id=user.id, enabled=True, approval_hash="a" * 64))
        return ManagementActor(user.id, 0), source.id


def command(revision, digest, **extra):
    return UpdateCommand(
        expected_revision=revision,
        expected_hash=digest,
        reason_code=ReasonCode.CORRECTION,
        approval_hash="b" * 64,
        request_id=uuid4(),
        changes={"display_name": "수정 합성 출처"},
        **extra,
    )


async def inspect_target(engine, actor, kind, target_id):
    async with async_sessionmaker(engine).begin() as session:
        return await SourceManagementService(session).inspect_target(actor, kind, target_id)


async def mutate(engine, actor, kind, target_id, request):
    async with async_sessionmaker(engine).begin() as session:
        return await SourceManagementService(session).mutate(actor, kind, target_id, request)


async def test_update_delete_and_retries_keep_one_atomic_receipt(database):
    engine, _, _ = database
    actor, target_id = await seed(engine)
    original = await inspect_target(engine, actor, TargetKind.SOURCE, target_id)
    request = command(original.revision, original.hash)
    result = await mutate(engine, actor, TargetKind.SOURCE, target_id, request)
    assert result.revision == 1 and result.hash != original.hash
    assert await mutate(engine, actor, TargetKind.SOURCE, target_id, request) == result
    deletion = ManagementCommand(
        **request.model_dump(exclude={"changes"}),
    ).model_copy(update={"request_id": uuid4(), "expected_revision": result.revision, "expected_hash": result.hash})
    deleted = await mutate(engine, actor, TargetKind.SOURCE, target_id, deletion)
    assert deleted.revision is None and deleted.hash is None
    assert await mutate(engine, actor, TargetKind.SOURCE, target_id, deletion) == deleted
    async with async_sessionmaker(engine)() as session:
        assert await session.get(RagSource, target_id) is None
        audits = (
            await session.scalars(select(SourceManagementAudit).where(SourceManagementAudit.target_id == target_id))
        ).all()
        assert len(audits) == 2
        assert audits[0].actor_id == actor.user_id
        assert all(a.permission == "source_catalog_manage" for a in audits)
        assert "합성" not in str([a.before_provenance for a in audits])


@pytest.mark.parametrize("case", ["forbidden", "stale", "identity", "referenced", "active", "conflicting_retry"])
async def test_fail_closed_cases_leave_no_mutation(database, case):
    engine, _, _ = database
    actor, target_id = await seed(engine, allowed=case != "forbidden")
    async with async_sessionmaker(engine).begin() as session:
        row = await session.get(RagSource, target_id)
        if case == "active":
            row.lifecycle_status = "ACTIVE"
        if case == "referenced":
            session.add(
                RagSourceEndpoint(
                    source_id=target_id,
                    endpoint_code="synthetic",
                    display_name="endpoint",
                    lifecycle_status="DRAFT",
                    runtime_status="DISABLED",
                    acquisition_status="PENDING",
                )
            )
        await session.flush()
        await session.refresh(row)
        request = command(0, row_hash(row))
    if case == "stale":
        request.expected_hash = "0" * 64
    if case == "identity":
        request.changes = {"source_code": "new-code"}
    if case == "conflicting_retry":
        await mutate(engine, actor, TargetKind.SOURCE, target_id, request)
        request.changes = {"display_name": "different"}
    with pytest.raises(ApiError) as error:
        await mutate(engine, actor, TargetKind.SOURCE, target_id, request)
    assert error.value.status_code == (403 if case == "forbidden" else 422 if case == "identity" else 409)
    async with async_sessionmaker(engine)() as session:
        row = await session.get(RagSource, target_id)
        assert row.display_name == ("수정 합성 출처" if case == "conflicting_retry" else "합성 출처")
        assert await session.scalar(select(func.count()).select_from(SourceManagementAudit)) == (
            1 if case == "conflicting_retry" else 0
        )


async def test_audit_failure_rolls_back_data(database):
    engine, _, _ = database
    actor, target_id = await seed(engine)
    original = await inspect_target(engine, actor, TargetKind.SOURCE, target_id)

    def reject_audit(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO source_management_audit"):
            raise RuntimeError("synthetic audit store failure")

    event.listen(engine.sync_engine, "before_cursor_execute", reject_audit)
    try:
        with pytest.raises(RuntimeError, match="synthetic audit"):
            await mutate(engine, actor, TargetKind.SOURCE, target_id, command(0, original.hash))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", reject_audit)
    assert await inspect_target(engine, actor, TargetKind.SOURCE, target_id) == original


async def test_two_actors_edit_delete_race_commits_one_result(database):
    engine, _, _ = database
    actor, target_id = await seed(engine)
    other, _ = await seed(engine)
    current = await inspect_target(engine, actor, TargetKind.SOURCE, target_id)
    edit = command(0, current.hash)
    deletion = ManagementCommand(**edit.model_dump(exclude={"changes"})).model_copy(update={"request_id": uuid4()})
    results = await asyncio.gather(
        mutate(engine, actor, TargetKind.SOURCE, target_id, edit),
        mutate(engine, other, TargetKind.SOURCE, target_id, deletion),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ApiError) for result in results) == 1
    async with async_sessionmaker(engine)() as session:
        assert await session.scalar(select(func.count()).select_from(SourceManagementAudit)) == 1


async def test_permission_grant_revoke_changes_existing_session(database):
    engine, _, _ = database
    actor, target_id = await seed(engine, allowed=False)
    with pytest.raises(ApiError):
        await inspect_target(engine, actor, TargetKind.SOURCE, target_id)
    grant = PermissionChange(
        user_id=actor.user_id, actor_id=actor.user_id, enabled=True, approval_hash="c" * 64, request_id=uuid4()
    )
    async with async_sessionmaker(engine).begin() as session:
        event_id = await set_permission(session, grant)
    async with async_sessionmaker(engine).begin() as session:
        assert await set_permission(session, grant) == event_id
    current = await inspect_target(engine, actor, TargetKind.SOURCE, target_id)
    async with async_sessionmaker(engine).begin() as session:
        await set_permission(session, grant.model_copy(update={"enabled": False, "request_id": uuid4()}))
    with pytest.raises(ApiError) as error:
        await mutate(engine, actor, TargetKind.SOURCE, target_id, command(0, current.hash))
    assert error.value.status_code == 403


async def seed_catalog(engine, source_id):
    async with async_sessionmaker(engine, expire_on_commit=False).begin() as session:
        endpoint = RagSourceEndpoint(
            source_id=source_id,
            endpoint_code="synthetic",
            display_name="endpoint",
            lifecycle_status="DRAFT",
            runtime_status="DISABLED",
            acquisition_status="PENDING",
        )
        session.add(endpoint)
        await session.flush()
        operation = RagSourceOperation(
            endpoint_id=endpoint.id,
            operation_code="synthetic",
            display_name="operation",
            runtime_status="DISABLED",
            acquisition_status="PENDING",
        )
        session.add(operation)
        await session.flush()
        snapshot = RagSourceSnapshot(
            operation_id=operation.id,
            source_version="synthetic-v1",
            raw_manifest_checksum="1" * 64,
            canonical_checksum="2" * 64,
            schema_version="v1",
            parser_version="v1",
            normalization_version="v1",
            canonicalization_spec_version="v1",
            endpoint_receipt_hash="3" * 64,
            record_count=1,
            rejected_record_count=0,
            verification_status="PENDING",
            collected_at=datetime.now(UTC),
        )
        session.add(snapshot)
        await session.flush()
        product = RagMedicationProduct(
            source_snapshot_id=snapshot.id,
            source_record_key="synthetic-key",
            code_system="synthetic",
            canonical_code="synthetic-code",
            product_name="합성 제품",
            normalized_product_name="합성 제품",
            product_status="ACTIVE",
        )
        session.add(product)
        await session.flush()
        return snapshot.id, product.id


@pytest.mark.parametrize("state", ["PENDING", "CURRENT", "missing_receipt"])
async def test_catalog_provenance_and_snapshot_state_gate(database, state):
    engine, _, _ = database
    actor, source_id = await seed(engine)
    snapshot_id, product_id = await seed_catalog(engine, source_id)
    async with async_sessionmaker(engine).begin() as session:
        snapshot = await session.get(RagSourceSnapshot, snapshot_id)
        if state == "missing_receipt":
            snapshot.endpoint_receipt_hash = None
        else:
            if state == "CURRENT":
                seal_id = uuid4()
                session.add(
                    RagSourceSnapshotVerification(
                        id=seal_id,
                        snapshot_id=snapshot.id,
                        check_name="snapshot-state-seal",
                        verification_result="NO_CHANGE",
                        verified_at=datetime.now(UTC),
                        verified_by="synthetic-fixture",
                    )
                )
                await session.flush()
                snapshot.verification_seal_id = seal_id
                snapshot.verified_at = datetime.now(UTC)
            snapshot.verification_status = state
    current = await inspect_target(engine, actor, TargetKind.PRODUCT, product_id)
    request = command(0, current.hash).model_copy(update={"changes": {"manufacturer_name": "합성 제조사"}})
    if state != "PENDING":
        with pytest.raises(ApiError):
            await mutate(engine, actor, TargetKind.PRODUCT, product_id, request)
    else:
        result = await mutate(engine, actor, TargetKind.PRODUCT, product_id, request)
        async with async_sessionmaker(engine)() as session:
            receipt = await session.get(SourceManagementAudit, result.event_id)
            assert receipt.before_provenance["receipt_hash"] == "3" * 64
            assert receipt.after_provenance["source_version"] == "synthetic-v1"
            assert receipt.before_provenance["snapshot_id"] == str(snapshot_id)


async def test_api_requires_real_auth_permission_and_rejects_payload_roles(database):
    engine, _, _ = database
    actor, target_id = await seed(engine, allowed=False)

    async def sessions():
        async with async_sessionmaker(engine).begin() as session:
            yield session

    management_app.dependency_overrides[get_db_session] = sessions
    # Use the actual signed access-token verifier, not a fake management actor dependency.
    async with async_sessionmaker(engine)() as session:
        user = await session.get(User, actor.user_id)
        token = str(JwtService().create_access_token(user))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=management_app), base_url="http://test"
        ) as client:
            path = f"/management/source/{target_id}"
            assert (await client.get(path)).status_code == 401
            headers = {"Authorization": f"Bearer {token}"}
            assert (await client.get(path, headers=headers)).status_code == 403
            forbidden = command(0, "0" * 64).model_dump(mode="json")
            assert (await client.patch(path, headers=headers, json=forbidden)).status_code == 403
            assert (
                await client.request(
                    "DELETE",
                    path,
                    headers=headers,
                    json={key: value for key, value in forbidden.items() if key != "changes"},
                )
            ).status_code == 403
            async with async_sessionmaker(engine).begin() as session:
                session.add(SourceManagementPermission(user_id=actor.user_id, enabled=True, approval_hash="a" * 64))
            response = await client.get(path, headers=headers)
            assert response.status_code == 200
            request = command(0, response.json()["hash"]).model_dump(mode="json")
            assert (await client.patch(path, headers=headers, json={**request, "role": "admin"})).status_code == 422
            assert (await client.patch(path, headers=headers, json=request)).status_code == 200
            async with async_sessionmaker(engine).begin() as session:
                permission = await session.get(SourceManagementPermission, actor.user_id)
                permission.enabled = False
            assert (await client.patch(path, headers=headers, json=request)).status_code == 403
    finally:
        management_app.dependency_overrides.clear()


async def test_real_management_role_cannot_self_grant_or_rewrite_audit(database):
    engine, url, (runtime, writer, manager) = database
    actor, target_id = await seed(engine)
    _, product_id = await seed_catalog(engine, target_id)
    async with engine.begin() as connection:
        for role in (runtime, writer, manager):
            await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'synthetic-management-only'"))
        await apply_management_role_policy(
            connection, owner=config.DB_USER, runtime=runtime, writer=writer, management=manager
        )
    managed = create_async_engine(url.set(username=manager, password="synthetic-management-only"), hide_parameters=True)
    try:
        async with managed.connect() as connection:
            await validate_management_connection(connection)
        current = await inspect_target(managed, actor, TargetKind.PRODUCT, product_id)
        await mutate(
            managed,
            actor,
            TargetKind.PRODUCT,
            product_id,
            command(0, current.hash).model_copy(update={"changes": {"manufacturer_name": "합성 수정"}}),
        )
        for sql in (
            "UPDATE source_management_permission SET enabled=true",
            "DELETE FROM source_management_audit",
            "UPDATE source_management_audit SET reason_code='CORRECTION'",
            'SELECT hashed_password FROM "user"',
            "UPDATE rag_source_snapshot SET canonical_checksum='x'",
            "INSERT INTO source_management_permission (user_id) VALUES ('x')",
        ):
            with pytest.raises(DBAPIError) as error:
                async with managed.begin() as connection:
                    await connection.execute(text(sql))
            assert error.value.orig.sqlstate == "42501"
    finally:
        await managed.dispose()


@pytest.mark.parametrize("database", ["migration"], indirect=True)
async def test_forward_migration_and_audit_preserving_downgrade(database):
    from scripts.ci.verify_database_head import read_database_head_state, validation_errors

    engine, url, _ = database
    root = Path(__file__).resolve().parents[3]
    environment = {**os.environ, "DB_NAME": url.database, "DB_PORT": str(url.port)}

    async def migrate(action, revision):
        return await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "-c", str(root / "backend/alembic.ini"), action, revision],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )

    assert (await migrate("upgrade", "3980718293a4")).returncode == 0
    async with engine.connect() as connection:
        assert validation_errors("3980718293a4", await read_database_head_state(connection)) == []
    assert (await migrate("downgrade", "398f60718293")).returncode == 0
    assert (await migrate("upgrade", "3980718293a4")).returncode == 0
    actor, target_id = await seed(engine)
    current = await inspect_target(engine, actor, TargetKind.SOURCE, target_id)
    async with async_sessionmaker(engine).begin() as session:
        await SourceManagementService(session).mutate(actor, TargetKind.SOURCE, target_id, command(0, current.hash))
        downgrade = asyncio.create_task(migrate("downgrade", "398f60718293"))
        waiting = False
        for _ in range(100):
            async with engine.connect() as connection:
                waiting = bool(
                    await connection.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname=current_database() "
                            "AND query LIKE 'LOCK TABLE source_management_audit%' AND wait_event_type='Lock')"
                        )
                    )
                )
            if waiting or downgrade.done():
                break
            await asyncio.sleep(0.05)
        assert waiting and not downgrade.done()
    assert (await downgrade).returncode != 0
    async with engine.connect() as connection:
        assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "3980718293a4"
        assert await connection.scalar(text("SELECT count(*) FROM source_management_audit")) == 1


async def test_permission_revocation_waits_for_inflight_change(database):
    engine, _, _ = database
    actor, target_id = await seed(engine)
    current = await inspect_target(engine, actor, TargetKind.SOURCE, target_id)
    revoke = PermissionChange(
        user_id=actor.user_id, actor_id=actor.user_id, enabled=False, approval_hash="d" * 64, request_id=uuid4()
    )
    started = asyncio.Event()

    async def revoke_permission():
        async with async_sessionmaker(engine).begin() as session:
            started.set()
            await set_permission(session, revoke)

    async with async_sessionmaker(engine).begin() as session:
        await SourceManagementService(session).authorize(actor, lock=True)
        task = asyncio.create_task(revoke_permission())
        await started.wait()
        result = await SourceManagementService(session).mutate(
            actor, TargetKind.SOURCE, target_id, command(0, current.hash)
        )
        assert not task.done()
    await asyncio.wait_for(task, timeout=5)
    with pytest.raises(ApiError) as error:
        await mutate(engine, actor, TargetKind.SOURCE, target_id, command(1, result.hash))
    assert error.value.status_code == 403


async def test_management_bootstrap_is_opt_in_and_redeploy_does_not_expand_rights(database):
    container = os.environ.get("ISSUE398_TEST_POSTGRES_CONTAINER")
    if not container:
        pytest.skip("Explicit disposable PostgreSQL container required")
    engine, url, (runtime, writer, manager) = database
    root = Path(__file__).resolve().parents[3]
    environment = {
        **os.environ,
        "SOURCE_MANAGEMENT_USER": manager,
        "SOURCE_MANAGEMENT_PASSWORD": "synthetic-management-only",
        "DB_MIGRATION_USER": "synthetic_owner",
        "DB_APP_USER": runtime,
        "SOURCE_WRITER_USER": writer,
    }
    arguments = ["docker", "exec", "-i"]
    for name in (
        "SOURCE_MANAGEMENT_USER",
        "SOURCE_MANAGEMENT_PASSWORD",
        "DB_MIGRATION_USER",
        "DB_APP_USER",
        "SOURCE_WRITER_USER",
    ):
        arguments.extend(["-e", name])
    arguments.extend([container, "psql", "-X", "-U", config.DB_USER, "-d", url.database])
    source = (root / "infra/docker/postgres/configure-management-role.sql").read_text()

    async def bootstrap(overrides=None):
        return await asyncio.to_thread(
            subprocess.run,
            arguments,
            input=source,
            env={**environment, **(overrides or {})},
            text=True,
            capture_output=True,
            timeout=30,
        )

    assert (await bootstrap({"SOURCE_MANAGEMENT_USER": config.DB_USER})).returncode != 0
    assert (await bootstrap()).returncode == 0
    async with engine.begin() as connection:
        for role in (runtime, writer):
            await connection.execute(text(f'CREATE ROLE "{role}" LOGIN'))
        assert not await connection.scalar(
            text("SELECT has_table_privilege(:role, 'rag_source', 'UPDATE')"), {"role": manager}
        )
        await apply_management_role_policy(
            connection, owner=config.DB_USER, runtime=runtime, writer=writer, management=manager
        )
    assert (await bootstrap()).returncode == 0
    managed = create_async_engine(url.set(username=manager, password="synthetic-management-only"), hide_parameters=True)
    try:
        async with managed.connect() as connection:
            await validate_management_connection(connection)
    finally:
        await managed.dispose()


@pytest.mark.parametrize("state", ["CURRENT", "STALE", "FAILED"])
async def test_verified_snapshot_cannot_be_deleted_by_direct_management_sql(database, state):
    from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
    from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotVerificationStatus
    from infra.python.source_role_policy import apply_source_role_policy

    engine, url, (runtime, writer, manager) = database
    actor, source_id = await seed(engine)
    snapshot_id, product_id = await seed_catalog(engine, source_id)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM rag_medication_product WHERE id=:id"), {"id": str(product_id)})
        for role in (runtime, writer, manager):
            await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'synthetic-seal-only'"))
        await apply_source_role_policy(
            connection, schema="public", owner=config.DB_USER, runtime=runtime, writer=writer
        )
        # Simulate the old provisioning grant and require the new policy to revoke it.
        await connection.execute(text(f'GRANT UPDATE (verified_at) ON rag_source_snapshot TO "{manager}"'))
        await apply_management_role_policy(
            connection, owner=config.DB_USER, runtime=runtime, writer=writer, management=manager
        )
    producer = create_async_engine(url.set(username=writer, password="synthetic-seal-only"), hide_parameters=True)
    managed = create_async_engine(url.set(username=manager, password="synthetic-seal-only"), hide_parameters=True)
    try:
        async with managed.connect() as connection:
            await validate_management_connection(connection)
        async with engine.begin() as connection:
            await connection.execute(text(f'GRANT UPDATE (verified_at) ON rag_source_snapshot TO "{manager}"'))
        with pytest.raises(ValueError, match="unexpected column update"):
            async with managed.connect() as connection:
                await validate_management_connection(connection)
        async with engine.begin() as connection:
            await apply_management_role_policy(
                connection, owner=config.DB_USER, runtime=runtime, writer=writer, management=manager
            )
        with pytest.raises(DBAPIError) as unsealed:
            async with producer.begin() as connection:
                await connection.execute(text("UPDATE rag_source_snapshot SET verification_status='CURRENT'"))
        assert unsealed.value.orig.sqlstate == "23514"
        async with async_sessionmaker(producer).begin() as session:
            repository = SqlAlchemySourceSnapshotRepository(session)
            failed = state == "FAILED"
            assert await repository.change_snapshot_status(
                snapshot_id=snapshot_id,
                expected_status=SnapshotVerificationStatus.PENDING,
                new_status=SnapshotVerificationStatus.FAILED if failed else SnapshotVerificationStatus.CURRENT,
                verified_at=datetime.now(UTC),
                effective_at=None if failed else datetime.now(UTC),
                selected_by="synthetic-reviewer",
            )
            if state == "STALE":
                assert await repository.change_snapshot_status(
                    snapshot_id=snapshot_id,
                    expected_status=SnapshotVerificationStatus.CURRENT,
                    new_status=SnapshotVerificationStatus.STALE,
                )
        # No Catalog/runtime reference exists and the Operation is disabled: the seal itself protects deletion.
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT runtime_status FROM rag_source_operation")) == "DISABLED"
        with pytest.raises(DBAPIError) as deletion:
            async with managed.begin() as connection:
                await connection.execute(text("DELETE FROM rag_source_snapshot WHERE id=:id"), {"id": str(snapshot_id)})
        assert deletion.value.orig.sqlstate == "23503"
        async with async_sessionmaker(engine).begin() as session:
            before_hash = row_hash(await session.get(RagSourceSnapshot, snapshot_id))
        async with managed.begin() as connection:
            await connection.execute(text("SELECT id FROM rag_source_snapshot FOR UPDATE"))
            await connection.execute(text("UPDATE rag_source_snapshot SET management_lock_marker=0"))
        with pytest.raises(DBAPIError) as marker_change:
            async with managed.begin() as connection:
                await connection.execute(text("UPDATE rag_source_snapshot SET management_lock_marker=1"))
        assert marker_change.value.orig.sqlstate == "23514"
        for sql in (
            "UPDATE rag_source_snapshot SET verified_at=now()",
            "UPDATE rag_source_snapshot SET verified_at=NULL",
            "UPDATE rag_source_snapshot SET effective_at=now()",
            "UPDATE rag_source_snapshot SET verification_seal_id=NULL",
            "DELETE FROM rag_source_snapshot_verification",
            "TRUNCATE rag_source_snapshot_verification CASCADE",
        ):
            with pytest.raises(DBAPIError) as denied:
                async with managed.begin() as connection:
                    await connection.execute(text(sql))
            assert denied.value.orig.sqlstate == "42501"
        async with async_sessionmaker(engine).begin() as session:
            assert row_hash(await session.get(RagSourceSnapshot, snapshot_id)) == before_hash
            assert await session.scalar(select(func.count()).select_from(SourceManagementAudit)) == 0
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT verification_status FROM rag_source_snapshot")) == state
    finally:
        await producer.dispose()
        await managed.dispose()


async def test_unverified_unreferenced_snapshot_still_allows_management_deletion(database):
    engine, url, (runtime, writer, manager) = database
    actor, source_id = await seed(engine)
    snapshot_id, product_id = await seed_catalog(engine, source_id)
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM rag_medication_product WHERE id=:id"), {"id": str(product_id)})
        for role in (runtime, writer, manager):
            await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'synthetic-seal-only'"))
        await apply_management_role_policy(
            connection, owner=config.DB_USER, runtime=runtime, writer=writer, management=manager
        )
    managed = create_async_engine(url.set(username=manager, password="synthetic-seal-only"), hide_parameters=True)
    try:
        before = await inspect_target(managed, actor, TargetKind.SNAPSHOT, snapshot_id)
        deletion = ManagementCommand(
            expected_revision=before.revision,
            expected_hash=before.hash,
            reason_code=ReasonCode.CORRECTION,
            approval_hash="b" * 64,
            request_id=uuid4(),
        )
        await mutate(managed, actor, TargetKind.SNAPSHOT, snapshot_id, deletion)
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM rag_source_snapshot")) == 0
            assert await connection.scalar(text("SELECT count(*) FROM source_management_audit")) == 1
    finally:
        await managed.dispose()


async def test_404_authentication_works_with_only_runtime_token_permissions(database, monkeypatch):
    from app.core.config import Env
    from app.dtos.auth import LoginRequest, SignUpRequest
    from app.repositories.password_reset_repository import PasswordResetRepository
    from app.repositories.refresh_session_repository import RefreshSessionRepository
    from app.repositories.user_repository import UserRepository
    from app.services.auth import AuthService
    from infra.python.provision_database_roles import provision_roles

    engine, url, (runtime, writer, manager) = database
    monkeypatch.setattr(config, "ENV", Env.LOCAL)
    async with engine.begin() as connection:
        for role in (runtime, writer, manager):
            await connection.execute(text(f"CREATE ROLE \"{role}\" LOGIN PASSWORD 'synthetic-auth-role-only'"))
        await provision_roles(connection, owner=config.DB_USER, runtime=runtime, writer=writer, management=manager)
    reader = create_async_engine(url.set(username=runtime, password="synthetic-auth-role-only"), hide_parameters=True)
    producer = create_async_engine(url.set(username=writer, password="synthetic-auth-role-only"), hide_parameters=True)
    managed = create_async_engine(url.set(username=manager, password="synthetic-auth-role-only"), hide_parameters=True)

    def service(session):
        return AuthService(UserRepository(session), PasswordResetRepository(session), RefreshSessionRepository(session))

    sessions = async_sessionmaker(reader, expire_on_commit=False)
    email = f"auth-{uuid4().hex[:12]}@example.com"
    try:
        async with sessions.begin() as session:
            await service(session).signup(SignUpRequest(email=email, password="Password123!", name="합성 인증 테스트"))
        async with sessions.begin() as session:
            auth = service(session)
            user = await auth.authenticate(LoginRequest(email=email, password="Password123!"))
            await auth.login(user, password="Password123!")
            row = (await session.execute(text("SELECT id,active_jti FROM refresh_session"))).one()
            assert await RefreshSessionRepository(session).rotate_jti(
                session_id=UUID(row.id), expected_jti=row.active_jti, new_jti="a" * 32
            )
            assert not await RefreshSessionRepository(session).rotate_jti(
                session_id=UUID(row.id), expected_jti=row.active_jti, new_jti="b" * 32
            )
        async with sessions() as session:
            token = await service(session).request_password_reset(email)
            assert token is not None
        async with sessions.begin() as session:
            await service(session).reset_password(token=token, new_password="UpdatedPass123!")
        async with sessions.begin() as session:
            auth = service(session)
            user = await auth.authenticate(LoginRequest(email=email, password="UpdatedPass123!"))
            await auth.login(user, password="UpdatedPass123!")
        async with sessions.begin() as session:
            with pytest.raises(ApiError) as reuse:
                await service(session).reset_password(token=token, new_password="AnotherPass123!")
            assert reuse.value.status_code == 422
        for limited, sql in (
            (reader, "UPDATE refresh_session SET user_id=user_id"),
            (reader, "UPDATE password_reset_token SET token_hash=token_hash"),
            (reader, "UPDATE password_reset_token SET expires_at=now()"),
            (reader, "DELETE FROM refresh_session"),
            (reader, "TRUNCATE password_reset_token"),
            (producer, "SELECT * FROM password_reset_token"),
            (producer, "UPDATE refresh_session SET active_jti=active_jti"),
            (managed, "SELECT * FROM refresh_session"),
        ):
            with pytest.raises(DBAPIError) as denied:
                async with limited.begin() as connection:
                    await connection.execute(text(sql))
            assert denied.value.orig.sqlstate == "42501"
    finally:
        await reader.dispose()
        await producer.dispose()
        await managed.dispose()


@pytest.mark.parametrize("database", ["migration"], indirect=True)
@pytest.mark.parametrize("previous", ["206a1b2c3d4e", "3980718293a4", "174a1b2c3d4e"])
async def test_merge_and_snapshot_seal_preserve_historical_rows(database, previous):
    from app.repositories.rag_source_catalog_repository import (
        RagSourceCatalogRepository,
        RagSourceCreate,
        RagSourceEndpointCreate,
        RagSourceOperationCreate,
    )
    from scripts.ci.verify_database_head import read_database_head_state, validation_errors

    engine, url, _ = database
    root = Path(__file__).resolve().parents[3]
    environment = {**os.environ, "DB_NAME": url.database, "DB_PORT": str(url.port)}

    async def migrate(action, revision):
        return await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "-c", str(root / "backend/alembic.ini"), action, revision],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )

    assert (await migrate("upgrade", previous)).returncode == 0
    assert (await migrate("upgrade", "39818293a4b5")).returncode == 0
    async with async_sessionmaker(engine).begin() as session:
        repository = RagSourceCatalogRepository(session)
        source = await repository.create_source(RagSourceCreate(source_code="seal-synthetic", display_name="합성 출처"))
        endpoint = await repository.create_endpoint(
            RagSourceEndpointCreate(source_id=source.id, endpoint_code="test", display_name="합성")
        )
        operation = await repository.create_operation(
            RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="test", display_name="합성")
        )
        current_id, pending_id = uuid4(), uuid4()
        for snapshot_id, state in ((current_id, "CURRENT"), (pending_id, "PENDING")):
            await session.execute(
                text(
                    "INSERT INTO rag_source_snapshot (id,operation_id,source_version,raw_manifest_checksum,canonical_checksum,"
                    "schema_version,parser_version,normalization_version,canonicalization_spec_version,"
                    "record_count,rejected_record_count,verification_status,collected_at) "
                    "VALUES (:id,:operation,:state,repeat('a',64),repeat('b',64),'1','1','1','1',0,0,:state,now())"
                ),
                {"id": str(snapshot_id), "operation": str(operation.id), "state": state},
            )
    from app.admin.source_management_service import fingerprint

    assert (await migrate("upgrade", "3983a4b5c6d7")).returncode == 0
    async with engine.connect() as connection:
        old_rows = (await connection.execute(text("SELECT * FROM rag_source_snapshot"))).mappings().all()
        old_hashes = {row["id"]: fingerprint(dict(row)) for row in old_rows}
    assert (await migrate("upgrade", "head")).returncode == 0
    async with async_sessionmaker(engine).begin() as session:
        for snapshot in (await session.scalars(select(RagSourceSnapshot))).all():
            assert snapshot.management_lock_marker == 0
            assert row_hash(snapshot) == old_hashes[str(snapshot.id)]
    async with engine.connect() as connection:
        assert validation_errors("3984b5c6d7e8", await read_database_head_state(connection)) == []
        rows = (
            (
                await connection.execute(
                    text(
                        "SELECT s.id,s.verification_status,s.canonical_checksum,s.verification_seal_id,v.snapshot_id,v.verification_result "
                        "FROM rag_source_snapshot s LEFT JOIN rag_source_snapshot_verification v ON v.id=s.verification_seal_id"
                    )
                )
            )
            .mappings()
            .all()
        )
        current = next(row for row in rows if row["id"] == str(current_id))
        pending = next(row for row in rows if row["id"] == str(pending_id))
        assert current["verification_status"] == "CURRENT" and current["canonical_checksum"] == "b" * 64
        assert current["snapshot_id"] == str(current_id) and current["verification_result"] == "NO_CHANGE"
        assert pending["verification_seal_id"] is None
    with pytest.raises(DBAPIError) as wrong_anchor:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE rag_source_snapshot SET verification_seal_id=:seal WHERE id=:id"),
                {"seal": current["verification_seal_id"], "id": str(pending_id)},
            )
    assert wrong_anchor.value.orig.sqlstate == "23503"
    rollback = await migrate("downgrade", "39818293a4b5")
    assert rollback.returncode != 0 and "cannot be downgraded" in rollback.stderr
