from __future__ import annotations

import asyncio
import importlib.util
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core import config
from app.models.guides import Guide, GuideGenerationStatus
from app.models.medical_documents import MedicalDocument
from app.models.ocr import OcrJob
from app.models.prescriptions import Prescription, PrescriptionVersion
from app.models.profiles import Profile, ProfileType
from app.models.users import Gender, User
from app.tests.fixtures.prescription_fingerprint import fingerprint_values
from rag_runtime.guide_release_projection import GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "backend/alembic/versions/2ba3431f4ef2_guide_release_projection_persistence.py"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("guide_release_projection_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config() -> Config:
    return Config(str(PROJECT_ROOT / "backend/alembic.ini"))


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
    database = f"guide_release_{uuid4().hex[:10]}"
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


async def _revision_and_release_column() -> tuple[str, bool]:
    async with _connection() as connection:
        revision = str(await connection.scalar(text("SELECT version_num FROM alembic_version")))
        column_exists = bool(
            await connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'guide'
                          AND column_name = 'release_projection_version'
                    )
                    """
                )
            )
        )
        return revision, column_exists


async def _seed_release_guide() -> None:
    engine = create_async_engine(config.database_url, poolclass=NullPool)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            user = User(
                email=f"migration-{uuid4().hex[:8]}@example.com",
                hashed_password="hashed-password",
                name="migration user",
                gender=Gender.MALE,
                birthday=date(1990, 1, 1),
                phone_number=f"010{uuid4().int % 100_000_000:08d}",
            )
            session.add(user)
            await session.flush()
            profile = Profile(user_id=user.id, profile_type=ProfileType.SELF, display_name=user.name)
            session.add(profile)
            await session.flush()
            document = MedicalDocument(
                uploaded_by=user.id,
                profile_id=profile.id,
                original_file_name="migration.jpg",
                object_key="migration.jpg",
                file_mime_type="image/jpeg",
                file_size_bytes=1,
            )
            session.add(document)
            await session.flush()
            ocr_job = OcrJob(document_id=document.id)
            session.add(ocr_job)
            await session.flush()
            version_id = uuid4()
            prescription = Prescription(
                active_version_id=version_id,
                document_id=document.id,
                source_ocr_job_id=ocr_job.id,
                profile_id=profile.id,
                prescribed_date=date.today(),
                confirmed_at=datetime.now(UTC),
            )
            session.add(prescription)
            await session.flush()
            version = PrescriptionVersion(
                **fingerprint_values(
                    prescription.prescribed_date,
                    [{"medication_name": "synthetic medication", "display_order": 1}],
                ),
                id=version_id,
                prescription_id=prescription.id,
                version_number=1,
                prescribed_date=prescription.prescribed_date,
                confirmed_at=prescription.confirmed_at,
            )
            session.add(version)
            await session.flush()
            session.add(
                Guide(
                    prescription_id=prescription.id,
                    prescription_version_id=version.id,
                    profile_id=profile.id,
                    generation_status=GuideGenerationStatus.COMPLETED,
                    content=None,
                    release_projection_version=GUIDE_RUNTIME_RELEASE_PROJECTION_CARRIER_VERSION,
                    release_decision="STALE",
                    release_is_current=False,
                    fallback_code="PRESCRIPTION_STALE",
                    fallback_text="synthetic fallback",
                    completed_at=datetime.now(UTC),
                )
            )
    finally:
        await engine.dispose()


def test_revision_parent_is_current_develop_head() -> None:
    migration = _load_migration()
    assert migration.revision == "2ba3431f4ef2"
    assert migration.down_revision == "869a1b2c3d4e"


def test_empty_downgrade_and_reupgrade_succeed(isolated_database: None) -> None:
    migration = _load_migration()
    cfg = _alembic_config()
    command.upgrade(cfg, migration.revision)
    assert asyncio.run(_revision_and_release_column()) == (migration.revision, True)

    command.downgrade(cfg, migration.down_revision)
    assert asyncio.run(_revision_and_release_column()) == (migration.down_revision, False)

    command.upgrade(cfg, migration.revision)
    assert asyncio.run(_revision_and_release_column()) == (migration.revision, True)


def test_populated_downgrade_preserves_release_data_and_revision(isolated_database: None) -> None:
    migration = _load_migration()
    cfg = _alembic_config()
    command.upgrade(cfg, migration.revision)
    asyncio.run(_seed_release_guide())

    with pytest.raises(RuntimeError, match="Guide release projection data exists"):
        command.downgrade(cfg, migration.down_revision)

    assert asyncio.run(_revision_and_release_column()) == (migration.revision, True)

    async def release_count() -> int:
        async with _connection() as connection:
            result = await connection.scalar(
                select(text("count(*)"))
                .select_from(text("guide"))
                .where(text("release_projection_version IS NOT NULL"))
            )
            return int(result or 0)

    assert asyncio.run(release_count()) == 1
