"""Provisional, persistence-free Evidence Gate for selected Knowledge Evidence.

This module does not establish Source, Bundle, or operation eligibility by
itself.  A caller must provide an ``EvidenceEligibilityVerifierPort`` backed
by the appropriate authority.  No production verifier exists in this slice.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Protocol, Self

from ai_worker.tasks.rag.evidence_retrieval import (
    CanonicalScore,
    EvidenceSearchStage,
    ImmutableArtifactRef,
    KnowledgeEvidenceCandidate,
    KnowledgeEvidenceProvenance,
    QueryFingerprint,
    SensitiveText,
    StageSignal,
    UntrustedKnowledgeEvidenceSelection,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_SCORE_RE = re.compile(r"^(?:0|-?[1-9][0-9]*|-?(?:0|[1-9][0-9]*)\.[0-9]*[1-9])$")
RERANK_OUTPUT_PROJECTION_VERSION = "evidence-rerank-output-v1"


class EvidenceAssessmentStance(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"


class EvidenceGateExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    NO_RESULT = "NO_RESULT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


class EvidenceStatus(StrEnum):
    SUFFICIENT = "SUFFICIENT"
    INSUFFICIENT = "INSUFFICIENT"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"


class EvidenceGateReason(StrEnum):
    EVIDENCE_SUFFICIENT = "EVIDENCE_SUFFICIENT"
    EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
    EVIDENCE_CONFLICTED = "EVIDENCE_CONFLICTED"
    EVIDENCE_STALE = "EVIDENCE_STALE"
    EVIDENCE_INELIGIBLE = "EVIDENCE_INELIGIBLE"
    ELIGIBILITY_RECEIPT_MISMATCH = "ELIGIBILITY_RECEIPT_MISMATCH"
    RETRIEVAL_RECEIPT_MISMATCH = "RETRIEVAL_RECEIPT_MISMATCH"
    ELIGIBILITY_VERIFICATION_ERROR = "ELIGIBILITY_VERIFICATION_ERROR"
    REQUEST_INVALID = "REQUEST_INVALID"


@dataclass(frozen=True, slots=True)
class EvidenceGateRetrievalReceipt:
    artifact_ref: ImmutableArtifactRef
    query_fingerprint: QueryFingerprint
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    retrieval_config_ref: ImmutableArtifactRef
    rerank_config_ref: ImmutableArtifactRef
    rerank_input_projection_version: str
    input_set_hash: str
    rerank_output_projection_version: str
    rerank_output_hash: str

    @classmethod
    def create(
        cls,
        artifact_code: str,
        version: str,
        *,
        query_fingerprint: QueryFingerprint,
        filter_snapshot_ref: ImmutableArtifactRef,
        evidence_index_ref: ImmutableArtifactRef,
        retrieval_config_ref: ImmutableArtifactRef,
        rerank_config_ref: ImmutableArtifactRef,
        rerank_input_projection_version: str,
        input_set_hash: str,
        rerank_output_projection_version: str,
        rerank_output_hash: str,
    ) -> Self:
        payload = {
            "evidence_index_ref": _artifact_payload(evidence_index_ref),
            "filter_snapshot_ref": _artifact_payload(filter_snapshot_ref),
            "input_set_hash": input_set_hash,
            "query_fingerprint": _fingerprint_payload(query_fingerprint),
            "rerank_config_ref": _artifact_payload(rerank_config_ref),
            "rerank_input_projection_version": rerank_input_projection_version,
            "rerank_output_hash": rerank_output_hash,
            "rerank_output_projection_version": rerank_output_projection_version,
            "retrieval_config_ref": _artifact_payload(retrieval_config_ref),
        }
        return cls(
            ImmutableArtifactRef(artifact_code, version, _canonical_sha256(payload)),
            query_fingerprint,
            filter_snapshot_ref,
            evidence_index_ref,
            retrieval_config_ref,
            rerank_config_ref,
            rerank_input_projection_version,
            input_set_hash,
            rerank_output_projection_version,
            rerank_output_hash,
        )


@dataclass(frozen=True, slots=True)
class EvidenceEligibilityVerificationSuccess:
    assessment_artifact_ref: ImmutableArtifactRef
    eligibility_receipt_ref: ImmutableArtifactRef
    selection_projection_hash: str
    retrieval_receipt_ref: ImmutableArtifactRef
    verifier_artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class EvidenceEligibilityVerificationFailure:
    pass


class EvidenceEligibilityVerifierPort(Protocol):
    def verify(
        self,
        assessment: EvidenceGateAssessment,
        retrieval_receipt: EvidenceGateRetrievalReceipt,
    ) -> EvidenceEligibilityVerificationSuccess | EvidenceEligibilityVerificationFailure: ...


@dataclass(frozen=True, slots=True)
class EvidenceGateAssessment:
    assessment_artifact_ref: ImmutableArtifactRef
    selection_projection_hash: str
    evidence_key: str
    coverage_key: str
    stance: EvidenceAssessmentStance
    evidence_index_ref: ImmutableArtifactRef
    source_snapshot_ref: ImmutableArtifactRef
    content_sha256: str
    retrieval_receipt_ref: ImmutableArtifactRef
    eligibility_receipt_ref: ImmutableArtifactRef
    valid_from: datetime
    valid_until: datetime

    @classmethod
    def create(
        cls,
        artifact_code: str,
        version: str,
        *,
        selection: UntrustedKnowledgeEvidenceSelection,
        coverage_key: str,
        stance: EvidenceAssessmentStance,
        retrieval_receipt_ref: ImmutableArtifactRef,
        eligibility_receipt_ref: ImmutableArtifactRef,
        valid_from: datetime,
        valid_until: datetime,
    ) -> Self:
        provenance = selection.candidate.provenance
        selection_projection_hash = canonical_gate_selection_hash(selection)
        payload = {
            "content_sha256": provenance.content_sha256,
            "coverage_key": coverage_key,
            "eligibility_receipt_ref": _artifact_payload(eligibility_receipt_ref),
            "evidence_index_ref": _artifact_payload(provenance.evidence_index_ref),
            "evidence_key": provenance.evidence_key,
            "retrieval_receipt_ref": _artifact_payload(retrieval_receipt_ref),
            "selection_projection_hash": selection_projection_hash,
            "source_snapshot_ref": _artifact_payload(provenance.source_snapshot_ref),
            "stance": stance.value,
            "valid_from": _canonical_datetime(valid_from),
            "valid_until": _canonical_datetime(valid_until),
        }
        return cls(
            ImmutableArtifactRef(artifact_code, version, _canonical_sha256(payload)),
            selection_projection_hash,
            provenance.evidence_key,
            coverage_key,
            stance,
            provenance.evidence_index_ref,
            provenance.source_snapshot_ref,
            provenance.content_sha256,
            retrieval_receipt_ref,
            eligibility_receipt_ref,
            valid_from,
            valid_until,
        )


@dataclass(frozen=True, slots=True)
class VersionedEvidenceGatePolicy:
    artifact_ref: ImmutableArtifactRef
    minimum_supporting_items_per_coverage: int
    minimum_distinct_source_snapshots_per_coverage: int

    @classmethod
    def create(
        cls,
        artifact_code: str,
        version: str,
        *,
        minimum_supporting_items_per_coverage: int,
        minimum_distinct_source_snapshots_per_coverage: int,
    ) -> Self:
        payload = {
            "conflict_strategy": "any-current-opposing-stance-v1",
            "freshness_strategy": "valid-from-inclusive-valid-until-exclusive-v1",
            "minimum_distinct_source_snapshots_per_coverage": minimum_distinct_source_snapshots_per_coverage,
            "minimum_supporting_items_per_coverage": minimum_supporting_items_per_coverage,
        }
        content_sha256 = _canonical_sha256(payload)
        return cls(
            ImmutableArtifactRef(artifact_code, version, content_sha256),
            minimum_supporting_items_per_coverage,
            minimum_distinct_source_snapshots_per_coverage,
        )


@dataclass(frozen=True, slots=True)
class EvidenceGateRequest:
    selections: tuple[UntrustedKnowledgeEvidenceSelection, ...]
    assessments: tuple[EvidenceGateAssessment, ...]
    retrieval_receipt: EvidenceGateRetrievalReceipt
    required_coverage_keys: tuple[str, ...]
    evaluated_at: datetime
    policy: VersionedEvidenceGatePolicy


@dataclass(frozen=True, slots=True)
class EvidenceGateTrace:
    policy_ref: ImmutableArtifactRef
    retrieval_receipt_ref: ImmutableArtifactRef
    evaluated_at: datetime
    assessment_artifact_refs: tuple[ImmutableArtifactRef, ...]
    selected_evidence_keys: tuple[str, ...]
    stale_evidence_keys: tuple[str, ...] = ()
    conflicting_coverage_keys: tuple[str, ...] = ()
    insufficient_coverage_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GatePassedKnowledgeEvidenceSelection:
    selection: UntrustedKnowledgeEvidenceSelection
    assessment_artifact_ref: ImmutableArtifactRef
    eligibility_receipt_ref: ImmutableArtifactRef
    retrieval_receipt_ref: ImmutableArtifactRef
    verifier_artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class EvidenceGateOutcome:
    execution_status: EvidenceGateExecutionStatus
    evidence_status: EvidenceStatus | None
    reason: EvidenceGateReason
    gate_passed_selections: tuple[GatePassedKnowledgeEvidenceSelection, ...] = ()
    trace: EvidenceGateTrace | None = None


def evaluate_evidence_gate(
    request: EvidenceGateRequest,
    *,
    eligibility_verifier: EvidenceEligibilityVerifierPort,
) -> EvidenceGateOutcome:
    """Evaluate pre-composer sufficiency, conflict, and freshness signals."""
    if not _is_valid_request(request):
        return EvidenceGateOutcome(
            EvidenceGateExecutionStatus.VALIDATION_ERROR,
            None,
            EvidenceGateReason.REQUEST_INVALID,
        )
    request_snapshot = _detached_request_snapshot(request)
    if request_snapshot is None:
        return EvidenceGateOutcome(
            EvidenceGateExecutionStatus.DEPENDENCY_ERROR,
            None,
            EvidenceGateReason.ELIGIBILITY_VERIFICATION_ERROR,
        )
    request = request_snapshot

    assessments_by_key = {item.evidence_key: item for item in request.assessments}
    ordered_selections = tuple(
        sorted(
            request.selections,
            key=lambda item: (item.rerank_rank, item.candidate.provenance.evidence_key.encode()),
        )
    )
    selected_evidence_keys = tuple(item.candidate.provenance.evidence_key for item in ordered_selections)
    verification_result = _verify_eligibility(request, eligibility_verifier, selected_evidence_keys)
    if isinstance(verification_result, EvidenceGateOutcome):
        return verification_result
    verification_by_assessment = verification_result
    stale_evidence_keys = tuple(
        sorted(
            (
                item.evidence_key
                for item in request.assessments
                if not (item.valid_from <= request.evaluated_at < item.valid_until)
            ),
            key=str.encode,
        )
    )
    if stale_evidence_keys:
        return EvidenceGateOutcome(
            EvidenceGateExecutionStatus.NO_RESULT,
            EvidenceStatus.STALE,
            EvidenceGateReason.EVIDENCE_STALE,
            trace=_trace(
                request,
                selected_evidence_keys,
                stale_evidence_keys=stale_evidence_keys,
            ),
        )

    stances_by_coverage: dict[str, set[EvidenceAssessmentStance]] = {}
    for item in request.assessments:
        stances_by_coverage.setdefault(item.coverage_key, set()).add(item.stance)
    conflicting_coverage_keys = tuple(
        sorted(
            (
                coverage_key
                for coverage_key, stances in stances_by_coverage.items()
                if EvidenceAssessmentStance.SUPPORTS in stances and EvidenceAssessmentStance.CONTRADICTS in stances
            ),
            key=str.encode,
        )
    )
    if conflicting_coverage_keys:
        return EvidenceGateOutcome(
            EvidenceGateExecutionStatus.NO_RESULT,
            EvidenceStatus.CONFLICTED,
            EvidenceGateReason.EVIDENCE_CONFLICTED,
            trace=_trace(
                request,
                selected_evidence_keys,
                conflicting_coverage_keys=conflicting_coverage_keys,
            ),
        )

    insufficient: list[str] = []
    for coverage_key in request.required_coverage_keys:
        supporting = tuple(
            item
            for item in request.assessments
            if item.coverage_key == coverage_key and item.stance is EvidenceAssessmentStance.SUPPORTS
        )
        if (
            len(supporting) < request.policy.minimum_supporting_items_per_coverage
            or len({item.source_snapshot_ref for item in supporting})
            < request.policy.minimum_distinct_source_snapshots_per_coverage
        ):
            insufficient.append(coverage_key)
    insufficient_coverage_keys = tuple(sorted(insufficient, key=str.encode))
    if insufficient_coverage_keys:
        return EvidenceGateOutcome(
            EvidenceGateExecutionStatus.NO_RESULT,
            EvidenceStatus.INSUFFICIENT,
            EvidenceGateReason.EVIDENCE_INSUFFICIENT,
            trace=_trace(
                request,
                selected_evidence_keys,
                insufficient_coverage_keys=insufficient_coverage_keys,
            ),
        )

    gate_passed = tuple(
        GatePassedKnowledgeEvidenceSelection(
            item,
            assessment.assessment_artifact_ref,
            assessment.eligibility_receipt_ref,
            request.retrieval_receipt.artifact_ref,
            verification_by_assessment[assessment.assessment_artifact_ref].verifier_artifact_ref,
        )
        for item in ordered_selections
        if (assessment := assessments_by_key[item.candidate.provenance.evidence_key]).stance
        is EvidenceAssessmentStance.SUPPORTS
    )
    return EvidenceGateOutcome(
        EvidenceGateExecutionStatus.SUCCEEDED,
        EvidenceStatus.SUFFICIENT,
        EvidenceGateReason.EVIDENCE_SUFFICIENT,
        gate_passed,
        _trace(request, selected_evidence_keys),
    )


def _detached_request_snapshot(request: EvidenceGateRequest) -> EvidenceGateRequest | None:
    try:
        snapshot = deepcopy(request)
        if not _is_valid_request(snapshot) or not _request_snapshot_matches(request, snapshot):
            return None
        return snapshot
    except Exception:
        return None


def _request_snapshot_matches(original: EvidenceGateRequest, snapshot: EvidenceGateRequest) -> bool:
    return (
        original.assessments == snapshot.assessments
        and original.retrieval_receipt == snapshot.retrieval_receipt
        and original.required_coverage_keys == snapshot.required_coverage_keys
        and original.evaluated_at == snapshot.evaluated_at
        and original.policy == snapshot.policy
        and len(original.selections) == len(snapshot.selections)
        and all(
            canonical_gate_selection_hash(source) == canonical_gate_selection_hash(copied)
            and source.candidate.content_text.reveal() == copied.candidate.content_text.reveal()
            for source, copied in zip(original.selections, snapshot.selections, strict=True)
        )
    )


def _verify_eligibility(
    request: EvidenceGateRequest,
    verifier: EvidenceEligibilityVerifierPort,
    selected_evidence_keys: tuple[str, ...],
) -> dict[ImmutableArtifactRef, EvidenceEligibilityVerificationSuccess] | EvidenceGateOutcome:
    verified: dict[ImmutableArtifactRef, EvidenceEligibilityVerificationSuccess] = {}
    for assessment in sorted(request.assessments, key=lambda item: item.evidence_key.encode()):
        try:
            verifier_input = deepcopy(assessment)
            retrieval_receipt_input = deepcopy(request.retrieval_receipt)
            response = deepcopy(verifier.verify(verifier_input, retrieval_receipt_input))
        except Exception:
            return EvidenceGateOutcome(
                EvidenceGateExecutionStatus.DEPENDENCY_ERROR,
                None,
                EvidenceGateReason.ELIGIBILITY_VERIFICATION_ERROR,
            )
        if (
            verifier_input != assessment
            or not _is_valid_assessment(verifier_input)
            or retrieval_receipt_input != request.retrieval_receipt
            or not _is_valid_retrieval_receipt(retrieval_receipt_input)
        ):
            return EvidenceGateOutcome(
                EvidenceGateExecutionStatus.DEPENDENCY_ERROR,
                None,
                EvidenceGateReason.ELIGIBILITY_VERIFICATION_ERROR,
            )
        if type(response) is EvidenceEligibilityVerificationFailure:
            return EvidenceGateOutcome(
                EvidenceGateExecutionStatus.NO_RESULT,
                EvidenceStatus.INSUFFICIENT,
                EvidenceGateReason.EVIDENCE_INELIGIBLE,
                trace=_trace(request, selected_evidence_keys),
            )
        if type(response) is not EvidenceEligibilityVerificationSuccess:
            return EvidenceGateOutcome(
                EvidenceGateExecutionStatus.DEPENDENCY_ERROR,
                None,
                EvidenceGateReason.ELIGIBILITY_RECEIPT_MISMATCH,
            )
        if not _retrieval_receipt_ref_matches(response, request.retrieval_receipt):
            return EvidenceGateOutcome(
                EvidenceGateExecutionStatus.DEPENDENCY_ERROR,
                None,
                EvidenceGateReason.RETRIEVAL_RECEIPT_MISMATCH,
            )
        if not _eligibility_verification_matches(response, assessment, request.retrieval_receipt):
            return EvidenceGateOutcome(
                EvidenceGateExecutionStatus.DEPENDENCY_ERROR,
                None,
                EvidenceGateReason.ELIGIBILITY_RECEIPT_MISMATCH,
            )
        verified[assessment.assessment_artifact_ref] = response
    return verified


def _is_valid_request(value: object) -> bool:
    try:
        if type(value) is not EvidenceGateRequest:
            return False
        if not _has_valid_request_shape(value):
            return False
        selections_by_key = _validated_selections(value.selections)
        if selections_by_key is None:
            return False
        if any(
            selection.candidate.provenance.evidence_index_ref != value.retrieval_receipt.evidence_index_ref
            for selection in selections_by_key.values()
        ):
            return False
        ordered_selections = tuple(sorted(selections_by_key.values(), key=lambda item: item.rerank_rank))
        if value.retrieval_receipt.rerank_output_hash != canonical_rerank_output_hash(
            value.retrieval_receipt.rerank_output_projection_version,
            ordered_selections,
        ):
            return False
        assessments_by_key = _validated_assessments(
            value.assessments,
            set(value.required_coverage_keys),
            value.retrieval_receipt.artifact_ref,
        )
        if assessments_by_key is None or assessments_by_key.keys() != selections_by_key.keys():
            return False

        return all(
            _assessment_matches_selection(assessments_by_key[evidence_key], selection)
            for evidence_key, selection in selections_by_key.items()
        )
    except Exception:
        return False


def _has_valid_request_shape(value: EvidenceGateRequest) -> bool:
    return (
        type(value.selections) is tuple
        and type(value.assessments) is tuple
        and _is_valid_retrieval_receipt(value.retrieval_receipt)
        and type(value.required_coverage_keys) is tuple
        and bool(value.required_coverage_keys)
        and _is_utc_datetime(value.evaluated_at)
        and _is_valid_policy(value.policy)
        and len(set(value.required_coverage_keys)) == len(value.required_coverage_keys)
        and all(_nonempty_nfc(item) for item in value.required_coverage_keys)
    )


def _validated_selections(
    selections: tuple[UntrustedKnowledgeEvidenceSelection, ...],
) -> dict[str, UntrustedKnowledgeEvidenceSelection] | None:
    selections_by_key: dict[str, UntrustedKnowledgeEvidenceSelection] = {}
    observed_ranks: set[int] = set()
    evidence_index_refs: set[ImmutableArtifactRef] = set()
    evidence_keys_by_chunk_ref: dict[str, str] = {}
    stage_ranks: dict[EvidenceSearchStage, set[int]] = {}
    for selection in selections:
        if not _is_valid_selection(selection):
            return None
        evidence_key = selection.candidate.provenance.evidence_key
        if evidence_key in selections_by_key or selection.rerank_rank in observed_ranks:
            return None
        selections_by_key[evidence_key] = selection
        observed_ranks.add(selection.rerank_rank)
        evidence_index_refs.add(selection.candidate.provenance.evidence_index_ref)
        chunk_ref = selection.candidate.provenance.knowledge_chunk_ref
        previous_evidence_key = evidence_keys_by_chunk_ref.setdefault(chunk_ref, evidence_key)
        if previous_evidence_key != evidence_key:
            return None
        for signal in selection.candidate.stage_signals:
            observed_stage_ranks = stage_ranks.setdefault(signal.stage, set())
            if signal.rank in observed_stage_ranks:
                return None
            observed_stage_ranks.add(signal.rank)
    if observed_ranks != set(range(1, len(observed_ranks) + 1)) or len(evidence_index_refs) > 1:
        return None
    return selections_by_key


def _validated_assessments(
    assessments: tuple[EvidenceGateAssessment, ...],
    required_coverage: set[str],
    retrieval_receipt_ref: ImmutableArtifactRef,
) -> dict[str, EvidenceGateAssessment] | None:
    assessments_by_key: dict[str, EvidenceGateAssessment] = {}
    for item in assessments:
        if (
            not _is_valid_assessment(item)
            or item.evidence_key in assessments_by_key
            or item.coverage_key not in required_coverage
            or item.retrieval_receipt_ref != retrieval_receipt_ref
        ):
            return None
        assessments_by_key[item.evidence_key] = item
    return assessments_by_key


def _is_valid_policy(value: object) -> bool:
    if (
        type(value) is not VersionedEvidenceGatePolicy
        or not _is_valid_artifact_ref(value.artifact_ref)
        or type(value.minimum_supporting_items_per_coverage) is not int
        or value.minimum_supporting_items_per_coverage <= 0
        or type(value.minimum_distinct_source_snapshots_per_coverage) is not int
        or value.minimum_distinct_source_snapshots_per_coverage <= 0
    ):
        return False
    expected = VersionedEvidenceGatePolicy.create(
        value.artifact_ref.artifact_code,
        value.artifact_ref.version,
        minimum_supporting_items_per_coverage=value.minimum_supporting_items_per_coverage,
        minimum_distinct_source_snapshots_per_coverage=value.minimum_distinct_source_snapshots_per_coverage,
    )
    return expected.artifact_ref == value.artifact_ref


def _is_valid_assessment(value: object) -> bool:
    return (
        type(value) is EvidenceGateAssessment
        and _is_valid_artifact_ref(value.assessment_artifact_ref)
        and isinstance(value.selection_projection_hash, str)
        and _SHA256_RE.fullmatch(value.selection_projection_hash) is not None
        and _nonempty_nfc(value.evidence_key)
        and _nonempty_nfc(value.coverage_key)
        and type(value.stance) is EvidenceAssessmentStance
        and _is_valid_artifact_ref(value.evidence_index_ref)
        and _is_valid_artifact_ref(value.source_snapshot_ref)
        and isinstance(value.content_sha256, str)
        and _SHA256_RE.fullmatch(value.content_sha256) is not None
        and _is_valid_artifact_ref(value.retrieval_receipt_ref)
        and _is_valid_artifact_ref(value.eligibility_receipt_ref)
        and _is_utc_datetime(value.valid_from)
        and _is_utc_datetime(value.valid_until)
        and value.valid_from < value.valid_until
        and _assessment_is_bound(value)
    )


def _assessment_is_bound(value: EvidenceGateAssessment) -> bool:
    expected = ImmutableArtifactRef(
        value.assessment_artifact_ref.artifact_code,
        value.assessment_artifact_ref.version,
        _canonical_sha256(_assessment_payload(value)),
    )
    return expected == value.assessment_artifact_ref


def _is_valid_selection(value: object) -> bool:
    if (
        type(value) is not UntrustedKnowledgeEvidenceSelection
        or type(value.candidate) is not KnowledgeEvidenceCandidate
        or type(value.candidate.provenance) is not KnowledgeEvidenceProvenance
        or type(value.candidate.content_text) is not SensitiveText
        or type(value.candidate.stage_signals) is not tuple
        or not value.candidate.stage_signals
        or type(value.rerank_rank) is not int
        or value.rerank_rank <= 0
        or not _is_valid_score(value.rerank_score)
    ):
        return False
    provenance = value.candidate.provenance
    content = value.candidate.content_text.reveal()
    if (
        not _nonempty_nfc(provenance.evidence_key)
        or not _nonempty_nfc(provenance.knowledge_chunk_ref)
        or not _is_valid_artifact_ref(provenance.evidence_index_ref)
        or not _is_valid_artifact_ref(provenance.source_snapshot_ref)
        or not _nonempty_nfc(provenance.source_version)
        or not _nonempty_nfc(provenance.locator)
        or not _nonempty_nfc(provenance.canonicalization_spec_version)
        or not isinstance(provenance.content_sha256, str)
        or _SHA256_RE.fullmatch(provenance.content_sha256) is None
        or not _nonempty_nfc(content)
        or hashlib.sha256(content.encode()).hexdigest() != provenance.content_sha256
    ):
        return False
    stages: set[EvidenceSearchStage] = set()
    for signal in value.candidate.stage_signals:
        if (
            type(signal) is not StageSignal
            or type(signal.stage) is not EvidenceSearchStage
            or signal.stage in stages
            or type(signal.rank) is not int
            or signal.rank <= 0
            or not _is_valid_score(signal.score)
            or not _score_in_stage_range(signal.stage, Decimal(signal.score.value))
        ):
            return False
        stages.add(signal.stage)
    canonical_stage_order = tuple(stage for stage in EvidenceSearchStage if stage in stages)
    return tuple(signal.stage for signal in value.candidate.stage_signals) == canonical_stage_order


def _assessment_matches_selection(
    assessment: EvidenceGateAssessment,
    selection: UntrustedKnowledgeEvidenceSelection,
) -> bool:
    provenance = selection.candidate.provenance
    return (
        assessment.evidence_key == provenance.evidence_key
        and assessment.evidence_index_ref == provenance.evidence_index_ref
        and assessment.source_snapshot_ref == provenance.source_snapshot_ref
        and assessment.content_sha256 == provenance.content_sha256
        and assessment.selection_projection_hash == canonical_gate_selection_hash(selection)
    )


def _eligibility_verification_matches(
    value: object,
    assessment: EvidenceGateAssessment,
    retrieval_receipt: EvidenceGateRetrievalReceipt,
) -> bool:
    try:
        return (
            type(value) is EvidenceEligibilityVerificationSuccess
            and value.assessment_artifact_ref == assessment.assessment_artifact_ref
            and value.eligibility_receipt_ref == assessment.eligibility_receipt_ref
            and value.selection_projection_hash == assessment.selection_projection_hash
            and value.retrieval_receipt_ref == retrieval_receipt.artifact_ref
            and _is_valid_artifact_ref(value.verifier_artifact_ref)
        )
    except Exception:
        return False


def _retrieval_receipt_ref_matches(
    value: object,
    retrieval_receipt: EvidenceGateRetrievalReceipt,
) -> bool:
    try:
        return (
            type(value) is EvidenceEligibilityVerificationSuccess
            and value.retrieval_receipt_ref == retrieval_receipt.artifact_ref
        )
    except Exception:
        return False


def _trace(
    request: EvidenceGateRequest,
    selected_evidence_keys: tuple[str, ...],
    *,
    stale_evidence_keys: tuple[str, ...] = (),
    conflicting_coverage_keys: tuple[str, ...] = (),
    insufficient_coverage_keys: tuple[str, ...] = (),
) -> EvidenceGateTrace:
    assessment_refs = tuple(
        sorted(
            (item.assessment_artifact_ref for item in request.assessments),
            key=lambda item: (item.artifact_code.encode(), item.version.encode(), item.content_sha256),
        )
    )
    return EvidenceGateTrace(
        request.policy.artifact_ref,
        request.retrieval_receipt.artifact_ref,
        request.evaluated_at,
        assessment_refs,
        selected_evidence_keys,
        stale_evidence_keys,
        conflicting_coverage_keys,
        insufficient_coverage_keys,
    )


def _is_valid_score(value: object) -> bool:
    if (
        type(value) is not CanonicalScore
        or not isinstance(value.value, str)
        or _CANONICAL_SCORE_RE.fullmatch(value.value) is None
    ):
        return False
    try:
        return Decimal(value.value).is_finite()
    except InvalidOperation:
        return False


def _score_in_stage_range(stage: EvidenceSearchStage, score: Decimal) -> bool:
    if stage is EvidenceSearchStage.LEXICAL:
        return Decimal(0) <= score <= Decimal(1)
    return Decimal(-1) <= score <= Decimal(1)


def _is_valid_artifact_ref(value: object) -> bool:
    return (
        type(value) is ImmutableArtifactRef
        and _nonempty_nfc(value.artifact_code)
        and _nonempty_nfc(value.version)
        and isinstance(value.content_sha256, str)
        and _SHA256_RE.fullmatch(value.content_sha256) is not None
    )


def _is_valid_retrieval_receipt(value: object) -> bool:
    if (
        type(value) is not EvidenceGateRetrievalReceipt
        or not _is_valid_artifact_ref(value.artifact_ref)
        or not _is_valid_query_fingerprint(value.query_fingerprint)
        or not _is_valid_artifact_ref(value.filter_snapshot_ref)
        or not _is_valid_artifact_ref(value.evidence_index_ref)
        or not _is_valid_artifact_ref(value.retrieval_config_ref)
        or not _is_valid_artifact_ref(value.rerank_config_ref)
        or not _nonempty_nfc(value.rerank_input_projection_version)
        or not isinstance(value.input_set_hash, str)
        or _SHA256_RE.fullmatch(value.input_set_hash) is None
        or value.rerank_output_projection_version != RERANK_OUTPUT_PROJECTION_VERSION
        or not isinstance(value.rerank_output_hash, str)
        or _SHA256_RE.fullmatch(value.rerank_output_hash) is None
    ):
        return False
    expected = EvidenceGateRetrievalReceipt.create(
        value.artifact_ref.artifact_code,
        value.artifact_ref.version,
        query_fingerprint=value.query_fingerprint,
        filter_snapshot_ref=value.filter_snapshot_ref,
        evidence_index_ref=value.evidence_index_ref,
        retrieval_config_ref=value.retrieval_config_ref,
        rerank_config_ref=value.rerank_config_ref,
        rerank_input_projection_version=value.rerank_input_projection_version,
        input_set_hash=value.input_set_hash,
        rerank_output_projection_version=value.rerank_output_projection_version,
        rerank_output_hash=value.rerank_output_hash,
    )
    return expected.artifact_ref == value.artifact_ref


def _is_valid_query_fingerprint(value: object) -> bool:
    return (
        type(value) is QueryFingerprint
        and _nonempty_nfc(value.algorithm)
        and _nonempty_nfc(value.key_version)
        and isinstance(value.digest, str)
        and _SHA256_RE.fullmatch(value.digest) is not None
    )


def _is_utc_datetime(value: object) -> bool:
    return type(value) is datetime and value.utcoffset() == timedelta(0)


def _nonempty_nfc(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and unicodedata.normalize("NFC", value) == value


def _artifact_payload(value: ImmutableArtifactRef) -> dict[str, str]:
    return {
        "artifact_code": value.artifact_code,
        "content_sha256": value.content_sha256,
        "version": value.version,
    }


def _fingerprint_payload(value: QueryFingerprint) -> dict[str, str]:
    return {
        "algorithm": value.algorithm,
        "digest": value.digest,
        "key_version": value.key_version,
    }


def _assessment_payload(value: EvidenceGateAssessment) -> dict[str, object]:
    return {
        "content_sha256": value.content_sha256,
        "coverage_key": value.coverage_key,
        "eligibility_receipt_ref": _artifact_payload(value.eligibility_receipt_ref),
        "evidence_index_ref": _artifact_payload(value.evidence_index_ref),
        "evidence_key": value.evidence_key,
        "retrieval_receipt_ref": _artifact_payload(value.retrieval_receipt_ref),
        "selection_projection_hash": value.selection_projection_hash,
        "source_snapshot_ref": _artifact_payload(value.source_snapshot_ref),
        "stance": value.stance.value,
        "valid_from": _canonical_datetime(value.valid_from),
        "valid_until": _canonical_datetime(value.valid_until),
    }


def canonical_gate_selection_hash(selection: UntrustedKnowledgeEvidenceSelection) -> str:
    """Hash the full non-text selection projection used by the provisional gate."""
    provenance = selection.candidate.provenance
    payload = {
        "projection_version": "evidence-gate-selection-v1",
        "provenance": {
            "canonicalization_spec_version": provenance.canonicalization_spec_version,
            "content_sha256": provenance.content_sha256,
            "evidence_index_ref": _artifact_payload(provenance.evidence_index_ref),
            "evidence_key": provenance.evidence_key,
            "knowledge_chunk_ref": provenance.knowledge_chunk_ref,
            "locator": provenance.locator,
            "source_snapshot_ref": _artifact_payload(provenance.source_snapshot_ref),
            "source_version": provenance.source_version,
        },
        "rerank_rank": selection.rerank_rank,
        "rerank_score": selection.rerank_score.value,
        "stage_signals": [
            {
                "rank": signal.rank,
                "score": signal.score.value,
                "stage": _stage_projection_value(signal.stage),
            }
            for signal in selection.candidate.stage_signals
        ],
    }
    return _canonical_sha256(payload)


def canonical_rerank_output_hash(
    projection_version: str,
    selections: tuple[UntrustedKnowledgeEvidenceSelection, ...],
) -> str:
    """Hash the evidence key/rank/score projection returned by reranking."""
    payload = {
        "projection_version": projection_version,
        "selections": [
            {
                "evidence_key": selection.candidate.provenance.evidence_key,
                "rerank_rank": selection.rerank_rank,
                "rerank_score": selection.rerank_score.value,
            }
            for selection in sorted(selections, key=lambda item: item.rerank_rank)
        ],
    }
    return _canonical_sha256(payload)


def _stage_projection_value(value: object) -> str:
    if type(value) is EvidenceSearchStage:
        return value.value
    return f"<invalid:{type(value).__module__}.{type(value).__qualname__}>"


def _canonical_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_sha256(payload: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
