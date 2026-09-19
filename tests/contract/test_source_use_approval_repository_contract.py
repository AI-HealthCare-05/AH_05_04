from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.repositories.rag_source_use_approval_repository import (
    RagSourceUseApprovalRepository,
    SourceUseApprovalConflictError,
    SourceUseApprovalCreate,
    SourceUseApprovalValidationError,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import SourceUsePurpose


def _create_request(**overrides: object) -> SourceUseApprovalCreate:
    values: dict[str, object] = {
        "source_snapshot_id": uuid4(),
        "source_code": "MFDS_PRODUCT_LABEL",
        "source_version": "2026-09-18",
        "environment": RuntimeEnvironmentCode.PRODUCTION,
        "purpose": SourceUsePurpose.PATIENT_CITATION,
        "approval_version": "approval-1",
        "valid_from": datetime(2026, 9, 19, tzinfo=UTC),
        "expires_at": datetime(2026, 9, 20, tzinfo=UTC),
        "actor_id": uuid4(),
        "evidence_ref": "evidence://approval-1",
    }
    values.update(overrides)
    return SourceUseApprovalCreate(**values)


def test_create_request_rejects_noncanonical_environment_without_normalization() -> None:
    with pytest.raises(SourceUseApprovalValidationError):
        _create_request(environment="production")


def test_create_request_rejects_expired_or_open_ended_validity() -> None:
    with pytest.raises(SourceUseApprovalValidationError):
        _create_request(expires_at=datetime(2026, 9, 19, tzinfo=UTC))


def test_repository_exposes_only_exact_read_and_one_way_revoke_operations() -> None:
    public_methods = {name for name in dir(RagSourceUseApprovalRepository) if not name.startswith("_")}

    assert {"create_approval", "get_exact", "revoke"} <= public_methods
    assert "get_latest" not in public_methods
    assert "get_current" not in public_methods
    assert "update_approval" not in public_methods
    assert "delete_approval" not in public_methods
    assert SourceUseApprovalConflictError is not None


def test_retrieval_approval_can_be_represented_but_is_not_patient_citation() -> None:
    request = _create_request(purpose=SourceUsePurpose.RETRIEVAL)

    assert request.purpose is SourceUsePurpose.RETRIEVAL
    assert request.purpose is not SourceUsePurpose.PATIENT_CITATION


def test_create_request_keeps_explicit_evaluation_window() -> None:
    request = _create_request(expires_at=datetime(2026, 9, 20, 12, tzinfo=UTC))

    assert request.valid_from == datetime(2026, 9, 19, tzinfo=UTC)
    assert request.expires_at - request.valid_from == timedelta(days=1, hours=12)
