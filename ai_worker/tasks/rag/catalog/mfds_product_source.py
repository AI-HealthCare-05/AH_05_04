"""Read-only MFDS Product Approval -> CatalogProductInput binding."""

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from ai_worker.adapters.local_private_source_artifact_finalizer import LocalPrivateSourceArtifactReader
from ai_worker.tasks.rag.catalog.build import CatalogProductInput
from ai_worker.tasks.rag.catalog.types import CandidateRecordStatus
from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_client.decoders import decode_mfds_json
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata
from ai_worker.tasks.rag.source_ingestion.mfds_label import IngestionArtifactReceipt
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotAttemptReceipt,
    SnapshotProvenanceReceipt,
    SnapshotVerificationStatus,
)

PRODUCT_SOURCE_CODE = "MFDS_PRODUCT_APPROVAL"
PRODUCT_ENDPOINT_CODE = "MFDS_PRODUCT_APPROVAL_API"
PRODUCT_OPERATION_CODE = "LIST_APPROVED_PRODUCTS"
PRODUCT_CODE_SYSTEM = "MFDS_ITEM_SEQ"
PRODUCT_SOURCE_AUTHORITY = SourceOperationIdentity(
    source_code=PRODUCT_SOURCE_CODE,
    endpoint_code=PRODUCT_ENDPOINT_CODE,
    operation_code=PRODUCT_OPERATION_CODE,
)


class ProductSourceBindingError(ValueError):
    """Sanitized, fail-closed source-to-catalog binding failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ProductSourceRepository(Protocol):
    async def get_snapshot_receipt(self, *, snapshot_id: UUID) -> SnapshotProvenanceReceipt | None: ...

    async def get_attempt_receipt(self, *, ingestion_run_id: UUID) -> SnapshotAttemptReceipt | None: ...

    async def get_ingestion_artifact_receipts(
        self, *, ingestion_run_id: UUID
    ) -> tuple[IngestionArtifactReceipt, ...]: ...


def map_mfds_product_record_status(
    *,
    authority: SourceOperationIdentity,
    record: Mapping[str, object],
) -> CandidateRecordStatus:
    """Map only the exact Product Approval authority to Catalog ACTIVE.

    The record contents are deliberately not consulted: this is an authority mapping,
    not a cancellation, expiry, or historical reconciliation rule.
    """
    del record
    if authority != PRODUCT_SOURCE_AUTHORITY:
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY")
    return CandidateRecordStatus.ACTIVE


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError:
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY") from None


def _text(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_RECORD_MAPPING")
    return value.strip()


def _validate_snapshot(receipt: SnapshotProvenanceReceipt, snapshot_id: UUID) -> None:
    if (
        receipt.source_snapshot_id != snapshot_id
        or receipt.source_code != PRODUCT_SOURCE_CODE
        or receipt.endpoint_code != PRODUCT_ENDPOINT_CODE
        or receipt.operation_code != PRODUCT_OPERATION_CODE
        or receipt.endpoint_receipt_hash is None
        or receipt.verification_status is not SnapshotVerificationStatus.CURRENT
        or receipt.rejected_record_count != 0
    ):
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY")
    if receipt.endpoint_id is None or receipt.operation_id is None:
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY")
    try:
        receipt.validate_provenance()
    except (ValueError, TypeError, AttributeError):
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY") from None


def _validate_run(run: SnapshotAttemptReceipt, snapshot: SnapshotProvenanceReceipt, snapshot_id: UUID) -> None:
    if (
        run.snapshot_id != snapshot_id
        or run.run_status != "SUCCEEDED"
        or run.failure_code is not None
        or run.operation_id is None
        or run.operation_id != snapshot.operation_id
    ):
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_INGESTION_RUN")


async def _read_matching_records(
    *,
    repository: ProductSourceRepository,
    artifact_reader: LocalPrivateSourceArtifactReader,
    run_uuid: UUID,
    item_seq: str,
) -> list[Mapping[str, object]]:
    artifacts = await repository.get_ingestion_artifact_receipts(ingestion_run_id=run_uuid)
    if not artifacts:
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_ARTIFACT")
    matches: list[Mapping[str, object]] = []
    for artifact in sorted(artifacts, key=lambda value: value.page_number):
        try:
            raw = artifact_reader.read_verified(
                object_key=artifact.object_key,
                metadata=RawArtifactMetadata(
                    artifact_key=artifact.artifact_key,
                    raw_checksum=artifact.raw_checksum,
                    byte_size=artifact.byte_size,
                    content_type=artifact.content_type,
                ),
            )
            decoded = decode_mfds_json(raw, artifact.content_type)
        except (OSError, ValueError, TypeError, UnicodeError):
            raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_ARTIFACT") from None
        if decoded.page_number != artifact.page_number:
            raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_ARTIFACT")
        matches.extend(record for record in decoded.records if record.get("ITEM_SEQ") == item_seq)
    return matches


async def read_product_input(
    *,
    repository: ProductSourceRepository,
    artifact_reader: LocalPrivateSourceArtifactReader,
    source_snapshot_id: str,
    ingestion_run_id: str,
    item_seq: str,
) -> tuple[CatalogProductInput, SnapshotProvenanceReceipt]:
    """Bind exactly one Product Approval record from one explicit, successful run."""
    snapshot_uuid = _uuid(source_snapshot_id)
    run_uuid = _uuid(ingestion_run_id)
    if not item_seq.strip():
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_RECORD_MAPPING")

    snapshot = await repository.get_snapshot_receipt(snapshot_id=snapshot_uuid)
    if snapshot is None:
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY")
    _validate_snapshot(snapshot, snapshot_uuid)
    if snapshot.endpoint_id is None or snapshot.operation_id is None:
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY")

    run = await repository.get_attempt_receipt(ingestion_run_id=run_uuid)
    if run is None:
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_INGESTION_RUN")
    _validate_run(run, snapshot, snapshot_uuid)

    matches = await _read_matching_records(
        repository=repository, artifact_reader=artifact_reader, run_uuid=run_uuid, item_seq=item_seq
    )
    if len(matches) != 1:
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_ITEM_SEQ_CARDINALITY")
    record = matches[0]
    manufacturer_name = record.get("ENTP_NAME")
    if not isinstance(manufacturer_name, str):
        manufacturer_name = None
    product = CatalogProductInput(
        source_snapshot_id=str(snapshot.source_snapshot_id),
        source_record_key=f"ITEM_SEQ:{_text(record, 'ITEM_SEQ')}",
        code_system=PRODUCT_CODE_SYSTEM,
        canonical_code=_text(record, "ITEM_SEQ"),
        product_name=_text(record, "ITEM_NAME"),
        product_status=map_mfds_product_record_status(authority=PRODUCT_SOURCE_AUTHORITY, record=record),
        manufacturer_name=manufacturer_name,
    )
    return product, snapshot
