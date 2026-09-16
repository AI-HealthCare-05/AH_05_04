from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.runner import AdapterRequest, AsyncEvaluationAdapter
from ai_worker.tasks.evaluation.schemas.artifacts import CASE_RESULT_ADAPTER, RetrievalCaseResult

if TYPE_CHECKING:
    from ai_worker.tasks.evaluation.config import ResolvedDevExecution
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    QueryFingerprint,
    SensitiveText,
)
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchExecutionBinding,
    EvidenceSearchPort,
    EvidenceSearchRequest,
    ProductionSearchHit,
    RetrievalExecutionMode,
    VersionedEvidenceRetrievalConfiguration,
)
from ai_worker.tasks.rag.production_evidence_gate import (
    ProductionEvidenceEligibilityVerifierPort,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    EvidenceGateSuccess,
    ProductionRetrievalOutcome,
    ProductionRetrievalRequest,
    ProductionSearchReceipt,
    RetrievalExecutionStatus,
    TextEmbeddingPort,
    execute_production_retrieval,
)


@dataclass(frozen=True, slots=True)
class LatencySummary:
    count: int
    min_ms: int
    median_ms: int
    p95_ms: int
    max_ms: int


@dataclass(frozen=True, slots=True)
class ActualRetrievalReceipts:
    search_receipt_hashes: tuple[str, ...]
    query_embedding_shas: tuple[str, ...]
    retrieval_config_hash: str
    adapter_artifact_ref: ImmutableArtifactRef
    latency_summary: LatencySummary


def sealed_retrieval_config(
    config: VersionedEvidenceRetrievalConfiguration,
) -> VersionedEvidenceRetrievalConfiguration:
    """Rebind every configuration artifact ref to its own canonical hash.

    ``validate_retrieval_configuration`` rejects a configuration whose artifact
    ref does not carry its canonical hash, so placeholder refs must be sealed
    before the configuration reaches the search port.
    """

    lexical = config.lexical_config
    lexical = replace(
        lexical,
        artifact_ref=replace(lexical.artifact_ref, content_sha256=lexical.compute_canonical_hash()),
    )
    dense = config.dense_config
    if dense is not None:
        dense = replace(
            dense,
            artifact_ref=replace(dense.artifact_ref, content_sha256=dense.compute_canonical_hash()),
        )
    shaped = replace(config, lexical_config=lexical, dense_config=dense)
    return replace(
        shaped,
        artifact_ref=replace(shaped.artifact_ref, content_sha256=shaped.compute_canonical_hash()),
    )


def variant_retrieval_config(
    config: VersionedEvidenceRetrievalConfiguration,
    mode: RetrievalExecutionMode,
) -> VersionedEvidenceRetrievalConfiguration:
    """Shape one retrieval configuration for a variant's execution mode.

    ``LEXICAL_ONLY`` must not carry dense inputs; the other modes require them.
    """

    if mode is RetrievalExecutionMode.LEXICAL_ONLY:
        shaped = replace(
            config,
            execution_mode=mode,
            dense_config=None,
            expected_query_embedding_adapter_ref=None,
        )
    else:
        shaped = replace(config, execution_mode=mode)
    return sealed_retrieval_config(shaped)


class ActualRetrievalEvaluationAdapter(AsyncEvaluationAdapter):
    """Adapter executing actual production retrieval through execute_production_retrieval."""

    def __init__(
        self,
        *,
        search_port: EvidenceSearchPort,
        text_embedding_port: TextEmbeddingPort | None,
        eligibility_verifier: ProductionEvidenceEligibilityVerifierPort,
        filter_snapshot_ref: ImmutableArtifactRef,
        evidence_index_ref: ImmutableArtifactRef,
        knowledge_index_id: UUID,
        allowed_source_snapshot_ids: tuple[UUID, ...],
        allowed_source_snapshot_member_ids: tuple[UUID, ...],
        retrieval_config: VersionedEvidenceRetrievalConfiguration,
        adapter_artifact_ref: ImmutableArtifactRef,
    ) -> None:
        self._search_port = search_port
        self._text_embedding_port = text_embedding_port
        self._eligibility_verifier = eligibility_verifier
        self._filter_snapshot_ref = filter_snapshot_ref
        self._evidence_index_ref = evidence_index_ref
        self._knowledge_index_id = knowledge_index_id
        self._allowed_source_snapshot_ids = allowed_source_snapshot_ids
        self._allowed_source_snapshot_member_ids = allowed_source_snapshot_member_ids
        self._retrieval_config = retrieval_config
        self._adapter_artifact_ref = adapter_artifact_ref

        self._receipts_by_case: dict[str, ProductionSearchReceipt] = {}
        self._latencies_ms: list[int] = []

    def validate_case_set(self, case_ids: Sequence[str]) -> None:
        if len(case_ids) != len(set(case_ids)):
            raise EvaluationValidationError(
                EvaluationErrorCode.RETRIEVAL_RESULT_INVALID,
                "Case set contains duplicate case IDs",
            )

    async def execute(self, request: AdapterRequest) -> RetrievalCaseResult:  # noqa: C901
        user_query = getattr(request.case, "query", None) or getattr(request.case, "user_query", "")
        dataset_code = getattr(request.case, "dataset_code", "rag-natural-language-retrieval-dev")
        dataset_version = getattr(request.case, "dataset_version", "1.0.0")
        input_sha = getattr(request, "input_sha256", None) or getattr(request, "case_input_sha256", "0" * 64)
        if not user_query or not str(user_query).strip():
            return cast(
                RetrievalCaseResult,
                CASE_RESULT_ADAPTER.validate_python(
                    {
                        "schema_id": "rag-eval.case-result",
                        "schema_version": "1.0.0",
                        "run_id": str(request.run_id),
                        "case_id": request.case.case_id,
                        "dataset_code": dataset_code,
                        "dataset_version": dataset_version,
                        "partition": request.case.partition.value
                        if hasattr(request.case.partition, "value")
                        else str(request.case.partition),
                        "task_type": "RETRIEVAL",
                        "input_sha256": input_sha,
                        "execution_status": "INVALID",
                        "decision_status": None,
                        "failure_codes": ["EMPTY_USER_QUERY"],
                        "retrieved_evidence_ids": [],
                        "selected_evidence_ids": [],
                        "actual_claim_ids": None,
                        "actual_citation_evidence_ids": None,
                        "actual_rule_ids": None,
                        "actual_scope_codes": None,
                        "actual_response_level": None,
                        "actual_safety_disposition": None,
                        "actual_execution_status": None,
                        "actual_release_decision": None,
                        "actual_fallback_code": None,
                        "actual_provider_invocation": None,
                        "actual_retrieval_invocation": True,
                        "actual_publication_allowed": None,
                        "actual_sections": None,
                        "omitted_sections": None,
                        "risk_level": None,
                        "answer_sha256": None,
                        "latency_ms": 0,
                        "input_token_count": None,
                        "output_token_count": None,
                        "estimated_cost": None,
                    }
                ),
            )

        # Map variant to Execution Mode
        variant_id = request.variant_id
        if variant_id == "RET-L":
            mode = RetrievalExecutionMode.LEXICAL_ONLY
        elif variant_id == "RET-D":
            mode = RetrievalExecutionMode.DENSE_ONLY
        elif variant_id == "RET-H":
            mode = RetrievalExecutionMode.HYBRID_RRF
        else:
            mode = self._retrieval_config.execution_mode

        if (
            mode in (RetrievalExecutionMode.DENSE_ONLY, RetrievalExecutionMode.HYBRID_RRF)
            and self._text_embedding_port is None
        ):
            return cast(
                RetrievalCaseResult,
                CASE_RESULT_ADAPTER.validate_python(
                    {
                        "schema_id": "rag-eval.case-result",
                        "schema_version": "1.0.0",
                        "run_id": str(request.run_id),
                        "case_id": request.case.case_id,
                        "dataset_code": dataset_code,
                        "dataset_version": dataset_version,
                        "partition": request.case.partition.value
                        if hasattr(request.case.partition, "value")
                        else str(request.case.partition),
                        "task_type": "RETRIEVAL",
                        "input_sha256": input_sha,
                        "execution_status": "INVALID",
                        "decision_status": None,
                        "failure_codes": ["BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL"],
                        "retrieved_evidence_ids": [],
                        "selected_evidence_ids": [],
                        "actual_claim_ids": None,
                        "actual_citation_evidence_ids": None,
                        "actual_rule_ids": None,
                        "actual_scope_codes": None,
                        "actual_response_level": None,
                        "actual_safety_disposition": None,
                        "actual_execution_status": None,
                        "actual_release_decision": None,
                        "actual_fallback_code": None,
                        "actual_provider_invocation": None,
                        "actual_retrieval_invocation": True,
                        "actual_publication_allowed": None,
                        "actual_sections": None,
                        "omitted_sections": None,
                        "risk_level": None,
                        "answer_sha256": None,
                        "latency_ms": 0,
                        "input_token_count": None,
                        "output_token_count": None,
                        "estimated_cost": None,
                    }
                ),
            )

        effective_config = variant_retrieval_config(self._retrieval_config, mode)

        binding = EvidenceSearchExecutionBinding(
            filter_snapshot_ref=self._filter_snapshot_ref,
            evidence_index_ref=self._evidence_index_ref,
            knowledge_index_id=self._knowledge_index_id,
            allowed_source_snapshot_ids=self._allowed_source_snapshot_ids,
            allowed_source_snapshot_member_ids=self._allowed_source_snapshot_member_ids,
            retrieval_config=effective_config,
        )

        q_hash = hashlib.sha256(user_query.strip().encode("utf-8")).hexdigest()
        q_fp = QueryFingerprint(
            algorithm="sha256",
            key_version="v1",
            digest=q_hash,
        )

        search_req = EvidenceSearchRequest(
            normalized_query=SensitiveText(user_query.strip()),
            query_fingerprint=q_fp,
            execution_binding=binding,
            query_embedding_receipt=None,
        )

        start_time = time.monotonic()
        try:
            outcome: ProductionRetrievalOutcome = await execute_production_retrieval(
                ProductionRetrievalRequest(search_request=search_req),
                search_port=self._search_port,
                text_embedding_port=self._text_embedding_port,
                eligibility_verifier=self._eligibility_verifier,
            )
        except Exception:
            elapsed_ms = max(0, int(round((time.monotonic() - start_time) * 1000.0)))
            return cast(
                RetrievalCaseResult,
                CASE_RESULT_ADAPTER.validate_python(
                    {
                        "schema_id": "rag-eval.case-result",
                        "schema_version": "1.0.0",
                        "run_id": str(request.run_id),
                        "case_id": request.case.case_id,
                        "dataset_code": dataset_code,
                        "dataset_version": dataset_version,
                        "partition": request.case.partition.value
                        if hasattr(request.case.partition, "value")
                        else str(request.case.partition),
                        "task_type": "RETRIEVAL",
                        "input_sha256": input_sha,
                        "execution_status": "ERROR",
                        "decision_status": None,
                        "failure_codes": ["RETRIEVAL_UNEXPECTED_ERROR"],
                        "retrieved_evidence_ids": [],
                        "selected_evidence_ids": [],
                        "actual_claim_ids": None,
                        "actual_citation_evidence_ids": None,
                        "actual_rule_ids": None,
                        "actual_scope_codes": None,
                        "actual_response_level": None,
                        "actual_safety_disposition": None,
                        "actual_execution_status": None,
                        "actual_release_decision": None,
                        "actual_fallback_code": None,
                        "actual_provider_invocation": None,
                        "actual_retrieval_invocation": True,
                        "actual_publication_allowed": None,
                        "actual_sections": None,
                        "omitted_sections": None,
                        "risk_level": None,
                        "answer_sha256": None,
                        "latency_ms": elapsed_ms,
                        "input_token_count": None,
                        "output_token_count": None,
                        "estimated_cost": None,
                    }
                ),
            )

        elapsed_ms = max(0, int(round((time.monotonic() - start_time) * 1000.0)))
        self._latencies_ms.append(elapsed_ms)

        if outcome.receipt is not None:
            self._receipts_by_case[request.case.case_id] = outcome.receipt

        if outcome.status != RetrievalExecutionStatus.SUCCEEDED:
            return cast(
                RetrievalCaseResult,
                CASE_RESULT_ADAPTER.validate_python(
                    {
                        "schema_id": "rag-eval.case-result",
                        "schema_version": "1.0.0",
                        "run_id": str(request.run_id),
                        "case_id": request.case.case_id,
                        "dataset_code": dataset_code,
                        "dataset_version": dataset_version,
                        "partition": request.case.partition.value
                        if hasattr(request.case.partition, "value")
                        else str(request.case.partition),
                        "task_type": "RETRIEVAL",
                        "input_sha256": input_sha,
                        "execution_status": "ERROR",
                        "decision_status": None,
                        "failure_codes": ["RETRIEVAL_EXECUTION_FAILED"],
                        "retrieved_evidence_ids": [],
                        "selected_evidence_ids": [],
                        "actual_claim_ids": None,
                        "actual_citation_evidence_ids": None,
                        "actual_rule_ids": None,
                        "actual_scope_codes": None,
                        "actual_response_level": None,
                        "actual_safety_disposition": None,
                        "actual_execution_status": None,
                        "actual_release_decision": None,
                        "actual_fallback_code": None,
                        "actual_provider_invocation": None,
                        "actual_retrieval_invocation": True,
                        "actual_publication_allowed": None,
                        "actual_sections": None,
                        "omitted_sections": None,
                        "risk_level": None,
                        "answer_sha256": None,
                        "latency_ms": elapsed_ms,
                        "input_token_count": None,
                        "output_token_count": None,
                        "estimated_cost": None,
                    }
                ),
            )

        # Collect Pre-Gate Hits (Top 5)
        raw_hits: tuple[ProductionSearchHit, ...] = ()
        if outcome.search_success is not None:
            if mode == RetrievalExecutionMode.LEXICAL_ONLY:
                raw_hits = outcome.search_success.lexical_hits
            elif mode == RetrievalExecutionMode.DENSE_ONLY:
                raw_hits = outcome.search_success.dense_hits
            else:
                raw_hits = outcome.search_success.hybrid_hits

        # Deduplicate while preserving order, up to 5
        pre_gate_ids: list[str] = []
        for h in raw_hits:
            doc_id = h.provenance.external_document_id
            if doc_id not in pre_gate_ids:
                pre_gate_ids.append(doc_id)
            if len(pre_gate_ids) >= 5:
                break

        # Collect Gate Selected Hits
        selected_ids: list[str] = []
        if isinstance(outcome.gate_outcome, EvidenceGateSuccess):
            for h in outcome.gate_outcome.selected_hits:
                doc_id = h.provenance.external_document_id
                if doc_id not in selected_ids:
                    selected_ids.append(doc_id)
                if len(selected_ids) >= 5:
                    break

        return cast(
            RetrievalCaseResult,
            CASE_RESULT_ADAPTER.validate_python(
                {
                    "schema_id": "rag-eval.case-result",
                    "schema_version": "1.0.0",
                    "run_id": str(request.run_id),
                    "case_id": request.case.case_id,
                    "dataset_code": dataset_code,
                    "dataset_version": dataset_version,
                    "partition": request.case.partition.value
                    if hasattr(request.case.partition, "value")
                    else str(request.case.partition),
                    "task_type": "RETRIEVAL",
                    "input_sha256": input_sha,
                    "execution_status": "COMPLETED",
                    "decision_status": "N/A",
                    "failure_codes": [],
                    "retrieved_evidence_ids": list(pre_gate_ids),
                    "selected_evidence_ids": list(selected_ids),
                    "actual_claim_ids": None,
                    "actual_citation_evidence_ids": None,
                    "actual_rule_ids": None,
                    "actual_scope_codes": None,
                    "actual_response_level": None,
                    "actual_safety_disposition": None,
                    "actual_execution_status": None,
                    "actual_release_decision": None,
                    "actual_fallback_code": None,
                    "actual_provider_invocation": None,
                    "actual_retrieval_invocation": True,
                    "actual_publication_allowed": None,
                    "actual_sections": None,
                    "omitted_sections": None,
                    "risk_level": None,
                    "answer_sha256": None,
                    "latency_ms": elapsed_ms,
                    "input_token_count": None,
                    "output_token_count": None,
                    "estimated_cost": None,
                }
            ),
        )

    def get_latency_summary(self) -> LatencySummary:
        if not self._latencies_ms:
            return LatencySummary(count=0, min_ms=0, median_ms=0, p95_ms=0, max_ms=0)
        sorted_latencies = sorted(self._latencies_ms)
        n = len(sorted_latencies)
        min_v = sorted_latencies[0]
        max_v = sorted_latencies[-1]
        med_v = sorted_latencies[n // 2]
        p95_idx = min(n - 1, int(math.ceil(0.95 * n)) - 1)
        p95_v = sorted_latencies[p95_idx]
        return LatencySummary(
            count=n,
            min_ms=min_v,
            median_ms=med_v,
            p95_ms=p95_v,
            max_ms=max_v,
        )

    def get_actual_retrieval_receipts(self) -> ActualRetrievalReceipts:
        receipt_hashes = tuple(
            r.artifact_ref.content_sha256 for r in self._receipts_by_case.values() if r.artifact_ref is not None
        )
        embedding_shas = tuple(
            r.query_embedding_sha256 for r in self._receipts_by_case.values() if r.query_embedding_sha256 is not None
        )
        return ActualRetrievalReceipts(
            search_receipt_hashes=receipt_hashes,
            query_embedding_shas=embedding_shas,
            retrieval_config_hash=self._retrieval_config.artifact_ref.content_sha256,
            adapter_artifact_ref=self._adapter_artifact_ref,
            latency_summary=self.get_latency_summary(),
        )


class ActualAdapterRegistry:
    def __init__(self, adapter: ActualRetrievalEvaluationAdapter) -> None:
        self._adapter = adapter

    def resolve(self, adapter_id: str) -> ActualRetrievalEvaluationAdapter | None:
        if adapter_id in {"knowledge-evidence-retrieval.actual.v1", "actual-retrieval.v1"}:
            return self._adapter
        return None


def build_actual_adapter_registry(  # noqa: C901
    resolved: ResolvedDevExecution,
    *,
    engine: object | None = None,
    search_port: EvidenceSearchPort | None = None,
    text_embedding_port: TextEmbeddingPort | None = None,
    eligibility_verifier: ProductionEvidenceEligibilityVerifierPort | None = None,
    synthetic_index_path: Path | None = None,
) -> ActualAdapterRegistry:
    import asyncio
    import os
    from typing import Any
    from uuid import uuid4

    from ai_worker.adapters.postgresql_evidence_eligibility import PostgreSqlEvidenceEligibilityVerifier
    from ai_worker.adapters.postgresql_evidence_search import PostgresqlEvidenceSearchAdapter
    from ai_worker.tasks.evaluation.actual_retrieval_index import (
        ACTUAL_RETRIEVAL_ADAPTER_REF,
        bootstrap_dev_knowledge_index,
    )
    from ai_worker.tasks.rag.evidence_search import (
        VersionedDenseSearchConfiguration,
        VersionedLexicalSearchConfiguration,
    )

    session_factory: Any = None
    if engine is None and (search_port is None or eligibility_verifier is None):
        try:
            from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

            from ai_worker.core import get_config

            worker_config = get_config()
            engine = create_async_engine(worker_config.database_url, hide_parameters=True)
            session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        except Exception:
            engine = None
            session_factory = None
    elif engine is not None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        session_factory = async_sessionmaker(cast(Any, engine), expire_on_commit=False, autoflush=False)

    if search_port is None and session_factory is not None:
        search_port = PostgresqlEvidenceSearchAdapter(
            session_factory, ImmutableArtifactRef("postgresql-evidence-search-adapter", "1.0.0", "a" * 64)
        )

    if eligibility_verifier is None and session_factory is not None:
        eligibility_verifier = PostgreSqlEvidenceEligibilityVerifier(session_factory)

    if text_embedding_port is None:
        if os.environ.get("OPENAI_API_KEY"):
            from openai import AsyncOpenAI

            from ai_worker.adapters.openai_text_embedding import OpenAITextEmbeddingAdapter

            text_embedding_port = OpenAITextEmbeddingAdapter(
                client=AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"]),
                adapter_artifact_ref=ImmutableArtifactRef("openai-text-embedding-adapter", "1.0.0", "e" * 64),
            )
        else:
            text_embedding_port = None

    if synthetic_index_path is None:
        synthetic_index_path = (
            resolved.repository_root
            / "evals/retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json"
        )

    if engine is not None:
        url = getattr(engine, "url", None)

        def _run_bootstrap():
            async def _inner():
                if url is not None:
                    from sqlalchemy.ext.asyncio import create_async_engine

                    bootstrap_engine = create_async_engine(url, hide_parameters=True)
                    try:
                        return await bootstrap_dev_knowledge_index(
                            bootstrap_engine,
                            text_embedding_port=text_embedding_port,
                            synthetic_index_path=synthetic_index_path,
                        )
                    finally:
                        await bootstrap_engine.dispose()
                else:
                    return await bootstrap_dev_knowledge_index(
                        engine,
                        text_embedding_port=text_embedding_port,
                        synthetic_index_path=synthetic_index_path,
                    )

            return asyncio.run(_inner())

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                summary = executor.submit(_run_bootstrap).result()
        else:
            summary = _run_bootstrap()

        knowledge_index_id = summary.knowledge_index_id
        source_snapshot_id = summary.source_snapshot_id
        source_snapshot_member_ids = summary.source_snapshot_member_ids
        evidence_index_ref = summary.evidence_index_ref
    else:
        knowledge_index_id = uuid4()
        source_snapshot_id = uuid4()
        source_snapshot_member_ids = (uuid4(),)
        evidence_index_ref = ImmutableArtifactRef("rag-knowledge-index-dev-v1", "1.0.0", "0" * 64)

    lexical_config = VersionedLexicalSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("lexical_search_config", "1.0", "a" * 64),
    )
    dense_config = VersionedDenseSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("dense_search_config", "1.0", "b" * 64),
    )
    expected_adapter_ref = getattr(
        text_embedding_port,
        "_adapter_artifact_ref",
        ImmutableArtifactRef("openai-text-embedding-adapter", "1.0.0", "e" * 64),
    )
    retrieval_config = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef("retrieval_config", "1.0", "c" * 64),
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
        lexical_config=lexical_config,
        dense_config=dense_config,
        expected_query_embedding_adapter_ref=expected_adapter_ref,
    )

    assert search_port is not None
    assert eligibility_verifier is not None
    adapter = ActualRetrievalEvaluationAdapter(
        search_port=search_port,
        text_embedding_port=text_embedding_port,
        eligibility_verifier=eligibility_verifier,
        filter_snapshot_ref=ImmutableArtifactRef("filter_snapshot", "1.0", "0" * 64),
        evidence_index_ref=evidence_index_ref,
        knowledge_index_id=knowledge_index_id,
        allowed_source_snapshot_ids=(source_snapshot_id,),
        allowed_source_snapshot_member_ids=source_snapshot_member_ids,
        retrieval_config=retrieval_config,
        adapter_artifact_ref=ACTUAL_RETRIEVAL_ADAPTER_REF,
    )
    return ActualAdapterRegistry(adapter)
