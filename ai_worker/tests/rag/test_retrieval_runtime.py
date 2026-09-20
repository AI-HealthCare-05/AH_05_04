from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from typing import Any
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
    PersistedHitInput,
    PersistedRetrievalRunReceipt,
    PersistedSignalInput,
    PersistedTerminalReplayPayload,
    compute_hit_manifest_hash,
    compute_receipt_hash,
    compute_signal_manifest_hash,
)
from ai_worker.tasks.rag.retrieval_run import (
    canonical_json_bytes as retrieval_run_canonical_json_bytes,
)
from ai_worker.tasks.rag.retrieval_runtime import (
    HYBRID_RETRIEVE_NODE_ID,
    PRODUCTION_SEARCH_RECEIPT_PROJECTION_VERSION,
    PRODUCTION_SEARCH_RECEIPT_VERSION,
    RETRIEVAL_SELECTION_MANIFEST_PROJECTION_VERSION,
    HybridRetrieveRequest,
    ProductionRetrievalRequest,
    RetrievalExecutionStatus,
    _make_terminal_replay_payload,
    _terminal_replay_outcome,
    compute_production_search_receipt,
    compute_selection_manifest_hash,
    execute_hybrid_retrieve,
    execute_production_retrieval,
    production_search_receipt_projection,
    selection_manifest_projection,
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
    def __init__(
        self,
        resumed_receipt: PersistedRetrievalRunReceipt | None = None,
        resumed_payload: PersistedTerminalReplayPayload | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.resumed_receipt = resumed_receipt
        self.run_id = uuid4()
        self.resumed_payload = resumed_payload
        self.last_finalize_req: FinalizeRetrievalRunRequest | None = None

    async def begin_run(self, request: BeginRetrievalRunRequest) -> BeginRetrievalRunOutcome:
        self.calls.append("begin_run")
        if self.resumed_receipt is not None:
            return BeginRetrievalRunSuccess(
                run_id=self.run_id,
                is_resumed=True,
                existing_receipt=self.resumed_receipt,
                existing_terminal_replay_payload=self.resumed_payload,
            )
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
            )
            if self.hits
            else (),
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

    assert run_store.last_finalize_req.terminal_replay_payload is not None


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
    sr = _make_dummy_search_request(RetrievalExecutionMode.LEXICAL_ONLY)
    selected_hit = _make_dummy_hit(1, uuid4())
    search_receipt = compute_production_search_receipt(
        variant="RET-L",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code=EvidenceGateReason.ELIGIBLE.value,
        query_fingerprint=sr.query_fingerprint,
        filter_snapshot_ref=sr.execution_binding.filter_snapshot_ref,
        evidence_index_ref=sr.execution_binding.evidence_index_ref,
        retrieval_config_ref=sr.execution_binding.retrieval_config.artifact_ref,
        adapter_artifact_ref=ImmutableArtifactRef("adapter", "1.0", "8" * 64),
        query_embedding_sha256=None,
        signal_manifest_sha256="9" * 64,
        hit_manifest_sha256="a" * 64,
        selection_manifest_sha256=compute_selection_manifest_hash((selected_hit,)),
    )
    replay_payload = _make_terminal_replay_payload(
        search_receipt,
        EvidenceGateSuccess(selected_hits=(selected_hit,)),
    )
    existing_receipt = replace(
        existing_receipt,
        search_receipt_hash=search_receipt.artifact_ref.content_sha256,
        diagnostic_code=EvidenceGateReason.ELIGIBLE.value,
        selected_count=1,
    )
    search_port = RecordingSearchPort(hits=())
    run_store = RecordingRunStore(resumed_receipt=existing_receipt, resumed_payload=replay_payload)
    verifier = RecordingVerifier()

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
    assert outcome.search_receipt == search_receipt
    assert isinstance(outcome.gate_outcome, EvidenceGateSuccess)
    assert outcome.gate_outcome.selected_hits == (selected_hit,)
    assert run_store.calls == ["begin_run"]
    assert search_port.calls == []
    assert verifier.calls == []


def test_terminal_replay_fails_closed_when_payload_does_not_match_persisted_receipt() -> None:
    search_request = _make_dummy_search_request(RetrievalExecutionMode.LEXICAL_ONLY)
    selected_hit = _make_dummy_hit(1, uuid4())
    search_receipt = compute_production_search_receipt(
        variant="RET-L",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code=EvidenceGateReason.ELIGIBLE.value,
        query_fingerprint=search_request.query_fingerprint,
        filter_snapshot_ref=search_request.execution_binding.filter_snapshot_ref,
        evidence_index_ref=search_request.execution_binding.evidence_index_ref,
        retrieval_config_ref=search_request.execution_binding.retrieval_config.artifact_ref,
        adapter_artifact_ref=ImmutableArtifactRef("adapter", "1.0", "8" * 64),
        query_embedding_sha256=None,
        signal_manifest_sha256="9" * 64,
        hit_manifest_sha256="a" * 64,
        selection_manifest_sha256=compute_selection_manifest_hash((selected_hit,)),
    )
    replay_payload = _make_terminal_replay_payload(
        search_receipt,
        EvidenceGateSuccess(selected_hits=(selected_hit,)),
    )
    mismatched_receipt = PersistedRetrievalRunReceipt(
        run_id=uuid4(),
        job_id=uuid4(),
        node_id=HYBRID_RETRIEVE_NODE_ID,
        variant=search_receipt.variant,
        status="COMPLETED",
        query_digest=search_receipt.query_fingerprint.digest,
        retrieval_configuration_hash=search_receipt.retrieval_config_ref.content_sha256,
        source_manifest_hash="f" * 64,
        receipt_hash="0" * 64,
        total_signals=0,
        total_hits=1,
        selected_count=1,
        signal_manifest_hash="s" * 64,
        hit_manifest_hash="h" * 64,
        search_receipt_hash="f" * 64,
        diagnostic_code=EvidenceGateReason.ELIGIBLE.value,
    )

    outcome = _terminal_replay_outcome(
        BeginRetrievalRunSuccess(
            run_id=mismatched_receipt.run_id,
            is_resumed=True,
            existing_receipt=mismatched_receipt,
            existing_terminal_replay_payload=replay_payload,
        )
    )

    assert outcome.status == RetrievalExecutionStatus.DEPENDENCY_ERROR
    assert outcome.persisted_receipt is None
    assert outcome.search_receipt is None
    assert outcome.gate_outcome.reason == EvidenceGateReason.INVALID_BINDING


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


@pytest.mark.asyncio
async def test_embedding_identity_aligns_with_openai_text_embedding_adapter() -> None:
    from ai_worker.adapters.openai_text_embedding import (
        EXPECTED_DIMENSION,
        EXPECTED_MODEL_REF,
        EXPECTED_MODEL_VERSION,
    )
    from ai_worker.tasks.rag.retrieval_runtime import (
        PRODUCTION_EMBEDDING_DIMENSION,
        PRODUCTION_EMBEDDING_MODEL_REF,
        PRODUCTION_EMBEDDING_MODEL_VERSION,
    )

    # 1. Exact constant alignment
    assert PRODUCTION_EMBEDDING_MODEL_REF == EXPECTED_MODEL_REF == "openai:text-embedding-3-large"
    assert PRODUCTION_EMBEDDING_MODEL_VERSION == EXPECTED_MODEL_VERSION == "text-embedding-3-large"
    assert PRODUCTION_EMBEDDING_DIMENSION == EXPECTED_DIMENSION == 1536

    # 2. Verify runtime passes these exact values to embed()
    class RecordingEmbedder:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def embed(
            self,
            text: SensitiveText,
            *,
            model_ref: str,
            model_version: str,
            dimension: int,
        ) -> TextEmbeddingSuccess:
            self.calls.append(
                {
                    "text": text,
                    "model_ref": model_ref,
                    "model_version": model_version,
                    "dimension": dimension,
                }
            )
            return TextEmbeddingSuccess(
                embedding=SensitiveVector((1.0,) + (0.0,) * (dimension - 1)),
                adapter_artifact_ref=ImmutableArtifactRef("openai_embed", "1.0", "e" * 64),
            )

    embedder = RecordingEmbedder()
    verifier = RecordingVerifier()
    search_port = RecordingSearchPort(hits=())
    sr = _make_dummy_search_request(RetrievalExecutionMode.DENSE_ONLY)

    outcome = await execute_production_retrieval(
        ProductionRetrievalRequest(search_request=sr),
        search_port=search_port,
        text_embedding_port=embedder,  # type: ignore[arg-type]
        eligibility_verifier=verifier,
    )

    assert outcome.status == RetrievalExecutionStatus.SUCCEEDED
    assert len(embedder.calls) == 1
    call = embedder.calls[0]
    assert call["model_ref"] == EXPECTED_MODEL_REF
    assert call["model_version"] == EXPECTED_MODEL_VERSION
    assert call["dimension"] == EXPECTED_DIMENSION


def test_retrieval_run_canonical_json_bytes_conforms_to_rfc8785_utf16_ordering() -> None:
    payload = {"\ue000": 1, "\U00010000": 2, "a": 3}
    # RFC 8785 UTF-16 code unit order: "a" (0x0061), "\U00010000" (surrogates 0xD800 0xDC00), "\ue000" (0xE000)
    expected = '{"a":3,"𐀀":2,"":1}'.encode()
    assert retrieval_run_canonical_json_bytes(payload) == expected


def test_selection_manifest_projection_structure_and_invariants() -> None:
    chunk_1 = uuid4()
    chunk_2 = uuid4()
    hit1 = _make_dummy_hit(1, chunk_1)
    hit2 = _make_dummy_hit(2, chunk_2)

    proj = selection_manifest_projection([hit2, hit1])
    assert isinstance(proj, dict)
    assert proj["projection_version"] == RETRIEVAL_SELECTION_MANIFEST_PROJECTION_VERSION
    assert RETRIEVAL_SELECTION_MANIFEST_PROJECTION_VERSION == "retrieval-selection-manifest-v2"

    selections = proj["selections"]
    assert isinstance(selections, list)
    assert len(selections) == 2
    item0 = selections[0]
    item1 = selections[1]
    assert isinstance(item0, dict)
    assert isinstance(item1, dict)
    # Sorted strictly by fusion_rank ascending
    assert item0["final_rank"] == 1
    assert item1["final_rank"] == 2

    expected_keys = {
        "knowledge_index_id",
        "index_code",
        "index_version",
        "index_configuration_hash",
        "knowledge_chunk_id",
        "source_snapshot_id",
        "source_snapshot_member_id",
        "source_code",
        "source_version",
        "canonical_checksum",
        "external_document_id",
        "chunk_index",
        "locator",
        "content_sha256",
        "canonicalization_spec_version",
        "normalization_version",
        "final_rank",
    }
    assert set(item0.keys()) == expected_keys
    assert set(item1.keys()) == expected_keys
    assert item0["content_sha256"] == hit1.provenance.content_hash

    # Order invariance: reversing input hits produces identical manifest digest
    hash_forward = compute_selection_manifest_hash([hit1, hit2])
    hash_reversed = compute_selection_manifest_hash([hit2, hit1])
    assert hash_forward == hash_reversed


def test_selection_manifest_projection_fails_closed_on_invalid_fusion_rank() -> None:
    chunk_1 = uuid4()
    chunk_2 = uuid4()

    # 1. Non-positive fusion_rank (0 or negative) raises ValueError
    invalid_hit_zero = replace(_make_dummy_hit(1, chunk_1), fusion_rank=0)
    with pytest.raises(ValueError, match="fusion_rank"):
        selection_manifest_projection([invalid_hit_zero])

    invalid_hit_neg = replace(_make_dummy_hit(1, chunk_1), fusion_rank=-1)
    with pytest.raises(ValueError, match="fusion_rank"):
        selection_manifest_projection([invalid_hit_neg])

    # 2. Duplicate fusion_rank raises ValueError
    hit1 = _make_dummy_hit(1, chunk_1)
    hit2_dup_rank = replace(_make_dummy_hit(2, chunk_2), fusion_rank=1)
    with pytest.raises(ValueError, match="fusion_rank"):
        selection_manifest_projection([hit1, hit2_dup_rank])


@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: replace(p, knowledge_index_id=uuid4()),
        lambda p: replace(p, index_code="OTHER_IDX"),
        lambda p: replace(p, index_version="2.0"),
        lambda p: replace(p, index_configuration_hash="e" * 64),
        lambda p: replace(p, knowledge_chunk_id=uuid4()),
        lambda p: replace(p, source_snapshot_id=uuid4()),
        lambda p: replace(p, source_snapshot_member_id=uuid4()),
        lambda p: replace(p, source_code="OTHER_SRC"),
        lambda p: replace(p, source_version="2.0"),
        lambda p: replace(p, canonical_checksum="9" * 64),
        lambda p: replace(p, external_document_id="doc-999"),
        lambda p: replace(p, chunk_index=99),
        lambda p: replace(p, locator="loc-mutated"),
        lambda p: replace(p, content_hash="8" * 64),
        lambda p: replace(p, canonicalization_spec_version="v2"),
        lambda p: replace(p, normalization_version="v2"),
    ],
)
def test_selection_manifest_projection_provenance_field_sensitivity(mutator: Any) -> None:
    hit = _make_dummy_hit(1, uuid4())
    original_hash = compute_selection_manifest_hash([hit])

    mutated_prov = mutator(hit.provenance)
    mutated_hit = replace(hit, provenance=mutated_prov)
    mutated_hash = compute_selection_manifest_hash([mutated_hit])

    assert mutated_hash != original_hash


def test_selection_manifest_projection_rank_and_version_sensitivity() -> None:
    hit = _make_dummy_hit(1, uuid4())
    original_hash = compute_selection_manifest_hash([hit])

    # Mutating fusion_rank changes final_rank in selection
    rank_mutated_hit = replace(hit, fusion_rank=2)
    assert compute_selection_manifest_hash([rank_mutated_hit]) != original_hash


def test_compute_production_search_receipt_cutover_to_v2() -> None:
    receipt = compute_production_search_receipt(
        variant="RET-H",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code="OK",
        query_fingerprint=QueryFingerprint("sha256", "v1", "0" * 64),
        filter_snapshot_ref=ImmutableArtifactRef("filter_snapshot", "1.0", "f" * 64),
        evidence_index_ref=ImmutableArtifactRef("knowledge_index", "1.0", "e" * 64),
        retrieval_config_ref=ImmutableArtifactRef("retrieval_config", "1.0", "3" * 64),
        adapter_artifact_ref=ImmutableArtifactRef("adapter", "1.0", "a" * 64),
        query_embedding_sha256="d" * 64,
        signal_manifest_sha256="1" * 64,
        hit_manifest_sha256="2" * 64,
        selection_manifest_sha256="3" * 64,
    )
    assert receipt.artifact_ref.artifact_code == "production_search_receipt"
    assert receipt.artifact_ref.version == "2.0"
    assert receipt.artifact_ref.version == PRODUCTION_SEARCH_RECEIPT_VERSION

    # Verify projection structure and version
    proj = production_search_receipt_projection(
        variant=receipt.variant,
        status=receipt.retrieval_execution_status,
        diagnostic_code=receipt.diagnostic_code,
        query_fingerprint=receipt.query_fingerprint,
        filter_snapshot_ref=receipt.filter_snapshot_ref,
        evidence_index_ref=receipt.evidence_index_ref,
        retrieval_config_ref=receipt.retrieval_config_ref,
        adapter_artifact_ref=receipt.adapter_artifact_ref,
        query_embedding_sha256=receipt.query_embedding_sha256,
        signal_manifest_sha256=receipt.signal_manifest_sha256,
        hit_manifest_sha256=receipt.hit_manifest_sha256,
        selection_manifest_sha256=receipt.selection_manifest_sha256,
    )
    assert isinstance(proj, dict)
    assert proj["projection_version"] == PRODUCTION_SEARCH_RECEIPT_PROJECTION_VERSION
    assert proj["projection_version"] == "production-search-receipt-v2"
    assert proj["adapter_artifact_ref"] == {"artifact_code": "adapter", "content_sha256": "a" * 64, "version": "1.0"}
    assert proj["query_fingerprint"] == {"algorithm": "sha256", "digest": "0" * 64, "key_version": "v1"}
    assert proj["filter_snapshot_ref"] == {
        "artifact_code": "filter_snapshot",
        "content_sha256": "f" * 64,
        "version": "1.0",
    }
    assert proj["evidence_index_ref"] == {
        "artifact_code": "knowledge_index",
        "content_sha256": "e" * 64,
        "version": "1.0",
    }
    assert proj["retrieval_config_ref"] == {
        "artifact_code": "retrieval_config",
        "content_sha256": "3" * 64,
        "version": "1.0",
    }


def test_production_search_receipt_field_tampering_sensitivity() -> None:
    base_kwargs: dict[str, object] = {
        "variant": "RET-H",
        "status": RetrievalExecutionStatus.SUCCEEDED,
        "diagnostic_code": "OK",
        "query_fingerprint": QueryFingerprint("sha256", "v1", "0" * 64),
        "filter_snapshot_ref": ImmutableArtifactRef("filter_snapshot", "1.0", "f" * 64),
        "evidence_index_ref": ImmutableArtifactRef("knowledge_index", "1.0", "e" * 64),
        "retrieval_config_ref": ImmutableArtifactRef("retrieval_config", "1.0", "3" * 64),
        "adapter_artifact_ref": ImmutableArtifactRef("adapter", "1.0", "a" * 64),
        "query_embedding_sha256": "d" * 64,
        "signal_manifest_sha256": "1" * 64,
        "hit_manifest_sha256": "2" * 64,
        "selection_manifest_sha256": "3" * 64,
    }
    baseline_receipt = compute_production_search_receipt(**base_kwargs)  # type: ignore[arg-type]
    baseline_hash = baseline_receipt.artifact_ref.content_sha256

    tampered_cases = [
        # variant
        ("variant", "RET-L"),
        # status
        ("status", RetrievalExecutionStatus.DEPENDENCY_ERROR),
        # diagnostic_code
        ("diagnostic_code", "FAIL_GATE"),
        # query_fingerprint subfields
        ("query_fingerprint", QueryFingerprint("blake2b", "v1", "0" * 64)),
        ("query_fingerprint", QueryFingerprint("sha256", "v2", "0" * 64)),
        ("query_fingerprint", QueryFingerprint("sha256", "v1", "9" * 64)),
        # filter_snapshot_ref subfields
        ("filter_snapshot_ref", ImmutableArtifactRef("other_code", "1.0", "f" * 64)),
        ("filter_snapshot_ref", ImmutableArtifactRef("filter_snapshot", "2.0", "f" * 64)),
        ("filter_snapshot_ref", ImmutableArtifactRef("filter_snapshot", "1.0", "9" * 64)),
        # evidence_index_ref subfields
        ("evidence_index_ref", ImmutableArtifactRef("other_index", "1.0", "e" * 64)),
        ("evidence_index_ref", ImmutableArtifactRef("knowledge_index", "2.0", "e" * 64)),
        ("evidence_index_ref", ImmutableArtifactRef("knowledge_index", "1.0", "9" * 64)),
        # retrieval_config_ref subfields
        ("retrieval_config_ref", ImmutableArtifactRef("other_config", "1.0", "3" * 64)),
        ("retrieval_config_ref", ImmutableArtifactRef("retrieval_config", "2.0", "3" * 64)),
        ("retrieval_config_ref", ImmutableArtifactRef("retrieval_config", "1.0", "9" * 64)),
        # adapter_artifact_ref subfields
        ("adapter_artifact_ref", ImmutableArtifactRef("other_adapter", "1.0", "a" * 64)),
        ("adapter_artifact_ref", ImmutableArtifactRef("adapter", "2.0", "a" * 64)),
        ("adapter_artifact_ref", ImmutableArtifactRef("adapter", "1.0", "9" * 64)),
        # query_embedding_sha256
        ("query_embedding_sha256", None),
        ("query_embedding_sha256", "9" * 64),
        # manifests
        ("signal_manifest_sha256", "9" * 64),
        ("hit_manifest_sha256", "9" * 64),
        ("selection_manifest_sha256", "9" * 64),
    ]

    for key, tampered_val in tampered_cases:
        modified_kwargs = dict(base_kwargs)
        modified_kwargs[key] = tampered_val
        tampered_receipt = compute_production_search_receipt(**modified_kwargs)  # type: ignore[arg-type]
        assert tampered_receipt.artifact_ref.content_sha256 != baseline_hash, (
            f"Tampering {key} to {tampered_val!r} did not alter receipt hash!"
        )


def test_legacy_digest_golden_regression_frozen_constants() -> None:
    # Frozen legacy expected values computed with historical codebase before canonical JCS alignment
    frozen_legacy_signal_hash = "80949196c75ff5de674d05d4971c35c540e941ca51750d05914a9524170825e7"
    frozen_legacy_hit_hash = "1f15571ed69526a78f7d4f341a746e55b2a0f368efc6700866b24fb345d2314b"
    frozen_legacy_receipt_hash = "094833a6c8ce15863990257d143c55c6626ac879a3b09d9adf41d2a8b2b7947e"

    chunk_id = UUID("11111111-1111-1111-1111-111111111111")
    sig = PersistedSignalInput(
        method="EXACT",
        raw_rank=1,
        raw_score=Decimal("1.000000000000000000"),
        knowledge_chunk_id=chunk_id,
        score_projection_version="v1",
    )
    hit = PersistedHitInput(
        knowledge_chunk_id=chunk_id,
        rrf_rank=1,
        rrf_score=Decimal("0.016393442622950820"),
        rrf_score_numerator="1",
        rrf_score_denominator="61",
        final_rank=1,
        selected=True,
        lexical_rank=1,
        dense_rank=None,
        rerank_score=None,
    )

    sig_hash = compute_signal_manifest_hash([sig])
    hit_hash = compute_hit_manifest_hash([hit])

    assert sig_hash == frozen_legacy_signal_hash
    assert hit_hash == frozen_legacy_hit_hash

    receipt_hash = compute_receipt_hash(
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        job_id=UUID("33333333-3333-3333-3333-333333333333"),
        node_id="hybrid_retrieve",
        variant="RET-H",
        status="COMPLETED",
        query_digest="4" * 64,
        retrieval_configuration_hash="5" * 64,
        source_manifest_hash="6" * 64,
        search_receipt_hash="7" * 64,
        total_signals=1,
        total_hits=1,
        selected_count=1,
        signal_manifest_hash=sig_hash,
        hit_manifest_hash=hit_hash,
    )
    assert receipt_hash == frozen_legacy_receipt_hash


def test_legacy_persisted_retrieval_run_replay_compatibility() -> None:
    frozen_legacy_signal_hash = "80949196c75ff5de674d05d4971c35c540e941ca51750d05914a9524170825e7"
    frozen_legacy_hit_hash = "1f15571ed69526a78f7d4f341a746e55b2a0f368efc6700866b24fb345d2314b"
    frozen_legacy_receipt_hash = "094833a6c8ce15863990257d143c55c6626ac879a3b09d9adf41d2a8b2b7947e"

    recomputed = compute_receipt_hash(
        run_id=UUID("22222222-2222-2222-2222-222222222222"),
        job_id=UUID("33333333-3333-3333-3333-333333333333"),
        node_id="hybrid_retrieve",
        variant="RET-H",
        status="COMPLETED",
        query_digest="4" * 64,
        retrieval_configuration_hash="5" * 64,
        source_manifest_hash="6" * 64,
        search_receipt_hash="7" * 64,
        total_signals=1,
        total_hits=1,
        selected_count=1,
        signal_manifest_hash=frozen_legacy_signal_hash,
        hit_manifest_hash=frozen_legacy_hit_hash,
    )
    assert recomputed == frozen_legacy_receipt_hash
