"""PostgreSQL downgrade safety for the #853 Runtime Bundle citation pin migration."""

from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from app.models.rag_runtime import (
    RagRuntimeBundleCitationApproval,
    RagRuntimeBundleStatus,
    RagRuntimeExecutionManifest,
    RagRuntimeReleaseBundle,
)
from app.models.users import User
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)
from app.repositories.rag_source_use_approval_repository import (
    RagSourceUseApprovalRepository,
    SourceUseApprovalCreate,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import SourceUsePurpose

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    PROJECT_ROOT / "backend" / "alembic" / "versions" / "853a1b2c3d4e_runtime_bundle_citation_approval_pin.py"
)


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("runtime_bundle_citation_pin_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend" / "alembic.ini"))


@asynccontextmanager
async def _connection() -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


@pytest.fixture
def isolated_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    database = f"runtime_bundle_citation853_{uuid4().hex[:10]}"
    cluster_url = config.database_url

    async def database_action(create: bool) -> None:
        engine = create_async_engine(cluster_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                sql = f'CREATE DATABASE "{database}"' if create else f'DROP DATABASE "{database}" WITH (FORCE)'
                await connection.execute(text(sql))
        finally:
            await engine.dispose()

    asyncio.run(database_action(True))
    monkeypatch.setattr(config, "DB_NAME", database)
    try:
        yield
    finally:
        asyncio.run(database_action(False))


async def _state() -> tuple[str, bool, int]:
    async with _connection() as connection:
        revision = str(await connection.scalar(text("SELECT version_num FROM alembic_version")))
        exists = await connection.scalar(text("SELECT to_regclass('public.rag_runtime_bundle_citation_approval')"))
        count = (
            0
            if exists is None
            else int(await connection.scalar(text("SELECT count(*) FROM rag_runtime_bundle_citation_approval")))
        )
        return revision, exists is not None, count


async def _seed_pin() -> None:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 20, tzinfo=UTC)
    try:
        async with factory.begin() as session:
            actor = User(email=f"853-{uuid4().hex[:10]}@example.com", hashed_password="x" * 60, name="Approver")
            session.add(actor)
            await session.flush()
            source_repository = RagSourceCatalogRepository(session)
            source = await source_repository.create_source(
                RagSourceCreate(source_code=f"SOURCE_853_{uuid4().hex[:8]}", display_name="Synthetic Source")
            )
            endpoint = await source_repository.create_endpoint(
                RagSourceEndpointCreate(source_id=source.id, endpoint_code="ENDPOINT", display_name="Endpoint")
            )
            operation = await source_repository.create_operation(
                RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="OPERATION", display_name="Operation")
            )
            snapshot = await source_repository.create_snapshot(
                RagSourceSnapshotCreate(
                    operation_id=operation.id,
                    source_version="version-853",
                    raw_manifest_checksum="a" * 64,
                    canonical_checksum="b" * 64,
                    schema_version="schema-v1",
                    parser_version="parser-v1",
                    normalization_version="normalization-v1",
                    canonicalization_spec_version="canonical-v1",
                    record_count=1,
                    rejected_record_count=0,
                    collected_at=now,
                )
            )
            approval = await RagSourceUseApprovalRepository(session).create_approval(
                SourceUseApprovalCreate(
                    source_snapshot_id=snapshot.id,
                    source_code=source.source_code,
                    source_version=snapshot.source_version,
                    environment=RuntimeEnvironmentCode.LOCAL,
                    purpose=SourceUsePurpose.PATIENT_CITATION,
                    approval_version="approval-853",
                    valid_from=now,
                    expires_at=now + timedelta(days=1),
                    actor_id=actor.id,
                    evidence_ref="evidence://853/migration",
                )
            )
            manifest = RagRuntimeExecutionManifest(
                manifest_key="runtime-853",
                manifest_version="1",
                manifest_hash="c" * 64,
                schema_version="1",
                git_commit_sha="abcdef1",
            )
            session.add(manifest)
            await session.flush()
            bundle = RagRuntimeReleaseBundle(
                bundle_key="bundle-853",
                bundle_version="1",
                bundle_status=RagRuntimeBundleStatus.BUILDING,
                execution_manifest_id=manifest.id,
                bundle_manifest_hash="d" * 64,
                environment_code="LOCAL",
                catalog_version="catalog-1",
                catalog_manifest_hash="e" * 64,
            )
            session.add(bundle)
            await session.flush()
            session.add(
                RagRuntimeBundleCitationApproval(
                    bundle_id=bundle.id,
                    bundle_manifest_hash=bundle.bundle_manifest_hash,
                    source_snapshot_id=snapshot.id,
                    source_use_approval_id=approval.id,
                    source_code=source.source_code,
                    source_version=snapshot.source_version,
                    approval_version=approval.identity.approval_version,
                    environment="LOCAL",
                    purpose="PATIENT_CITATION",
                )
            )
    finally:
        await engine.dispose()


def test_revision_parent_is_actual_develop_head() -> None:
    migration = _load_migration()
    assert migration.revision == "853a1b2c3d4e"
    assert migration.down_revision == "807a1b2c3d4e"


def test_populated_downgrade_is_rejected_without_losing_table_row_or_revision(isolated_database: None) -> None:
    migration = _load_migration()
    cfg = _alembic_config()
    command.upgrade(cfg, migration.revision)
    asyncio.run(_seed_pin())

    with pytest.raises(RuntimeError, match="refusing destructive downgrade"):
        command.downgrade(cfg, migration.down_revision)

    assert asyncio.run(_state()) == (migration.revision, True, 1)


def test_empty_downgrade_and_reupgrade_succeed(isolated_database: None) -> None:
    migration = _load_migration()
    cfg = _alembic_config()
    command.upgrade(cfg, migration.revision)
    assert asyncio.run(_state()) == (migration.revision, True, 0)

    command.downgrade(cfg, migration.down_revision)
    assert asyncio.run(_state()) == (migration.down_revision, False, 0)

    command.upgrade(cfg, migration.revision)
    assert asyncio.run(_state()) == (migration.revision, True, 0)
