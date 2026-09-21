from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI

from ai_worker.tasks.rag.evidence_retrieval import SensitiveText
from ai_worker.tasks.rag.retrieval_runtime import RetrievalExecutionStatus
from app import main
from app.core.closed_demo_retrieval import (
    ClosedDemoRetrievalDatabaseConfig,
    ClosedDemoRetrievalDependencies,
    build_closed_demo_retrieval_dependencies,
)
from app.core.guide_closed_demo_retrieval import (
    GuideClosedDemoEvidence,
    GuideClosedDemoEvidenceFilteringError,
    GuideClosedDemoRetrievalService,
)
from app.dependencies import services
from app.dependencies.services import (
    GuideQueryHmacKeyDependency,
    build_guide_query_fingerprint_producer,
    build_production_query_binding_verifier,
)
from rag_runtime.guide_query_binding import ApprovedGuideQueryHmacKey


def test_guide_closed_demo_dependencies_use_source591_read_only_factory() -> None:
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


def test_guide_closed_demo_dependency_returns_the_lifespan_owned_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retriever = cast(GuideClosedDemoRetrievalService, object())
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(guide_closed_demo_retrieval_service=retriever)))
    monkeypatch.setattr(services.config, "GUIDE_CLOSED_DEMO_RAG_ENABLED", True)

    assert services.get_guide_closed_demo_retrieval_service(request) is retriever  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_guide_closed_demo_service_closes_its_source591_engine() -> None:
    engine = SimpleNamespace(dispose=AsyncMock())
    service = object.__new__(GuideClosedDemoRetrievalService)
    service._dependencies = cast(ClosedDemoRetrievalDependencies, SimpleNamespace(engine=engine))

    await service.aclose()

    engine.dispose.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_lifespan_owns_one_guide_closed_demo_retriever_and_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    openai_client = SimpleNamespace(close=AsyncMock())
    retriever = SimpleNamespace(aclose=AsyncMock())
    build_calls: list[object] = []
    close_database = AsyncMock()
    app = FastAPI()

    def build_retriever(client: object) -> SimpleNamespace:
        build_calls.append(client)
        return retriever

    monkeypatch.setattr(main.config, "GUIDE_CLOSED_DEMO_RAG_ENABLED", True)
    monkeypatch.setattr(main, "get_email_sender", lambda: None)
    monkeypatch.setattr(main, "AsyncOpenAI", lambda **_kwargs: openai_client)
    monkeypatch.setattr(main, "build_configured_guide_closed_demo_retrieval_service", build_retriever)
    monkeypatch.setattr(main, "close_database", close_database)

    async with main.lifespan(app):
        assert app.state.guide_closed_demo_retrieval_service is retriever
        assert build_calls == [openai_client]

    retriever.aclose.assert_awaited_once_with()
    openai_client.close.assert_awaited_once_with()
    close_database.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_exact_product_filtering_drops_cross_drug_evidence() -> None:
    """MUST ADJUST 4 regression test:

    Cross-drug evidence in retrieval results must be discarded before reaching Provider.
    Only evidence with external_document_id starting with mfds-label:{expected_item_seq}:
    is preserved.
    """
    key_dep = GuideQueryHmacKeyDependency(
        "guide-query-hmac-key@1", ApprovedGuideQueryHmacKey(b"test-key-32-bytes-long-secret-val")
    )
    producer = build_guide_query_fingerprint_producer(key_dep)
    verifier = build_production_query_binding_verifier(key_dep)

    hit_novasc = SimpleNamespace(
        provenance=SimpleNamespace(
            external_document_id="mfds-label:200610660:item1",
            source_code="MFDS_LABEL",
            source_version="1.0",
            locator="dosage",
            content_hash="hash1",
        )
    )
    hit_atorva = SimpleNamespace(
        provenance=SimpleNamespace(
            external_document_id="mfds-label:199902000:item1",
            source_code="MFDS_LABEL",
            source_version="1.0",
            locator="precautions",
            content_hash="hash2",
        )
    )
    hit_ibuprofen = SimpleNamespace(
        provenance=SimpleNamespace(
            external_document_id="mfds-label:199300305:item1",
            source_code="MFDS_LABEL",
            source_version="1.0",
            locator="warnings",
            content_hash="hash3",
        )
    )

    mock_gate_outcome = SimpleNamespace(selected_hits=(hit_novasc, hit_atorva, hit_ibuprofen))
    mock_outcome = SimpleNamespace(
        status=RetrievalExecutionStatus.SUCCEEDED,
        gate_outcome=mock_gate_outcome,
    )

    service = object.__new__(GuideClosedDemoRetrievalService)
    service._dependencies = SimpleNamespace(
        binding=SimpleNamespace(execution_binding=SimpleNamespace()),
        search_adapter=object(),
        eligibility_verifier=object(),
    )
    service._text_embedding_adapter = object()
    service._fingerprint_producer = producer
    service._binding_verifier = verifier

    async def fake_hydrate(hits: tuple[object, ...]) -> tuple[GuideClosedDemoEvidence, ...]:
        return tuple(
            GuideClosedDemoEvidence(
                slot=i,
                external_document_id=h.provenance.external_document_id,
                source_code=h.provenance.source_code,
                source_version=h.provenance.source_version,
                locator=h.provenance.locator,
                content=SensitiveText("safe text"),
            )
            for i, h in enumerate(hits, start=1)
        )

    service._hydrate_selected_hits = fake_hydrate

    with (
        patch(
            "app.core.closed_demo_retrieval.execute_production_retrieval",
            new=AsyncMock(return_value=mock_outcome),
        ),
        patch("app.core.closed_demo_retrieval.EvidenceGateSuccess", new=type(mock_gate_outcome)),
    ):
        results = await service.retrieve_exact_evidence(
            query_text="노바스크정 5mg",
            expected_item_seq="200610660",
        )

    # Must contain ONLY the novasc hit (200610660); atorvastatin and ibuprofen must be eliminated!
    assert len(results) == 1
    assert results[0].external_document_id == "mfds-label:200610660:item1"
    assert results[0].slot == 1


@pytest.mark.asyncio
async def test_exact_product_filtering_fails_closed_on_zero_exact_hits() -> None:
    """When retrieval selects hits but 0 match expected_item_seq, fail closed."""
    key_dep = GuideQueryHmacKeyDependency(
        "guide-query-hmac-key@1", ApprovedGuideQueryHmacKey(b"test-key-32-bytes-long-secret-val")
    )
    producer = build_guide_query_fingerprint_producer(key_dep)
    verifier = build_production_query_binding_verifier(key_dep)

    hit_other = SimpleNamespace(
        provenance=SimpleNamespace(
            external_document_id="mfds-label:999999999:item1",
            source_code="MFDS_LABEL",
            source_version="1.0",
            locator="dosage",
            content_hash="hash1",
        )
    )
    mock_gate_outcome = SimpleNamespace(selected_hits=(hit_other,))
    mock_outcome = SimpleNamespace(
        status=RetrievalExecutionStatus.SUCCEEDED,
        gate_outcome=mock_gate_outcome,
    )

    service = object.__new__(GuideClosedDemoRetrievalService)
    service._dependencies = SimpleNamespace(
        binding=SimpleNamespace(execution_binding=SimpleNamespace()),
        search_adapter=object(),
        eligibility_verifier=object(),
    )
    service._text_embedding_adapter = object()
    service._fingerprint_producer = producer
    service._binding_verifier = verifier

    with (
        patch(
            "app.core.closed_demo_retrieval.execute_production_retrieval",
            new=AsyncMock(return_value=mock_outcome),
        ),
        patch("app.core.closed_demo_retrieval.EvidenceGateSuccess", new=type(mock_gate_outcome)),
    ):
        with pytest.raises(GuideClosedDemoEvidenceFilteringError, match="Zero exact product evidence"):
            await service.retrieve_exact_evidence(
                query_text="노바스크정 5mg",
                expected_item_seq="200610660",
            )
