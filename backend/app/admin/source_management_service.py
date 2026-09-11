"""Source/Catalog management transaction. The caller owns commit/rollback."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, func, inspect, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.source_management_contract import ManagementCommand, ManagementResult, TargetKind, UpdateCommand
from app.core.db.databases import Base
from app.core.errors import ApiError
from app.models.rag_catalog import (
    RagMedicationAlias,
    RagMedicationIngredient,
    RagMedicationProduct,
    RagMedicationProductComponent,
)
from app.models.rag_source import RagSource, RagSourceEndpoint, RagSourceOperation, RagSourceSnapshot
from app.models.source_management import SourceManagementAudit, SourceManagementPermission
from app.models.users import AccountStatus, User

PERMISSION = "source_catalog_manage"
MODELS: dict[TargetKind, Any] = {
    TargetKind.SOURCE: RagSource,
    TargetKind.ENDPOINT: RagSourceEndpoint,
    TargetKind.OPERATION: RagSourceOperation,
    TargetKind.SNAPSHOT: RagSourceSnapshot,
    TargetKind.PRODUCT: RagMedicationProduct,
    TargetKind.INGREDIENT: RagMedicationIngredient,
    TargetKind.ALIAS: RagMedicationAlias,
    TargetKind.COMPONENT: RagMedicationProductComponent,
}
EDITABLE_FIELDS: dict[TargetKind, frozenset[str]] = {
    TargetKind.SOURCE: frozenset({"display_name", "owner_name", "license_name", "attribution_text", "purpose"}),
    TargetKind.ENDPOINT: frozenset({"display_name"}),
    TargetKind.OPERATION: frozenset({"display_name"}),
    TargetKind.SNAPSHOT: frozenset(),
    TargetKind.PRODUCT: frozenset({"product_name", "manufacturer_name", "strength_text", "dosage_form"}),
    TargetKind.INGREDIENT: frozenset({"ingredient_name"}),
    TargetKind.ALIAS: frozenset({"alias_text"}),
    TargetKind.COMPONENT: frozenset({"amount_text"}),
}
NORMALIZED_NAMES = {
    "product_name": "normalized_product_name",
    "ingredient_name": "normalized_ingredient_name",
    "alias_text": "normalized_alias_text",
}


@dataclass(frozen=True)
class ManagementActor:
    user_id: UUID
    token_version: int


def fail(code: str, status: int = 409) -> ApiError:
    return ApiError(status_code=status, code=code, message="Source·Catalog 관리 요청을 처리할 수 없습니다.")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, (UUID, Decimal)):
        return str(value)
    raise TypeError("Unsupported fingerprint value")


def fingerprint(values: dict[str, Any]) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_value)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def row_hash(row: Any) -> str:
    return fingerprint(
        {
            column.key: getattr(row, column.key)
            for column in inspect(type(row)).columns
            if not (isinstance(row, RagSourceSnapshot) and column.key == "management_lock_marker")
        }
    )


def provenance(snapshot: RagSourceSnapshot | None) -> dict[str, str | None]:
    if snapshot is None:
        return dict.fromkeys(
            ("source_version", "external_version", "snapshot_id", "canonical_checksum", "receipt_hash")
        )
    if not snapshot.endpoint_receipt_hash or not re.fullmatch(r"[0-9a-f]{64}", snapshot.endpoint_receipt_hash):
        raise fail("MANAGEMENT_PROVENANCE_MISSING")
    return {
        "source_version": snapshot.source_version,
        "external_version": None,
        "snapshot_id": str(snapshot.id),
        "canonical_checksum": snapshot.canonical_checksum,
        "receipt_hash": snapshot.endpoint_receipt_hash,
    }


class SourceManagementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def authorize(self, actor: ManagementActor, *, lock: bool = False) -> None:
        statement = select(SourceManagementPermission).where(SourceManagementPermission.user_id == actor.user_id)
        if lock:
            statement = statement.with_for_update()
        permission = await self.session.scalar(statement.execution_options(populate_existing=True))
        # Read actual server state on every request, including a retry after revocation.
        user = await self.session.scalar(
            select(User.id).where(
                User.id == actor.user_id,
                User.is_active.is_(True),
                User.account_status == AccountStatus.ACTIVE,
                User.token_version == actor.token_version,
            )
        )
        if user is None:
            raise fail("INVALID_TOKEN", 401)
        if permission is None or not permission.enabled:
            raise fail("SOURCE_MANAGEMENT_FORBIDDEN", 403)

    async def _revision(self, kind: TargetKind, target_id: UUID) -> int:
        return int(
            await self.session.scalar(
                select(func.coalesce(func.max(SourceManagementAudit.after_revision), 0)).where(
                    SourceManagementAudit.target_kind == kind,
                    SourceManagementAudit.target_id == target_id,
                )
            )
            or 0
        )

    async def _load(self, kind: TargetKind, target_id: UUID, *, lock: bool = True) -> Any:
        model = MODELS[kind]
        statement = select(model).where(model.id == target_id)
        if lock:
            statement = statement.with_for_update()
        row = await self.session.scalar(statement.execution_options(populate_existing=True))
        if row is None:
            raise fail("MANAGEMENT_TARGET_NOT_FOUND", 404)
        return row

    async def _lock_snapshot(self, kind: TargetKind, target_id: UUID) -> RagSourceSnapshot | None:
        if kind in {TargetKind.SOURCE, TargetKind.ENDPOINT, TargetKind.OPERATION}:
            return None
        row = await self._load(kind, target_id, lock=False)
        snapshot_id = row.id if kind == TargetKind.SNAPSHOT else row.source_snapshot_id
        snapshot = await self._load(TargetKind.SNAPSHOT, snapshot_id, lock=False)
        # Match the Source lifecycle writer's Operation -> Snapshot lock order.
        operation = await self._load(TargetKind.OPERATION, snapshot.operation_id)
        snapshot = await self._load(TargetKind.SNAPSHOT, snapshot_id)
        if (
            operation.runtime_status != "DISABLED"
            or snapshot.verification_status != "PENDING"
            or snapshot.verified_at is not None
            or snapshot.effective_at is not None
        ):
            raise fail("MANAGEMENT_TARGET_IN_USE")
        return snapshot

    async def _check_references(self, row: Any) -> None:
        # Only trusted ORM metadata defines tables/columns. Composite references use AND,
        # not independent per-column matches. FOR UPDATE blocks concurrent FK additions.
        parent = inspect(type(row)).local_table
        for table in Base.metadata.tables.values():
            for constraint in table.foreign_key_constraints:
                if constraint.referred_table is not parent:
                    continue
                predicates = [element.parent == getattr(row, element.column.key) for element in constraint.elements]
                if await self.session.scalar(
                    select(select(literal(1)).select_from(table).where(and_(*predicates)).exists())
                ):
                    raise fail("MANAGEMENT_TARGET_REFERENCED")

    async def _validate_state(self, kind: TargetKind, row: Any) -> None:
        if kind == TargetKind.SOURCE and row.lifecycle_status != "DRAFT":
            raise fail("MANAGEMENT_TARGET_IN_USE")
        if kind == TargetKind.ENDPOINT and (
            row.lifecycle_status != "DRAFT" or row.runtime_status != "DISABLED" or row.acquisition_status != "PENDING"
        ):
            raise fail("MANAGEMENT_TARGET_IN_USE")
        if kind == TargetKind.OPERATION and (row.runtime_status != "DISABLED" or row.acquisition_status != "PENDING"):
            raise fail("MANAGEMENT_TARGET_IN_USE")
        if kind == TargetKind.ALIAS and row.review_status == "APPROVED":
            raise fail("MANAGEMENT_TARGET_IN_USE")
        await self._check_references(row)

    def _apply_changes(self, kind: TargetKind, row: Any, changes: dict[str, str | None]) -> None:
        if not changes.keys() <= EDITABLE_FIELDS[kind]:
            raise fail("MANAGEMENT_FIELD_IMMUTABLE", 422)
        for name, value in changes.items():
            column = inspect(type(row)).columns[name]
            if value is None:
                if not column.nullable:
                    raise fail("MANAGEMENT_INVALID_VALUE", 422)
            elif not value.strip() or len(value) > (getattr(column.type, "length", None) or 2000):
                raise fail("MANAGEMENT_INVALID_VALUE", 422)
            if name in NORMALIZED_NAMES:
                normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "").strip())
                if normalized != getattr(row, NORMALIZED_NAMES[name]):
                    raise fail("MANAGEMENT_IDENTITY_CHANGE_REQUIRES_NEW_VERSION")
            setattr(row, name, value)

    async def inspect_target(self, actor: ManagementActor, kind: TargetKind, target_id: UUID) -> ManagementResult:
        await self.authorize(actor, lock=True)
        row = await self._load(kind, target_id)
        return ManagementResult(
            target_kind=kind, target_id=target_id, revision=await self._revision(kind, target_id), hash=row_hash(row)
        )

    async def mutate(
        self, actor: ManagementActor, kind: TargetKind, target_id: UUID, command: ManagementCommand
    ) -> ManagementResult:
        await self.authorize(actor, lock=True)
        operation = "UPDATE" if isinstance(command, UpdateCommand) else "DELETE"
        request_hash = fingerprint(
            {"kind": kind, "id": target_id, "operation": operation, **command.model_dump(mode="json")}
        )
        existing = await self.session.scalar(
            select(SourceManagementAudit).where(
                SourceManagementAudit.actor_id == actor.user_id, SourceManagementAudit.request_id == command.request_id
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise fail("IDEMPOTENCY_KEY_CONFLICT")
            return self._result(existing)
        snapshot = await self._lock_snapshot(kind, target_id)
        row = await self._load(kind, target_id)
        revision, before_hash = await self._revision(kind, target_id), row_hash(row)
        if revision != command.expected_revision or before_hash != command.expected_hash:
            raise fail("MANAGEMENT_STALE_TARGET")
        await self._validate_state(kind, row)
        before_provenance = provenance(snapshot)
        if isinstance(command, UpdateCommand):
            self._apply_changes(kind, row, command.changes)
            await self.session.flush()
            await self.session.refresh(row)
            after_hash, after_revision = row_hash(row), revision + 1
            after_provenance = provenance(snapshot)
        else:
            # Core DELETE avoids ORM relationship nullification/cascades. References were
            # checked under a row lock; ordinary FK constraints remain the final backstop.
            await self.session.execute(delete(MODELS[kind]).where(MODELS[kind].id == target_id))
            after_hash, after_revision, after_provenance = None, None, None
        audit = SourceManagementAudit(
            target_kind=kind,
            target_id=target_id,
            operation=operation,
            actor_id=actor.user_id,
            permission=PERMISSION,
            reason_code=command.reason_code,
            approval_hash=command.approval_hash,
            request_id=command.request_id,
            request_hash=request_hash,
            before_revision=revision,
            after_revision=after_revision,
            before_hash=before_hash,
            after_hash=after_hash,
            before_provenance=before_provenance,
            after_provenance=after_provenance,
        )
        self.session.add(audit)
        await self.session.flush()
        return self._result(audit)

    @staticmethod
    def _result(audit: SourceManagementAudit) -> ManagementResult:
        return ManagementResult(
            event_id=audit.id,
            target_kind=TargetKind(audit.target_kind),
            target_id=audit.target_id,
            revision=audit.after_revision,
            hash=audit.after_hash,
        )
