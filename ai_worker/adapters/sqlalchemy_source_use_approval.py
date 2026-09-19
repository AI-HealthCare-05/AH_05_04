"""Read-only AI Worker adapter for exact Source Use Approval facts (#807)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import DateTime, String, and_, column, select, table, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import (
    SourceUseApprovalIdentity,
    SourceUseApprovalObservation,
    SourceUseApprovalValidationError,
    SourceUsePurpose,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AsyncSession]

_APPROVAL = table(
    "rag_source_use_approval",
    column("id", String(36)),
    column("source_snapshot_id", String(36)),
    column("source_code", String(100)),
    column("source_version", String(200)),
    column("environment", String(20)),
    column("purpose", String(40)),
    column("approval_version", String(120)),
    column("valid_from", DateTime(timezone=True)),
    column("expires_at", DateTime(timezone=True)),
    column("revoked_at", DateTime(timezone=True)),
    column("revoked_by", String(36)),
    column("revoked_reason", String(200)),
    column("actor_id", String(36)),
    column("evidence_ref", String(500)),
)


class SourceUseApprovalReadError(RuntimeError):
    """The exact approval read failed or returned an untrustworthy row."""


class _CorruptSourceUseApprovalRowError(Exception):
    """Internal signal for a persisted row that violates the pure contract."""


def _exact_statement(identity: SourceUseApprovalIdentity):
    """Build an equality-only six-field lookup; no latest/current selector is allowed."""

    return (
        select(*_APPROVAL.c)
        .select_from(_APPROVAL)
        .where(
            and_(
                _APPROVAL.c.source_snapshot_id == str(identity.source_snapshot_id),
                _APPROVAL.c.source_code == identity.source_code,
                _APPROVAL.c.source_version == identity.source_version,
                _APPROVAL.c.environment == identity.environment.value,
                _APPROVAL.c.purpose == identity.purpose.value,
                _APPROVAL.c.approval_version == identity.approval_version,
            )
        )
    )


def _persisted_uuid(value: object, field: str, *, optional: bool = False) -> UUID | None:
    if value is None and optional:
        return None
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise _CorruptSourceUseApprovalRowError(f"persisted {field} is malformed") from error


def _persisted_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or not value.strip() or value != value.strip():
        raise _CorruptSourceUseApprovalRowError(f"persisted {field} is missing or noncanonical")
    return value


def _persisted_utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise _CorruptSourceUseApprovalRowError(f"persisted {field} is not timezone-aware UTC")
    return value.astimezone(UTC)


def _persisted_optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _persisted_text(value, field)


def _observation_from_row(row: RowMapping) -> SourceUseApprovalObservation:
    try:
        identity = SourceUseApprovalIdentity(
            source_snapshot_id=_persisted_uuid(row["source_snapshot_id"], "source_snapshot_id"),  # type: ignore[arg-type]
            source_code=_persisted_text(row["source_code"], "source_code"),
            source_version=_persisted_text(row["source_version"], "source_version"),
            environment=RuntimeEnvironmentCode(_persisted_text(row["environment"], "environment")),
            purpose=SourceUsePurpose(_persisted_text(row["purpose"], "purpose")),
            approval_version=_persisted_text(row["approval_version"], "approval_version"),
        )
        return SourceUseApprovalObservation(
            id=_persisted_uuid(row["id"], "id"),  # type: ignore[arg-type]
            identity=identity,
            valid_from=_persisted_utc(row["valid_from"], "valid_from"),
            expires_at=_persisted_utc(row["expires_at"], "expires_at"),
            revoked_at=None if row["revoked_at"] is None else _persisted_utc(row["revoked_at"], "revoked_at"),
            revoked_by=_persisted_uuid(row["revoked_by"], "revoked_by", optional=True),
            revoked_reason=_persisted_optional_text(row["revoked_reason"], "revoked_reason"),
            actor_id=_persisted_uuid(row["actor_id"], "actor_id"),  # type: ignore[arg-type]
            evidence_ref=_persisted_text(row["evidence_ref"], "evidence_ref"),
        )
    except (SourceUseApprovalValidationError, ValueError, TypeError) as error:
        raise _CorruptSourceUseApprovalRowError("persisted Source Use Approval row is invalid") from error


class SqlAlchemySourceUseApprovalReader:
    """Read exact historical approvals without importing Backend ORM models."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def read_exact(self, identity: SourceUseApprovalIdentity) -> SourceUseApprovalObservation | None:
        if type(identity) is not SourceUseApprovalIdentity:
            raise SourceUseApprovalReadError("SourceUseApprovalIdentity is invalid")
        return await self._read(_exact_statement(identity))

    async def read_usable_exact(
        self,
        identity: SourceUseApprovalIdentity,
        *,
        evaluation_time: datetime,
    ) -> SourceUseApprovalObservation | None:
        observation = await self.read_exact(identity)
        if observation is None or not observation.is_usable_at(evaluation_time):
            return None
        return observation

    async def _read(self, statement) -> SourceUseApprovalObservation | None:
        try:
            rows = await self._fetch_rows(statement)
        except SQLAlchemyError:
            logger.error("Source Use Approval read failed")
            raise SourceUseApprovalReadError("Source Use Approval read failed") from None

        if not rows:
            return None
        if len(rows) > 1:
            logger.error("Source Use Approval exact lookup is ambiguous: %d", len(rows))
            raise SourceUseApprovalReadError("Source Use Approval exact lookup is ambiguous")
        try:
            return _observation_from_row(rows[0])
        except _CorruptSourceUseApprovalRowError as error:
            logger.error("Source Use Approval row is corrupt: %s", error)
            raise SourceUseApprovalReadError("Source Use Approval row is corrupt") from None

    async def _fetch_rows(self, statement) -> list[RowMapping]:
        async with self._session_factory() as session, session.begin():
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            result = await session.execute(statement)
            return list(result.mappings().all())


__all__ = ["SourceUseApprovalReadError", "SqlAlchemySourceUseApprovalReader"]
