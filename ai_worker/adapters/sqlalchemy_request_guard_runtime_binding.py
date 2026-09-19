"""Read-only SQLAlchemy adapter for #806 runtime binding authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, String, column, select, table
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag_runtime.request_authority import RequestAuthorityArtifactRef
from rag_runtime.request_guard_runtime_binding import (
    REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_CODE,
    REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_VERSION,
    RequestGuardRuntimeBindingObservation,
    RequestGuardRuntimeBindingRef,
    RequestGuardRuntimeBindingValidationError,
    compute_request_guard_runtime_binding_ref,
)

_AUTHORITY = table(
    "rag_request_guard_runtime_binding",
    column("request_guard_decision_id", String(36)),
    column("artifact_code", String(100)),
    column("artifact_version", String(50)),
    column("artifact_content_sha256", String(64)),
    column("actual_decision_outcome", String(10)),
    column("user_id", String(36)),
    column("request_operation_code", String(100)),
    column("decision_stage", String(20)),
    column("environment_code", String(50)),
    column("bundle_id", String(36)),
    column("bundle_manifest_hash", String(64)),
    column("request_scope_codes", JSON),
    column("scope_manifest_hash", String(64)),
    column("legacy_request_guard_artifact_code", String(100)),
    column("legacy_request_guard_artifact_version", String(50)),
    column("legacy_request_guard_content_sha256", String(64)),
)


class RequestGuardRuntimeBindingReadError(RuntimeError):
    """The authority store cannot be trusted for this exact read."""


class SqlAlchemyRequestGuardRuntimeBindingReader:
    """Worker-side exact read-only seam; it has no latest/current fallback."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def read_exact(
        self,
        reference: RequestGuardRuntimeBindingRef,
    ) -> RequestGuardRuntimeBindingObservation | None:
        _validate_reference(reference)
        try:
            async with self._session_factory() as session:
                row = (
                    (
                        await session.execute(
                            select(*_AUTHORITY.c).where(
                                _AUTHORITY.c.artifact_code == reference.artifact_code,
                                _AUTHORITY.c.artifact_version == reference.version,
                                _AUTHORITY.c.artifact_content_sha256 == reference.content_sha256,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as error:
            raise RequestGuardRuntimeBindingReadError("runtime binding exact read failed") from error
        if row is None:
            return None
        try:
            observation = _observation_from_row(row)
            recomputed = compute_request_guard_runtime_binding_ref(observation)
            if recomputed != reference:
                raise RequestGuardRuntimeBindingReadError("persisted runtime binding artifact identity mismatch")
            return observation
        except (KeyError, TypeError, ValueError, RequestGuardRuntimeBindingValidationError) as error:
            raise RequestGuardRuntimeBindingReadError("persisted runtime binding is corrupt") from error


def _validate_reference(reference: RequestGuardRuntimeBindingRef) -> None:
    if type(reference) is not RequestGuardRuntimeBindingRef:
        raise RequestGuardRuntimeBindingValidationError("reference 형식이 아닙니다")
    if (
        reference.artifact_code != REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_CODE
        or reference.version != REQUEST_GUARD_RUNTIME_BINDING_ARTIFACT_VERSION
    ):
        raise RequestGuardRuntimeBindingValidationError("지원하지 않는 runtime binding reference입니다")
    if len(reference.content_sha256) != 64 or any(char not in "0123456789abcdef" for char in reference.content_sha256):
        raise RequestGuardRuntimeBindingValidationError("runtime binding reference hash가 올바르지 않습니다")


def _observation_from_row(row: Mapping[Any, Any]) -> RequestGuardRuntimeBindingObservation:
    from rag_runtime.request_authority import RequestAuthorityDecisionOutcome, RequestAuthorityDecisionStage
    from rag_runtime.runtime_environment import RuntimeEnvironmentCode

    return RequestGuardRuntimeBindingObservation(
        request_guard_decision_id=UUID(str(row["request_guard_decision_id"])),
        actual_decision_outcome=RequestAuthorityDecisionOutcome(str(row["actual_decision_outcome"])),
        user_id=UUID(str(row["user_id"])),
        request_operation_code=str(row["request_operation_code"]),
        decision_stage=RequestAuthorityDecisionStage(str(row["decision_stage"])),
        environment=RuntimeEnvironmentCode(str(row["environment_code"])),
        bundle_id=UUID(str(row["bundle_id"])),
        bundle_manifest_hash=str(row["bundle_manifest_hash"]),
        request_scope_codes=tuple(row["request_scope_codes"]),
        scope_manifest_hash=str(row["scope_manifest_hash"]),
        legacy_request_authority_ref=RequestAuthorityArtifactRef(
            artifact_code=str(row["legacy_request_guard_artifact_code"]),
            version=str(row["legacy_request_guard_artifact_version"]),
            content_sha256=str(row["legacy_request_guard_content_sha256"]),
        ),
    )


__all__ = [
    "RequestGuardRuntimeBindingReadError",
    "SqlAlchemyRequestGuardRuntimeBindingReader",
]
