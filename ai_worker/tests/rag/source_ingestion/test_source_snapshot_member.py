from dataclasses import replace
from uuid import UUID

import pytest

from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotProvenanceReceipt,
    SnapshotVerificationStatus,
    SourceSnapshotMemberCreate,
    SourceSnapshotMemberFailureReason,
    SourceSnapshotMemberKind,
    SourceSnapshotMemberReceipt,
    SourceSnapshotMemberValidationError,
    append_snapshot_member,
)

_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000001")
_ENDPOINT_ID = UUID("00000000-0000-4000-8000-000000000002")
_OPERATION_ID = UUID("00000000-0000-4000-8000-000000000003")
_SNAPSHOT_ID = UUID("00000000-0000-4000-8000-000000000004")
_SEAL_ID = UUID("00000000-0000-4000-8000-000000000005")
_ARTIFACT_ID = UUID("00000000-0000-4000-8000-000000000006")
_MEMBER_ID = UUID("00000000-0000-4000-8000-000000000007")


def provenance() -> SnapshotProvenanceReceipt:
    return SnapshotProvenanceReceipt(
        source_id=_SOURCE_ID,
        source_code="MFDS_PRODUCT_APPROVAL",
        endpoint_id=_ENDPOINT_ID,
        operation_id=_OPERATION_ID,
        source_snapshot_id=_SNAPSHOT_ID,
        source_version="external:v1",
        external_version="v1",
        canonical_checksum="a" * 64,
        canonicalization_spec_version="mfds-product-approval@1",
        endpoint_receipt_hash="b" * 64,
        verification_seal_id=_SEAL_ID,
        verification_status=SnapshotVerificationStatus.CURRENT,
        rejected_record_count=0,
        publication_verification_id=None,
    )


def endpoint_request() -> SourceSnapshotMemberCreate:
    return SourceSnapshotMemberCreate(
        provenance=provenance(),
        member_kind=SourceSnapshotMemberKind.ENDPOINT_OPERATION,
        endpoint_id=_ENDPOINT_ID,
        operation_id=_OPERATION_ID,
        ingestion_artifact_id=None,
        locator="$.records[0]",
        content_sha256="c" * 64,
    )


def test_endpoint_operation_and_artifact_shapes_are_distinct() -> None:
    endpoint = endpoint_request()
    artifact = SourceSnapshotMemberCreate(
        provenance=provenance(),
        member_kind=SourceSnapshotMemberKind.ARTIFACT,
        endpoint_id=None,
        operation_id=None,
        ingestion_artifact_id=_ARTIFACT_ID,
        locator="artifact://page/1/record/0",
        content_sha256="d" * 64,
    )

    assert endpoint.operation_id == _OPERATION_ID
    assert artifact.ingestion_artifact_id == _ARTIFACT_ID


def test_endpoint_member_allows_nullable_operation_but_not_another_operation() -> None:
    assert replace(endpoint_request(), operation_id=None).operation_id is None

    with pytest.raises(SourceSnapshotMemberValidationError) as exc_info:
        replace(
            endpoint_request(),
            operation_id=UUID("00000000-0000-4000-8000-000000000099"),
        )

    assert exc_info.value.reason is SourceSnapshotMemberFailureReason.SOURCE_BINDING_INVALID


@pytest.mark.parametrize(
    "changes",
    [
        {"member_kind": SourceSnapshotMemberKind.ARTIFACT},
        {"ingestion_artifact_id": _ARTIFACT_ID},
        {"endpoint_id": None},
    ],
)
def test_mixed_member_shape_is_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(SourceSnapshotMemberValidationError):
        replace(endpoint_request(), **changes)


@pytest.mark.parametrize("locator", ["", " leading", "trailing ", "e\u0301", "line\nbreak"])
def test_locator_requires_bounded_trimmed_nfc_text(locator: str) -> None:
    with pytest.raises(SourceSnapshotMemberValidationError) as exc_info:
        replace(endpoint_request(), locator=locator)

    assert exc_info.value.reason is SourceSnapshotMemberFailureReason.MEMBER_INVALID
    if locator:
        assert locator not in repr(exc_info.value)


def test_rejected_snapshot_requires_publication_verification() -> None:
    rejected = replace(provenance(), rejected_record_count=1)

    with pytest.raises(SourceSnapshotMemberValidationError):
        replace(endpoint_request(), provenance=rejected)


class CapturingRepository:
    async def append_snapshot_member(self, request: SourceSnapshotMemberCreate) -> SourceSnapshotMemberReceipt:
        return SourceSnapshotMemberReceipt(
            source_snapshot_member_id=_MEMBER_ID,
            source_snapshot_id=request.provenance.source_snapshot_id,
            member_kind=request.member_kind,
            endpoint_id=request.endpoint_id,
            operation_id=request.operation_id,
            ingestion_artifact_id=request.ingestion_artifact_id,
            content_sha256=request.content_sha256,
        )


async def test_append_service_checks_repository_receipt_without_exposing_locator() -> None:
    result = await append_snapshot_member(endpoint_request(), repository=CapturingRepository())

    assert result.source_snapshot_member_id == _MEMBER_ID
    assert "records" not in repr(result)
    assert "records" not in repr(endpoint_request())
