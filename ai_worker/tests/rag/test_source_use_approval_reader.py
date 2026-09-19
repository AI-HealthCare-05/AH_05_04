from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_source_use_approval import (
    SourceUseApprovalReadError,
    SqlAlchemySourceUseApprovalReader,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode
from rag_runtime.source_use_approval import (
    SourceUseApprovalIdentity,
    SourceUseApprovalReaderPort,
    SourceUsePurpose,
)


def _identity() -> SourceUseApprovalIdentity:
    return SourceUseApprovalIdentity(
        source_snapshot_id=uuid4(),
        source_code="MFDS_PRODUCT_LABEL",
        source_version="2026-09-18",
        environment=RuntimeEnvironmentCode.PRODUCTION,
        purpose=SourceUsePurpose.PATIENT_CITATION,
        approval_version="approval-1",
    )


def _row(identity: SourceUseApprovalIdentity, *, revoked: bool = False) -> dict[str, object]:
    return {
        "id": str(uuid4()),
        "source_snapshot_id": str(identity.source_snapshot_id),
        "source_code": identity.source_code,
        "source_version": identity.source_version,
        "environment": identity.environment.value,
        "purpose": identity.purpose.value,
        "approval_version": identity.approval_version,
        "valid_from": datetime(2026, 9, 19, tzinfo=UTC),
        "expires_at": datetime(2026, 9, 20, tzinfo=UTC),
        "revoked_at": datetime(2026, 9, 19, 12, tzinfo=UTC) if revoked else None,
        "revoked_by": str(uuid4()) if revoked else None,
        "revoked_reason": "policy change" if revoked else None,
        "actor_id": str(uuid4()),
        "evidence_ref": "evidence://approval-1",
    }


def _reader_with_rows(rows: list[dict[str, object]]) -> tuple[SqlAlchemySourceUseApprovalReader, AsyncMock, list[str]]:
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.begin.return_value.__aenter__.return_value = None
    executed: list[str] = []

    async def _execute(statement, *args, **kwargs):
        del args, kwargs
        executed.append(str(statement))
        result = MagicMock()
        result.mappings.return_value.all.return_value = rows
        return result

    session.execute.side_effect = _execute
    return SqlAlchemySourceUseApprovalReader(lambda: session), session, executed


def _unused_session_factory() -> AsyncSession:
    raise AssertionError("the structural port test must not open a session")


def test_reader_requires_exact_approval_version_and_has_no_latest_selector() -> None:
    public_methods = {name for name in dir(SqlAlchemySourceUseApprovalReader) if not name.startswith("_")}

    assert {"read_exact", "read_usable_exact"} <= public_methods
    assert "read_latest" not in public_methods
    assert "read_current" not in public_methods
    assert _identity().approval_version == "approval-1"


def test_sqlalchemy_reader_implements_the_shared_pure_read_port() -> None:
    reader = SqlAlchemySourceUseApprovalReader(_unused_session_factory)

    assert isinstance(reader, SourceUseApprovalReaderPort)


def test_worker_consumer_can_evaluate_expiry_without_reconstructing_currentness() -> None:
    identity = _identity()
    assert identity.environment is RuntimeEnvironmentCode.PRODUCTION
    assert identity.purpose is SourceUsePurpose.PATIENT_CITATION
    assert datetime(2026, 9, 19, tzinfo=UTC) < datetime(2026, 9, 20, tzinfo=UTC)


@pytest.mark.parametrize("approval_version", ["", " ", " approval-1"])
def test_exact_identity_rejects_missing_or_noncanonical_approval_version(approval_version: str) -> None:
    with pytest.raises(ValueError):
        SourceUseApprovalIdentity(
            source_snapshot_id=uuid4(),
            source_code="MFDS_PRODUCT_LABEL",
            source_version="2026-09-18",
            environment=RuntimeEnvironmentCode.PRODUCTION,
            purpose=SourceUsePurpose.PATIENT_CITATION,
            approval_version=approval_version,
        )


@pytest.mark.asyncio
async def test_exact_reader_uses_read_only_repeatable_read_and_all_six_identity_fields() -> None:
    identity = _identity()
    reader, _, executed = _reader_with_rows([_row(identity)])

    observation = await reader.read_exact(identity)

    assert observation is not None
    assert observation.identity == identity
    assert executed[0] == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    for field in ("source_snapshot_id", "source_code", "source_version", "environment", "purpose", "approval_version"):
        assert field in executed[1]


@pytest.mark.asyncio
async def test_exact_reader_returns_no_usable_observation_for_expiry_or_revocation() -> None:
    identity = _identity()
    expired_reader, _, _ = _reader_with_rows([_row(identity)])
    revoked_reader, _, _ = _reader_with_rows([_row(identity, revoked=True)])

    assert await expired_reader.read_usable_exact(identity, evaluation_time=datetime(2026, 9, 20, tzinfo=UTC)) is None
    assert (
        await revoked_reader.read_usable_exact(identity, evaluation_time=datetime(2026, 9, 19, 13, tzinfo=UTC)) is None
    )


@pytest.mark.asyncio
async def test_exact_reader_fails_closed_on_sqlalchemy_errors() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.begin.return_value.__aenter__.return_value = None
    session.execute.side_effect = SQLAlchemyError("database unavailable")
    reader = SqlAlchemySourceUseApprovalReader(lambda: session)

    with pytest.raises(SourceUseApprovalReadError):
        await reader.read_exact(_identity())
