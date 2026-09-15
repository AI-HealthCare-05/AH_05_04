from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import TypeAdapter

from ai_worker.tasks.evaluation.actual_retrieval import ActualRetrievalEvaluationAdapter
from ai_worker.tasks.evaluation.actual_retrieval_index import DeterministicFakeEmbeddingAdapter
from ai_worker.tasks.evaluation.loaders import EvaluationCaseContract
from ai_worker.tasks.evaluation.runner import AdapterRequest
from ai_worker.tasks.evaluation.schemas.common import TaskType
from ai_worker.tasks.rag.evidence_rank_fusion import FractionReceipt, StableCoordinate
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
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
    coord = StableCoordinate("SRC", "1.0", external_id, rank)
    prov = ProductionEvidenceProvenance(
        knowledge_index_id=uuid4(),
        index_code="IDX",
        index_version="1.0",
        index_configuration_hash="d" * 64,
        knowledge_chunk_id=uuid4(),
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


class DeterministicFakeSearchPort:
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


class DeterministicEligibilityVerifier:
    async def pre_search(self, request: PreSearchEligibilityRequest) -> PreSearchEligibilityOutcome:
        return PreSearchEligibilitySuccess(is_eligible=True)

    async def post_search(
        self,
        request: PostSearchEligibilityRequest,
        hits: Sequence[ProductionSearchHit],
    ) -> PostSearchEligibilityOutcome:
        return PostSearchEligibilitySuccess(eligible_chunk_ids=frozenset(h.provenance.knowledge_chunk_id for h in hits))


def _build_adapter() -> ActualRetrievalEvaluationAdapter:
    snapshot_id = UUID("17800000-0000-4000-8000-000000000104")
    member_id = UUID("17800000-0000-4000-8000-000000000105")
    hits = tuple(_make_dummy_hit(i + 1, f"doc_{i + 1}", snapshot_id, member_id) for i in range(5))

    lex_cfg = VersionedLexicalSearchConfiguration(ImmutableArtifactRef("lex", "1.0", "l" * 64))
    dense_cfg = VersionedDenseSearchConfiguration(ImmutableArtifactRef("dense", "1.0", "d" * 64))
    ret_cfg = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef("ret", "1.0", "r" * 64),
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
        lexical_config=lex_cfg,
        dense_config=dense_cfg,
        expected_query_embedding_adapter_ref=ImmutableArtifactRef(
            "openai:text-embedding-3-large", "text-embedding-3-large", "e" * 64
        ),
    )
    return ActualRetrievalEvaluationAdapter(
        search_port=DeterministicFakeSearchPort(hits),
        text_embedding_port=DeterministicFakeEmbeddingAdapter(),
        eligibility_verifier=DeterministicEligibilityVerifier(),
        filter_snapshot_ref=ImmutableArtifactRef("filt", "1.0", "f" * 64),
        evidence_index_ref=ImmutableArtifactRef("idx", "1.0", "i" * 64),
        knowledge_index_id=uuid4(),
        allowed_source_snapshot_ids=(snapshot_id,),
        allowed_source_snapshot_member_ids=(member_id,),
        retrieval_config=ret_cfg,
        adapter_artifact_ref=ImmutableArtifactRef("ada", "1.0", "a" * 64),
    )


def _load_case(case_id: str) -> EvaluationCaseContract:
    repo_root = Path(__file__).resolve().parents[3]
    case_path = repo_root / f"evals/retrieval/cases/rag-natural-language-retrieval-dev-v1/{case_id}.json"
    case_data = json.loads(case_path.read_text(encoding="utf-8"))
    return TypeAdapter(EvaluationCaseContract).validate_python(case_data)


@pytest.mark.asyncio
async def test_consecutive_runs_produce_identical_semantic_results() -> None:
    cases = [
        _load_case("rag-nlr-dev-001"),
        _load_case("rag-nlr-dev-002"),
        _load_case("rag-nlr-dev-003"),
    ]

    adapter_1 = _build_adapter()
    run_1_results = []
    for c in cases:
        req = AdapterRequest(
            run_id="17800000-0000-4000-8000-000000000001",
            case=c,
            task_type=TaskType.RETRIEVAL,
            input_sha256=c.input_sha256,
            case_resource_sha256="c" * 64,
            variant_id="RET-H",
            variant_manifest_hash="m" * 64,
        )
        res = await adapter_1.execute(req)
        run_1_results.append(res)

    adapter_2 = _build_adapter()
    run_2_results = []
    for c in cases:
        req = AdapterRequest(
            run_id="17800000-0000-4000-8000-000000000002",
            case=c,
            task_type=TaskType.RETRIEVAL,
            input_sha256=c.input_sha256,
            case_resource_sha256="c" * 64,
            variant_id="RET-H",
            variant_manifest_hash="m" * 64,
        )
        res = await adapter_2.execute(req)
        run_2_results.append(res)

    assert len(run_1_results) == len(run_2_results) == 3
    for r1, r2 in zip(run_1_results, run_2_results, strict=True):
        # Case ID and execution status match
        assert r1.case_id == r2.case_id
        assert r1.execution_status == r2.execution_status
        assert r1.failure_codes == r2.failure_codes
        # Retrieved and selected evidence IDs match identically
        assert r1.retrieved_evidence_ids == r2.retrieved_evidence_ids
        assert r1.selected_evidence_ids == r2.selected_evidence_ids
        # Latency is positive
        assert r1.latency_ms >= 0
        assert r2.latency_ms >= 0


@pytest.mark.asyncio
async def test_determinism_verifier_fails_closed_on_semantic_mismatch() -> None:
    cases = [_load_case("rag-nlr-dev-001")]

    adapter = _build_adapter()
    req = AdapterRequest(
        run_id="17800000-0000-4000-8000-000000000001",
        case=cases[0],
        task_type=TaskType.RETRIEVAL,
        input_sha256=cases[0].input_sha256,
        case_resource_sha256="c" * 64,
        variant_id="RET-H",
        variant_manifest_hash="m" * 64,
    )
    result = await adapter.execute(req)

    # Injected disturbance: altered retrieved_evidence_ids
    tampered_evidence = list(result.retrieved_evidence_ids) + ["unexpected_doc"]

    with pytest.raises(AssertionError):
        assert result.retrieved_evidence_ids == tampered_evidence
