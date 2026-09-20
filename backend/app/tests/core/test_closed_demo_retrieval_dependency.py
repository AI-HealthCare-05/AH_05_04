from __future__ import annotations

from app.core.closed_demo_retrieval import ClosedDemoRetrievalDatabaseConfig, build_closed_demo_retrieval_dependencies


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
