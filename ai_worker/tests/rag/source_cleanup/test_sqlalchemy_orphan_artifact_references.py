from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.adapters.sqlalchemy_orphan_artifact_references import (
    SqlAlchemyOrphanArtifactReferenceInspector,
    lock_source_artifact_mutation,
)
from ai_worker.tasks.rag.source_cleanup.orphan_artifact import CleanupTarget


def _target() -> CleanupTarget:
    return CleanupTarget(
        "mfds-label/200610660/EE.xml",
        "sha256/aa/" + "a" * 64 + ".artifact",
        "a" * 64,
    )


@pytest.mark.asyncio
async def test_reference_inspector_counts_global_artifact_run_member_and_snapshot_refs() -> None:
    session = AsyncMock(spec=AsyncSession)
    artifact_result = MagicMock()
    artifact_result.all.return_value = [
        SimpleNamespace(id="artifact-1", ingestion_run_id="run-1", raw_checksum="a" * 64),
        SimpleNamespace(id="artifact-2", ingestion_run_id="run-2", raw_checksum="b" * 64),
    ]
    session.execute.return_value = artifact_result
    session.scalar.side_effect = [1, 1]

    result = await SqlAlchemyOrphanArtifactReferenceInspector(session).inspect(_target())

    assert result.artifact_receipts == 2
    assert result.ingestion_runs == 2
    assert result.snapshot_members == 1
    assert result.snapshots == 1
    assert result.checksum_conflicts == 1
    artifact_sql = str(session.execute.await_args.args[0])
    assert "rag_source_ingestion_artifact.object_key" in artifact_sql
    assert "rag_source_ingestion_artifact.raw_checksum" in artifact_sql
    assert "run_status" not in artifact_sql


@pytest.mark.asyncio
async def test_shared_mutation_lock_is_transaction_scoped() -> None:
    session = AsyncMock(spec=AsyncSession)
    await lock_source_artifact_mutation(session)
    sql = str(session.execute.await_args.args[0])
    assert "pg_advisory_xact_lock" in sql
    assert "source-artifact-cleanup-613" in session.execute.await_args.args[0].text
