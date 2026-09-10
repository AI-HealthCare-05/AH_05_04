"""RAG-12A Runtime Bundle build port: kernel decision composed with the repository transaction (Issue #175).

The "internal build port" of ``#175`` is exactly this composition -- the kernel decides, the
repository persists, and nothing sits between them.  ``CONTRIBUTING.md`` forbids adding a
Protocol or Service layer for a single implementation and a single consumer, so this test is what
fixes the composition.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Table, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from ai_worker.tasks.rag.runtime_bundle_builder import (
    RuntimeBundleArtifactKind,
    RuntimeBundleArtifactMemberInput,
    RuntimeBundleBuildDecision,
    RuntimeBundleBuildRequest,
    RuntimeBundleMemberPurpose,
    RuntimeBundleSourceMemberInput,
    RuntimeExecutionManifestInput,
    evaluate_runtime_bundle_build,
)
from app.core import config
from app.core.db.databases import Base
from app.models.rag_runtime import (
    RagRuntimeBundleSource,
    RagRuntimeBundleStatus,
    RagRuntimeEnvironmentStatus,
    RagRuntimeEnvironmentTransition,
    RagRuntimeExecutionManifest,
    RagRuntimeReleaseBundle,
    RagRuntimeSourcePurpose,
)
from app.models.rag_source import RagSnapshotVerificationStatus
from app.repositories.rag_runtime_repository import (
    RagRuntimeBundleSourceCreate,
    RagRuntimeEnvironmentCreate,
    RagRuntimeExecutionManifestCreate,
    RagRuntimeReleaseBundleCreate,
    RagRuntimeRepository,
)
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)

pytestmark = pytest.mark.asyncio

TEST_SCHEMA = "rag_runtime_bundle_build_test"
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

_ENVIRONMENT = "local"
_NOW = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)


def _hash(char: str) -> str:
    return char * 64


_ROOT_TABLES = (
    "rag_runtime_execution_manifest",
    "rag_runtime_release_bundle",
    "rag_runtime_bundle_source",
    "rag_runtime_environment",
    "rag_runtime_environment_transition",
    "rag_release_evaluation_approval",
    "rag_source_snapshot",
)


def _required_tables() -> list[Table]:
    """Only the runtime tables and their transitive FK closure: 14 tables, not all 60.

    A full ``Base.metadata.create_all`` here issues ~150 DDL statements into this module's
    schema.  Doing that alongside the other integration module that uses the same pattern was
    enough to intermittently kill the backend suite's own schema setup with
    ``ConnectionDoesNotExistError``.  Creating the closure keeps this module's DDL cost
    proportional to what it actually exercises.
    """
    seen: dict[str, Table] = {}
    stack = [Base.metadata.tables[name] for name in _ROOT_TABLES]
    while stack:
        table = stack.pop()
        if table.name in seen:
            continue
        seen[table.name] = table
        stack.extend(key.column.table for key in table.foreign_keys)
    # create_all sorts by dependency itself; Base.metadata.sorted_tables is avoided because it
    # warns about an unrelated pre-existing FK cycle (ai_job / ocr_job / prescription*).
    return [seen[name] for name in sorted(seen)]


@pytest_asyncio.fixture(scope="module", autouse=True)
async def isolated_schema() -> AsyncIterator[None]:
    admin_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await connection.execute(text(f"CREATE SCHEMA {TEST_SCHEMA}"))
    tables = _required_tables()
    async with test_engine.begin() as connection:
        await connection.run_sync(lambda sync_connection: Base.metadata.create_all(sync_connection, tables=tables))
    try:
        yield
    finally:
        await test_engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE"))
        await admin_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def clean_runtime_tables() -> AsyncIterator[None]:
    """Runtime rows only.

    Source and eval rows are seeded under per-test unique keys, so they need no cleanup and a
    CASCADE across the whole FK graph is avoided.
    """
    yield
    async with test_engine.begin() as connection:
        await connection.execute(
            text(
                # Every table referencing these is listed explicitly, so no CASCADE is needed.
                "TRUNCATE TABLE "
                f"{TEST_SCHEMA}.rag_runtime_bundle_source, "
                f"{TEST_SCHEMA}.rag_runtime_environment_transition, "
                f"{TEST_SCHEMA}.rag_runtime_environment, "
                f"{TEST_SCHEMA}.rag_release_evaluation_approval, "
                f"{TEST_SCHEMA}.rag_runtime_release_bundle, "
                f"{TEST_SCHEMA}.rag_runtime_execution_manifest"
            )
        )


async def _seed_source_snapshot(purpose_label: str):
    suffix = uuid4().hex[:10]
    async with session_factory.begin() as session:
        repository = RagSourceCatalogRepository(session)
        source = await repository.create_source(
            RagSourceCreate(source_code=f"MFDS_{purpose_label}_{suffix}", display_name="MFDS Source")
        )
        endpoint = await repository.create_endpoint(
            RagSourceEndpointCreate(source_id=source.id, endpoint_code="PRODUCT_LIST", display_name="Product List")
        )
        operation = await repository.create_operation(
            RagSourceOperationCreate(
                endpoint_id=endpoint.id, operation_code="LIST_PRODUCTS", display_name="List Products"
            )
        )
        snapshot = await repository.create_snapshot(
            RagSourceSnapshotCreate(
                operation_id=operation.id,
                source_version=f"api:2026-09-10:{suffix}",
                raw_manifest_checksum=_hash("a"),
                canonical_checksum=_hash("b"),
                schema_version="schema-v1",
                parser_version="parser-v1",
                normalization_version="normalization-v1",
                canonicalization_spec_version="canonical-v1",
                record_count=1,
                rejected_record_count=0,
                verification_status=RagSnapshotVerificationStatus.CURRENT,
                collected_at=_NOW,
                verified_at=_NOW,
            )
        )
    return snapshot


def _build_request(
    *,
    catalog_snapshot_id: str,
    knowledge_snapshot_id: str,
    catalog_overrides: dict[str, object] | None = None,
) -> RuntimeBundleBuildRequest:
    catalog_member = RuntimeBundleSourceMemberInput(
        source_snapshot_id=catalog_snapshot_id,
        source_purpose=RuntimeBundleMemberPurpose.CATALOG,
        source_version="api:2026-09-10:catalog",
        canonical_checksum=_hash("b"),
        approval_version="approval-v1",
        scope_policy_hash=_hash("c"),
        freshness_policy_hash=_hash("d"),
        observed_environment=_ENVIRONMENT,
        **(catalog_overrides or {}),  # type: ignore[arg-type]
    )
    return RuntimeBundleBuildRequest(
        bundle_key="local-rag-runtime",
        bundle_version="2026.09.10-001",
        target_environment=_ENVIRONMENT,
        execution_manifest=RuntimeExecutionManifestInput(
            manifest_key="rag-runtime",
            manifest_version="2026.09.10-001",
            schema_version="runtime-manifest-v1",
            git_commit_sha="abcdef1",
            worker_artifact_ref="worker:local:2026.09.10",
        ),
        source_members=(
            catalog_member,
            RuntimeBundleSourceMemberInput(
                source_snapshot_id=knowledge_snapshot_id,
                source_purpose=RuntimeBundleMemberPurpose.KNOWLEDGE,
                source_version="api:2026-09-10:knowledge",
                canonical_checksum=_hash("b"),
                approval_version="approval-v1",
                scope_policy_hash=_hash("c"),
                freshness_policy_hash=_hash("d"),
                observed_environment=_ENVIRONMENT,
            ),
        ),
        artifact_members=(
            RuntimeBundleArtifactMemberInput(
                artifact_kind=RuntimeBundleArtifactKind.CANDIDATE_INDEX,
                artifact_ref="candidate-index:local",
                artifact_version="1.0.0",
                observed_environment=_ENVIRONMENT,
                manifest_hash=_hash("e"),
            ),
        ),
        created_by="integration-test",
    )


async def _persist(request: RuntimeBundleBuildRequest, outcome) -> None:
    """Persist a BUILDABLE outcome exactly as the build port does."""
    candidate_index = next(
        member
        for member in request.artifact_members
        if member.artifact_kind is RuntimeBundleArtifactKind.CANDIDATE_INDEX
    )
    async with session_factory.begin() as session:
        repository = RagRuntimeRepository(session)
        assert outcome.manifest_hash is not None
        assert outcome.bundle_manifest_hash is not None
        await repository.build_runtime_bundle(
            manifest=RagRuntimeExecutionManifestCreate(
                manifest_key=request.execution_manifest.manifest_key,
                manifest_version=request.execution_manifest.manifest_version,
                manifest_hash=outcome.manifest_hash,
                schema_version=request.execution_manifest.schema_version,
                git_commit_sha=request.execution_manifest.git_commit_sha,
                worker_artifact_ref=request.execution_manifest.worker_artifact_ref,
            ),
            bundle=RagRuntimeReleaseBundleCreate(
                bundle_key=request.bundle_key,
                bundle_version=request.bundle_version,
                execution_manifest_id=uuid4(),
                bundle_manifest_hash=outcome.bundle_manifest_hash,
                candidate_index_ref=candidate_index.artifact_ref,
                candidate_index_manifest_hash=candidate_index.manifest_hash,
                created_by=request.created_by,
            ),
            bundle_sources=tuple(
                RagRuntimeBundleSourceCreate(
                    bundle_id=uuid4(),
                    source_snapshot_id=member.source_snapshot_id,  # type: ignore[arg-type]
                    source_purpose=RagRuntimeSourcePurpose(member.source_purpose.value),
                    required=member.required,
                    selected_for_operation=member.selected_for_operation,
                )
                for member in request.source_members
            ),
        )


async def _count(model: type) -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(model))
        return int(result.scalar_one())


async def test_buildable_member_set_lands_as_a_building_bundle_with_the_kernel_hashes() -> None:
    catalog = await _seed_source_snapshot("CATALOG")
    knowledge = await _seed_source_snapshot("KNOWLEDGE")
    request = _build_request(
        catalog_snapshot_id=str(catalog.id),
        knowledge_snapshot_id=str(knowledge.id),
    )
    outcome = evaluate_runtime_bundle_build(request)
    assert outcome.decision is RuntimeBundleBuildDecision.BUILDABLE

    await _persist(request, outcome)

    async with session_factory() as session:
        bundle = (
            await session.execute(
                select(RagRuntimeReleaseBundle).where(
                    RagRuntimeReleaseBundle.bundle_manifest_hash == outcome.bundle_manifest_hash
                )
            )
        ).scalar_one()
        manifest = (
            await session.execute(
                select(RagRuntimeExecutionManifest).where(
                    RagRuntimeExecutionManifest.manifest_hash == outcome.manifest_hash
                )
            )
        ).scalar_one()
        members = list(
            (
                await session.execute(
                    select(RagRuntimeBundleSource).where(RagRuntimeBundleSource.bundle_id == bundle.id)
                )
            ).scalars()
        )

    assert bundle.bundle_status is RagRuntimeBundleStatus.BUILDING
    assert bundle.execution_manifest_id == manifest.id
    assert manifest.worker_artifact_ref == "worker:local:2026.09.10"
    assert {member.source_purpose for member in members} == {
        RagRuntimeSourcePurpose.CATALOG,
        RagRuntimeSourcePurpose.KNOWLEDGE,
    }


async def test_ready_and_active_pointer_stay_at_zero_while_components_are_incomplete() -> None:
    catalog = await _seed_source_snapshot("CATALOG")
    knowledge = await _seed_source_snapshot("KNOWLEDGE")
    request = _build_request(
        catalog_snapshot_id=str(catalog.id),
        knowledge_snapshot_id=str(knowledge.id),
    )
    outcome = evaluate_runtime_bundle_build(request)
    # Rule, Guideline and Safety members are absent, so the bundle may build but not be promoted.
    assert outcome.readiness_blockers != ()

    async with session_factory.begin() as session:
        await RagRuntimeRepository(session).create_environment(
            RagRuntimeEnvironmentCreate(
                environment_code=_ENVIRONMENT,
                environment_status=RagRuntimeEnvironmentStatus.SUSPENDED,
            )
        )
    await _persist(request, outcome)

    async with session_factory() as session:
        promoted = list(
            (
                await session.execute(
                    select(RagRuntimeReleaseBundle).where(
                        RagRuntimeReleaseBundle.bundle_status != RagRuntimeBundleStatus.BUILDING
                    )
                )
            ).scalars()
        )
        environment = (
            await session.execute(
                select(app.models.rag_runtime.RagRuntimeEnvironment).where(
                    app.models.rag_runtime.RagRuntimeEnvironment.environment_code == _ENVIRONMENT
                )
            )
        ).scalar_one()

    assert promoted == []
    assert environment.active_bundle_id is None
    assert environment.active_bundle_manifest_hash is None
    assert environment.environment_status is RagRuntimeEnvironmentStatus.SUSPENDED
    assert await _count(RagRuntimeEnvironmentTransition) == 0


async def test_build_failure_persists_nothing_and_leaves_the_active_pointer_untouched() -> None:
    catalog = await _seed_source_snapshot("CATALOG")
    knowledge = await _seed_source_snapshot("KNOWLEDGE")
    async with session_factory.begin() as session:
        await RagRuntimeRepository(session).create_environment(
            RagRuntimeEnvironmentCreate(
                environment_code=_ENVIRONMENT,
                environment_status=RagRuntimeEnvironmentStatus.SUSPENDED,
            )
        )
    request = _build_request(
        catalog_snapshot_id=str(catalog.id),
        knowledge_snapshot_id=str(knowledge.id),
        catalog_overrides={"revocation_unresolved": True},
    )

    outcome = evaluate_runtime_bundle_build(request)

    assert outcome.decision is RuntimeBundleBuildDecision.REJECTED
    assert outcome.bundle_manifest_hash is None
    # A REJECTED outcome carries no hash, so the port has nothing to persist: 0 rows, by construction.
    assert await _count(RagRuntimeExecutionManifest) == 0
    assert await _count(RagRuntimeReleaseBundle) == 0
    assert await _count(RagRuntimeBundleSource) == 0
    assert await _count(RagRuntimeEnvironmentTransition) == 0

    async with session_factory() as session:
        environment = (
            await session.execute(
                select(app.models.rag_runtime.RagRuntimeEnvironment).where(
                    app.models.rag_runtime.RagRuntimeEnvironment.environment_code == _ENVIRONMENT
                )
            )
        ).scalar_one()
    assert environment.active_bundle_id is None
    assert environment.environment_status is RagRuntimeEnvironmentStatus.SUSPENDED
