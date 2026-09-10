"""RAG-12A Runtime Bundle build transaction boundaries (Issue #175)."""

from dataclasses import replace
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.runtime_bundle_builder import RuntimeBundleMemberPurpose
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

_UNUSED_UUID = uuid4()


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


def _manifest_payload(manifest_hash: str) -> RagRuntimeExecutionManifestCreate:
    return RagRuntimeExecutionManifestCreate(
        manifest_key="rag-runtime",
        manifest_version="2026.09.10-001",
        manifest_hash=manifest_hash,
        schema_version="runtime-manifest-v1",
        git_commit_sha="abcdef1",
        worker_artifact_ref="worker:local:2026.09.10",
    )


def _bundle_payload(bundle_manifest_hash: str, **overrides: object) -> RagRuntimeReleaseBundleCreate:
    payload = RagRuntimeReleaseBundleCreate(
        bundle_key="local-rag-runtime",
        bundle_version="2026.09.10-001",
        # The build transaction supplies the real manifest id; this placeholder proves it does.
        execution_manifest_id=_UNUSED_UUID,
        bundle_manifest_hash=bundle_manifest_hash,
        environment_code="local",
        catalog_version="catalog-1.0.0",
        catalog_manifest_hash=_hash("9"),
        candidate_index_ref="candidate-index:local",
        candidate_index_version="1.0.0",
        candidate_index_manifest_hash=_hash("4"),
        created_by="backend-test",
    )
    return replace(payload, **overrides)  # type: ignore[arg-type]


def _member(
    snapshot, purpose: RagRuntimeSourcePurpose = RagRuntimeSourcePurpose.CATALOG
) -> RagRuntimeBundleSourceCreate:
    return RagRuntimeBundleSourceCreate(
        bundle_id=_UNUSED_UUID,
        source_snapshot_id=snapshot.id,
        source_purpose=purpose,
        source_version=snapshot.source_version,
        canonical_checksum=snapshot.canonical_checksum,
        approval_version="approval-v1",
        scope_policy_hash=_hash("c"),
        freshness_policy_hash=_hash("d"),
    )


async def _count(session: AsyncSession, model: type) -> int:
    result = await session.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


async def test_build_persists_manifest_bundle_and_every_member(db_session: AsyncSession) -> None:
    catalog_snapshot = await _create_source_snapshot(db_session)
    knowledge_snapshot = await _create_source_snapshot(db_session)
    repository = RagRuntimeRepository(db_session)

    result = await repository.build_runtime_bundle(
        manifest=_manifest_payload(_hash("2")),
        bundle=_bundle_payload(_hash("3")),
        bundle_sources=(
            _member(catalog_snapshot, RagRuntimeSourcePurpose.CATALOG),
            _member(knowledge_snapshot, RagRuntimeSourcePurpose.KNOWLEDGE),
        ),
    )

    assert result.execution_manifest_reused is False
    assert result.bundle.execution_manifest_id == result.execution_manifest.id
    assert len(result.bundle_sources) == 2
    assert all(member.bundle_id == result.bundle.id for member in result.bundle_sources)
    persisted = await repository.list_bundle_sources(result.bundle.id)
    assert {member.id for member in persisted} == {member.id for member in result.bundle_sources}
    assert {member.source_purpose for member in persisted} == {
        RagRuntimeSourcePurpose.CATALOG,
        RagRuntimeSourcePurpose.KNOWLEDGE,
    }


async def test_build_forces_building_status(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    repository = RagRuntimeRepository(db_session)

    result = await repository.build_runtime_bundle(
        manifest=_manifest_payload(_hash("2")),
        # A caller asking for READY must not get it: READY belongs to RAG-17 (#180).
        bundle=_bundle_payload(_hash("3"), bundle_status=RagRuntimeBundleStatus.READY),
        bundle_sources=(_member(snapshot, RagRuntimeSourcePurpose.CATALOG),),
    )

    assert result.bundle.bundle_status is RagRuntimeBundleStatus.BUILDING
    assert result.bundle.ready_at is None
    assert result.bundle.activated_at is None


async def test_build_reuses_an_existing_manifest_instead_of_duplicating_it(db_session: AsyncSession) -> None:
    first_snapshot = await _create_source_snapshot(db_session)
    second_snapshot = await _create_source_snapshot(db_session)
    repository = RagRuntimeRepository(db_session)

    first = await repository.build_runtime_bundle(
        manifest=_manifest_payload(_hash("2")),
        bundle=_bundle_payload(_hash("3")),
        bundle_sources=(_member(first_snapshot, RagRuntimeSourcePurpose.CATALOG),),
    )
    second = await repository.build_runtime_bundle(
        manifest=_manifest_payload(_hash("2")),
        bundle=_bundle_payload(_hash("5"), bundle_version="2026.09.10-002"),
        bundle_sources=(_member(second_snapshot, RagRuntimeSourcePurpose.CATALOG),),
    )

    assert second.execution_manifest_reused is True
    assert second.execution_manifest.id == first.execution_manifest.id
    assert await _count(db_session, RagRuntimeExecutionManifest) == 1


async def test_reusing_a_hash_for_a_different_manifest_fails_closed(db_session: AsyncSession) -> None:
    first_snapshot = await _create_source_snapshot(db_session)
    second_snapshot = await _create_source_snapshot(db_session)
    repository = RagRuntimeRepository(db_session)
    await repository.build_runtime_bundle(
        manifest=_manifest_payload(_hash("2")),
        bundle=_bundle_payload(_hash("3")),
        bundle_sources=(_member(first_snapshot, RagRuntimeSourcePurpose.CATALOG),),
    )

    # Same hash, different worker artifact: silently reusing the stored manifest would pin the
    # bundle to an execution axis the caller never asked for.
    conflicting = replace(_manifest_payload(_hash("2")), worker_artifact_ref="worker:local:other")
    with pytest.raises(RagRuntimeExecutionManifestConflictError):
        await repository.build_runtime_bundle(
            manifest=conflicting,
            bundle=_bundle_payload(_hash("5"), bundle_version="2026.09.10-002"),
            bundle_sources=(_member(second_snapshot, RagRuntimeSourcePurpose.CATALOG),),
        )

    assert await _count(db_session, RagRuntimeReleaseBundle) == 1


async def test_identical_bundle_content_collides_and_leaves_no_partial_rows(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    repository = RagRuntimeRepository(db_session)
    members = (_member(snapshot, RagRuntimeSourcePurpose.CATALOG),)
    await repository.build_runtime_bundle(
        manifest=_manifest_payload(_hash("2")),
        bundle=_bundle_payload(_hash("3")),
        bundle_sources=members,
    )
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await repository.build_runtime_bundle(
            manifest=_manifest_payload(_hash("2")),
            bundle=_bundle_payload(_hash("3"), bundle_version="2026.09.10-002"),
            bundle_sources=members,
        )
    await db_session.rollback()

    assert await _count(db_session, RagRuntimeReleaseBundle) == 1
    assert await _count(db_session, RagRuntimeBundleSource) == 1


async def test_build_does_not_read_or_change_the_environment_pointer(db_session: AsyncSession) -> None:
    snapshot = await _create_source_snapshot(db_session)
    repository = RagRuntimeRepository(db_session)
    environment = await repository.create_environment(
        RagRuntimeEnvironmentCreate(
            environment_code="local",
            environment_status=RagRuntimeEnvironmentStatus.SUSPENDED,
        )
    )

    await repository.build_runtime_bundle(
        manifest=_manifest_payload(_hash("2")),
        bundle=_bundle_payload(_hash("3")),
        bundle_sources=(_member(snapshot, RagRuntimeSourcePurpose.CATALOG),),
    )

    assert environment.active_bundle_id is None
    assert environment.active_bundle_manifest_hash is None
    assert environment.environment_status is RagRuntimeEnvironmentStatus.SUSPENDED
    assert await _count(db_session, RagRuntimeEnvironmentTransition) == 0


def test_repository_exposes_no_way_to_update_a_building_member() -> None:
    """Scoped to bundle members on purpose.

    RAG-17 (#180) must add environment-pointer mutation, so asserting that the whole repository
    has no update/delete method would fail a legitimate future change while proving nothing about
    member immutability.  The property that matters is that no member-mutating path exists.
    """
    member_methods = {
        name
        for name in dir(RagRuntimeRepository)
        if not name.startswith("_") and ("bundle_source" in name or "member" in name)
    }

    assert member_methods == {"create_bundle_source", "list_bundle_sources"}


def test_kernel_member_purpose_matches_the_persisted_enum() -> None:
    assert {member.value for member in RuntimeBundleMemberPurpose} == {
        member.value for member in RagRuntimeSourcePurpose
    }
