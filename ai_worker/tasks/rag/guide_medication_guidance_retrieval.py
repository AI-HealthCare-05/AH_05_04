"""Canonical, per-medication Guide retrieval composition (#180).

This boundary consumes the verified Backend Sync carrier as an opaque cross-package
input. It never reads Backend ORM/current Source state and stops at #697 authenticated
selections; #711 hydration and #760 handoff retain their own authority inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from rag_runtime.guide_runtime_execution import GuideRuntimeRequestIdentificationPort as SharedGuideRuntimeRequestIdentificationPort

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchExecutionBinding,
    EvidenceSearchPort,
    EvidenceSearchRequest,
    ProductionSearchHit,
    RetrievalExecutionMode,
    VersionedDenseSearchConfiguration,
    VersionedEvidenceRetrievalConfiguration,
    VersionedLexicalSearchConfiguration,
    validate_query_text,
)
from ai_worker.tasks.rag.guide_evidence_authority import (
    GuideEvidenceAuthorityReaderPort,
    GuideRequestAuthoritySelectedMember,
    GuideSelectedMemberAuthorityResolverPort,
    SyncGuideEvidenceAuthorityDecision,
    SyncGuideEvidenceAuthorityRequest,
    SyncGuideEvidenceAuthoritySelection,
    assemble_sync_guide_evidence_authority,
)
from ai_worker.tasks.rag.guide_evidence_handoff import ObservedDecisionOutcome
from ai_worker.tasks.rag.guide_retrieval_composition import (
    GuideRetrievalCompositionDecision,
    GuideRetrievalCompositionOutcome,
    compose_guide_authority_with_production_retrieval,
)
from ai_worker.tasks.rag.guide_retrieval_outcome_binding import (
    GuideRetrievalOutcomeBindingDecision,
    project_hybrid_retrieval_for_guide_composition,
)
from ai_worker.tasks.rag.production_evidence_gate import EvidenceGateSuccess, ProductionEvidenceEligibilityVerifierPort
from ai_worker.tasks.rag.request_authority_artifact import worker_artifact_ref
from ai_worker.tasks.rag.retrieval_run import RetrievalRunStorePort
from ai_worker.tasks.rag.retrieval_runtime import HybridRetrieveOutcome, HybridRetrieveRequest, execute_hybrid_retrieve
from ai_worker.tasks.rag.text_embedding import TextEmbeddingPort
from rag_runtime.guide_query_binding import (
    GuideQueryFingerprintDependencyError,
    GuideQueryFingerprintProducer,
    QueryBindingVerificationSuccess,
    QueryBindingVerifierPort,
)
from rag_runtime.guide_retrieval_binding import (
    GuideRetrievalArtifactRef,
    GuideRetrievalBindingManifest,
    validate_retrieval_configuration_projection,
)
from rag_runtime.request_authority import RequestAuthorityDecisionOutcome, RequestAuthorityDecisionStage
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    RequestGuardRuntimeBindingRef,
)

__all__ = [
    "GuideMedicationGuidanceRetrievalDecision",
    "GuideMedicationGuidanceRetrievalDependencies",
    "GuideMedicationGuidanceRetrievalOutcome",
    "GuideMedicationGuidanceRetrievalReason",
    "GuideMedicationGuidanceRetrievalRequest",
    "MedicationGuidanceRetrieval",
    "medication_hybrid_retrieve_node_id",
    "retrieve_medication_guidance",
]


class RequestGuardRuntimeBindingReaderPort(Protocol):
    async def read_exact(
        self, reference: RequestGuardRuntimeBindingRef
    ) -> RequestGuardRuntimeBindingObservation | None: ...


class GuideRuntimeRequestIdentificationPort(Protocol):
    prescription_version_medication_id: UUID
    medication_name_snapshot: str
    strength_text_snapshot: str | None


class GuideRuntimeRequestBundleSourcePort(Protocol):
    source_snapshot_id: UUID
    selected_for_operation: bool


class GuideRuntimeRequestCarrierPort(Protocol):
    job_id: UUID
    execution_context_id: UUID
    prescription_version_id: UUID
    runtime_release_bundle_id: UUID
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    runtime_guard_decision_ref: str
    request_guard_runtime_binding_ref: RequestGuardRuntimeBindingRef
    identifications: tuple[SharedGuideRuntimeRequestIdentificationPort, ...]
    bundle_sources: tuple[GuideRuntimeRequestBundleSourcePort, ...]
    retrieval_binding: GuideRetrievalBindingManifest


class GuideMedicationGuidanceRetrievalDecision(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class GuideMedicationGuidanceRetrievalReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    REQUEST_GUARD_BINDING_UNAVAILABLE = "REQUEST_GUARD_BINDING_UNAVAILABLE"
    REQUEST_GUARD_BINDING_REJECTED = "REQUEST_GUARD_BINDING_REJECTED"
    QUERY_BINDING_UNAVAILABLE = "QUERY_BINDING_UNAVAILABLE"
    QUERY_BINDING_REJECTED = "QUERY_BINDING_REJECTED"
    RETRIEVAL_BLOCKED = "RETRIEVAL_BLOCKED"
    SELECTED_MEMBER_NOT_FOUND = "SELECTED_MEMBER_NOT_FOUND"
    SELECTED_MEMBER_AUTHORITY_ERROR = "SELECTED_MEMBER_AUTHORITY_ERROR"
    AUTHORITY_REJECTED = "AUTHORITY_REJECTED"
    COMPOSITION_REJECTED = "COMPOSITION_REJECTED"


@dataclass(frozen=True, slots=True)
class GuideMedicationGuidanceRetrievalRequest:
    """One verified B1 carrier; no raw request, medication, or Source facts."""

    runtime_request: GuideRuntimeRequestCarrierPort


@dataclass(frozen=True, slots=True)
class GuideMedicationGuidanceRetrievalDependencies:
    query_fingerprint_producer: GuideQueryFingerprintProducer
    query_binding_verifier: QueryBindingVerifierPort
    request_guard_runtime_binding_reader: RequestGuardRuntimeBindingReaderPort
    selected_member_resolver: GuideSelectedMemberAuthorityResolverPort
    guide_evidence_authority_reader: GuideEvidenceAuthorityReaderPort
    search_port: EvidenceSearchPort
    text_embedding_port: TextEmbeddingPort | None
    run_store: RetrievalRunStorePort
    eligibility_verifier: ProductionEvidenceEligibilityVerifierPort


@dataclass(frozen=True, slots=True)
class MedicationGuidanceRetrieval:
    prescription_version_medication_id: UUID
    hybrid_outcome: HybridRetrieveOutcome
    composition: GuideRetrievalCompositionOutcome


@dataclass(frozen=True, slots=True)
class GuideMedicationGuidanceRetrievalOutcome:
    decision: GuideMedicationGuidanceRetrievalDecision
    reason: GuideMedicationGuidanceRetrievalReason | None
    medications: tuple[MedicationGuidanceRetrieval, ...] = ()


def medication_hybrid_retrieve_node_id(prescription_version_medication_id: UUID) -> str:
    """Return the deterministic retrieval-run node ID for one frozen medication."""
    if type(prescription_version_medication_id) is not UUID:
        raise TypeError("prescription_version_medication_id must be a UUID")
    return f"hybrid_retrieve:medication:{prescription_version_medication_id}"


def _blocked(reason: GuideMedicationGuidanceRetrievalReason) -> GuideMedicationGuidanceRetrievalOutcome:
    return GuideMedicationGuidanceRetrievalOutcome(GuideMedicationGuidanceRetrievalDecision.BLOCKED, reason)


def _artifact_ref(value: GuideRetrievalArtifactRef) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(value.artifact_code, value.version, value.content_sha256)


def _projection_artifact_ref(value: object) -> ImmutableArtifactRef:
    if type(value) is not dict:
        raise ValueError("artifact projection is invalid")
    code, version, digest = value.get("artifact_code"), value.get("version"), value.get("content_sha256")
    if type(code) is not str or type(version) is not str or type(digest) is not str:
        raise ValueError("artifact projection is invalid")
    return ImmutableArtifactRef(code, version, digest)


def _restore_retrieval_configuration(projection: object) -> VersionedEvidenceRetrievalConfiguration:
    """Restore the verified B1 projection; its existing hash validation remains authority."""
    validate_retrieval_configuration_projection(projection)
    if type(projection) is not dict:
        raise ValueError("retrieval configuration is invalid")
    lexical, dense = projection["lexical_config"], projection["dense_config"]
    if type(lexical) is not dict or (dense is not None and type(dense) is not dict):
        raise ValueError("retrieval configuration is invalid")
    lexical_config = VersionedLexicalSearchConfiguration(
        artifact_ref=_projection_artifact_ref(lexical["artifact_ref"]),
        exact_strategy=lexical["exact_strategy"],
        query_normalization=lexical["query_normalization"],
        trigram_match_operator=lexical["trigram_match_operator"],
        trigram_score_function=lexical["trigram_score_function"],
        trigram_threshold=lexical["trigram_threshold"],
        fts_regconfig=lexical["fts_regconfig"],
        fts_vector_expression=lexical["fts_vector_expression"],
        fts_query_constructor=lexical["fts_query_constructor"],
        fts_score_function=lexical["fts_score_function"],
        exact_limit=lexical["exact_limit"],
        trigram_limit=lexical["trigram_limit"],
        fts_limit=lexical["fts_limit"],
    )
    dense_config = (
        None
        if dense is None
        else VersionedDenseSearchConfiguration(
            artifact_ref=_projection_artifact_ref(dense["artifact_ref"]),
            distance_metric=dense["distance_metric"],
            minimum_similarity=dense["minimum_similarity"],
            cutoff_policy=dense["cutoff_policy"],
            dense_limit=dense["dense_limit"],
        )
    )
    adapter = projection["expected_query_embedding_adapter_ref"]
    config = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=_projection_artifact_ref(projection["artifact_ref"]),
        lexical_config=lexical_config,
        dense_config=dense_config,
        expected_query_embedding_adapter_ref=None if adapter is None else _projection_artifact_ref(adapter),
        execution_mode=RetrievalExecutionMode(projection["execution_mode"]),
        algorithm_id=projection["algorithm_id"],
        rrf_k=projection["rrf_k"],
        exact_limit=projection["exact_limit"],
        trigram_limit=projection["trigram_limit"],
        fts_limit=projection["fts_limit"],
        lexical_limit=projection["lexical_limit"],
        dense_limit=projection["dense_limit"],
        hybrid_limit=projection["hybrid_limit"],
        future_reranker_input_limit=projection["future_reranker_input_limit"],
        stable_coordinate_fields=tuple(projection["stable_coordinate_fields"]),
        tie_break=projection["tie_break"],
        observed_score_projection=projection["observed_score_projection"],
        observed_score_quantum=projection["observed_score_quantum"],
        observed_score_rounding=projection["observed_score_rounding"],
        transaction_isolation=projection["transaction_isolation"],
        transaction_access=projection["transaction_access"],
    )
    if not config.is_hash_valid():
        raise ValueError("retrieval configuration is not canonical")
    return config


def _validated_carrier(value: GuideRuntimeRequestCarrierPort) -> GuideRuntimeRequestCarrierPort:
    """Validate only the shared B1 surface, without importing Backend modules."""
    carrier, binding = value, value.retrieval_binding
    if (
        type(carrier.job_id) is not UUID
        or type(carrier.execution_context_id) is not UUID
        or type(carrier.prescription_version_id) is not UUID
        or type(carrier.runtime_release_bundle_id) is not UUID
        or type(carrier.runtime_execution_manifest_id) is not UUID
        or type(carrier.runtime_guard_decision_ref) is not str
        or not carrier.runtime_guard_decision_ref
        or type(carrier.request_guard_runtime_binding_ref) is not RequestGuardRuntimeBindingRef
        or type(binding) is not GuideRetrievalBindingManifest
        or not carrier.identifications
    ):
        raise ValueError("carrier is invalid")
    _restore_retrieval_configuration(binding.retrieval_configuration)
    for identification in carrier.identifications:
        name, strength = identification.medication_name_snapshot, identification.strength_text_snapshot
        if (
            type(identification.prescription_version_medication_id) is not UUID
            or type(name) is not str
            or not name
            or name != name.strip()
            or (strength is not None and (type(strength) is not str or not strength or strength != strength.strip()))
        ):
            raise ValueError("carrier medication snapshot is invalid")
    return carrier


def _query_for_identification(identification: GuideRuntimeRequestIdentificationPort) -> SensitiveText:
    strength = identification.strength_text_snapshot
    query = SensitiveText(
        identification.medication_name_snapshot
        if strength is None
        else f"{identification.medication_name_snapshot} {strength}"
    )
    validate_query_text(query)
    return query


def _execution_binding(carrier: GuideRuntimeRequestCarrierPort) -> EvidenceSearchExecutionBinding:
    binding = carrier.retrieval_binding
    source_ids = tuple(source.source_snapshot_id for source in carrier.bundle_sources if source.selected_for_operation)
    member_ids = tuple(member.source_snapshot_member_id for member in binding.member_bindings)
    if not source_ids or not member_ids:
        raise ValueError("carrier retrieval scope is empty")
    return EvidenceSearchExecutionBinding(
        filter_snapshot_ref=_artifact_ref(binding.filter_snapshot_ref),
        evidence_index_ref=_artifact_ref(binding.evidence_index_ref),
        knowledge_index_id=binding.knowledge_index_id,
        allowed_source_snapshot_ids=source_ids,
        allowed_source_snapshot_member_ids=member_ids,
        retrieval_config=_restore_retrieval_configuration(binding.retrieval_configuration),
    )


def _guard_is_usable(observation: object, carrier: GuideRuntimeRequestCarrierPort) -> bool:
    return (
        type(observation) is RequestGuardRuntimeBindingObservation
        and observation.actual_decision_outcome is RequestAuthorityDecisionOutcome.PASS
        and observation.decision_stage is RequestAuthorityDecisionStage.REQUEST
        and observation.bundle_id == carrier.runtime_release_bundle_id
        and observation.bundle_manifest_hash == carrier.runtime_release_bundle_manifest_hash
    )


async def _selected_authority(
    *,
    selected_hits: tuple[ProductionSearchHit, ...],
    request_guard_ref: ImmutableArtifactRef,
    guard: RequestGuardRuntimeBindingObservation,
    dependencies: GuideMedicationGuidanceRetrievalDependencies,
) -> tuple[SyncGuideEvidenceAuthoritySelection, ...] | None:
    selections: list[SyncGuideEvidenceAuthoritySelection] = []
    seen: set[tuple[UUID, UUID]] = set()
    for hit in selected_hits:
        provenance = hit.provenance
        key = (provenance.source_snapshot_id, provenance.source_snapshot_member_id)
        if key in seen:
            continue
        seen.add(key)
        try:
            restored: (
                GuideRequestAuthoritySelectedMember | None
            ) = await dependencies.selected_member_resolver.resolve_selected_member(
                request_guard_ref=request_guard_ref,
                user_id=guard.user_id,
                request_operation_code=guard.request_operation_code,
                source_snapshot_id=provenance.source_snapshot_id,
                source_snapshot_member_id=provenance.source_snapshot_member_id,
                expected_decision_outcome=ObservedDecisionOutcome.PASS,
            )
        except Exception:
            return None
        if restored is None:
            return ()
        selections.append(
            SyncGuideEvidenceAuthoritySelection(
                source_snapshot_id=restored.source_snapshot_id,
                source_snapshot_member_id=restored.source_snapshot_member_id,
                source_code=restored.source_code,
                source_version=restored.source_version,
                member_identity=restored.member_identity,
                request_source_decision_ref=restored.request_source_decision_ref,
                request_member_decision_ref=restored.request_member_decision_ref,
            )
        )
    return tuple(selections)


async def _prepare_carrier(
    request: GuideMedicationGuidanceRetrievalRequest,
    dependencies: GuideMedicationGuidanceRetrievalDependencies,
) -> (
    tuple[GuideRuntimeRequestCarrierPort, EvidenceSearchExecutionBinding, RequestGuardRuntimeBindingObservation]
    | GuideMedicationGuidanceRetrievalReason
):
    try:
        carrier = _validated_carrier(request.runtime_request)
        binding = _execution_binding(carrier)
        guard = await dependencies.request_guard_runtime_binding_reader.read_exact(
            carrier.request_guard_runtime_binding_ref
        )
    except Exception:
        return GuideMedicationGuidanceRetrievalReason.REQUEST_GUARD_BINDING_UNAVAILABLE
    if not _guard_is_usable(guard, carrier):
        return GuideMedicationGuidanceRetrievalReason.REQUEST_GUARD_BINDING_REJECTED
    assert isinstance(guard, RequestGuardRuntimeBindingObservation)
    return carrier, binding, guard


async def _retrieve_one_medication(
    carrier: GuideRuntimeRequestCarrierPort,
    identification: GuideRuntimeRequestIdentificationPort,
    execution_binding: EvidenceSearchExecutionBinding,
    guard: RequestGuardRuntimeBindingObservation,
    dependencies: GuideMedicationGuidanceRetrievalDependencies,
) -> MedicationGuidanceRetrieval | GuideMedicationGuidanceRetrievalReason:
    try:
        query = _query_for_identification(identification)
        fingerprint = dependencies.query_fingerprint_producer.produce(query)
        verification = dependencies.query_binding_verifier.verify(query, fingerprint)
    except GuideQueryFingerprintDependencyError:
        return GuideMedicationGuidanceRetrievalReason.QUERY_BINDING_UNAVAILABLE
    except Exception:
        return GuideMedicationGuidanceRetrievalReason.QUERY_BINDING_REJECTED
    if type(verification) is not QueryBindingVerificationSuccess or verification.query_fingerprint != fingerprint:
        return GuideMedicationGuidanceRetrievalReason.QUERY_BINDING_REJECTED
    hybrid = await execute_hybrid_retrieve(
        HybridRetrieveRequest(
            job_id=carrier.job_id,
            execution_context_id=carrier.execution_context_id,
            prescription_version_id=carrier.prescription_version_id,
            runtime_release_bundle_id=carrier.runtime_release_bundle_id,
            runtime_release_bundle_manifest_hash=carrier.runtime_release_bundle_manifest_hash,
            runtime_execution_manifest_id=carrier.runtime_execution_manifest_id,
            runtime_execution_manifest_hash=carrier.runtime_execution_manifest_hash,
            runtime_guard_decision_ref=carrier.runtime_guard_decision_ref,
            search_request=EvidenceSearchRequest(query, fingerprint, execution_binding, None),
            source_manifest_hash=carrier.retrieval_binding.source_manifest_hash,
            node_id=medication_hybrid_retrieve_node_id(identification.prescription_version_medication_id),
        ),
        search_port=dependencies.search_port,
        text_embedding_port=dependencies.text_embedding_port,
        run_store=dependencies.run_store,
        eligibility_verifier=dependencies.eligibility_verifier,
    )
    bound = project_hybrid_retrieval_for_guide_composition(hybrid)
    if bound.decision is not GuideRetrievalOutcomeBindingDecision.READY or bound.retrieval_outcome is None:
        return GuideMedicationGuidanceRetrievalReason.RETRIEVAL_BLOCKED
    selected_hits = hybrid.gate_outcome.selected_hits if type(hybrid.gate_outcome) is EvidenceGateSuccess else ()
    request_guard_ref = worker_artifact_ref(guard.legacy_request_authority_ref)
    selections = await _selected_authority(
        selected_hits=selected_hits, request_guard_ref=request_guard_ref, guard=guard, dependencies=dependencies
    )
    if selections is None:
        return GuideMedicationGuidanceRetrievalReason.SELECTED_MEMBER_AUTHORITY_ERROR
    if not selections:
        return GuideMedicationGuidanceRetrievalReason.SELECTED_MEMBER_NOT_FOUND
    authority = await assemble_sync_guide_evidence_authority(
        SyncGuideEvidenceAuthorityRequest(guard.user_id, request_guard_ref, guard.request_operation_code, selections),
        dependencies.guide_evidence_authority_reader,
    )
    if authority.decision is not SyncGuideEvidenceAuthorityDecision.AUTHENTICATED:
        return GuideMedicationGuidanceRetrievalReason.AUTHORITY_REJECTED
    composition = compose_guide_authority_with_production_retrieval(
        authority_outcome=authority, retrieval_outcome=bound.retrieval_outcome
    )
    if composition.decision is not GuideRetrievalCompositionDecision.AUTHENTICATED:
        return GuideMedicationGuidanceRetrievalReason.COMPOSITION_REJECTED
    return MedicationGuidanceRetrieval(identification.prescription_version_medication_id, hybrid, composition)


async def retrieve_medication_guidance(
    request: GuideMedicationGuidanceRetrievalRequest,
    *,
    dependencies: GuideMedicationGuidanceRetrievalDependencies,
) -> GuideMedicationGuidanceRetrievalOutcome:
    """Run #178 once per pinned medication, then exact-resolve #672/#697 authority."""
    if (
        type(request) is not GuideMedicationGuidanceRetrievalRequest
        or type(dependencies) is not GuideMedicationGuidanceRetrievalDependencies
    ):
        return _blocked(GuideMedicationGuidanceRetrievalReason.REQUEST_INVALID)
    prepared = await _prepare_carrier(request, dependencies)
    if isinstance(prepared, GuideMedicationGuidanceRetrievalReason):
        return _blocked(prepared)
    carrier, execution_binding, guard = prepared
    completed: list[MedicationGuidanceRetrieval] = []
    for identification in carrier.identifications:
        medication = await _retrieve_one_medication(carrier, identification, execution_binding, guard, dependencies)
        if isinstance(medication, GuideMedicationGuidanceRetrievalReason):
            return _blocked(medication)
        completed.append(medication)
    return GuideMedicationGuidanceRetrievalOutcome(
        GuideMedicationGuidanceRetrievalDecision.READY, None, tuple(completed)
    )
