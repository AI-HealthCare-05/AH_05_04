from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from app import main
from app.core.closed_demo_retrieval import (
    ClosedDemoRetrievalDatabaseConfig,
    ClosedDemoRetrievalDependencies,
    ClosedDemoRetrievalService,
    build_closed_demo_retrieval_dependencies,
)
from app.dependencies import services


def test_closed_demo_dependencies_use_a_separate_source591_read_only_factory() -> None:
    """Catches accidentally routing CLOSED_DEMO retrieval through the application DB factory."""
    dependencies = build_closed_demo_retrieval_dependencies(
        ClosedDemoRetrievalDatabaseConfig(
            host="source591.internal",
            port=5432,
            database="source591_staging",
            username="source591_consumer",
            password="secret-not-logged",
        )
    )

    assert dependencies.binding.database == "source591_staging"
    assert dependencies.binding.access_mode == "READ_ONLY"
    assert dependencies.search_adapter._session_factory is dependencies.session_factory
    assert dependencies.eligibility_verifier._session_factory is dependencies.session_factory
    assert "source591.internal" in str(dependencies.engine.url)


def test_closed_demo_dependency_returns_the_lifespan_owned_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request must never construct another source591 engine or pool."""
    retriever = cast(ClosedDemoRetrievalService, object())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(closed_demo_retrieval_service=retriever)))
    monkeypatch.setattr(services.config, "CHAT_CLOSED_DEMO_RAG_ENABLED", True)

    assert services.get_closed_demo_retrieval_service(request) is retriever  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_closed_demo_service_closes_its_source591_engine() -> None:
    """The lifespan owner can deterministically release the dedicated pool."""
    engine = SimpleNamespace(dispose=AsyncMock())
    service = object.__new__(ClosedDemoRetrievalService)
    service._dependencies = cast(ClosedDemoRetrievalDependencies, SimpleNamespace(engine=engine))

    await service.aclose()

    engine.dispose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_lifespan_owns_one_closed_demo_retriever_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dedicated source591 pool has one application lifecycle owner."""
    openai_client = SimpleNamespace(close=AsyncMock())
    retriever = SimpleNamespace(aclose=AsyncMock())
    build_calls: list[object] = []
    close_database = AsyncMock()
    app = FastAPI()

    monkeypatch.setattr(main.config, "CHAT_CLOSED_DEMO_RAG_ENABLED", True)
    monkeypatch.setattr(main, "get_email_sender", lambda: None)
    monkeypatch.setattr(main, "AsyncOpenAI", lambda **_kwargs: openai_client)
    monkeypatch.setattr(
        main,
        "build_configured_closed_demo_retrieval_service",
        lambda client: build_calls.append(client) or retriever,
    )
    monkeypatch.setattr(main, "close_database", close_database)

    async with main.lifespan(app):
        assert app.state.closed_demo_retrieval_service is retriever
        assert build_calls == [openai_client]

    retriever.aclose.assert_awaited_once_with()
    openai_client.close.assert_awaited_once_with()
    close_database.assert_awaited_once_with()
