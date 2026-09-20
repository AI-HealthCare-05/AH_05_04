from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_runtime_bundle_citation_approval import (
    RuntimeBundleCitationApprovalReadError,
    SqlAlchemyRuntimeBundleCitationApprovalReader,
)
from rag_runtime.source_use_approval import SourceUsePurpose


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "source_snapshot_id": str(uuid4()),
        "source_use_approval_id": str(uuid4()),
        "source_code": "MFDS_PATIENT_CITATION",
        "source_version": "2026-09-20",
        "approval_version": "approval-1",
        "environment": "LOCAL",
        "purpose": "PATIENT_CITATION",
    }
    row.update(overrides)
    return row


def _reader(rows: list[dict[str, object]]):
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    session.execute.return_value = result
    return SqlAlchemyRuntimeBundleCitationApprovalReader(lambda: session), session


@pytest.mark.asyncio
async def test_reader_returns_canonical_pin_order_for_exact_bundle_pair() -> None:
    first = _row(source_code="Z_SOURCE")
    second = _row(source_code="A_SOURCE")
    reader, session = _reader([first, second])
    bundle_id = uuid4()

    pins = await reader.read_exact(bundle_id=bundle_id, bundle_manifest_hash="a" * 64)

    assert [pin.source_code for pin in pins] == ["A_SOURCE", "Z_SOURCE"]
    assert all(pin.purpose is SourceUsePurpose.PATIENT_CITATION for pin in pins)
    statement = str(session.execute.await_args.args[0])
    assert "bundle_id" in statement and "bundle_manifest_hash" in statement


@pytest.mark.asyncio
async def test_reader_returns_empty_when_exact_bundle_pair_has_no_pins() -> None:
    reader, _ = _reader([])

    assert await reader.read_exact(bundle_id=uuid4(), bundle_manifest_hash="b" * 64) == ()


@pytest.mark.asyncio
async def test_reader_fails_closed_for_corrupt_or_ambiguous_rows() -> None:
    snapshot_id = str(uuid4())
    corrupt_reader, _ = _reader([_row(purpose="RETRIEVAL")])
    duplicate_reader, _ = _reader([_row(source_snapshot_id=snapshot_id), _row(source_snapshot_id=snapshot_id)])

    with pytest.raises(RuntimeBundleCitationApprovalReadError):
        await corrupt_reader.read_exact(bundle_id=uuid4(), bundle_manifest_hash="c" * 64)
    with pytest.raises(RuntimeBundleCitationApprovalReadError):
        await duplicate_reader.read_exact(bundle_id=uuid4(), bundle_manifest_hash="d" * 64)


@pytest.mark.asyncio
async def test_reader_fails_closed_on_database_error() -> None:
    session = AsyncMock(spec=AsyncSession)
    session.__aenter__.return_value = session
    session.execute.side_effect = SQLAlchemyError("offline")
    reader = SqlAlchemyRuntimeBundleCitationApprovalReader(lambda: session)

    with pytest.raises(RuntimeBundleCitationApprovalReadError):
        await reader.read_exact(bundle_id=uuid4(), bundle_manifest_hash="e" * 64)
