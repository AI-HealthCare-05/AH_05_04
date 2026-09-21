from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter

from ai_worker.tasks.evaluation.actual_retrieval import (
    ActualAdapterRegistry,
    ActualRetrievalEvaluationAdapter,
)
from ai_worker.tasks.evaluation.actual_retrieval_index import DeterministicFakeEmbeddingAdapter
from ai_worker.tasks.evaluation.loaders import EvaluationCaseContract
from ai_worker.tasks.evaluation.runner import AdapterRequest
from ai_worker.tasks.evaluation.schemas.common import TaskType
from ai_worker.tasks.rag.evidence_rank_fusion import FractionReceipt, StableCoordinate
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
)
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchRequest,
    EvidenceSearchSuccess,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    ProductionSearchMethod,
    ProductionSearchSignal,
    RetrievalExecutionMode,
    VersionedDenseSearchConfiguration,
    VersionedEvidenceRetrievalConfiguration,
    VersionedLexicalSearchConfiguration,
)
from ai_worker.tasks.rag.production_evidence_gate import (
    PostSearchEligibilityOutcome,
    PostSearchEligibilityRequest,
    PostSearchEligibilitySuccess,
    PreSearchEligibilityOutcome,
    PreSearchEligibilityRequest,
    PreSearchEligibilitySuccess,
)


def _make_dummy_hit(rank: int, external_id: str, snapshot_id: UUID, member_id: UUID) -> ProductionSearchHit:
    chunk_id = uuid4()
    coord = StableCoordinate("SRC", "1.0", external_id, rank)
    prov = ProductionEvidenceProvenance(
        knowledge_index_id=uuid4(),
        index_code="IDX",
        index_version="1.0",
        index_configuration_hash="d" * 64,
        knowledge_chunk_id=chunk_id,
        evidence_key=f"synthetic-evidence-{rank}",
        source_snapshot_id=snapshot_id,
        source_snapshot_member_id=member_id,
        source_code=coord.source_code,
        source_version=coord.source_version,
        canonical_checksum="2" * 64,
        external_document_id=external_id,
        chunk_index=coord.chunk_index,
        locator="loc",
        content_hash="3" * 64,
        canonicalization_spec_version="v1",
        normalization_version="v1",
    )
    return ProductionSearchHit(
        provenance=prov,
        coordinate=coord,
        exact_hit=True,
        observed_trigram_score=None,
        observed_fts_score=None,
        observed_dense_score=None,
        lexical_rank=rank,
        dense_rank=rank,
        fusion_rank=rank,
        fraction_receipt=FractionReceipt("1", "61"),
        is_eligible_for_future_reranker=True,
    )


class FakeSearchPort:
    def __init__(self, hits: tuple[ProductionSearchHit, ...]) -> None:
        self.hits = hits

    async def search(self, request: EvidenceSearchRequest) -> EvidenceSearchSuccess:
        mode = request.execution_binding.retrieval_config.execution_mode
        return EvidenceSearchSuccess(
            request=request,
            adapter_artifact_ref=ImmutableArtifactRef("adapter", "1.0", "h" * 64),
            lexical_hits=self.hits if mode != RetrievalExecutionMode.DENSE_ONLY else (),
            dense_hits=self.hits if mode != RetrievalExecutionMode.LEXICAL_ONLY else (),
            hybrid_hits=self.hits if mode == RetrievalExecutionMode.HYBRID_RRF else (),
            signals=(
                ProductionSearchSignal(
                    provenance=self.hits[0].provenance,
                    method=ProductionSearchMethod.EXACT,
                    raw_rank=1,
                    observed_score="1.000000000000000000",
                ),
            )
            if self.hits
            else (),
        )


class FakeEligibilityVerifier:
    def __init__(self, eligible_chunks: frozenset[UUID] | None = None) -> None:
        self.eligible_chunks = eligible_chunks

    async def pre_search(self, request: PreSearchEligibilityRequest) -> PreSearchEligibilityOutcome:
        return PreSearchEligibilitySuccess(is_eligible=True)

    async def post_search(
        self,
        request: PostSearchEligibilityRequest,
        hits: Sequence[ProductionSearchHit],
    ) -> PostSearchEligibilityOutcome:
        chunks = (
            self.eligible_chunks
            if self.eligible_chunks is not None
            else frozenset(h.provenance.knowledge_chunk_id for h in hits)
        )
        return PostSearchEligibilitySuccess(eligible_chunk_ids=chunks)


def _build_test_adapter(
    mode: RetrievalExecutionMode = RetrievalExecutionMode.HYBRID_RRF,
) -> ActualRetrievalEvaluationAdapter:
    snapshot_id = uuid4()
    member_id = uuid4()
    hits = tuple(_make_dummy_hit(i, f"ev-nlr-00{i}", snapshot_id, member_id) for i in range(1, 6))

    lex_cfg = VersionedLexicalSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("lex_cfg", "1.0", "1" * 64),
    )
    dense_cfg = (
        VersionedDenseSearchConfiguration(
            artifact_ref=ImmutableArtifactRef("dense_cfg", "1.0", "2" * 64),
        )
        if mode != RetrievalExecutionMode.LEXICAL_ONLY
        else None
    )
    ret_cfg = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef("ret_cfg", "1.0", "a" * 64),
        execution_mode=mode,
        lexical_config=lex_cfg,
        dense_config=dense_cfg,
        expected_query_embedding_adapter_ref=ImmutableArtifactRef(
            "openai:text-embedding-3-large", "text-embedding-3-large", "e" * 64
        )
        if dense_cfg
        else None,
    )
    return ActualRetrievalEvaluationAdapter(
        search_port=FakeSearchPort(hits),
        text_embedding_port=DeterministicFakeEmbeddingAdapter(),
        eligibility_verifier=FakeEligibilityVerifier(),
        filter_snapshot_ref=ImmutableArtifactRef("filter_snapshot", "1.0", "f" * 64),
        evidence_index_ref=ImmutableArtifactRef("evidence_index", "1.0", "i" * 64),
        knowledge_index_id=uuid4(),
        allowed_source_snapshot_ids=(snapshot_id,),
        allowed_source_snapshot_member_ids=(member_id,),
        retrieval_config=ret_cfg,
        adapter_artifact_ref=ImmutableArtifactRef("adapter_ref", "1.0", "d" * 64),
    )


def _make_adapter_request(case_id: str = "rag-nlr-dev-001", variant_id: str = "RET-H") -> AdapterRequest:
    repo_root = Path(__file__).resolve().parents[3]
    case_path = repo_root / f"evals/retrieval/cases/rag-natural-language-retrieval-dev-v1/{case_id}.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8"))
    case: EvaluationCaseContract = TypeAdapter(EvaluationCaseContract).validate_python(case_data)

    return AdapterRequest(
        run_id="00000000-0000-0000-0000-000000000001",
        case=case,
        task_type=TaskType.RETRIEVAL,
        input_sha256=case_data["input_sha256"],
        case_resource_sha256="c" * 64,
        variant_id=variant_id,
        variant_manifest_hash="m" * 64,
    )


@pytest.mark.asyncio
async def test_actual_retrieval_adapter_ret_l() -> None:
    adapter = _build_test_adapter(RetrievalExecutionMode.LEXICAL_ONLY)
    req = _make_adapter_request(variant_id="RET-L")

    res = await adapter.execute(req)
    assert res.actual_retrieval_invocation is True
    assert res.decision_status == "N/A"
    assert res.latency_ms is not None and res.latency_ms >= 0
    assert len(res.retrieved_evidence_ids) == 5
    assert res.retrieved_evidence_ids[0] == "ev-nlr-001"

    receipts = adapter.get_actual_retrieval_receipts()
    assert len(receipts.search_receipt_hashes) == 1
    assert len(receipts.query_embedding_shas) == 0


@pytest.mark.asyncio
async def test_actual_retrieval_adapter_ret_d() -> None:
    adapter = _build_test_adapter(RetrievalExecutionMode.DENSE_ONLY)
    req = _make_adapter_request(variant_id="RET-D")

    res = await adapter.execute(req)
    assert res.actual_retrieval_invocation is True
    assert res.decision_status == "N/A"
    assert len(res.retrieved_evidence_ids) == 5

    receipts = adapter.get_actual_retrieval_receipts()
    assert len(receipts.search_receipt_hashes) == 1
    assert len(receipts.query_embedding_shas) == 1


@pytest.mark.asyncio
async def test_actual_retrieval_adapter_ret_h() -> None:
    adapter = _build_test_adapter(RetrievalExecutionMode.HYBRID_RRF)
    req = _make_adapter_request(variant_id="RET-H")

    res = await adapter.execute(req)
    assert res.actual_retrieval_invocation is True
    assert res.decision_status == "N/A"
    assert len(res.retrieved_evidence_ids) == 5
    assert len(res.selected_evidence_ids) == 5

    latency = adapter.get_latency_summary()
    assert latency.count == 1
    assert latency.min_ms >= 0
    assert latency.max_ms >= latency.min_ms


def test_actual_adapter_registry_resolution() -> None:
    adapter = _build_test_adapter(RetrievalExecutionMode.HYBRID_RRF)
    reg = ActualAdapterRegistry(adapter)

    assert reg.resolve("knowledge-evidence-retrieval.actual.v1") is adapter
    assert reg.resolve("actual-retrieval.v1") is adapter
    assert reg.resolve("unknown") is None


@pytest.mark.asyncio
async def test_execute_ret_h_smoke_transaction_orchestration() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    from ai_worker.tasks.evaluation.ret_h_smoke import execute_ret_h_smoke_transaction

    fake_request = MagicMock()
    fake_search_port = MagicMock()
    fake_text_embedding_port = MagicMock()
    fake_run_store = MagicMock()
    fake_eligibility_verifier = MagicMock()

    with patch(
        "ai_worker.tasks.evaluation.ret_h_smoke.execute_hybrid_retrieve",
        new=AsyncMock(return_value="smoke_outcome"),
    ) as mock_exec:
        outcome = await execute_ret_h_smoke_transaction(
            request=fake_request,
            search_port=fake_search_port,
            text_embedding_port=fake_text_embedding_port,
            run_store=fake_run_store,
            eligibility_verifier=fake_eligibility_verifier,
        )

    assert outcome == "smoke_outcome"
    mock_exec.assert_awaited_once_with(
        fake_request,
        search_port=fake_search_port,
        text_embedding_port=fake_text_embedding_port,
        run_store=fake_run_store,
        eligibility_verifier=fake_eligibility_verifier,
    )


# ---------------------------------------------------------------------------
# Variant execution-mode configuration sealing (#273 actual DEV execution)
# ---------------------------------------------------------------------------

from ai_worker.tasks.evaluation.actual_retrieval import (  # noqa: E402
    sealed_retrieval_config,
    variant_retrieval_config,
)
from ai_worker.tasks.rag.evidence_search import validate_retrieval_configuration  # noqa: E402


def _placeholder_retrieval_config() -> VersionedEvidenceRetrievalConfiguration:
    return VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef("retrieval_config", "1.0", "c" * 64),
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
        lexical_config=VersionedLexicalSearchConfiguration(
            artifact_ref=ImmutableArtifactRef("lexical_search_config", "1.0", "a" * 64),
        ),
        dense_config=VersionedDenseSearchConfiguration(
            artifact_ref=ImmutableArtifactRef("dense_search_config", "1.0", "b" * 64),
        ),
        expected_query_embedding_adapter_ref=ImmutableArtifactRef("openai-text-embedding-adapter", "1.0.0", "e" * 64),
    )


def test_sealed_retrieval_config_binds_each_artifact_ref_to_its_canonical_hash() -> None:
    sealed = sealed_retrieval_config(_placeholder_retrieval_config())

    assert sealed.is_hash_valid()
    assert sealed.lexical_config.is_hash_valid()
    assert sealed.dense_config is not None and sealed.dense_config.is_hash_valid()


def test_lexical_only_variant_config_drops_dense_inputs_and_validates() -> None:
    config = variant_retrieval_config(_placeholder_retrieval_config(), RetrievalExecutionMode.LEXICAL_ONLY)

    assert config.execution_mode is RetrievalExecutionMode.LEXICAL_ONLY
    assert config.dense_config is None
    assert config.expected_query_embedding_adapter_ref is None
    assert validate_retrieval_configuration(config) is None


@pytest.mark.parametrize(
    "mode",
    [RetrievalExecutionMode.DENSE_ONLY, RetrievalExecutionMode.HYBRID_RRF],
)
def test_dense_and_hybrid_variant_configs_keep_dense_inputs_and_validate(mode: RetrievalExecutionMode) -> None:
    config = variant_retrieval_config(_placeholder_retrieval_config(), mode)

    assert config.execution_mode is mode
    assert config.dense_config is not None
    assert config.expected_query_embedding_adapter_ref is not None
    assert validate_retrieval_configuration(config) is None


def test_placeholder_configuration_is_rejected_before_sealing() -> None:
    assert validate_retrieval_configuration(_placeholder_retrieval_config()) is not None


# ---------------------------------------------------------------------------
# Issue #738: Authoritative query embedding adapter identity & placeholder removal
# ---------------------------------------------------------------------------


def test_actual_retrieval_query_composition_expects_canonical_ref() -> None:
    from unittest.mock import MagicMock

    from ai_worker.adapters.openai_text_embedding import OPENAI_TEXT_EMBEDDING_ADAPTER_REF
    from ai_worker.tasks.evaluation.actual_retrieval import build_actual_adapter_registry

    mock_resolved = MagicMock()
    mock_search_port = MagicMock()
    mock_eligibility = MagicMock()

    registry = build_actual_adapter_registry(
        mock_resolved,
        search_port=mock_search_port,
        eligibility_verifier=mock_eligibility,
        text_embedding_port=None,
    )
    adapter = registry.resolve("knowledge-evidence-retrieval.actual.v1")
    assert adapter is not None
    assert adapter._retrieval_config.expected_query_embedding_adapter_ref == OPENAI_TEXT_EMBEDDING_ADAPTER_REF


def test_production_like_fallback_e64_is_not_execution_identity() -> None:
    from unittest.mock import MagicMock

    from ai_worker.tasks.evaluation.actual_retrieval import build_actual_adapter_registry

    mock_resolved = MagicMock()
    mock_search_port = MagicMock()
    mock_eligibility = MagicMock()

    # Pass text_embedding_port without _adapter_artifact_ref attribute
    plain_port = MagicMock(spec=[])
    registry = build_actual_adapter_registry(
        mock_resolved,
        search_port=mock_search_port,
        eligibility_verifier=mock_eligibility,
        text_embedding_port=plain_port,
    )
    adapter = registry.resolve("knowledge-evidence-retrieval.actual.v1")
    assert adapter is not None
    assert adapter._retrieval_config.expected_query_embedding_adapter_ref is not None
    assert adapter._retrieval_config.expected_query_embedding_adapter_ref.content_sha256 != "e" * 64


def test_actual_retrieval_search_composition_uses_canonical_ref_and_no_placeholder() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch
    from uuid import uuid4

    from ai_worker.adapters.postgresql_evidence_search import POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF
    from ai_worker.tasks.evaluation.actual_retrieval import build_actual_adapter_registry
    from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef

    mock_resolved = MagicMock()
    mock_engine = MagicMock(spec=[])
    mock_eligibility = MagicMock()

    summary_mock = MagicMock(
        knowledge_index_id=uuid4(),
        source_snapshot_id=uuid4(),
        source_snapshot_member_ids=(uuid4(),),
        evidence_index_ref=ImmutableArtifactRef("idx", "1.0", "0" * 64),
    )

    with patch(
        "ai_worker.tasks.evaluation.actual_retrieval_index.bootstrap_dev_knowledge_index",
        new=AsyncMock(return_value=summary_mock),
    ):
        with patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=MagicMock()):
            registry = build_actual_adapter_registry(
                mock_resolved,
                engine=mock_engine,
                search_port=None,
                eligibility_verifier=mock_eligibility,
                text_embedding_port=None,
            )
            adapter = registry.resolve("knowledge-evidence-retrieval.actual.v1")
            assert adapter is not None
            search_port = adapter._search_port
            assert hasattr(search_port, "_adapter_artifact_ref")
            assert search_port._adapter_artifact_ref == POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF
            assert search_port._adapter_artifact_ref.content_sha256 != "a" * 64


@pytest.mark.asyncio
async def test_mismatched_query_embedding_adapter_ref_fails_closed() -> None:
    from typing import Any

    from ai_worker.adapters.openai_text_embedding import OPENAI_TEXT_EMBEDDING_ADAPTER_REF
    from ai_worker.tasks.rag.evidence_search import (
        SensitiveVector,
        validate_search_request,
    )
    from ai_worker.tasks.rag.text_embedding import TextEmbeddingPort, TextEmbeddingSuccess

    class MismatchedEmbeddingPort(TextEmbeddingPort):
        async def embed(self, text: Any, **kwargs: Any) -> TextEmbeddingSuccess:
            return TextEmbeddingSuccess(
                embedding=SensitiveVector([0.1] * 1536),
                adapter_artifact_ref=ImmutableArtifactRef("openai-text-embedding-adapter", "1.0.0", "f" * 64),
            )

    class ValidatingSearchPort(FakeSearchPort):
        async def search(self, request: EvidenceSearchRequest) -> Any:
            failure = validate_search_request(request)
            if failure is not None:
                return failure
            return await super().search(request)

    snapshot_id = uuid4()
    member_id = uuid4()
    hits = tuple(_make_dummy_hit(i, f"ev-nlr-00{i}", snapshot_id, member_id) for i in range(1, 6))

    lex_cfg = VersionedLexicalSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("lex_cfg", "1.0", "1" * 64),
    )
    dense_cfg = VersionedDenseSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("dense_cfg", "1.0", "2" * 64),
    )
    ret_cfg = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef("ret_cfg", "1.0", "a" * 64),
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
        lexical_config=lex_cfg,
        dense_config=dense_cfg,
        expected_query_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
    )
    adapter = ActualRetrievalEvaluationAdapter(
        search_port=ValidatingSearchPort(hits),
        text_embedding_port=MismatchedEmbeddingPort(),
        eligibility_verifier=FakeEligibilityVerifier(),
        filter_snapshot_ref=ImmutableArtifactRef("filter_snapshot", "1.0", "f" * 64),
        evidence_index_ref=ImmutableArtifactRef("evidence_index", "1.0", "i" * 64),
        knowledge_index_id=uuid4(),
        allowed_source_snapshot_ids=(snapshot_id,),
        allowed_source_snapshot_member_ids=(member_id,),
        retrieval_config=ret_cfg,
        adapter_artifact_ref=ImmutableArtifactRef("adapter_ref", "1.0", "d" * 64),
    )
    req = _make_adapter_request(variant_id="RET-H")
    res = await adapter.execute(req)
    assert res.execution_status == "ERROR"
    assert "RETRIEVAL_EXECUTION_FAILED" in res.failure_codes
