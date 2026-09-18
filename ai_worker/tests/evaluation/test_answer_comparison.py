from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from ai_worker.tasks.evaluation.answer_comparison import (
    ORDERED_ANSWER_CONTROLLED_VARIABLE_KEYS,
    AnswerComparisonDeltaBindings,
    AnswerComparisonRunInput,
    AnswerComparisonSetBuildResult,
    AnswerComparisonSupplementalControls,
    AnswerDraftInputBinding,
    build_answer_comparison_set,
    build_answer_pair_comparison,
    compute_comparison_semantic_hash,
    compute_comparison_sha256,
    validate_answer_comparison_set_bundle,
)
from ai_worker.tasks.evaluation.canonical import (
    JsonValue,
    canonical_json_bytes,
    canonical_sha256,
)
from ai_worker.tasks.evaluation.comparison import LoadedRunBundle
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.manifest import semantic_content_hash
from ai_worker.tasks.evaluation.schemas.answer_quality_v1 import (
    CANONICAL_ANSWER_COMPARISON_PAIRS,
    AnswerComparisonPairId,
    AnswerVariantId,
)
from ai_worker.tasks.evaluation.schemas.artifacts import (
    CASE_RESULT_ADAPTER,
    CaseResult,
    ContentManifest,
    MetricResult,
    MetricResults,
    RagEvaluationRun,
    RuntimeEnvironment,
)
from ai_worker.tasks.evaluation.schemas.common import (
    ActorNamespace,
    ActorRef,
    ActorRole,
    DecisionStatus,
    ExecutionStatus,
    ExperimentType,
    ImmutableReference,
    Partition,
    TaskType,
)


def _make_run(
    variant: AnswerVariantId,
    *,
    run_id: str,
    experiment_id: str = "exp-answer-dev-1",
    experiment_type: ExperimentType = ExperimentType.ANSWER_GROUNDING_SAFETY,
    partition: Partition = Partition.DEV,
    execution_status: ExecutionStatus = ExecutionStatus.COMPLETED,
    answer_variant_manifest_hash: str | None = "5" * 64,
    dataset_manifest_sha256: str = "d" * 64,
    partition_manifest_hash: str = "c" * 64,
    resource_set_hash: str = "a" * 64,
    evidence_mapping_manifest_sha256: str = "e" * 64,
    critical_claim_rubric_ref: ImmutableReference | None = None,
    comparison_policy_ref: ImmutableReference | None = None,
    model_config_hash: str = "b" * 64,
    result_content_manifest_hash: str | None = None,
) -> RagEvaluationRun:
    rubric_ref = critical_claim_rubric_ref or ImmutableReference(id="rubric-qa", version="1.0.0", hash="2" * 64)
    policy_ref = comparison_policy_ref or ImmutableReference(id="policy-qa", version="1.0.0", hash="3" * 64)
    return RagEvaluationRun(
        schema_id="rag-eval.run",
        schema_version="1.0.0",
        run_id=run_id,
        experiment_id=experiment_id,
        variant_id=variant.value,
        experiment_type=experiment_type,
        task_types=(TaskType.ANSWER_QUALITY,),
        evaluation_profile_ref=ImmutableReference(id="profile-1", version="1.0.0", hash="1" * 64),
        comparison_policy_ref=policy_ref,
        evaluation_policy_ref=ImmutableReference(id="eval-policy-1", version="1.0.0", hash="4" * 64),
        artifact_schema_set_ref=ImmutableReference(id="schema-set-1", version="1.5.0", hash="5" * 64),
        dataset_code="dataset-qa",
        dataset_version="1.0.0",
        dataset_manifest_sha256=dataset_manifest_sha256,
        resource_set_hash=resource_set_hash,
        evidence_mapping_manifest_sha256=evidence_mapping_manifest_sha256,
        critical_claim_rubric_ref=rubric_ref,
        fixture_git_commit_sha="a" * 40,
        protected_artifact_receipt_ref=None,
        resolved_evaluation_config_hash="6" * 64,
        upstream_contract_manifest_hash="7" * 64,
        retrieval_variant_manifest_hash=None,
        answer_variant_manifest_hash=answer_variant_manifest_hash,
        model_config_hash=model_config_hash,
        prompt_version="prompt-v1",
        evaluated_partitions=(partition,),
        partition_manifest_hash=partition_manifest_hash,
        environment=RuntimeEnvironment.LOCAL,
        runtime_eligible=False,
        candidate_bundle_id=None,
        candidate_bundle_manifest_hash=None,
        candidate_guard_decision_id=None,
        candidate_guard_decision=None,
        required_case_guard_coverage_manifest_hash=None,
        executed_by=ActorRef(
            namespace=ActorNamespace.GITHUB_LOGIN,
            actor_id="ceohwj",
            role=ActorRole.EVALUATION_IMPLEMENTER,
        ),
        started_at="2026-09-18T00:00:00.000000Z",
        completed_at="2026-09-18T00:01:00.000000Z" if execution_status is ExecutionStatus.COMPLETED else None,
        execution_status=execution_status,
        decision_status=DecisionStatus.INCONCLUSIVE if execution_status is ExecutionStatus.COMPLETED else None,
        blocking_execution_statuses=(),
        result_content_manifest_hash=result_content_manifest_hash
        if result_content_manifest_hash is not None
        else ("8" * 64 if execution_status is ExecutionStatus.COMPLETED else None),
    )


def _make_cases(
    run_id: str,
    *,
    answer_sha256: str = "a" * 64,
    input_sha256: str = "1" * 64,
    case_id: str = "case-001",
) -> tuple[CaseResult, ...]:
    return (
        CASE_RESULT_ADAPTER.validate_python(
            {
                "schema_id": "rag-eval.case-result",
                "schema_version": "1.0.0",
                "run_id": run_id,
                "case_id": case_id,
                "dataset_code": "dataset-qa",
                "dataset_version": "1.0.0",
                "task_type": "ANSWER_QUALITY",
                "partition": "DEV",
                "input_sha256": input_sha256,
                "execution_status": "COMPLETED",
                "decision_status": "N/A",
                "failure_codes": [],
                "retrieved_evidence_ids": None,
                "selected_evidence_ids": None,
                "actual_claim_ids": ["claim-1"],
                "actual_citation_evidence_ids": [],
                "actual_rule_ids": None,
                "actual_scope_codes": None,
                "actual_response_level": None,
                "actual_safety_disposition": None,
                "actual_execution_status": None,
                "actual_release_decision": None,
                "actual_fallback_code": None,
                "actual_provider_invocation": None,
                "actual_retrieval_invocation": None,
                "actual_publication_allowed": None,
                "actual_sections": ["section-1"],
                "omitted_sections": [],
                "risk_level": None,
                "answer_sha256": answer_sha256,
                "latency_ms": 0,
                "input_token_count": 0,
                "output_token_count": 0,
                "estimated_cost": "0",
            }
        ),
    )


def _make_metrics(run_id: str, *, value: str = "0.8") -> MetricResults:
    return MetricResults(
        schema_id="rag-eval.metrics",
        schema_version="1.0.0",
        run_id=run_id,
        metrics=(
            MetricResult(
                metric_id="ANSWER_CORRECTNESS",
                metric_version="1.0.0",
                partition=Partition.DEV,
                slice_id="ALL",
                required=False,
                execution_status=ExecutionStatus.COMPLETED,
                decision_status=DecisionStatus.NOT_APPLICABLE,
                sample_case_count=1,
                sample_independent_group_count=1,
                numerator=4,
                denominator=5,
                metric_value=value,
                unit_of_analysis="CLAIM",
                estimator_id="MICRO_RATIO",
                estimator_version="1.0.0",
                independence_unit="case",
                cluster_dimension="case_id",
                ci_lower="0.5",
                ci_upper="1",
                ci_method_id="PERCENTILE_CLUSTER_BOOTSTRAP",
                ci_method_version="1.0.0",
                ci_level="0.95",
                ci_sidedness="TWO_SIDED",
                threshold=None,
                reason_code=None,
            ),
        ),
    )


def _make_bundle(
    variant: AnswerVariantId,
    *,
    run_id: str,
    answer_sha256: str = "a" * 64,
    metric_value: str = "0.8",
    **run_kwargs,
) -> LoadedRunBundle:
    manifest_payload: dict[str, JsonValue] = {
        "schema_id": "rag-eval.content-manifest",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "hash_algorithm": "SHA-256",
        "artifacts": [],
        "artifact_count": 0,
    }
    content_hash = canonical_sha256(manifest_payload, excluded_top_level_keys=frozenset({"manifest_sha256"}))
    manifest_payload["manifest_sha256"] = content_hash
    content_manifest = ContentManifest.model_validate(manifest_payload)

    run_kwargs_copy = dict(run_kwargs)
    run_kwargs_copy.setdefault("result_content_manifest_hash", content_hash)
    run = _make_run(variant, run_id=run_id, **run_kwargs_copy)
    cases = _make_cases(run_id, answer_sha256=answer_sha256)
    metrics = _make_metrics(run_id, value=metric_value)
    suite_results: dict[str, JsonValue] = {
        "schema_id": "rag-eval.suite-results",
        "schema_version": "1.0.0",
        "case_results": [],
    }
    cases_bytes = b"".join(canonical_json_bytes(c.model_dump(mode="json")) + b"\n" for c in cases)
    files = {
        "run.json": canonical_json_bytes(run.model_dump(mode="json")),
        "cases.jsonl": cases_bytes,
        "metrics.json": canonical_json_bytes(metrics.model_dump(mode="json")),
        "suite-results.json": canonical_json_bytes(suite_results),
        "failures.jsonl": b"",
    }
    semantic_hash = semantic_content_hash(files)
    return LoadedRunBundle(
        root=Path(f"/fake/{run_id}"),
        run=run,
        cases=cases,
        metrics=metrics,
        failures=(),
        comparison=None,
        content_manifest=content_manifest,
        files=files,
        semantic_hash=semantic_hash,
    )


def _default_supplemental() -> AnswerComparisonSupplementalControls:
    return AnswerComparisonSupplementalControls(
        input_context_hash="1" * 64,
        prompt_structure_hash="2" * 64,
        parser_hash="3" * 64,
        seed_hash="4" * 64,
        sampling_parameters_hash="5" * 64,
        token_limit_hash="6" * 64,
        timeout_hash="7" * 64,
    )


def _default_delta() -> AnswerComparisonDeltaBindings:
    return AnswerComparisonDeltaBindings(
        retrieval_pipeline_hash="a" * 64,
        source_index_hash="b" * 64,
        runtime_bundle_hash="c" * 64,
        retrieved_evidence_hash="d" * 64,
        final_validator_hash="e" * 64,
        citation_gate_hash="f" * 64,
        safety_gate_hash="0" * 64,
        release_gate_hash="1" * 64,
    )


def _make_run_input(
    variant: AnswerVariantId,
    *,
    run_id: str,
    answer_sha256: str = "a" * 64,
    metric_value: str = "0.8",
    supplemental: AnswerComparisonSupplementalControls | None = None,
    delta: AnswerComparisonDeltaBindings | None = None,
    runner_commit_sha: str = "c" * 40,
    draft_answer_bindings: tuple[AnswerDraftInputBinding, ...] = (),
    **run_kwargs,
) -> AnswerComparisonRunInput:
    bundle = _make_bundle(variant, run_id=run_id, answer_sha256=answer_sha256, metric_value=metric_value, **run_kwargs)
    return AnswerComparisonRunInput(
        bundle=bundle,
        supplemental_controls=supplemental or _default_supplemental(),
        delta_bindings=delta or _default_delta(),
        runner_commit_sha=runner_commit_sha,
        draft_answer_bindings=draft_answer_bindings,
    )


def test_happy_path_exact_three_pairs_and_manifest_bundle() -> None:
    rag_answer_sha = "9" * 64
    ans_base = _make_run_input(
        AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111", metric_value="0.6"
    )
    ans_rag = _make_run_input(
        AnswerVariantId.ANS_RAG,
        run_id="22222222-2222-2222-2222-222222222222",
        answer_sha256=rag_answer_sha,
        metric_value="0.8",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64),
    )
    final_drafts = (AnswerDraftInputBinding(case_id="case-001", draft_answer_sha256=rag_answer_sha),)
    ans_final = _make_run_input(
        AnswerVariantId.ANS_FINAL,
        run_id="33333333-3333-3333-3333-333333333333",
        answer_sha256="8" * 64,
        metric_value="0.9",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64, final_validator_hash="8" * 64),
        draft_answer_bindings=final_drafts,
    )

    gold_ref = ImmutableReference(id="gold-qa", version="1.0.0", hash="0" * 64)
    build_result = build_answer_comparison_set(
        ans_base=ans_base,
        ans_rag=ans_rag,
        ans_final=ans_final,
        gold_manifest_ref=gold_ref,
    )

    assert isinstance(build_result, AnswerComparisonSetBuildResult)
    manifest = build_result.manifest
    assert tuple(entry.pair_id for entry in manifest.pairs) == CANONICAL_ANSWER_COMPARISON_PAIRS

    # Check the 3 comparisons
    for pair_id in CANONICAL_ANSWER_COMPARISON_PAIRS:
        comp = build_result.comparisons[pair_id]
        assert comp.schema_id == "rag-eval.comparison"
        assert comp.schema_version == "1.0.0"
        assert comp.execution_status is ExecutionStatus.COMPLETED
        assert comp.decision_status is DecisionStatus.INCONCLUSIVE
        assert len(comp.controlled_variable_checks) == 14
        assert all(check.matched for check in comp.controlled_variable_checks)
        check_keys = tuple(check.variable_key for check in comp.controlled_variable_checks)
        assert check_keys == ORDERED_ANSWER_CONTROLLED_VARIABLE_KEYS

    # Verify fixed relative paths
    expected_paths = {
        "ANS-BASE--ANS-RAG": "ans-base--ans-rag/comparison.json",
        "ANS-RAG--ANS-FINAL": "ans-rag--ans-final/comparison.json",
        "ANS-BASE--ANS-FINAL": "ans-base--ans-final/comparison.json",
    }
    for entry in manifest.pairs:
        assert entry.relative_path == expected_paths[entry.pair_id]
        comp = build_result.comparisons[entry.pair_id]
        assert entry.comparison_sha256 == compute_comparison_sha256(comp)
        assert entry.comparison_semantic_hash == compute_comparison_semantic_hash(comp)

    # Validate bundle from bytes
    validated_manifest = validate_answer_comparison_set_bundle(
        build_result.manifest_bytes,
        build_result.comparison_files,
        ans_base=ans_base,
        ans_rag=ans_rag,
        ans_final=ans_final,
        gold_manifest_ref=gold_ref,
    )
    assert validated_manifest == manifest


def test_14_control_ordering_and_resolution() -> None:
    ans_base = _make_run_input(AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111")
    ans_rag = _make_run_input(AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222")

    comp = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag)
    keys = tuple(check.variable_key for check in comp.controlled_variable_checks)
    assert keys == (
        "CASE_SET",
        "DATASET",
        "PARTITION",
        "GOLD",
        "RUBRIC",
        "METRIC_POLICY",
        "INPUT_CONTEXT",
        "MODEL_CONFIGURATION",
        "PROMPT_STRUCTURE",
        "PARSER",
        "SEED",
        "SAMPLING_PARAMETERS",
        "TOKEN_LIMIT",
        "TIMEOUT",
    )


@pytest.mark.parametrize(
    "mismatch_field,kwargs",
    [
        ("CASE_SET", {"partition_manifest_hash": "f" * 64}),
        ("DATASET", {"dataset_manifest_sha256": "f" * 64}),
        ("PARTITION", {"partition": Partition.HOLDOUT}),
        ("GOLD", {"resource_set_hash": "f" * 64}),
        ("RUBRIC", {"critical_claim_rubric_ref": ImmutableReference(id="r-diff", version="1.0.0", hash="f" * 64)}),
        ("METRIC_POLICY", {"comparison_policy_ref": ImmutableReference(id="p-diff", version="1.0.0", hash="f" * 64)}),
        ("MODEL_CONFIGURATION", {"model_config_hash": "f" * 64}),
    ],
)
def test_known_7_control_mismatch_invalidates_comparison(mismatch_field: str, kwargs: dict) -> None:
    ans_base = _make_run_input(AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111")
    if mismatch_field == "PARTITION":
        # Partition.HOLDOUT violates the DEV partition gate
        with pytest.raises(EvaluationValidationError) as exc_info:
            ans_rag = _make_run_input(AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222", **kwargs)
            build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag)
        assert exc_info.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID
        return

    ans_rag = _make_run_input(AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222", **kwargs)
    comp = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag)
    assert comp.execution_status is ExecutionStatus.INVALID
    assert comp.decision_status is None
    checks = {c.variable_key: c for c in comp.controlled_variable_checks}
    assert checks[mismatch_field].matched is False


@pytest.mark.parametrize(
    "supp_field",
    [
        "input_context_hash",
        "prompt_structure_hash",
        "parser_hash",
        "seed_hash",
        "sampling_parameters_hash",
        "token_limit_hash",
        "timeout_hash",
    ],
)
def test_supplemental_controls_missing_invalidates(supp_field: str) -> None:
    base_supp = replace(_default_supplemental(), **{supp_field: None})
    ans_base = _make_run_input(
        AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111", supplemental=base_supp
    )
    ans_rag = _make_run_input(AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222")

    comp = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag)
    assert comp.execution_status is ExecutionStatus.INVALID
    assert comp.decision_status is None
    assert comp.controlled_variable_checks == ()
    assert comp.scope_comparisons == ()


@pytest.mark.parametrize(
    "supp_field",
    [
        "input_context_hash",
        "prompt_structure_hash",
        "parser_hash",
        "seed_hash",
        "sampling_parameters_hash",
        "token_limit_hash",
        "timeout_hash",
    ],
)
def test_supplemental_controls_mismatch_invalidates(supp_field: str) -> None:
    rag_supp = replace(_default_supplemental(), **{supp_field: "9" * 64})
    ans_base = _make_run_input(AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111")
    ans_rag = _make_run_input(
        AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222", supplemental=rag_supp
    )

    comp = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag)
    assert comp.execution_status is ExecutionStatus.INVALID
    assert comp.decision_status is None
    assert len(comp.controlled_variable_checks) == 14
    supp_key_map = {
        "input_context_hash": "INPUT_CONTEXT",
        "prompt_structure_hash": "PROMPT_STRUCTURE",
        "parser_hash": "PARSER",
        "seed_hash": "SEED",
        "sampling_parameters_hash": "SAMPLING_PARAMETERS",
        "token_limit_hash": "TOKEN_LIMIT",
        "timeout_hash": "TIMEOUT",
    }
    checks = {c.variable_key: c for c in comp.controlled_variable_checks}
    assert checks[supp_key_map[supp_field]].matched is False


@pytest.mark.parametrize("invalid_sha", ["abc", "A" * 64, "1" * 63, "g" * 64, 12345])
def test_supplemental_controls_malformed_sha_raises_validation_error(invalid_sha: object) -> None:
    with pytest.raises(EvaluationValidationError) as exc_info:
        AnswerComparisonSupplementalControls(
            input_context_hash=cast(str, invalid_sha),
            prompt_structure_hash="2" * 64,
            parser_hash="3" * 64,
            seed_hash="4" * 64,
            sampling_parameters_hash="5" * 64,
            token_limit_hash="6" * 64,
            timeout_hash="7" * 64,
        )
    assert exc_info.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_delta_allowed_changes() -> None:
    # BASE -> RAG allows RETRIEVAL_PIPELINE, SOURCE_INDEX, RUNTIME_BUNDLE, RETRIEVED_EVIDENCE
    delta_rag = replace(
        _default_delta(),
        retrieval_pipeline_hash="8" * 64,
        source_index_hash="7" * 64,
        runtime_bundle_hash="6" * 64,
        retrieved_evidence_hash="5" * 64,
    )
    ans_base = _make_run_input(AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111")
    ans_rag = _make_run_input(AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222", delta=delta_rag)

    comp = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag)
    assert comp.execution_status is ExecutionStatus.COMPLETED
    assert comp.decision_status is DecisionStatus.INCONCLUSIVE


def test_unauthorized_delta_change_invalidates() -> None:
    # BASE -> RAG: FINAL_VALIDATOR is NOT allowed to change
    delta_rag = replace(_default_delta(), final_validator_hash="9" * 64)
    ans_base = _make_run_input(AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111")
    ans_rag = _make_run_input(AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222", delta=delta_rag)

    comp = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag)
    assert comp.execution_status is ExecutionStatus.INVALID
    assert comp.decision_status is None
    assert comp.scope_comparisons == ()

    # RAG -> FINAL: SOURCE_INDEX is NOT allowed to change
    rag_answer_sha = "9" * 64
    ans_rag_valid = _make_run_input(
        AnswerVariantId.ANS_RAG,
        run_id="22222222-2222-2222-2222-222222222222",
        answer_sha256=rag_answer_sha,
    )
    delta_final = replace(_default_delta(), source_index_hash="8" * 64)
    ans_final = _make_run_input(
        AnswerVariantId.ANS_FINAL,
        run_id="33333333-3333-3333-3333-333333333333",
        delta=delta_final,
        draft_answer_bindings=(AnswerDraftInputBinding(case_id="case-001", draft_answer_sha256=rag_answer_sha),),
    )
    comp2 = build_answer_pair_comparison(AnswerComparisonPairId.ANS_RAG_ANS_FINAL, ans_rag_valid, ans_final)
    assert comp2.execution_status is ExecutionStatus.INVALID
    assert comp2.decision_status is None
    assert comp2.scope_comparisons == ()


def test_delta_missing_binding_invalidates() -> None:
    delta_missing = replace(_default_delta(), retrieval_pipeline_hash=None)
    ans_base = _make_run_input(
        AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111", delta=delta_missing
    )
    ans_rag = _make_run_input(AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222")

    comp = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag)
    assert comp.execution_status is ExecutionStatus.INVALID
    assert comp.decision_status is None


@pytest.mark.parametrize("invalid_sha", ["not-a-sha", "A" * 64, "0" * 63])
def test_delta_malformed_sha_raises_validation_error(invalid_sha: str) -> None:
    with pytest.raises(EvaluationValidationError) as exc_info:
        AnswerComparisonDeltaBindings(
            retrieval_pipeline_hash=invalid_sha,
            source_index_hash="b" * 64,
            runtime_bundle_hash="c" * 64,
            retrieved_evidence_hash="d" * 64,
            final_validator_hash="e" * 64,
            citation_gate_hash="f" * 64,
            safety_gate_hash="0" * 64,
            release_gate_hash="1" * 64,
        )
    assert exc_info.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_experiment_identity_negative_cases() -> None:
    ans_base = _make_run_input(AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111")

    # 1. Wrong variant wiring (e.g. baseline is ANS_RAG for ANS_BASE_ANS_RAG pair)
    ans_rag_as_base = _make_run_input(AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222")
    with pytest.raises(EvaluationValidationError):
        build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_rag_as_base, ans_rag_as_base)

    # 2. Same run_id
    ans_same = _make_run_input(AnswerVariantId.ANS_RAG, run_id="11111111-1111-1111-1111-111111111111")
    with pytest.raises(EvaluationValidationError):
        build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_same)

    # 3. Different experiment_id
    ans_diff_exp = _make_run_input(
        AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222", experiment_id="exp-diff"
    )
    with pytest.raises(EvaluationValidationError):
        build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_diff_exp)

    # 4. KNOWLEDGE_RETRIEVAL is forbidden
    ans_retrieval = _make_run_input(
        AnswerVariantId.ANS_BASE,
        run_id="11111111-1111-1111-1111-111111111111",
        experiment_type=ExperimentType.KNOWLEDGE_RETRIEVAL,
    )
    with pytest.raises(EvaluationValidationError):
        build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_retrieval, ans_rag_as_base)

    # 5. Missing answer_variant_manifest_hash
    ans_no_hash = _make_run_input(
        AnswerVariantId.ANS_RAG,
        run_id="22222222-2222-2222-2222-222222222222",
        answer_variant_manifest_hash=None,
    )
    with pytest.raises(EvaluationValidationError):
        build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_no_hash)


def test_same_case_binding_negatives() -> None:
    ans_base = _make_run_input(AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111")
    # Different case input_sha256
    ans_rag_diff_input = _make_run_input(AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222")
    modified_cases = (ans_rag_diff_input.bundle.cases[0].model_copy(update={"input_sha256": "f" * 64}),)
    ans_rag_diff_input = replace(
        ans_rag_diff_input,
        bundle=replace(ans_rag_diff_input.bundle, cases=modified_cases),
    )

    comp = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag_diff_input)
    assert comp.execution_status is ExecutionStatus.INVALID
    assert comp.decision_status is None
    assert comp.scope_comparisons == ()

    # Different case ID sequence
    diff_case_id = (ans_rag_diff_input.bundle.cases[0].model_copy(update={"case_id": "case-999"}),)
    ans_rag_diff_id = replace(
        ans_rag_diff_input,
        bundle=replace(ans_rag_diff_input.bundle, cases=diff_case_id),
    )
    comp2 = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag_diff_id)
    assert comp2.execution_status is ExecutionStatus.INVALID
    assert comp2.decision_status is None
    assert comp2.scope_comparisons == ()


def test_rag_to_final_draft_binding_checks() -> None:
    rag_answer_sha = "9" * 64
    ans_rag = _make_run_input(
        AnswerVariantId.ANS_RAG,
        run_id="22222222-2222-2222-2222-222222222222",
        answer_sha256=rag_answer_sha,
    )

    # 1. Exact match works
    final_valid = _make_run_input(
        AnswerVariantId.ANS_FINAL,
        run_id="33333333-3333-3333-3333-333333333333",
        draft_answer_bindings=(AnswerDraftInputBinding(case_id="case-001", draft_answer_sha256=rag_answer_sha),),
    )
    comp = build_answer_pair_comparison(AnswerComparisonPairId.ANS_RAG_ANS_FINAL, ans_rag, final_valid)
    assert comp.execution_status is ExecutionStatus.COMPLETED

    # 2. Hash mismatch
    final_mismatch = _make_run_input(
        AnswerVariantId.ANS_FINAL,
        run_id="33333333-3333-3333-3333-333333333333",
        draft_answer_bindings=(AnswerDraftInputBinding(case_id="case-001", draft_answer_sha256="8" * 64),),
    )
    comp_mismatch = build_answer_pair_comparison(AnswerComparisonPairId.ANS_RAG_ANS_FINAL, ans_rag, final_mismatch)
    assert comp_mismatch.execution_status is ExecutionStatus.INVALID
    assert comp_mismatch.decision_status is None

    # 3. Missing draft binding
    final_missing = _make_run_input(
        AnswerVariantId.ANS_FINAL,
        run_id="33333333-3333-3333-3333-333333333333",
        draft_answer_bindings=(),
    )
    comp_missing = build_answer_pair_comparison(AnswerComparisonPairId.ANS_RAG_ANS_FINAL, ans_rag, final_missing)
    assert comp_missing.execution_status is ExecutionStatus.INVALID
    assert comp_missing.decision_status is None


def test_comparison_semantic_hash_excludes_run_uuids() -> None:
    ans_base = _make_run_input(AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111")
    ans_rag = _make_run_input(AnswerVariantId.ANS_RAG, run_id="22222222-2222-2222-2222-222222222222")
    comp1 = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, ans_rag)

    # Same data but different run_ids and candidate run_id
    ans_base_new_ids = _make_run_input(AnswerVariantId.ANS_BASE, run_id="44444444-4444-4444-4444-444444444444")
    ans_rag_new_ids = _make_run_input(AnswerVariantId.ANS_RAG, run_id="55555555-5555-5555-5555-555555555555")
    # keep semantic hash on bundle identical
    ans_base_new_ids = replace(
        ans_base_new_ids, bundle=replace(ans_base_new_ids.bundle, semantic_hash=ans_base.bundle.semantic_hash)
    )
    ans_rag_new_ids = replace(
        ans_rag_new_ids, bundle=replace(ans_rag_new_ids.bundle, semantic_hash=ans_rag.bundle.semantic_hash)
    )
    comp2 = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base_new_ids, ans_rag_new_ids)

    # UUIDs differ
    assert comp1.run_id != comp2.run_id
    assert comp1.baseline_run_id != comp2.baseline_run_id
    assert comp1.candidate_run_id != comp2.candidate_run_id

    # Semantic hashes MUST match
    assert compute_comparison_semantic_hash(comp1) == compute_comparison_semantic_hash(comp2)


def test_manifest_bundle_tampering_rejection() -> None:
    rag_answer_sha = "9" * 64
    ans_base = _make_run_input(
        AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111", metric_value="0.6"
    )
    ans_rag = _make_run_input(
        AnswerVariantId.ANS_RAG,
        run_id="22222222-2222-2222-2222-222222222222",
        answer_sha256=rag_answer_sha,
        metric_value="0.8",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64),
    )
    final_drafts = (AnswerDraftInputBinding(case_id="case-001", draft_answer_sha256=rag_answer_sha),)
    ans_final = _make_run_input(
        AnswerVariantId.ANS_FINAL,
        run_id="33333333-3333-3333-3333-333333333333",
        answer_sha256="8" * 64,
        metric_value="0.9",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64, final_validator_hash="8" * 64),
        draft_answer_bindings=final_drafts,
    )
    gold_ref = ImmutableReference(id="gold-qa", version="1.0.0", hash="0" * 64)
    built = build_answer_comparison_set(
        ans_base=ans_base,
        ans_rag=ans_rag,
        ans_final=ans_final,
        gold_manifest_ref=gold_ref,
    )

    # 1. 1 byte mutated in comparison file
    tampered_files = dict(built.comparison_files)
    first_path = list(tampered_files.keys())[0]
    tampered_files[first_path] = tampered_files[first_path] + b" "
    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_comparison_set_bundle(
            built.manifest_bytes,
            tampered_files,
            ans_base=ans_base,
            ans_rag=ans_rag,
            ans_final=ans_final,
            gold_manifest_ref=gold_ref,
        )
    assert exc_info.value.code is EvaluationErrorCode.HASH_MISMATCH

    # 2. Missing comparison file
    missing_files = {k: v for k, v in built.comparison_files.items() if k != first_path}
    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_comparison_set_bundle(
            built.manifest_bytes,
            missing_files,
            ans_base=ans_base,
            ans_rag=ans_rag,
            ans_final=ans_final,
            gold_manifest_ref=gold_ref,
        )
    assert exc_info.value.code is EvaluationErrorCode.RESOURCE_MISSING

    # 3. Extra comparison file
    extra_files = dict(built.comparison_files)
    extra_files["extra/comparison.json"] = b"{}"
    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_comparison_set_bundle(
            built.manifest_bytes,
            extra_files,
            ans_base=ans_base,
            ans_rag=ans_rag,
            ans_final=ans_final,
            gold_manifest_ref=gold_ref,
        )
    assert exc_info.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID

    # 4. Mutated manifest self-hash
    tampered_manifest_bytes = built.manifest_bytes.replace(b"manifest_sha256", b"manifest_shaxxx")
    with pytest.raises(EvaluationValidationError):
        validate_answer_comparison_set_bundle(
            tampered_manifest_bytes,
            built.comparison_files,
            ans_base=ans_base,
            ans_rag=ans_rag,
            ans_final=ans_final,
            gold_manifest_ref=gold_ref,
        )

    # 5. RunInput authority mismatch (e.g. runner_commit_sha changed on ans_base)
    tampered_base = replace(ans_base, runner_commit_sha="f" * 40)
    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_comparison_set_bundle(
            built.manifest_bytes,
            built.comparison_files,
            ans_base=tampered_base,
            ans_rag=ans_rag,
            ans_final=ans_final,
            gold_manifest_ref=gold_ref,
        )
    assert exc_info.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_manifest_bundle_rejects_stale_model_configuration() -> None:
    rag_answer_sha = "9" * 64
    ans_base = _make_run_input(
        AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111", metric_value="0.6"
    )
    ans_rag = _make_run_input(
        AnswerVariantId.ANS_RAG,
        run_id="22222222-2222-2222-2222-222222222222",
        answer_sha256=rag_answer_sha,
        metric_value="0.8",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64),
    )
    final_drafts = (AnswerDraftInputBinding(case_id="case-001", draft_answer_sha256=rag_answer_sha),)
    ans_final = _make_run_input(
        AnswerVariantId.ANS_FINAL,
        run_id="33333333-3333-3333-3333-333333333333",
        answer_sha256="8" * 64,
        metric_value="0.9",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64, final_validator_hash="8" * 64),
        draft_answer_bindings=final_drafts,
    )
    gold_ref = ImmutableReference(id="gold-qa", version="1.0.0", hash="0" * 64)
    built = build_answer_comparison_set(
        ans_base=ans_base,
        ans_rag=ans_rag,
        ans_final=ans_final,
        gold_manifest_ref=gold_ref,
    )

    tampered_base = _make_run_input(
        AnswerVariantId.ANS_BASE,
        run_id="11111111-1111-1111-1111-111111111111",
        metric_value="0.6",
        model_config_hash="f" * 64,
    )

    new_pair = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, tampered_base, ans_rag)
    assert new_pair.execution_status is ExecutionStatus.INVALID

    with pytest.raises(EvaluationValidationError):
        validate_answer_comparison_set_bundle(
            built.manifest_bytes,
            built.comparison_files,
            ans_base=tampered_base,
            ans_rag=ans_rag,
            ans_final=ans_final,
            gold_manifest_ref=gold_ref,
        )


def test_manifest_bundle_rejects_stale_safety_gate_change() -> None:
    rag_answer_sha = "9" * 64
    ans_base = _make_run_input(
        AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111", metric_value="0.6"
    )
    ans_rag = _make_run_input(
        AnswerVariantId.ANS_RAG,
        run_id="22222222-2222-2222-2222-222222222222",
        answer_sha256=rag_answer_sha,
        metric_value="0.8",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64),
    )
    final_drafts = (AnswerDraftInputBinding(case_id="case-001", draft_answer_sha256=rag_answer_sha),)
    ans_final = _make_run_input(
        AnswerVariantId.ANS_FINAL,
        run_id="33333333-3333-3333-3333-333333333333",
        answer_sha256="8" * 64,
        metric_value="0.9",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64, final_validator_hash="8" * 64),
        draft_answer_bindings=final_drafts,
    )
    gold_ref = ImmutableReference(id="gold-qa", version="1.0.0", hash="0" * 64)
    built = build_answer_comparison_set(
        ans_base=ans_base,
        ans_rag=ans_rag,
        ans_final=ans_final,
        gold_manifest_ref=gold_ref,
    )

    tampered_rag_delta = replace(ans_rag.delta_bindings, safety_gate_hash="f" * 64)
    tampered_rag = replace(ans_rag, delta_bindings=tampered_rag_delta)

    new_pair = build_answer_pair_comparison(AnswerComparisonPairId.ANS_BASE_ANS_RAG, ans_base, tampered_rag)
    assert new_pair.execution_status is ExecutionStatus.INVALID

    with pytest.raises(EvaluationValidationError):
        validate_answer_comparison_set_bundle(
            built.manifest_bytes,
            built.comparison_files,
            ans_base=ans_base,
            ans_rag=tampered_rag,
            ans_final=ans_final,
            gold_manifest_ref=gold_ref,
        )


def test_manifest_bundle_rejects_stale_missing_draft_binding() -> None:
    rag_answer_sha = "9" * 64
    ans_base = _make_run_input(
        AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111", metric_value="0.6"
    )
    ans_rag = _make_run_input(
        AnswerVariantId.ANS_RAG,
        run_id="22222222-2222-2222-2222-222222222222",
        answer_sha256=rag_answer_sha,
        metric_value="0.8",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64),
    )
    final_drafts = (AnswerDraftInputBinding(case_id="case-001", draft_answer_sha256=rag_answer_sha),)
    ans_final = _make_run_input(
        AnswerVariantId.ANS_FINAL,
        run_id="33333333-3333-3333-3333-333333333333",
        answer_sha256="8" * 64,
        metric_value="0.9",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64, final_validator_hash="8" * 64),
        draft_answer_bindings=final_drafts,
    )
    gold_ref = ImmutableReference(id="gold-qa", version="1.0.0", hash="0" * 64)
    built = build_answer_comparison_set(
        ans_base=ans_base,
        ans_rag=ans_rag,
        ans_final=ans_final,
        gold_manifest_ref=gold_ref,
    )

    tampered_final = replace(ans_final, draft_answer_bindings=())

    new_pair = build_answer_pair_comparison(AnswerComparisonPairId.ANS_RAG_ANS_FINAL, ans_rag, tampered_final)
    assert new_pair.execution_status is ExecutionStatus.INVALID

    with pytest.raises(EvaluationValidationError):
        validate_answer_comparison_set_bundle(
            built.manifest_bytes,
            built.comparison_files,
            ans_base=ans_base,
            ans_rag=ans_rag,
            ans_final=tampered_final,
            gold_manifest_ref=gold_ref,
        )


def test_manifest_bundle_rejects_semantic_hash_mismatch() -> None:
    rag_answer_sha = "9" * 64
    ans_base = _make_run_input(
        AnswerVariantId.ANS_BASE, run_id="11111111-1111-1111-1111-111111111111", metric_value="0.6"
    )
    ans_rag = _make_run_input(
        AnswerVariantId.ANS_RAG,
        run_id="22222222-2222-2222-2222-222222222222",
        answer_sha256=rag_answer_sha,
        metric_value="0.8",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64),
    )
    final_drafts = (AnswerDraftInputBinding(case_id="case-001", draft_answer_sha256=rag_answer_sha),)
    ans_final = _make_run_input(
        AnswerVariantId.ANS_FINAL,
        run_id="33333333-3333-3333-3333-333333333333",
        answer_sha256="8" * 64,
        metric_value="0.9",
        delta=replace(_default_delta(), retrieval_pipeline_hash="9" * 64, final_validator_hash="8" * 64),
        draft_answer_bindings=final_drafts,
    )
    gold_ref = ImmutableReference(id="gold-qa", version="1.0.0", hash="0" * 64)
    built = build_answer_comparison_set(
        ans_base=ans_base,
        ans_rag=ans_rag,
        ans_final=ans_final,
        gold_manifest_ref=gold_ref,
    )

    tampered_bundle = replace(ans_base.bundle, semantic_hash="f" * 64)
    tampered_base = replace(ans_base, bundle=tampered_bundle)

    with pytest.raises(EvaluationValidationError) as exc_info:
        validate_answer_comparison_set_bundle(
            built.manifest_bytes,
            built.comparison_files,
            ans_base=tampered_base,
            ans_rag=ans_rag,
            ans_final=ans_final,
            gold_manifest_ref=gold_ref,
        )
    assert exc_info.value.code is EvaluationErrorCode.HASH_MISMATCH
