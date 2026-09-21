"""Isolated read-only data-plane for the Sync Chat CLOSED_DEMO binding."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import URL, Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.openai_text_embedding import OpenAITextEmbeddingAdapter
from ai_worker.adapters.postgresql_evidence_eligibility import PostgreSqlEvidenceEligibilityVerifier
from ai_worker.adapters.postgresql_evidence_search import (
    POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF,
    PostgresqlEvidenceSearchAdapter,
)
from ai_worker.adapters.sqlalchemy_knowledge_chunk_content import SqlAlchemyKnowledgeChunkContentReader
from ai_worker.tasks.rag.closed_demo_retrieval_binding import (
    ClosedDemoRetrievalBinding,
    load_closed_demo_retrieval_binding,
)
from ai_worker.tasks.rag.evidence_retrieval import QueryFingerprint, SensitiveText
from ai_worker.tasks.rag.evidence_search import EvidenceSearchRequest, ProductionSearchHit
from ai_worker.tasks.rag.production_evidence_gate import EvidenceGateSuccess
from ai_worker.tasks.rag.retrieval_runtime import (
    ProductionRetrievalRequest,
    RetrievalExecutionStatus,
    execute_production_retrieval,
)
from rag_runtime.closed_demo_chat_query_binding import (
    ClosedDemoChatQueryBindingDependencyError,
    ClosedDemoChatQueryFingerprintProducer,
    ClosedDemoChatQueryVerificationSuccess,
    ClosedDemoChatQueryVerifier,
)
from rag_runtime.guide_query_binding import (
    GuideQueryFingerprintDependencyError,
    GuideQueryFingerprintProducer,
    ProductionQueryBindingVerifier,
)
from rag_runtime.query_binding import QueryBindingVerificationSuccess

_SOURCE591_DATABASE = "source591_staging"
_SOURCE591_CONSUMER = "source591_consumer"


class ClosedDemoRetrievalConfigurationError(ValueError):
    """The dedicated retrieval data-plane configuration is unsafe or incomplete."""


class ClosedDemoRetrievalExecutionError(RuntimeError):
    """CLOSED_DEMO evidence was not fully authenticated and hydrated."""


@dataclass(frozen=True, slots=True)
class ClosedDemoRetrievalDatabaseConfig:
    host: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)

    def database_url(self) -> URL:
        if not self.host.strip() or not self.password:
            raise ClosedDemoRetrievalConfigurationError("source591 read-only credentials are required")
        if not 1 <= self.port <= 65535:
            raise ClosedDemoRetrievalConfigurationError("source591 port is invalid")
        if self.database != _SOURCE591_DATABASE or self.username != _SOURCE591_CONSUMER:
            raise ClosedDemoRetrievalConfigurationError(
                "CLOSED_DEMO retrieval must use the source591 consumer database"
            )
        return URL.create(
            "postgresql+asyncpg",
            username=self.username,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.database,
        )


@dataclass(frozen=True, slots=True)
class ClosedDemoRetrievalDependencies:
    binding: ClosedDemoRetrievalBinding
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    search_adapter: PostgresqlEvidenceSearchAdapter
    eligibility_verifier: PostgreSqlEvidenceEligibilityVerifier


@dataclass(frozen=True, slots=True)
class ClosedDemoEvidence:
    """The only evidence representation released to the Chat prompt builder."""

    display_order: int
    source_code: str
    source_version: str
    locator: str
    content: SensitiveText


class ClosedDemoRetrievalService:
    """Read-only CLOSED_DEMO composition over one sealed retrieval binding.

    This is intentionally the only Backend module permitted to consume the
    production-retrieval adapters.  It neither discovers a source/index nor
    persists data; any failed phase produces no evidence.
    """

    def __init__(
        self,
        *,
        dependencies: ClosedDemoRetrievalDependencies,
        text_embedding_adapter: OpenAITextEmbeddingAdapter,
        fingerprint_producer: ClosedDemoChatQueryFingerprintProducer,
        binding_verifier: ClosedDemoChatQueryVerifier,
    ) -> None:
        if (
            type(fingerprint_producer) is not ClosedDemoChatQueryFingerprintProducer
            or type(binding_verifier) is not ClosedDemoChatQueryVerifier
        ):
            raise ClosedDemoRetrievalConfigurationError("CLOSED_DEMO Chat query authority is required")
        self._dependencies = dependencies
        self._text_embedding_adapter = text_embedding_adapter
        self._fingerprint_producer = fingerprint_producer
        self._binding_verifier = binding_verifier
        self._content_reader = SqlAlchemyKnowledgeChunkContentReader(dependencies.session_factory)

    async def aclose(self) -> None:
        """Release the lifespan-owned source591 connection pool."""
        await self._dependencies.engine.dispose()

    async def retrieve(self, question: str) -> tuple[ClosedDemoEvidence, ...]:
        """Retrieve all-or-nothing evidence for an already validated Chat question."""
        # ``SensitiveText`` and ``QueryFingerprint`` are neutral shared value
        # types.  The authority below is exclusively CLOSED_DEMO Chat policy.
        query = SensitiveText(question)
        try:
            fingerprint = self._fingerprint_producer.produce(query)
        except ClosedDemoChatQueryBindingDependencyError as exc:
            raise ClosedDemoRetrievalExecutionError("query fingerprint unavailable") from exc
        verification = self._binding_verifier.verify(query, fingerprint)
        if type(verification) is not ClosedDemoChatQueryVerificationSuccess:
            raise ClosedDemoRetrievalExecutionError("query fingerprint verification failed")

        outcome = await execute_production_retrieval(
            ProductionRetrievalRequest(
                search_request=EvidenceSearchRequest(
                    normalized_query=query,
                    query_fingerprint=QueryFingerprint(
                        algorithm=fingerprint.algorithm,
                        key_version=fingerprint.key_version,
                        digest=fingerprint.digest,
                    ),
                    execution_binding=self._dependencies.binding.execution_binding,
                    query_embedding_receipt=None,
                )
            ),
            search_port=self._dependencies.search_adapter,
            text_embedding_port=self._text_embedding_adapter,
            eligibility_verifier=self._dependencies.eligibility_verifier,
        )
        if (
            outcome.status is not RetrievalExecutionStatus.SUCCEEDED
            or type(outcome.gate_outcome) is not EvidenceGateSuccess
        ):
            raise ClosedDemoRetrievalExecutionError("production retrieval did not select evidence")

        selected_hits = outcome.gate_outcome.selected_hits
        if not selected_hits:
            raise ClosedDemoRetrievalExecutionError("production retrieval selected no evidence")
        return await self._hydrate_selected_hits(selected_hits)

    async def _hydrate_selected_hits(self, hits: tuple[ProductionSearchHit, ...]) -> tuple[ClosedDemoEvidence, ...]:
        binding = self._dependencies.binding
        allowed_pairs = {
            (pair.source_snapshot_id, pair.source_snapshot_member_id) for pair in binding.snapshot_member_pairs
        }
        hydrated: list[ClosedDemoEvidence] = []
        for display_order, hit in enumerate(hits, start=1):
            provenance = hit.provenance
            if (
                provenance.knowledge_index_id != binding.execution_binding.knowledge_index_id
                or provenance.source_snapshot_id not in binding.execution_binding.allowed_source_snapshot_ids
                or provenance.source_snapshot_member_id
                not in binding.execution_binding.allowed_source_snapshot_member_ids
                or (provenance.source_snapshot_id, provenance.source_snapshot_member_id) not in allowed_pairs
            ):
                raise ClosedDemoRetrievalExecutionError("selected evidence is outside the sealed binding")
            try:
                observation = await self._content_reader.read_content(
                    knowledge_index_id=provenance.knowledge_index_id,
                    knowledge_chunk_id=provenance.knowledge_chunk_id,
                )
            except Exception as exc:
                raise ClosedDemoRetrievalExecutionError("selected evidence content is unavailable") from exc
            if observation is None or observation.provenance != provenance:
                raise ClosedDemoRetrievalExecutionError("selected evidence provenance mismatch")
            if hashlib.sha256(observation.content_text.reveal().encode("utf-8")).hexdigest() != provenance.content_hash:
                raise ClosedDemoRetrievalExecutionError("selected evidence content hash mismatch")
            hydrated.append(
                ClosedDemoEvidence(
                    display_order=display_order,
                    source_code=provenance.source_code,
                    source_version=provenance.source_version,
                    locator=provenance.locator,
                    content=observation.content_text,
                )
            )
        return tuple(hydrated)


def build_closed_demo_retrieval_dependencies(
    database_config: ClosedDemoRetrievalDatabaseConfig,
) -> ClosedDemoRetrievalDependencies:
    """Create only the sealed binding's read-only Search and Evidence Gate dependencies."""
    binding = load_closed_demo_retrieval_binding()
    engine = create_async_engine(
        database_config.database_url(),
        isolation_level="REPEATABLE READ",
        hide_parameters=True,
        pool_pre_ping=True,
    )

    @event.listens_for(engine.sync_engine, "begin")
    def _declare_read_only(connection: Connection) -> None:
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
    return ClosedDemoRetrievalDependencies(
        binding=binding,
        engine=engine,
        session_factory=session_factory,
        search_adapter=PostgresqlEvidenceSearchAdapter(session_factory, POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF),
        eligibility_verifier=PostgreSqlEvidenceEligibilityVerifier(session_factory),
    )


def build_closed_demo_retrieval_service(
    *,
    database_config: ClosedDemoRetrievalDatabaseConfig,
    openai_client: Any,
    fingerprint_producer: ClosedDemoChatQueryFingerprintProducer,
    binding_verifier: ClosedDemoChatQueryVerifier,
) -> ClosedDemoRetrievalService:
    """Build the sealed read-only retrieval composition without opening a session."""
    dependencies = build_closed_demo_retrieval_dependencies(database_config)
    embedding_adapter_ref = dependencies.binding.execution_binding.retrieval_config.expected_query_embedding_adapter_ref
    if embedding_adapter_ref is None:
        raise ClosedDemoRetrievalConfigurationError("sealed binding has no embedding adapter")
    return ClosedDemoRetrievalService(
        dependencies=dependencies,
        text_embedding_adapter=OpenAITextEmbeddingAdapter(
            openai_client,
            embedding_adapter_ref,
        ),
        fingerprint_producer=fingerprint_producer,
        binding_verifier=binding_verifier,
    )


class GuideClosedDemoRetrievalConfigurationError(ValueError):
    """The Guide CLOSED_DEMO retrieval configuration is unsafe or incomplete."""


class GuideClosedDemoRetrievalExecutionError(RuntimeError):
    """Guide CLOSED_DEMO evidence was not fully authenticated and hydrated."""


class GuideClosedDemoEvidenceFilteringError(RuntimeError):
    """No exact-product evidence remained after cross-drug filtering."""


@dataclass(frozen=True, slots=True)
class GuideClosedDemoEvidence:
    """Exact-product evidence released to the Guide prompt builder."""

    slot: int
    external_document_id: str
    source_code: str
    source_version: str
    locator: str
    content: SensitiveText


class GuideClosedDemoRetrievalService:
    """Read-only Guide CLOSED_DEMO composition over one sealed retrieval binding.

    It executes lexical + dense + RRF retrieval, verifies the Guide HMAC query fingerprint,
    filters out any cross-drug evidence that does not match the medication's exact item_seq,
    and hydrates verified knowledge chunks.
    """

    def __init__(
        self,
        *,
        dependencies: ClosedDemoRetrievalDependencies,
        text_embedding_adapter: OpenAITextEmbeddingAdapter,
        fingerprint_producer: GuideQueryFingerprintProducer,
        binding_verifier: ProductionQueryBindingVerifier,
    ) -> None:
        if (
            type(fingerprint_producer) is not GuideQueryFingerprintProducer
            or type(binding_verifier) is not ProductionQueryBindingVerifier
        ):
            raise GuideClosedDemoRetrievalConfigurationError("Guide query HMAC authority is required")
        self._dependencies = dependencies
        self._text_embedding_adapter = text_embedding_adapter
        self._fingerprint_producer = fingerprint_producer
        self._binding_verifier = binding_verifier
        self._content_reader = SqlAlchemyKnowledgeChunkContentReader(dependencies.session_factory)

    async def aclose(self) -> None:
        """Release the lifespan-owned source591 connection pool."""
        await self._dependencies.engine.dispose()

    async def retrieve_exact_evidence(
        self,
        *,
        query_text: str,
        expected_item_seq: str,
    ) -> tuple[GuideClosedDemoEvidence, ...]:
        """Retrieve and hydrate exact-product evidence for a medication item_seq."""
        query = SensitiveText(query_text)
        try:
            fingerprint = self._fingerprint_producer.produce(query)
        except GuideQueryFingerprintDependencyError as exc:
            raise GuideClosedDemoRetrievalExecutionError("Guide query fingerprint unavailable") from exc

        verification = self._binding_verifier.verify(query, fingerprint)
        if type(verification) is not QueryBindingVerificationSuccess:
            raise GuideClosedDemoRetrievalExecutionError("Guide query fingerprint verification failed")

        outcome = await execute_production_retrieval(
            ProductionRetrievalRequest(
                search_request=EvidenceSearchRequest(
                    normalized_query=query,
                    query_fingerprint=QueryFingerprint(
                        algorithm=fingerprint.algorithm,
                        key_version=fingerprint.key_version,
                        digest=fingerprint.digest,
                    ),
                    execution_binding=self._dependencies.binding.execution_binding,
                    query_embedding_receipt=None,
                )
            ),
            search_port=self._dependencies.search_adapter,
            text_embedding_port=self._text_embedding_adapter,
            eligibility_verifier=self._dependencies.eligibility_verifier,
        )

        if (
            outcome.status is not RetrievalExecutionStatus.SUCCEEDED
            or type(outcome.gate_outcome) is not EvidenceGateSuccess
        ):
            raise GuideClosedDemoRetrievalExecutionError("production retrieval did not select evidence")

        selected_hits = outcome.gate_outcome.selected_hits
        if not selected_hits:
            raise GuideClosedDemoRetrievalExecutionError("production retrieval selected no evidence")

        # EXACT PRODUCT FILTERING:
        # Provider can ONLY receive evidence belonging strictly to expected_item_seq.
        prefix = f"mfds-label:{expected_item_seq}:"
        exact_hits = tuple(hit for hit in selected_hits if hit.provenance.external_document_id.startswith(prefix))

        if not exact_hits:
            raise GuideClosedDemoEvidenceFilteringError(f"Zero exact product evidence for item_seq {expected_item_seq}")

        return await self._hydrate_selected_hits(exact_hits)

    async def _hydrate_selected_hits(
        self,
        hits: tuple[ProductionSearchHit, ...],
    ) -> tuple[GuideClosedDemoEvidence, ...]:
        binding = self._dependencies.binding
        allowed_pairs = {
            (pair.source_snapshot_id, pair.source_snapshot_member_id) for pair in binding.snapshot_member_pairs
        }
        hydrated: list[GuideClosedDemoEvidence] = []
        for slot, hit in enumerate(hits, start=1):
            provenance = hit.provenance
            if (
                provenance.knowledge_index_id != binding.execution_binding.knowledge_index_id
                or provenance.source_snapshot_id not in binding.execution_binding.allowed_source_snapshot_ids
                or provenance.source_snapshot_member_id
                not in binding.execution_binding.allowed_source_snapshot_member_ids
                or (provenance.source_snapshot_id, provenance.source_snapshot_member_id) not in allowed_pairs
            ):
                raise GuideClosedDemoRetrievalExecutionError("selected evidence is outside the sealed binding")

            try:
                observation = await self._content_reader.read_content(
                    knowledge_index_id=provenance.knowledge_index_id,
                    knowledge_chunk_id=provenance.knowledge_chunk_id,
                )
            except Exception as exc:
                raise GuideClosedDemoRetrievalExecutionError("selected evidence content is unavailable") from exc

            if observation is None or observation.provenance != provenance:
                raise GuideClosedDemoRetrievalExecutionError("selected evidence provenance mismatch")

            if hashlib.sha256(observation.content_text.reveal().encode("utf-8")).hexdigest() != provenance.content_hash:
                raise GuideClosedDemoRetrievalExecutionError("selected evidence content hash mismatch")

            hydrated.append(
                GuideClosedDemoEvidence(
                    slot=slot,
                    external_document_id=provenance.external_document_id,
                    source_code=provenance.source_code,
                    source_version=provenance.source_version,
                    locator=provenance.locator,
                    content=observation.content_text,
                )
            )
        return tuple(hydrated)


def build_guide_closed_demo_retrieval_service(
    *,
    database_config: ClosedDemoRetrievalDatabaseConfig,
    openai_client: Any,
    fingerprint_producer: GuideQueryFingerprintProducer,
    binding_verifier: ProductionQueryBindingVerifier,
) -> GuideClosedDemoRetrievalService:
    """Build the sealed read-only Guide retrieval composition without opening a session."""
    dependencies = build_closed_demo_retrieval_dependencies(database_config)
    embedding_adapter_ref = dependencies.binding.execution_binding.retrieval_config.expected_query_embedding_adapter_ref
    if embedding_adapter_ref is None:
        raise GuideClosedDemoRetrievalConfigurationError("sealed binding has no embedding adapter")
    return GuideClosedDemoRetrievalService(
        dependencies=dependencies,
        text_embedding_adapter=OpenAITextEmbeddingAdapter(
            openai_client,
            embedding_adapter_ref,
        ),
        fingerprint_producer=fingerprint_producer,
        binding_verifier=binding_verifier,
    )
