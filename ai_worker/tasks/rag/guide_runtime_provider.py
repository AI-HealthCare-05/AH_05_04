"""Production construction seam for the canonical Guide runtime executor."""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ai_worker.adapters.openai_guideline_generator import OpenAIGuidelineGeneratorAdapter
from ai_worker.adapters.openai_text_embedding import (
    OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
    OpenAITextEmbeddingAdapter,
)
from ai_worker.adapters.postgresql_evidence_eligibility import PostgreSqlEvidenceEligibilityVerifier
from ai_worker.adapters.postgresql_evidence_search import (
    POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF,
    PostgresqlEvidenceSearchAdapter,
)
from ai_worker.adapters.sqlalchemy_evidence_authority import SqlAlchemyAssessmentEligibilityAuthorityReader
from ai_worker.adapters.sqlalchemy_guide_evidence_authority import SqlAlchemyGuideEvidenceAuthorityReader
from ai_worker.adapters.sqlalchemy_knowledge_chunk_content import SqlAlchemyKnowledgeChunkContentReader
from ai_worker.adapters.sqlalchemy_request_guard_runtime_binding import SqlAlchemyRequestGuardRuntimeBindingReader
from ai_worker.adapters.sqlalchemy_retrieval_run import SqlAlchemyRetrievalRunStore
from ai_worker.adapters.sqlalchemy_runtime_bundle_citation_approval import (
    SqlAlchemyRuntimeBundleCitationApprovalReader,
)
from ai_worker.adapters.sqlalchemy_source_use_approval import SqlAlchemySourceUseApprovalReader
from ai_worker.tasks.rag.citation_authorization_authority import (
    CitationAuthorityStorePort,
    CitationEligibilityReaderPort,
    RuntimeBundleCitationApprovalReaderPort,
)
from ai_worker.tasks.rag.guide_medication_guidance_retrieval import GuideMedicationGuidanceRetrievalDependencies
from ai_worker.tasks.rag.guide_medication_identity_resolution import MedicationIdentityRefResolverPort
from ai_worker.tasks.rag.guide_runtime_composition import (
    GuideRuntimeExecutorDependencies,
    create_guide_runtime_executor_factory,
)
from ai_worker.tasks.rag.guide_runtime_preflight import GuideRuntimePreflightRequest
from ai_worker.tasks.rag.guideline_approval_pack import Rag15ApprovalDecisionVerifierPort
from provider_runtime.observability import ProviderCallContext
from rag_runtime.guide_query_binding import (
    GuideQueryHmacKeyProvider,
    build_guide_query_fingerprint_producer,
    build_production_query_binding_verifier,
)
from rag_runtime.guide_runtime_execution import GuideRuntimeExecutorFactoryPort

__all__ = [
    "ProductionGuideRuntimeProviderDependencies",
    "ProductionGuideRuntimeProviderInitializationError",
    "build_production_guide_runtime_executor_factory",
]


class ProductionGuideRuntimeProviderInitializationError(RuntimeError):
    """The production factory cannot be constructed from the supplied authorities."""


@dataclass(frozen=True, slots=True)
class ProductionGuideRuntimeProviderDependencies:
    """Authorities that cannot be selected or invented by the production provider.

    Local/internal composition supplies these already-pinned runtime authorities.
    Backend treats this value as opaque and never imports its Worker-owned type.
    """

    session_factory: async_sessionmaker[AsyncSession]
    guide_query_hmac_keys: GuideQueryHmacKeyProvider
    guide_preflight_request: GuideRuntimePreflightRequest
    medication_identity_resolver: MedicationIdentityRefResolverPort
    decision_verifier: Rag15ApprovalDecisionVerifierPort
    citation_eligibility_reader: CitationEligibilityReaderPort
    citation_authority_store: CitationAuthorityStorePort
    provider_call_context: ProviderCallContext


def _require_dependencies(dependencies: object) -> ProductionGuideRuntimeProviderDependencies:
    if type(dependencies) is not ProductionGuideRuntimeProviderDependencies:
        raise ProductionGuideRuntimeProviderInitializationError("production Guide runtime dependencies are missing")
    for dependency_field in fields(dependencies):
        if getattr(dependencies, dependency_field.name) is None:
            raise ProductionGuideRuntimeProviderInitializationError(
                f"production Guide runtime dependency is missing: {dependency_field.name}"
            )
    return dependencies


def build_production_guide_runtime_executor_factory(
    dependencies: ProductionGuideRuntimeProviderDependencies,
    *,
    openai_client: Any,
    openai_model: str,
    openai_timeout_seconds: float,
) -> GuideRuntimeExecutorFactoryPort:
    """Build one process-scoped shared factory without selecting runtime authority.

    All Worker implementation types are constructed here. Missing or malformed
    inputs abort construction; there is no legacy Guide generator fallback.
    """

    resolved = _require_dependencies(dependencies)
    try:
        request_guard_reader = SqlAlchemyRequestGuardRuntimeBindingReader(resolved.session_factory)
        guide_evidence_reader = SqlAlchemyGuideEvidenceAuthorityReader(resolved.session_factory)
        retrieval_dependencies = GuideMedicationGuidanceRetrievalDependencies(
            query_fingerprint_producer=build_guide_query_fingerprint_producer(resolved.guide_query_hmac_keys),
            query_binding_verifier=build_production_query_binding_verifier(resolved.guide_query_hmac_keys),
            request_guard_runtime_binding_reader=request_guard_reader,
            selected_member_resolver=guide_evidence_reader,
            guide_evidence_authority_reader=guide_evidence_reader,
            search_port=PostgresqlEvidenceSearchAdapter(
                resolved.session_factory,
                POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF,
            ),
            text_embedding_port=OpenAITextEmbeddingAdapter(
                openai_client,
                OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
            ),
            run_store=SqlAlchemyRetrievalRunStore(resolved.session_factory),
            eligibility_verifier=PostgreSqlEvidenceEligibilityVerifier(resolved.session_factory),
        )
        executor_dependencies = GuideRuntimeExecutorDependencies(
            guide_preflight_request=resolved.guide_preflight_request,
            retrieval_dependencies=retrieval_dependencies,
            medication_identity_resolver=resolved.medication_identity_resolver,
            content_reader=SqlAlchemyKnowledgeChunkContentReader(resolved.session_factory),
            evidence_authority_reader=SqlAlchemyAssessmentEligibilityAuthorityReader(resolved.session_factory),
            generator=OpenAIGuidelineGeneratorAdapter(
                client=openai_client,
                model=openai_model,
                timeout_seconds=openai_timeout_seconds,
                context=resolved.provider_call_context,
            ),
            decision_verifier=resolved.decision_verifier,
            guard_reader=request_guard_reader,
            request_authority_reader=guide_evidence_reader,
            pin_reader=cast(
                RuntimeBundleCitationApprovalReaderPort,
                SqlAlchemyRuntimeBundleCitationApprovalReader(resolved.session_factory),
            ),
            approval_reader=SqlAlchemySourceUseApprovalReader(resolved.session_factory),
            eligibility_reader=resolved.citation_eligibility_reader,
            store=resolved.citation_authority_store,
        )
        return create_guide_runtime_executor_factory(executor_dependencies)
    except ProductionGuideRuntimeProviderInitializationError:
        raise
    except Exception as error:
        raise ProductionGuideRuntimeProviderInitializationError(
            "production Guide runtime factory initialization failed"
        ) from error
