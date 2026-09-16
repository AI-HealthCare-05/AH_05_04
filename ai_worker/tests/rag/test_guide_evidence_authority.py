"""Unit tests for Sync Guide Evidence Authority Assembly Contract (#672 Prerequisite)."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.guide_evidence_authority import (
    AuthoritativeMemberDecisionObservation,
    AuthoritativeRequestGuardObservation,
    AuthoritativeSourceDecisionObservation,
    GuideEvidenceAuthorityReaderError,
    GuideEvidenceAuthorityReaderPort,
    SyncGuideEvidenceAuthorityDecision,
    SyncGuideEvidenceAuthorityReason,
    SyncGuideEvidenceAuthorityRequest,
    SyncGuideEvidenceAuthoritySelection,
    assemble_sync_guide_evidence_authority,
)
from ai_worker.tasks.rag.guide_evidence_handoff import (
    ObservedDecisionOutcome,
    RequestDecisionStage,
    RequestSourceMemberBinding,
)
from ai_worker.tasks.rag.source_member_identity import (
    SourceMemberIdentity,
    SourceMemberKind,
)


def _art(code: str, version: str = "v1", digest: str | None = None) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(
        artifact_code=code,
        version=version,
        content_sha256=digest or ("a" * 64),
    )


@dataclass
class SyntheticGuideEvidenceAuthorityReader(GuideEvidenceAuthorityReaderPort):
    guards: dict[ImmutableArtifactRef, AuthoritativeRequestGuardObservation] = field(default_factory=dict)
    source_decisions: dict[ImmutableArtifactRef, AuthoritativeSourceDecisionObservation] = field(default_factory=dict)
    member_decisions: dict[ImmutableArtifactRef, AuthoritativeMemberDecisionObservation] = field(default_factory=dict)
    call_counts: dict[str, int] = field(default_factory=lambda: {"guard": 0, "source": 0, "member": 0})
    error_trigger: str | None = None
    runtime_error_trigger: str | None = None

    async def read_request_guard(
        self,
        *,
        request_guard_ref: ImmutableArtifactRef,
    ) -> AuthoritativeRequestGuardObservation | None:
        self.call_counts["guard"] += 1
        if self.runtime_error_trigger == "guard":
            raise RuntimeError("Unexpected boom in read_request_guard")
        if self.error_trigger == "guard":
            raise GuideEvidenceAuthorityReaderError("Storage connection failed reading guard")
        return self.guards.get(request_guard_ref)

    async def read_source_decision(
        self,
        *,
        request_source_decision_ref: ImmutableArtifactRef,
    ) -> AuthoritativeSourceDecisionObservation | None:
        self.call_counts["source"] += 1
        if self.runtime_error_trigger == "source":
            raise RuntimeError("Unexpected boom in read_source_decision")
        if self.error_trigger == "source":
            raise GuideEvidenceAuthorityReaderError("Storage connection failed reading source decision")
        return self.source_decisions.get(request_source_decision_ref)

    async def read_member_decision(
        self,
        *,
        request_member_decision_ref: ImmutableArtifactRef,
    ) -> AuthoritativeMemberDecisionObservation | None:
        self.call_counts["member"] += 1
        if self.runtime_error_trigger == "member":
            raise RuntimeError("Unexpected boom in read_member_decision")
        if self.error_trigger == "member":
            raise GuideEvidenceAuthorityReaderError("Storage connection failed reading member decision")
        return self.member_decisions.get(request_member_decision_ref)


def _setup_baseline() -> tuple[
    SyncGuideEvidenceAuthorityRequest,
    SyntheticGuideEvidenceAuthorityReader,
    UUID,
    ImmutableArtifactRef,
    ImmutableArtifactRef,
    ImmutableArtifactRef,
]:
    user_id = uuid4()
    guard_ref = _art("guard_artifact", "v1", "1" * 64)
    src_dec_ref = _art("src_dec_artifact", "v1", "2" * 64)
    mem_dec_ref = _art("mem_dec_artifact", "v1", "3" * 64)

    snapshot_id = uuid4()
    member_id = uuid4()
    op_code = "guide.medication.inquiry"

    member_identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="kims_api",
        operation_code="drug_detail",
    )

    selection = SyncGuideEvidenceAuthoritySelection(
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        source_code="KIMS",
        source_version="2026.09",
        member_identity=member_identity,
        request_source_decision_ref=src_dec_ref,
        request_member_decision_ref=mem_dec_ref,
    )

    request = SyncGuideEvidenceAuthorityRequest(
        user_id=user_id,
        request_guard_ref=guard_ref,
        request_operation_code=op_code,
        selections=(selection,),
    )

    reader = SyntheticGuideEvidenceAuthorityReader(
        guards={
            guard_ref: AuthoritativeRequestGuardObservation(
                artifact_ref=guard_ref,
                user_id=user_id,
                request_operation_code=op_code,
                decision_stage="REQUEST",
            )
        },
        source_decisions={
            src_dec_ref: AuthoritativeSourceDecisionObservation(
                artifact_ref=src_dec_ref,
                request_guard_ref=guard_ref,
                user_id=user_id,
                request_operation_code=op_code,
                decision_stage="REQUEST",
                source_snapshot_id=snapshot_id,
                source_code="KIMS",
                source_version="2026.09",
                actual_decision_outcome=ObservedDecisionOutcome.PASS,
            )
        },
        member_decisions={
            mem_dec_ref: AuthoritativeMemberDecisionObservation(
                artifact_ref=mem_dec_ref,
                request_guard_ref=guard_ref,
                user_id=user_id,
                request_operation_code=op_code,
                decision_stage="REQUEST",
                source_snapshot_id=snapshot_id,
                source_snapshot_member_id=member_id,
                member_identity=member_identity,
                actual_decision_outcome=ObservedDecisionOutcome.PASS,
            )
        },
    )

    return request, reader, user_id, guard_ref, src_dec_ref, mem_dec_ref


# ==============================================================================
# Scenario 14 & 13: Normal happy paths (Endpoint & Nullable operation_code & Artifact member)
# ==============================================================================


@pytest.mark.asyncio
async def test_successful_assembly_creates_request_source_member_binding() -> None:
    request, reader, user_id, guard_ref, src_dec_ref, mem_dec_ref = _setup_baseline()

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.AUTHENTICATED
    assert outcome.reasons == ()
    assert len(outcome.bindings) == 1

    binding = outcome.bindings[0]
    assert isinstance(binding, RequestSourceMemberBinding)
    assert binding.request_guard_ref == guard_ref
    assert binding.request_source_decision_ref == src_dec_ref
    assert binding.request_member_decision_ref == mem_dec_ref
    assert binding.observed_source_decision_outcome == ObservedDecisionOutcome.PASS
    assert binding.observed_member_decision_outcome == ObservedDecisionOutcome.PASS
    assert binding.request_decision_stage == RequestDecisionStage.REQUEST
    assert binding.source_code == "KIMS"
    assert binding.source_version == "2026.09"
    assert binding.member_kind == SourceMemberKind.ENDPOINT_OPERATION
    assert binding.endpoint_code == "kims_api"
    assert binding.operation_code == "drug_detail"
    assert binding.artifact_code is None
    assert binding.artifact_version is None


@pytest.mark.asyncio
async def test_successful_assembly_with_nullable_endpoint_operation_code() -> None:
    request, reader, user_id, guard_ref, src_dec_ref, mem_dec_ref = _setup_baseline()
    snapshot_id = request.selections[0].source_snapshot_id
    member_id = request.selections[0].source_snapshot_member_id

    nullable_identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="kims_api",
        operation_code=None,
    )

    new_selection = SyncGuideEvidenceAuthoritySelection(
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        source_code="KIMS",
        source_version="2026.09",
        member_identity=nullable_identity,
        request_source_decision_ref=src_dec_ref,
        request_member_decision_ref=mem_dec_ref,
    )
    req = SyncGuideEvidenceAuthorityRequest(
        user_id=user_id,
        request_guard_ref=guard_ref,
        request_operation_code="guide.medication.inquiry",
        selections=(new_selection,),
    )

    reader.member_decisions[mem_dec_ref] = AuthoritativeMemberDecisionObservation(
        artifact_ref=mem_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        member_identity=nullable_identity,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(req, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.AUTHENTICATED
    assert outcome.reasons == ()
    assert len(outcome.bindings) == 1
    assert outcome.bindings[0].operation_code is None
    assert outcome.bindings[0].endpoint_code == "kims_api"


@pytest.mark.asyncio
async def test_successful_assembly_with_artifact_member() -> None:
    request, reader, user_id, guard_ref, src_dec_ref, mem_dec_ref = _setup_baseline()
    snapshot_id = request.selections[0].source_snapshot_id
    member_id = request.selections[0].source_snapshot_member_id

    art_identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        artifact_code="kims_pkg_insert",
        artifact_version="v2.1",
    )

    new_selection = SyncGuideEvidenceAuthoritySelection(
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        source_code="KIMS",
        source_version="2026.09",
        member_identity=art_identity,
        request_source_decision_ref=src_dec_ref,
        request_member_decision_ref=mem_dec_ref,
    )
    req = SyncGuideEvidenceAuthorityRequest(
        user_id=user_id,
        request_guard_ref=guard_ref,
        request_operation_code="guide.medication.inquiry",
        selections=(new_selection,),
    )

    reader.member_decisions[mem_dec_ref] = AuthoritativeMemberDecisionObservation(
        artifact_ref=mem_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        member_identity=art_identity,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(req, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.AUTHENTICATED
    assert outcome.reasons == ()
    assert len(outcome.bindings) == 1
    assert outcome.bindings[0].member_kind == SourceMemberKind.ARTIFACT_MEMBER
    assert outcome.bindings[0].artifact_code == "kims_pkg_insert"
    assert outcome.bindings[0].artifact_version == "v2.1"
    assert outcome.bindings[0].endpoint_code is None
    assert outcome.bindings[0].operation_code is None


# ==============================================================================
# Scenario 1, 2, 3: Not found rejections
# ==============================================================================


@pytest.mark.asyncio
async def test_rejects_when_request_guard_not_found() -> None:
    request, reader, _, _, _, _ = _setup_baseline()
    reader.guards.clear()

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.REQUEST_GUARD_NOT_FOUND,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_source_decision_not_found() -> None:
    request, reader, _, _, _, _ = _setup_baseline()
    reader.source_decisions.clear()

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.SOURCE_DECISION_NOT_FOUND,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_member_decision_not_found() -> None:
    request, reader, _, _, _, _ = _setup_baseline()
    reader.member_decisions.clear()

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.MEMBER_DECISION_NOT_FOUND,)
    assert outcome.bindings == ()


# ==============================================================================
# Scenario 18: Reader returned artifact_ref mismatch (AUTHORITY_REF_MISMATCH)
# ==============================================================================


@pytest.mark.asyncio
async def test_rejects_when_guard_reader_returns_mismatched_artifact_ref() -> None:
    request, reader, user_id, guard_ref, _, _ = _setup_baseline()
    bogus_ref = _art("other_guard", "v1", "9" * 64)
    reader.guards[guard_ref] = AuthoritativeRequestGuardObservation(
        artifact_ref=bogus_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.AUTHORITY_REF_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_source_decision_reader_returns_mismatched_artifact_ref() -> None:
    request, reader, user_id, guard_ref, src_dec_ref, _ = _setup_baseline()
    bogus_ref = _art("other_source_dec", "v1", "9" * 64)
    obs = reader.source_decisions[src_dec_ref]
    reader.source_decisions[src_dec_ref] = AuthoritativeSourceDecisionObservation(
        artifact_ref=bogus_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_code=obs.source_code,
        source_version=obs.source_version,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.AUTHORITY_REF_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_member_decision_reader_returns_mismatched_artifact_ref() -> None:
    request, reader, user_id, guard_ref, _, mem_dec_ref = _setup_baseline()
    bogus_ref = _art("other_member_dec", "v1", "9" * 64)
    obs = reader.member_decisions[mem_dec_ref]
    reader.member_decisions[mem_dec_ref] = AuthoritativeMemberDecisionObservation(
        artifact_ref=bogus_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_snapshot_member_id=obs.source_snapshot_member_id,
        member_identity=obs.member_identity,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.AUTHORITY_REF_MISMATCH,)
    assert outcome.bindings == ()


# ==============================================================================
# Scenario 4: User ownership mismatch
# ==============================================================================


@pytest.mark.asyncio
async def test_rejects_when_guard_owned_by_another_user() -> None:
    request, reader, _, guard_ref, _, _ = _setup_baseline()
    attacker_user_id = uuid4()
    reader.guards[guard_ref] = AuthoritativeRequestGuardObservation(
        artifact_ref=guard_ref,
        user_id=attacker_user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.OWNER_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_source_decision_owned_by_another_user() -> None:
    request, reader, _, guard_ref, src_dec_ref, _ = _setup_baseline()
    attacker_user_id = uuid4()
    obs = reader.source_decisions[src_dec_ref]
    reader.source_decisions[src_dec_ref] = AuthoritativeSourceDecisionObservation(
        artifact_ref=src_dec_ref,
        request_guard_ref=guard_ref,
        user_id=attacker_user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_code=obs.source_code,
        source_version=obs.source_version,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.OWNER_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_member_decision_owned_by_another_user() -> None:
    request, reader, _, guard_ref, _, mem_dec_ref = _setup_baseline()
    attacker_user_id = uuid4()
    obs = reader.member_decisions[mem_dec_ref]
    reader.member_decisions[mem_dec_ref] = AuthoritativeMemberDecisionObservation(
        artifact_ref=mem_dec_ref,
        request_guard_ref=guard_ref,
        user_id=attacker_user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_snapshot_member_id=obs.source_snapshot_member_id,
        member_identity=obs.member_identity,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.OWNER_MISMATCH,)
    assert outcome.bindings == ()


# ==============================================================================
# Scenario 5: Request operation mismatch
# ==============================================================================


@pytest.mark.asyncio
async def test_rejects_when_guard_operation_mismatch() -> None:
    request, reader, user_id, guard_ref, _, _ = _setup_baseline()
    reader.guards[guard_ref] = AuthoritativeRequestGuardObservation(
        artifact_ref=guard_ref,
        user_id=user_id,
        request_operation_code="chat.routine.inquiry",
        decision_stage="REQUEST",
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.REQUEST_OPERATION_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_source_decision_operation_mismatch() -> None:
    request, reader, user_id, guard_ref, src_dec_ref, _ = _setup_baseline()
    obs = reader.source_decisions[src_dec_ref]
    reader.source_decisions[src_dec_ref] = AuthoritativeSourceDecisionObservation(
        artifact_ref=src_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="chat.routine.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_code=obs.source_code,
        source_version=obs.source_version,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.REQUEST_OPERATION_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_member_decision_operation_mismatch() -> None:
    request, reader, user_id, guard_ref, _, mem_dec_ref = _setup_baseline()
    obs = reader.member_decisions[mem_dec_ref]
    reader.member_decisions[mem_dec_ref] = AuthoritativeMemberDecisionObservation(
        artifact_ref=mem_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="chat.routine.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_snapshot_member_id=obs.source_snapshot_member_id,
        member_identity=obs.member_identity,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.REQUEST_OPERATION_MISMATCH,)
    assert outcome.bindings == ()


# ==============================================================================
# Scenario 6: Decision stage != REQUEST (Raw string validation)
# ==============================================================================


@pytest.mark.asyncio
async def test_rejects_when_guard_decision_stage_is_not_request() -> None:
    request, reader, user_id, guard_ref, _, _ = _setup_baseline()
    reader.guards[guard_ref] = AuthoritativeRequestGuardObservation(
        artifact_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="EXECUTION",
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.DECISION_STAGE_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_source_decision_stage_is_not_request() -> None:
    request, reader, user_id, guard_ref, src_dec_ref, _ = _setup_baseline()
    obs = reader.source_decisions[src_dec_ref]
    reader.source_decisions[src_dec_ref] = AuthoritativeSourceDecisionObservation(
        artifact_ref=src_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="INTAKE",
        source_snapshot_id=obs.source_snapshot_id,
        source_code=obs.source_code,
        source_version=obs.source_version,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.DECISION_STAGE_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_member_decision_stage_is_not_request() -> None:
    request, reader, user_id, guard_ref, _, mem_dec_ref = _setup_baseline()
    obs = reader.member_decisions[mem_dec_ref]
    reader.member_decisions[mem_dec_ref] = AuthoritativeMemberDecisionObservation(
        artifact_ref=mem_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="INTAKE",
        source_snapshot_id=obs.source_snapshot_id,
        source_snapshot_member_id=obs.source_snapshot_member_id,
        member_identity=obs.member_identity,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.DECISION_STAGE_MISMATCH,)
    assert outcome.bindings == ()


# ==============================================================================
# Scenario 7 & 8: Actual decision outcome != PASS
# ==============================================================================


@pytest.mark.asyncio
async def test_rejects_when_source_decision_outcome_is_fail() -> None:
    request, reader, user_id, guard_ref, src_dec_ref, _ = _setup_baseline()
    obs = reader.source_decisions[src_dec_ref]
    reader.source_decisions[src_dec_ref] = AuthoritativeSourceDecisionObservation(
        artifact_ref=src_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_code=obs.source_code,
        source_version=obs.source_version,
        actual_decision_outcome=ObservedDecisionOutcome.FAIL,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.SOURCE_DECISION_NOT_PASS,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_member_decision_outcome_is_fail() -> None:
    request, reader, user_id, guard_ref, _, mem_dec_ref = _setup_baseline()
    obs = reader.member_decisions[mem_dec_ref]
    reader.member_decisions[mem_dec_ref] = AuthoritativeMemberDecisionObservation(
        artifact_ref=mem_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_snapshot_member_id=obs.source_snapshot_member_id,
        member_identity=obs.member_identity,
        actual_decision_outcome=ObservedDecisionOutcome.FAIL,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.MEMBER_DECISION_NOT_PASS,)
    assert outcome.bindings == ()


# ==============================================================================
# Scenario 9: Source snapshot / code / version mismatch
# ==============================================================================


@pytest.mark.asyncio
async def test_rejects_when_source_snapshot_id_mismatch() -> None:
    request, reader, user_id, guard_ref, src_dec_ref, _ = _setup_baseline()
    obs = reader.source_decisions[src_dec_ref]
    other_snapshot_id = uuid4()
    reader.source_decisions[src_dec_ref] = AuthoritativeSourceDecisionObservation(
        artifact_ref=src_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=other_snapshot_id,
        source_code=obs.source_code,
        source_version=obs.source_version,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.SOURCE_BINDING_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_source_code_mismatch() -> None:
    request, reader, user_id, guard_ref, src_dec_ref, _ = _setup_baseline()
    obs = reader.source_decisions[src_dec_ref]
    reader.source_decisions[src_dec_ref] = AuthoritativeSourceDecisionObservation(
        artifact_ref=src_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_code="MFDS_LABEL",
        source_version=obs.source_version,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.SOURCE_BINDING_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_source_version_mismatch() -> None:
    request, reader, user_id, guard_ref, src_dec_ref, _ = _setup_baseline()
    obs = reader.source_decisions[src_dec_ref]
    reader.source_decisions[src_dec_ref] = AuthoritativeSourceDecisionObservation(
        artifact_ref=src_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_code=obs.source_code,
        source_version="2026.01",
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.SOURCE_BINDING_MISMATCH,)
    assert outcome.bindings == ()


# ==============================================================================
# Scenario 10, 11, 12: Member binding & identity mismatch
# ==============================================================================


@pytest.mark.asyncio
async def test_rejects_when_member_snapshot_member_id_mismatch() -> None:
    request, reader, user_id, guard_ref, _, mem_dec_ref = _setup_baseline()
    obs = reader.member_decisions[mem_dec_ref]
    other_member_id = uuid4()
    reader.member_decisions[mem_dec_ref] = AuthoritativeMemberDecisionObservation(
        artifact_ref=mem_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_snapshot_member_id=other_member_id,
        member_identity=obs.member_identity,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.MEMBER_BINDING_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_endpoint_member_identity_mismatch() -> None:
    request, reader, user_id, guard_ref, _, mem_dec_ref = _setup_baseline()
    obs = reader.member_decisions[mem_dec_ref]
    mismatched_identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="kims_api",
        operation_code="different_operation",
    )
    reader.member_decisions[mem_dec_ref] = AuthoritativeMemberDecisionObservation(
        artifact_ref=mem_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=obs.source_snapshot_id,
        source_snapshot_member_id=obs.source_snapshot_member_id,
        member_identity=mismatched_identity,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.MEMBER_BINDING_MISMATCH,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_rejects_when_artifact_member_identity_mismatch() -> None:
    request, reader, user_id, guard_ref, src_dec_ref, mem_dec_ref = _setup_baseline()
    snapshot_id = request.selections[0].source_snapshot_id
    member_id = request.selections[0].source_snapshot_member_id

    selected_identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        artifact_code="kims_pkg_insert",
        artifact_version="v2.1",
    )

    observed_identity = SourceMemberIdentity(
        member_kind=SourceMemberKind.ARTIFACT_MEMBER,
        artifact_code="kims_pkg_insert",
        artifact_version="v2.0",
    )

    new_selection = SyncGuideEvidenceAuthoritySelection(
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        source_code="KIMS",
        source_version="2026.09",
        member_identity=selected_identity,
        request_source_decision_ref=src_dec_ref,
        request_member_decision_ref=mem_dec_ref,
    )
    req = SyncGuideEvidenceAuthorityRequest(
        user_id=user_id,
        request_guard_ref=guard_ref,
        request_operation_code="guide.medication.inquiry",
        selections=(new_selection,),
    )

    reader.member_decisions[mem_dec_ref] = AuthoritativeMemberDecisionObservation(
        artifact_ref=mem_dec_ref,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        member_identity=observed_identity,
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )

    outcome = await assemble_sync_guide_evidence_authority(req, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.MEMBER_BINDING_MISMATCH,)
    assert outcome.bindings == ()


# ==============================================================================
# Scenario 15: Atomic fail-closed across multiple selections
# ==============================================================================


@pytest.mark.asyncio
async def test_multiple_selections_one_failure_rejects_all_without_partial_bindings() -> None:
    request, reader, user_id, guard_ref, src_dec_ref1, mem_dec_ref1 = _setup_baseline()
    sel1 = request.selections[0]

    src_dec_ref2 = _art("src_dec_artifact_2", "v1", "4" * 64)
    mem_dec_ref2 = _art("mem_dec_artifact_2", "v1", "5" * 64)
    snapshot_id2 = uuid4()
    member_id2 = uuid4()

    member_identity2 = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="dur_api",
        operation_code="contraindication",
    )

    sel2 = SyncGuideEvidenceAuthoritySelection(
        source_snapshot_id=snapshot_id2,
        source_snapshot_member_id=member_id2,
        source_code="DUR",
        source_version="2026.08",
        member_identity=member_identity2,
        request_source_decision_ref=src_dec_ref2,
        request_member_decision_ref=mem_dec_ref2,
    )

    reader.source_decisions[src_dec_ref2] = AuthoritativeSourceDecisionObservation(
        artifact_ref=src_dec_ref2,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=snapshot_id2,
        source_code="DUR",
        source_version="2026.08",
        actual_decision_outcome=ObservedDecisionOutcome.PASS,
    )
    reader.member_decisions[mem_dec_ref2] = AuthoritativeMemberDecisionObservation(
        artifact_ref=mem_dec_ref2,
        request_guard_ref=guard_ref,
        user_id=user_id,
        request_operation_code="guide.medication.inquiry",
        decision_stage="REQUEST",
        source_snapshot_id=snapshot_id2,
        source_snapshot_member_id=member_id2,
        member_identity=member_identity2,
        actual_decision_outcome=ObservedDecisionOutcome.FAIL,  # Fails!
    )

    multi_request = SyncGuideEvidenceAuthorityRequest(
        user_id=user_id,
        request_guard_ref=guard_ref,
        request_operation_code="guide.medication.inquiry",
        selections=(sel1, sel2),
    )

    outcome = await assemble_sync_guide_evidence_authority(multi_request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.MEMBER_DECISION_NOT_PASS,)
    assert outcome.bindings == ()  # Strict: no partial bindings returned!


# ==============================================================================
# Scenario 16: Explicit reader dependency failure (GuideEvidenceAuthorityReaderError)
# ==============================================================================


@pytest.mark.asyncio
async def test_explicit_reader_error_on_guard_converts_to_typed_rejection() -> None:
    request, reader, _, _, _, _ = _setup_baseline()
    reader.error_trigger = "guard"

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.AUTHORITY_READER_ERROR,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_explicit_reader_error_on_source_decision_converts_to_typed_rejection() -> None:
    request, reader, _, _, _, _ = _setup_baseline()
    reader.error_trigger = "source"

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.AUTHORITY_READER_ERROR,)
    assert outcome.bindings == ()


@pytest.mark.asyncio
async def test_explicit_reader_error_on_member_decision_converts_to_typed_rejection() -> None:
    request, reader, _, _, _, _ = _setup_baseline()
    reader.error_trigger = "member"

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.AUTHORITY_READER_ERROR,)
    assert outcome.bindings == ()


# ==============================================================================
# Scenario 19: Unexpected exceptions (e.g. RuntimeError) are NOT swallowed
# ==============================================================================


@pytest.mark.asyncio
async def test_unexpected_runtime_error_in_reader_propagates_unhandled() -> None:
    request, reader, _, _, _, _ = _setup_baseline()
    reader.runtime_error_trigger = "guard"

    with pytest.raises(RuntimeError, match="Unexpected boom in read_request_guard"):
        await assemble_sync_guide_evidence_authority(request, reader)


@pytest.mark.asyncio
async def test_unexpected_runtime_error_in_source_decision_propagates_unhandled() -> None:
    request, reader, _, _, _, _ = _setup_baseline()
    reader.runtime_error_trigger = "source"

    with pytest.raises(RuntimeError, match="Unexpected boom in read_source_decision"):
        await assemble_sync_guide_evidence_authority(request, reader)


@pytest.mark.asyncio
async def test_unexpected_runtime_error_in_member_decision_propagates_unhandled() -> None:
    request, reader, _, _, _, _ = _setup_baseline()
    reader.runtime_error_trigger = "member"

    with pytest.raises(RuntimeError, match="Unexpected boom in read_member_decision"):
        await assemble_sync_guide_evidence_authority(request, reader)


# ==============================================================================
# Scenario 17: Caller cannot provide PASS outcomes in request shape
# ==============================================================================


def test_caller_cannot_provide_pass_in_request_or_selection_shapes() -> None:
    selection_fields = SyncGuideEvidenceAuthoritySelection.__annotations__.keys()
    request_fields = SyncGuideEvidenceAuthorityRequest.__annotations__.keys()

    assert "source_decision_outcome" not in selection_fields
    assert "member_decision_outcome" not in selection_fields
    assert "actual_decision_outcome" not in selection_fields
    assert "observed_source_decision_outcome" not in selection_fields
    assert "observed_member_decision_outcome" not in selection_fields

    assert "source_decision_outcome" not in request_fields
    assert "member_decision_outcome" not in request_fields
    assert "actual_decision_outcome" not in request_fields


# ==============================================================================
# Scenario 20: Phase-ordered fail-fast stops early without unnecessary reader calls
# ==============================================================================


@pytest.mark.asyncio
async def test_guard_failure_stops_before_reading_decisions() -> None:
    request, reader, _, guard_ref, _, _ = _setup_baseline()
    reader.guards.clear()  # Guard will not be found

    outcome = await assemble_sync_guide_evidence_authority(request, reader)

    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.REQUEST_GUARD_NOT_FOUND,)
    assert reader.call_counts["guard"] == 1
    assert reader.call_counts["source"] == 0  # Did not attempt reading source decisions
    assert reader.call_counts["member"] == 0  # Did not attempt reading member decisions


# ==============================================================================
# Scenario 21: Structural invalid request shape fail-fast
# ==============================================================================


@pytest.mark.asyncio
async def test_rejects_malformed_request_shape() -> None:
    _, reader, _, _, _, _ = _setup_baseline()

    # Empty selections
    bad_req = SyncGuideEvidenceAuthorityRequest(
        user_id=uuid4(),
        request_guard_ref=_art("g"),
        request_operation_code="op",
        selections=(),
    )
    outcome = await assemble_sync_guide_evidence_authority(bad_req, reader)
    assert outcome.decision == SyncGuideEvidenceAuthorityDecision.REJECTED
    assert outcome.reasons == (SyncGuideEvidenceAuthorityReason.REQUEST_INVALID,)
    assert reader.call_counts["guard"] == 0


def test_authoritative_request_guard_observation_requires_decision_stage() -> None:
    with pytest.raises(TypeError):
        AuthoritativeRequestGuardObservation(  # type: ignore[call-arg]
            artifact_ref=_art("g"),
            user_id=uuid4(),
            request_operation_code="op",
        )
