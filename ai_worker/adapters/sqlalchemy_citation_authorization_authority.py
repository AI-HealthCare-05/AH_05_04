"""SQLAlchemy Core exact reader/store for Citation Authorization authority (#869)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import column, insert, select, table
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.citation_authorization import (
    CitationAuthorizationReceipt,
    CitationAuthorizationSelectionEntry,
    CitationAuthorizationSelectionReceipt,
    GuardDecision,
    GuardOperation,
    RuntimeEnvironment,
    UsePurpose,
)
from ai_worker.tasks.rag.citation_authorization_authority import (
    CitationAuthorityAggregate,
    CitationAuthorizationAuthorityError,
    CitationMemberEligibilityObservation,
    CitationSourceEligibilityObservation,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.source_member_identity import SourceMemberKind
from rag_runtime.citation_authorization_authority import (
    AUTHORITY_ARTIFACT_VERSION,
    MEMBER_DECISION_ARTIFACT_CODE,
    SOURCE_DECISION_ARTIFACT_CODE,
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


def _columns(*names: str):
    return tuple(column(name) for name in names)


_SOURCE_DECISION = table(
    "rag_citation_authorization_source_decision",
    *_columns(
        "id",
        "artifact_code",
        "artifact_version",
        "artifact_content_sha256",
        "request_sha256",
        "request_guard_decision_id",
        "origin_guard_artifact_code",
        "origin_guard_artifact_version",
        "origin_guard_content_sha256",
        "user_id",
        "request_operation_code",
        "environment",
        "bundle_id",
        "bundle_manifest_hash",
        "scope_manifest_hash",
        "source_snapshot_id",
        "source_use_approval_id",
        "source_code",
        "source_version",
        "approval_version",
        "purpose",
        "evaluation_time",
        "approval_valid_from",
        "approval_expires_at",
        "approval_revoked_at",
        "source_lifecycle_status",
        "snapshot_verification_status",
        "actual_decision_outcome",
        "reason_code",
    ),
)
_MEMBER_DECISION = table(
    "rag_citation_authorization_member_decision",
    *_columns(
        "id",
        "source_decision_id",
        "artifact_code",
        "artifact_version",
        "artifact_content_sha256",
        "request_sha256",
        "source_decision_content_sha256",
        "source_snapshot_id",
        "source_snapshot_member_id",
        "source_code",
        "source_version",
        "member_kind",
        "endpoint_code",
        "operation_code",
        "artifact_member_code",
        "artifact_member_version",
        "evaluation_time",
        "endpoint_lifecycle_status",
        "endpoint_runtime_status",
        "endpoint_acquisition_status",
        "operation_runtime_status",
        "operation_acquisition_status",
        "current_member_kind",
        "snapshot_endpoint_id",
        "snapshot_operation_id",
        "current_endpoint_id",
        "current_operation_id",
        "current_ingestion_artifact_id",
        "artifact_fk_exists",
        "actual_decision_outcome",
        "reason_code",
    ),
)
_RECEIPT = table(
    "rag_citation_authorization_receipt",
    *_columns(
        "id",
        "artifact_code",
        "artifact_version",
        "artifact_content_sha256",
        "request_sha256",
        "origin_guard_artifact_code",
        "origin_guard_artifact_version",
        "origin_guard_content_sha256",
        "origin_decision",
        "operation",
        "environment",
        "bundle_id",
        "bundle_manifest_hash",
    ),
    column("request_scope_codes", JSONB()),
    *_columns(
        "scope_manifest_hash",
        "validated_selection_sha256",
        "selection_manifest_sha256",
    ),
)
_SELECTION = table(
    "rag_citation_authorization_receipt_selection",
    *_columns(
        "id",
        "receipt_id",
        "selection_order",
        "source_decision_id",
        "member_decision_id",
        "source_code",
        "source_version",
        "member_kind",
        "endpoint_code",
        "operation_code",
        "artifact_member_code",
        "artifact_member_version",
        "selected_for_operation",
        "purpose",
        "source_decision",
        "member_decision",
    ),
)

_SOURCE = table("rag_source", column("id"), column("source_code"), column("lifecycle_status"))
_ENDPOINT = table(
    "rag_source_endpoint",
    column("id"),
    column("source_id"),
    column("endpoint_code"),
    column("lifecycle_status"),
    column("runtime_status"),
    column("acquisition_status"),
)
_OPERATION = table(
    "rag_source_operation",
    column("id"),
    column("endpoint_id"),
    column("operation_code"),
    column("runtime_status"),
    column("acquisition_status"),
)
_SNAPSHOT = table(
    "rag_source_snapshot",
    column("id"),
    column("operation_id"),
    column("source_version"),
    column("verification_status"),
)
_SNAPSHOT_MEMBER = table(
    "rag_source_snapshot_member",
    column("id"),
    column("source_snapshot_id"),
    column("member_kind"),
    column("endpoint_id"),
    column("operation_id"),
    column("ingestion_artifact_id"),
)
_INGESTION_ARTIFACT = table("rag_source_ingestion_artifact", column("id"))


class SqlAlchemyCitationEligibilityReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read_source_exact(self, *, source_snapshot_id: UUID) -> CitationSourceEligibilityObservation | None:
        statement = (
            select(
                _SNAPSHOT.c.id.label("source_snapshot_id"),
                _SOURCE.c.source_code,
                _SNAPSHOT.c.source_version,
                _SOURCE.c.lifecycle_status.label("source_lifecycle_status"),
                _SNAPSHOT.c.verification_status.label("snapshot_verification_status"),
            )
            .select_from(
                _SNAPSHOT.join(_OPERATION, _SNAPSHOT.c.operation_id == _OPERATION.c.id)
                .join(_ENDPOINT, _OPERATION.c.endpoint_id == _ENDPOINT.c.id)
                .join(_SOURCE, _ENDPOINT.c.source_id == _SOURCE.c.id)
            )
            .where(_SNAPSHOT.c.id == str(source_snapshot_id))
        )
        try:
            row = (await self._session.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise CitationAuthorizationAuthorityError("current source exact read failed") from error
        if row is None:
            return None
        return CitationSourceEligibilityObservation(
            source_snapshot_id=UUID(str(row["source_snapshot_id"])),
            source_code=str(row["source_code"]),
            source_version=str(row["source_version"]),
            source_lifecycle_status=str(row["source_lifecycle_status"]),
            snapshot_verification_status=str(row["snapshot_verification_status"]),
        )

    async def read_member_exact(
        self, *, source_snapshot_id: UUID, source_snapshot_member_id: UUID
    ) -> CitationMemberEligibilityObservation | None:
        origin = (
            _SNAPSHOT_MEMBER.join(_SNAPSHOT, _SNAPSHOT_MEMBER.c.source_snapshot_id == _SNAPSHOT.c.id)
            .join(_OPERATION, _SNAPSHOT.c.operation_id == _OPERATION.c.id)
            .join(_ENDPOINT, _OPERATION.c.endpoint_id == _ENDPOINT.c.id)
            .outerjoin(_INGESTION_ARTIFACT, _SNAPSHOT_MEMBER.c.ingestion_artifact_id == _INGESTION_ARTIFACT.c.id)
        )
        statement = (
            select(
                _SNAPSHOT_MEMBER.c.source_snapshot_id,
                _SNAPSHOT_MEMBER.c.id.label("source_snapshot_member_id"),
                _SNAPSHOT_MEMBER.c.member_kind,
                _SNAPSHOT_MEMBER.c.endpoint_id,
                _SNAPSHOT_MEMBER.c.operation_id,
                _SNAPSHOT_MEMBER.c.ingestion_artifact_id,
                _ENDPOINT.c.id.label("snapshot_endpoint_id"),
                _OPERATION.c.id.label("snapshot_operation_id"),
                _ENDPOINT.c.endpoint_code,
                _OPERATION.c.operation_code,
                _ENDPOINT.c.lifecycle_status.label("endpoint_lifecycle_status"),
                _ENDPOINT.c.runtime_status.label("endpoint_runtime_status"),
                _ENDPOINT.c.acquisition_status.label("endpoint_acquisition_status"),
                _OPERATION.c.runtime_status.label("operation_runtime_status"),
                _OPERATION.c.acquisition_status.label("operation_acquisition_status"),
                _INGESTION_ARTIFACT.c.id.label("artifact_id"),
            )
            .select_from(origin)
            .where(
                _SNAPSHOT_MEMBER.c.source_snapshot_id == str(source_snapshot_id),
                _SNAPSHOT_MEMBER.c.id == str(source_snapshot_member_id),
            )
        )
        try:
            row = (await self._session.execute(statement)).mappings().one_or_none()
        except SQLAlchemyError as error:
            raise CitationAuthorizationAuthorityError("current member exact read failed") from error
        if row is None:
            return None
        return CitationMemberEligibilityObservation(
            source_snapshot_id=UUID(str(row["source_snapshot_id"])),
            source_snapshot_member_id=UUID(str(row["source_snapshot_member_id"])),
            member_kind=str(row["member_kind"]),
            endpoint_id=_uuid(row["endpoint_id"]),
            operation_id=_uuid(row["operation_id"]),
            ingestion_artifact_id=_uuid(row["ingestion_artifact_id"]),
            endpoint_code=str(row["endpoint_code"]) if row["endpoint_code"] is not None else None,
            operation_code=str(row["operation_code"]) if row["operation_code"] is not None else None,
            endpoint_lifecycle_status=str(row["endpoint_lifecycle_status"]),
            endpoint_runtime_status=str(row["endpoint_runtime_status"]),
            endpoint_acquisition_status=str(row["endpoint_acquisition_status"]),
            operation_runtime_status=str(row["operation_runtime_status"]),
            operation_acquisition_status=str(row["operation_acquisition_status"]),
            artifact_fk_exists=row["artifact_id"] is not None,
            snapshot_endpoint_id=_uuid(row["snapshot_endpoint_id"]),
            snapshot_operation_id=_uuid(row["snapshot_operation_id"]),
        )


class SqlAlchemyCitationAuthorizationAuthorityStore:
    """Append/read a complete aggregate in the caller-owned transaction; never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, aggregate: CitationAuthorityAggregate) -> None:
        receipt_id = uuid4()
        source_ids: dict[str, UUID] = {}
        member_ids: dict[str, UUID] = {}
        try:
            for source_projection in aggregate.source_decisions:
                ref = compute_citation_source_decision_ref(source_projection)
                row_id = uuid4()
                source_ids[ref.content_sha256] = row_id
                await self._session.execute(
                    insert(_SOURCE_DECISION).values(
                        id=str(row_id),
                        artifact_code=ref.artifact_code,
                        artifact_version=ref.version,
                        artifact_content_sha256=ref.content_sha256,
                        **_source_values(source_projection),
                    )
                )
            for member_projection in aggregate.member_decisions:
                ref = compute_citation_member_decision_ref(member_projection)
                row_id = uuid4()
                member_ids[ref.content_sha256] = row_id
                await self._session.execute(
                    insert(_MEMBER_DECISION).values(
                        id=str(row_id),
                        source_decision_id=str(source_ids[member_projection.source_decision_content_sha256]),
                        artifact_code=ref.artifact_code,
                        artifact_version=ref.version,
                        artifact_content_sha256=ref.content_sha256,
                        **_member_values(member_projection),
                    )
                )
            receipt = aggregate.receipt
            await self._session.execute(
                insert(_RECEIPT).values(
                    id=str(receipt_id),
                    artifact_code=receipt.receipt_ref.artifact_code,
                    artifact_version=receipt.receipt_ref.version,
                    artifact_content_sha256=receipt.receipt_ref.content_sha256,
                    request_sha256=receipt.request_sha256,
                    origin_guard_artifact_code=receipt.origin_guard_ref.artifact_code,
                    origin_guard_artifact_version=receipt.origin_guard_ref.version,
                    origin_guard_content_sha256=receipt.origin_guard_ref.content_sha256,
                    origin_decision=receipt.origin_decision.value,
                    operation=receipt.operation.value,
                    environment=receipt.environment.value,
                    bundle_id=receipt.bundle_id,
                    bundle_manifest_hash=receipt.bundle_manifest_hash,
                    request_scope_codes=list(receipt.request_scope_codes),
                    scope_manifest_hash=receipt.scope_manifest_hash,
                    validated_selection_sha256=receipt.validated_selection_sha256,
                    selection_manifest_sha256=receipt.selection_manifest_sha256,
                )
            )
            for order, selection in enumerate(receipt.selections):
                await self._session.execute(
                    insert(_SELECTION).values(
                        id=str(uuid4()),
                        receipt_id=str(receipt_id),
                        selection_order=order,
                        source_decision_id=str(source_ids[selection.source_decision_ref.content_sha256]),
                        member_decision_id=str(member_ids[selection.member_decision_ref.content_sha256]),
                        source_code=selection.selection.source_code,
                        source_version=selection.selection.source_version,
                        member_kind=selection.selection.member_kind.value,
                        endpoint_code=selection.selection.endpoint_code,
                        operation_code=selection.selection.operation_code,
                        artifact_member_code=selection.selection.artifact_code,
                        artifact_member_version=selection.selection.artifact_version,
                        selected_for_operation=selection.selected_for_operation,
                        purpose=selection.purpose.value,
                        source_decision=selection.source_decision.value,
                        member_decision=selection.member_decision.value,
                    )
                )
        except (KeyError, SQLAlchemyError, ValueError) as error:
            raise CitationAuthorizationAuthorityError("citation authority aggregate insert failed") from error

    async def read_complete(self, *, request_sha256: str) -> CitationAuthorityAggregate | None:  # noqa: C901
        try:
            receipt_row = (
                (await self._session.execute(select(*_RECEIPT.c).where(_RECEIPT.c.request_sha256 == request_sha256)))
                .mappings()
                .one_or_none()
            )
            if receipt_row is None:
                source_exists = (
                    await self._session.execute(
                        select(_SOURCE_DECISION.c.id).where(_SOURCE_DECISION.c.request_sha256 == request_sha256)
                    )
                ).first()
                member_exists = (
                    await self._session.execute(
                        select(_MEMBER_DECISION.c.id).where(_MEMBER_DECISION.c.request_sha256 == request_sha256)
                    )
                ).first()
                if source_exists or member_exists:
                    raise CitationAuthorizationAuthorityError("partial citation authority aggregate")
                return None
            rows = list(
                (
                    await self._session.execute(
                        select(*_SELECTION.c)
                        .where(_SELECTION.c.receipt_id == receipt_row["id"])
                        .order_by(_SELECTION.c.selection_order)
                    )
                )
                .mappings()
                .all()
            )
            if not rows or [row["selection_order"] for row in rows] != list(range(len(rows))):
                raise CitationAuthorizationAuthorityError("receipt selections are partial")
            source_rows = [await self._row_by_id(_SOURCE_DECISION, row["source_decision_id"]) for row in rows]
            member_rows = [await self._row_by_id(_MEMBER_DECISION, row["member_decision_id"]) for row in rows]
            source_pairs = {str(row["artifact_content_sha256"]): (row, _source_projection(row)) for row in source_rows}
            member_pairs = {str(row["artifact_content_sha256"]): (row, _member_projection(row)) for row in member_rows}
            for persisted, source_projection in source_pairs.values():
                if (
                    compute_citation_source_decision_ref(source_projection).content_sha256
                    != persisted["artifact_content_sha256"]
                ):
                    raise CitationAuthorizationAuthorityError("source decision artifact mismatch")
            for persisted, member_projection in member_pairs.values():
                if (
                    compute_citation_member_decision_ref(member_projection).content_sha256
                    != persisted["artifact_content_sha256"]
                ):
                    raise CitationAuthorizationAuthorityError("member decision artifact mismatch")
            receipt = _receipt(receipt_row, rows, source_rows, member_rows)
            receipt_projection = _receipt_projection(receipt)
            if compute_citation_receipt_ref(receipt_projection).content_sha256 != receipt.receipt_ref.content_sha256:
                raise CitationAuthorizationAuthorityError("receipt artifact mismatch")
            return CitationAuthorityAggregate(
                tuple(pair[1] for pair in source_pairs.values()),
                tuple(pair[1] for pair in member_pairs.values()),
                receipt,
            )
        except CitationAuthorizationAuthorityError:
            raise
        except (KeyError, SQLAlchemyError, TypeError, ValueError) as error:
            raise CitationAuthorizationAuthorityError("citation authority aggregate exact read failed") from error

    async def _row_by_id(self, source, row_id) -> Mapping[Any, Any]:
        row = (await self._session.execute(select(*source.c).where(source.c.id == row_id))).mappings().one_or_none()
        if row is None:
            raise CitationAuthorizationAuthorityError("citation authority referenced decision is absent")
        return row


def _uuid(value: object) -> UUID | None:
    return None if value is None else UUID(str(value))


def _source_values(value: CitationSourceDecisionProjection) -> dict[str, object]:
    return {
        field: (item.value if isinstance(item, StrEnum) else str(item) if isinstance(item, UUID) else item)
        for field, item in ((item.name, getattr(value, item.name)) for item in fields(value))
    }


def _member_values(value: CitationMemberDecisionProjection) -> dict[str, object]:
    values = {
        field: (item.value if isinstance(item, StrEnum) else str(item) if isinstance(item, UUID) else item)
        for field, item in ((item.name, getattr(value, item.name)) for item in fields(value))
    }
    values["artifact_member_code"] = values.pop("artifact_code")
    values["artifact_member_version"] = values.pop("artifact_version")
    return values


def _source_projection(row) -> CitationSourceDecisionProjection:
    return CitationSourceDecisionProjection(
        request_sha256=str(row["request_sha256"]),
        request_guard_decision_id=UUID(str(row["request_guard_decision_id"])),
        origin_guard_artifact_code=str(row["origin_guard_artifact_code"]),
        origin_guard_artifact_version=str(row["origin_guard_artifact_version"]),
        origin_guard_content_sha256=str(row["origin_guard_content_sha256"]),
        user_id=UUID(str(row["user_id"])),
        request_operation_code=str(row["request_operation_code"]),
        environment=str(row["environment"]),
        bundle_id=UUID(str(row["bundle_id"])),
        bundle_manifest_hash=str(row["bundle_manifest_hash"]),
        scope_manifest_hash=str(row["scope_manifest_hash"]),
        source_snapshot_id=UUID(str(row["source_snapshot_id"])),
        source_use_approval_id=UUID(str(row["source_use_approval_id"])),
        source_code=str(row["source_code"]),
        source_version=str(row["source_version"]),
        approval_version=str(row["approval_version"]),
        purpose=str(row["purpose"]),
        evaluation_time=row["evaluation_time"],
        approval_valid_from=row["approval_valid_from"],
        approval_expires_at=row["approval_expires_at"],
        approval_revoked_at=row["approval_revoked_at"],
        source_lifecycle_status=str(row["source_lifecycle_status"]),
        snapshot_verification_status=str(row["snapshot_verification_status"]),
        actual_decision_outcome=CitationAuthorityDecision(str(row["actual_decision_outcome"])),
        reason_code=CitationAuthorityReason(str(row["reason_code"])),
    )


def _member_projection(row) -> CitationMemberDecisionProjection:
    return CitationMemberDecisionProjection(
        request_sha256=str(row["request_sha256"]),
        source_decision_content_sha256=str(row["source_decision_content_sha256"]),
        source_snapshot_id=UUID(str(row["source_snapshot_id"])),
        source_snapshot_member_id=UUID(str(row["source_snapshot_member_id"])),
        source_code=str(row["source_code"]),
        source_version=str(row["source_version"]),
        member_kind=str(row["member_kind"]),
        endpoint_code=row["endpoint_code"],
        operation_code=row["operation_code"],
        artifact_code=row["artifact_member_code"],
        artifact_version=row["artifact_member_version"],
        evaluation_time=row["evaluation_time"],
        endpoint_lifecycle_status=row["endpoint_lifecycle_status"],
        endpoint_runtime_status=row["endpoint_runtime_status"],
        endpoint_acquisition_status=row["endpoint_acquisition_status"],
        operation_runtime_status=row["operation_runtime_status"],
        operation_acquisition_status=row["operation_acquisition_status"],
        current_member_kind=row["current_member_kind"],
        snapshot_endpoint_id=_uuid(row["snapshot_endpoint_id"]),
        snapshot_operation_id=_uuid(row["snapshot_operation_id"]),
        current_endpoint_id=_uuid(row["current_endpoint_id"]),
        current_operation_id=_uuid(row["current_operation_id"]),
        current_ingestion_artifact_id=_uuid(row["current_ingestion_artifact_id"]),
        artifact_fk_exists=row["artifact_fk_exists"],
        actual_decision_outcome=CitationAuthorityDecision(str(row["actual_decision_outcome"])),
        reason_code=CitationAuthorityReason(str(row["reason_code"])),
    )


def _receipt(receipt_row, rows, source_rows, member_rows) -> CitationAuthorizationReceipt:
    selections = tuple(
        CitationAuthorizationSelectionReceipt(
            selection=CitationAuthorizationSelectionEntry(
                source_code=str(row["source_code"]),
                source_version=str(row["source_version"]),
                member_kind=SourceMemberKind(str(row["member_kind"])),
                endpoint_code=row["endpoint_code"],
                operation_code=row["operation_code"],
                artifact_code=row["artifact_member_code"],
                artifact_version=row["artifact_member_version"],
            ),
            source_decision_ref=ImmutableArtifactRef(
                SOURCE_DECISION_ARTIFACT_CODE, AUTHORITY_ARTIFACT_VERSION, str(src["artifact_content_sha256"])
            ),
            member_decision_ref=ImmutableArtifactRef(
                MEMBER_DECISION_ARTIFACT_CODE, AUTHORITY_ARTIFACT_VERSION, str(mem["artifact_content_sha256"])
            ),
            selected_for_operation=bool(row["selected_for_operation"]),
            purpose=UsePurpose(str(row["purpose"])),
            source_decision=GuardDecision(str(row["source_decision"])),
            member_decision=GuardDecision(str(row["member_decision"])),
        )
        for row, src, mem in zip(rows, source_rows, member_rows, strict=True)
    )
    return CitationAuthorizationReceipt(
        receipt_ref=ImmutableArtifactRef(
            str(receipt_row["artifact_code"]),
            str(receipt_row["artifact_version"]),
            str(receipt_row["artifact_content_sha256"]),
        ),
        request_sha256=str(receipt_row["request_sha256"]),
        origin_guard_ref=ImmutableArtifactRef(
            str(receipt_row["origin_guard_artifact_code"]),
            str(receipt_row["origin_guard_artifact_version"]),
            str(receipt_row["origin_guard_content_sha256"]),
        ),
        origin_decision=GuardDecision(str(receipt_row["origin_decision"])),
        operation=GuardOperation(str(receipt_row["operation"])),
        environment=RuntimeEnvironment(str(receipt_row["environment"])),
        bundle_id=str(receipt_row["bundle_id"]),
        bundle_manifest_hash=str(receipt_row["bundle_manifest_hash"]),
        request_scope_codes=tuple(receipt_row["request_scope_codes"]),
        scope_manifest_hash=str(receipt_row["scope_manifest_hash"]),
        validated_selection_sha256=str(receipt_row["validated_selection_sha256"]),
        selection_manifest_sha256=str(receipt_row["selection_manifest_sha256"]),
        selections=selections,
    )


def _receipt_projection(receipt: CitationAuthorizationReceipt) -> CitationReceiptProjection:
    return CitationReceiptProjection(
        request_sha256=receipt.request_sha256,
        origin_guard_artifact_code=receipt.origin_guard_ref.artifact_code,
        origin_guard_artifact_version=receipt.origin_guard_ref.version,
        origin_guard_content_sha256=receipt.origin_guard_ref.content_sha256,
        origin_decision=receipt.origin_decision.value,
        operation=receipt.operation.value,
        environment=receipt.environment.value,
        bundle_id=receipt.bundle_id,
        bundle_manifest_hash=receipt.bundle_manifest_hash,
        request_scope_codes=receipt.request_scope_codes,
        scope_manifest_hash=receipt.scope_manifest_hash,
        validated_selection_sha256=receipt.validated_selection_sha256,
        selection_manifest_sha256=receipt.selection_manifest_sha256,
        selections=tuple(
            CitationReceiptSelectionProjection(
                selection_order=order,
                source_code=item.selection.source_code,
                source_version=item.selection.source_version,
                member_kind=item.selection.member_kind.value,
                endpoint_code=item.selection.endpoint_code,
                operation_code=item.selection.operation_code,
                artifact_code=item.selection.artifact_code,
                artifact_version=item.selection.artifact_version,
                source_decision_content_sha256=item.source_decision_ref.content_sha256,
                member_decision_content_sha256=item.member_decision_ref.content_sha256,
                selected_for_operation=item.selected_for_operation,
                purpose=item.purpose.value,
                source_decision=item.source_decision.value,
                member_decision=item.member_decision.value,
            )
            for order, item in enumerate(receipt.selections)
        ),
    )


__all__ = ["SqlAlchemyCitationAuthorizationAuthorityStore", "SqlAlchemyCitationEligibilityReader"]
