from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from ai_worker.tasks.rag.citation_authorization import (
    AuthorizationReason,
    AuthorizationVerificationDecision,
    verify_citation_authorization_receipt,
)
from ai_worker.tasks.rag.citation_authorization_authority import (
    CitationAuthorityIssueDecision,
    CitationAuthorityIssueReason,
    CitationMemberEligibilityObservation,
    CitationSourceEligibilityObservation,
    issue_citation_authorization,
)
from ai_worker.tasks.rag.guide_evidence_authority import (
    AuthoritativeMemberDecisionObservation,
    AuthoritativeSourceDecisionObservation,
)
from ai_worker.tasks.rag.request_guard_runtime_binding import (
    build_origin_request_guard_binding,
    build_runtime_authorization_binding,
)
from ai_worker.tests.rag.test_citation_authorization import _validated_selection
from ai_worker.tests.rag.test_claim_citation_validator import _artifact, _candidate_set, _support_receipt
from rag_runtime.citation_authorization_authority import CitationAuthorityDecision, CitationAuthorityReason
from rag_runtime.request_authority import (
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
)
from rag_runtime.request_guard_runtime_binding import RequestGuardRuntimeBindingObservation
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import (
    SourceUseApprovalIdentity,
    SourceUseApprovalObservation,
    SourceUsePurpose,
)

NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)
SNAPSHOT_ID = UUID("10000000-0000-4000-8000-000000000001")
MEMBER_ID = UUID("20000000-0000-4000-8000-000000000001")
APPROVAL_ID = UUID("30000000-0000-4000-8000-000000000001")


def _guard_observation() -> RequestGuardRuntimeBindingObservation:
    from rag_runtime.request_guard_runtime_binding import canonical_scope_manifest_hash

    scopes = ("GUIDE", "PATIENT_CITATION")
    return RequestGuardRuntimeBindingObservation(
        request_guard_decision_id=UUID("40000000-0000-4000-8000-000000000001"),
        actual_decision_outcome=RequestAuthorityDecisionOutcome.PASS,
        user_id=UUID("50000000-0000-4000-8000-000000000001"),
        request_operation_code="GUIDE_GENERATION",
        decision_stage=RequestAuthorityDecisionStage.REQUEST,
        environment=RuntimeEnvironmentCode.TEST,
        bundle_id=UUID("60000000-0000-4000-8000-000000000001"),
        bundle_manifest_hash="a" * 64,
        request_scope_codes=scopes,
        scope_manifest_hash=canonical_scope_manifest_hash(scopes),
        legacy_request_authority_ref=RequestAuthorityArtifactRef(
            artifact_code="request_guard_authority",
            version="1.0",
            content_sha256="b" * 64,
        ),
    )


class _GuardReader:
    def __init__(self, observation):
        self.observation = observation

    async def read_exact(self, reference):
        return self.observation


class _AuthorityReader:
    def __init__(self, selection=None):
        self.selection = selection or _validated_selection()

    async def read_source_decision(self, *, request_source_decision_ref):
        return AuthoritativeSourceDecisionObservation(
            artifact_ref=request_source_decision_ref,
            request_guard_ref=RequestAuthorityArtifactRef(
                artifact_code="request_guard_authority", version="1.0", content_sha256="b" * 64
            ),
            user_id=UUID("50000000-0000-4000-8000-000000000001"),
            request_operation_code="GUIDE_GENERATION",
            decision_stage="REQUEST",
            source_snapshot_id=SNAPSHOT_ID,
            source_code="knowledge-source",
            source_version="2026-09-01",
            actual_decision_outcome="PASS",
        )

    async def read_member_decision(self, *, request_member_decision_ref):
        selection = self.selection.candidate_set.citations[0].evidence_ref.execution_provenance
        return AuthoritativeMemberDecisionObservation(
            artifact_ref=request_member_decision_ref,
            request_guard_ref=RequestAuthorityArtifactRef(
                artifact_code="request_guard_authority", version="1.0", content_sha256="b" * 64
            ),
            user_id=UUID("50000000-0000-4000-8000-000000000001"),
            request_operation_code="GUIDE_GENERATION",
            decision_stage="REQUEST",
            source_snapshot_id=SNAPSHOT_ID,
            source_snapshot_member_id=MEMBER_ID,
            member_identity=selection_identity(selection),
            actual_decision_outcome="PASS",
        )


def selection_identity(selection):
    from ai_worker.tasks.rag.source_member_identity import SourceMemberIdentity

    return SourceMemberIdentity(
        member_kind=selection.member_kind,
        endpoint_code=selection.endpoint_code,
        operation_code=selection.operation_code,
        artifact_code=selection.artifact_code,
        artifact_version=selection.artifact_version,
    )


class _PinReader:
    def __init__(self, *, present: bool = True):
        self.present = present

    async def read_exact(self, *, bundle_id, bundle_manifest_hash):
        if not self.present:
            return ()
        return (
            SimpleNamespace(
                source_snapshot_id=str(SNAPSHOT_ID),
                source_use_approval_id=str(APPROVAL_ID),
                source_code="knowledge-source",
                source_version="2026-09-01",
                approval_version="approval-v1",
                environment="TEST",
                purpose=SourceUsePurpose.PATIENT_CITATION,
            ),
        )


class _ApprovalReader:
    def __init__(self, observation: SourceUseApprovalObservation | None = None):
        self.observation = observation

    async def read_exact(self, identity: SourceUseApprovalIdentity):
        return self.observation or SourceUseApprovalObservation(
            id=APPROVAL_ID,
            identity=identity,
            valid_from=NOW - timedelta(days=1),
            expires_at=NOW + timedelta(days=1),
            revoked_at=None,
            revoked_by=None,
            revoked_reason=None,
            actor_id=UUID("70000000-0000-4000-8000-000000000001"),
            evidence_ref="approval-evidence-v1",
        )


class _EligibilityReader:
    def __init__(
        self,
        *,
        source_status: str = "ACTIVE",
        snapshot_status: str = "CURRENT",
        member: CitationMemberEligibilityObservation | None = None,
    ):
        self.source_status = source_status
        self.snapshot_status = snapshot_status
        self.member = member

    async def read_source_exact(self, *, source_snapshot_id):
        return CitationSourceEligibilityObservation(
            source_snapshot_id=source_snapshot_id,
            source_code="knowledge-source",
            source_version="2026-09-01",
            source_lifecycle_status=self.source_status,
            snapshot_verification_status=self.snapshot_status,
        )

    async def read_member_exact(self, *, source_snapshot_id, source_snapshot_member_id):
        return self.member or CitationMemberEligibilityObservation(
            source_snapshot_id=source_snapshot_id,
            source_snapshot_member_id=source_snapshot_member_id,
            member_kind="ARTIFACT",
            endpoint_id=None,
            operation_id=None,
            ingestion_artifact_id=UUID("80000000-0000-4000-8000-000000000001"),
            endpoint_code=None,
            operation_code=None,
            endpoint_lifecycle_status=None,
            endpoint_runtime_status=None,
            endpoint_acquisition_status=None,
            operation_runtime_status=None,
            operation_acquisition_status=None,
            artifact_fk_exists=True,
        )


class _Store:
    def __init__(self):
        self.aggregate = None

    async def read_complete(self, *, request_sha256):
        return self.aggregate

    async def append(self, aggregate):
        self.aggregate = aggregate


def _endpoint_selection():
    from ai_worker.tasks.rag.claim_citation_validator import (
        KnowledgeChunkEvidenceRef,
        SourceExecutionProvenance,
        SourceMemberKind,
        validate_claim_citations,
    )

    provenance = SourceExecutionProvenance(
        source_code="knowledge-source",
        source_version="2026-09-01",
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="endpoint",
        operation_code="operation",
        artifact_code=None,
        artifact_version=None,
        request_source_decision_ref=_artifact("knowledge-source-decision", "b" * 64),
        request_member_decision_ref=_artifact("knowledge-source-member-decision", "c" * 64),
    )
    evidence = KnowledgeChunkEvidenceRef(
        knowledge_chunk_ref="knowledge-chunk-001",
        source_snapshot_ref=_artifact("knowledge-snapshot"),
        source_version="2026-09-01",
        locator="section-1",
        content_sha256="a" * 64,
        execution_provenance=provenance,
    )
    candidate_set = _candidate_set(evidence=evidence)
    outcome = validate_claim_citations(candidate_set, (_support_receipt(candidate_set),))
    assert outcome.validated_selection is not None
    return outcome.validated_selection


def _request(selection, guard):
    from ai_worker.tasks.rag.citation_authorization import build_citation_authorization_request

    built = build_citation_authorization_request(
        selection,
        build_runtime_authorization_binding(guard),
        build_origin_request_guard_binding(guard),
    )
    assert built.request is not None
    return built.request


async def _issue(
    *,
    selection=None,
    pin_reader=None,
    approval_reader=None,
    eligibility_reader=None,
    store=None,
):
    selection = selection or _validated_selection()
    guard = _guard_observation()
    request = _request(selection, guard)
    store = store or _Store()
    outcome = await issue_citation_authorization(
        validated_selection=selection,
        request=request,
        evaluation_time=NOW,
        guard_reader=_GuardReader(guard),
        request_authority_reader=_AuthorityReader(selection),
        pin_reader=pin_reader or _PinReader(),
        approval_reader=approval_reader or _ApprovalReader(),
        eligibility_reader=eligibility_reader or _EligibilityReader(),
        store=store,
    )
    return request, store, outcome


@pytest.mark.asyncio
async def test_exact_replay_mismatch_creates_no_authority() -> None:
    selection = _validated_selection()
    guard = _guard_observation()
    store = _Store()
    request = _request(selection, guard)
    request = replace(
        request,
        runtime_binding=replace(request.runtime_binding, bundle_manifest_hash="f" * 64),
    )

    outcome = await issue_citation_authorization(
        validated_selection=selection,
        request=request,
        evaluation_time=NOW,
        guard_reader=_GuardReader(guard),
        request_authority_reader=_AuthorityReader(),
        pin_reader=_PinReader(),
        approval_reader=_ApprovalReader(),
        eligibility_reader=_EligibilityReader(),
        store=store,
    )

    assert outcome.decision is CitationAuthorityIssueDecision.PREREQUISITE_FAILED
    assert outcome.reason is CitationAuthorityIssueReason.REQUEST_REPLAY_MISMATCH
    assert outcome.receipt is None
    assert store.aggregate is None


@pytest.mark.asyncio
async def test_all_exact_authority_passes_and_persists_verified_receipt() -> None:
    selection = _validated_selection()
    guard = _guard_observation()
    store = _Store()
    request = _request(selection, guard)

    outcome = await issue_citation_authorization(
        validated_selection=selection,
        request=request,
        evaluation_time=NOW,
        guard_reader=_GuardReader(guard),
        request_authority_reader=_AuthorityReader(),
        pin_reader=_PinReader(),
        approval_reader=_ApprovalReader(),
        eligibility_reader=_EligibilityReader(),
        store=store,
    )

    assert outcome.decision is CitationAuthorityIssueDecision.ISSUED
    assert outcome.receipt is not None
    assert (
        verify_citation_authorization_receipt(request, outcome.receipt).decision
        is AuthorizationVerificationDecision.AUTHORIZED
    )
    assert store.aggregate is not None


@pytest.mark.asyncio
async def test_missing_bundle_pin_creates_no_authority() -> None:
    _, store, outcome = await _issue(pin_reader=_PinReader(present=False))

    assert outcome.decision is CitationAuthorityIssueDecision.PREREQUISITE_FAILED
    assert outcome.reason is CitationAuthorityIssueReason.BUNDLE_PIN_ABSENT
    assert outcome.receipt is None
    assert store.aggregate is None


@pytest.mark.asyncio
async def test_missing_exact_approval_creates_no_authority() -> None:
    class MissingApprovalReader:
        async def read_exact(self, identity):
            return None

    _, store, outcome = await _issue(approval_reader=MissingApprovalReader())

    assert outcome.reason is CitationAuthorityIssueReason.SOURCE_APPROVAL_ABSENT
    assert outcome.receipt is None
    assert store.aggregate is None


@pytest.mark.parametrize(
    ("approval", "source_reason"),
    (
        (
            SourceUseApprovalObservation(
                id=APPROVAL_ID,
                identity=SourceUseApprovalIdentity(
                    source_snapshot_id=SNAPSHOT_ID,
                    source_code="knowledge-source",
                    source_version="2026-09-01",
                    environment=RuntimeEnvironmentCode.TEST,
                    purpose=SourceUsePurpose.PATIENT_CITATION,
                    approval_version="approval-v1",
                ),
                valid_from=NOW - timedelta(days=2),
                expires_at=NOW - timedelta(days=1),
                revoked_at=None,
                revoked_by=None,
                revoked_reason=None,
                actor_id=UUID("70000000-0000-4000-8000-000000000001"),
                evidence_ref="approval-evidence-v1",
            ),
            CitationAuthorityReason.APPROVAL_EXPIRED,
        ),
        (
            SourceUseApprovalObservation(
                id=APPROVAL_ID,
                identity=SourceUseApprovalIdentity(
                    source_snapshot_id=SNAPSHOT_ID,
                    source_code="knowledge-source",
                    source_version="2026-09-01",
                    environment=RuntimeEnvironmentCode.TEST,
                    purpose=SourceUsePurpose.PATIENT_CITATION,
                    approval_version="approval-v1",
                ),
                valid_from=NOW - timedelta(days=2),
                expires_at=NOW + timedelta(days=1),
                revoked_at=NOW - timedelta(hours=1),
                revoked_by=UUID("70000000-0000-4000-8000-000000000002"),
                revoked_reason="withdrawn",
                actor_id=UUID("70000000-0000-4000-8000-000000000001"),
                evidence_ref="approval-evidence-v1",
            ),
            CitationAuthorityReason.APPROVAL_REVOKED,
        ),
    ),
)
@pytest.mark.asyncio
async def test_unusable_approval_persists_complete_fail_receipt(approval, source_reason) -> None:
    request, store, outcome = await _issue(approval_reader=_ApprovalReader(approval))

    assert outcome.receipt is not None
    verification = verify_citation_authorization_receipt(request, outcome.receipt)
    assert verification.decision is AuthorizationVerificationDecision.REJECTED
    assert verification.reasons == (AuthorizationReason.SELECTION_NOT_AUTHORIZED,)
    assert store.aggregate is not None
    assert store.aggregate.source_decisions[0].reason_code is source_reason
    assert store.aggregate.member_decisions[0].reason_code is CitationAuthorityReason.SOURCE_DECISION_FAILED


@pytest.mark.parametrize(
    ("source_status", "snapshot_status", "reason"),
    (
        ("RETIRED", "CURRENT", CitationAuthorityReason.SOURCE_INACTIVE),
        ("ACTIVE", "STALE", CitationAuthorityReason.SNAPSHOT_NOT_CURRENT),
        ("ACTIVE", "FAILED", CitationAuthorityReason.SNAPSHOT_NOT_CURRENT),
    ),
)
@pytest.mark.asyncio
async def test_ineligible_source_persists_fail_receipt(source_status, snapshot_status, reason) -> None:
    _, store, outcome = await _issue(
        eligibility_reader=_EligibilityReader(source_status=source_status, snapshot_status=snapshot_status)
    )

    assert outcome.receipt is not None
    assert store.aggregate is not None
    assert store.aggregate.source_decisions[0].actual_decision_outcome is CitationAuthorityDecision.FAIL
    assert store.aggregate.source_decisions[0].reason_code is reason


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("endpoint_lifecycle_status", "UNVERIFIED", CitationAuthorityReason.ENDPOINT_NOT_VERIFIED),
        ("endpoint_runtime_status", "DISABLED", CitationAuthorityReason.ENDPOINT_DISABLED),
        ("endpoint_acquisition_status", "REJECTED", CitationAuthorityReason.ENDPOINT_NOT_APPROVED),
        ("operation_runtime_status", "DISABLED", CitationAuthorityReason.OPERATION_DISABLED),
        ("operation_acquisition_status", "REJECTED", CitationAuthorityReason.OPERATION_NOT_APPROVED),
    ),
)
@pytest.mark.asyncio
async def test_endpoint_member_status_failure_persists_fail_receipt(field, value, reason) -> None:
    endpoint_id = UUID("90000000-0000-4000-8000-000000000001")
    operation_id = UUID("90000000-0000-4000-8000-000000000002")
    member = CitationMemberEligibilityObservation(
        source_snapshot_id=SNAPSHOT_ID,
        source_snapshot_member_id=MEMBER_ID,
        member_kind="ENDPOINT_OPERATION",
        endpoint_id=endpoint_id,
        operation_id=operation_id,
        ingestion_artifact_id=None,
        endpoint_code="endpoint",
        operation_code="operation",
        endpoint_lifecycle_status="VERIFIED",
        endpoint_runtime_status="ENABLED",
        endpoint_acquisition_status="APPROVED",
        operation_runtime_status="ENABLED",
        operation_acquisition_status="APPROVED",
        artifact_fk_exists=False,
        snapshot_endpoint_id=endpoint_id,
        snapshot_operation_id=operation_id,
    )
    member = replace(member, **{field: value})
    request, store, outcome = await _issue(
        selection=_endpoint_selection(), eligibility_reader=_EligibilityReader(member=member)
    )

    assert outcome.receipt is not None
    assert verify_citation_authorization_receipt(request, outcome.receipt).reasons == (
        AuthorizationReason.SELECTION_NOT_AUTHORIZED,
    )
    assert store.aggregate is not None
    assert store.aggregate.member_decisions[0].reason_code is reason


@pytest.mark.asyncio
async def test_existing_receipt_replays_without_current_eligibility_read() -> None:
    store = _Store()
    request, _, first = await _issue(store=store)

    class ExplodingEligibilityReader:
        async def read_source_exact(self, *, source_snapshot_id):
            raise AssertionError("historical replay must not read current source")

        async def read_member_exact(self, *, source_snapshot_id, source_snapshot_member_id):
            raise AssertionError("historical replay must not read current member")

    _, _, replay = await _issue(store=store, eligibility_reader=ExplodingEligibilityReader())

    assert first.receipt is not None
    assert replay.decision is CitationAuthorityIssueDecision.REPLAYED
    assert replay.receipt == first.receipt
    assert (
        verify_citation_authorization_receipt(request, replay.receipt).decision
        is AuthorizationVerificationDecision.AUTHORIZED
    )
