"""One-shot permission grant/revoke under the migration owner's isolated credential."""

import argparse
import asyncio
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.source_management_contract import Digest
from app.admin.source_management_service import fingerprint, provenance, row_hash
from app.core.db.databases import AsyncSessionFactory, close_database
from app.models.source_management import SourceManagementAudit, SourceManagementPermission
from app.models.users import User


class PermissionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: UUID
    actor_id: UUID
    enabled: bool
    approval_hash: Digest
    request_id: UUID


async def set_permission(session: AsyncSession, change: PermissionChange) -> UUID:
    owner = await session.scalar(
        text(
            "SELECT pg_get_userbyid(relowner)=current_user FROM pg_class WHERE oid='source_management_permission'::regclass"
        )
    )
    if not owner:
        raise ValueError("Permission changes require the isolated migration owner connection")
    if await session.scalar(select(User.id).where(User.id == change.actor_id)) is None:
        raise ValueError("A recorded operator identity is required")
    # Serializes initial grants too, when a permission row does not exist yet.
    if await session.scalar(select(User.id).where(User.id == change.user_id).with_for_update()) is None:
        raise ValueError("Permission target does not exist")
    request_hash = fingerprint(change.model_dump(mode="json"))
    existing = await session.scalar(
        select(SourceManagementAudit).where(
            SourceManagementAudit.actor_id == change.actor_id, SourceManagementAudit.request_id == change.request_id
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ValueError("Permission request ID conflict")
        return existing.id
    permission = await session.scalar(
        select(SourceManagementPermission)
        .where(SourceManagementPermission.user_id == change.user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    before_hash = row_hash(permission) if permission else fingerprint({"enabled": False})
    revision = int(
        await session.scalar(
            select(func.coalesce(func.max(SourceManagementAudit.after_revision), 0)).where(
                SourceManagementAudit.target_kind == "permission", SourceManagementAudit.target_id == change.user_id
            )
        )
        or 0
    )
    if permission is None:
        permission = SourceManagementPermission(
            user_id=change.user_id, enabled=change.enabled, approval_hash=change.approval_hash
        )
        session.add(permission)
    else:
        permission.enabled = change.enabled
        permission.approval_hash = change.approval_hash
    await session.flush()
    audit = SourceManagementAudit(
        target_kind="permission",
        target_id=change.user_id,
        actor_id=change.actor_id,
        operation="GRANT" if change.enabled else "REVOKE",
        permission="source_catalog_permission_admin",
        reason_code="ACCESS_APPROVAL" if change.enabled else "ACCESS_REVOCATION",
        approval_hash=change.approval_hash,
        request_id=change.request_id,
        request_hash=request_hash,
        before_revision=revision,
        after_revision=revision + 1,
        before_hash=before_hash,
        after_hash=row_hash(permission),
        before_provenance=provenance(None),
        after_provenance=provenance(None),
    )
    session.add(audit)
    await session.flush()
    return audit.id


async def _run(change: PermissionChange) -> None:
    try:
        async with AsyncSessionFactory.begin() as session:
            event_id = await set_permission(session, change)
        print(f"Permission audit event: {event_id}")
    finally:
        await close_database()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("grant", "revoke"))
    parser.add_argument("--user-id", required=True, type=UUID)
    parser.add_argument("--actor-id", required=True, type=UUID)
    parser.add_argument("--approval-hash", required=True)
    parser.add_argument("--request-id", required=True, type=UUID)
    args = parser.parse_args()
    asyncio.run(
        _run(
            PermissionChange(
                user_id=args.user_id,
                actor_id=args.actor_id,
                enabled=args.action == "grant",
                approval_hash=args.approval_hash,
                request_id=args.request_id,
            )
        )
    )


if __name__ == "__main__":
    main()
