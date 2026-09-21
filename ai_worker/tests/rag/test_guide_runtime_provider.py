from __future__ import annotations

from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_worker.tasks.rag.guide_runtime_provider import (
    ProductionGuideRuntimeProviderDependencies,
    ProductionGuideRuntimeProviderInitializationError,
    build_production_guide_runtime_executor_factory,
)
from provider_runtime.observability import DeploymentEnvironment, ProviderCallContext
from rag_runtime.guide_query_binding import ApprovedGuideQueryHmacKey
from rag_runtime.guide_runtime_execution import GuideRuntimeExecutorFactoryPort, GuideRuntimeExecutorPort


class _GuideQueryKeys:
    def active_key_version(self) -> str:
        return "guide-query-hmac-key@1"

    def key_for_version(self, key_version: str) -> ApprovedGuideQueryHmacKey | None:
        if key_version != self.active_key_version():
            return None
        return ApprovedGuideQueryHmacKey(b"synthetic-provider-test-key")


class _OpenAIClient:
    max_retries = 0

    def with_options(self, **_: object) -> _OpenAIClient:
        return self


def _dependencies() -> ProductionGuideRuntimeProviderDependencies:
    opaque_authority = object()
    return ProductionGuideRuntimeProviderDependencies(
        session_factory=cast(async_sessionmaker[AsyncSession], lambda: cast(AsyncSession, object())),
        guide_query_hmac_keys=_GuideQueryKeys(),
        guide_preflight_request=cast(Any, object()),
        medication_identity_resolver=cast(Any, opaque_authority),
        decision_verifier=cast(Any, opaque_authority),
        citation_eligibility_reader=cast(Any, opaque_authority),
        citation_authority_store=cast(Any, opaque_authority),
        provider_call_context=ProviderCallContext(
            trace_id="a" * 32,
            validation_run_id=UUID("61a10000-0000-4000-8000-000000000003"),
            environment=DeploymentEnvironment.LOCAL,
            validation_enabled=True,
        ),
    )


def test_production_provider_returns_shared_factory_port() -> None:
    factory: GuideRuntimeExecutorFactoryPort = build_production_guide_runtime_executor_factory(
        _dependencies(),
        openai_client=_OpenAIClient(),
        openai_model="gpt-4o",
        openai_timeout_seconds=20.0,
    )

    executor: GuideRuntimeExecutorPort = factory.create()

    assert callable(executor.execute)


def test_production_provider_fails_closed_when_a_required_dependency_is_missing() -> None:
    dependencies = replace(_dependencies(), medication_identity_resolver=None)  # type: ignore[arg-type]

    with pytest.raises(
        ProductionGuideRuntimeProviderInitializationError,
        match="medication_identity_resolver",
    ):
        build_production_guide_runtime_executor_factory(
            dependencies,
            openai_client=_OpenAIClient(),
            openai_model="gpt-4o",
            openai_timeout_seconds=20.0,
        )
