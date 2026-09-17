"""#713 REQUEST authority artifact identity kernel tests."""

from __future__ import annotations

from uuid import UUID

import pytest

from ai_worker.tasks.rag.guide_evidence_handoff import (
    ObservedDecisionOutcome,
    RequestDecisionStage,
)
from ai_worker.tasks.rag.request_authority_artifact import (
    REQUEST_AUTHORITY_ARTIFACT_VERSION,
    REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE,
    REQUEST_MEMBER_DECISION_AUTHORITY_ARTIFACT_CODE,
    REQUEST_SOURCE_DECISION_AUTHORITY_ARTIFACT_CODE,
    RequestAuthorityArtifactError,
    RequestAuthorityArtifactReason,
    compute_request_guard_authority_ref,
    compute_request_member_decision_authority_ref,
    compute_request_source_decision_authority_ref,
)
from ai_worker.tasks.rag.source_member_identity import SourceMemberIdentity, SourceMemberKind

USER_A = UUID("11111111-1111-4111-8111-111111111111")
USER_B = UUID("22222222-2222-4222-8222-222222222222")
SNAPSHOT_A = UUID("33333333-3333-4333-8333-333333333333")
SNAPSHOT_B = UUID("44444444-4444-4444-8444-444444444444")
MEMBER_A = UUID("55555555-5555-4555-8555-555555555555")
MEMBER_B = UUID("66666666-6666-4666-8666-666666666666")

OPERATION = "GUIDE_SYNC_ANSWER"
ENDPOINT_IDENTITY = SourceMemberIdentity(
    member_kind=SourceMemberKind.ENDPOINT_OPERATION,
    endpoint_code="MFDS_DUR",
)
ARTIFACT_IDENTITY = SourceMemberIdentity(
    member_kind=SourceMemberKind.ARTIFACT_MEMBER,
    artifact_code="mfds_label_bundle",
    artifact_version="2026.09",
)


def _guard_ref(user_id: UUID = USER_A, operation: str = OPERATION):
    return compute_request_guard_authority_ref(
        user_id=user_id,
        request_operation_code=operation,
        decision_stage=RequestDecisionStage.REQUEST,
    )


def _source_ref(**overrides):
    kwargs = {
        "request_guard_ref": _guard_ref(),
        "user_id": USER_A,
        "request_operation_code": OPERATION,
        "decision_stage": RequestDecisionStage.REQUEST,
        "source_snapshot_id": SNAPSHOT_A,
        "source_code": "MFDS",
        "source_version": "2026.09.01",
        "actual_decision_outcome": ObservedDecisionOutcome.PASS,
    }
    kwargs.update(overrides)
    return compute_request_source_decision_authority_ref(**kwargs)


def _member_ref(**overrides):
    kwargs = {
        "request_guard_ref": _guard_ref(),
        "user_id": USER_A,
        "request_operation_code": OPERATION,
        "decision_stage": RequestDecisionStage.REQUEST,
        "source_snapshot_id": SNAPSHOT_A,
        "source_snapshot_member_id": MEMBER_A,
        "member_identity": ENDPOINT_IDENTITY,
        "actual_decision_outcome": ObservedDecisionOutcome.PASS,
    }
    kwargs.update(overrides)
    return compute_request_member_decision_authority_ref(**kwargs)


# ---------------------------------------------------------------------------
# Guard artifact identity
# ---------------------------------------------------------------------------


def test_guard_ref_uses_contract_artifact_code_and_version() -> None:
    ref = _guard_ref()
    assert ref.artifact_code == REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE
    assert ref.version == REQUEST_AUTHORITY_ARTIFACT_VERSION
    assert len(ref.content_sha256) == 64


def test_guard_ref_is_deterministic_for_identical_input() -> None:
    assert _guard_ref() == _guard_ref()


def test_guard_ref_changes_when_user_changes() -> None:
    assert _guard_ref(user_id=USER_A) != _guard_ref(user_id=USER_B)


def test_guard_ref_changes_when_operation_changes() -> None:
    assert _guard_ref(operation=OPERATION) != _guard_ref(operation="GUIDE_SYNC_OTHER")


def test_guard_ref_rejects_non_request_stage() -> None:
    with pytest.raises(RequestAuthorityArtifactError) as exc:
        compute_request_guard_authority_ref(
            user_id=USER_A,
            request_operation_code=OPERATION,
            decision_stage="APPROVAL",  # type: ignore[arg-type]
        )
    assert exc.value.reason is RequestAuthorityArtifactReason.DECISION_STAGE_INVALID


@pytest.mark.parametrize("operation", ["", " ", " GUIDE_SYNC_ANSWER", "GUIDE_SYNC_ANSWER "])
def test_guard_ref_rejects_blank_or_noncanonical_operation(operation: str) -> None:
    with pytest.raises(RequestAuthorityArtifactError) as exc:
        compute_request_guard_authority_ref(
            user_id=USER_A,
            request_operation_code=operation,
            decision_stage=RequestDecisionStage.REQUEST,
        )
    assert exc.value.reason is RequestAuthorityArtifactReason.REQUEST_OPERATION_CODE_INVALID


def test_guard_ref_rejects_non_uuid_user() -> None:
    with pytest.raises(RequestAuthorityArtifactError) as exc:
        compute_request_guard_authority_ref(
            user_id="not-a-uuid",  # type: ignore[arg-type]
            request_operation_code=OPERATION,
            decision_stage=RequestDecisionStage.REQUEST,
        )
    assert exc.value.reason is RequestAuthorityArtifactReason.USER_ID_INVALID


# ---------------------------------------------------------------------------
# Source Decision artifact identity
# ---------------------------------------------------------------------------


def test_source_ref_uses_contract_artifact_code_and_version() -> None:
    ref = _source_ref()
    assert ref.artifact_code == REQUEST_SOURCE_DECISION_AUTHORITY_ARTIFACT_CODE
    assert ref.version == REQUEST_AUTHORITY_ARTIFACT_VERSION


def test_source_ref_is_deterministic_for_identical_input() -> None:
    assert _source_ref() == _source_ref()


def test_source_ref_changes_when_guard_changes() -> None:
    assert _source_ref() != _source_ref(request_guard_ref=_guard_ref(user_id=USER_B))


def test_source_ref_changes_when_snapshot_changes() -> None:
    assert _source_ref() != _source_ref(source_snapshot_id=SNAPSHOT_B)


def test_source_ref_changes_when_source_code_changes() -> None:
    assert _source_ref() != _source_ref(source_code="KIMS")


def test_source_ref_changes_when_source_version_changes() -> None:
    assert _source_ref() != _source_ref(source_version="2026.09.02")


def test_source_ref_changes_when_outcome_changes() -> None:
    assert _source_ref() != _source_ref(actual_decision_outcome=ObservedDecisionOutcome.FAIL)


def test_source_ref_rejects_unknown_outcome() -> None:
    with pytest.raises(RequestAuthorityArtifactError) as exc:
        _source_ref(actual_decision_outcome="UNKNOWN")
    assert exc.value.reason is RequestAuthorityArtifactReason.DECISION_OUTCOME_INVALID


def test_source_ref_rejects_null_outcome() -> None:
    with pytest.raises(RequestAuthorityArtifactError) as exc:
        _source_ref(actual_decision_outcome=None)
    assert exc.value.reason is RequestAuthorityArtifactReason.DECISION_OUTCOME_INVALID


def test_source_ref_rejects_invalid_guard_ref() -> None:
    with pytest.raises(RequestAuthorityArtifactError) as exc:
        _source_ref(request_guard_ref="guard-ref")
    assert exc.value.reason is RequestAuthorityArtifactReason.REQUEST_GUARD_REF_INVALID


def test_source_ref_rejects_blank_source_code() -> None:
    with pytest.raises(RequestAuthorityArtifactError) as exc:
        _source_ref(source_code=" ")
    assert exc.value.reason is RequestAuthorityArtifactReason.SOURCE_BINDING_INVALID


# ---------------------------------------------------------------------------
# Member Decision artifact identity
# ---------------------------------------------------------------------------


def test_member_ref_uses_contract_artifact_code_and_version() -> None:
    ref = _member_ref()
    assert ref.artifact_code == REQUEST_MEMBER_DECISION_AUTHORITY_ARTIFACT_CODE
    assert ref.version == REQUEST_AUTHORITY_ARTIFACT_VERSION


def test_member_ref_is_deterministic_for_identical_input() -> None:
    assert _member_ref() == _member_ref()


def test_member_ref_changes_when_member_changes() -> None:
    assert _member_ref() != _member_ref(source_snapshot_member_id=MEMBER_B)


def test_member_ref_changes_when_member_identity_changes() -> None:
    assert _member_ref() != _member_ref(member_identity=ARTIFACT_IDENTITY)


def test_member_ref_changes_when_nullable_operation_code_is_set() -> None:
    with_operation = SourceMemberIdentity(
        member_kind=SourceMemberKind.ENDPOINT_OPERATION,
        endpoint_code="MFDS_DUR",
        operation_code="LIST",
    )
    assert _member_ref() != _member_ref(member_identity=with_operation)


def test_member_ref_changes_when_outcome_changes() -> None:
    assert _member_ref() != _member_ref(actual_decision_outcome=ObservedDecisionOutcome.FAIL)


def test_member_ref_supports_artifact_member_identity() -> None:
    ref = _member_ref(member_identity=ARTIFACT_IDENTITY)
    assert ref.artifact_code == REQUEST_MEMBER_DECISION_AUTHORITY_ARTIFACT_CODE


def test_member_ref_rejects_invalid_member_identity() -> None:
    invalid = SourceMemberIdentity(member_kind=SourceMemberKind.ENDPOINT_OPERATION)
    with pytest.raises(RequestAuthorityArtifactError) as exc:
        _member_ref(member_identity=invalid)
    assert exc.value.reason is RequestAuthorityArtifactReason.MEMBER_IDENTITY_INVALID


def test_member_ref_rejects_unknown_outcome() -> None:
    with pytest.raises(RequestAuthorityArtifactError) as exc:
        _member_ref(actual_decision_outcome="MAYBE")
    assert exc.value.reason is RequestAuthorityArtifactReason.DECISION_OUTCOME_INVALID


# ---------------------------------------------------------------------------
# Cross-kind separation
# ---------------------------------------------------------------------------


def test_distinct_authority_kinds_do_not_share_artifact_code() -> None:
    codes = {
        REQUEST_GUARD_AUTHORITY_ARTIFACT_CODE,
        REQUEST_SOURCE_DECISION_AUTHORITY_ARTIFACT_CODE,
        REQUEST_MEMBER_DECISION_AUTHORITY_ARTIFACT_CODE,
    }
    assert len(codes) == 3


def test_source_and_member_digests_differ_for_same_request_context() -> None:
    assert _source_ref().content_sha256 != _member_ref().content_sha256
