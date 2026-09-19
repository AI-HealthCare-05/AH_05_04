"""PostgreSQL round-trip coverage for #807 Source Use Approval authority."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Table, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from ai_worker.adapters.sqlalchemy_source_use_approval import SqlAlchemySourceUseApprovalReader
from app.core import config
from app.core.db.databases import Base
from app.models.rag_source import RagSourceSnapshot
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
    SourceUseApprovalRevoke,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import SourceUseApprovalIdentity, SourceUsePurpose

pytestmark = pytest.mark.asyncio

TEST_SCHEMA = "source_use_approval_807_test"
TEST_DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=config.DB_USER,
    password=config.DB_PASSWORD,
    host="127.0.0.1",
    port=config.DB_EXPOSE_PORT,
    database=config.DB_NAME,
)
test_engine = create_async_engine(
    TEST_DATABASE_URL,
    pool_pre_ping=True,
    poolclass=NullPool,
    connect_args={"server_settings": {"search_path": TEST_SCHEMA}},
)
session_factory = async_sessionmaker(test_engine, expire_on_commit=False, autoflush=False)

_NOW = datetime(2026, 9, 19, 0, 0, tzinfo=UTC)


def _hash(char: str) -> str:
    return char * 64


def _required_tables() -> list[Table]:
    seen: dict[str, Table] = {}
    stack = [Base.metadata.tables["rag_source_use_approval"]]
    while stack:
        table = stack.pop()
        if table.name in seen:
            continue
        seen[table.name] = table
        stack.extend(key.column.table for key in table.foreign_keys)
    return [seen[name] for name in sorted(seen)]


@pytest_asyncio.fixture(scope="module", autouse=True)
async def isolated_schema() -> AsyncIterator[None]:
    admin_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))
    async with test_engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(sync_connection, tables=_required_tables())
        )
    try:
        yield
    finally:
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await admin_engine.dispose()


async def _seed_snapshot(session: AsyncSession) -> tuple[RagSourceSnapshot, str, User, User]:
    suffix = uuid4().hex[:12]
    approver = User(email=f"807-ap-{suffix}@example.com", hashed_password="x" * 60, name="Approver")
    revoker = User(email=f"807-rv-{suffix}@example.com", hashed_password="x" * 60, name="Revoker")
    session.add_all([approver, revoker])
    await session.flush()

    catalog = RagSourceCatalogRepository(session)
    source = await catalog.create_source(
        RagSourceCreate(source_code=f"SOURCE_{suffix}", display_name="Synthetic Source")
    )
    endpoint = await catalog.create_endpoint(
        RagSourceEndpointCreate(source_id=source.id, endpoint_code="ENDPOINT", display_name="Endpoint")
    )
    operation = await catalog.create_operation(
        RagSourceOperationCreate(endpoint_id=endpoint.id, operation_code="OPERATION", display_name="Operation")
    )
    snapshot = await catalog.create_snapshot(
        RagSourceSnapshotCreate(
            operation_id=operation.id,
            source_version=f"version-{suffix}",
            raw_manifest_checksum=_hash("a"),
            canonical_checksum=_hash("b"),
            schema_version="schema-v1",
            parser_version="parser-v1",
            normalization_version="normalization-v1",
            canonicalization_spec_version="canonical-v1",
            record_count=1,
            rejected_record_count=0,
            collected_at=_NOW,
        )
    )
    return snapshot, source.source_code, approver, revoker


def _request(
    snapshot: RagSourceSnapshot,
    source_code: str,
    approver: User,
    *,
    purpose: SourceUsePurpose,
    environment: RuntimeEnvironmentCode = RuntimeEnvironmentCode.PRODUCTION,
):
    return SourceUseApprovalCreate(
        source_snapshot_id=snapshot.id,
        source_code=source_code,
        source_version=snapshot.source_version,
        environment=environment,
        purpose=purpose,
        approval_version="approval-1",
        valid_from=_NOW,
        expires_at=datetime(2026, 9, 20, tzinfo=UTC),
        actor_id=approver.id,
        evidence_ref="evidence://807/approval-1",
    )


async def test_postgresql_commit_then_exact_worker_read_preserves_approval_identity() -> None:
    async with session_factory.begin() as session:
        snapshot, source_code, approver, _ = await _seed_snapshot(session)
        request = _request(snapshot, source_code, approver, purpose=SourceUsePurpose.PATIENT_CITATION)
        created = await RagSourceUseApprovalRepository(session).create_approval(request)

    identity = request.identity()
    observation = await SqlAlchemySourceUseApprovalReader(session_factory).read_usable_exact(
        identity,
        evaluation_time=datetime(2026, 9, 19, 12, tzinfo=UTC),
    )

    assert observation is not None
    assert observation.id == created.id
    assert observation.identity == identity
    assert observation.is_usable_at(datetime(2026, 9, 19, 12, tzinfo=UTC))


async def test_postgresql_retrieval_only_approval_is_unavailable_for_patient_citation() -> None:
    async with session_factory.begin() as session:
        snapshot, source_code, approver, _ = await _seed_snapshot(session)
        retrieval = _request(snapshot, source_code, approver, purpose=SourceUsePurpose.RETRIEVAL)
        await RagSourceUseApprovalRepository(session).create_approval(retrieval)

    citation_identity = SourceUseApprovalIdentity(
        source_snapshot_id=retrieval.source_snapshot_id,
        source_code=retrieval.source_code,
        source_version=retrieval.source_version,
        environment=retrieval.environment,
        purpose=SourceUsePurpose.PATIENT_CITATION,
        approval_version=retrieval.approval_version,
    )
    assert await SqlAlchemySourceUseApprovalReader(session_factory).read_exact(citation_identity) is None


async def test_postgresql_environment_is_an_exact_approval_dimension() -> None:
    async with session_factory.begin() as session:
        snapshot, source_code, approver, _ = await _seed_snapshot(session)
        local_request = _request(
            snapshot,
            source_code,
            approver,
            purpose=SourceUsePurpose.PATIENT_CITATION,
            environment=RuntimeEnvironmentCode.LOCAL,
        )
        await RagSourceUseApprovalRepository(session).create_approval(local_request)

    production_identity = SourceUseApprovalIdentity(
        source_snapshot_id=local_request.source_snapshot_id,
        source_code=local_request.source_code,
        source_version=local_request.source_version,
        environment=RuntimeEnvironmentCode.PRODUCTION,
        purpose=local_request.purpose,
        approval_version=local_request.approval_version,
    )
    assert await SqlAlchemySourceUseApprovalReader(session_factory).read_exact(production_identity) is None


async def test_postgresql_revocation_keeps_historical_row_but_removes_usable_read() -> None:
    async with session_factory.begin() as session:
        snapshot, source_code, approver, revoker = await _seed_snapshot(session)
        request = _request(snapshot, source_code, approver, purpose=SourceUsePurpose.PATIENT_CITATION)
        created = await RagSourceUseApprovalRepository(session).create_approval(request)

    async with session_factory.begin() as session:
        await RagSourceUseApprovalRepository(session).revoke(
            SourceUseApprovalRevoke(
                approval_id=created.id,
                revoked_at=datetime(2026, 9, 19, 12, tzinfo=UTC),
                revoked_by=revoker.id,
                revoked_reason="policy change",
            )
        )

    reader = SqlAlchemySourceUseApprovalReader(session_factory)
    assert (
        await reader.read_usable_exact(request.identity(), evaluation_time=datetime(2026, 9, 19, 13, tzinfo=UTC))
        is None
    )
    historical = await reader.read_exact(request.identity())
    assert historical is not None
    assert historical.revoked_at == datetime(2026, 9, 19, 12, tzinfo=UTC)
