"""SQLAlchemy adapter for the isolated protected-retrieval PostgreSQL boundary."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from hashlib import sha256
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.evaluation.protected_retrieval import (
    ApprovalSourceEvidence,
    OpaqueLogicalRef,
    OpaqueRefNamespace,
    OperationAuditEntry,
    OperationAuditOutcome,
    ProtectedAction,
    ProtectedAuditEventKind,
    ProtectedAuditReason,
    ProtectedAuthorizationCapability,
    ProtectedAuthorizationGrant,
    ProtectedDatasetBinding,
    ProtectedOperationRequest,
    ProtectedOperationResult,
    ProtectedSecurityError,
    audit_entry_sha256,
    new_event_id,
)

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_SAFE_DATABASE_REASONS = frozenset(reason.value for reason in ProtectedAuditReason)


def _json_value(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")  # type: ignore[union-attr]
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _database_reason(error: BaseException, fallback: str) -> ProtectedSecurityError:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        message = getattr(current, "message", None)
        sqlstate = getattr(current, "sqlstate", None)
        if sqlstate == "P0001" and message in _SAFE_DATABASE_REASONS:
            return ProtectedSecurityError(message)
        for nested in (getattr(current, "orig", None), current.__cause__, current.__context__):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return ProtectedSecurityError(fallback)


def _model(model_type, value: object, fallback: str):
    try:
        if isinstance(value, (dict, list)):
            return model_type.model_validate_json(json.dumps(value))
        return model_type.model_validate(value)
    except (TypeError, ValueError, ValidationError):
        raise ProtectedSecurityError(fallback) from None


class _ProtectedSession:
    def __init__(self, session: AsyncSession, schema: str) -> None:
        if _IDENTIFIER.fullmatch(schema) is None:
            raise ValueError("protected schema must be a safe PostgreSQL identifier")
        self._session = session
        self._schema = f'"{schema}"'

    async def _scalar(self, function: str, arguments: str = "", parameters: dict[str, object] | None = None):
        try:
            return await self._session.scalar(
                text(f'SELECT {self._schema}."{function}"({arguments})'),
                parameters or {},
            )
        except (DBAPIError, SQLAlchemyError) as error:
            raise _database_reason(error, "INTERNAL_ERROR") from None

    async def _rows(self, function: str, arguments: str, parameters: dict[str, object]) -> list[object]:
        try:
            result = await self._session.execute(
                text(f'SELECT * FROM {self._schema}."{function}"({arguments})'),
                parameters,
            )
            return [row[0] for row in result]
        except (DBAPIError, SQLAlchemyError) as error:
            raise _database_reason(error, "AUDIT_UNAVAILABLE") from None


class PostgresqlTrustedClock:
    """Transaction-scoped PostgreSQL clock snapshot."""

    def __init__(self, trusted_now: datetime) -> None:
        if trusted_now.tzinfo is None or trusted_now.utcoffset() is None:
            raise ProtectedSecurityError("INTERNAL_ERROR")
        self._trusted_now = trusted_now.astimezone(UTC)

    @classmethod
    async def from_session(cls, session: AsyncSession) -> Self:
        try:
            trusted_now = await session.scalar(text("SELECT clock_timestamp()"))
        except SQLAlchemyError:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None
        if not isinstance(trusted_now, datetime):
            raise ProtectedSecurityError("INTERNAL_ERROR")
        return cls(trusted_now)

    def now_utc(self) -> datetime:
        return self._trusted_now


class PostgresqlApprovalEvidenceVerifier(_ProtectedSession):
    async def require_source(self, source_event_id: str) -> ApprovalSourceEvidence:
        value = await self._scalar(
            "load_approval",
            ":source_event_id",
            {"source_event_id": source_event_id},
        )
        return _model(ApprovalSourceEvidence, value, "APPROVAL_NOT_VERIFIED")


class PostgresqlAuthorizationLedger(_ProtectedSession):
    async def find_for(self, request: ProtectedOperationRequest) -> ProtectedAuthorizationGrant | None:
        value = await self._scalar(
            "find_grant",
            "CAST(:request_body AS jsonb)",
            {"request_body": _json_value(request)},
        )
        if value is None:
            return None
        return _model(ProtectedAuthorizationGrant, value, "INTERNAL_ERROR")

    async def require_current(self, grant_id: str) -> ProtectedAuthorizationGrant:
        try:
            UUID(grant_id)
        except ValueError:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND") from None
        value = await self._scalar(
            "require_grant",
            "CAST(:grant_id AS uuid)",
            {"grant_id": grant_id},
        )
        return _model(ProtectedAuthorizationGrant, value, "INTERNAL_ERROR")

    async def require_dataset(self, request: ProtectedOperationRequest) -> ProtectedDatasetBinding:
        value = await self._scalar(
            "load_dataset",
            ":dataset_id, :dataset_version",
            {
                "dataset_id": request.dataset.dataset_id,
                "dataset_version": request.dataset.dataset_version,
            },
        )
        return _model(ProtectedDatasetBinding, value, "INTERNAL_ERROR")


class PostgresqlProtectedAuditJournal(_ProtectedSession):
    def __init__(self, session: AsyncSession, schema: str, clock: PostgresqlTrustedClock) -> None:
        super().__init__(session, schema)
        self._clock = clock

    async def operation_history(self, request: ProtectedOperationRequest) -> tuple[OperationAuditEntry, ...]:
        values = await self._rows(
            "operation_history",
            "CAST(:request_body AS jsonb)",
            {"request_body": _json_value(request)},
        )
        entries = tuple(_model(OperationAuditEntry, value, "AUDIT_UNAVAILABLE") for value in values)
        for entry in entries:
            if audit_entry_sha256(entry) != entry.entry_sha256:
                raise ProtectedSecurityError("AUDIT_HASH_MISMATCH")
        return entries

    async def append_operation(
        self,
        request: ProtectedOperationRequest,
        grant: ProtectedAuthorizationGrant | None,
        outcome: OperationAuditOutcome,
        reason_code: ProtectedAuditReason | str,
        capability: ProtectedAuthorizationCapability | None = None,
        result: ProtectedOperationResult | None = None,
        *,
        closes_intent: bool = False,
    ) -> OperationAuditEntry:
        try:
            safe_reason = ProtectedAuditReason(reason_code)
        except ValueError:
            raise ProtectedSecurityError("INTERNAL_ERROR") from None
        checkpoint = await self._scalar("audit_checkpoint")
        if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("sequence"), int):
            raise ProtectedSecurityError("AUDIT_UNAVAILABLE")
        previous = checkpoint.get("entry_sha256")
        if previous is not None and not isinstance(previous, str):
            raise ProtectedSecurityError("AUDIT_UNAVAILABLE")
        entry = OperationAuditEntry(
            event_kind=ProtectedAuditEventKind.OPERATION,
            sequence=checkpoint["sequence"] + 1,
            event_id=new_event_id(),
            operation_key=request.operation_key,
            request_id=request.request_id,
            principal=request.principal,
            protected_action=request.action,
            target_ref=request.target_ref,
            dataset_id=request.dataset.dataset_id,
            dataset_version=request.dataset.dataset_version,
            manifest_sha256=request.dataset.manifest_sha256,
            hmac_key_version=request.dataset.hmac_key_version,
            grant_id=grant.grant_id if grant else None,
            grant_revision=grant.revision if grant else None,
            dataset_state_revision=request.dataset.state_revision,
            protected_artifact_sha256=request.dataset.protected_artifact_sha256,
            capability_nonce=capability.nonce if capability else None,
            result_ref=result.result_ref if result else None,
            closes_intent=closes_intent,
            outcome=outcome,
            reason_code=safe_reason,
            recorded_at=self._clock.now_utc(),
            previous_entry_sha256=previous,
            entry_sha256="0" * 64,
        )
        entry = entry.model_copy(update={"entry_sha256": audit_entry_sha256(entry)})
        value = await self._scalar(
            "append_operation",
            "CAST(:request_body AS jsonb), CAST(:entry_body AS jsonb)",
            {"request_body": _json_value(request), "entry_body": _json_value(entry)},
        )
        return _model(OperationAuditEntry, value, "AUDIT_UNAVAILABLE")


class _PostgresqlGuardSession(_ProtectedSession):
    def __init__(
        self,
        session: AsyncSession,
        schema: str,
        request: ProtectedOperationRequest,
        grant: ProtectedAuthorizationGrant,
    ) -> None:
        super().__init__(session, schema)
        self._request = request
        self._grant = grant

    async def require_current(self, grant_id: str) -> ProtectedAuthorizationGrant:
        return await PostgresqlAuthorizationLedger(self._session, self._schema.strip('"')).require_current(grant_id)

    async def issue_capability(
        self,
        request: ProtectedOperationRequest,
        grant: ProtectedAuthorizationGrant,
    ) -> ProtectedAuthorizationCapability:
        if request != self._request or grant != self._grant:
            raise ProtectedSecurityError("GUARD_BINDING_MISMATCH")
        value = await self._scalar(
            "issue_capability",
            "CAST(:request_body AS jsonb), CAST(:grant_id AS uuid)",
            {"request_body": _json_value(request), "grant_id": grant.grant_id},
        )
        return _model(ProtectedAuthorizationCapability, value, "INTERNAL_ERROR")

    async def consume(self, capability: ProtectedAuthorizationCapability) -> None:
        await self._scalar(
            "consume_capability",
            "CAST(:nonce AS uuid)",
            {"nonce": capability.nonce},
        )


class _GuardContext(AbstractAsyncContextManager[_PostgresqlGuardSession]):
    def __init__(
        self,
        session: AsyncSession,
        schema: str,
        request: ProtectedOperationRequest,
        grant: ProtectedAuthorizationGrant,
    ) -> None:
        self._session = session
        self._guard_session = _PostgresqlGuardSession(session, schema, request, grant)

    async def __aenter__(self) -> _PostgresqlGuardSession:
        if not self._session.in_transaction():
            raise ProtectedSecurityError("INTERNAL_ERROR")
        await self._guard_session._scalar(
            "lock_operation",
            "CAST(:request_body AS jsonb), CAST(:grant_id AS uuid)",
            {
                "request_body": _json_value(self._guard_session._request),
                "grant_id": self._guard_session._grant.grant_id,
            },
        )
        return self._guard_session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class PostgresqlAuthorizationGuard:
    def __init__(self, session: AsyncSession, schema: str) -> None:
        if _IDENTIFIER.fullmatch(schema) is None:
            raise ValueError("protected schema must be a safe PostgreSQL identifier")
        self._session = session
        self._schema = schema

    def hold(
        self,
        request: ProtectedOperationRequest,
        grant: ProtectedAuthorizationGrant,
    ) -> AbstractAsyncContextManager[_PostgresqlGuardSession]:
        return _GuardContext(self._session, self._schema, request, grant)


class PostgresqlProtectedArtifactOperation(_ProtectedSession):
    def __init__(
        self,
        session: AsyncSession,
        schema: str,
        *,
        write_payload: bytes | None = None,
        on_read: Callable[[bytes], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(session, schema)
        self._write_payload = write_payload
        self._on_read = on_read

    async def execute(
        self,
        request: ProtectedOperationRequest,
        capability: ProtectedAuthorizationCapability,
    ) -> ProtectedOperationResult:
        try:
            if request.action is ProtectedAction.WRITE:
                if self._write_payload is None:
                    raise ProtectedSecurityError("INTERNAL_ERROR")
                await self._scalar(
                    "write_artifact",
                    "CAST(:nonce AS uuid), :payload, :payload_sha256",
                    {
                        "nonce": capability.nonce,
                        "payload": self._write_payload,
                        "payload_sha256": sha256(self._write_payload).hexdigest(),
                    },
                )
            elif request.action in {ProtectedAction.READ, ProtectedAction.RUN}:
                payload = await self._scalar(
                    "read_artifact",
                    "CAST(:nonce AS uuid)",
                    {"nonce": capability.nonce},
                )
                if not isinstance(payload, bytes):
                    raise ProtectedSecurityError("INTERNAL_ERROR")
                if self._on_read is not None:
                    await self._on_read(payload)
            return ProtectedOperationResult(
                result_ref=OpaqueLogicalRef(
                    namespace=OpaqueRefNamespace.RUN_RESULT,
                    value=str(uuid4()),
                ),
                reason_code="PROTECTED_OPERATION_SUCCEEDED",
            )
        except ProtectedSecurityError:
            raise
        except Exception:
            raise ProtectedSecurityError("OPERATION_OUTCOME_UNKNOWN") from None


__all__ = [
    "PostgresqlApprovalEvidenceVerifier",
    "PostgresqlAuthorizationGuard",
    "PostgresqlAuthorizationLedger",
    "PostgresqlProtectedArtifactOperation",
    "PostgresqlProtectedAuditJournal",
    "PostgresqlTrustedClock",
]
