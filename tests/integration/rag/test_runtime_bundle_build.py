"""RAG-12A Runtime Bundle build port over real persistence (Issue #175).

These tests drive ``execute_runtime_bundle_build`` -- the production execution boundary -- rather
than reassembling the kernel and the repository inside the test.  The round-trip tests are the
ones that make ``bundle_manifest_hash`` a verifiable identity: the hash is recomputed from stored
rows and compared with the stored value.
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import Table, func, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401
from ai_worker.tasks.rag.catalog.types import CatalogFreshnessStatus, CatalogVerificationStatus
from ai_worker.tasks.rag.runtime_bundle_builder import (
    MedicationCatalogBinding,
    RuntimeBundleArtifactKind,
    RuntimeBundleArtifactMemberInput,
    RuntimeBundleBuildDecision,
    RuntimeBundleBuildRequest,
    RuntimeBundleMemberPurpose,
    RuntimeBundleRejectionReason,
    RuntimeBundleSourceMemberInput,
    RuntimeExecutionManifestInput,
    canonical_runtime_bundle_manifest_hash,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotVerificationStatus
from app.core import config
from app.core.db.databases import Base
from app.models.rag_runtime import (
    RagRuntimeBundleSource,
    RagRuntimeBundleStatus,
    RagRuntimeEnvironment,
    RagRuntimeEnvironmentStatus,
    RagRuntimeEnvironmentTransition,
    RagRuntimeExecutionManifest,
    RagRuntimeReleaseBundle,
    RagRuntimeSourcePurpose,
)
from app.repositories.rag_runtime_repository import (
    RagRuntimeBundleSourceVersionMismatchError,
    RagRuntimeEnvironmentCreate,
    RagRuntimeRepository,
)
from app.repositories.rag_source_catalog_repository import (
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)
from app.services.rag_runtime_bundle_build import (
    execute_runtime_bundle_build,
    load_persisted_bundle_configuration,
    verify_persisted_bundle_manifest_hash,
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
_CATALOG_VERSION = "catalog-1.0.0"
_NOW = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)

_ROOT_TABLES = (
    "rag_runtime_execution_manifest",
    "rag_runtime_release_bundle",
    "rag_runtime_bundle_source",
    "rag_runtime_environment",
    "rag_runtime_environment_transition",
    "rag_release_evaluation_approval",
    "rag_source_snapshot",
)


def _hash(char: str) -> str:
    return char * 64


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


async def _seed_source_snapshot(label: str):
    suffix = uuid4().hex[:10]
    async with session_factory.begin() as session:
        repository = RagSourceCatalogRepository(session)
        source = await repository.create_source(
            RagSourceCreate(source_code=f"MFDS_{label}_{suffix}", display_name="MFDS Source")
        )
        endpoint = await repository.create_endpoint(
            RagSourceEndpointCreate(source_id=source.id, endpoint_code="PRODUCT_LIST", display_name="Product List")
        )
        operation = await repository.create_operation(
            RagSourceOperationCreate(
                endpoint_id=endpoint.id, operation_code="LIST_PRODUCTS", display_name="List Products"
            )
        )
        return await repository.create_snapshot(
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
                collected_at=_NOW,
            )
        )


def _source_member(snapshot, purpose: RuntimeBundleMemberPurpose) -> RuntimeBundleSourceMemberInput:
    return RuntimeBundleSourceMemberInput(
        source_snapshot_id=str(snapshot.id),
        source_purpose=purpose,
        source_version=snapshot.source_version,
        canonical_checksum=snapshot.canonical_checksum,
        approval_version="approval-v1",
        scope_policy_hash=_hash("c"),
        freshness_policy_hash=_hash("d"),
        observed_environment=_ENVIRONMENT,
        verification_status=SnapshotVerificationStatus.CURRENT,
        rejected_record_count=0,
        publication_approval_passed=True,
        freshness_eligible=True,
        provenance_valid=True,
        approval_expired=False,
        revocation_unresolved=False,
        scope_allowed=True,
    )


def _request(catalog_snapshot, knowledge_snapshot, **overrides: object) -> RuntimeBundleBuildRequest:
    request = RuntimeBundleBuildRequest(
        bundle_key="local-rag-runtime",
        bundle_version="2026.09.10-001",
        environment_code=_ENVIRONMENT,
        execution_manifest=RuntimeExecutionManifestInput(
            manifest_key="rag-runtime",
            manifest_version="2026.09.10-001",
            schema_version="runtime-manifest-v1",
            git_commit_sha="abcdef1",
            worker_artifact_ref="worker:local:2026.09.10",
        ),
        catalog=MedicationCatalogBinding(
            catalog_version=_CATALOG_VERSION,
            catalog_manifest_hash=_hash("9"),
            verification_status=CatalogVerificationStatus.APPROVED,
            freshness_status=CatalogFreshnessStatus.CURRENT,
            is_complete=True,
            source_snapshot_ids=(str(catalog_snapshot.id),),
        ),
        source_members=(
            _source_member(catalog_snapshot, RuntimeBundleMemberPurpose.CATALOG),
            _source_member(knowledge_snapshot, RuntimeBundleMemberPurpose.KNOWLEDGE),
        ),
        artifact_members=(
            RuntimeBundleArtifactMemberInput(
                artifact_kind=RuntimeBundleArtifactKind.CANDIDATE_INDEX,
                artifact_ref="candidate-index:local",
                artifact_version="1.0.0",
                observed_environment=_ENVIRONMENT,
                approval_effective=True,
                approval_expired=False,
                revocation_unresolved=False,
                manifest_hash=_hash("e"),
                catalog_version=_CATALOG_VERSION,
                catalog_manifest_hash=_hash("9"),
            ),
        ),
        created_by="integration-test",
    )
    return replace(request, **overrides)  # type: ignore[arg-type]


async def _count(model: type) -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(model))
        return int(result.scalar_one())


async def _seed_environment() -> None:
    async with session_factory.begin() as session:
        await RagRuntimeRepository(session).create_environment(
            RagRuntimeEnvironmentCreate(
                environment_code=_ENVIRONMENT,
                environment_status=RagRuntimeEnvironmentStatus.SUSPENDED,
            )
        )


async def test_port_persists_a_building_bundle_with_the_full_canonical_configuration() -> None:
    catalog = await _seed_source_snapshot("CATALOG")
    knowledge = await _seed_source_snapshot("KNOWLEDGE")
    request = _request(catalog, knowledge)

    async with session_factory.begin() as session:
        execution = await execute_runtime_bundle_build(session, request)

    assert execution.outcome.decision is RuntimeBundleBuildDecision.BUILDABLE
    assert execution.stored
    assert execution.persisted is not None
    bundle = execution.persisted.bundle
    assert bundle.bundle_status is RagRuntimeBundleStatus.BUILDING
    assert bundle.environment_code == _ENVIRONMENT
    assert bundle.catalog_version == _CATALOG_VERSION
    assert bundle.candidate_index_version == "1.0.0"
    assert {member.source_purpose for member in execution.persisted.bundle_sources} == {
        RagRuntimeSourcePurpose.CATALOG,
        RagRuntimeSourcePurpose.KNOWLEDGE,
    }
    assert all(member.approval_version == "approval-v1" for member in execution.persisted.bundle_sources)


async def test_stored_bundle_manifest_hash_recomputes_from_storage() -> None:
    """The review blocker: the hash must be verifiable from persisted rows alone."""
    catalog = await _seed_source_snapshot("CATALOG")
    knowledge = await _seed_source_snapshot("KNOWLEDGE")

    async with session_factory.begin() as session:
        execution = await execute_runtime_bundle_build(session, _request(catalog, knowledge))
    assert execution.persisted is not None
    bundle_id = execution.persisted.bundle.id
    built_hash = execution.outcome.bundle_manifest_hash

    async with session_factory() as session:
        configuration = await load_persisted_bundle_configuration(session, bundle_id)
        assert configuration is not None
        assert canonical_runtime_bundle_manifest_hash(configuration) == built_hash
        assert await verify_persisted_bundle_manifest_hash(session, bundle_id) is True


async def test_two_configurations_differing_only_in_artifact_version_do_not_collide_in_storage() -> None:
    """Regression for the review finding: artifact_version was hashed but never stored.

    Both bundles must persist distinct, individually verifiable rows -- not byte-identical rows
    carrying different hashes.
    """
    catalog = await _seed_source_snapshot("CATALOG")
    knowledge = await _seed_source_snapshot("KNOWLEDGE")
    first_request = _request(catalog, knowledge)
    second_request = _request(
        catalog,
        knowledge,
        bundle_version="2026.09.10-002",
        artifact_members=(replace(first_request.artifact_members[0], artifact_version="2.0.0"),),
    )

    async with session_factory.begin() as session:
        first = await execute_runtime_bundle_build(session, first_request)
        second = await execute_runtime_bundle_build(session, second_request)

    assert first.persisted is not None and second.persisted is not None
    assert first.outcome.bundle_manifest_hash != second.outcome.bundle_manifest_hash
    assert first.persisted.bundle.candidate_index_version == "1.0.0"
    assert second.persisted.bundle.candidate_index_version == "2.0.0"

    async with session_factory() as session:
        assert await verify_persisted_bundle_manifest_hash(session, first.persisted.bundle.id) is True
        assert await verify_persisted_bundle_manifest_hash(session, second.persisted.bundle.id) is True


async def test_appending_a_member_after_creation_breaks_hash_verification() -> None:
    """Member-set immutability is enforced by verification, not by a DB trigger.

    ``CONTRIBUTING.md`` forbids introducing triggers, so nothing physically prevents an INSERT.
    What the design guarantees instead is detection: the recomputed hash no longer matches.
    """
    catalog = await _seed_source_snapshot("CATALOG")
    knowledge = await _seed_source_snapshot("KNOWLEDGE")
    extra = await _seed_source_snapshot("EXTRA")

    async with session_factory.begin() as session:
        execution = await execute_runtime_bundle_build(session, _request(catalog, knowledge))
    assert execution.persisted is not None
    bundle_id = execution.persisted.bundle.id

    async with session_factory() as session:
        assert await verify_persisted_bundle_manifest_hash(session, bundle_id) is True

    # Inserted directly through the ORM: the repository no longer exposes a public member write,
    # so this simulates the strongest available bypass -- a raw INSERT -- and shows it is detected.
    async with session_factory.begin() as session:
        session.add(
            RagRuntimeBundleSource(
                bundle_id=bundle_id,
                source_snapshot_id=extra.id,
                source_purpose=RagRuntimeSourcePurpose.RULE,
                source_version=extra.source_version,
                canonical_checksum=extra.canonical_checksum,
                approval_version="approval-v1",
                scope_policy_hash=_hash("c"),
                freshness_policy_hash=_hash("d"),
            )
        )

    async with session_factory() as session:
        assert await verify_persisted_bundle_manifest_hash(session, bundle_id) is False


async def test_member_cannot_claim_a_version_its_snapshot_does_not_have() -> None:
    """``source_version`` is validated against the snapshot inside the build transaction.

    This was a composite FK onto ``uq_rag_source_snapshot_id_version``, but that made this
    migration a hard dependant of #369's unique constraint -- #369's downgrade could then no
    longer drop it and 11 of its tests broke.  #398 moved integrity enforcement from the database
    into Python, and this check follows that direction.
    """
    catalog = await _seed_source_snapshot("CATALOG")
    knowledge = await _seed_source_snapshot("KNOWLEDGE")
    request = _request(catalog, knowledge)
    forged = replace(
        request,
        source_members=(
            replace(request.source_members[0], source_version="api:2026-01-01:forged"),
            request.source_members[1],
        ),
    )

    with pytest.raises(RagRuntimeBundleSourceVersionMismatchError):
        async with session_factory.begin() as session:
            await execute_runtime_bundle_build(session, forged)

    assert await _count(RagRuntimeReleaseBundle) == 0
    assert await _count(RagRuntimeBundleSource) == 0


async def test_rejected_request_writes_nothing_and_leaves_the_pointer_untouched() -> None:
    catalog = await _seed_source_snapshot("CATALOG")
    knowledge = await _seed_source_snapshot("KNOWLEDGE")
    await _seed_environment()
    request = _request(catalog, knowledge)
    unapproved = replace(
        request,
        catalog=replace(request.catalog, verification_status=CatalogVerificationStatus.NOT_APPROVED),
    )

    async with session_factory.begin() as session:
        execution = await execute_runtime_bundle_build(session, unapproved)

    assert execution.outcome.decision is RuntimeBundleBuildDecision.REJECTED
    assert RuntimeBundleRejectionReason.CATALOG_NOT_APPROVED in execution.outcome.rejection_reasons
    assert execution.stored is False
    # Not even the execution manifest is written for a rejected request.
    assert await _count(RagRuntimeExecutionManifest) == 0
    assert await _count(RagRuntimeReleaseBundle) == 0
    assert await _count(RagRuntimeBundleSource) == 0
    assert await _count(RagRuntimeEnvironmentTransition) == 0

    async with session_factory() as session:
        environment = (
            await session.execute(
                select(RagRuntimeEnvironment).where(RagRuntimeEnvironment.environment_code == _ENVIRONMENT)
            )
        ).scalar_one()
    assert environment.active_bundle_id is None
    assert environment.environment_status is RagRuntimeEnvironmentStatus.SUSPENDED


async def test_ready_and_active_pointer_stay_at_zero_while_components_are_incomplete() -> None:
    catalog = await _seed_source_snapshot("CATALOG")
    knowledge = await _seed_source_snapshot("KNOWLEDGE")
    await _seed_environment()

    async with session_factory.begin() as session:
        execution = await execute_runtime_bundle_build(session, _request(catalog, knowledge))

    # Rule, Guideline and Safety members are absent, so the bundle may build but not be promoted.
    assert execution.outcome.readiness_blockers != ()

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
                select(RagRuntimeEnvironment).where(RagRuntimeEnvironment.environment_code == _ENVIRONMENT)
            )
        ).scalar_one()

    assert promoted == []
    assert environment.active_bundle_id is None
    assert environment.active_bundle_manifest_hash is None
    assert environment.environment_status is RagRuntimeEnvironmentStatus.SUSPENDED
    assert await _count(RagRuntimeEnvironmentTransition) == 0
