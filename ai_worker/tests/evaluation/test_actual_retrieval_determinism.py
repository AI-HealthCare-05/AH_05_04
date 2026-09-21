from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal
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
        assert r1.latency_ms is not None and r1.latency_ms >= 0
        assert r2.latency_ms is not None and r2.latency_ms >= 0


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


# ---------------------------------------------------------------------------
# Run Bundle level determinism comparator (#273 actual DEV evidence)
# ---------------------------------------------------------------------------

from dataclasses import replace as _dataclass_replace  # noqa: E402

from ai_worker.tasks.evaluation.actual_retrieval_determinism import (  # noqa: E402
    compare_actual_retrieval_runs,
    latency_observation,
)
from ai_worker.tasks.evaluation.comparison import load_published_run_bundle  # noqa: E402
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError  # noqa: E402
from ai_worker.tasks.evaluation.manifest import (  # noqa: E402
    ArtifactDraft,
    build_artifact_draft,
    finalize_artifacts,
)
from ai_worker.tasks.evaluation.schemas.artifacts import CaseResult  # noqa: E402
from ai_worker.tests.evaluation.test_result_manifest import (  # noqa: E402
    RUN_ID_A,
    RUN_ID_B,
    TIME_A,
    TIME_B,
    retrieval_run_material,
)


def _bundle_draft(variant: str = "RET-L", *, run_id: str = RUN_ID_A, started_at: str = TIME_A) -> ArtifactDraft:
    return build_artifact_draft(retrieval_run_material(variant, run_id=run_id, started_at=started_at))


def _publish_bundle(root: Path, draft: ArtifactDraft, *, completed_at: str = TIME_B):
    artifacts = finalize_artifacts(draft, b"safe retrieval report\n", completed_at=completed_at)
    run_root = root / draft.report_data.run_id
    run_root.mkdir()
    for name, payload in artifacts.files.items():
        (run_root / name).write_bytes(payload)
    return load_published_run_bundle(root, draft.report_data.run_id)


def _pair(root: Path, *, second: ArtifactDraft | None = None, started_at: str = TIME_A, completed_at: str = TIME_B):
    first = _publish_bundle(root, _bundle_draft(run_id=RUN_ID_A))
    other = second if second is not None else _bundle_draft(run_id=RUN_ID_B, started_at=started_at)
    return first, _publish_bundle(root, other, completed_at=completed_at)


def _with_cases(draft: ArtifactDraft, cases: Sequence[CaseResult]) -> ArtifactDraft:
    return _dataclass_replace(draft, cases=tuple(cases))


def test_comparator_reports_equality_when_only_run_id_differs(tmp_path: Path) -> None:
    first, second = _pair(tmp_path)

    report = compare_actual_retrieval_runs(first, second)

    assert report.run_ids == (RUN_ID_A, RUN_ID_B)
    assert report.retrieval_semantic_equal is True
    assert report.metric_equal is True
    assert report.failure_equal is True
    assert report.retrieval_semantic_hashes[0] == report.retrieval_semantic_hashes[1]
    assert report.compared_case_count == len(first.cases)


def test_comparator_ignores_started_and_completed_timestamps(tmp_path: Path) -> None:
    first, second = _pair(
        tmp_path, started_at="2026-02-02T00:00:00.000000Z", completed_at="2026-02-02T01:00:00.000000Z"
    )

    report = compare_actual_retrieval_runs(first, second)

    assert first.run.started_at != second.run.started_at
    assert report.retrieval_semantic_equal is True
    assert report.metric_equal is True


def test_comparator_ignores_latency_but_observes_it(tmp_path: Path) -> None:
    second_draft = _bundle_draft(run_id=RUN_ID_B)
    second_draft = _with_cases(
        second_draft,
        [case.model_copy(update={"latency_ms": (case.latency_ms or 0) + 137}) for case in second_draft.cases],
    )
    first, second = _pair(tmp_path, second=second_draft)

    report = compare_actual_retrieval_runs(first, second)

    assert report.retrieval_semantic_equal is True
    assert report.metric_equal is True
    assert report.latency_observations[0] != report.latency_observations[1]


def test_comparator_detects_retrieved_evidence_order_change(tmp_path: Path) -> None:
    second_draft = _bundle_draft(run_id=RUN_ID_B)
    target = second_draft.cases[0]
    assert target.retrieved_evidence_ids is not None
    reordered = tuple(reversed(target.retrieved_evidence_ids))
    second_draft = _with_cases(
        second_draft,
        [target.model_copy(update={"retrieved_evidence_ids": reordered}), *second_draft.cases[1:]],
    )
    first, second = _pair(tmp_path, second=second_draft)

    report = compare_actual_retrieval_runs(first, second)

    assert report.retrieval_semantic_equal is False
    assert target.case_id in report.mismatched_case_ids


def test_comparator_detects_selected_evidence_change(tmp_path: Path) -> None:
    second_draft = _bundle_draft(run_id=RUN_ID_B)
    target = second_draft.cases[0]
    assert target.selected_evidence_ids is not None
    truncated = target.selected_evidence_ids[:1]
    second_draft = _with_cases(
        second_draft,
        [target.model_copy(update={"selected_evidence_ids": truncated}), *second_draft.cases[1:]],
    )
    first, second = _pair(tmp_path, second=second_draft)

    report = compare_actual_retrieval_runs(first, second)

    assert report.retrieval_semantic_equal is False
    assert target.case_id in report.mismatched_case_ids


def test_comparator_detects_failure_code_change(tmp_path: Path) -> None:
    second_draft = _bundle_draft(run_id=RUN_ID_B)
    target = second_draft.cases[0]
    second_draft = _with_cases(
        second_draft,
        [
            target.model_copy(update={"failure_codes": ("BLOCKED_BY_QUERY_EMBEDDING_CREDENTIAL",)}),
            *second_draft.cases[1:],
        ],
    )
    first, second = _pair(tmp_path, second=second_draft)

    report = compare_actual_retrieval_runs(first, second)

    assert report.retrieval_semantic_equal is False
    assert target.case_id in report.mismatched_case_ids


def _bumped(value: object) -> str:
    return format(Decimal(str(value)) + Decimal("0.01"), "f")


def test_comparator_detects_metric_value_change(tmp_path: Path) -> None:
    first, second = _pair(tmp_path)
    target = next(metric for metric in second.metrics.metrics if metric.metric_value is not None)
    mutated = tuple(
        metric.model_copy(update={"metric_value": _bumped(metric.metric_value)}) if metric is target else metric
        for metric in second.metrics.metrics
    )
    changed = _dataclass_replace(second, metrics=second.metrics.model_copy(update={"metrics": mutated}))

    report = compare_actual_retrieval_runs(first, changed)

    assert report.retrieval_semantic_equal is True
    assert report.metric_equal is False
    assert report.mismatched_metric_keys


def test_comparator_refuses_when_dataset_manifest_sha_differs(tmp_path: Path) -> None:
    first, second = _pair(tmp_path)
    tampered = _dataclass_replace(
        second,
        run=second.run.model_copy(update={"dataset_manifest_sha256": "f" * 64}),
    )

    with pytest.raises(EvaluationValidationError) as error:
        compare_actual_retrieval_runs(first, tampered)
    assert error.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_comparator_refuses_when_resolved_evaluation_config_hash_differs(tmp_path: Path) -> None:
    first, second = _pair(tmp_path)
    tampered = _dataclass_replace(
        second,
        run=second.run.model_copy(update={"resolved_evaluation_config_hash": "b" * 64}),
    )

    with pytest.raises(EvaluationValidationError):
        compare_actual_retrieval_runs(first, tampered)


def test_comparator_refuses_when_variant_differs(tmp_path: Path) -> None:
    first, second = _pair(tmp_path)
    tampered = _dataclass_replace(second, run=second.run.model_copy(update={"variant_id": "RET-D"}))

    with pytest.raises(EvaluationValidationError):
        compare_actual_retrieval_runs(first, tampered)


def test_comparator_refuses_when_knowledge_index_ref_differs(tmp_path: Path) -> None:
    first, second = _pair(tmp_path)
    ref = {"id": "rag-natural-language-retrieval-dev-synthetic-index", "version": "1.0.0", "hash": "1" * 64}

    with pytest.raises(EvaluationValidationError):
        compare_actual_retrieval_runs(first, second, knowledge_index_refs=(ref, {**ref, "hash": "2" * 64}))


def test_comparator_refuses_when_case_sets_differ(tmp_path: Path) -> None:
    first, second = _pair(tmp_path)
    truncated = _dataclass_replace(second, cases=second.cases[:-1])

    with pytest.raises(EvaluationValidationError):
        compare_actual_retrieval_runs(first, truncated)


def test_latency_observation_handles_missing_and_present_samples() -> None:
    empty = latency_observation(())
    assert empty.count == 0 and empty.median is None


def test_comparator_refuses_duplicate_case_ids_that_the_loader_accepts(tmp_path: Path) -> None:
    """A duplicated case_id must not be collapsed into a single comparison row.

    The published Run Bundle loader does not enforce case_id uniqueness. If the
    comparator keys cases by ID, an earlier duplicate carrying different
    retrieval results is silently dropped, so two runs can be reported equal
    while their stable projection hashes disagree.
    """

    def _duplicated(draft: ArtifactDraft, ranked: tuple[str, ...]) -> ArtifactDraft:
        target = draft.cases[0]
        assert target.retrieved_evidence_ids is not None
        shadowed = target.model_copy(update={"retrieved_evidence_ids": ranked, "selected_evidence_ids": ranked})
        return _with_cases(draft, [shadowed, *draft.cases])

    first_draft = _duplicated(_bundle_draft(run_id=RUN_ID_A), ("ev-shadowed-first",))
    second_draft = _duplicated(_bundle_draft(run_id=RUN_ID_B), ("ev-shadowed-second",))

    first = _publish_bundle(tmp_path, first_draft)
    second = _publish_bundle(tmp_path, second_draft)

    # The loader accepts both bundles, and only the duplicated row differs.
    duplicated_id = first_draft.cases[0].case_id
    assert [case.case_id for case in first.cases].count(duplicated_id) == 2
    assert first.cases[0].retrieved_evidence_ids != second.cases[0].retrieved_evidence_ids

    with pytest.raises(EvaluationValidationError) as error:
        compare_actual_retrieval_runs(first, second)
    assert error.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID
