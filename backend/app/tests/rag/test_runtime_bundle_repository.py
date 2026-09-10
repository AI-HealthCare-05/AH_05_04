"""RAG-12A Runtime Bundle build transaction boundaries (Issue #175).

The write path takes the kernel outcome as a required argument, so these tests build a real
judgment rather than hand-rolling rows.  The guard tests then attempt the bypasses the reviewer
identified: reaching persistence without a BUILDABLE judgment, and handing a valid judgment
different rows.
"""

from dataclasses import replace
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.catalog.types import CatalogFreshnessStatus, CatalogVerificationStatus
from ai_worker.tasks.rag.runtime_bundle_builder import (
    MedicationCatalogBinding,
    RuntimeBundleArtifactKind,
    RuntimeBundleArtifactMemberInput,
    RuntimeBundleBuildDecision,
    RuntimeBundleBuildRequest,
    RuntimeBundleMemberPurpose,
    RuntimeBundleSourceMemberInput,
    RuntimeExecutionManifestInput,
    evaluate_runtime_bundle_build,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotVerificationStatus
from app.core import config
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
    RagRuntimeBundleBuildError,
    RagRuntimeBundleNotBuildableError,
    RagRuntimeBundleSourceCreate,
    RagRuntimeEnvironmentCreate,
    RagRuntimeExecutionManifestConflictError,
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
from app.services.rag_runtime_bundle_build import execute_runtime_bundle_build

_ENVIRONMENT = "local"
_CATALOG_VERSION = "catalog-1.0.0"


def _hash(char: str) -> str:
    return char * 64


async def _create_source_snapshot(session: AsyncSession):
    suffix = uuid4().hex[:10]
    repository = RagSourceCatalogRepository(session)
    source = await repository.create_source(
        RagSourceCreate(
            source_code=f"MFDS_BUILD_{suffix}",
            display_name="MFDS Runtime Source",
            owner_name="MFDS",
        )
    )
    endpoint = await repository.create_endpoint(
        RagSourceEndpointCreate(
            source_id=source.id,
            endpoint_code="PRODUCT_LIST",
            display_name="Product List",
        )
    )
    operation = await repository.create_operation(
        RagSourceOperationCreate(
            endpoint_id=endpoint.id,
            operation_code="LIST_PRODUCTS",
            display_name="List Products",
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
            verification_status=RagSnapshotVerificationStatus.CURRENT,
            collected_at=datetime.now(config.TIMEZONE),
            verified_at=datetime.now(config.TIMEZONE),
        )
    )


def _member(snapshot, purpose: RuntimeBundleMemberPurpose) -> RuntimeBundleSourceMemberInput:
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
            _member(catalog_snapshot, RuntimeBundleMemberPurpose.CATALOG),
            _member(knowledge_snapshot, RuntimeBundleMemberPurpose.KNOWLEDGE),
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
        created_by="backend-test",
    )
    return replace(request, **overrides)  # type: ignore[arg-type]


def _manifest_create(request: RuntimeBundleBuildRequest, manifest_hash: str) -> RagRuntimeExecutionManifestCreate:
    return RagRuntimeExecutionManifestCreate(
        manifest_key=request.execution_manifest.manifest_key,
        manifest_version=request.execution_manifest.manifest_version,
        manifest_hash=manifest_hash,
        schema_version=request.execution_manifest.schema_version,
        git_commit_sha=request.execution_manifest.git_commit_sha,
        worker_artifact_ref=request.execution_manifest.worker_artifact_ref,
    )


def _bundle_create(request: RuntimeBundleBuildRequest, bundle_manifest_hash: str) -> RagRuntimeReleaseBundleCreate:
    artifact = request.artifact_members[0]
    return RagRuntimeReleaseBundleCreate(
        bundle_key=request.bundle_key,
        bundle_version=request.bundle_version,
        execution_manifest_id=uuid4(),
        bundle_manifest_hash=bundle_manifest_hash,
        environment_code=request.environment_code,
        catalog_version=request.catalog.catalog_version,
        catalog_manifest_hash=request.catalog.catalog_manifest_hash,
        candidate_index_ref=artifact.artifact_ref,
        candidate_index_version=artifact.artifact_version,
        candidate_index_manifest_hash=artifact.manifest_hash,
        created_by=request.created_by,
    )


def _source_creates(request: RuntimeBundleBuildRequest) -> tuple[RagRuntimeBundleSourceCreate, ...]:
    return tuple(
        RagRuntimeBundleSourceCreate(
            bundle_id=uuid4(),
            source_snapshot_id=UUID(member.source_snapshot_id),
            source_purpose=RagRuntimeSourcePurpose(member.source_purpose.value),
            source_version=member.source_version,
            canonical_checksum=member.canonical_checksum,
            approval_version=member.approval_version,
            scope_policy_hash=member.scope_policy_hash,
            freshness_policy_hash=member.freshness_policy_hash,
            required=member.required,
            selected_for_operation=member.selected_for_operation,
        )
        for member in request.source_members
    )


async def _count(session: AsyncSession, model: type) -> int:
    result = await session.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


async def test_build_persists_manifest_bundle_and_every_member(db_session: AsyncSession) -> None:
    catalog = await _create_source_snapshot(db_session)
    knowledge = await _create_source_snapshot(db_session)

    execution = await execute_runtime_bundle_build(db_session, _request(catalog, knowledge))

    assert execution.persisted is not None
    result = execution.persisted
    assert result.execution_manifest_reused is False
    assert result.bundle.execution_manifest_id == result.execution_manifest.id
    assert result.bundle.bundle_status is RagRuntimeBundleStatus.BUILDING
    assert result.bundle.environment_code == _ENVIRONMENT
    assert result.bundle.candidate_index_version == "1.0.0"
    assert len(result.bundle_sources) == 2
    assert all(member.bundle_id == result.bundle.id for member in result.bundle_sources)
    assert all(member.approval_version == "approval-v1" for member in result.bundle_sources)

    persisted = await RagRuntimeRepository(db_session).list_bundle_sources(result.bundle.id)
    assert {member.id for member in persisted} == {member.id for member in result.bundle_sources}


async def test_rejected_judgment_is_refused_at_the_write_boundary(db_session: AsyncSession) -> None:
    """The reviewer's bypass: reaching persistence without a BUILDABLE judgment."""
    catalog = await _create_source_snapshot(db_session)
    knowledge = await _create_source_snapshot(db_session)
    request = _request(catalog, knowledge)
    rejected = evaluate_runtime_bundle_build(
        replace(request, catalog=replace(request.catalog, verification_status=CatalogVerificationStatus.NOT_APPROVED))
    )
    assert rejected.decision is RuntimeBundleBuildDecision.REJECTED

    with pytest.raises(RagRuntimeBundleNotBuildableError):
        await RagRuntimeRepository(db_session).build_runtime_bundle(
            outcome=rejected,
            manifest=_manifest_create(request, _hash("2")),
            bundle=_bundle_create(request, _hash("3")),
            bundle_sources=_source_creates(request),
        )

    assert await _count(db_session, RagRuntimeReleaseBundle) == 0
    assert await _count(db_session, RagRuntimeExecutionManifest) == 0


async def test_rows_differing_from_the_judged_configuration_are_refused(db_session: AsyncSession) -> None:
    """The remaining bypass: a valid judgment handed different rows."""
    catalog = await _create_source_snapshot(db_session)
    knowledge = await _create_source_snapshot(db_session)
    request = _request(catalog, knowledge)
    outcome = evaluate_runtime_bundle_build(request)
    assert outcome.bundle_manifest_hash is not None and outcome.manifest_hash is not None

    tampered = replace(_bundle_create(request, outcome.bundle_manifest_hash), environment_code="production")

    with pytest.raises(RagRuntimeBundleNotBuildableError):
        await RagRuntimeRepository(db_session).build_runtime_bundle(
            outcome=outcome,
            manifest=_manifest_create(request, outcome.manifest_hash),
            bundle=tampered,
            bundle_sources=_source_creates(request),
        )

    assert await _count(db_session, RagRuntimeReleaseBundle) == 0


async def test_dropping_a_member_from_the_judged_set_is_refused(db_session: AsyncSession) -> None:
    catalog = await _create_source_snapshot(db_session)
    knowledge = await _create_source_snapshot(db_session)
    request = _request(catalog, knowledge)
    outcome = evaluate_runtime_bundle_build(request)
    assert outcome.bundle_manifest_hash is not None and outcome.manifest_hash is not None

    with pytest.raises(RagRuntimeBundleNotBuildableError):
        await RagRuntimeRepository(db_session).build_runtime_bundle(
            outcome=outcome,
            manifest=_manifest_create(request, outcome.manifest_hash),
            bundle=_bundle_create(request, outcome.bundle_manifest_hash),
            bundle_sources=_source_creates(request)[:1],
        )

    assert await _count(db_session, RagRuntimeBundleSource) == 0


async def test_empty_member_set_is_refused(db_session: AsyncSession) -> None:
    catalog = await _create_source_snapshot(db_session)
    knowledge = await _create_source_snapshot(db_session)
    request = _request(catalog, knowledge)
    outcome = evaluate_runtime_bundle_build(request)
    assert outcome.bundle_manifest_hash is not None and outcome.manifest_hash is not None

    with pytest.raises(RagRuntimeBundleBuildError):
        await RagRuntimeRepository(db_session).build_runtime_bundle(
            outcome=outcome,
            manifest=_manifest_create(request, outcome.manifest_hash),
            bundle=_bundle_create(request, outcome.bundle_manifest_hash),
            bundle_sources=(),
        )


async def test_build_reuses_an_existing_manifest_instead_of_duplicating_it(db_session: AsyncSession) -> None:
    catalog = await _create_source_snapshot(db_session)
    knowledge = await _create_source_snapshot(db_session)
    other_knowledge = await _create_source_snapshot(db_session)

    first = await execute_runtime_bundle_build(db_session, _request(catalog, knowledge))
    # Different content (other knowledge member) but the same execution axis, so the manifest is
    # reused; the bundle_version must differ to satisfy uq_rag_runtime_bundle_key_version.
    second = await execute_runtime_bundle_build(
        db_session, _request(catalog, other_knowledge, bundle_version="2026.09.10-002")
    )

    assert first.persisted is not None and second.persisted is not None
    assert second.persisted.execution_manifest_reused is True
    assert second.persisted.execution_manifest.id == first.persisted.execution_manifest.id
    assert await _count(db_session, RagRuntimeExecutionManifest) == 1


async def test_reusing_a_hash_for_a_different_manifest_fails_closed(db_session: AsyncSession) -> None:
    catalog = await _create_source_snapshot(db_session)
    knowledge = await _create_source_snapshot(db_session)
    await execute_runtime_bundle_build(db_session, _request(catalog, knowledge))

    # The stored manifest hash forced onto a different execution axis: silently reusing the stored
    # row would pin the bundle to an axis the caller never asked for.
    other_catalog = await _create_source_snapshot(db_session)
    other_request = _request(other_catalog, knowledge)
    other_outcome = evaluate_runtime_bundle_build(other_request)
    assert other_outcome.manifest_hash is not None and other_outcome.bundle_manifest_hash is not None
    conflicting = replace(
        _manifest_create(other_request, other_outcome.manifest_hash),
        worker_artifact_ref="worker:local:other",
    )

    with pytest.raises(RagRuntimeExecutionManifestConflictError):
        await RagRuntimeRepository(db_session).build_runtime_bundle(
            outcome=other_outcome,
            manifest=conflicting,
            bundle=_bundle_create(other_request, other_outcome.bundle_manifest_hash),
            bundle_sources=_source_creates(other_request),
        )


async def test_identical_bundle_content_collides_on_the_unique_constraint(db_session: AsyncSession) -> None:
    catalog = await _create_source_snapshot(db_session)
    knowledge = await _create_source_snapshot(db_session)
    await execute_runtime_bundle_build(db_session, _request(catalog, knowledge))
    await db_session.commit()

    # Same content under a new bundle_version: content identity excludes the version, so this is
    # the same bundle and uq_rag_runtime_bundle_manifest_hash refuses it by design.
    with pytest.raises(IntegrityError):
        await execute_runtime_bundle_build(db_session, _request(catalog, knowledge, bundle_version="2026.09.10-002"))
    await db_session.rollback()

    assert await _count(db_session, RagRuntimeReleaseBundle) == 1
    assert await _count(db_session, RagRuntimeBundleSource) == 2


async def test_build_does_not_read_or_change_the_environment_pointer(db_session: AsyncSession) -> None:
    catalog = await _create_source_snapshot(db_session)
    knowledge = await _create_source_snapshot(db_session)
    repository = RagRuntimeRepository(db_session)
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code=_ENVIRONMENT,
            environment_status=RagRuntimeEnvironmentStatus.SUSPENDED,
        )
    )

    await execute_runtime_bundle_build(db_session, _request(catalog, knowledge))

    assert environment.active_bundle_id is None
    assert environment.active_bundle_manifest_hash is None
    assert environment.environment_status is RagRuntimeEnvironmentStatus.SUSPENDED
    assert await _count(db_session, RagRuntimeEnvironmentTransition) == 0


def test_repository_exposes_no_public_member_write_path() -> None:
    """Members are writable only inside ``build_runtime_bundle``.

    Scoped to bundle members on purpose: RAG-17 (#180) must add environment-pointer mutation, so
    asserting that the whole repository has no update/delete would fail a legitimate future change
    while proving nothing about member immutability.
    """
    member_methods = {
        name
        for name in dir(RagRuntimeRepository)
        if not name.startswith("_") and ("bundle_source" in name or "member" in name)
    }

    assert member_methods == {"list_bundle_sources"}


def test_kernel_member_purpose_matches_the_persisted_enum() -> None:
    assert {member.value for member in RuntimeBundleMemberPurpose} == {
        member.value for member in RagRuntimeSourcePurpose
    }
