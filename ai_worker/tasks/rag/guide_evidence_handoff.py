"""Guide Evidence Handoff Contract Kernel (#180 Prerequisite).

Persistence-free exact-match binding between #174 REQUEST Guard Source/Member
decision observations and #178 Production Retrieval selections for safe
consumption by #179 Guideline Card kernel.

Scope & Authority Boundaries:
- 'Verified' is strictly limited to verifying structure, hash, and identity
  consistency of opaque caller observations.
- Artifact references (assessment_artifact_ref, eligibility_receipt_ref,
  verifier_artifact_ref, request_guard_ref, request_source_decision_ref,
  request_member_decision_ref) are treated as opaque caller observations
  and structural bindings.
- This kernel verifies structural invariants, canonical projections, hash
  integrity, and exact coordinate/provenance bindings.
- It does NOT verify Decision ownership, PASS validity, or assessment content
  authenticity. Until the #174 authenticated assembler verifies Decision
  ownership/PASS/assessment content, #180 runtime cannot directly consume
  this handoff as authority.
- Upstream #178 canonical hash contract alignment:
  The selection manifest and ProductionSearchReceipt (v2.0) conform to the
  canonical RFC 8785 JCS specification of PD-178-20260916 (PR #636 merged).
  The former BLOCKED_BY_178_CANONICAL_HASH_CONTRACT dependency marker has been resolved.
- Downstream #180 endpoint-member contract resolution:
  The former non-enforcing BLOCKED_BY_180_ENDPOINT_MEMBER_CONTRACT marker was resolved
  via PD-180-EM-20260916 and the shared source_member_identity kernel.
  Downstream validators now accept nullable operation_code via typed validation
  delegated to is_valid_source_member_identity. Note that pure validation does not grant
  authenticated authority; runtime integration remains blocked until PD-315 approval,
  #174 authenticated assembler implementation, and #180 runtime orchestration wiring.
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import cast
from uuid import UUID

from ai_worker.tasks.evaluation.canonical import (
    JsonValue,
    canonical_json_bytes,
    canonical_sha256,
)
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    QueryFingerprint,
    SensitiveText,
    is_valid_immutable_artifact_ref,
)
from ai_worker.tasks.rag.evidence_search import (
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    StableCoordinate,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    PRODUCTION_SEARCH_RECEIPT_VERSION,
    ProductionSearchReceipt,
    RetrievalExecutionStatus,
    compute_production_search_receipt,
    compute_selection_manifest_hash,
)
from ai_worker.tasks.rag.source_member_identity import (
    SourceMemberIdentity,
    SourceMemberKind,
    is_valid_source_member_identity,
)

__all__ = [
    "canonical_jcs_bytes",
    "canonical_jcs_sha256",
]


def canonical_jcs_bytes(value: object) -> bytes:
    return canonical_json_bytes(cast(JsonValue, value))


def canonical_jcs_sha256(value: object) -> str:
    return canonical_sha256(cast(JsonValue, value))


GUIDE_EVIDENCE_HANDOFF_PROJECTION_VERSION = "guide-evidence-handoff-v1"


class ObservedDecisionOutcome(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class RequestDecisionStage(StrEnum):
    REQUEST = "REQUEST"


class GuideEvidenceHandoffBuildDecision(StrEnum):
    BUILT = "BUILT"
    REJECTED = "REJECTED"


class GuideEvidenceHandoffVerificationDecision(StrEnum):
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class GuideEvidenceHandoffReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    REQUEST_ORIGIN_MISMATCH = "REQUEST_ORIGIN_MISMATCH"
    COORDINATE_PROVENANCE_MISMATCH = "COORDINATE_PROVENANCE_MISMATCH"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    SOURCE_SNAPSHOT_MISMATCH = "SOURCE_SNAPSHOT_MISMATCH"
    SOURCE_MEMBER_MISMATCH = "SOURCE_MEMBER_MISMATCH"
    SOURCE_VERSION_MISMATCH = "SOURCE_VERSION_MISMATCH"
    MEMBER_IDENTITY_INVALID = "MEMBER_IDENTITY_INVALID"
    REQUEST_GUARD_REF_REQUIRED = "REQUEST_GUARD_REF_REQUIRED"
    REQUEST_SOURCE_DECISION_REQUIRED = "REQUEST_SOURCE_DECISION_REQUIRED"
    REQUEST_MEMBER_DECISION_REQUIRED = "REQUEST_MEMBER_DECISION_REQUIRED"
    OBSERVED_DECISION_NOT_PASS = "OBSERVED_DECISION_NOT_PASS"
    RETRIEVAL_RECEIPT_MISMATCH = "RETRIEVAL_RECEIPT_MISMATCH"
    SELECTION_MANIFEST_MISMATCH = "SELECTION_MANIFEST_MISMATCH"
    OBSERVED_PROVENANCE_REF_REQUIRED = "OBSERVED_PROVENANCE_REF_REQUIRED"
    CONTENT_HASH_MISMATCH = "CONTENT_HASH_MISMATCH"
    DUPLICATE_EVIDENCE_KEY = "DUPLICATE_EVIDENCE_KEY"
    DUPLICATE_STABLE_COORDINATE = "DUPLICATE_STABLE_COORDINATE"
    DUPLICATE_KNOWLEDGE_CHUNK_ID = "DUPLICATE_KNOWLEDGE_CHUNK_ID"
    SELECTION_ORDER_INVALID = "SELECTION_ORDER_INVALID"
    ASSESSMENT_NOT_YET_VALID = "ASSESSMENT_NOT_YET_VALID"
    ASSESSMENT_EXPIRED = "ASSESSMENT_EXPIRED"
    DATETIME_NOT_AWARE = "DATETIME_NOT_AWARE"
    FORGED_HANDOFF_HASH = "FORGED_HANDOFF_HASH"
    HANDOFF_MISMATCH = "HANDOFF_MISMATCH"


@dataclass(frozen=True, slots=True)
class RequestSourceMemberBinding:
    request_guard_ref: ImmutableArtifactRef
    request_operation_code: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    member_kind: SourceMemberKind
    request_source_decision_ref: ImmutableArtifactRef
    request_member_decision_ref: ImmutableArtifactRef
    observed_source_decision_outcome: ObservedDecisionOutcome
    observed_member_decision_outcome: ObservedDecisionOutcome
    request_decision_stage: RequestDecisionStage = RequestDecisionStage.REQUEST
    endpoint_code: str | None = None
    operation_code: str | None = None
    artifact_code: str | None = None
    artifact_version: str | None = None


@dataclass(frozen=True, slots=True)
class GuideEvidenceSelectionRequest:
    hit: ProductionSearchHit
    binding: RequestSourceMemberBinding
    evidence_key: str
    content_text: SensitiveText
    retrieval_receipt_ref: ImmutableArtifactRef
    eligibility_receipt_ref: ImmutableArtifactRef
    assessment_artifact_ref: ImmutableArtifactRef
    verifier_artifact_ref: ImmutableArtifactRef
    assessment_valid_from: datetime
    assessment_valid_until: datetime
    content_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class GuideEvidenceHandoffRequest:
    retrieval_receipt: ProductionSearchReceipt
    selections: tuple[GuideEvidenceSelectionRequest, ...]
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedGuideEvidenceSelection:
    evidence_key: str
    knowledge_chunk_id: UUID
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    locator: str
    content_sha256: str
    content_text: SensitiveText
    member_kind: SourceMemberKind
    endpoint_code: str | None
    operation_code: str | None
    artifact_code: str | None
    artifact_version: str | None
    request_guard_ref: ImmutableArtifactRef
    request_operation_code: str
    request_decision_stage: RequestDecisionStage
    request_source_decision_ref: ImmutableArtifactRef
    request_member_decision_ref: ImmutableArtifactRef
    retrieval_receipt_ref: ImmutableArtifactRef
    eligibility_receipt_ref: ImmutableArtifactRef
    assessment_artifact_ref: ImmutableArtifactRef
    verifier_artifact_ref: ImmutableArtifactRef
    assessment_valid_from: datetime
    assessment_valid_until: datetime
    final_rank: int
    canonical_checksum: str
    external_document_id: str
    chunk_index: int
    canonicalization_spec_version: str
    normalization_version: str


@dataclass(frozen=True, slots=True)
class VerifiedGuideEvidenceHandoff:
    retrieval_receipt_ref: ImmutableArtifactRef
    retrieval_selection_manifest_sha256: str
    evaluated_at: datetime
    selections: tuple[VerifiedGuideEvidenceSelection, ...]
    handoff_sha256: str


@dataclass(frozen=True, slots=True)
class GuideEvidenceHandoffBuildOutcome:
    decision: GuideEvidenceHandoffBuildDecision
    reasons: tuple[GuideEvidenceHandoffReason, ...]
    handoff: VerifiedGuideEvidenceHandoff | None


@dataclass(frozen=True, slots=True)
class GuideEvidenceHandoffVerificationOutcome:
    decision: GuideEvidenceHandoffVerificationDecision
    reasons: tuple[GuideEvidenceHandoffReason, ...]


def _artifact_projection(ref: ImmutableArtifactRef) -> dict[str, str]:
    return {
        "artifact_code": ref.artifact_code,
        "content_sha256": ref.content_sha256,
        "version": ref.version,
    }


def _canonical_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _selection_projection(s: VerifiedGuideEvidenceSelection) -> dict[str, object]:
    return {
        "artifact_code": s.artifact_code,
        "artifact_version": s.artifact_version,
        "assessment_artifact_ref": _artifact_projection(s.assessment_artifact_ref),
        "assessment_valid_from": _canonical_datetime(s.assessment_valid_from),
        "assessment_valid_until": _canonical_datetime(s.assessment_valid_until),
        "canonical_checksum": s.canonical_checksum,
        "canonicalization_spec_version": s.canonicalization_spec_version,
        "chunk_index": s.chunk_index,
        "content_sha256": s.content_sha256,
        "eligibility_receipt_ref": _artifact_projection(s.eligibility_receipt_ref),
        "endpoint_code": s.endpoint_code,
        "evidence_key": s.evidence_key,
        "external_document_id": s.external_document_id,
        "final_rank": s.final_rank,
        "knowledge_chunk_id": str(s.knowledge_chunk_id),
        "locator": s.locator,
        "member_kind": s.member_kind.value,
        "normalization_version": s.normalization_version,
        "operation_code": s.operation_code,
        "request_decision_stage": s.request_decision_stage.value,
        "request_guard_ref": _artifact_projection(s.request_guard_ref),
        "request_member_decision_ref": _artifact_projection(s.request_member_decision_ref),
        "request_operation_code": s.request_operation_code,
        "request_source_decision_ref": _artifact_projection(s.request_source_decision_ref),
        "retrieval_receipt_ref": _artifact_projection(s.retrieval_receipt_ref),
        "source_code": s.source_code,
        "source_snapshot_id": str(s.source_snapshot_id),
        "source_snapshot_member_id": str(s.source_snapshot_member_id),
        "source_version": s.source_version,
        "verifier_artifact_ref": _artifact_projection(s.verifier_artifact_ref),
    }


def compute_guide_evidence_handoff_hash(
    retrieval_receipt_ref: ImmutableArtifactRef,
    retrieval_selection_manifest_sha256: str,
    evaluated_at: datetime,
    selections: tuple[VerifiedGuideEvidenceSelection, ...],
) -> str:
    payload = {
        "evaluated_at": _canonical_datetime(evaluated_at),
        "projection_version": GUIDE_EVIDENCE_HANDOFF_PROJECTION_VERSION,
        "retrieval_receipt_ref": _artifact_projection(retrieval_receipt_ref),
        "retrieval_selection_manifest_sha256": retrieval_selection_manifest_sha256,
        "selections": [_selection_projection(s) for s in selections],
    }
    return canonical_jcs_sha256(payload)


def _is_sha256(val: object) -> bool:
    return isinstance(val, str) and len(val) == 64 and all(c in "0123456789abcdef" for c in val)


def _is_nonblank_nfc(val: object) -> bool:
    return isinstance(val, str) and len(val) > 0 and val == val.strip() and unicodedata.is_normalized("NFC", val)


def _is_utc_datetime(dt: object) -> bool:
    from datetime import timedelta

    return isinstance(dt, datetime) and dt.tzinfo is not None and dt.utcoffset() == timedelta(0)


def _request_shape_is_valid(request: GuideEvidenceHandoffRequest) -> bool:
    if type(request) is not GuideEvidenceHandoffRequest:
        return False
    if (
        type(request.retrieval_receipt) is not ProductionSearchReceipt
        or type(request.selections) is not tuple
        or not request.selections
        or not isinstance(request.evaluated_at, datetime)
    ):
        return False
    for sel in request.selections:
        if (
            type(sel) is not GuideEvidenceSelectionRequest
            or type(sel.hit) is not ProductionSearchHit
            or type(sel.hit.provenance) is not ProductionEvidenceProvenance
            or type(sel.hit.coordinate) is not StableCoordinate
            or type(sel.hit.fusion_rank) is not int
            or type(sel.binding) is not RequestSourceMemberBinding
            or not isinstance(sel.content_text, SensitiveText)
            or not _is_nonblank_nfc(sel.evidence_key)
            or not _is_nonblank_nfc(sel.binding.request_operation_code)
            or type(sel.binding.request_decision_stage) is not RequestDecisionStage
            or type(sel.binding.observed_source_decision_outcome) is not ObservedDecisionOutcome
            or type(sel.binding.observed_member_decision_outcome) is not ObservedDecisionOutcome
            or type(sel.hit.provenance.knowledge_index_id) is not UUID
            or type(sel.hit.provenance.knowledge_chunk_id) is not UUID
            or type(sel.hit.provenance.source_snapshot_id) is not UUID
            or type(sel.hit.provenance.source_snapshot_member_id) is not UUID
            or type(sel.binding.source_snapshot_id) is not UUID
            or type(sel.binding.source_snapshot_member_id) is not UUID
            or type(sel.hit.coordinate.chunk_index) is not int
            or sel.hit.coordinate.chunk_index < 0
            or type(sel.hit.provenance.chunk_index) is not int
            or sel.hit.provenance.chunk_index < 0
            or not _is_nonblank_nfc(sel.hit.coordinate.source_code)
            or not _is_nonblank_nfc(sel.hit.coordinate.source_version)
            or not _is_nonblank_nfc(sel.hit.coordinate.external_document_id)
            or not _is_nonblank_nfc(sel.hit.provenance.source_code)
            or not _is_nonblank_nfc(sel.hit.provenance.source_version)
            or not _is_nonblank_nfc(sel.hit.provenance.external_document_id)
            or not _is_nonblank_nfc(sel.hit.provenance.locator)
            or not _is_nonblank_nfc(sel.binding.source_code)
            or not _is_nonblank_nfc(sel.binding.source_version)
            or not _is_sha256(sel.hit.provenance.canonical_checksum)
            or not _is_sha256(sel.hit.provenance.content_hash)
            or not _is_nonblank_nfc(sel.hit.provenance.canonicalization_spec_version)
            or not _is_nonblank_nfc(sel.hit.provenance.normalization_version)
        ):
            return False
        try:
            rev = sel.content_text.reveal()
            if not isinstance(rev, str):
                return False
        except (AttributeError, TypeError, ValueError, UnicodeError):
            return False
    return True


def _check_request_origin_consistency(
    selections: tuple[GuideEvidenceSelectionRequest, ...],
) -> list[GuideEvidenceHandoffReason]:
    if not selections:
        return []
    first_guard = selections[0].binding.request_guard_ref
    first_op = selections[0].binding.request_operation_code
    first_stage = selections[0].binding.request_decision_stage
    for sel in selections[1:]:
        if (
            sel.binding.request_guard_ref != first_guard
            or sel.binding.request_operation_code != first_op
            or sel.binding.request_decision_stage != first_stage
        ):
            return [GuideEvidenceHandoffReason.REQUEST_ORIGIN_MISMATCH]
    return []


def _check_ordering_and_duplicates(
    selections: tuple[GuideEvidenceSelectionRequest, ...],
) -> list[GuideEvidenceHandoffReason]:
    reasons: list[GuideEvidenceHandoffReason] = []
    ranks = [sel.hit.fusion_rank for sel in selections]
    for r in ranks:
        if r < 1 or r > 5:
            reasons.append(GuideEvidenceHandoffReason.SELECTION_ORDER_INVALID)
            break

    for i in range(1, len(ranks)):
        if ranks[i] <= ranks[i - 1]:
            reasons.append(GuideEvidenceHandoffReason.SELECTION_ORDER_INVALID)
            break

    keys = [sel.evidence_key for sel in selections]
    if len(keys) != len(set(keys)):
        reasons.append(GuideEvidenceHandoffReason.DUPLICATE_EVIDENCE_KEY)

    coordinates = [
        (
            sel.hit.coordinate.source_code,
            sel.hit.coordinate.source_version,
            sel.hit.coordinate.external_document_id,
            sel.hit.coordinate.chunk_index,
        )
        for sel in selections
    ]
    if len(coordinates) != len(set(coordinates)):
        reasons.append(GuideEvidenceHandoffReason.DUPLICATE_STABLE_COORDINATE)

    chunks = [sel.hit.provenance.knowledge_chunk_id for sel in selections]
    if len(chunks) != len(set(chunks)):
        reasons.append(GuideEvidenceHandoffReason.DUPLICATE_KNOWLEDGE_CHUNK_ID)

    return reasons


def _receipt_structure_is_valid(receipt: ProductionSearchReceipt) -> bool:
    if (
        not is_valid_immutable_artifact_ref(receipt.artifact_ref)
        or not is_valid_immutable_artifact_ref(receipt.filter_snapshot_ref)
        or not is_valid_immutable_artifact_ref(receipt.evidence_index_ref)
        or not is_valid_immutable_artifact_ref(receipt.retrieval_config_ref)
        or not is_valid_immutable_artifact_ref(receipt.adapter_artifact_ref)
    ):
        return False

    if receipt.artifact_ref.artifact_code != "production_search_receipt":
        return False

    if receipt.artifact_ref.version != PRODUCTION_SEARCH_RECEIPT_VERSION:
        return False

    if receipt.variant != "RET-H":
        return False

    if receipt.retrieval_execution_status != RetrievalExecutionStatus.SUCCEEDED:
        return False

    if (
        type(receipt.query_fingerprint) is not QueryFingerprint
        or not _is_nonblank_nfc(receipt.query_fingerprint.algorithm)
        or not _is_nonblank_nfc(receipt.query_fingerprint.key_version)
        or not _is_sha256(receipt.query_fingerprint.digest)
    ):
        return False

    if receipt.query_embedding_sha256 is None or not _is_sha256(receipt.query_embedding_sha256):
        return False

    if (
        not _is_sha256(receipt.signal_manifest_sha256)
        or not _is_sha256(receipt.hit_manifest_sha256)
        or not _is_sha256(receipt.selection_manifest_sha256)
    ):
        return False

    return True


def _check_receipt_and_manifest(request: GuideEvidenceHandoffRequest) -> list[GuideEvidenceHandoffReason]:
    reasons: list[GuideEvidenceHandoffReason] = []
    receipt = request.retrieval_receipt

    if not _receipt_structure_is_valid(receipt):
        reasons.append(GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH)

    try:
        recomputed_receipt = compute_production_search_receipt(
            variant=receipt.variant,
            status=receipt.retrieval_execution_status,
            diagnostic_code=receipt.diagnostic_code,
            query_fingerprint=receipt.query_fingerprint,
            filter_snapshot_ref=receipt.filter_snapshot_ref,
            evidence_index_ref=receipt.evidence_index_ref,
            retrieval_config_ref=receipt.retrieval_config_ref,
            adapter_artifact_ref=receipt.adapter_artifact_ref,
            query_embedding_sha256=receipt.query_embedding_sha256,
            signal_manifest_sha256=receipt.signal_manifest_sha256,
            hit_manifest_sha256=receipt.hit_manifest_sha256,
            selection_manifest_sha256=receipt.selection_manifest_sha256,
        )
        if recomputed_receipt.artifact_ref != receipt.artifact_ref:
            reasons.append(GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH)
    except (AttributeError, TypeError, ValueError, UnicodeError):
        reasons.append(GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH)

    for sel in request.selections:
        if sel.retrieval_receipt_ref != receipt.artifact_ref:
            reasons.append(GuideEvidenceHandoffReason.RETRIEVAL_RECEIPT_MISMATCH)
        if (
            not is_valid_immutable_artifact_ref(sel.eligibility_receipt_ref)
            or not is_valid_immutable_artifact_ref(sel.assessment_artifact_ref)
            or not is_valid_immutable_artifact_ref(sel.verifier_artifact_ref)
        ):
            reasons.append(GuideEvidenceHandoffReason.OBSERVED_PROVENANCE_REF_REQUIRED)

    try:
        recalculated_manifest = compute_selection_manifest_hash([sel.hit for sel in request.selections])
        if recalculated_manifest != receipt.selection_manifest_sha256:
            reasons.append(GuideEvidenceHandoffReason.SELECTION_MANIFEST_MISMATCH)
    except (TypeError, ValueError):
        reasons.append(GuideEvidenceHandoffReason.SELECTION_MANIFEST_MISMATCH)

    return reasons


def _check_member_identity_for_kind(b: RequestSourceMemberBinding) -> list[GuideEvidenceHandoffReason]:
    identity = SourceMemberIdentity(
        member_kind=b.member_kind,
        endpoint_code=b.endpoint_code,
        operation_code=b.operation_code,
        artifact_code=b.artifact_code,
        artifact_version=b.artifact_version,
    )
    if not is_valid_source_member_identity(identity):
        return [GuideEvidenceHandoffReason.MEMBER_IDENTITY_INVALID]
    return []


def _check_source_member_binding(sel: GuideEvidenceSelectionRequest) -> list[GuideEvidenceHandoffReason]:
    reasons: list[GuideEvidenceHandoffReason] = []
    b = sel.binding
    prov = sel.hit.provenance

    if not is_valid_immutable_artifact_ref(b.request_guard_ref):
        reasons.append(GuideEvidenceHandoffReason.REQUEST_GUARD_REF_REQUIRED)
    if not is_valid_immutable_artifact_ref(b.request_source_decision_ref):
        reasons.append(GuideEvidenceHandoffReason.REQUEST_SOURCE_DECISION_REQUIRED)
    if not is_valid_immutable_artifact_ref(b.request_member_decision_ref):
        reasons.append(GuideEvidenceHandoffReason.REQUEST_MEMBER_DECISION_REQUIRED)

    if (
        b.observed_source_decision_outcome != ObservedDecisionOutcome.PASS
        or b.observed_member_decision_outcome != ObservedDecisionOutcome.PASS
    ):
        reasons.append(GuideEvidenceHandoffReason.OBSERVED_DECISION_NOT_PASS)

    if b.source_code != prov.source_code:
        reasons.append(GuideEvidenceHandoffReason.SOURCE_MISMATCH)
    if b.source_version != prov.source_version:
        reasons.append(GuideEvidenceHandoffReason.SOURCE_VERSION_MISMATCH)
    if b.source_snapshot_id != prov.source_snapshot_id:
        reasons.append(GuideEvidenceHandoffReason.SOURCE_SNAPSHOT_MISMATCH)
    if b.source_snapshot_member_id != prov.source_snapshot_member_id:
        reasons.append(GuideEvidenceHandoffReason.SOURCE_MEMBER_MISMATCH)

    coord = sel.hit.coordinate
    if (
        coord.source_code != prov.source_code
        or coord.source_version != prov.source_version
        or coord.external_document_id != prov.external_document_id
        or coord.chunk_index != prov.chunk_index
    ):
        reasons.append(GuideEvidenceHandoffReason.COORDINATE_PROVENANCE_MISMATCH)

    reasons.extend(_check_member_identity_for_kind(b))
    return reasons


def _check_content_hash(sel: GuideEvidenceSelectionRequest) -> list[GuideEvidenceHandoffReason]:
    reasons: list[GuideEvidenceHandoffReason] = []
    prov = sel.hit.provenance
    calculated_content_hash = hashlib.sha256(sel.content_text.reveal().encode("utf-8")).hexdigest()
    if calculated_content_hash != prov.content_hash:
        reasons.append(GuideEvidenceHandoffReason.CONTENT_HASH_MISMATCH)
    if sel.content_sha256 is not None and sel.content_sha256 != prov.content_hash:
        reasons.append(GuideEvidenceHandoffReason.CONTENT_HASH_MISMATCH)
    return reasons


def _check_datetime_and_freshness(request: GuideEvidenceHandoffRequest) -> list[GuideEvidenceHandoffReason]:
    reasons: list[GuideEvidenceHandoffReason] = []
    if not _is_utc_datetime(request.evaluated_at):
        reasons.append(GuideEvidenceHandoffReason.DATETIME_NOT_AWARE)

    for sel in request.selections:
        if not _is_utc_datetime(sel.assessment_valid_from) or not _is_utc_datetime(sel.assessment_valid_until):
            reasons.append(GuideEvidenceHandoffReason.DATETIME_NOT_AWARE)
        elif _is_utc_datetime(request.evaluated_at):
            if request.evaluated_at < sel.assessment_valid_from:
                reasons.append(GuideEvidenceHandoffReason.ASSESSMENT_NOT_YET_VALID)
            if request.evaluated_at >= sel.assessment_valid_until:
                reasons.append(GuideEvidenceHandoffReason.ASSESSMENT_EXPIRED)

    return reasons


def build_guide_evidence_handoff(
    request: GuideEvidenceHandoffRequest,
) -> GuideEvidenceHandoffBuildOutcome:
    if not _request_shape_is_valid(request):
        return GuideEvidenceHandoffBuildOutcome(
            decision=GuideEvidenceHandoffBuildDecision.REJECTED,
            reasons=(GuideEvidenceHandoffReason.REQUEST_INVALID,),
            handoff=None,
        )

    reasons: list[GuideEvidenceHandoffReason] = []
    reasons.extend(_check_request_origin_consistency(request.selections))
    reasons.extend(_check_ordering_and_duplicates(request.selections))
    reasons.extend(_check_receipt_and_manifest(request))

    for sel in request.selections:
        reasons.extend(_check_source_member_binding(sel))
        reasons.extend(_check_content_hash(sel))

    reasons.extend(_check_datetime_and_freshness(request))

    if reasons:
        return GuideEvidenceHandoffBuildOutcome(
            decision=GuideEvidenceHandoffBuildDecision.REJECTED,
            reasons=tuple(dict.fromkeys(reasons)),
            handoff=None,
        )

    verified_selections = tuple(
        VerifiedGuideEvidenceSelection(
            evidence_key=sel.evidence_key,
            knowledge_chunk_id=sel.hit.provenance.knowledge_chunk_id,
            source_snapshot_id=sel.hit.provenance.source_snapshot_id,
            source_snapshot_member_id=sel.hit.provenance.source_snapshot_member_id,
            source_code=sel.hit.provenance.source_code,
            source_version=sel.hit.provenance.source_version,
            locator=sel.hit.provenance.locator,
            content_sha256=sel.hit.provenance.content_hash,
            content_text=sel.content_text,
            member_kind=sel.binding.member_kind,
            endpoint_code=sel.binding.endpoint_code,
            operation_code=sel.binding.operation_code,
            artifact_code=sel.binding.artifact_code,
            artifact_version=sel.binding.artifact_version,
            request_guard_ref=sel.binding.request_guard_ref,
            request_operation_code=sel.binding.request_operation_code,
            request_decision_stage=sel.binding.request_decision_stage,
            request_source_decision_ref=sel.binding.request_source_decision_ref,
            request_member_decision_ref=sel.binding.request_member_decision_ref,
            retrieval_receipt_ref=sel.retrieval_receipt_ref,
            eligibility_receipt_ref=sel.eligibility_receipt_ref,
            assessment_artifact_ref=sel.assessment_artifact_ref,
            verifier_artifact_ref=sel.verifier_artifact_ref,
            assessment_valid_from=sel.assessment_valid_from,
            assessment_valid_until=sel.assessment_valid_until,
            final_rank=sel.hit.fusion_rank,
            canonical_checksum=sel.hit.provenance.canonical_checksum,
            external_document_id=sel.hit.provenance.external_document_id,
            chunk_index=sel.hit.provenance.chunk_index,
            canonicalization_spec_version=sel.hit.provenance.canonicalization_spec_version,
            normalization_version=sel.hit.provenance.normalization_version,
        )
        for sel in request.selections
    )

    handoff_hash = compute_guide_evidence_handoff_hash(
        retrieval_receipt_ref=request.retrieval_receipt.artifact_ref,
        retrieval_selection_manifest_sha256=request.retrieval_receipt.selection_manifest_sha256,
        evaluated_at=request.evaluated_at,
        selections=verified_selections,
    )
    handoff = VerifiedGuideEvidenceHandoff(
        retrieval_receipt_ref=request.retrieval_receipt.artifact_ref,
        retrieval_selection_manifest_sha256=request.retrieval_receipt.selection_manifest_sha256,
        evaluated_at=request.evaluated_at,
        selections=verified_selections,
        handoff_sha256=handoff_hash,
    )
    return GuideEvidenceHandoffBuildOutcome(
        decision=GuideEvidenceHandoffBuildDecision.BUILT,
        reasons=(),
        handoff=handoff,
    )


def _handoff_boundary_is_valid(value: object) -> bool:
    if type(value) is not VerifiedGuideEvidenceHandoff or type(value.selections) is not tuple:
        return False
    for selection in value.selections:
        if type(selection) is not VerifiedGuideEvidenceSelection or not isinstance(
            selection.content_text, SensitiveText
        ):
            return False
        try:
            if not isinstance(selection.content_text.reveal(), str):
                return False
        except (AttributeError, TypeError, ValueError, UnicodeError):
            return False
    return True


def _verified_selection_matches(
    actual: VerifiedGuideEvidenceSelection,
    expected: VerifiedGuideEvidenceSelection,
) -> bool:
    return (
        actual.evidence_key == expected.evidence_key
        and actual.knowledge_chunk_id == expected.knowledge_chunk_id
        and actual.source_snapshot_id == expected.source_snapshot_id
        and actual.source_snapshot_member_id == expected.source_snapshot_member_id
        and actual.source_code == expected.source_code
        and actual.source_version == expected.source_version
        and actual.locator == expected.locator
        and actual.content_sha256 == expected.content_sha256
        and actual.content_text.reveal() == expected.content_text.reveal()
        and actual.member_kind == expected.member_kind
        and actual.endpoint_code == expected.endpoint_code
        and actual.operation_code == expected.operation_code
        and actual.artifact_code == expected.artifact_code
        and actual.artifact_version == expected.artifact_version
        and actual.request_guard_ref == expected.request_guard_ref
        and actual.request_operation_code == expected.request_operation_code
        and actual.request_decision_stage == expected.request_decision_stage
        and actual.request_source_decision_ref == expected.request_source_decision_ref
        and actual.request_member_decision_ref == expected.request_member_decision_ref
        and actual.retrieval_receipt_ref == expected.retrieval_receipt_ref
        and actual.eligibility_receipt_ref == expected.eligibility_receipt_ref
        and actual.assessment_artifact_ref == expected.assessment_artifact_ref
        and actual.verifier_artifact_ref == expected.verifier_artifact_ref
        and actual.assessment_valid_from == expected.assessment_valid_from
        and actual.assessment_valid_until == expected.assessment_valid_until
        and actual.final_rank == expected.final_rank
        and actual.canonical_checksum == expected.canonical_checksum
        and actual.external_document_id == expected.external_document_id
        and actual.chunk_index == expected.chunk_index
        and actual.canonicalization_spec_version == expected.canonicalization_spec_version
        and actual.normalization_version == expected.normalization_version
    )


def verify_guide_evidence_handoff(
    request: GuideEvidenceHandoffRequest,
    handoff: VerifiedGuideEvidenceHandoff,
) -> GuideEvidenceHandoffVerificationOutcome:
    if type(request) is not GuideEvidenceHandoffRequest or not _handoff_boundary_is_valid(handoff):
        return GuideEvidenceHandoffVerificationOutcome(
            decision=GuideEvidenceHandoffVerificationDecision.REJECTED,
            reasons=(GuideEvidenceHandoffReason.REQUEST_INVALID,),
        )

    build_outcome = build_guide_evidence_handoff(request)
    if build_outcome.decision != GuideEvidenceHandoffBuildDecision.BUILT or build_outcome.handoff is None:
        return GuideEvidenceHandoffVerificationOutcome(
            decision=GuideEvidenceHandoffVerificationDecision.REJECTED,
            reasons=build_outcome.reasons,
        )

    expected = build_outcome.handoff

    if (
        handoff.retrieval_receipt_ref != expected.retrieval_receipt_ref
        or handoff.retrieval_selection_manifest_sha256 != expected.retrieval_selection_manifest_sha256
        or handoff.evaluated_at != expected.evaluated_at
        or len(handoff.selections) != len(expected.selections)
    ):
        return GuideEvidenceHandoffVerificationOutcome(
            decision=GuideEvidenceHandoffVerificationDecision.REJECTED,
            reasons=(GuideEvidenceHandoffReason.HANDOFF_MISMATCH,),
        )

    for h_sel, e_sel in zip(handoff.selections, expected.selections, strict=True):
        if not _verified_selection_matches(h_sel, e_sel):
            return GuideEvidenceHandoffVerificationOutcome(
                decision=GuideEvidenceHandoffVerificationDecision.REJECTED,
                reasons=(GuideEvidenceHandoffReason.HANDOFF_MISMATCH,),
            )

    recalculated_hash = compute_guide_evidence_handoff_hash(
        handoff.retrieval_receipt_ref,
        handoff.retrieval_selection_manifest_sha256,
        handoff.evaluated_at,
        handoff.selections,
    )
    if handoff.handoff_sha256 != recalculated_hash or handoff.handoff_sha256 != expected.handoff_sha256:
        return GuideEvidenceHandoffVerificationOutcome(
            decision=GuideEvidenceHandoffVerificationDecision.REJECTED,
            reasons=(GuideEvidenceHandoffReason.FORGED_HANDOFF_HASH,),
        )

    return GuideEvidenceHandoffVerificationOutcome(
        decision=GuideEvidenceHandoffVerificationDecision.VERIFIED,
        reasons=(),
    )
