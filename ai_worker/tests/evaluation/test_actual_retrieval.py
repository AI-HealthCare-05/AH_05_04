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
