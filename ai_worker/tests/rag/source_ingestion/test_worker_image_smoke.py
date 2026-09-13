from unittest.mock import AsyncMock, MagicMock

import pytest

from ai_worker.tasks.rag.source_ingestion import worker_image_smoke


@pytest.mark.asyncio
async def test_verify_source_snapshot_adapter_queries_table_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MagicMock()
    repository.get_latest_snapshot = AsyncMock(return_value=None)
    session = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    session_factory = MagicMock(return_value=session_context)
    engine = MagicMock()
    engine.dispose = AsyncMock()

    create_engine = MagicMock(return_value=engine)
    create_factory = MagicMock(return_value=session_factory)
    create_repository = MagicMock(return_value=repository)
    monkeypatch.setattr(worker_image_smoke, "create_worker_engine", create_engine)
    monkeypatch.setattr(worker_image_smoke, "create_session_factory", create_factory)
    monkeypatch.setattr(worker_image_smoke, "SqlAlchemySourceSnapshotRepository", create_repository)

    config = MagicMock()
    await worker_image_smoke.verify_source_snapshot_adapter(config)

    create_engine.assert_called_once_with(config)
    create_factory.assert_called_once_with(engine)
    create_repository.assert_called_once_with(session)
    repository.get_latest_snapshot.assert_awaited_once_with(operation_id=worker_image_smoke._MISSING_OPERATION_ID)
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_source_snapshot_adapter_disposes_engine_after_query_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = MagicMock()
    repository.get_latest_snapshot = AsyncMock(side_effect=RuntimeError("query failed"))
    session_context = AsyncMock()
    session_context.__aenter__.return_value = AsyncMock()
    engine = MagicMock()
    engine.dispose = AsyncMock()

    monkeypatch.setattr(worker_image_smoke, "create_worker_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(
        worker_image_smoke,
        "create_session_factory",
        MagicMock(return_value=MagicMock(return_value=session_context)),
    )
    monkeypatch.setattr(
        worker_image_smoke,
        "SqlAlchemySourceSnapshotRepository",
        MagicMock(return_value=repository),
    )

    with pytest.raises(RuntimeError, match="query failed"):
        await worker_image_smoke.verify_source_snapshot_adapter(MagicMock())

    engine.dispose.assert_awaited_once()
