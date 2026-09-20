from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

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

SHA_A = "a" * 64
SHA_B = "b" * 64
SOURCE_SNAPSHOT_ID = UUID("10000000-0000-4000-8000-000000000001")
SOURCE_MEMBER_ID = UUID("20000000-0000-4000-8000-000000000001")
APPROVAL_ID = UUID("30000000-0000-4000-8000-000000000001")
GUARD_DECISION_ID = UUID("40000000-0000-4000-8000-000000000001")
USER_ID = UUID("50000000-0000-4000-8000-000000000001")
EVALUATED_AT = datetime(2026, 9, 20, 1, 2, 3, tzinfo=UTC)


def _source_projection(
    outcome: CitationAuthorityDecision = CitationAuthorityDecision.PASS,
) -> CitationSourceDecisionProjection:
    return CitationSourceDecisionProjection(
        request_sha256=SHA_A,
        request_guard_decision_id=GUARD_DECISION_ID,
        origin_guard_artifact_code="request_guard_runtime_binding",
        origin_guard_artifact_version="1.0",
        origin_guard_content_sha256=SHA_B,
        user_id=USER_ID,
        request_operation_code="GUIDE_GENERATION",
        environment="TEST",
        bundle_id=UUID("60000000-0000-4000-8000-000000000001"),
        bundle_manifest_hash="c" * 64,
        scope_manifest_hash="d" * 64,
        source_snapshot_id=SOURCE_SNAPSHOT_ID,
        source_use_approval_id=APPROVAL_ID,
        source_code="MFDS",
        source_version="2026-09",
        approval_version="approval-v1",
        purpose="PATIENT_CITATION",
        evaluation_time=EVALUATED_AT,
        approval_valid_from=datetime(2026, 9, 1, tzinfo=UTC),
        approval_expires_at=datetime(2026, 10, 1, tzinfo=UTC),
        approval_revoked_at=None,
        source_lifecycle_status="ACTIVE",
        snapshot_verification_status="CURRENT",
        actual_decision_outcome=outcome,
        reason_code=(
            CitationAuthorityReason.ELIGIBLE
            if outcome is CitationAuthorityDecision.PASS
            else CitationAuthorityReason.SOURCE_INACTIVE
        ),
    )


def _member_projection(source_ref_hash: str) -> CitationMemberDecisionProjection:
    return CitationMemberDecisionProjection(
        request_sha256=SHA_A,
        source_decision_content_sha256=source_ref_hash,
        source_snapshot_id=SOURCE_SNAPSHOT_ID,
        source_snapshot_member_id=SOURCE_MEMBER_ID,
        source_code="MFDS",
        source_version="2026-09",
        member_kind="ENDPOINT_OPERATION",
        endpoint_code="drug-info",
        operation_code=None,
        artifact_code=None,
        artifact_version=None,
        evaluation_time=EVALUATED_AT,
        endpoint_lifecycle_status="VERIFIED",
        endpoint_runtime_status="ENABLED",
        endpoint_acquisition_status="APPROVED",
        operation_runtime_status="ENABLED",
        operation_acquisition_status="APPROVED",
        current_member_kind="ENDPOINT_OPERATION",
        snapshot_endpoint_id=UUID("70000000-0000-4000-8000-000000000001"),
        snapshot_operation_id=UUID("80000000-0000-4000-8000-000000000001"),
        current_endpoint_id=UUID("70000000-0000-4000-8000-000000000001"),
        current_operation_id=UUID("80000000-0000-4000-8000-000000000001"),
        current_ingestion_artifact_id=None,
        artifact_fk_exists=None,
        actual_decision_outcome=CitationAuthorityDecision.PASS,
        reason_code=CitationAuthorityReason.ELIGIBLE,
    )


def test_decision_refs_are_deterministic_and_outcome_sensitive() -> None:
    passing = _source_projection()

    assert compute_citation_source_decision_ref(passing) == compute_citation_source_decision_ref(passing)
    assert compute_citation_source_decision_ref(passing) != compute_citation_source_decision_ref(
        _source_projection(CitationAuthorityDecision.FAIL)
    )

    source_ref = compute_citation_source_decision_ref(passing)
    member = _member_projection(source_ref.content_sha256)
    assert compute_citation_member_decision_ref(member) == compute_citation_member_decision_ref(member)


def test_receipt_ref_preserves_selection_order() -> None:
    source_ref = compute_citation_source_decision_ref(_source_projection())
    member_ref = compute_citation_member_decision_ref(_member_projection(source_ref.content_sha256))
    first = CitationReceiptSelectionProjection(
        selection_order=0,
        source_code="A",
        source_version="1",
        member_kind="ENDPOINT_OPERATION",
        endpoint_code="endpoint-a",
        operation_code=None,
        artifact_code=None,
        artifact_version=None,
        source_decision_content_sha256=source_ref.content_sha256,
        member_decision_content_sha256=member_ref.content_sha256,
        selected_for_operation=True,
        purpose="PATIENT_CITATION",
        source_decision="PASS",
        member_decision="PASS",
    )
    second = replace(first, selection_order=1, source_code="B")
    forward = CitationReceiptProjection(
        request_sha256=SHA_A,
        origin_guard_artifact_code="request_guard_runtime_binding",
        origin_guard_artifact_version="1.0",
        origin_guard_content_sha256=SHA_B,
        origin_decision="PASS",
        operation="CITATION_AUTHORIZATION",
        environment="TEST",
        bundle_id="60000000-0000-4000-8000-000000000001",
        bundle_manifest_hash="c" * 64,
        request_scope_codes=("GUIDE", "PATIENT_CITATION"),
        scope_manifest_hash="d" * 64,
        validated_selection_sha256="e" * 64,
        selection_manifest_sha256="f" * 64,
        selections=(first, second),
    )
    reverse = replace(
        forward,
        selections=(replace(second, selection_order=0), replace(first, selection_order=1)),
    )

    assert compute_citation_receipt_ref(forward) != compute_citation_receipt_ref(reverse)


def test_malformed_projection_is_rejected() -> None:
    with pytest.raises(ValueError, match="request_sha256"):
        compute_citation_source_decision_ref(replace(_source_projection(), request_sha256="bad"))
