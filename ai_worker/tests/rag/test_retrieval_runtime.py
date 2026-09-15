from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from ai_worker.tasks.rag.evidence_rank_fusion import FractionReceipt, StableCoordinate
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    QueryFingerprint,
    SensitiveText,
)
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchExecutionBinding,
    EvidenceSearchRequest,
    EvidenceSearchSuccess,
    ProductionEvidenceProvenance,
    ProductionSearchHit,
    ProductionSearchMethod,
    ProductionSearchSignal,
    RetrievalExecutionMode,
    SensitiveVector,
    VersionedDenseSearchConfiguration,
    VersionedEvidenceRetrievalConfiguration,
    VersionedLexicalSearchConfiguration,
)
from ai_worker.tasks.rag.production_evidence_gate import (
    EvidenceGateReason,
    EvidenceGateStatus,
    EvidenceGateSuccess,
    PostSearchEligibilityOutcome,
    PostSearchEligibilityRequest,
    PostSearchEligibilitySuccess,
    PreSearchEligibilityFailure,
    PreSearchEligibilityOutcome,
    PreSearchEligibilityRequest,
    PreSearchEligibilitySuccess,
)
from ai_worker.tasks.rag.retrieval_run import (
    BeginRetrievalRunFailure,
    BeginRetrievalRunFailureReason,
    BeginRetrievalRunOutcome,
    BeginRetrievalRunRequest,
    BeginRetrievalRunSuccess,
    FinalizeRetrievalRunOutcome,
    FinalizeRetrievalRunRequest,
    FinalizeRetrievalRunSuccess,
    PersistedRetrievalRunReceipt,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    HYBRID_RETRIEVE_NODE_ID,
    HybridRetrieveRequest,
    ProductionRetrievalRequest,
    RetrievalExecutionStatus,
    execute_hybrid_retrieve,
    execute_production_retrieval,
)
from ai_worker.tasks.rag.text_embedding import (
    TextEmbeddingFailure,
    TextEmbeddingFailureReason,
    TextEmbeddingSuccess,
)


def _make_dummy_search_request(
    mode: RetrievalExecutionMode = RetrievalExecutionMode.LEXICAL_ONLY,
) -> EvidenceSearchRequest:
    lex_cfg = VersionedLexicalSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("lexical_cfg", "1.0", "1" * 64),
    )
    dense_cfg = (
        VersionedDenseSearchConfiguration(
            artifact_ref=ImmutableArtifactRef("dense_cfg", "1.0", "2" * 64),
        )
        if mode != RetrievalExecutionMode.LEXICAL_ONLY
        else None
    )
    cfg = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef("retrieval_cfg", "1.0", "a" * 64),
        lexical_config=lex_cfg,
        dense_config=dense_cfg,
        expected_query_embedding_adapter_ref=ImmutableArtifactRef("adapter", "1.0", "3" * 64) if dense_cfg else None,
        execution_mode=mode,
    )
    binding = EvidenceSearchExecutionBinding(
        filter_snapshot_ref=ImmutableArtifactRef("filter_snapshot", "1.0", "e" * 64),
        evidence_index_ref=ImmutableArtifactRef("evidence_index", "1.0", "d" * 64),
        knowledge_index_id=uuid4(),
        allowed_source_snapshot_ids=(uuid4(),),
        allowed_source_snapshot_member_ids=(uuid4(),),
        retrieval_config=cfg,
    )
    return EvidenceSearchRequest(
        normalized_query=SensitiveText("아스피린 복용법"),
        query_fingerprint=QueryFingerprint("sha256", "v1", "1" * 64),
        execution_binding=binding,
        query_embedding_receipt=None,
    )


def _make_dummy_hit(rank: int, chunk_id: UUID) -> ProductionSearchHit:
    coord = StableCoordinate("SRC", "1.0", f"doc-{rank}", rank)
    prov = ProductionEvidenceProvenance(
        knowledge_index_id=uuid4(),
        index_code="IDX",
        index_version="1.0",
        index_configuration_hash="d" * 64,
        knowledge_chunk_id=chunk_id,
        source_snapshot_id=uuid4(),
        source_snapshot_member_id=uuid4(),
        source_code=coord.source_code,
        source_version=coord.source_version,
        canonical_checksum="2" * 64,
        external_document_id=coord.external_document_id,
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


class RecordingRunStore:
    def __init__(self, resumed_receipt: PersistedRetrievalRunReceipt | None = None) -> None:
        self.calls: list[str] = []
        self.resumed_receipt = resumed_receipt
        self.run_id = uuid4()
        self.last_finalize_req: FinalizeRetrievalRunRequest | None = None

    async def begin_run(self, request: BeginRetrievalRunRequest) -> BeginRetrievalRunOutcome:
        self.calls.append("begin_run")
        if self.resumed_receipt is not None:
            return BeginRetrievalRunSuccess(run_id=self.run_id, is_resumed=True, existing_receipt=self.resumed_receipt)
        return BeginRetrievalRunSuccess(run_id=self.run_id, is_resumed=False)

    async def finalize_run(self, request: FinalizeRetrievalRunRequest) -> FinalizeRetrievalRunOutcome:
        self.calls.append("finalize_run")
        self.last_finalize_req = request
        receipt = PersistedRetrievalRunReceipt(
            run_id=request.run_id,
            job_id=uuid4(),
            node_id=HYBRID_RETRIEVE_NODE_ID,
            variant="RET-L",
            status=request.status,
            query_digest="1" * 64,
            retrieval_configuration_hash="a" * 64,
            source_manifest_hash="f" * 64,
            receipt_hash="0" * 64,
            total_signals=len(request.signals),
            total_hits=len(request.hits),
            selected_count=sum(1 for h in request.hits if h.selected),
            signal_manifest_hash="s" * 64,
            hit_manifest_hash="h" * 64,
        )
        return FinalizeRetrievalRunSuccess(receipt=receipt)

    async def get_run_receipt(self, run_id: UUID) -> PersistedRetrievalRunReceipt | None:
        return None


class RecordingVerifier:
    def __init__(self, eligible_chunks: frozenset[UUID] | None = None) -> None:
        self.calls: list[str] = []
        self.eligible_chunks = eligible_chunks if eligible_chunks is not None else frozenset()

    async def pre_search(self, request: PreSearchEligibilityRequest) -> PreSearchEligibilityOutcome:
        self.calls.append("pre_search")
        return PreSearchEligibilitySuccess(is_eligible=True)

    async def post_search(
        self,
        request: PostSearchEligibilityRequest,
        hits: Sequence[ProductionSearchHit],
    ) -> PostSearchEligibilityOutcome:
        self.calls.append("post_search")
        return PostSearchEligibilitySuccess(eligible_chunk_ids=self.eligible_chunks)


class RecordingSearchPort:
    def __init__(self, hits: tuple[ProductionSearchHit, ...]) -> None:
        self.calls: list[str] = []
        self.hits = hits

    async def search(self, request: EvidenceSearchRequest) -> EvidenceSearchSuccess:
        self.calls.append("search")
        return EvidenceSearchSuccess(
            request=request,
            adapter_artifact_ref=ImmutableArtifactRef("adapter", "1.0", "h" * 64),
            lexical_hits=self.hits,
            dense_hits=self.hits,
            hybrid_hits=self.hits,
            signals=(
                ProductionSearchSignal(
                    provenance=self.hits[0].provenance,
                    method=ProductionSearchMethod.EXACT,
                    raw_rank=1,
                    observed_score="1.000000000000000000",
                ),
            ) if self.hits else (),
        )


class RecordingEmbeddingPort:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    async def embed(
        self,
        text: SensitiveText,
        *,
        model_ref: str,
        model_version: str,
        dimension: int,
    ) -> TextEmbeddingSuccess | TextEmbeddingFailure:
        self.calls.append("embed")
        if self.fail:
            return TextEmbeddingFailure(reason=TextEmbeddingFailureReason.DEPENDENCY_ERROR)
        vec = SensitiveVector(tuple(0.01 for _ in range(dimension)))
        return TextEmbeddingSuccess(
            embedding=vec,
            adapter_artifact_ref=ImmutableArtifactRef("test_embed", "1.0", "9" * 64),
        )


@pytest.mark.asyncio
async def test_execute_production_retrieval_lexical_success() -> None:
    chunk_1 = uuid4()
    hit = _make_dummy_hit(1, chunk_1)
    search_port = RecordingSearchPort(hits=(hit,))
    verifier = RecordingVerifier(eligible_chunks=frozenset({chunk_1}))
    sr = _make_dummy_search_request(RetrievalExecutionMode.LEXICAL_ONLY)

    req = ProductionRetrievalRequest(search_request=sr)
    outcome = await execute_production_retrieval(
        req,
        search_port=search_port,
        text_embedding_port=None,
        eligibility_verifier=verifier,
    )

    assert outcome.status == RetrievalExecutionStatus.SUCCEEDED
    assert outcome.receipt is not None
    assert outcome.receipt.variant == "RET-L"
    assert outcome.receipt.query_embedding_sha256 is None
    assert isinstance(outcome.gate_outcome, EvidenceGateSuccess)
    assert len(outcome.gate_outcome.selected_hits) == 1
    assert verifier.calls == ["pre_search", "post_search"]
    assert search_port.calls == ["search"]


@pytest.mark.asyncio
async def test_execute_production_retrieval_hybrid_calls_embedding() -> None:
    chunk_1 = uuid4()
    hit = _make_dummy_hit(1, chunk_1)
    search_port = RecordingSearchPort(hits=(hit,))
    embed_port = RecordingEmbeddingPort()
    verifier = RecordingVerifier(eligible_chunks=frozenset({chunk_1}))
    sr = _make_dummy_search_request(RetrievalExecutionMode.HYBRID_RRF)

    req = ProductionRetrievalRequest(search_request=sr)
    outcome = await execute_production_retrieval(
        req,
        search_port=search_port,
        text_embedding_port=embed_port,
        eligibility_verifier=verifier,
    )

    assert outcome.status == RetrievalExecutionStatus.SUCCEEDED
    assert outcome.receipt is not None
    assert outcome.receipt.variant == "RET-H"
    assert outcome.receipt.query_embedding_sha256 is not None
    assert len(outcome.receipt.query_embedding_sha256) == 64
    assert embed_port.calls == ["embed"]
    assert search_port.calls == ["search"]


@pytest.mark.asyncio
async def test_execute_production_retrieval_embedding_failure() -> None:
    chunk_1 = uuid4()
    hit = _make_dummy_hit(1, chunk_1)
    search_port = RecordingSearchPort(hits=(hit,))
    embed_port = RecordingEmbeddingPort(fail=True)
    verifier = RecordingVerifier(eligible_chunks=frozenset({chunk_1}))
    sr = _make_dummy_search_request(RetrievalExecutionMode.HYBRID_RRF)

    req = ProductionRetrievalRequest(search_request=sr)
    outcome = await execute_production_retrieval(
        req,
        search_port=search_port,
        text_embedding_port=embed_port,
        eligibility_verifier=verifier,
    )

    assert outcome.status == RetrievalExecutionStatus.DEPENDENCY_ERROR
    assert outcome.receipt is None
    assert outcome.gate_outcome.reason == EvidenceGateReason.INELIGIBLE


@pytest.mark.asyncio
async def test_execute_hybrid_retrieve_full_lifecycle_persisted() -> None:
    chunk_1 = uuid4()
    hit = _make_dummy_hit(1, chunk_1)
    search_port = RecordingSearchPort(hits=(hit,))
    run_store = RecordingRunStore()
    verifier = RecordingVerifier(eligible_chunks=frozenset({chunk_1}))
    sr = _make_dummy_search_request(RetrievalExecutionMode.LEXICAL_ONLY)

    req = HybridRetrieveRequest(
        job_id=uuid4(),
        execution_context_id=uuid4(),
        prescription_version_id=uuid4(),
        runtime_release_bundle_id=uuid4(),
        runtime_release_bundle_manifest_hash="a" * 64,
        runtime_execution_manifest_id=uuid4(),
        runtime_execution_manifest_hash="b" * 64,
        runtime_guard_decision_ref="guard-ref",
        search_request=sr,
    )

    outcome = await execute_hybrid_retrieve(
        req,
        search_port=search_port,
        text_embedding_port=None,
        run_store=run_store,
        eligibility_verifier=verifier,
    )

    assert outcome.status == RetrievalExecutionStatus.SUCCEEDED
    assert outcome.persisted_receipt is not None
    assert outcome.search_receipt is not None
    assert run_store.calls == ["begin_run", "finalize_run"]
    assert run_store.last_finalize_req is not None
    assert run_store.last_finalize_req.status == "COMPLETED"


@pytest.mark.asyncio
async def test_execute_hybrid_retrieve_fast_path_resumed() -> None:
    existing_receipt = PersistedRetrievalRunReceipt(
        run_id=uuid4(),
        job_id=uuid4(),
        node_id=HYBRID_RETRIEVE_NODE_ID,
        variant="RET-L",
        status="COMPLETED",
        query_digest="1" * 64,
        retrieval_configuration_hash="a" * 64,
        source_manifest_hash="f" * 64,
        receipt_hash="0" * 64,
        total_signals=0,
        total_hits=0,
        selected_count=0,
        signal_manifest_hash="s" * 64,
        hit_manifest_hash="h" * 64,
    )
    search_port = RecordingSearchPort(hits=())
    run_store = RecordingRunStore(resumed_receipt=existing_receipt)
    verifier = RecordingVerifier()
    sr = _make_dummy_search_request(RetrievalExecutionMode.LEXICAL_ONLY)

    req = HybridRetrieveRequest(
        job_id=uuid4(),
        execution_context_id=uuid4(),
        prescription_version_id=uuid4(),
        runtime_release_bundle_id=uuid4(),
        runtime_release_bundle_manifest_hash="a" * 64,
        runtime_execution_manifest_id=uuid4(),
        runtime_execution_manifest_hash="b" * 64,
        runtime_guard_decision_ref="guard-ref",
        search_request=sr,
    )

    outcome = await execute_hybrid_retrieve(
        req,
        search_port=search_port,
        text_embedding_port=None,
        run_store=run_store,
        eligibility_verifier=verifier,
    )

    assert outcome.status == RetrievalExecutionStatus.SUCCEEDED
    assert outcome.persisted_receipt == existing_receipt
    assert run_store.calls == ["begin_run"]
    assert search_port.calls == []


@pytest.mark.asyncio
async def test_execute_production_retrieval_pre_search_failure() -> None:
    search_port = RecordingSearchPort(hits=())

    class FailingPreVerifier(RecordingVerifier):
        async def pre_search(self, request: PreSearchEligibilityRequest) -> PreSearchEligibilityOutcome:
            return PreSearchEligibilityFailure(
                status=EvidenceGateStatus.DEPENDENCY_ERROR,
                reason=EvidenceGateReason.INELIGIBLE,
                message="Index is inactive",
            )

    sr = _make_dummy_search_request(RetrievalExecutionMode.LEXICAL_ONLY)
    req = ProductionRetrievalRequest(search_request=sr)
    outcome = await execute_production_retrieval(
        req,
        search_port=search_port,
        text_embedding_port=None,
        eligibility_verifier=FailingPreVerifier(),
    )

    assert outcome.status == RetrievalExecutionStatus.DEPENDENCY_ERROR
    assert outcome.receipt is None
    assert outcome.gate_outcome.reason == EvidenceGateReason.INELIGIBLE
    assert search_port.calls == []


@pytest.mark.asyncio
async def test_execute_hybrid_retrieve_search_failure_marks_run_failed() -> None:
    class FailingSearchPort:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def search(self, request: EvidenceSearchRequest):
            from ai_worker.tasks.rag.evidence_search import EvidenceSearchFailure, EvidenceSearchFailureReason

            self.calls.append("search")
            return EvidenceSearchFailure(
                reason=EvidenceSearchFailureReason.LEXICAL_DEPENDENCY_ERROR,
            )

    run_store = RecordingRunStore()
    verifier = RecordingVerifier()
    sr = _make_dummy_search_request(RetrievalExecutionMode.LEXICAL_ONLY)

    req = HybridRetrieveRequest(
        job_id=uuid4(),
        execution_context_id=uuid4(),
        prescription_version_id=uuid4(),
        runtime_release_bundle_id=uuid4(),
        runtime_release_bundle_manifest_hash="a" * 64,
        runtime_execution_manifest_id=uuid4(),
        runtime_execution_manifest_hash="b" * 64,
        runtime_guard_decision_ref="guard-ref",
        search_request=sr,
    )

    outcome = await execute_hybrid_retrieve(
        req,
        search_port=FailingSearchPort(),
        text_embedding_port=None,
        run_store=run_store,
        eligibility_verifier=verifier,
    )

    assert outcome.status == RetrievalExecutionStatus.DEPENDENCY_ERROR
    assert outcome.persisted_receipt is None
    assert run_store.calls == ["begin_run", "finalize_run"]
    assert run_store.last_finalize_req is not None
    assert run_store.last_finalize_req.status == "FAILED"


@pytest.mark.asyncio
async def test_execute_hybrid_retrieve_begin_failure() -> None:
    class FailingBeginStore(RecordingRunStore):
        async def begin_run(self, request: BeginRetrievalRunRequest) -> BeginRetrievalRunOutcome:
            return BeginRetrievalRunFailure(
                reason=BeginRetrievalRunFailureReason.CONFLICT,
                message="Active run exists",
            )

    run_store = FailingBeginStore()
    verifier = RecordingVerifier()
    sr = _make_dummy_search_request(RetrievalExecutionMode.LEXICAL_ONLY)

    req = HybridRetrieveRequest(
        job_id=uuid4(),
        execution_context_id=uuid4(),
        prescription_version_id=uuid4(),
        runtime_release_bundle_id=uuid4(),
        runtime_release_bundle_manifest_hash="a" * 64,
        runtime_execution_manifest_id=uuid4(),
        runtime_execution_manifest_hash="b" * 64,
        runtime_guard_decision_ref="guard-ref",
        search_request=sr,
    )

    outcome = await execute_hybrid_retrieve(
        req,
        search_port=RecordingSearchPort(hits=()),
        text_embedding_port=None,
        run_store=run_store,
        eligibility_verifier=verifier,
    )

    assert outcome.status == RetrievalExecutionStatus.VALIDATION_ERROR
    assert outcome.persisted_receipt is None
    assert run_store.calls == []

