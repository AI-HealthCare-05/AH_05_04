"""SQLAlchemy adapters for the isolated protected-retrieval PostgreSQL boundary."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import TracebackType
from typing import Self
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ai_worker.tasks.evaluation.protected_retrieval import (
    ApprovalSourceEvidence,
    AuthorizationAuditEntry,
    ControlCommandAuditEntry,
    OpaqueLogicalRef,
    OpaqueRefNamespace,
    OperationAuditEntry,
    OperationAuditOutcome,
    PreparedProtectedOperation,
    ProtectedAction,
    ProtectedAuditEntry,
    ProtectedAuditEventKind,
    ProtectedAuditReason,
    ProtectedAuthorizationCapability,
    ProtectedAuthorizationGrant,
    ProtectedDatasetBinding,
    ProtectedOperationRequest,
    ProtectedOperationResult,
    ProtectedPrincipal,
    ProtectedSecurityError,
    audit_entry_sha256,
    new_event_id,
    prepare_protected_operation,
    validate_prepared_operation,
)
from infra.python.protected_retrieval_role_policy import validate_protected_data_connection

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
        self._schema_name = schema
        self._schema = f'"{schema}"'

    async def _execute(
        self,
        statement: str,
        parameters: dict[str, object] | None = None,
        *,
        fallback: str = "INTERNAL_ERROR",
    ):
        try:
            return await self._session.execute(text(statement), parameters or {})
        except (DBAPIError, SQLAlchemyError) as error:
            raise _database_reason(error, fallback) from None

    async def _resolve_principal(self) -> ProtectedPrincipal:
        result = await self._execute(
            f"""
            SELECT actor_id, actor_namespace, principal_role
            FROM {self._schema}.protected_identity
            WHERE database_login = session_user::name AND enabled
            """
        )
        row = result.one_or_none()
        if row is None:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
        return _model(
            ProtectedPrincipal,
            {
                "actor": {"actor_id": row.actor_id, "namespace": row.actor_namespace},
                "role": row.principal_role,
            },
            "INTERNAL_ERROR",
        )

    async def _require_request_principal(self, request: ProtectedOperationRequest) -> ProtectedPrincipal:
        principal = await self._resolve_principal()
        if principal != request.principal:
            raise ProtectedSecurityError("GUARD_BINDING_MISMATCH")
        return principal


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
        await self._resolve_principal()
        result = await self._execute(
            f"""
            SELECT evidence
            FROM {self._schema}.approval_evidence
            WHERE source_event_id = :source_event_id
            """,
            {"source_event_id": source_event_id},
        )
        value = result.scalar_one_or_none()
        if value is None:
            raise ProtectedSecurityError("APPROVAL_NOT_VERIFIED")
        return _model(ApprovalSourceEvidence, value, "APPROVAL_NOT_VERIFIED")


class PostgresqlAuthorizationLedger(_ProtectedSession):
    async def find_for(self, request: ProtectedOperationRequest) -> ProtectedAuthorizationGrant | None:
        await self._require_request_principal(request)
        result = await self._execute(
            f"""
            SELECT grant_body
            FROM {self._schema}.authorization_grant
            WHERE subject_actor_id = :actor_id
              AND subject_namespace = :actor_namespace
              AND subject_role = :subject_role
              AND dataset_id = :dataset_id
              AND dataset_version = :dataset_version
              AND manifest_sha256 = :manifest_sha256
              AND protected_artifact_sha256 = :protected_artifact_sha256
              AND hmac_key_version = :hmac_key_version
              AND :protected_action = ANY(actions)
              AND revoked_at IS NULL
            ORDER BY revision DESC
            LIMIT 1
            """,
            {
                "actor_id": request.principal.actor.actor_id,
                "actor_namespace": request.principal.actor.namespace,
                "subject_role": request.principal.role.value,
                "dataset_id": request.dataset.dataset_id,
                "dataset_version": request.dataset.dataset_version,
                "manifest_sha256": request.dataset.manifest_sha256,
                "protected_artifact_sha256": request.dataset.protected_artifact_sha256,
                "hmac_key_version": request.dataset.hmac_key_version,
                "protected_action": request.action.value,
            },
        )
        value = result.scalar_one_or_none()
        return None if value is None else _model(ProtectedAuthorizationGrant, value, "INTERNAL_ERROR")

    async def require_current(self, grant_id: str) -> ProtectedAuthorizationGrant:
        try:
            UUID(grant_id)
        except ValueError:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND") from None
        await self._resolve_principal()
        result = await self._execute(
            f"""
            SELECT grant_body, revision, effective_revision, revoked_at
            FROM {self._schema}.authorization_grant
            WHERE grant_id = CAST(:grant_id AS uuid)
            """,
            {"grant_id": grant_id},
        )
        row = result.one_or_none()
        if row is None:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
        if row.revoked_at is not None:
            raise ProtectedSecurityError("AUTHORIZATION_REVOKED")
        grant = _model(ProtectedAuthorizationGrant, row.grant_body, "INTERNAL_ERROR")
        if row.revision != grant.revision or row.effective_revision != grant.revision:
            raise ProtectedSecurityError("AUTHORIZATION_REVISION_MISMATCH")
        return grant

    async def require_dataset(self, request: ProtectedOperationRequest) -> ProtectedDatasetBinding:
        await self._resolve_principal()
        result = await self._execute(
            f"""
            SELECT binding
            FROM {self._schema}.protected_dataset
            WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version
            """,
            {
                "dataset_id": request.dataset.dataset_id,
                "dataset_version": request.dataset.dataset_version,
            },
        )
        value = result.scalar_one_or_none()
        if value is None:
            raise ProtectedSecurityError("DATASET_STATE_MISMATCH")
        return _model(ProtectedDatasetBinding, value, "INTERNAL_ERROR")


class PostgresqlProtectedAuditJournal(_ProtectedSession):
    def __init__(self, session: AsyncSession, schema: str, clock: PostgresqlTrustedClock) -> None:
        super().__init__(session, schema)
        self._clock = clock

    async def _verified_entries(self, *, lock_head: bool) -> tuple[ProtectedAuditEntry, ...]:
        lock = " FOR UPDATE" if lock_head else ""
        head_result = await self._execute(
            f"SELECT sequence, entry_sha256 FROM {self._schema}.audit_head WHERE singleton{lock}",
            fallback="AUDIT_UNAVAILABLE",
        )
        head = head_result.one_or_none()
        if head is None:
            raise ProtectedSecurityError("AUDIT_UNAVAILABLE")
        entries_result = await self._execute(
            f"SELECT sequence, entry_body FROM {self._schema}.audit_entry ORDER BY sequence",
            fallback="AUDIT_UNAVAILABLE",
        )
        entries: list[ProtectedAuditEntry] = []
        previous: str | None = None
        for expected_sequence, row in enumerate(entries_result, start=1):
            if row.sequence != expected_sequence or not isinstance(row.entry_body, dict):
                raise ProtectedSecurityError("AUDIT_TAIL_TRUNCATED")
            model_types = {
                ProtectedAuditEventKind.AUTHORIZATION.value: AuthorizationAuditEntry,
                ProtectedAuditEventKind.CONTROL.value: ControlCommandAuditEntry,
                ProtectedAuditEventKind.OPERATION.value: OperationAuditEntry,
            }
            event_kind = row.entry_body.get("event_kind")
            if not isinstance(event_kind, str):
                raise ProtectedSecurityError("AUDIT_UNAVAILABLE")
            model_type = model_types.get(event_kind)
            if model_type is None:
                raise ProtectedSecurityError("AUDIT_UNAVAILABLE")
            entry = _model(model_type, row.entry_body, "AUDIT_UNAVAILABLE")
            if entry.sequence != expected_sequence or entry.previous_entry_sha256 != previous:
                raise ProtectedSecurityError("AUDIT_HASH_MISMATCH")
            if audit_entry_sha256(entry) != entry.entry_sha256:
                raise ProtectedSecurityError("AUDIT_HASH_MISMATCH")
            previous = entry.entry_sha256
            entries.append(entry)
        if head.sequence != len(entries):
            raise ProtectedSecurityError("AUDIT_TAIL_TRUNCATED")
        if head.entry_sha256 != previous:
            raise ProtectedSecurityError("AUDIT_HASH_MISMATCH")
        return tuple(entries)

    async def operation_history(self, request: ProtectedOperationRequest) -> tuple[OperationAuditEntry, ...]:
        await self._resolve_principal()
        entries = await self._verified_entries(lock_head=False)
        history = tuple(
            entry
            for entry in entries
            if isinstance(entry, OperationAuditEntry) and entry.operation_key == request.operation_key
        )
        for entry in history:
            if (
                entry.principal != request.principal
                or entry.protected_action is not request.action
                or entry.target_ref != request.target_ref
                or entry.dataset_id != request.dataset.dataset_id
                or entry.dataset_version != request.dataset.dataset_version
                or entry.manifest_sha256 != request.dataset.manifest_sha256
                or entry.hmac_key_version != request.dataset.hmac_key_version
                or entry.dataset_state_revision != request.dataset.state_revision
                or entry.protected_artifact_sha256 != request.dataset.protected_artifact_sha256
            ):
                raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        return history

    @staticmethod
    def _validate_operation_transition(
        history: tuple[OperationAuditEntry, ...],
        outcome: OperationAuditOutcome,
        *,
        closes_intent: bool,
        result: ProtectedOperationResult | None,
    ) -> None:
        lifecycle = [
            entry for entry in history if entry.outcome is not OperationAuditOutcome.DENIED or entry.closes_intent
        ]
        terminal = lifecycle[-1] if lifecycle else None
        if (outcome is OperationAuditOutcome.SUCCEEDED) != (result is not None):
            raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        if outcome is OperationAuditOutcome.DENIED:
            valid = not closes_intent or (terminal is not None and terminal.outcome is OperationAuditOutcome.INTENT)
        elif terminal is None or terminal.outcome is OperationAuditOutcome.DENIED:
            valid = outcome is OperationAuditOutcome.INTENT
        else:
            valid = terminal.outcome is OperationAuditOutcome.INTENT and outcome in {
                OperationAuditOutcome.SUCCEEDED,
                OperationAuditOutcome.UNKNOWN,
            }
        if not valid:
            raise ProtectedSecurityError("AUDIT_TRANSITION_INVALID")

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
        authenticated = await self._resolve_principal()
        audited_request = request.model_copy(update={"principal": authenticated})
        entries = await self._verified_entries(lock_head=True)
        history = tuple(
            entry
            for entry in entries
            if isinstance(entry, OperationAuditEntry) and entry.operation_key == audited_request.operation_key
        )
        self._validate_operation_transition(history, outcome, closes_intent=closes_intent, result=result)
        lifecycle = [item for item in history if item.outcome is not OperationAuditOutcome.DENIED or item.closes_intent]
        terminal = lifecycle[-1] if lifecycle else None
        if (
            terminal is not None
            and terminal.outcome is OperationAuditOutcome.INTENT
            and outcome is not OperationAuditOutcome.INTENT
        ):
            expected_binding = (
                audited_request.request_id,
                audited_request.principal,
                audited_request.action,
                audited_request.target_ref,
                audited_request.dataset.dataset_id,
                audited_request.dataset.dataset_version,
                audited_request.dataset.manifest_sha256,
                audited_request.dataset.hmac_key_version,
                grant.grant_id if grant else None,
                grant.revision if grant else None,
                audited_request.dataset.state_revision,
                audited_request.dataset.protected_artifact_sha256,
            )
            actual_binding = (
                terminal.request_id,
                terminal.principal,
                terminal.protected_action,
                terminal.target_ref,
                terminal.dataset_id,
                terminal.dataset_version,
                terminal.manifest_sha256,
                terminal.hmac_key_version,
                terminal.grant_id,
                terminal.grant_revision,
                terminal.dataset_state_revision,
                terminal.protected_artifact_sha256,
            )
            if actual_binding != expected_binding:
                raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
        previous = entries[-1].entry_sha256 if entries else None
        entry = OperationAuditEntry(
            event_kind=ProtectedAuditEventKind.OPERATION,
            sequence=len(entries) + 1,
            event_id=new_event_id(),
            operation_key=audited_request.operation_key,
            request_id=audited_request.request_id,
            principal=audited_request.principal,
            protected_action=audited_request.action,
            target_ref=audited_request.target_ref,
            dataset_id=audited_request.dataset.dataset_id,
            dataset_version=audited_request.dataset.dataset_version,
            manifest_sha256=audited_request.dataset.manifest_sha256,
            hmac_key_version=audited_request.dataset.hmac_key_version,
            grant_id=grant.grant_id if grant else None,
            grant_revision=grant.revision if grant else None,
            dataset_state_revision=audited_request.dataset.state_revision,
            protected_artifact_sha256=audited_request.dataset.protected_artifact_sha256,
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
        await self._execute(
            f"""
            INSERT INTO {self._schema}.audit_entry (
                sequence, event_id, event_kind, operation_key, entry_body,
                previous_entry_sha256, entry_sha256, recorded_at
            ) VALUES (
                :sequence, CAST(:event_id AS uuid), 'OPERATION', :operation_key,
                CAST(:entry_body AS jsonb), :previous_entry_sha256, :entry_sha256, :recorded_at
            )
            """,
            {
                "sequence": entry.sequence,
                "event_id": entry.event_id,
                "operation_key": entry.operation_key,
                "entry_body": _json_value(entry),
                "previous_entry_sha256": entry.previous_entry_sha256,
                "entry_sha256": entry.entry_sha256,
                "recorded_at": entry.recorded_at,
            },
            fallback="AUDIT_CAS_CONFLICT",
        )
        updated = await self._execute(
            f"""
            UPDATE {self._schema}.audit_head
            SET sequence = :sequence, entry_sha256 = :entry_sha256
            WHERE singleton AND sequence = :previous_sequence
              AND entry_sha256 IS NOT DISTINCT FROM :previous_entry_sha256
            RETURNING sequence
            """,
            {
                "sequence": entry.sequence,
                "entry_sha256": entry.entry_sha256,
                "previous_sequence": entry.sequence - 1,
                "previous_entry_sha256": entry.previous_entry_sha256,
            },
            fallback="AUDIT_CAS_CONFLICT",
        )
        if updated.scalar_one_or_none() != entry.sequence:
            raise ProtectedSecurityError("AUDIT_CAS_CONFLICT")
        return entry


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
        return await PostgresqlAuthorizationLedger(self._session, self._schema_name).require_current(grant_id)

    async def issue_capability(
        self,
        request: ProtectedOperationRequest,
        grant: ProtectedAuthorizationGrant,
    ) -> ProtectedAuthorizationCapability:
        if request != self._request or grant != self._grant:
            raise ProtectedSecurityError("GUARD_BINDING_MISMATCH")
        await self._require_request_principal(request)
        nonce = str(uuid4())
        now_result = await self._execute("SELECT clock_timestamp()")
        trusted_now = now_result.scalar_one_or_none()
        if not isinstance(trusted_now, datetime):
            raise ProtectedSecurityError("INTERNAL_ERROR")
        if not grant.valid_from <= trusted_now.astimezone(UTC) < grant.expires_at:
            raise ProtectedSecurityError("AUTHORIZATION_EXPIRED")
        expires_at = min(grant.expires_at, trusted_now.astimezone(UTC) + timedelta(minutes=5))
        capability = ProtectedAuthorizationCapability(
            request_id=request.request_id,
            grant_id=grant.grant_id,
            grant_revision=grant.revision,
            dataset_state_revision=request.dataset.state_revision,
            protected_artifact_sha256=request.dataset.protected_artifact_sha256,
            action=request.action,
            target_ref=request.target_ref,
            nonce=nonce,
            expires_at=expires_at,
        )
        await self._execute(
            f"""
            INSERT INTO {self._schema}.operation_capability (
                nonce, request_id, operation_key, grant_id, grant_revision,
                dataset_id, dataset_version, dataset_state_revision,
                protected_artifact_sha256, protected_action, target_ref, expires_at
            ) VALUES (
                CAST(:nonce AS uuid), CAST(:request_id AS uuid), :operation_key,
                CAST(:grant_id AS uuid), :grant_revision, :dataset_id, :dataset_version,
                :dataset_state_revision, :protected_artifact_sha256,
                :protected_action, CAST(:target_ref AS uuid), :expires_at
            )
            """,
            {
                "nonce": capability.nonce,
                "request_id": capability.request_id,
                "operation_key": request.operation_key,
                "grant_id": capability.grant_id,
                "grant_revision": capability.grant_revision,
                "dataset_id": request.dataset.dataset_id,
                "dataset_version": request.dataset.dataset_version,
                "dataset_state_revision": capability.dataset_state_revision,
                "protected_artifact_sha256": capability.protected_artifact_sha256,
                "protected_action": capability.action.value,
                "target_ref": capability.target_ref.value,
                "expires_at": capability.expires_at,
            },
        )
        return capability

    async def consume(self, capability: ProtectedAuthorizationCapability) -> None:
        result = await self._execute(
            f"""
            UPDATE {self._schema}.operation_capability
            SET consumed_at = clock_timestamp()
            WHERE nonce = CAST(:nonce AS uuid)
              AND request_id = CAST(:request_id AS uuid)
              AND grant_id = CAST(:grant_id AS uuid)
              AND grant_revision = :grant_revision
              AND dataset_state_revision = :dataset_state_revision
              AND protected_artifact_sha256 = :protected_artifact_sha256
              AND protected_action = :protected_action
              AND target_ref = CAST(:target_ref AS uuid)
              AND expires_at = :expires_at
              AND consumed_at IS NULL
              AND expires_at > clock_timestamp()
            RETURNING nonce
            """,
            {
                "nonce": capability.nonce,
                "request_id": capability.request_id,
                "grant_id": capability.grant_id,
                "grant_revision": capability.grant_revision,
                "dataset_state_revision": capability.dataset_state_revision,
                "protected_artifact_sha256": capability.protected_artifact_sha256,
                "protected_action": capability.action.value,
                "target_ref": capability.target_ref.value,
                "expires_at": capability.expires_at,
            },
        )
        if result.scalar_one_or_none() is None:
            existing = await self._execute(
                f"SELECT consumed_at FROM {self._schema}.operation_capability WHERE nonce = CAST(:nonce AS uuid)",
                {"nonce": capability.nonce},
            )
            row = existing.one_or_none()
            reason = (
                "CAPABILITY_ALREADY_CONSUMED"
                if row is not None and row.consumed_at is not None
                else "CAPABILITY_BINDING_MISMATCH"
            )
            raise ProtectedSecurityError(reason)


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
        await self._guard_session._require_request_principal(self._guard_session._request)
        dataset_result = await self._guard_session._execute(
            f"""
            SELECT binding
            FROM {self._guard_session._schema}.protected_dataset
            WHERE dataset_id = :dataset_id AND dataset_version = :dataset_version
            FOR UPDATE
            """,
            {
                "dataset_id": self._guard_session._request.dataset.dataset_id,
                "dataset_version": self._guard_session._request.dataset.dataset_version,
            },
        )
        dataset_value = dataset_result.scalar_one_or_none()
        grant_result = await self._guard_session._execute(
            f"""
            SELECT grant_body, revision, effective_revision, revoked_at
            FROM {self._guard_session._schema}.authorization_grant
            WHERE grant_id = CAST(:grant_id AS uuid)
            FOR UPDATE
            """,
            {"grant_id": self._guard_session._grant.grant_id},
        )
        grant_row = grant_result.one_or_none()
        if dataset_value is None or grant_row is None:
            raise ProtectedSecurityError("AUTHORIZATION_NOT_FOUND")
        locked_dataset = _model(ProtectedDatasetBinding, dataset_value, "INTERNAL_ERROR")
        locked_grant = _model(ProtectedAuthorizationGrant, grant_row.grant_body, "INTERNAL_ERROR")
        if locked_dataset != self._guard_session._request.dataset or locked_grant != self._guard_session._grant:
            raise ProtectedSecurityError("GUARD_BINDING_MISMATCH")
        if grant_row.revoked_at is not None:
            raise ProtectedSecurityError("AUTHORIZATION_REVOKED")
        if grant_row.revision != grant_row.effective_revision:
            raise ProtectedSecurityError("AUTHORIZATION_REVISION_MISMATCH")
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

    async def _claim_operation(
        self,
        request: ProtectedOperationRequest,
        capability: ProtectedAuthorizationCapability,
        actions: tuple[ProtectedAction, ...],
    ):
        if (
            capability.request_id != request.request_id
            or capability.action is not request.action
            or capability.target_ref != request.target_ref
            or capability.dataset_state_revision != request.dataset.state_revision
            or capability.protected_artifact_sha256 != request.dataset.protected_artifact_sha256
        ):
            raise ProtectedSecurityError("CAPABILITY_BINDING_MISMATCH")
        result = await self._execute(
            f"""
            UPDATE {self._schema}.operation_capability
            SET operated_at = clock_timestamp()
            WHERE nonce = CAST(:nonce AS uuid)
              AND request_id = CAST(:request_id AS uuid)
              AND grant_id = CAST(:grant_id AS uuid)
              AND grant_revision = :grant_revision
              AND dataset_state_revision = :dataset_state_revision
              AND protected_artifact_sha256 = :protected_artifact_sha256
              AND protected_action = :protected_action
              AND target_ref = CAST(:target_ref AS uuid)
              AND expires_at = :expires_at
              AND consumed_at IS NOT NULL
              AND operated_at IS NULL
              AND expires_at > clock_timestamp()
              AND protected_action = ANY(:actions)
            RETURNING target_ref, dataset_id, dataset_version, protected_artifact_sha256
            """,
            {
                "nonce": capability.nonce,
                "request_id": capability.request_id,
                "grant_id": capability.grant_id,
                "grant_revision": capability.grant_revision,
                "dataset_state_revision": capability.dataset_state_revision,
                "protected_artifact_sha256": capability.protected_artifact_sha256,
                "protected_action": capability.action.value,
                "target_ref": capability.target_ref.value,
                "expires_at": capability.expires_at,
                "actions": [action.value for action in actions],
            },
        )
        row = result.one_or_none()
        if row is None:
            raise ProtectedSecurityError("CAPABILITY_BINDING_MISMATCH")
        if (
            str(row.target_ref) != capability.target_ref.value
            or row.protected_artifact_sha256 != capability.protected_artifact_sha256
        ):
            raise ProtectedSecurityError("CAPABILITY_BINDING_MISMATCH")
        return row

    async def execute(
        self,
        request: ProtectedOperationRequest,
        capability: ProtectedAuthorizationCapability,
    ) -> ProtectedOperationResult:
        try:
            if request.action is ProtectedAction.WRITE:
                await self._write(request, capability)
            elif request.action in {ProtectedAction.READ, ProtectedAction.RUN}:
                await self._read(request, capability)
            else:
                raise ProtectedSecurityError("ROLE_ACTION_STATE_DENIED")
            return ProtectedOperationResult(
                result_ref=OpaqueLogicalRef(namespace=OpaqueRefNamespace.RUN_RESULT, value=str(uuid4())),
                reason_code="PROTECTED_OPERATION_SUCCEEDED",
            )
        except ProtectedSecurityError:
            raise
        except Exception:
            raise ProtectedSecurityError("OPERATION_OUTCOME_UNKNOWN") from None

    async def _write(self, request: ProtectedOperationRequest, capability: ProtectedAuthorizationCapability) -> None:
        if self._write_payload is None:
            raise ProtectedSecurityError("INTERNAL_ERROR")
        payload_sha256 = sha256(self._write_payload).hexdigest()
        if payload_sha256 != capability.protected_artifact_sha256:
            raise ProtectedSecurityError("CAPABILITY_BINDING_MISMATCH")
        row = await self._claim_operation(request, capability, (ProtectedAction.WRITE,))
        existing = await self._execute(
            f"""
            SELECT dataset_id, dataset_version, hmac_key_version
            FROM {self._schema}.protected_artifact
            WHERE target_ref = :target_ref
            FOR UPDATE
            """,
            {"target_ref": row.target_ref},
        )
        existing_row = existing.one_or_none()
        if existing_row is None:
            await self._execute(
                f"""
                INSERT INTO {self._schema}.protected_artifact (
                    target_ref, dataset_id, dataset_version, envelope, envelope_sha256, hmac_key_version
                ) VALUES (
                    :target_ref, :dataset_id, :dataset_version, :envelope, :envelope_sha256, :hmac_key_version
                )
                """,
                {
                    "target_ref": row.target_ref,
                    "dataset_id": row.dataset_id,
                    "dataset_version": row.dataset_version,
                    "envelope": self._write_payload,
                    "envelope_sha256": payload_sha256,
                    "hmac_key_version": request.dataset.hmac_key_version,
                },
            )
            return
        if (
            existing_row.dataset_id != row.dataset_id
            or existing_row.dataset_version != row.dataset_version
            or existing_row.hmac_key_version != request.dataset.hmac_key_version
        ):
            raise ProtectedSecurityError("CAPABILITY_BINDING_MISMATCH")
        await self._execute(
            f"""
            UPDATE {self._schema}.protected_artifact
            SET envelope = :envelope, envelope_sha256 = :envelope_sha256
            WHERE target_ref = :target_ref
            """,
            {"target_ref": row.target_ref, "envelope": self._write_payload, "envelope_sha256": payload_sha256},
        )

    async def _read(self, request: ProtectedOperationRequest, capability: ProtectedAuthorizationCapability) -> None:
        row = await self._claim_operation(request, capability, (ProtectedAction.READ, ProtectedAction.RUN))
        result = await self._execute(
            f"""
            SELECT envelope, envelope_sha256, hmac_key_version
            FROM {self._schema}.protected_artifact
            WHERE target_ref = :target_ref
              AND dataset_id = :dataset_id
              AND dataset_version = :dataset_version
            """,
            {
                "target_ref": row.target_ref,
                "dataset_id": row.dataset_id,
                "dataset_version": row.dataset_version,
            },
        )
        artifact = result.one_or_none()
        if (
            artifact is None
            or artifact.hmac_key_version != request.dataset.hmac_key_version
            or sha256(artifact.envelope).hexdigest() != artifact.envelope_sha256
            or artifact.envelope_sha256 != capability.protected_artifact_sha256
        ):
            raise ProtectedSecurityError("CAPABILITY_BINDING_MISMATCH")
        if self._on_read is not None:
            await self._on_read(artifact.envelope)


class PostgresqlProtectedRetrievalService:
    """Owns the durable transaction phases for one protected data-plane identity."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        schema: str,
        data_access_role: str,
        control_role: str,
    ) -> None:
        for identifier in (schema, data_access_role, control_role):
            if _IDENTIFIER.fullmatch(identifier) is None:
                raise ValueError("protected runtime identifiers must be safe")
        self._engine = engine
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        self._schema = schema
        self._data_access_role = data_access_role
        self._control_role = control_role

    async def validate(self) -> None:
        async with self._engine.connect() as connection:
            await validate_protected_data_connection(
                connection,
                schema=self._schema,
                data_access=self._data_access_role,
                control=self._control_role,
            )

    async def close(self) -> None:
        await self._engine.dispose()

    async def _validate_session(self, session: AsyncSession) -> None:
        await validate_protected_data_connection(
            await session.connection(),
            schema=self._schema,
            data_access=self._data_access_role,
            control=self._control_role,
        )

    async def _prepare(
        self,
        request: ProtectedOperationRequest,
    ) -> PreparedProtectedOperation | ProtectedOperationResult:
        async with self._session_factory() as session:
            transaction = await session.begin()
            try:
                await self._validate_session(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                prepared = await prepare_protected_operation(
                    request,
                    ledger=PostgresqlAuthorizationLedger(session, self._schema),
                    guard=PostgresqlAuthorizationGuard(session, self._schema),
                    journal=PostgresqlProtectedAuditJournal(session, self._schema, clock),
                    clock=clock,
                )
            except ProtectedSecurityError:
                await transaction.commit()
                raise
            except Exception:
                await transaction.rollback()
                raise ProtectedSecurityError("INTERNAL_ERROR") from None
            await transaction.commit()
            return prepared

    async def _record_unknown_or_recover(
        self,
        prepared: PreparedProtectedOperation,
        result: ProtectedOperationResult | None,
    ) -> ProtectedOperationResult | None:
        try:
            async with self._session_factory() as session, session.begin():
                await self._validate_session(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                journal = PostgresqlProtectedAuditJournal(session, self._schema, clock)
                history = await journal.operation_history(prepared.request)
                terminal = history[-1] if history else None
                if terminal is not None and terminal.outcome is OperationAuditOutcome.SUCCEEDED:
                    if result is None or terminal.result_ref != result.result_ref:
                        raise ProtectedSecurityError("AUDIT_BINDING_MISMATCH")
                    return result
                if terminal is not None and terminal.outcome is OperationAuditOutcome.UNKNOWN:
                    return None
                await journal.append_operation(
                    prepared.request,
                    prepared.grant,
                    OperationAuditOutcome.UNKNOWN,
                    ProtectedAuditReason.OPERATION_OUTCOME_UNKNOWN,
                    prepared.capability,
                )
        except Exception:
            return None
        return None

    async def execute(
        self,
        request: ProtectedOperationRequest,
        *,
        write_payload: bytes | None = None,
        on_read: Callable[[bytes], Awaitable[None]] | None = None,
    ) -> ProtectedOperationResult:
        prepared = await self._prepare(request)
        if isinstance(prepared, ProtectedOperationResult):
            return prepared

        result: ProtectedOperationResult | None = None
        try:
            async with self._session_factory() as session, session.begin():
                await self._validate_session(session)
                clock = await PostgresqlTrustedClock.from_session(session)
                ledger = PostgresqlAuthorizationLedger(session, self._schema)
                current_dataset = await ledger.require_dataset(prepared.request)
                current_grant = await ledger.require_current(prepared.grant.grant_id)
                validate_prepared_operation(prepared, current_dataset, current_grant, clock.now_utc())
                async with PostgresqlAuthorizationGuard(session, self._schema).hold(
                    prepared.request,
                    current_grant,
                ):
                    result = await PostgresqlProtectedArtifactOperation(
                        session,
                        self._schema,
                        write_payload=write_payload,
                        on_read=on_read,
                    ).execute(prepared.request, prepared.capability)
                    await PostgresqlProtectedAuditJournal(session, self._schema, clock).append_operation(
                        prepared.request,
                        prepared.grant,
                        OperationAuditOutcome.SUCCEEDED,
                        ProtectedAuditReason.COMPLETED,
                        prepared.capability,
                        result,
                    )
            assert result is not None
            return result
        except Exception:
            recovered = await self._record_unknown_or_recover(prepared, result)
            if recovered is not None:
                return recovered
            raise ProtectedSecurityError("OPERATION_OUTCOME_UNKNOWN") from None


__all__ = [
    "PostgresqlApprovalEvidenceVerifier",
    "PostgresqlAuthorizationGuard",
    "PostgresqlAuthorizationLedger",
    "PostgresqlProtectedArtifactOperation",
    "PostgresqlProtectedAuditJournal",
    "PostgresqlProtectedRetrievalService",
    "PostgresqlTrustedClock",
]
