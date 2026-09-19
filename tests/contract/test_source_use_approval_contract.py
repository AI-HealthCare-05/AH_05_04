from datetime import UTC, datetime
from uuid import uuid4

import pytest

from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import (
    SourceUseApprovalIdentity,
    SourceUseApprovalObservation,
    SourceUseApprovalValidationError,
    SourceUsePurpose,
)


def _identity(*, purpose: SourceUsePurpose = SourceUsePurpose.PATIENT_CITATION) -> SourceUseApprovalIdentity:
    return SourceUseApprovalIdentity(
        source_snapshot_id=uuid4(),
        source_code="MFDS_PRODUCT_LABEL",
        source_version="2026-09-18",
        environment=RuntimeEnvironmentCode.PRODUCTION,
        purpose=purpose,
        approval_version="approval-1",
    )


def _observation(
    *,
    purpose: SourceUsePurpose = SourceUsePurpose.PATIENT_CITATION,
    valid_from: datetime = datetime(2026, 9, 19, 0, 0, tzinfo=UTC),
    expires_at: datetime = datetime(2026, 9, 20, 0, 0, tzinfo=UTC),
    revoked_at: datetime | None = None,
    revoked_by=None,
    revoked_reason=None,
) -> SourceUseApprovalObservation:
    return SourceUseApprovalObservation(
        id=uuid4(),
        identity=_identity(purpose=purpose),
        valid_from=valid_from,
        expires_at=expires_at,
        revoked_at=revoked_at,
        revoked_by=revoked_by,
        revoked_reason=revoked_reason,
        actor_id=uuid4(),
        evidence_ref="evidence://approval-1",
    )


def test_source_use_purpose_vocabulary_is_distinct_from_runtime_bundle_purpose() -> None:
    assert tuple(purpose.value for purpose in SourceUsePurpose) == (
        "PRODUCT_IDENTIFICATION",
        "SAFETY_ROUTING",
        "RULE_DERIVATION",
        "RETRIEVAL",
        "PATIENT_CITATION",
    )


def test_patient_citation_approval_is_usable_only_inside_explicit_validity_window() -> None:
    observation = _observation()

    assert not observation.is_usable_at(datetime(2026, 9, 18, 23, 59, tzinfo=UTC))
    assert observation.is_usable_at(datetime(2026, 9, 19, 0, 0, tzinfo=UTC))
    assert observation.is_usable_at(datetime(2026, 9, 19, 23, 59, tzinfo=UTC))
    assert not observation.is_usable_at(datetime(2026, 9, 20, 0, 0, tzinfo=UTC))


def test_revoked_approval_is_not_usable_but_remains_historical() -> None:
    revoked_at = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    observation = _observation(revoked_at=revoked_at, revoked_by=uuid4(), revoked_reason="policy change")

    assert observation.is_usable_at(datetime(2026, 9, 19, 13, 0, tzinfo=UTC)) is False
    assert observation.revoked_at == revoked_at


@pytest.mark.parametrize(
    ("revoked_at", "revoked_by", "revoked_reason"),
    [
        (datetime(2026, 9, 19, 12, 0, tzinfo=UTC), None, "policy change"),
        (datetime(2026, 9, 19, 12, 0, tzinfo=UTC), uuid4(), None),
        (None, uuid4(), "policy change"),
    ],
)
def test_revocation_metadata_must_be_all_present_or_all_absent(
    revoked_at: datetime | None,
    revoked_by,
    revoked_reason: str | None,
) -> None:
    with pytest.raises(SourceUseApprovalValidationError):
        _observation(revoked_at=revoked_at, revoked_by=revoked_by, revoked_reason=revoked_reason)


def test_revocation_reason_is_not_normalized() -> None:
    with pytest.raises(SourceUseApprovalValidationError):
        _observation(
            revoked_at=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
            revoked_by=uuid4(),
            revoked_reason=" policy change",
        )


def test_retrieval_approval_is_not_a_patient_citation_approval() -> None:
    observation = _observation(purpose=SourceUsePurpose.RETRIEVAL)

    assert observation.identity.purpose is SourceUsePurpose.RETRIEVAL
    assert observation.identity.purpose is not SourceUsePurpose.PATIENT_CITATION


@pytest.mark.parametrize(
    "environment",
    ["production", " PRODUCTION", "PRODUCTION ", "UNKNOWN"],
)
def test_environment_is_case_sensitive_and_never_normalized(environment: str) -> None:
    with pytest.raises(SourceUseApprovalValidationError):
        SourceUseApprovalIdentity(
            source_snapshot_id=uuid4(),
            source_code="MFDS_PRODUCT_LABEL",
            source_version="2026-09-18",
            environment=environment,
            purpose=SourceUsePurpose.PATIENT_CITATION,
            approval_version="approval-1",
        )


def test_semantic_payload_cannot_have_an_open_ended_validity_window() -> None:
    with pytest.raises(SourceUseApprovalValidationError):
        _observation(expires_at=datetime(2026, 9, 19, 0, 0, tzinfo=UTC))


def test_member_specific_fields_are_not_part_of_source_use_approval_identity() -> None:
    identity_fields = set(SourceUseApprovalIdentity.__dataclass_fields__)

    assert identity_fields == {
        "source_snapshot_id",
        "source_code",
        "source_version",
        "environment",
        "purpose",
        "approval_version",
    }
