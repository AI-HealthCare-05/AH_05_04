"""Read-only exact Runtime Bundle PATIENT_CITATION approval pins (#853)."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import String, and_, column, select, table
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.runtime_bundle_builder import RuntimeBundleCitationApprovalPinIdentity
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import SourceUsePurpose

SessionFactory = Callable[[], AsyncSession]

_BUNDLE = table(
    "rag_runtime_release_bundle",
    column("id", String(36)),
    column("bundle_manifest_hash", String(64)),
)
_PIN = table(
    "rag_runtime_bundle_citation_approval",
    column("bundle_id", String(36)),
    column("bundle_manifest_hash", String(64)),
    column("source_snapshot_id", String(36)),
    column("source_use_approval_id", String(36)),
    column("source_code", String(100)),
    column("source_version", String(200)),
    column("approval_version", String(120)),
    column("environment", String(20)),
    column("purpose", String(40)),
)


class RuntimeBundleCitationApprovalReadError(RuntimeError):
    """The exact bundle pin set cannot be trusted."""


class SqlAlchemyRuntimeBundleCitationApprovalReader:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def read_exact(
        self, *, bundle_id: UUID, bundle_manifest_hash: str
    ) -> tuple[RuntimeBundleCitationApprovalPinIdentity, ...]:
        if type(bundle_id) is not UUID or len(bundle_manifest_hash) != 64:
            raise RuntimeBundleCitationApprovalReadError("exact bundle pair is invalid")
        statement = (
            select(
                _PIN.c.source_snapshot_id,
                _PIN.c.source_use_approval_id,
                _PIN.c.source_code,
                _PIN.c.source_version,
                _PIN.c.approval_version,
                _PIN.c.environment,
                _PIN.c.purpose,
            )
            .select_from(
                _PIN.join(
                    _BUNDLE,
                    and_(
                        _PIN.c.bundle_id == _BUNDLE.c.id,
                        _PIN.c.bundle_manifest_hash == _BUNDLE.c.bundle_manifest_hash,
                    ),
                )
            )
            .where(
                _PIN.c.bundle_id == str(bundle_id),
                _PIN.c.bundle_manifest_hash == bundle_manifest_hash,
            )
        )
        try:
            async with self._session_factory() as session:
                rows = list((await session.execute(statement)).mappings().all())
            pins = tuple(self._pin(row) for row in rows)
        except (SQLAlchemyError, KeyError, TypeError, ValueError) as error:
            raise RuntimeBundleCitationApprovalReadError("Runtime Bundle citation pin read failed") from error
        if len({pin.source_snapshot_id for pin in pins}) != len(pins):
            raise RuntimeBundleCitationApprovalReadError("Runtime Bundle citation pin set is ambiguous")
        return tuple(sorted(pins, key=lambda pin: (pin.source_code, pin.source_version, pin.source_snapshot_id)))

    @staticmethod
    def _pin(row: object) -> RuntimeBundleCitationApprovalPinIdentity:
        mapping = row  # RowMapping/dict share string-key access.
        purpose = SourceUsePurpose(str(mapping["purpose"]))  # type: ignore[index]
        if purpose is not SourceUsePurpose.PATIENT_CITATION:
            raise ValueError("non-PATIENT_CITATION pin")
        texts = {
            field: str(mapping[field])  # type: ignore[index]
            for field in ("source_code", "source_version", "approval_version", "environment")
        }
        if any(not value or value != value.strip() or value == "None" for value in texts.values()):
            raise ValueError("noncanonical citation pin text")
        RuntimeEnvironmentCode(texts["environment"])
        return RuntimeBundleCitationApprovalPinIdentity(
            source_snapshot_id=str(UUID(str(mapping["source_snapshot_id"]))),  # type: ignore[index]
            source_use_approval_id=str(UUID(str(mapping["source_use_approval_id"]))),  # type: ignore[index]
            source_code=texts["source_code"],
            source_version=texts["source_version"],
            approval_version=texts["approval_version"],
            environment=texts["environment"],
            purpose=purpose,
        )


__all__ = ["RuntimeBundleCitationApprovalReadError", "SqlAlchemyRuntimeBundleCitationApprovalReader"]
