from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import RagKnowledgeIndex, RagKnowledgeIndexMember
from app.models.rag_runtime import (
    GuideRetrievalBindingManifest,
    RagRuntimeBundleSource,
    RagRuntimeSourcePurpose,
)
from app.repositories.rag_runtime_repository import (
    GuideRetrievalBindingManifestCreate,
    RagRuntimeRepository,
)
from app.services.rag_runtime_bundle_build import verify_persisted_bundle_manifest_hash
from rag_runtime.guide_retrieval_binding import (
    GUIDE_RETRIEVAL_BINDING_PROJECTION_VERSION,
    GuideRetrievalArtifactRef,
    GuideRetrievalBindingValidationError,
    GuideRetrievalMemberBinding,
    GuideRetrievalSourceMember,
    JsonValue,
    build_guide_retrieval_binding_manifest,
    parse_member_bindings,
)
from rag_runtime.guide_retrieval_binding import (
    GuideRetrievalBindingManifest as CanonicalGuideRetrievalBindingManifest,
)


class GuideRetrievalBindingBuildError(ValueError):
    """The requested binding cannot be proven from pinned persistence."""


@dataclass(frozen=True, slots=True)
class GuideRetrievalBindingBuildRequest:
    runtime_release_bundle_id: UUID
    runtime_execution_manifest_id: UUID
    knowledge_index_id: UUID
    member_bindings: tuple[GuideRetrievalMemberBinding, ...]
    retrieval_configuration: dict[str, JsonValue]


async def create_guide_retrieval_binding_manifest(
    session: AsyncSession,
    request: GuideRetrievalBindingBuildRequest,
) -> GuideRetrievalBindingManifest:
    repository = RagRuntimeRepository(session)
    if not await verify_persisted_bundle_manifest_hash(session, request.runtime_release_bundle_id):
        raise GuideRetrievalBindingBuildError("runtime bundle canonical verification failed")
    bundle = await repository.get_release_bundle_by_id(request.runtime_release_bundle_id)
    execution_manifest = await repository.get_execution_manifest_by_id(request.runtime_execution_manifest_id)
    if bundle is None or execution_manifest is None or bundle.execution_manifest_id != execution_manifest.id:
        raise GuideRetrievalBindingBuildError("runtime bundle and execution manifest are not exact-bound")

    bundle_sources = await repository.list_bundle_sources(bundle.id)
    knowledge_index = await session.get(RagKnowledgeIndex, request.knowledge_index_id)
    if knowledge_index is None:
        raise GuideRetrievalBindingBuildError("knowledge index authority is missing")
    evidence_index_ref = GuideRetrievalArtifactRef(
        artifact_code=knowledge_index.index_code,
        version=knowledge_index.index_version,
        content_sha256=knowledge_index.index_configuration_hash,
    )
    if (
        bundle.knowledge_index_ref,
        bundle.knowledge_index_version,
        bundle.knowledge_index_manifest_hash,
    ) != (
        evidence_index_ref.artifact_code,
        evidence_index_ref.version,
        evidence_index_ref.content_sha256,
    ):
        raise GuideRetrievalBindingBuildError("runtime bundle knowledge index authority does not exact-match")

    await _verify_member_bindings(
        session,
        knowledge_index_id=knowledge_index.id,
        member_bindings=request.member_bindings,
        allowed_snapshot_ids={
            source.source_snapshot_id
            for source in bundle_sources
            if source.source_purpose is RagRuntimeSourcePurpose.KNOWLEDGE and source.selected_for_operation
        },
    )
    try:
        canonical = build_guide_retrieval_binding_manifest(
            runtime_release_bundle_id=bundle.id,
            runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
            runtime_execution_manifest_id=execution_manifest.id,
            runtime_execution_manifest_hash=execution_manifest.manifest_hash,
            knowledge_index_id=knowledge_index.id,
            evidence_index_ref=evidence_index_ref,
            member_bindings=request.member_bindings,
            retrieval_configuration=request.retrieval_configuration,
            source_members=tuple(_source_member(source) for source in bundle_sources),
        )
    except GuideRetrievalBindingValidationError as exc:
        raise GuideRetrievalBindingBuildError(str(exc)) from exc
    return await repository.create_guide_retrieval_binding_manifest(_create_payload(canonical))


async def load_verified_guide_retrieval_binding_manifest(
    session: AsyncSession,
    manifest_id: UUID,
    expected_manifest_hash: str,
) -> CanonicalGuideRetrievalBindingManifest | None:
    repository = RagRuntimeRepository(session)
    row = await repository.get_guide_retrieval_binding_manifest_by_id(manifest_id)
    if (
        row is None
        or row.manifest_version != GUIDE_RETRIEVAL_BINDING_PROJECTION_VERSION
        or row.manifest_hash != expected_manifest_hash
    ):
        return None
    if not await verify_persisted_bundle_manifest_hash(session, row.runtime_release_bundle_id):
        return None
    bundle = await repository.get_release_bundle_by_id(row.runtime_release_bundle_id)
    execution_manifest = await repository.get_execution_manifest_by_id(row.runtime_execution_manifest_id)
    knowledge_index = await session.get(RagKnowledgeIndex, row.knowledge_index_id)
    if bundle is None or execution_manifest is None or knowledge_index is None:
        return None
    bundle_sources = await repository.list_bundle_sources(bundle.id)
    evidence_index_ref = GuideRetrievalArtifactRef(
        artifact_code=row.evidence_index_code,
        version=row.evidence_index_version,
        content_sha256=row.evidence_index_configuration_hash,
    )
    if (
        bundle.bundle_manifest_hash != row.runtime_release_bundle_manifest_hash
        or bundle.execution_manifest_id != execution_manifest.id
        or execution_manifest.manifest_hash != row.runtime_execution_manifest_hash
        or (
            knowledge_index.index_code,
            knowledge_index.index_version,
            knowledge_index.index_configuration_hash,
        )
        != (evidence_index_ref.artifact_code, evidence_index_ref.version, evidence_index_ref.content_sha256)
        or (
            bundle.knowledge_index_ref,
            bundle.knowledge_index_version,
            bundle.knowledge_index_manifest_hash,
        )
        != (evidence_index_ref.artifact_code, evidence_index_ref.version, evidence_index_ref.content_sha256)
    ):
        return None
    try:
        member_bindings = parse_member_bindings(row.member_bindings_json)
        await _verify_member_bindings(
            session,
            knowledge_index_id=knowledge_index.id,
            member_bindings=member_bindings,
            allowed_snapshot_ids={
                source.source_snapshot_id
                for source in bundle_sources
                if source.source_purpose is RagRuntimeSourcePurpose.KNOWLEDGE and source.selected_for_operation
            },
        )
        source_members = tuple(_source_member(source) for source in bundle_sources)
        canonical = build_guide_retrieval_binding_manifest(
            runtime_release_bundle_id=bundle.id,
            runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
            runtime_execution_manifest_id=execution_manifest.id,
            runtime_execution_manifest_hash=execution_manifest.manifest_hash,
            knowledge_index_id=knowledge_index.id,
            evidence_index_ref=evidence_index_ref,
            member_bindings=member_bindings,
            retrieval_configuration=row.retrieval_configuration_json,  # type: ignore[arg-type]
            source_members=source_members,
        )
    except (GuideRetrievalBindingBuildError, GuideRetrievalBindingValidationError, TypeError, ValueError):
        return None
    if (
        canonical.manifest_hash != row.manifest_hash
        or _retrieval_configuration_hash(canonical.retrieval_configuration) != row.retrieval_configuration_hash
        or canonical.filter_snapshot_ref.artifact_code != row.filter_snapshot_code
        or canonical.filter_snapshot_ref.version != row.filter_snapshot_version
        or canonical.filter_snapshot_ref.content_sha256 != row.filter_snapshot_hash
        or canonical.source_manifest_hash != row.source_manifest_hash
    ):
        return None
    return canonical


async def _verify_member_bindings(
    session: AsyncSession,
    *,
    knowledge_index_id: UUID,
    member_bindings: tuple[GuideRetrievalMemberBinding, ...],
    allowed_snapshot_ids: set[UUID],
) -> None:
    if not member_bindings or not allowed_snapshot_ids:
        raise GuideRetrievalBindingBuildError("pinned knowledge member scope is empty")
    member_ids = {binding.source_snapshot_member_id for binding in member_bindings}
    rows = (
        await session.execute(
            select(RagKnowledgeIndexMember.source_snapshot_id, RagKnowledgeIndexMember.source_snapshot_member_id)
            .where(
                RagKnowledgeIndexMember.knowledge_index_id == knowledge_index_id,
                RagKnowledgeIndexMember.source_snapshot_member_id.in_(member_ids),
            )
            .order_by(RagKnowledgeIndexMember.source_snapshot_id, RagKnowledgeIndexMember.source_snapshot_member_id)
        )
    ).all()
    expected = {(binding.source_snapshot_id, binding.source_snapshot_member_id) for binding in member_bindings}
    if {(row[0], row[1]) for row in rows} != expected or not {
        binding.source_snapshot_id for binding in member_bindings
    } <= allowed_snapshot_ids:
        raise GuideRetrievalBindingBuildError("pinned member scope does not exact-match index and bundle authority")


def _source_member(source: RagRuntimeBundleSource) -> GuideRetrievalSourceMember:
    return GuideRetrievalSourceMember(
        source_snapshot_id=source.source_snapshot_id,
        source_purpose=source.source_purpose.value,
        source_version=source.source_version,
        canonical_checksum=source.canonical_checksum,
        approval_version=source.approval_version,
        scope_policy_hash=source.scope_policy_hash,
        freshness_policy_hash=source.freshness_policy_hash,
        required=source.required,
        selected_for_operation=source.selected_for_operation,
    )


def _create_payload(manifest: CanonicalGuideRetrievalBindingManifest) -> GuideRetrievalBindingManifestCreate:
    config_hash = _retrieval_configuration_hash(manifest.retrieval_configuration)
    return GuideRetrievalBindingManifestCreate(
        manifest_version=GUIDE_RETRIEVAL_BINDING_PROJECTION_VERSION,
        manifest_hash=manifest.manifest_hash,
        runtime_release_bundle_id=manifest.runtime_release_bundle_id,
        runtime_release_bundle_manifest_hash=manifest.runtime_release_bundle_manifest_hash,
        runtime_execution_manifest_id=manifest.runtime_execution_manifest_id,
        runtime_execution_manifest_hash=manifest.runtime_execution_manifest_hash,
        knowledge_index_id=manifest.knowledge_index_id,
        evidence_index_code=manifest.evidence_index_ref.artifact_code,
        evidence_index_version=manifest.evidence_index_ref.version,
        evidence_index_configuration_hash=manifest.evidence_index_ref.content_sha256,
        member_bindings_json=[
            {
                "source_snapshot_id": str(item.source_snapshot_id),
                "source_snapshot_member_id": str(item.source_snapshot_member_id),
            }
            for item in manifest.member_bindings
        ],
        retrieval_configuration_json=manifest.retrieval_configuration,  # type: ignore[arg-type]
        retrieval_configuration_hash=config_hash,
        filter_snapshot_code=manifest.filter_snapshot_ref.artifact_code,
        filter_snapshot_version=manifest.filter_snapshot_ref.version,
        filter_snapshot_hash=manifest.filter_snapshot_ref.content_sha256,
        source_manifest_hash=manifest.source_manifest_hash,
    )


def _retrieval_configuration_hash(configuration: dict[str, JsonValue]) -> str:
    artifact_ref = configuration.get("artifact_ref")
    if not isinstance(artifact_ref, dict):
        raise GuideRetrievalBindingValidationError("retrieval configuration artifact ref is missing")
    content_sha256 = artifact_ref.get("content_sha256")
    if not isinstance(content_sha256, str):
        raise GuideRetrievalBindingValidationError("retrieval configuration hash is missing")
    return content_sha256


__all__ = [
    "GuideRetrievalBindingBuildError",
    "GuideRetrievalBindingBuildRequest",
    "create_guide_retrieval_binding_manifest",
    "load_verified_guide_retrieval_binding_manifest",
]
