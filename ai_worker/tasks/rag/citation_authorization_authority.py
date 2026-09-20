"""Worker-owned issuer for persisted Citation Authorization authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ai_worker.tasks.rag.citation_authorization import (
    AuthorizationBuildDecision,
    AuthorizationReason,
    AuthorizationVerificationDecision,
    CitationAuthorizationReceipt,
    CitationAuthorizationRequest,
    CitationAuthorizationSelectionEntry,
    CitationAuthorizationSelectionReceipt,
    GuardDecision,
    GuardOperation,
    UsePurpose,
    build_citation_authorization_request,
    verify_citation_authorization_receipt,
)
from ai_worker.tasks.rag.claim_citation_validator import (
    InteractionRuleEvidenceRef,
    KnowledgeChunkEvidenceRef,
    LifestyleGuidelineEvidenceRef,
    SafetyPolicyEvidenceRef,
    SourceExecutionProvenance,
    ValidatedCitationSelection,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.guide_evidence_authority import (
    AuthoritativeMemberDecisionObservation,
    AuthoritativeSourceDecisionObservation,
)
from ai_worker.tasks.rag.request_guard_runtime_binding import (
    build_origin_request_guard_binding,
    build_runtime_authorization_binding,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberIdentity, SourceMemberKind
from rag_runtime.citation_authorization_authority import (
    CitationAuthorityDecision,
    CitationAuthorityReason,
    CitationMemberDecisionProjection,
    CitationReceiptProjection,
    CitationReceiptSelectionProjection,
    CitationSourceDecisionProjection,
    compute_citation_member_decision_ref,
    compute_citation_receipt_ref,
    compute_citation_source_decision_ref,
)
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    RequestGuardRuntimeBindingRef,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import (
    SourceUseApprovalIdentity,
    SourceUseApprovalObservation,
    SourceUsePurpose,
)


class CitationAuthorityIssueDecision(StrEnum):
    ISSUED = "ISSUED"
    REPLAYED = "REPLAYED"
    PREREQUISITE_FAILED = "PREREQUISITE_FAILED"


class CitationAuthorityIssueReason(StrEnum):
    EXISTING_RECEIPT_CORRUPT = "EXISTING_RECEIPT_CORRUPT"
    REQUEST_GUARD_ABSENT = "REQUEST_GUARD_ABSENT"
    REQUEST_REPLAY_MISMATCH = "REQUEST_REPLAY_MISMATCH"
    REQUEST_AUTHORITY_ABSENT = "REQUEST_AUTHORITY_ABSENT"
    REQUEST_AUTHORITY_MISMATCH = "REQUEST_AUTHORITY_MISMATCH"
    AMBIGUOUS_BINDING = "AMBIGUOUS_BINDING"
    BUNDLE_PIN_ABSENT = "BUNDLE_PIN_ABSENT"
    BUNDLE_PIN_MISMATCH = "BUNDLE_PIN_MISMATCH"
    SOURCE_APPROVAL_ABSENT = "SOURCE_APPROVAL_ABSENT"
    SOURCE_APPROVAL_MISMATCH = "SOURCE_APPROVAL_MISMATCH"
    CURRENT_SOURCE_ABSENT = "CURRENT_SOURCE_ABSENT"
    CURRENT_MEMBER_ABSENT = "CURRENT_MEMBER_ABSENT"
    ISSUER_RECEIPT_INVALID = "ISSUER_RECEIPT_INVALID"


class CitationAuthorizationAuthorityError(RuntimeError):
    """Dependency failure or persisted corruption that must not become a FAIL receipt."""


@dataclass(frozen=True, slots=True)
class CitationSourceEligibilityObservation:
    source_snapshot_id: UUID
    source_code: str
    source_version: str
    source_lifecycle_status: str
    snapshot_verification_status: str


@dataclass(frozen=True, slots=True)
class CitationMemberEligibilityObservation:
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    member_kind: str
    endpoint_id: UUID | None
    operation_id: UUID | None
    ingestion_artifact_id: UUID | None
    endpoint_code: str | None
    operation_code: str | None
    endpoint_lifecycle_status: str | None
    endpoint_runtime_status: str | None
    endpoint_acquisition_status: str | None
    operation_runtime_status: str | None
    operation_acquisition_status: str | None
    artifact_fk_exists: bool | None
    snapshot_endpoint_id: UUID | None = None
    snapshot_operation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class CitationAuthorityAggregate:
    source_decisions: tuple[CitationSourceDecisionProjection, ...]
    member_decisions: tuple[CitationMemberDecisionProjection, ...]
    receipt: CitationAuthorizationReceipt


@dataclass(frozen=True, slots=True)
class CitationAuthorityIssueOutcome:
    decision: CitationAuthorityIssueDecision
    reason: CitationAuthorityIssueReason | None
    receipt: CitationAuthorizationReceipt | None


class RequestGuardRuntimeBindingReaderPort(Protocol):
    async def read_exact(
        self, reference: RequestGuardRuntimeBindingRef
    ) -> RequestGuardRuntimeBindingObservation | None: ...


class RequestCitationAuthorityReaderPort(Protocol):
    async def read_source_decision(
        self, *, request_source_decision_ref: ImmutableArtifactRef
    ) -> AuthoritativeSourceDecisionObservation | None: ...

    async def read_member_decision(
        self, *, request_member_decision_ref: ImmutableArtifactRef
    ) -> AuthoritativeMemberDecisionObservation | None: ...


class SourceUseApprovalExactReaderPort(Protocol):
    async def read_exact(self, identity: SourceUseApprovalIdentity) -> SourceUseApprovalObservation | None: ...


class CitationApprovalPinObservation(Protocol):
    source_snapshot_id: str
    source_use_approval_id: str
    source_code: str
    source_version: str
    approval_version: str
    environment: str
    purpose: SourceUsePurpose


class RuntimeBundleCitationApprovalReaderPort(Protocol):
    async def read_exact(
        self, *, bundle_id: UUID, bundle_manifest_hash: str
    ) -> tuple[CitationApprovalPinObservation, ...]: ...


class CitationEligibilityReaderPort(Protocol):
    async def read_source_exact(self, *, source_snapshot_id: UUID) -> CitationSourceEligibilityObservation | None: ...

    async def read_member_exact(
        self, *, source_snapshot_id: UUID, source_snapshot_member_id: UUID
    ) -> CitationMemberEligibilityObservation | None: ...


class CitationAuthorityStorePort(Protocol):
    async def read_complete(self, *, request_sha256: str) -> CitationAuthorityAggregate | None: ...

    async def append(self, aggregate: CitationAuthorityAggregate) -> None: ...


def _value(value: object) -> object:
    return getattr(value, "value", value)


def _fail(reason: CitationAuthorityIssueReason) -> CitationAuthorityIssueOutcome:
    return CitationAuthorityIssueOutcome(CitationAuthorityIssueDecision.PREREQUISITE_FAILED, reason, None)


def _entry_key(entry: CitationAuthorizationSelectionEntry) -> tuple[object, ...]:
    return (
        entry.source_code,
        entry.source_version,
        entry.member_kind,
        entry.endpoint_code,
        entry.operation_code,
        entry.artifact_code,
        entry.artifact_version,
    )


def _provenance_key(value: SourceExecutionProvenance) -> tuple[object, ...]:
    return (
        value.source_code,
        value.source_version,
        value.member_kind,
        value.endpoint_code,
        value.operation_code,
        value.artifact_code,
        value.artifact_version,
    )


def _source_provenances(
    selection: ValidatedCitationSelection,
) -> dict[tuple[object, ...], list[SourceExecutionProvenance]]:
    source_backed = (
        KnowledgeChunkEvidenceRef,
        InteractionRuleEvidenceRef,
        LifestyleGuidelineEvidenceRef,
        SafetyPolicyEvidenceRef,
    )
    result: dict[tuple[object, ...], list[SourceExecutionProvenance]] = {}
    for citation in selection.candidate_set.citations:
        evidence = citation.evidence_ref
        if isinstance(evidence, source_backed):
            provenance = evidence.execution_provenance
            result.setdefault(_provenance_key(provenance), []).append(provenance)
    return result


def _immutable_ref(value) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(value.artifact_code, value.version, value.content_sha256)


def _request_authority_matches(
    *,
    source: AuthoritativeSourceDecisionObservation,
    member: AuthoritativeMemberDecisionObservation,
    provenance: SourceExecutionProvenance,
    guard: RequestGuardRuntimeBindingObservation,
) -> bool:
    identity = SourceMemberIdentity(
        member_kind=provenance.member_kind,
        endpoint_code=provenance.endpoint_code,
        operation_code=provenance.operation_code,
        artifact_code=provenance.artifact_code,
        artifact_version=provenance.artifact_version,
    )
    legacy = guard.legacy_request_authority_ref
    legacy_tuple = (legacy.artifact_code, legacy.version, legacy.content_sha256)
    return (
        source.artifact_ref == provenance.request_source_decision_ref
        and member.artifact_ref == provenance.request_member_decision_ref
        and (
            source.request_guard_ref.artifact_code,
            source.request_guard_ref.version,
            source.request_guard_ref.content_sha256,
        )
        == legacy_tuple
        and (
            member.request_guard_ref.artifact_code,
            member.request_guard_ref.version,
            member.request_guard_ref.content_sha256,
        )
        == legacy_tuple
        and source.user_id == guard.user_id
        and member.user_id == guard.user_id
        and source.request_operation_code == guard.request_operation_code
        and member.request_operation_code == guard.request_operation_code
        and source.decision_stage == "REQUEST"
        and member.decision_stage == "REQUEST"
        and _value(source.actual_decision_outcome) == "PASS"
        and _value(member.actual_decision_outcome) == "PASS"
        and source.source_snapshot_id == member.source_snapshot_id
        and source.source_code == provenance.source_code
        and source.source_version == provenance.source_version
        and member.member_identity == identity
    )


def _source_decision(
    approval: SourceUseApprovalObservation,
    current: CitationSourceEligibilityObservation,
    evaluation_time: datetime,
) -> tuple[CitationAuthorityDecision, CitationAuthorityReason]:
    if approval.revoked_at is not None:
        return CitationAuthorityDecision.FAIL, CitationAuthorityReason.APPROVAL_REVOKED
    if not approval.is_usable_at(evaluation_time):
        return CitationAuthorityDecision.FAIL, CitationAuthorityReason.APPROVAL_EXPIRED
    if current.source_lifecycle_status != "ACTIVE":
        return CitationAuthorityDecision.FAIL, CitationAuthorityReason.SOURCE_INACTIVE
    if current.snapshot_verification_status != "CURRENT":
        return CitationAuthorityDecision.FAIL, CitationAuthorityReason.SNAPSHOT_NOT_CURRENT
    return CitationAuthorityDecision.PASS, CitationAuthorityReason.ELIGIBLE


def _member_decision(
    entry: CitationAuthorizationSelectionEntry,
    current: CitationMemberEligibilityObservation,
) -> tuple[CitationAuthorityDecision, CitationAuthorityReason]:
    if entry.member_kind is SourceMemberKind.ARTIFACT_MEMBER:
        eligible = (
            current.member_kind == "ARTIFACT"
            and current.ingestion_artifact_id is not None
            and current.endpoint_id is None
            and current.operation_id is None
            and current.artifact_fk_exists is True
        )
        return (
            (CitationAuthorityDecision.PASS, CitationAuthorityReason.ELIGIBLE)
            if eligible
            else (CitationAuthorityDecision.FAIL, CitationAuthorityReason.ARTIFACT_BINDING_INVALID)
        )
    if (
        current.member_kind != "ENDPOINT_OPERATION"
        or current.endpoint_id is None
        or current.snapshot_endpoint_id is None
        or current.endpoint_id != current.snapshot_endpoint_id
        or current.snapshot_operation_id is None
        or (current.operation_id is not None and current.operation_id != current.snapshot_operation_id)
        or current.endpoint_code != entry.endpoint_code
        or current.ingestion_artifact_id is not None
        or (entry.operation_code is not None and current.operation_code != entry.operation_code)
    ):
        return CitationAuthorityDecision.FAIL, CitationAuthorityReason.MEMBER_BINDING_INVALID
    checks = (
        (current.endpoint_lifecycle_status == "VERIFIED", CitationAuthorityReason.ENDPOINT_NOT_VERIFIED),
        (current.endpoint_runtime_status == "ENABLED", CitationAuthorityReason.ENDPOINT_DISABLED),
        (current.endpoint_acquisition_status == "APPROVED", CitationAuthorityReason.ENDPOINT_NOT_APPROVED),
        (current.operation_runtime_status == "ENABLED", CitationAuthorityReason.OPERATION_DISABLED),
        (current.operation_acquisition_status == "APPROVED", CitationAuthorityReason.OPERATION_NOT_APPROVED),
    )
    for passed, reason in checks:
        if not passed:
            return CitationAuthorityDecision.FAIL, reason
    return CitationAuthorityDecision.PASS, CitationAuthorityReason.ELIGIBLE


async def issue_citation_authorization(  # noqa: C901
    *,
    validated_selection: ValidatedCitationSelection,
    request: CitationAuthorizationRequest,
    evaluation_time: datetime,
    guard_reader: RequestGuardRuntimeBindingReaderPort,
    request_authority_reader: RequestCitationAuthorityReaderPort,
    pin_reader: RuntimeBundleCitationApprovalReaderPort,
    approval_reader: SourceUseApprovalExactReaderPort,
    eligibility_reader: CitationEligibilityReaderPort,
    store: CitationAuthorityStorePort,
) -> CitationAuthorityIssueOutcome:
    if evaluation_time.tzinfo is None or evaluation_time.utcoffset() is None:
        raise ValueError("evaluation_time must be timezone-aware")

    existing = await store.read_complete(request_sha256=request.request_sha256)
    if existing is not None:
        verification = verify_citation_authorization_receipt(request, existing.receipt)
        if verification.receipt is None:
            raise CitationAuthorizationAuthorityError(CitationAuthorityIssueReason.EXISTING_RECEIPT_CORRUPT)
        return CitationAuthorityIssueOutcome(CitationAuthorityIssueDecision.REPLAYED, None, existing.receipt)

    guard_ref = request.origin_request_guard.guard_ref
    guard = await guard_reader.read_exact(
        RequestGuardRuntimeBindingRef(guard_ref.artifact_code, guard_ref.version, guard_ref.content_sha256)
    )
    if guard is None:
        return _fail(CitationAuthorityIssueReason.REQUEST_GUARD_ABSENT)
    replayed = build_citation_authorization_request(
        validated_selection,
        build_runtime_authorization_binding(guard),
        build_origin_request_guard_binding(guard),
    )
    if replayed.decision is not AuthorizationBuildDecision.BUILT or replayed.request != request:
        return _fail(CitationAuthorityIssueReason.REQUEST_REPLAY_MISMATCH)

    provenance_by_entry = _source_provenances(validated_selection)
    bindings: list[
        tuple[
            CitationAuthorizationSelectionEntry,
            AuthoritativeSourceDecisionObservation,
            AuthoritativeMemberDecisionObservation,
        ]
    ] = []
    for entry in request.selection_manifest:
        candidates = provenance_by_entry.get(_entry_key(entry), [])
        if not candidates:
            return _fail(CitationAuthorityIssueReason.REQUEST_AUTHORITY_ABSENT)
        coordinates: dict[
            tuple[UUID, UUID], tuple[AuthoritativeSourceDecisionObservation, AuthoritativeMemberDecisionObservation]
        ] = {}
        for provenance in candidates:
            source = await request_authority_reader.read_source_decision(
                request_source_decision_ref=provenance.request_source_decision_ref
            )
            member = await request_authority_reader.read_member_decision(
                request_member_decision_ref=provenance.request_member_decision_ref
            )
            if source is None or member is None:
                return _fail(CitationAuthorityIssueReason.REQUEST_AUTHORITY_ABSENT)
            if not _request_authority_matches(source=source, member=member, provenance=provenance, guard=guard):
                return _fail(CitationAuthorityIssueReason.REQUEST_AUTHORITY_MISMATCH)
            coordinates[(source.source_snapshot_id, member.source_snapshot_member_id)] = (source, member)
        if len(coordinates) != 1:
            return _fail(CitationAuthorityIssueReason.AMBIGUOUS_BINDING)
        source, member = next(iter(coordinates.values()))
        bindings.append((entry, source, member))

    pins = await pin_reader.read_exact(bundle_id=guard.bundle_id, bundle_manifest_hash=guard.bundle_manifest_hash)
    pins_by_snapshot = {UUID(pin.source_snapshot_id): pin for pin in pins}
    source_projections: dict[str, CitationSourceDecisionProjection] = {}
    member_projections: dict[str, CitationMemberDecisionProjection] = {}
    receipt_selections: list[CitationAuthorizationSelectionReceipt] = []
    receipt_projection_selections: list[CitationReceiptSelectionProjection] = []
    for order, (entry, source_authority, member_authority) in enumerate(bindings):
        pin = pins_by_snapshot.get(source_authority.source_snapshot_id)
        if pin is None:
            return _fail(CitationAuthorityIssueReason.BUNDLE_PIN_ABSENT)
        if (
            pin.source_code != source_authority.source_code
            or pin.source_version != source_authority.source_version
            or pin.environment != guard.environment.value
            or pin.purpose is not SourceUsePurpose.PATIENT_CITATION
        ):
            return _fail(CitationAuthorityIssueReason.BUNDLE_PIN_MISMATCH)
        approval_identity = SourceUseApprovalIdentity(
            source_snapshot_id=source_authority.source_snapshot_id,
            source_code=source_authority.source_code,
            source_version=source_authority.source_version,
            environment=RuntimeEnvironmentCode(guard.environment.value),
            purpose=SourceUsePurpose.PATIENT_CITATION,
            approval_version=pin.approval_version,
        )
        approval = await approval_reader.read_exact(approval_identity)
        if approval is None:
            return _fail(CitationAuthorityIssueReason.SOURCE_APPROVAL_ABSENT)
        if approval.id != UUID(pin.source_use_approval_id) or approval.identity != approval_identity:
            return _fail(CitationAuthorityIssueReason.SOURCE_APPROVAL_MISMATCH)
        current_source = await eligibility_reader.read_source_exact(
            source_snapshot_id=source_authority.source_snapshot_id
        )
        if current_source is None:
            return _fail(CitationAuthorityIssueReason.CURRENT_SOURCE_ABSENT)
        if (
            current_source.source_snapshot_id != source_authority.source_snapshot_id
            or current_source.source_code != source_authority.source_code
            or current_source.source_version != source_authority.source_version
        ):
            raise CitationAuthorizationAuthorityError("current source exact binding is corrupt")

        source_outcome, source_reason = _source_decision(approval, current_source, evaluation_time)
        source_projection = CitationSourceDecisionProjection(
            request_sha256=request.request_sha256,
            request_guard_decision_id=guard.request_guard_decision_id,
            origin_guard_artifact_code=guard_ref.artifact_code,
            origin_guard_artifact_version=guard_ref.version,
            origin_guard_content_sha256=guard_ref.content_sha256,
            user_id=guard.user_id,
            request_operation_code=guard.request_operation_code,
            environment=guard.environment.value,
            bundle_id=guard.bundle_id,
            bundle_manifest_hash=guard.bundle_manifest_hash,
            scope_manifest_hash=guard.scope_manifest_hash,
            source_snapshot_id=source_authority.source_snapshot_id,
            source_use_approval_id=approval.id,
            source_code=source_authority.source_code,
            source_version=source_authority.source_version,
            approval_version=pin.approval_version,
            purpose="PATIENT_CITATION",
            evaluation_time=evaluation_time,
            approval_valid_from=approval.valid_from,
            approval_expires_at=approval.expires_at,
            approval_revoked_at=approval.revoked_at,
            source_lifecycle_status=current_source.source_lifecycle_status,
            snapshot_verification_status=current_source.snapshot_verification_status,
            actual_decision_outcome=source_outcome,
            reason_code=source_reason,
        )
        source_ref = compute_citation_source_decision_ref(source_projection)

        if source_outcome is CitationAuthorityDecision.FAIL:
            current_member = None
            member_outcome = CitationAuthorityDecision.FAIL
            member_reason = CitationAuthorityReason.SOURCE_DECISION_FAILED
        else:
            current_member = await eligibility_reader.read_member_exact(
                source_snapshot_id=source_authority.source_snapshot_id,
                source_snapshot_member_id=member_authority.source_snapshot_member_id,
            )
            if current_member is None:
                return _fail(CitationAuthorityIssueReason.CURRENT_MEMBER_ABSENT)
            if (
                current_member.source_snapshot_id != source_authority.source_snapshot_id
                or current_member.source_snapshot_member_id != member_authority.source_snapshot_member_id
            ):
                raise CitationAuthorizationAuthorityError("current member exact binding is corrupt")
            member_outcome, member_reason = _member_decision(entry, current_member)

        member_projection = CitationMemberDecisionProjection(
            request_sha256=request.request_sha256,
            source_decision_content_sha256=source_ref.content_sha256,
            source_snapshot_id=source_authority.source_snapshot_id,
            source_snapshot_member_id=member_authority.source_snapshot_member_id,
            source_code=entry.source_code,
            source_version=entry.source_version,
            member_kind=entry.member_kind.value,
            endpoint_code=entry.endpoint_code,
            operation_code=entry.operation_code,
            artifact_code=entry.artifact_code,
            artifact_version=entry.artifact_version,
            evaluation_time=evaluation_time,
            endpoint_lifecycle_status=current_member.endpoint_lifecycle_status if current_member else None,
            endpoint_runtime_status=current_member.endpoint_runtime_status if current_member else None,
            endpoint_acquisition_status=current_member.endpoint_acquisition_status if current_member else None,
            operation_runtime_status=current_member.operation_runtime_status if current_member else None,
            operation_acquisition_status=current_member.operation_acquisition_status if current_member else None,
            current_member_kind=current_member.member_kind if current_member else None,
            snapshot_endpoint_id=current_member.snapshot_endpoint_id if current_member else None,
            snapshot_operation_id=current_member.snapshot_operation_id if current_member else None,
            current_endpoint_id=current_member.endpoint_id if current_member else None,
            current_operation_id=current_member.operation_id if current_member else None,
            current_ingestion_artifact_id=current_member.ingestion_artifact_id if current_member else None,
            artifact_fk_exists=current_member.artifact_fk_exists if current_member else None,
            actual_decision_outcome=member_outcome,
            reason_code=member_reason,
        )
        member_ref = compute_citation_member_decision_ref(member_projection)
        pure_source_ref = _immutable_ref(source_ref)
        pure_member_ref = _immutable_ref(member_ref)
        receipt_selection = CitationAuthorizationSelectionReceipt(
            selection=entry,
            source_decision_ref=pure_source_ref,
            member_decision_ref=pure_member_ref,
            selected_for_operation=True,
            purpose=UsePurpose.PATIENT_CITATION,
            source_decision=GuardDecision(source_outcome.value),
            member_decision=GuardDecision(member_outcome.value),
        )
        source_projections[source_ref.content_sha256] = source_projection
        member_projections[member_ref.content_sha256] = member_projection
        receipt_selections.append(receipt_selection)
        receipt_projection_selections.append(
            CitationReceiptSelectionProjection(
                selection_order=order,
                source_code=entry.source_code,
                source_version=entry.source_version,
                member_kind=entry.member_kind.value,
                endpoint_code=entry.endpoint_code,
                operation_code=entry.operation_code,
                artifact_code=entry.artifact_code,
                artifact_version=entry.artifact_version,
                source_decision_content_sha256=source_ref.content_sha256,
                member_decision_content_sha256=member_ref.content_sha256,
                selected_for_operation=True,
                purpose="PATIENT_CITATION",
                source_decision=source_outcome.value,
                member_decision=member_outcome.value,
            )
        )

    receipt_projection = CitationReceiptProjection(
        request_sha256=request.request_sha256,
        origin_guard_artifact_code=guard_ref.artifact_code,
        origin_guard_artifact_version=guard_ref.version,
        origin_guard_content_sha256=guard_ref.content_sha256,
        origin_decision="PASS",
        operation="CITATION_AUTHORIZATION",
        environment=request.runtime_binding.environment.value,
        bundle_id=request.runtime_binding.bundle_id,
        bundle_manifest_hash=request.runtime_binding.bundle_manifest_hash,
        request_scope_codes=request.runtime_binding.request_scope_codes,
        scope_manifest_hash=request.runtime_binding.scope_manifest_hash,
        validated_selection_sha256=request.validated_selection_sha256,
        selection_manifest_sha256=request.selection_manifest_sha256,
        selections=tuple(receipt_projection_selections),
    )
    receipt_ref = compute_citation_receipt_ref(receipt_projection)
    receipt = CitationAuthorizationReceipt(
        receipt_ref=_immutable_ref(receipt_ref),
        request_sha256=request.request_sha256,
        origin_guard_ref=request.origin_request_guard.guard_ref,
        origin_decision=GuardDecision.PASS,
        operation=GuardOperation.CITATION_AUTHORIZATION,
        environment=request.runtime_binding.environment,
        bundle_id=request.runtime_binding.bundle_id,
        bundle_manifest_hash=request.runtime_binding.bundle_manifest_hash,
        request_scope_codes=request.runtime_binding.request_scope_codes,
        scope_manifest_hash=request.runtime_binding.scope_manifest_hash,
        validated_selection_sha256=request.validated_selection_sha256,
        selection_manifest_sha256=request.selection_manifest_sha256,
        selections=tuple(receipt_selections),
    )
    verification = verify_citation_authorization_receipt(request, receipt)
    allowed_rejection = verification.reasons == (AuthorizationReason.SELECTION_NOT_AUTHORIZED,)
    if verification.decision is not AuthorizationVerificationDecision.AUTHORIZED and not allowed_rejection:
        return _fail(CitationAuthorityIssueReason.ISSUER_RECEIPT_INVALID)
    aggregate = CitationAuthorityAggregate(
        tuple(source_projections[key] for key in sorted(source_projections)),
        tuple(member_projections[key] for key in sorted(member_projections)),
        receipt,
    )
    await store.append(aggregate)
    return CitationAuthorityIssueOutcome(CitationAuthorityIssueDecision.ISSUED, None, receipt)


__all__ = [
    "CitationAuthorizationAuthorityError",
    "CitationAuthorityAggregate",
    "CitationAuthorityIssueDecision",
    "CitationAuthorityIssueOutcome",
    "CitationAuthorityIssueReason",
    "CitationAuthorityStorePort",
    "CitationEligibilityReaderPort",
    "CitationMemberEligibilityObservation",
    "CitationSourceEligibilityObservation",
    "issue_citation_authorization",
]
