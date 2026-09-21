from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import cast
from uuid import UUID

import pytest

import ai_worker.tasks.rag.guide_medication_guidance_retrieval as subject
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.evidence_search import (
    FractionReceipt,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    RetrievalExecutionMode,
    StableCoordinate,
    VersionedDenseSearchConfiguration,
    VersionedEvidenceRetrievalConfiguration,
    VersionedLexicalSearchConfiguration,
    project_versioned_evidence_retrieval_configuration,
)
from ai_worker.tasks.rag.guide_evidence_authority import (
    AuthoritativeMemberDecisionObservation,
    AuthoritativeRequestGuardObservation,
    AuthoritativeSourceDecisionObservation,
    GuideRequestAuthoritySelectedMember,
)
from ai_worker.tasks.rag.guide_evidence_handoff import ObservedDecisionOutcome, RequestDecisionStage
from ai_worker.tasks.rag.production_evidence_gate import EvidenceGateReason, EvidenceGateSuccess
from ai_worker.tasks.rag.request_authority_artifact import (
    compute_request_guard_authority_ref as worker_guard_ref,
)
from ai_worker.tasks.rag.request_authority_artifact import (
    compute_request_member_decision_authority_ref as worker_member_ref,
)
from ai_worker.tasks.rag.request_authority_artifact import (
    compute_request_source_decision_authority_ref as worker_source_ref,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    HybridRetrieveOutcome,
    ProductionSearchReceipt,
    RetrievalExecutionStatus,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberIdentity, SourceMemberKind
from rag_runtime.guide_query_binding import (
    ApprovedGuideQueryHmacKey,
    GuideQueryFingerprintProducer,
    build_production_query_binding_verifier,
)
from rag_runtime.guide_retrieval_binding import (
    GuideRetrievalArtifactRef,
    GuideRetrievalMemberBinding,
    GuideRetrievalSourceMember,
    build_guide_retrieval_binding_manifest,
)
from rag_runtime.request_authority import (
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
    compute_request_guard_authority_ref,
)
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    canonical_scope_manifest_hash,
    compute_request_guard_runtime_binding_ref,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode

_USER = UUID("18010000-0000-4000-8000-000000000001")
_BUNDLE = UUID("18010000-0000-4000-8000-000000000002")
_MANIFEST = UUID("18010000-0000-4000-8000-000000000003")
_INDEX = UUID("18010000-0000-4000-8000-000000000004")
_SNAPSHOT = UUID("18010000-0000-4000-8000-000000000005")
_MEMBER = UUID("18010000-0000-4000-8000-000000000006")
_MEDICATION = UUID("18010000-0000-4000-8000-000000000007")
_JOB = UUID("18010000-0000-4000-8000-000000000008")
_EXECUTION = UUID("18010000-0000-4000-8000-000000000009")
_PRESCRIPTION = UUID("18010000-0000-4000-8000-000000000010")
_CHUNK = UUID("18010000-0000-4000-8000-000000000011")
_OPERATION = "GUIDE_SYNC_ANSWER"
_IDENTITY = SourceMemberIdentity(member_kind=SourceMemberKind.ENDPOINT_OPERATION, endpoint_code="MFDS_DUR")


class _Keys:
    def active_key_version(self) -> str:
        return "guide-query-hmac-key@1"

    def key_for_version(self, key_version: str) -> ApprovedGuideQueryHmacKey | None:
        return ApprovedGuideQueryHmacKey(b"test-key") if key_version == self.active_key_version() else None


@dataclass(frozen=True)
class _Identification:
    prescription_version_medication_id: UUID
    medication_name_snapshot: str
    strength_text_snapshot: str | None


@dataclass(frozen=True)
class _Source:
    source_snapshot_id: UUID
    selected_for_operation: bool


@dataclass(frozen=True)
class _Carrier:
    job_id: UUID
    execution_context_id: UUID
    prescription_version_id: UUID
    runtime_release_bundle_id: UUID
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    runtime_guard_decision_ref: str
    request_guard_runtime_binding_ref: object
    identifications: tuple[_Identification, ...]
    bundle_sources: tuple[_Source, ...]
    retrieval_binding: object


def _ref(code: str, digest: str = "a" * 64) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(code, "1.0", digest)


def _configuration() -> VersionedEvidenceRetrievalConfiguration:
    lexical = VersionedLexicalSearchConfiguration(_ref("lexical", "0" * 64))
    lexical = replace(lexical, artifact_ref=_ref("lexical", lexical.compute_canonical_hash()))
    dense = VersionedDenseSearchConfiguration(_ref("dense", "0" * 64))
    dense = replace(dense, artifact_ref=_ref("dense", dense.compute_canonical_hash()))
    config = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=_ref("retrieval", "0" * 64),
        lexical_config=lexical,
        dense_config=dense,
        expected_query_embedding_adapter_ref=_ref("embedding"),
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
    )
    return replace(config, artifact_ref=_ref("retrieval", config.compute_canonical_hash()))


def _carrier_and_guard() -> tuple[_Carrier, RequestGuardRuntimeBindingObservation]:
    configuration = _configuration()
    binding = build_guide_retrieval_binding_manifest(
        runtime_release_bundle_id=_BUNDLE,
        runtime_release_bundle_manifest_hash="b" * 64,
        runtime_execution_manifest_id=_MANIFEST,
        runtime_execution_manifest_hash="c" * 64,
        knowledge_index_id=_INDEX,
        evidence_index_ref=GuideRetrievalArtifactRef("guide-index", "1.0", "d" * 64),
        member_bindings=(GuideRetrievalMemberBinding(_SNAPSHOT, _MEMBER),),
        retrieval_configuration=project_versioned_evidence_retrieval_configuration(configuration),
        source_members=(
            GuideRetrievalSourceMember(
                _SNAPSHOT, "KNOWLEDGE", "2026.09", "e" * 64, "approval-v1", "f" * 64, "1" * 64, True, True
            ),
        ),
    )
    guard_ref = compute_request_guard_authority_ref(
        user_id=_USER, request_operation_code=_OPERATION, decision_stage=RequestAuthorityDecisionStage.REQUEST
    )
    guard = RequestGuardRuntimeBindingObservation(
        request_guard_decision_id=UUID("18010000-0000-4000-8000-000000000012"),
        actual_decision_outcome=RequestAuthorityDecisionOutcome.PASS,
        user_id=_USER,
        request_operation_code=_OPERATION,
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
        environment=RuntimeEnvironmentCode.LOCAL,
        bundle_id=_BUNDLE,
        bundle_manifest_hash="b" * 64,
        request_scope_codes=("GUIDE",),
        scope_manifest_hash=canonical_scope_manifest_hash(("GUIDE",)),
        legacy_request_authority_ref=guard_ref,
    )
    return _Carrier(
        _JOB,
        _EXECUTION,
        _PRESCRIPTION,
        _BUNDLE,
        "b" * 64,
        _MANIFEST,
        "c" * 64,
        "runtime-guard",
        compute_request_guard_runtime_binding_ref(guard),
        (_Identification(_MEDICATION, "아세트아미노펜", "500mg"),),
        (_Source(_SNAPSHOT, True),),
        binding,
    ), guard


def _hit() -> ProductionSearchHit:
    provenance = ProductionEvidenceProvenance(
        knowledge_index_id=_INDEX,
        index_code="guide-index",
        index_version="1.0",
        index_configuration_hash="d" * 64,
        knowledge_chunk_id=_CHUNK,
        evidence_key="test:guide-medication-evidence-key",
        source_snapshot_id=_SNAPSHOT,
        source_snapshot_member_id=_MEMBER,
        source_code="MFDS",
        source_version="2026.09",
        canonical_checksum="e" * 64,
        external_document_id="DOC-1",
        chunk_index=0,
        locator="doc:1",
        content_hash=hashlib.sha256(b"chunk").hexdigest(),
        canonicalization_spec_version="v1",
        normalization_version="v1",
    )
    return ProductionSearchHit(
        provenance,
        StableCoordinate("MFDS", "2026.09", "DOC-1", 0),
        False,
        None,
        None,
        None,
        None,
        None,
        1,
        FractionReceipt("1", "60"),
        True,
    )


class _GuardReader:
    def __init__(self, guard: RequestGuardRuntimeBindingObservation) -> None:
        self.guard = guard

    async def read_exact(self, reference: object) -> RequestGuardRuntimeBindingObservation:
        return self.guard


class _Resolver:
    async def resolve_selected_member(self, **_: object) -> GuideRequestAuthoritySelectedMember:
        guard_ref = worker_guard_ref(
            user_id=_USER, request_operation_code=_OPERATION, decision_stage=RequestDecisionStage.REQUEST
        )
        source_ref = worker_source_ref(
            request_guard_ref=guard_ref,
            user_id=_USER,
            request_operation_code=_OPERATION,
            decision_stage=RequestDecisionStage.REQUEST,
            source_snapshot_id=_SNAPSHOT,
            source_code="MFDS",
            source_version="2026.09",
            actual_decision_outcome=ObservedDecisionOutcome.PASS,
        )
        member_ref = worker_member_ref(
            request_guard_ref=guard_ref,
            user_id=_USER,
            request_operation_code=_OPERATION,
            decision_stage=RequestDecisionStage.REQUEST,
            source_snapshot_id=_SNAPSHOT,
            source_snapshot_member_id=_MEMBER,
            member_identity=_IDENTITY,
            actual_decision_outcome=ObservedDecisionOutcome.PASS,
        )
        return GuideRequestAuthoritySelectedMember(
            guard_ref,
            _USER,
            _OPERATION,
            _SNAPSHOT,
            _MEMBER,
            "MFDS",
            "2026.09",
            _IDENTITY,
            source_ref,
            member_ref,
        )


class _AuthorityReader:
    async def read_request_guard(
        self, *, request_guard_ref: ImmutableArtifactRef
    ) -> AuthoritativeRequestGuardObservation:
        return AuthoritativeRequestGuardObservation(request_guard_ref, _USER, _OPERATION, "REQUEST")

    async def read_source_decision(
        self, *, request_source_decision_ref: ImmutableArtifactRef
    ) -> AuthoritativeSourceDecisionObservation:
        return AuthoritativeSourceDecisionObservation(
            request_source_decision_ref,
            worker_guard_ref(
                user_id=_USER, request_operation_code=_OPERATION, decision_stage=RequestDecisionStage.REQUEST
            ),
            _USER,
            _OPERATION,
            "REQUEST",
            _SNAPSHOT,
            "MFDS",
            "2026.09",
            ObservedDecisionOutcome.PASS,
        )

    async def read_member_decision(
        self, *, request_member_decision_ref: ImmutableArtifactRef
    ) -> AuthoritativeMemberDecisionObservation:
        return AuthoritativeMemberDecisionObservation(
            request_member_decision_ref,
            worker_guard_ref(
                user_id=_USER, request_operation_code=_OPERATION, decision_stage=RequestDecisionStage.REQUEST
            ),
            _USER,
            _OPERATION,
            "REQUEST",
            _SNAPSHOT,
            _MEMBER,
            _IDENTITY,
            ObservedDecisionOutcome.PASS,
        )


def test_medication_hybrid_retrieve_node_id_is_scoped_to_the_pinned_medication() -> None:
    assert subject.medication_hybrid_retrieve_node_id(_MEDICATION) == f"hybrid_retrieve:medication:{_MEDICATION}"


@pytest.mark.asyncio
async def test_canonical_callable_composes_pinned_query_hybrid_retrieval_and_historical_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    carrier, guard = _carrier_and_guard()
    hit = _hit()
    calls = []

    async def _hybrid(request, **_: object) -> HybridRetrieveOutcome:
        calls.append(request)
        receipt = ProductionSearchReceipt(
            _ref("receipt"),
            "RET-H",
            RetrievalExecutionStatus.SUCCEEDED,
            EvidenceGateReason.ELIGIBLE.value,
            request.search_request.query_fingerprint,
            request.search_request.execution_binding.filter_snapshot_ref,
            request.search_request.execution_binding.evidence_index_ref,
            _ref("retrieval"),
            _ref("adapter"),
            "2" * 64,
            "3" * 64,
            "4" * 64,
            "5" * 64,
        )
        return HybridRetrieveOutcome(RetrievalExecutionStatus.SUCCEEDED, None, receipt, EvidenceGateSuccess((hit,)))

    monkeypatch.setattr(subject, "execute_hybrid_retrieve", _hybrid)
    keys = _Keys()
    outcome = await subject.retrieve_medication_guidance(
        subject.GuideMedicationGuidanceRetrievalRequest(cast(subject.GuideRuntimeRequestCarrierPort, carrier)),
        dependencies=subject.GuideMedicationGuidanceRetrievalDependencies(
            GuideQueryFingerprintProducer(keys),
            build_production_query_binding_verifier(keys),
            _GuardReader(guard),
            _Resolver(),
            _AuthorityReader(),
            cast(subject.EvidenceSearchPort, object()),
            None,
            cast(subject.RetrievalRunStorePort, object()),
            cast(subject.ProductionEvidenceEligibilityVerifierPort, object()),
        ),
    )

    assert outcome.decision is subject.GuideMedicationGuidanceRetrievalDecision.READY
    assert len(outcome.medications) == 1
    assert outcome.medications[0].composition.selections[0].hit is hit
    assert calls[0].node_id == f"hybrid_retrieve:medication:{_MEDICATION}"
    assert calls[0].search_request.normalized_query.reveal() == "아세트아미노펜 500mg"
