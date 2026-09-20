from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from ai_worker.adapters import sqlalchemy_citation_authorization_authority as authority_store
from ai_worker.tasks.rag.citation_authorization_authority import (
    CitationAuthorityIssueReason,
    CitationAuthorizationAuthorityError,
)


def _bound_rows() -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    request_sha256 = "1" * 64
    source_sha256 = "2" * 64
    member_sha256 = "3" * 64
    evaluated_at = datetime(2026, 9, 20, tzinfo=UTC)
    receipt: dict[str, object] = {
        "id": "receipt-id",
        "request_sha256": request_sha256,
        "origin_guard_artifact_code": "guard",
        "origin_guard_artifact_version": "1.0",
        "origin_guard_content_sha256": "4" * 64,
        "environment": "TEST",
        "bundle_id": "bundle-id",
        "bundle_manifest_hash": "5" * 64,
        "scope_manifest_hash": "6" * 64,
    }
    source: dict[str, object] = {
        "id": "source-id",
        "artifact_content_sha256": source_sha256,
        "request_sha256": request_sha256,
        "origin_guard_artifact_code": "guard",
        "origin_guard_artifact_version": "1.0",
        "origin_guard_content_sha256": "4" * 64,
        "environment": "TEST",
        "bundle_id": "bundle-id",
        "bundle_manifest_hash": "5" * 64,
        "scope_manifest_hash": "6" * 64,
        "source_snapshot_id": "snapshot-id",
        "source_code": "source",
        "source_version": "v1",
        "purpose": "PATIENT_CITATION",
        "evaluation_time": evaluated_at,
        "actual_decision_outcome": "PASS",
    }
    member: dict[str, object] = {
        "id": "member-id",
        "source_decision_id": "source-id",
        "artifact_content_sha256": member_sha256,
        "request_sha256": request_sha256,
        "source_decision_content_sha256": source_sha256,
        "source_snapshot_id": "snapshot-id",
        "source_code": "source",
        "source_version": "v1",
        "member_kind": "ENDPOINT_OPERATION",
        "endpoint_code": "endpoint",
        "operation_code": "operation",
        "artifact_member_code": None,
        "artifact_member_version": None,
        "evaluation_time": evaluated_at,
        "actual_decision_outcome": "PASS",
    }
    selection: dict[str, object] = {
        "receipt_id": "receipt-id",
        "request_sha256": request_sha256,
        "source_decision_id": "source-id",
        "source_decision_content_sha256": source_sha256,
        "member_decision_id": "member-id",
        "member_decision_content_sha256": member_sha256,
        "source_code": "source",
        "source_version": "v1",
        "member_kind": "ENDPOINT_OPERATION",
        "endpoint_code": "endpoint",
        "operation_code": "operation",
        "artifact_member_code": None,
        "artifact_member_version": None,
        "purpose": "PATIENT_CITATION",
        "source_decision": "PASS",
        "member_decision": "PASS",
    }
    return receipt, selection, source, member


@pytest.mark.parametrize(
    ("target", "field", "value"),
    (
        ("source", "request_sha256", "f" * 64),
        ("member", "source_decision_id", "other-source-id"),
        ("selection", "member_decision", "FAIL"),
        ("selection", "source_version", "tampered-version"),
    ),
)
def test_persisted_binding_rejects_cross_row_semantic_corruption(target: str, field: str, value: object) -> None:
    validator = getattr(authority_store, "_validate_persisted_binding", None)
    assert validator is not None, "exact-read must validate cross-row semantic binding"
    receipt, selection, source, member = deepcopy(_bound_rows())
    rows = {"receipt": receipt, "selection": selection, "source": source, "member": member}
    rows[target][field] = value

    with pytest.raises(CitationAuthorizationAuthorityError) as error:
        validator(receipt, selection, source, member)

    assert error.value.args == (CitationAuthorityIssueReason.EXISTING_RECEIPT_CORRUPT,)
