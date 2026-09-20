from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_runtime import RagRuntimeSourcePurpose
from app.repositories.rag_runtime_repository import RagRuntimeRepository
from app.services.rag_runtime_bundle_build import verify_persisted_bundle_manifest_hash


@dataclass(frozen=True, slots=True)
class GuideRuntimeRequestIdentification:
    medication_identification_id: UUID
    prescription_version_medication_id: UUID


@dataclass(frozen=True, slots=True)
class GuideRuntimeRequestBundleSource:
    source_snapshot_id: UUID
    source_purpose: RagRuntimeSourcePurpose
    source_version: str
    canonical_checksum: str
    approval_version: str
    scope_policy_hash: str
    freshness_policy_hash: str
    required: bool
    selected_for_operation: bool


@dataclass(frozen=True, slots=True)
class GuideRuntimeRequestCarrier:
    job_id: UUID
    guide_id: UUID
    execution_context_id: UUID
    prescription_version_id: UUID
    runtime_environment_id: UUID
    runtime_environment_revision: int
    runtime_release_bundle_id: UUID
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    runtime_guard_decision_ref: str
    patient_context_digest: str | None
    source_scope_manifest_hash: str | None
    bundle_key: str
    bundle_version: str
    environment_code: str
    catalog_version: str
    catalog_manifest_hash: str
    candidate_index_ref: str | None
    candidate_index_version: str | None
    candidate_index_manifest_hash: str | None
    knowledge_index_ref: str | None
    knowledge_index_version: str | None
    knowledge_index_manifest_hash: str | None
    rule_set_ref: str | None
    rule_set_version: str | None
    guideline_set_ref: str | None
    guideline_set_version: str | None
    safety_policy_ref: str | None
    safety_policy_version: str | None
    governance_revision_ref: str | None
    identifications: tuple[GuideRuntimeRequestIdentification, ...]
    bundle_sources: tuple[GuideRuntimeRequestBundleSource, ...]


async def load_verified_guide_runtime_request_carrier(
    session: AsyncSession,
    ai_job_id: UUID,
) -> GuideRuntimeRequestCarrier | None:
    repository = RagRuntimeRepository(session)
    execution_context = await repository.get_execution_context_by_job(ai_job_id)
    if execution_context is None or execution_context.guide_id is None or execution_context.chat_message_id is not None:
        return None

    bundle_id = execution_context.runtime_release_bundle_id
    if not await verify_persisted_bundle_manifest_hash(session, bundle_id):
        return None

    bundle = await repository.get_release_bundle_by_id(bundle_id)
    if (
        bundle is None
        or bundle.bundle_manifest_hash != execution_context.runtime_release_bundle_manifest_hash
        or bundle.execution_manifest_id != execution_context.runtime_execution_manifest_id
    ):
        return None

    identifications = await repository.list_execution_identifications(execution_context.id)
    bundle_sources = await repository.list_bundle_sources(bundle.id)
    return GuideRuntimeRequestCarrier(
        job_id=execution_context.ai_job_id,
        guide_id=execution_context.guide_id,
        execution_context_id=execution_context.id,
        prescription_version_id=execution_context.prescription_version_id,
        runtime_environment_id=execution_context.runtime_environment_id,
        runtime_environment_revision=execution_context.runtime_environment_revision,
        runtime_release_bundle_id=bundle.id,
        runtime_release_bundle_manifest_hash=bundle.bundle_manifest_hash,
        runtime_execution_manifest_id=execution_context.runtime_execution_manifest_id,
        runtime_execution_manifest_hash=execution_context.runtime_execution_manifest_hash,
        runtime_guard_decision_ref=execution_context.runtime_guard_decision_ref,
        patient_context_digest=execution_context.patient_context_digest,
        source_scope_manifest_hash=execution_context.source_scope_manifest_hash,
        bundle_key=bundle.bundle_key,
        bundle_version=bundle.bundle_version,
        environment_code=bundle.environment_code,
        catalog_version=bundle.catalog_version,
        catalog_manifest_hash=bundle.catalog_manifest_hash,
        candidate_index_ref=bundle.candidate_index_ref,
        candidate_index_version=bundle.candidate_index_version,
        candidate_index_manifest_hash=bundle.candidate_index_manifest_hash,
        knowledge_index_ref=bundle.knowledge_index_ref,
        knowledge_index_version=bundle.knowledge_index_version,
        knowledge_index_manifest_hash=bundle.knowledge_index_manifest_hash,
        rule_set_ref=bundle.rule_set_ref,
        rule_set_version=bundle.rule_set_version,
        guideline_set_ref=bundle.guideline_set_ref,
        guideline_set_version=bundle.guideline_set_version,
        safety_policy_ref=bundle.safety_policy_ref,
        safety_policy_version=bundle.safety_policy_version,
        governance_revision_ref=bundle.governance_revision_ref,
        identifications=tuple(
            GuideRuntimeRequestIdentification(
                medication_identification_id=identification.medication_identification_id,
                prescription_version_medication_id=identification.prescription_version_medication_id,
            )
            for identification in identifications
        ),
        bundle_sources=tuple(
            GuideRuntimeRequestBundleSource(
                source_snapshot_id=source.source_snapshot_id,
                source_purpose=source.source_purpose,
                source_version=source.source_version,
                canonical_checksum=source.canonical_checksum,
                approval_version=source.approval_version,
                scope_policy_hash=source.scope_policy_hash,
                freshness_policy_hash=source.freshness_policy_hash,
                required=source.required,
                selected_for_operation=source.selected_for_operation,
            )
            for source in bundle_sources
        ),
    )
