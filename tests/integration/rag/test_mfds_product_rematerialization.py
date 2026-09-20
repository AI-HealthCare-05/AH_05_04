"""Restricted Source Writer PostgreSQL coverage for MFDS product rematerialization."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from ai_worker.admin.mfds_product_rematerialization import (
    BLOCKED_BY_PRODUCT_ARTIFACT,
    MfdsProductRematerializationConfig,
    ProductRematerializationBlockedError,
    run_rematerialization,
)
from ai_worker.admin.source_writer import WriterConfig
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotIngestionDecision
from ai_worker.tests.rag.source_ingestion.test_mfds_product import product
from ai_worker.tests.rag.source_ingestion.test_mfds_product_rematerialization import _manifest
from app.core import config
from app.core.db.databases import Base
from app.models.rag_source import (
    RagSource,
    RagSourceEndpoint,
    RagSourceIngestionArtifact,
    RagSourceIngestionRun,
    RagSourceOperation,
    RagSourceSnapshot,
)
from app.tests.db_extensions import ensure_vector_extension
from infra.python.source_role_policy import apply_source_role_policy

pytestmark = pytest.mark.asyncio
_ARTIFACT_COUNT = 430


@dataclass(frozen=True)
class _Database:
    admin_sessions: async_sessionmaker
    rematerialization: MfdsProductRematerializationConfig


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[_Database]:
    suffix = uuid4().hex[:12]
    schema = f"product800_schema_{suffix}"
    runtime = f"product800_runtime_{suffix}"
    writer = f"product800_writer_{suffix}"
    password = "synthetic-product800-test-only"
    admin_url = URL.create(
        "postgresql+asyncpg",
        username=config.DB_USER,
        password=config.DB_PASSWORD,
        host=config.DB_HOST,
        port=config.DB_EXPOSE_PORT,
        database=config.DB_NAME,
    )
    admin = create_async_engine(admin_url, poolclass=NullPool)
    schema_engine = create_async_engine(
        admin_url,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": f"{schema},test_extensions"}},
        execution_options={"schema_translate_map": {None: schema}},
    )
    writer_url = admin_url.set(username=writer, password=password)
    try:
        async with admin.begin() as connection:
            await connection.execute(text("CREATE SCHEMA IF NOT EXISTS test_extensions"))
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA test_extensions"))
            await connection.execute(text("ALTER EXTENSION pg_trgm SET SCHEMA test_extensions"))
            await ensure_vector_extension(connection, "test_extensions")
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            for role in (runtime, writer):
                await connection.execute(text(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\''))
        async with schema_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with admin.begin() as connection:
            await apply_source_role_policy(
                connection,
                schema=schema,
                owner=config.DB_USER,
                runtime=runtime,
                writer=writer,
            )
            await connection.execute(
                text(
                    f'ALTER ROLE "{writer}" IN DATABASE "{config.DB_NAME}" '
                    f'SET search_path = "{schema}", test_extensions'
                )
            )
        yield _Database(
            async_sessionmaker(schema_engine, expire_on_commit=False),
            MfdsProductRematerializationConfig(
                WriterConfig(writer_url, "issue-800-integration"),
                tmp_path / "artifacts",
            ),
        )
    finally:
        artifact_root = tmp_path / "artifacts"
        if artifact_root.exists():
            artifact_root.chmod(0o700)
            for path in artifact_root.rglob("*"):
                path.chmod(0o700 if path.is_dir() else 0o600)
        await schema_engine.dispose()
        await admin.dispose()
        cleanup = create_async_engine(admin_url, poolclass=NullPool)
        async with cleanup.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            for role in (runtime, writer):
                if await connection.scalar(text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}):
                    await connection.execute(text(f'DROP OWNED BY "{role}"'))
                    await connection.execute(text(f'DROP ROLE "{role}"'))
        await cleanup.dispose()


def _actual_sized_evidence(tmp_path: Path):
    rows = [product(f"P-{number:06d}") for number in range(1, _ARTIFACT_COUNT + 1)]
    manifest, receipt, _, payload = _manifest(tmp_path, rows=rows)
    return manifest, receipt, payload


def _make_artifacts_read_only(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            path.chmod(0o400)
    for path in sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        path.chmod(0o500)
    root.chmod(0o500)


async def test_restricted_writer_command_commits_430_and_reruns_no_change(database, tmp_path):
    manifest, receipt, _ = _actual_sized_evidence(tmp_path)
    _make_artifacts_read_only(database.rematerialization.artifact_reader_root)

    first = await run_rematerialization(
        database.rematerialization,
        manifest_path=manifest,
        receipt_path=receipt,
        repository_root=tmp_path,
    )
    second = await run_rematerialization(
        database.rematerialization,
        manifest_path=manifest,
        receipt_path=receipt,
        repository_root=tmp_path,
    )

    assert first.persistence.decision is SnapshotIngestionDecision.CREATED
    assert second.persistence.decision is SnapshotIngestionDecision.NO_CHANGE
    assert second.persistence.snapshot_id == first.persistence.snapshot_id
    assert isinstance(first.persistence.snapshot_id, UUID)
    assert first.persistence.snapshot_id != first.persistence.ingestion_run_id
    assert first.artifact_count == _ARTIFACT_COUNT

    async with database.admin_sessions() as session:
        snapshot = await session.get(RagSourceSnapshot, first.persistence.snapshot_id)
        assert snapshot.verification_status == "PENDING"
        assert snapshot.record_count == _ARTIFACT_COUNT
        assert await session.scalar(select(func.count()).select_from(RagSource)) == 1
        assert await session.scalar(select(func.count()).select_from(RagSourceEndpoint)) == 1
        assert await session.scalar(select(func.count()).select_from(RagSourceOperation)) == 1
        runs = (await session.scalars(select(RagSourceIngestionRun).order_by(RagSourceIngestionRun.created_at))).all()
        assert [run.run_status for run in runs] == ["SUCCEEDED", "NO_CHANGE"]
        assert all(run.failure_code is None for run in runs)
        for run in runs:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RagSourceIngestionArtifact)
                    .where(
                        RagSourceIngestionArtifact.ingestion_run_id == run.id,
                        RagSourceIngestionArtifact.artifact_kind == "RAW_RESPONSE",
                    )
                )
                == _ARTIFACT_COUNT
            )


async def test_artifact_failure_rolls_back_new_hierarchy(database, tmp_path):
    manifest, receipt, payload = _actual_sized_evidence(tmp_path)
    payload["record_count"] += 1
    manifest.write_text(json.dumps(payload))
    _make_artifacts_read_only(database.rematerialization.artifact_reader_root)

    with pytest.raises(ProductRematerializationBlockedError) as caught:
        await run_rematerialization(
            database.rematerialization,
            manifest_path=manifest,
            receipt_path=receipt,
            repository_root=tmp_path,
        )
    assert caught.value.code == BLOCKED_BY_PRODUCT_ARTIFACT

    async with database.admin_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(RagSource)) == 0
        assert await session.scalar(select(func.count()).select_from(RagSourceEndpoint)) == 0
        assert await session.scalar(select(func.count()).select_from(RagSourceOperation)) == 0
        assert await session.scalar(select(func.count()).select_from(RagSourceSnapshot)) == 0
