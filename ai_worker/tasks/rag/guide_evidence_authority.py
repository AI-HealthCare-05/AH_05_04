"""Sync Guide Evidence Authority Assembly Contract Kernel (#672 Prerequisite).

Persistence-free read-only authority assembly seam between authoritative
REQUEST Guard, Source Decision, and Member Decision observations and the
downstream `RequestSourceMemberBinding` consumed by Guide Evidence Handoff.

Scope & Authority Boundaries:
- Pure/Read-Only Seam: This module provides side-effect-free, in-memory authority
  assembly. It performs no database writes, no migrations, no network I/O, and
  does not evaluate or issue new Decisions.
- Authoritative Verification: Callers cannot supply PASS outcomes. Decisions and
  PASS outcomes are sourced exclusively from `GuideEvidenceAuthorityReaderPort`
  using exact artifact references.
- Exact Bindings: Exact match is enforced on user ownership, REQUEST guard origin,
  request operation code, REQUEST decision stage, source coordinates, and member
  identity via `verify_member_authority_binding`.
- Excluded Authority: Assessment authenticity (`assessment_artifact_ref`,
  `eligibility_receipt_ref`, assessment freshness) is NOT authenticated by this
  kernel. That responsibility is handled by Evidence Gate / #180 runtime.
- Fail-Closed Atomicity: Rejection is phase-ordered and fail-fast; if any
  selection fails, no partial bindings are returned (`bindings = ()`).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    is_valid_immutable_artifact_ref,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    ObservedDecisionOutcome,
    RequestDecisionStage,
    RequestSourceMemberBinding,
)
from ai_worker.tasks.rag.source_member_identity import (
    SourceMemberIdentity,
    is_valid_source_member_identity,
    verify_member_authority_binding,
)

__all__ = [
    "AuthoritativeMemberDecisionObservation",
    "AuthoritativeRequestGuardObservation",
    "AuthoritativeSourceDecisionObservation",
    "GuideEvidenceAuthorityReaderError",
    "GuideEvidenceAuthorityReaderPort",
    "GuideRequestAuthorityDecisionRefs",
    "GuideRequestAuthorityLookupCoordinate",
    "GuideRequestAuthorityLookupPort",
    "GuideRequestAuthoritySelectedMember",
    "GuideSelectedMemberAuthorityResolverPort",
    "SyncGuideEvidenceAuthorityDecision",
    "SyncGuideEvidenceAuthorityOutcome",
    "SyncGuideEvidenceAuthorityReason",
    "SyncGuideEvidenceAuthorityRequest",
    "SyncGuideEvidenceAuthoritySelection",
    "assemble_sync_guide_evidence_authority",
]


class SyncGuideEvidenceAuthorityDecision(StrEnum):
    AUTHENTICATED = "AUTHENTICATED"
    REJECTED = "REJECTED"


class SyncGuideEvidenceAuthorityReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    REQUEST_GUARD_NOT_FOUND = "REQUEST_GUARD_NOT_FOUND"
    AUTHORITY_REF_MISMATCH = "AUTHORITY_REF_MISMATCH"
    SOURCE_DECISION_NOT_FOUND = "SOURCE_DECISION_NOT_FOUND"
    MEMBER_DECISION_NOT_FOUND = "MEMBER_DECISION_NOT_FOUND"
    OWNER_MISMATCH = "OWNER_MISMATCH"
    REQUEST_OPERATION_MISMATCH = "REQUEST_OPERATION_MISMATCH"
    DECISION_STAGE_MISMATCH = "DECISION_STAGE_MISMATCH"
    SOURCE_DECISION_NOT_PASS = "SOURCE_DECISION_NOT_PASS"
    MEMBER_DECISION_NOT_PASS = "MEMBER_DECISION_NOT_PASS"
    SOURCE_BINDING_MISMATCH = "SOURCE_BINDING_MISMATCH"
    MEMBER_BINDING_MISMATCH = "MEMBER_BINDING_MISMATCH"
    AUTHORITY_READER_ERROR = "AUTHORITY_READER_ERROR"


@dataclass(frozen=True, slots=True)
class AuthoritativeRequestGuardObservation:
    artifact_ref: ImmutableArtifactRef
    user_id: UUID
    request_operation_code: str
    decision_stage: str


@dataclass(frozen=True, slots=True)
class AuthoritativeSourceDecisionObservation:
    artifact_ref: ImmutableArtifactRef
    request_guard_ref: ImmutableArtifactRef
    user_id: UUID
    request_operation_code: str
    decision_stage: str
    source_snapshot_id: UUID
    source_code: str
    source_version: str
    actual_decision_outcome: ObservedDecisionOutcome


@dataclass(frozen=True, slots=True)
class AuthoritativeMemberDecisionObservation:
    artifact_ref: ImmutableArtifactRef
    request_guard_ref: ImmutableArtifactRef
    user_id: UUID
    request_operation_code: str
    decision_stage: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    member_identity: SourceMemberIdentity
    actual_decision_outcome: ObservedDecisionOutcome


class GuideEvidenceAuthorityReaderError(Exception):
    """Explicit dependency failure when reading authoritative guard or decisions.

    Callers of the assembly seam convert only this exception into AUTHORITY_READER_ERROR.
    Programming errors and unexpected exceptions propagate unhandled.
    """


class GuideEvidenceAuthorityReaderPort(Protocol):
    async def read_request_guard(
        self,
        *,
        request_guard_ref: ImmutableArtifactRef,
    ) -> AuthoritativeRequestGuardObservation | None: ...

    async def read_source_decision(
        self,
        *,
        request_source_decision_ref: ImmutableArtifactRef,
    ) -> AuthoritativeSourceDecisionObservation | None: ...

    async def read_member_decision(
        self,
        *,
        request_member_decision_ref: ImmutableArtifactRef,
    ) -> AuthoritativeMemberDecisionObservation | None: ...


@dataclass(frozen=True, slots=True)
class GuideRequestAuthorityLookupCoordinate:
    """Pinned request and selected-member facts used for an exact historical lookup."""

    request_guard_ref: ImmutableArtifactRef
    user_id: UUID
    request_operation_code: str
    decision_stage: RequestDecisionStage
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    expected_source_decision_outcome: ObservedDecisionOutcome
    expected_member_decision_outcome: ObservedDecisionOutcome
    member_identity: SourceMemberIdentity


@dataclass(frozen=True, slots=True)
class GuideRequestAuthorityDecisionRefs:
    request_source_decision_ref: ImmutableArtifactRef
    request_member_decision_ref: ImmutableArtifactRef


class GuideRequestAuthorityLookupPort(Protocol):
    async def lookup_request_decision_refs(
        self,
        *,
        coordinate: GuideRequestAuthorityLookupCoordinate,
    ) -> GuideRequestAuthorityDecisionRefs | None: ...


@dataclass(frozen=True, slots=True)
class GuideRequestAuthoritySelectedMember:
    """Historical REQUEST-selected member restored without caller identity input."""

    request_guard_ref: ImmutableArtifactRef
    user_id: UUID
    request_operation_code: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    member_identity: SourceMemberIdentity
    request_source_decision_ref: ImmutableArtifactRef
    request_member_decision_ref: ImmutableArtifactRef


class GuideSelectedMemberAuthorityResolverPort(Protocol):
    async def resolve_selected_member(
        self,
        *,
        request_guard_ref: ImmutableArtifactRef,
        user_id: UUID,
        request_operation_code: str,
        source_snapshot_id: UUID,
        source_snapshot_member_id: UUID,
        expected_decision_outcome: ObservedDecisionOutcome,
    ) -> GuideRequestAuthoritySelectedMember | None: ...


@dataclass(frozen=True, slots=True)
class SyncGuideEvidenceAuthoritySelection:
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    member_identity: SourceMemberIdentity
    request_source_decision_ref: ImmutableArtifactRef
    request_member_decision_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class SyncGuideEvidenceAuthorityRequest:
    user_id: UUID
    request_guard_ref: ImmutableArtifactRef
    request_operation_code: str
    selections: tuple[SyncGuideEvidenceAuthoritySelection, ...]


@dataclass(frozen=True, slots=True)
class SyncGuideEvidenceAuthorityOutcome:
    decision: SyncGuideEvidenceAuthorityDecision
    reasons: tuple[SyncGuideEvidenceAuthorityReason, ...]
    bindings: tuple[RequestSourceMemberBinding, ...] = ()


def _is_nonblank_nfc(val: object) -> bool:
    return isinstance(val, str) and len(val) > 0 and val == val.strip() and unicodedata.is_normalized("NFC", val)


def _request_shape_is_valid(request: SyncGuideEvidenceAuthorityRequest) -> bool:
    if type(request) is not SyncGuideEvidenceAuthorityRequest:
        return False
    if (
        type(request.user_id) is not UUID
        or not is_valid_immutable_artifact_ref(request.request_guard_ref)
        or not _is_nonblank_nfc(request.request_operation_code)
        or type(request.selections) is not tuple
        or not request.selections
    ):
        return False

    for sel in request.selections:
        if (
            type(sel) is not SyncGuideEvidenceAuthoritySelection
            or type(sel.source_snapshot_id) is not UUID
            or type(sel.source_snapshot_member_id) is not UUID
            or not _is_nonblank_nfc(sel.source_code)
            or not _is_nonblank_nfc(sel.source_version)
            or type(sel.member_identity) is not SourceMemberIdentity
            or not is_valid_source_member_identity(sel.member_identity)
            or not is_valid_immutable_artifact_ref(sel.request_source_decision_ref)
            or not is_valid_immutable_artifact_ref(sel.request_member_decision_ref)
        ):
            return False

    return True


async def _verify_guard(
    request: SyncGuideEvidenceAuthorityRequest,
    reader: GuideEvidenceAuthorityReaderPort,
) -> tuple[AuthoritativeRequestGuardObservation | None, SyncGuideEvidenceAuthorityReason | None]:
    try:
        guard_obs = await reader.read_request_guard(request_guard_ref=request.request_guard_ref)
    except GuideEvidenceAuthorityReaderError:
        return None, SyncGuideEvidenceAuthorityReason.AUTHORITY_READER_ERROR

    if guard_obs is None:
        return None, SyncGuideEvidenceAuthorityReason.REQUEST_GUARD_NOT_FOUND
    if guard_obs.artifact_ref != request.request_guard_ref:
        return None, SyncGuideEvidenceAuthorityReason.AUTHORITY_REF_MISMATCH
    if guard_obs.user_id != request.user_id:
        return None, SyncGuideEvidenceAuthorityReason.OWNER_MISMATCH
    if guard_obs.request_operation_code != request.request_operation_code:
        return None, SyncGuideEvidenceAuthorityReason.REQUEST_OPERATION_MISMATCH
    if guard_obs.decision_stage != RequestDecisionStage.REQUEST.value:
        return None, SyncGuideEvidenceAuthorityReason.DECISION_STAGE_MISMATCH

    return guard_obs, None


async def _verify_source_decision(
    request: SyncGuideEvidenceAuthorityRequest,
    sel: SyncGuideEvidenceAuthoritySelection,
    reader: GuideEvidenceAuthorityReaderPort,
) -> tuple[AuthoritativeSourceDecisionObservation | None, SyncGuideEvidenceAuthorityReason | None]:
    try:
        src_obs = await reader.read_source_decision(
            request_source_decision_ref=sel.request_source_decision_ref,
        )
    except GuideEvidenceAuthorityReaderError:
        return None, SyncGuideEvidenceAuthorityReason.AUTHORITY_READER_ERROR

    if src_obs is None:
        return None, SyncGuideEvidenceAuthorityReason.SOURCE_DECISION_NOT_FOUND
    if src_obs.artifact_ref != sel.request_source_decision_ref:
        return None, SyncGuideEvidenceAuthorityReason.AUTHORITY_REF_MISMATCH
    if src_obs.request_guard_ref != request.request_guard_ref:
        return None, SyncGuideEvidenceAuthorityReason.SOURCE_BINDING_MISMATCH
    if src_obs.user_id != request.user_id:
        return None, SyncGuideEvidenceAuthorityReason.OWNER_MISMATCH
    if src_obs.request_operation_code != request.request_operation_code:
        return None, SyncGuideEvidenceAuthorityReason.REQUEST_OPERATION_MISMATCH
    if src_obs.decision_stage != RequestDecisionStage.REQUEST.value:
        return None, SyncGuideEvidenceAuthorityReason.DECISION_STAGE_MISMATCH
    if src_obs.actual_decision_outcome != ObservedDecisionOutcome.PASS:
        return None, SyncGuideEvidenceAuthorityReason.SOURCE_DECISION_NOT_PASS
    if (
        src_obs.source_snapshot_id != sel.source_snapshot_id
        or src_obs.source_code != sel.source_code
        or src_obs.source_version != sel.source_version
    ):
        return None, SyncGuideEvidenceAuthorityReason.SOURCE_BINDING_MISMATCH

    return src_obs, None


def _check_member_obs(
    mem_obs: AuthoritativeMemberDecisionObservation,
    request: SyncGuideEvidenceAuthorityRequest,
    sel: SyncGuideEvidenceAuthoritySelection,
    src_obs: AuthoritativeSourceDecisionObservation,
) -> SyncGuideEvidenceAuthorityReason | None:
    if mem_obs.artifact_ref != sel.request_member_decision_ref:
        return SyncGuideEvidenceAuthorityReason.AUTHORITY_REF_MISMATCH
    if mem_obs.request_guard_ref != request.request_guard_ref:
        return SyncGuideEvidenceAuthorityReason.MEMBER_BINDING_MISMATCH
    if mem_obs.user_id != request.user_id:
        return SyncGuideEvidenceAuthorityReason.OWNER_MISMATCH
    if mem_obs.request_operation_code != request.request_operation_code:
        return SyncGuideEvidenceAuthorityReason.REQUEST_OPERATION_MISMATCH
    if mem_obs.decision_stage != RequestDecisionStage.REQUEST.value:
        return SyncGuideEvidenceAuthorityReason.DECISION_STAGE_MISMATCH
    if mem_obs.actual_decision_outcome != ObservedDecisionOutcome.PASS:
        return SyncGuideEvidenceAuthorityReason.MEMBER_DECISION_NOT_PASS
    if (
        mem_obs.source_snapshot_id != sel.source_snapshot_id
        or mem_obs.source_snapshot_id != src_obs.source_snapshot_id
        or mem_obs.source_snapshot_member_id != sel.source_snapshot_member_id
    ):
        return SyncGuideEvidenceAuthorityReason.MEMBER_BINDING_MISMATCH

    if verify_member_authority_binding(
        observed=mem_obs.member_identity,
        selected=sel.member_identity,
    ):
        return SyncGuideEvidenceAuthorityReason.MEMBER_BINDING_MISMATCH

    return None


async def _verify_member_decision(
    request: SyncGuideEvidenceAuthorityRequest,
    sel: SyncGuideEvidenceAuthoritySelection,
    src_obs: AuthoritativeSourceDecisionObservation,
    reader: GuideEvidenceAuthorityReaderPort,
) -> tuple[AuthoritativeMemberDecisionObservation | None, SyncGuideEvidenceAuthorityReason | None]:
    try:
        mem_obs = await reader.read_member_decision(
            request_member_decision_ref=sel.request_member_decision_ref,
        )
    except GuideEvidenceAuthorityReaderError:
        return None, SyncGuideEvidenceAuthorityReason.AUTHORITY_READER_ERROR

    if mem_obs is None:
        return None, SyncGuideEvidenceAuthorityReason.MEMBER_DECISION_NOT_FOUND

    err = _check_member_obs(mem_obs, request, sel, src_obs)
    if err is not None:
        return None, err

    return mem_obs, None


async def assemble_sync_guide_evidence_authority(
    request: SyncGuideEvidenceAuthorityRequest,
    reader: GuideEvidenceAuthorityReaderPort,
) -> SyncGuideEvidenceAuthorityOutcome:
    """Assemble authenticated RequestSourceMemberBinding instances fail-closed.

    Validation is phase-ordered and fail-fast:
    - Phase 1: Request Structural Validation
    - Phase 2: Authoritative Guard Verification
    - Phase 3: Selections Verification (in selection order, fail-fast)
    """
    # --------------------------------------------------------------------------
    # Phase 1: Request Structural Validation
    # --------------------------------------------------------------------------
    if not _request_shape_is_valid(request):
        return SyncGuideEvidenceAuthorityOutcome(
            decision=SyncGuideEvidenceAuthorityDecision.REJECTED,
            reasons=(SyncGuideEvidenceAuthorityReason.REQUEST_INVALID,),
            bindings=(),
        )

    # --------------------------------------------------------------------------
    # Phase 2: Authoritative Guard Verification
    # --------------------------------------------------------------------------
    _, guard_err = await _verify_guard(request, reader)
    if guard_err is not None:
        return SyncGuideEvidenceAuthorityOutcome(
            decision=SyncGuideEvidenceAuthorityDecision.REJECTED,
            reasons=(guard_err,),
            bindings=(),
        )

    # --------------------------------------------------------------------------
    # Phase 3: Selections Verification (in selection order, fail-fast)
    # --------------------------------------------------------------------------
    assembled_bindings: list[RequestSourceMemberBinding] = []

    for sel in request.selections:
        src_obs, src_err = await _verify_source_decision(request, sel, reader)
        if src_err is not None:
            return SyncGuideEvidenceAuthorityOutcome(
                decision=SyncGuideEvidenceAuthorityDecision.REJECTED,
                reasons=(src_err,),
                bindings=(),
            )
        assert src_obs is not None

        _, mem_err = await _verify_member_decision(request, sel, src_obs, reader)
        if mem_err is not None:
            return SyncGuideEvidenceAuthorityOutcome(
                decision=SyncGuideEvidenceAuthorityDecision.REJECTED,
                reasons=(mem_err,),
                bindings=(),
            )

        assembled_bindings.append(
            RequestSourceMemberBinding(
                request_guard_ref=request.request_guard_ref,
                request_operation_code=request.request_operation_code,
                source_snapshot_id=sel.source_snapshot_id,
                source_snapshot_member_id=sel.source_snapshot_member_id,
                source_code=sel.source_code,
                source_version=sel.source_version,
                member_kind=sel.member_identity.member_kind,
                request_source_decision_ref=sel.request_source_decision_ref,
                request_member_decision_ref=sel.request_member_decision_ref,
                observed_source_decision_outcome=ObservedDecisionOutcome.PASS,
                observed_member_decision_outcome=ObservedDecisionOutcome.PASS,
                request_decision_stage=RequestDecisionStage.REQUEST,
                endpoint_code=sel.member_identity.endpoint_code,
                operation_code=sel.member_identity.operation_code,
                artifact_code=sel.member_identity.artifact_code,
                artifact_version=sel.member_identity.artifact_version,
            )
        )

    return SyncGuideEvidenceAuthorityOutcome(
        decision=SyncGuideEvidenceAuthorityDecision.AUTHENTICATED,
        reasons=(),
        bindings=tuple(assembled_bindings),
    )
