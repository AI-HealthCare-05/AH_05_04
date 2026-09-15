from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.loaders import EvaluationCaseContract, ValidatedDataset, load_dataset
from ai_worker.tasks.evaluation.safety_metrics import build_safety_metrics
from ai_worker.tasks.evaluation.schemas.artifacts import CASE_RESULT_ADAPTER, CaseResult
from ai_worker.tasks.evaluation.schemas.authoring import EVALUATION_CASE_ADAPTER
from ai_worker.tasks.evaluation.schemas.authoring_v1_2 import EVALUATION_CASE_ADAPTER_V1_2
from ai_worker.tasks.evaluation.schemas.common import (
    DecisionStatus,
    ExecutionStatus,
    ImmutableReference,
    Partition,
    TaskType,
)
from ai_worker.tasks.evaluation.schemas.grounding_v1 import (
    GroundingSignal,
    GroundingSignalStatus,
)
from ai_worker.tasks.evaluation.schemas.policy import ComparisonPolicy, ComparisonScope

EVALS_ROOT = Path(__file__).parents[3] / "evals"
BASE_DATASET = load_dataset(
    EVALS_ROOT / "retrieval/manifests/dev-foundation-v1.dataset.json",
    evals_root=EVALS_ROOT,
)
RUN_ID = "16100000-0000-4000-8000-000000000001"
DATASET_CODE = BASE_DATASET.manifest.dataset_code
DATASET_VERSION = BASE_DATASET.manifest.dataset_version

_APPROVED_CRITICAL_INVARIANTS_ORDERED = (
    "CRITICAL_UNSUPPORTED_CLAIM",
    "FALLBACK_RELEASE_PUBLICATION_MISMATCH",
    "FORBIDDEN_CLAIM_EMITTED",
    "PROVIDER_INVOCATION_VIOLATION",
    "RETRIEVAL_INVOCATION_VIOLATION",
    "RULE_REVERSAL",
    "SAFETY_ROUTING_MISMATCH",
    "SCOPE_VIOLATION",
    "SOURCE_BINDING_MISUSE",
    "UNCITED_MEDICAL_CLAIM",
)


def _scope(
    metric_id: str,
    unit_of_analysis: str,
    *,
    minimum_case_count: int = 1,
    minimum_independent_group_count: int = 1,
    estimator_id: str = "MICRO_RATIO",
    ci_method_id: str = "PERCENTILE_CLUSTER_BOOTSTRAP",
    seed: int = 161,
    partition: Partition = Partition.DEV,
    slice_id: str = "ALL",
    required: bool = False,
    iterations: int = 200,
    level: str = "0.95",
    sidedness: str = "TWO_SIDED",
    decision_basis: str = "DIAGNOSTIC_ONLY",
    threshold: str = "0",
) -> ComparisonScope:
    return ComparisonScope.model_validate(
        {
            "metric_id": metric_id,
            "metric_version": "1.0.0",
            "partition": partition.value,
            "slice_id": slice_id,
            "required": required,
            "unit_of_analysis": unit_of_analysis,
            "estimator_id": estimator_id,
            "estimator_version": "1.0.0",
            "minimum_case_count": minimum_case_count,
            "independence_unit": "question_template",
            "cluster_dimension": "question_template",
            "minimum_independent_group_count": minimum_independent_group_count,
            "threshold": threshold,
            "decision_basis": decision_basis,
            "ci_method_id": ci_method_id,
            "ci_method_version": "1.0.0",
            "ci_parameters": {"iterations": iterations, "level": level, "sidedness": sidedness},
            "seed": seed,
        }
    )


ALL_EIGHT_SCOPES = (
    _scope("SAFETY_ROUTING_ACCURACY", "CASE"),
    _scope("RULE_RECALL", "RULE"),
    _scope("RULE_REVERSAL_RATE", "CASE"),
    _scope("SCOPE_VIOLATION_RATE", "CASE"),
    _scope("PROVIDER_INVOCATION_VIOLATION_RATE", "CASE"),
    _scope("RETRIEVAL_INVOCATION_VIOLATION_RATE", "CASE"),
    _scope("FALLBACK_MISMATCH_RATE", "CASE"),
    _scope("CRITICAL_SAFETY_FAILURE_RATE", "CASE"),
)


def _safety_case_v12(
    *,
    case_id: str,
    input_sha256: str = "a" * 64,
    group_id: str = "group-1",
    task_type: TaskType = TaskType.SAFETY,
    partition: Partition = Partition.DEV,
    slice_ids: tuple[str, ...] = ("SYNTHETIC_SLICE",),
    expected_rule_outcome: str = "MATCHED_RULES",
    expected_rule_ids: tuple[str, ...] = ("rule-1",),
    expected_rule_not_invoked_reason: str | None = None,
    expected_scope_codes: tuple[str, ...] = ("SCOPE_A",),
    expected_response_level: str = "ROUTINE",
    expected_safety_disposition: str = "NORMAL",
    expected_execution_status: str = "SUCCEEDED",
    expected_release_decision: str = "PASS",
    expected_fallback_code: str | None = None,
    expected_provider_invocation: bool = True,
    expected_retrieval_invocation: bool = True,
    expected_publication_allowed: bool = True,
    bundle_eligibility_status: str = "ELIGIBLE",
    source_eligibility_status: str = "ELIGIBLE",
    dependency_fault: str = "NONE",
    forbidden_claims: tuple[dict[str, Any], ...] = (),
    gold_claims: tuple[dict[str, Any], ...] = (),
    expected_citations: tuple[dict[str, Any], ...] = (),
    query: str | None = None,
) -> EvaluationCaseContract:
    not_invoked_reason = expected_rule_not_invoked_reason
    rule_ids = list(expected_rule_ids)
    if expected_rule_outcome == "NOT_INVOKED":
        rule_ids = []
        if not_invoked_reason is None:
            if bundle_eligibility_status in ("SCOPE_INELIGIBLE", "MEMBER_INELIGIBLE"):
                not_invoked_reason = "BUNDLE_INELIGIBLE"
            elif source_eligibility_status != "ELIGIBLE":
                not_invoked_reason = "SOURCE_INELIGIBLE"
            else:
                not_invoked_reason = "SAFETY_ROUTED"
                if expected_safety_disposition == "NORMAL":
                    expected_safety_disposition = "URGENT_ROUTED"
        expected_provider_invocation = False
        expected_retrieval_invocation = False
    elif expected_rule_outcome == "NO_MATCH":
        rule_ids = []
        not_invoked_reason = None

    payload: dict[str, Any] = {
        "schema_id": "rag-eval.case",
        "schema_version": "1.2.0",
        "case_id": case_id,
        "dataset_code": DATASET_CODE,
        "dataset_version": DATASET_VERSION,
        "task_type": task_type.value,
        "partition": partition.value,
        "slice_ids": list(slice_ids),
        "data_classification": "SYNTHETIC",
        "query": query or f"SYNTHETIC_QUERY_{case_id}",
        "context": {
            "medication_fixtures": [
                {
                    "medication_fixture_id": "SYNTHETIC_MEDICATION_SAFETY",
                    "medication_product_fixture_id": "SYNTHETIC_PRODUCT_SAFETY",
                    "display_name_token": "SYNTHETIC_DISPLAY_SAFETY",
                    "ingredient_tokens": ["SYNTHETIC_INGREDIENT_SAFETY"],
                    "strength_text_token": "SYNTHETIC_STRENGTH_SAFETY",
                    "identification_status": "MATCHED",
                }
            ],
            "patient_context_fixture": {
                "condition_tokens": [],
                "allergy_tokens": [],
                "barrier_codes": [],
                "pregnancy_status": "NOT_APPLICABLE",
            },
            "prescription_fixture": None,
            "runtime_fixture": {
                "source_snapshot_ref": {"id": "src", "version": "1.0.0", "hash": "1" * 64},
                "knowledge_index_ref": {"id": "idx", "version": "1.0.0", "hash": "2" * 64},
                "rule_set_ref": {"id": "rules", "version": "1.0.0", "hash": "3" * 64},
                "guideline_set_ref": None,
                "safety_policy_set_ref": {"id": "pol", "version": "1.0.0", "hash": "4" * 64},
                "runtime_bundle_manifest_hash": "5" * 64,
                "source_eligibility_status": source_eligibility_status,
                "bundle_eligibility_status": bundle_eligibility_status,
                "dependency_fault": dependency_fault,
            },
        },
        "input_sha256": input_sha256,
        "leakage_group_ids": {
            "question_template": group_id,
            "source_segment": "seg-1",
            "medication_family": "fam-1",
            "transform_origin": "orig-1",
        },
        "critical_claim_rubric_ref": {"id": "rubric", "version": "1.0.0", "hash": "6" * 64},
        "gold_version": "1.0.0",
        "review_provenance": {
            "authored_by": {"namespace": "GITHUB_LOGIN", "actor_id": "tester", "role": "EVALUATION_IMPLEMENTER"},
            "authored_at": "2026-09-01T00:00:00.000000Z",
            "reviewed_by": None,
            "approved_by": None,
            "reviewed_at": None,
            "approved_at": None,
            "team_gold_status": "DRAFT",
            "external_medical_review_status": "NOT_REQUESTED",
            "external_medical_approval_receipt_ref": None,
            "evidence_review_refs": [],
        },
        "tags": ["TAG1"],
        "expected": {
            "relevant_evidence_refs": None,
            "required_evidence_refs": None,
            "gold_claims": list(gold_claims),
            "forbidden_claims": list(forbidden_claims),
            "expected_citations": list(expected_citations),
            "expected_rule_ids": rule_ids,
            "expected_rule_outcome": expected_rule_outcome,
            "expected_rule_not_invoked_reason": not_invoked_reason,
            "expected_scope_codes": list(expected_scope_codes),
            "expected_response_level": expected_response_level,
            "expected_safety_disposition": expected_safety_disposition,
            "expected_execution_status": expected_execution_status,
            "expected_release_decision": expected_release_decision,
            "expected_fallback_code": expected_fallback_code,
            "expected_provider_invocation": expected_provider_invocation,
            "expected_retrieval_invocation": expected_retrieval_invocation,
            "expected_publication_allowed": expected_publication_allowed,
            "expected_sections": ["SEC1"],
            "omitted_sections": [],
            "risk_level": "GENERAL",
        },
    }
    return cast(EvaluationCaseContract, EVALUATION_CASE_ADAPTER_V1_2.validate_python(payload))


def _dataset_with_cases_and_scopes(
    cases: tuple[EvaluationCaseContract, ...],
    scopes: tuple[ComparisonScope, ...] = ALL_EIGHT_SCOPES,
    critical_invariants: tuple[str, ...] = _APPROVED_CRITICAL_INVARIANTS_ORDERED,
) -> ValidatedDataset:
    policy = ComparisonPolicy.model_validate(
        {
            **BASE_DATASET.comparison_policy.model_dump(mode="json"),
            "scopes": [scope.model_dump(mode="json") for scope in scopes],
        }
    )
    suite = BASE_DATASET.suite.model_copy(update={"critical_invariant_ids": critical_invariants})
    return replace(BASE_DATASET, cases=cases, comparison_policy=policy, suite=suite)


def _case_result(
    case: EvaluationCaseContract,
    *,
    execution_status: str = "COMPLETED",
    decision_status: str | None = "N/A",
    actual_response_level: str | None = "ROUTINE",
    actual_safety_disposition: str | None = "NORMAL",
    actual_rule_ids: tuple[str, ...] | None = ("rule-1",),
    actual_scope_codes: tuple[str, ...] | None = ("SCOPE_A",),
    actual_provider_invocation: bool | None = True,
    actual_retrieval_invocation: bool | None = True,
    actual_execution_status: str | None = "SUCCEEDED",
    actual_release_decision: str | None = "PASS",
    actual_fallback_code: str | None = None,
    actual_publication_allowed: bool | None = True,
    actual_claim_ids: tuple[str, ...] = (),
    actual_citation_evidence_ids: tuple[str, ...] = (),
    answer_sha256: str | None = None,
    run_id: str = RUN_ID,
    input_sha256: str | None = None,
    dataset_code: str | None = None,
    dataset_version: str | None = None,
    task_type: TaskType | None = None,
    partition: Partition | None = None,
) -> CaseResult:
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.case-result",
        "schema_version": "1.0.0",
        "run_id": run_id,
        "case_id": case.case_id,
        "dataset_code": dataset_code or case.dataset_code,
        "dataset_version": dataset_version or case.dataset_version,
        "task_type": (task_type or case.task_type).value,
        "partition": (partition or case.partition).value,
        "input_sha256": input_sha256 or case.input_sha256,
        "execution_status": execution_status,
        "decision_status": decision_status if execution_status == "COMPLETED" else None,
        "failure_codes": [],
        "retrieved_evidence_ids": None,
        "selected_evidence_ids": None,
        "actual_claim_ids": list(actual_claim_ids),
        "actual_citation_evidence_ids": list(actual_citation_evidence_ids),
        "actual_rule_ids": list(actual_rule_ids) if actual_rule_ids is not None else [],
        "actual_scope_codes": list(actual_scope_codes) if actual_scope_codes is not None else [],
        "actual_response_level": actual_response_level,
        "actual_safety_disposition": actual_safety_disposition,
        "actual_execution_status": actual_execution_status,
        "actual_release_decision": actual_release_decision,
        "actual_fallback_code": actual_fallback_code,
        "actual_provider_invocation": actual_provider_invocation,
        "actual_retrieval_invocation": actual_retrieval_invocation,
        "actual_publication_allowed": actual_publication_allowed,
        "actual_sections": [],
        "omitted_sections": [],
        "risk_level": "GENERAL",
        "answer_sha256": answer_sha256,
        "latency_ms": 0,
        "input_token_count": 0,
        "output_token_count": 0,
        "estimated_cost": None,
    }
    return CASE_RESULT_ADAPTER.validate_python(payload)


def _grounding_signal(
    case: EvaluationCaseContract,
    *,
    status: GroundingSignalStatus = GroundingSignalStatus.NOT_APPLICABLE_NO_CLAIMS,
    observation_ref: ImmutableReference | None = None,
    observation_sha256: str | None = None,
    answer_sha256: str | None = None,
    critical_unsupported_claim: bool = False,
    uncited_medical_claim: bool = False,
    source_binding_misuse: bool = False,
    run_id: str = RUN_ID,
    input_sha256: str | None = None,
    dataset_code: str | None = None,
    dataset_version: str | None = None,
    task_type: TaskType | None = None,
    corrupt_self_hash: bool = False,
    case_id: str | None = None,
) -> GroundingSignal:
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.grounding-signal",
        "schema_version": "1.0.0",
        "signal_sha256": "0" * 64,
        "run_id": run_id,
        "case_id": case_id or case.case_id,
        "dataset_code": dataset_code or case.dataset_code,
        "dataset_version": dataset_version or case.dataset_version,
        "task_type": (task_type or case.task_type).value,
        "input_sha256": input_sha256 or case.input_sha256,
        "answer_sha256": answer_sha256,
        "status": status.value,
        "observation_ref": observation_ref.model_dump(mode="json") if observation_ref else None,
        "observation_sha256": observation_sha256,
        "critical_unsupported_claim": critical_unsupported_claim,
        "uncited_medical_claim": uncited_medical_claim,
        "source_binding_misuse": source_binding_misuse,
    }
    temp = GroundingSignal.model_validate(payload)
    canon_dump = cast(dict[str, JsonValue], temp.model_dump(mode="json"))
    real_sha = canonical_sha256(canon_dump, excluded_top_level_keys=frozenset({"signal_sha256"}))
    payload["signal_sha256"] = "f" * 64 if corrupt_self_hash else real_sha
    return GroundingSignal.model_validate(payload)


def _evaluated_signal_pair(
    case: EvaluationCaseContract,
    *,
    critical_unsupported: bool = False,
    uncited_med: bool = False,
    source_misuse: bool = False,
    answer_sha256: str = "e" * 64,
) -> GroundingSignal:
    obs_sha = "d" * 64
    obs_ref = ImmutableReference(id="obs-1", version="1.0.0", hash=obs_sha)
    return _grounding_signal(
        case,
        status=GroundingSignalStatus.EVALUATED,
        observation_ref=obs_ref,
        observation_sha256=obs_sha,
        answer_sha256=answer_sha256,
        critical_unsupported_claim=critical_unsupported,
        uncited_medical_claim=uncited_med,
        source_binding_misuse=source_misuse,
    )


# ==============================================================================
# TESTS
# ==============================================================================


def test_hand_calculated_all_eight_metrics() -> None:
    """Verify hand-calculated values for all 8 metrics across 2 multi-group Cases."""
    case_1 = _safety_case_v12(
        case_id="case-1",
        input_sha256="1" * 64,
        group_id="group-1",
        expected_rule_outcome="MATCHED_RULES",
        expected_rule_ids=("rule-1", "rule-2"),
        expected_scope_codes=("SCOPE_A",),
        expected_response_level="ROUTINE",
        expected_safety_disposition="NORMAL",
        expected_provider_invocation=True,
        expected_retrieval_invocation=False,
    )
    case_2 = _safety_case_v12(
        case_id="case-2",
        input_sha256="2" * 64,
        group_id="group-2",
        expected_rule_outcome="MATCHED_RULES",
        expected_rule_ids=("rule-3",),
        expected_scope_codes=("SCOPE_A", "SCOPE_B"),
        expected_response_level="ROUTINE",
        expected_safety_disposition="NORMAL",
        expected_provider_invocation=False,
        expected_retrieval_invocation=True,
    )
    dataset = _dataset_with_cases_and_scopes((case_1, case_2))

    res_1 = _case_result(
        case_1,
        actual_rule_ids=("rule-1",),  # recall 1/2
        actual_scope_codes=("SCOPE_A",),  # match
        actual_provider_invocation=True,  # expected True (not applicable to provider viol metric)
        actual_retrieval_invocation=False,  # expected False -> violation 0
    )
    res_2 = _case_result(
        case_2,
        actual_rule_ids=("rule-3",),  # recall 1/1
        actual_scope_codes=("SCOPE_A", "SCOPE_B"),  # match
        actual_provider_invocation=True,  # expected False -> violation 1 (applicable!)
        actual_retrieval_invocation=True,  # expected True (not applicable to retrieval viol metric)
    )

    sig_1 = _grounding_signal(case_1)
    sig_2 = _grounding_signal(case_2)

    results = build_safety_metrics(
        dataset,
        (res_1, res_2),
        (sig_1, sig_2),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-1": "1" * 64, "case-2": "2" * 64},
    )

    metrics_by_id = {m.metric_id: m for m in results.metrics}
    assert len(metrics_by_id) == 8

    # 1. SAFETY_ROUTING_ACCURACY: 2/2 = 1
    m_routing = metrics_by_id["SAFETY_ROUTING_ACCURACY"]
    assert m_routing.execution_status == ExecutionStatus.COMPLETED
    assert m_routing.decision_status == DecisionStatus.NOT_APPLICABLE
    assert m_routing.numerator == 2
    assert m_routing.denominator == 2
    assert m_routing.metric_value == "1"
    assert m_routing.sample_case_count == 2
    assert m_routing.sample_independent_group_count == 2

    # 2. RULE_RECALL: (1 + 1) / (2 + 1) = 2/3 = 0.666667
    m_recall = metrics_by_id["RULE_RECALL"]
    assert m_recall.numerator == 2
    assert m_recall.denominator == 3
    assert m_recall.metric_value == "0.666667"
    assert m_recall.sample_case_count == 2

    # 3. RULE_REVERSAL_RATE: 0/2 = 0
    m_rev = metrics_by_id["RULE_REVERSAL_RATE"]
    assert m_rev.numerator == 0
    assert m_rev.denominator == 2
    assert m_rev.metric_value == "0"

    # 4. SCOPE_VIOLATION_RATE: 0/2 = 0
    m_scope = metrics_by_id["SCOPE_VIOLATION_RATE"]
    assert m_scope.numerator == 0
    assert m_scope.denominator == 2
    assert m_scope.metric_value == "0"

    # 5. PROVIDER_INVOCATION_VIOLATION_RATE: case-2 only (expected=False), actual=True -> 1/1 = 1
    m_prov = metrics_by_id["PROVIDER_INVOCATION_VIOLATION_RATE"]
    assert m_prov.numerator == 1
    assert m_prov.denominator == 1
    assert m_prov.metric_value == "1"
    assert m_prov.sample_case_count == 1  # metric-specific applicable case count!
    assert m_prov.sample_independent_group_count == 1

    # 6. RETRIEVAL_INVOCATION_VIOLATION_RATE: case-1 only (expected=False), actual=False -> 0/1 = 0
    m_ret = metrics_by_id["RETRIEVAL_INVOCATION_VIOLATION_RATE"]
    assert m_ret.numerator == 0
    assert m_ret.denominator == 1
    assert m_ret.metric_value == "0"
    assert m_ret.sample_case_count == 1  # metric-specific applicable case count!
    assert m_ret.sample_independent_group_count == 1

    # 7. FALLBACK_MISMATCH_RATE: 0/2 = 0
    m_fall = metrics_by_id["FALLBACK_MISMATCH_RATE"]
    assert m_fall.numerator == 0
    assert m_fall.denominator == 2
    assert m_fall.metric_value == "0"

    # 8. CRITICAL_SAFETY_FAILURE_RATE: case-1 has 0 failures, case-2 has PROVIDER_INVOCATION_VIOLATION -> 1/2 = 0.5
    m_crit = metrics_by_id["CRITICAL_SAFETY_FAILURE_RATE"]
    assert m_crit.numerator == 1
    assert m_crit.denominator == 2
    assert m_crit.metric_value == "0.5"
    assert m_crit.sample_case_count == 2
    assert m_crit.sample_independent_group_count == 2


def test_routing_match_and_mismatch_urgent_emergency_unknown() -> None:
    """Test URGENT, EMERGENCY, and UNKNOWN routing match and mismatch."""
    cases = (
        _safety_case_v12(
            case_id="c-urgent", expected_response_level="URGENT", expected_safety_disposition="URGENT_ROUTED"
        ),
        _safety_case_v12(
            case_id="c-emerg", expected_response_level="EMERGENCY", expected_safety_disposition="EMERGENCY_ROUTED"
        ),
        _safety_case_v12(
            case_id="c-unk", expected_response_level="UNKNOWN", expected_safety_disposition="UNKNOWN_RISK"
        ),
    )
    dataset = _dataset_with_cases_and_scopes(cases, (_scope("SAFETY_ROUTING_ACCURACY", "CASE"),))

    # All 3 match
    results_match = (
        _case_result(cases[0], actual_response_level="URGENT", actual_safety_disposition="URGENT_ROUTED"),
        _case_result(cases[1], actual_response_level="EMERGENCY", actual_safety_disposition="EMERGENCY_ROUTED"),
        _case_result(cases[2], actual_response_level="UNKNOWN", actual_safety_disposition="UNKNOWN_RISK"),
    )
    signals = tuple(_grounding_signal(c) for c in cases)
    res = build_safety_metrics(
        dataset,
        results_match,
        signals,
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={c.case_id: c.input_sha256 for c in cases},
    )
    assert res.metrics[0].metric_value == "1"
    assert res.metrics[0].numerator == 3

    # 1 mismatches (urgent gets ROUTINE)
    results_mismatch = (
        _case_result(cases[0], actual_response_level="ROUTINE", actual_safety_disposition="URGENT_ROUTED"),
        _case_result(cases[1], actual_response_level="EMERGENCY", actual_safety_disposition="EMERGENCY_ROUTED"),
        _case_result(cases[2], actual_response_level="UNKNOWN", actual_safety_disposition="UNKNOWN_RISK"),
    )
    res2 = build_safety_metrics(
        dataset,
        results_mismatch,
        signals,
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={c.case_id: c.input_sha256 for c in cases},
    )
    assert res2.metrics[0].numerator == 2
    assert res2.metrics[0].metric_value == "0.666667"


def test_matched_rules_full_partial_zero_recall() -> None:
    """Test full, partial, and zero recall under MATCHED_RULES."""
    case = _safety_case_v12(
        case_id="c-recall", expected_rule_outcome="MATCHED_RULES", expected_rule_ids=("r1", "r2", "r3")
    )
    dataset = _dataset_with_cases_and_scopes((case,), (_scope("RULE_RECALL", "RULE"),))
    sig = _grounding_signal(case)

    # Full recall: 3/3
    res_full = _case_result(case, actual_rule_ids=("r1", "r2", "r3"))
    m1 = build_safety_metrics(
        dataset,
        (res_full,),
        (sig,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case.case_id: case.input_sha256},
    )
    assert m1.metrics[0].numerator == 3
    assert m1.metrics[0].denominator == 3
    assert m1.metrics[0].metric_value == "1"

    # Partial recall: 1/3
    res_partial = _case_result(case, actual_rule_ids=("r2",))
    m2 = build_safety_metrics(
        dataset,
        (res_partial,),
        (sig,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case.case_id: case.input_sha256},
    )
    assert m2.metrics[0].numerator == 1
    assert m2.metrics[0].denominator == 3
    assert m2.metrics[0].metric_value == "0.333333"

    # Zero recall: 0/3
    res_zero = _case_result(case, actual_rule_ids=())
    m3 = build_safety_metrics(
        dataset,
        (res_zero,),
        (sig,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case.case_id: case.input_sha256},
    )
    assert m3.metrics[0].numerator == 0
    assert m3.metrics[0].denominator == 3
    assert m3.metrics[0].metric_value == "0"


def test_matched_rules_reversal_empty_or_unexpected_with_forbidden_release_or_publication() -> None:
    """Test RULE_REVERSAL under MATCHED_RULES conditions."""
    case = _safety_case_v12(
        case_id="c-rev",
        expected_rule_outcome="MATCHED_RULES",
        expected_rule_ids=("r1",),
        expected_release_decision="LIMITED",
        expected_publication_allowed=False,
    )
    dataset = _dataset_with_cases_and_scopes((case,), (_scope("RULE_REVERSAL_RATE", "CASE"),))
    sig = _grounding_signal(case)

    # 1. Empty actual rules + actual_release=PASS (forbidden release) -> REVERSAL
    r1 = _case_result(case, actual_rule_ids=(), actual_release_decision="PASS", actual_publication_allowed=False)
    m1 = build_safety_metrics(
        dataset, (r1,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case={case.case_id: case.input_sha256}
    )
    assert m1.metrics[0].numerator == 1

    # 2. Unexpected actual rule ("r2") + actual_publication=True (forbidden publication) -> REVERSAL
    r2 = _case_result(case, actual_rule_ids=("r2",), actual_release_decision="LIMITED", actual_publication_allowed=True)
    m2 = build_safety_metrics(
        dataset, (r2,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case={case.case_id: case.input_sha256}
    )
    assert m2.metrics[0].numerator == 1

    # 3. Partial omission of expected rules alone (no forbidden release/pub) -> NOT reversal!
    case_multi = _safety_case_v12(
        case_id="c-multi",
        expected_rule_outcome="MATCHED_RULES",
        expected_rule_ids=("r1", "r2"),
        expected_release_decision="PASS",
        expected_publication_allowed=True,
    )
    dataset_multi = _dataset_with_cases_and_scopes((case_multi,), (_scope("RULE_REVERSAL_RATE", "CASE"),))
    sig_multi = _grounding_signal(case_multi)
    r3 = _case_result(
        case_multi, actual_rule_ids=("r1",), actual_release_decision="PASS", actual_publication_allowed=True
    )
    m3 = build_safety_metrics(
        dataset_multi,
        (r3,),
        (sig_multi,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case_multi.case_id: case_multi.input_sha256},
    )
    assert m3.metrics[0].numerator == 0
    assert m3.metrics[0].metric_value == "0"


def test_rule_reversal_no_match_and_not_invoked() -> None:
    """Test NO_MATCH and NOT_INVOKED reversal whenever actual_rule_ids is non-empty."""
    case_no_match = _safety_case_v12(case_id="c-nomatch", expected_rule_outcome="NO_MATCH", expected_rule_ids=())
    case_not_inv = _safety_case_v12(
        case_id="c-notinv",
        expected_rule_outcome="NOT_INVOKED",
        expected_rule_ids=(),
        expected_response_level="URGENT",
        expected_safety_disposition="URGENT_ROUTED",
    )
    dataset = _dataset_with_cases_and_scopes((case_no_match, case_not_inv), (_scope("RULE_REVERSAL_RATE", "CASE"),))
    sig_1 = _grounding_signal(case_no_match)
    sig_2 = _grounding_signal(case_not_inv)

    # Both empty -> 0 reversals
    r1 = _case_result(case_no_match, actual_rule_ids=())
    r2 = _case_result(
        case_not_inv, actual_rule_ids=(), actual_response_level="URGENT", actual_safety_disposition="URGENT_ROUTED"
    )
    m = build_safety_metrics(
        dataset,
        (r1, r2),
        (sig_1, sig_2),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={c.case_id: c.input_sha256 for c in (case_no_match, case_not_inv)},
    )
    assert m.metrics[0].numerator == 0

    # Non-empty actual rules -> reversals!
    r1_viol = _case_result(case_no_match, actual_rule_ids=("unexpected-1",))
    r2_viol = _case_result(
        case_not_inv,
        actual_rule_ids=("unexpected-2",),
        actual_response_level="URGENT",
        actual_safety_disposition="URGENT_ROUTED",
    )
    m_viol = build_safety_metrics(
        dataset,
        (r1_viol, r2_viol),
        (sig_1, sig_2),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={c.case_id: c.input_sha256 for c in (case_no_match, case_not_inv)},
    )
    assert m_viol.metrics[0].numerator == 2


def test_contract_example_not_invoked_with_unexpected_rule() -> None:
    """Verify the contract hand-calculation example: NOT_INVOKED + 1 unexpected rule.

    - RULE_RECALL: not applicable -> 0 / 0 (ZERO_DENOMINATOR / INCONCLUSIVE)
    - RULE_REVERSAL_RATE: 1 / 1
    - CRITICAL_SAFETY_FAILURE_RATE: 1 / 1
    """
    case = _safety_case_v12(
        case_id="c-contract",
        expected_rule_outcome="NOT_INVOKED",
        expected_rule_ids=(),
        expected_response_level="URGENT",
        expected_safety_disposition="URGENT_ROUTED",
        expected_execution_status="SUCCEEDED",
        expected_release_decision="PASS",
        expected_fallback_code=None,
        expected_publication_allowed=True,
    )
    scopes = (
        _scope("RULE_RECALL", "RULE"),
        _scope("RULE_REVERSAL_RATE", "CASE"),
        _scope("CRITICAL_SAFETY_FAILURE_RATE", "CASE"),
    )
    dataset = _dataset_with_cases_and_scopes((case,), scopes)
    res = _case_result(
        case,
        actual_rule_ids=("unexpected-rule",),
        actual_response_level="URGENT",
        actual_safety_disposition="URGENT_ROUTED",
    )
    sig = _grounding_signal(case)

    results = build_safety_metrics(
        dataset,
        (res,),
        (sig,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case.case_id: case.input_sha256},
    )
    metrics_by_id = {m.metric_id: m for m in results.metrics}

    # RULE_RECALL: not applicable -> sample_case_count=0, denominator=0, ZERO_DENOMINATOR
    m_recall = metrics_by_id["RULE_RECALL"]
    assert m_recall.sample_case_count == 0
    assert m_recall.denominator == 0
    assert m_recall.numerator == 0
    assert m_recall.reason_code == "ZERO_DENOMINATOR"
    assert m_recall.decision_status == DecisionStatus.INCONCLUSIVE

    # RULE_REVERSAL_RATE: 1/1 = 1
    m_rev = metrics_by_id["RULE_REVERSAL_RATE"]
    assert m_rev.numerator == 1
    assert m_rev.denominator == 1
    assert m_rev.metric_value == "1"

    # CRITICAL_SAFETY_FAILURE_RATE: 1/1 = 1
    m_crit = metrics_by_id["CRITICAL_SAFETY_FAILURE_RATE"]
    assert m_crit.numerator == 1
    assert m_crit.denominator == 1
    assert m_crit.metric_value == "1"


def test_scope_match_exact_set_regardless_of_order_and_mismatch() -> None:
    """Test exact-set Scope match regardless of order, and Scope mismatch."""
    case = _safety_case_v12(case_id="c-scope", expected_scope_codes=("SCOPE_A", "SCOPE_B"))
    dataset = _dataset_with_cases_and_scopes((case,), (_scope("SCOPE_VIOLATION_RATE", "CASE"),))
    sig = _grounding_signal(case)

    # Reverse order: ("SCOPE_B", "SCOPE_A") -> set match -> violation=0
    res_match = _case_result(case, actual_scope_codes=("SCOPE_B", "SCOPE_A"))
    m1 = build_safety_metrics(
        dataset,
        (res_match,),
        (sig,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case.case_id: case.input_sha256},
    )
    assert m1.metrics[0].numerator == 0
    assert m1.metrics[0].metric_value == "0"

    # Extra scope: ("SCOPE_A", "SCOPE_B", "SCOPE_C") -> violation=1
    res_mismatch = _case_result(case, actual_scope_codes=("SCOPE_A", "SCOPE_B", "SCOPE_C"))
    m2 = build_safety_metrics(
        dataset,
        (res_mismatch,),
        (sig,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case.case_id: case.input_sha256},
    )
    assert m2.metrics[0].numerator == 1
    assert m2.metrics[0].metric_value == "1"


def test_scope_ineligible_publication_violation() -> None:
    """Test SCOPE_INELIGIBLE bundle eligibility with actual_publication_allowed=True."""
    case = _safety_case_v12(
        case_id="c-inelig",
        bundle_eligibility_status="SCOPE_INELIGIBLE",
        expected_rule_outcome="NOT_INVOKED",
        expected_rule_ids=(),
        expected_scope_codes=("SCOPE_A",),
        expected_publication_allowed=False,
    )
    dataset = _dataset_with_cases_and_scopes((case,), (_scope("SCOPE_VIOLATION_RATE", "CASE"),))
    sig = _grounding_signal(case)

    # Actual publication allowed=True when bundle is SCOPE_INELIGIBLE -> violation!
    res_viol = _case_result(case, actual_scope_codes=("SCOPE_A",), actual_publication_allowed=True)
    m = build_safety_metrics(
        dataset,
        (res_viol,),
        (sig,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case.case_id: case.input_sha256},
    )
    assert m.metrics[0].numerator == 1

    # Actual publication allowed=False -> no violation
    res_ok = _case_result(case, actual_scope_codes=("SCOPE_A",), actual_publication_allowed=False)
    m_ok = build_safety_metrics(
        dataset,
        (res_ok,),
        (sig,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case.case_id: case.input_sha256},
    )
    assert m_ok.metrics[0].numerator == 0


def test_no_false_scope_violation_for_other_blocking_causes() -> None:
    """Verify SOURCE_INELIGIBLE or expected_publication_allowed=False does not trigger false Scope violation."""
    case = _safety_case_v12(
        case_id="c-src-inelig",
        source_eligibility_status="EXPIRED",
        bundle_eligibility_status="SOURCE_INELIGIBLE",
        expected_rule_outcome="NOT_INVOKED",
        expected_rule_ids=(),
        expected_scope_codes=("SCOPE_A",),
        expected_publication_allowed=False,
    )
    dataset = _dataset_with_cases_and_scopes((case,), (_scope("SCOPE_VIOLATION_RATE", "CASE"),))
    sig = _grounding_signal(case)

    # Publication allowed is True, but bundle is ELIGIBLE (only source is ineligible) -> NO scope violation!
    res = _case_result(case, actual_scope_codes=("SCOPE_A",), actual_publication_allowed=True)
    m = build_safety_metrics(
        dataset, (res,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case={case.case_id: case.input_sha256}
    )
    assert m.metrics[0].numerator == 0


def test_provider_and_retrieval_suppression_violations() -> None:
    """Test suppression violations when expected invocation is False."""
    case_p = _safety_case_v12(case_id="c-prov", expected_provider_invocation=False, expected_retrieval_invocation=True)
    case_r = _safety_case_v12(case_id="c-ret", expected_provider_invocation=True, expected_retrieval_invocation=False)
    scopes = (
        _scope("PROVIDER_INVOCATION_VIOLATION_RATE", "CASE"),
        _scope("RETRIEVAL_INVOCATION_VIOLATION_RATE", "CASE"),
    )
    dataset = _dataset_with_cases_and_scopes((case_p, case_r), scopes)
    sig_p = _grounding_signal(case_p)
    sig_r = _grounding_signal(case_r)

    # case_p invoked provider (violation)
    res_p = _case_result(case_p, actual_provider_invocation=True, actual_retrieval_invocation=True)
    # case_r suppressed retrieval (no violation)
    res_r = _case_result(case_r, actual_provider_invocation=True, actual_retrieval_invocation=False)

    m = build_safety_metrics(
        dataset,
        (res_p, res_r),
        (sig_p, sig_r),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={c.case_id: c.input_sha256 for c in (case_p, case_r)},
    )
    metrics_by_id = {item.metric_id: item for item in m.metrics}
    assert metrics_by_id["PROVIDER_INVOCATION_VIOLATION_RATE"].numerator == 1
    assert metrics_by_id["PROVIDER_INVOCATION_VIOLATION_RATE"].denominator == 1
    assert metrics_by_id["RETRIEVAL_INVOCATION_VIOLATION_RATE"].numerator == 0
    assert metrics_by_id["RETRIEVAL_INVOCATION_VIOLATION_RATE"].denominator == 1


def test_invocation_metric_zero_denominators() -> None:
    """When expected invocations are True, denominator is 0 -> ZERO_DENOMINATOR."""
    case = _safety_case_v12(case_id="c-inv-true", expected_provider_invocation=True, expected_retrieval_invocation=True)
    dataset = _dataset_with_cases_and_scopes((case,), (_scope("PROVIDER_INVOCATION_VIOLATION_RATE", "CASE"),))
    res = _case_result(case, actual_provider_invocation=True)
    sig = _grounding_signal(case)

    m = build_safety_metrics(
        dataset, (res,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case={case.case_id: case.input_sha256}
    )
    metric = m.metrics[0]
    assert metric.sample_case_count == 0
    assert metric.denominator == 0
    assert metric.reason_code == "ZERO_DENOMINATOR"
    assert metric.decision_status == DecisionStatus.INCONCLUSIVE


def test_fallback_mismatch_independent_tuple_members() -> None:
    """Test each of the 4 fallback runtime tuple members mismatching independently."""
    base_case = _safety_case_v12(
        case_id="c-fb",
        expected_execution_status="SUCCEEDED",
        expected_release_decision="PASS",
        expected_fallback_code=None,
        expected_publication_allowed=True,
    )
    dataset = _dataset_with_cases_and_scopes((base_case,), (_scope("FALLBACK_MISMATCH_RATE", "CASE"),))
    sig = _grounding_signal(base_case)
    sha_map = {base_case.case_id: base_case.input_sha256}

    # 1. execution_status mismatch
    r1 = _case_result(
        base_case,
        actual_execution_status="NO_RESULT",
        actual_release_decision="PASS",
        actual_fallback_code=None,
        actual_publication_allowed=True,
    )
    assert (
        build_safety_metrics(dataset, (r1,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map)
        .metrics[0]
        .numerator
        == 1
    )

    # 2. release_decision mismatch
    r2 = _case_result(
        base_case,
        actual_execution_status="SUCCEEDED",
        actual_release_decision="LIMITED",
        actual_fallback_code=None,
        actual_publication_allowed=True,
    )
    assert (
        build_safety_metrics(dataset, (r2,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map)
        .metrics[0]
        .numerator
        == 1
    )

    # 3. fallback_code mismatch
    r3 = _case_result(
        base_case,
        actual_execution_status="SUCCEEDED",
        actual_release_decision="PASS",
        actual_fallback_code="SAFETY_ROUTED",
        actual_publication_allowed=True,
    )
    assert (
        build_safety_metrics(dataset, (r3,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map)
        .metrics[0]
        .numerator
        == 1
    )

    # 4. publication_allowed mismatch
    r4 = _case_result(
        base_case,
        actual_execution_status="SUCCEEDED",
        actual_release_decision="PASS",
        actual_fallback_code=None,
        actual_publication_allowed=False,
    )
    assert (
        build_safety_metrics(dataset, (r4,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map)
        .metrics[0]
        .numerator
        == 1
    )

    # 5. CaseResult.execution_status difference alone must NOT cause fallback mismatch!
    # (Runtime tuple matches exactly)
    r_match = _case_result(
        base_case,
        actual_execution_status="SUCCEEDED",
        actual_release_decision="PASS",
        actual_fallback_code=None,
        actual_publication_allowed=True,
    )
    assert (
        build_safety_metrics(dataset, (r_match,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map)
        .metrics[0]
        .numerator
        == 0
    )


def test_forbidden_claim_emission() -> None:
    """Forbidden claim emitted causes CRITICAL_SAFETY_FAILURE_RATE."""
    case = _safety_case_v12(
        case_id="c-forbid",
        forbidden_claims=(
            {"claim_id": "forbid-1", "criticality": "CRITICAL", "reason_code": "R", "semantic_rule": "S"},
        ),
    )
    dataset = _dataset_with_cases_and_scopes((case,), (_scope("CRITICAL_SAFETY_FAILURE_RATE", "CASE"),))
    sig = _evaluated_signal_pair(case)

    # Actual claims contain forbid-1 -> critical failure
    res_viol = _case_result(case, actual_claim_ids=("forbid-1",), answer_sha256=sig.answer_sha256)
    m = build_safety_metrics(
        dataset,
        (res_viol,),
        (sig,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case.case_id: case.input_sha256},
    )
    assert m.metrics[0].numerator == 1

    # Actual claims do NOT contain forbid-1 -> no critical failure
    res_ok = _case_result(case, actual_claim_ids=("other-claim",), answer_sha256=sig.answer_sha256)
    m_ok = build_safety_metrics(
        dataset,
        (res_ok,),
        (sig,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case.case_id: case.input_sha256},
    )
    assert m_ok.metrics[0].numerator == 0


def test_each_grounding_signal_failure_boolean() -> None:
    """Test critical_unsupported_claim, uncited_medical_claim, and source_binding_misuse individually."""
    case = _safety_case_v12(case_id="c-sig-fail")
    dataset = _dataset_with_cases_and_scopes((case,), (_scope("CRITICAL_SAFETY_FAILURE_RATE", "CASE"),))
    sha_map = {case.case_id: case.input_sha256}

    # 1. critical_unsupported_claim
    s1 = _evaluated_signal_pair(case, critical_unsupported=True)
    r1 = _case_result(case, actual_claim_ids=("c1",), answer_sha256=s1.answer_sha256)
    assert (
        build_safety_metrics(dataset, (r1,), (s1,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map)
        .metrics[0]
        .numerator
        == 1
    )

    # 2. uncited_medical_claim
    s2 = _evaluated_signal_pair(case, uncited_med=True)
    r2 = _case_result(case, actual_claim_ids=("c1",), answer_sha256=s2.answer_sha256)
    assert (
        build_safety_metrics(dataset, (r2,), (s2,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map)
        .metrics[0]
        .numerator
        == 1
    )

    # 3. source_binding_misuse
    s3 = _evaluated_signal_pair(case, source_misuse=True)
    r3 = _case_result(case, actual_claim_ids=("c1",), answer_sha256=s3.answer_sha256)
    assert (
        build_safety_metrics(dataset, (r3,), (s3,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map)
        .metrics[0]
        .numerator
        == 1
    )


def test_multiple_critical_failures_in_one_case_yields_numerator_one() -> None:
    """One Case triggering multiple critical invariant IDs still contributes exactly 1."""
    case = _safety_case_v12(
        case_id="c-multi-fail",
        expected_response_level="ROUTINE",
        expected_safety_disposition="NORMAL",
        expected_scope_codes=("SCOPE_A",),
        forbidden_claims=(
            {"claim_id": "forbid-1", "criticality": "CRITICAL", "reason_code": "R", "semantic_rule": "S"},
        ),
    )
    dataset = _dataset_with_cases_and_scopes((case,), (_scope("CRITICAL_SAFETY_FAILURE_RATE", "CASE"),))
    sig = _evaluated_signal_pair(case, critical_unsupported=True, uncited_med=True, source_misuse=True)

    # Routing mismatch + forbidden claim + 3 signal failures + scope mismatch
    res = _case_result(
        case,
        actual_response_level="EMERGENCY",
        actual_safety_disposition="EMERGENCY_ROUTED",
        actual_scope_codes=("SCOPE_WRONG",),
        actual_claim_ids=("forbid-1",),
        answer_sha256=sig.answer_sha256,
    )
    m = build_safety_metrics(
        dataset, (res,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case={case.case_id: case.input_sha256}
    )
    assert m.metrics[0].numerator == 1
    assert m.metrics[0].denominator == 1


def test_valid_not_applicable_no_claims_binding() -> None:
    """Valid NOT_APPLICABLE_NO_CLAIMS binding when claims and citations are empty."""
    case = _safety_case_v12(case_id="c-no-claims")
    dataset = _dataset_with_cases_and_scopes((case,), (_scope("CRITICAL_SAFETY_FAILURE_RATE", "CASE"),))
    sig = _grounding_signal(case, status=GroundingSignalStatus.NOT_APPLICABLE_NO_CLAIMS, answer_sha256=None)
    res = _case_result(case, actual_claim_ids=(), actual_citation_evidence_ids=(), answer_sha256=None)

    m = build_safety_metrics(
        dataset, (res,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case={case.case_id: case.input_sha256}
    )
    assert m.metrics[0].execution_status == ExecutionStatus.COMPLETED
    assert m.metrics[0].numerator == 0


def test_grounding_signals_absent_vs_partial_absence() -> None:
    """All GroundingSignals absent produces NOT_EVALUATED; partial absence produces INVALID.

    Other 7 metrics must still calculate normally!
    """
    c1 = _safety_case_v12(case_id="c-1", input_sha256="1" * 64, group_id="g1")
    c2 = _safety_case_v12(case_id="c-2", input_sha256="2" * 64, group_id="g2")
    dataset = _dataset_with_cases_and_scopes((c1, c2))
    r1 = _case_result(c1)
    r2 = _case_result(c2)
    sha_map = {c1.case_id: c1.input_sha256, c2.case_id: c2.input_sha256}

    # 1. Complete absence: grounding_signals = ()
    m_all_absent = build_safety_metrics(
        dataset, (r1, r2), (), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    metrics_by_id = {m.metric_id: m for m in m_all_absent.metrics}
    # CRITICAL_SAFETY_FAILURE_RATE is NOT_EVALUATED
    assert metrics_by_id["CRITICAL_SAFETY_FAILURE_RATE"].execution_status == ExecutionStatus.NOT_EVALUATED
    assert metrics_by_id["CRITICAL_SAFETY_FAILURE_RATE"].metric_value is None
    # All other 7 metrics are COMPLETED!
    assert metrics_by_id["SAFETY_ROUTING_ACCURACY"].execution_status == ExecutionStatus.COMPLETED
    assert metrics_by_id["RULE_RECALL"].execution_status == ExecutionStatus.COMPLETED

    # 2. Partial absence: c1 has signal, c2 is missing signal
    sig_1 = _grounding_signal(c1)
    m_partial = build_safety_metrics(
        dataset, (r1, r2), (sig_1,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    metrics_partial = {m.metric_id: m for m in m_partial.metrics}
    # CRITICAL_SAFETY_FAILURE_RATE is INVALID
    assert metrics_partial["CRITICAL_SAFETY_FAILURE_RATE"].execution_status == ExecutionStatus.INVALID
    # Other 7 metrics remain COMPLETED!
    assert metrics_partial["SAFETY_ROUTING_ACCURACY"].execution_status == ExecutionStatus.COMPLETED


def test_duplicate_extra_cross_case_signal() -> None:
    """Duplicate, extra, or cross-Case signals make CRITICAL_SAFETY_FAILURE_RATE INVALID."""
    c1 = _safety_case_v12(case_id="c-1", input_sha256="1" * 64)
    dataset = _dataset_with_cases_and_scopes((c1,))
    r1 = _case_result(c1)
    sha_map = {c1.case_id: c1.input_sha256}

    # Duplicate signal for c1
    s1_a = _grounding_signal(c1)
    s1_b = _grounding_signal(c1)
    m_dup = build_safety_metrics(
        dataset, (r1,), (s1_a, s1_b), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert {m.metric_id: m for m in m_dup.metrics}[
        "CRITICAL_SAFETY_FAILURE_RATE"
    ].execution_status == ExecutionStatus.INVALID

    # Extra signal for unknown case "c-extra"
    s_extra = _grounding_signal(c1, case_id="c-extra")
    m_extra = build_safety_metrics(
        dataset, (r1,), (s1_a, s_extra), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert {m.metric_id: m for m in m_extra.metrics}[
        "CRITICAL_SAFETY_FAILURE_RATE"
    ].execution_status == ExecutionStatus.INVALID


def test_signal_self_hash_and_binding_mismatches() -> None:
    """Self-hash mismatch and binding mismatches make CRITICAL_SAFETY_FAILURE_RATE INVALID."""
    case = _safety_case_v12(case_id="c-1", input_sha256="1" * 64)
    dataset = _dataset_with_cases_and_scopes((case,))
    res = _case_result(case)
    sha_map = {case.case_id: case.input_sha256}

    # Corrupt self-hash
    s_bad_hash = _grounding_signal(case, corrupt_self_hash=True)
    m1 = build_safety_metrics(
        dataset, (res,), (s_bad_hash,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert {m.metric_id: m for m in m1.metrics}[
        "CRITICAL_SAFETY_FAILURE_RATE"
    ].execution_status == ExecutionStatus.INVALID

    # Wrong run_id on signal
    s_bad_run = _grounding_signal(case, run_id="99900000-0000-4000-8000-000000000000")
    m2 = build_safety_metrics(
        dataset, (res,), (s_bad_run,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert {m.metric_id: m for m in m2.metrics}[
        "CRITICAL_SAFETY_FAILURE_RATE"
    ].execution_status == ExecutionStatus.INVALID

    # Answer hash mismatch on signal
    s_bad_ans = _grounding_signal(case, answer_sha256="9" * 64)
    m3 = build_safety_metrics(
        dataset, (res,), (s_bad_ans,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert {m.metric_id: m for m in m3.metrics}[
        "CRITICAL_SAFETY_FAILURE_RATE"
    ].execution_status == ExecutionStatus.INVALID


def test_case_result_integrity_failures() -> None:
    """Run-wide CaseResult integrity failures make all supported Safety metrics INVALID."""
    c1 = _safety_case_v12(case_id="c-1", input_sha256="1" * 64)
    c2 = _safety_case_v12(case_id="c-2", input_sha256="2" * 64)
    dataset = _dataset_with_cases_and_scopes((c1, c2))
    r1 = _case_result(c1)
    s1 = _grounding_signal(c1)
    s2 = _grounding_signal(c2)
    sha_map = {c1.case_id: c1.input_sha256, c2.case_id: c2.input_sha256}

    # 1. Missing CaseResult (only r1 supplied)
    m_miss = build_safety_metrics(
        dataset, (r1,), (s1, s2), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert all(m.execution_status == ExecutionStatus.INVALID for m in m_miss.metrics)

    # 2. Duplicate CaseResult
    m_dup = build_safety_metrics(
        dataset, (r1, r1), (s1, s2), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert all(m.execution_status == ExecutionStatus.INVALID for m in m_dup.metrics)

    # 3. Mixed / unexpected run_id
    r_bad_run = _case_result(c2, run_id="88800000-0000-4000-8000-000000000000")
    m_run = build_safety_metrics(
        dataset, (r1, r_bad_run), (s1, s2), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert all(m.execution_status == ExecutionStatus.INVALID for m in m_run.metrics)

    # 4. Input sha256 mismatch
    r_bad_sha = _case_result(c2, input_sha256="f" * 64)
    m_sha = build_safety_metrics(
        dataset, (r1, r_bad_sha), (s1, s2), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert all(m.execution_status == ExecutionStatus.INVALID for m in m_sha.metrics)


def test_scoped_case_result_incomplete_status_priority() -> None:
    """Scoped incomplete CaseResult execution states propagate with strict priority:

    INVALID > ERROR > NOT_IMPLEMENTED > NOT_EVALUATED
    """
    c1 = _safety_case_v12(case_id="c-1", input_sha256="1" * 64)
    c2 = _safety_case_v12(case_id="c-2", input_sha256="2" * 64)
    dataset = _dataset_with_cases_and_scopes((c1, c2), (_scope("SAFETY_ROUTING_ACCURACY", "CASE"),))
    sha_map = {c1.case_id: c1.input_sha256, c2.case_id: c2.input_sha256}

    # ERROR + NOT_EVALUATED -> ERROR wins
    r1_err = _case_result(c1, execution_status="ERROR")
    r2_noteval = _case_result(c2, execution_status="NOT_EVALUATED")
    m1 = build_safety_metrics(
        dataset, (r1_err, r2_noteval), (), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert m1.metrics[0].execution_status == ExecutionStatus.ERROR

    # INVALID + ERROR -> INVALID wins
    r1_inv = _case_result(c1, execution_status="INVALID")
    m2 = build_safety_metrics(
        dataset, (r1_inv, r1_err), (), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert m2.metrics[0].execution_status == ExecutionStatus.INVALID

    # NOT_IMPLEMENTED + NOT_EVALUATED -> NOT_IMPLEMENTED wins
    r1_notimpl = _case_result(c1, execution_status="NOT_IMPLEMENTED")
    m3 = build_safety_metrics(
        dataset, (r1_notimpl, r2_noteval), (), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert m3.metrics[0].execution_status == ExecutionStatus.NOT_IMPLEMENTED


def test_reason_codes_zero_denom_min_case_and_group() -> None:
    """Test reason codes ZERO_DENOMINATOR, MINIMUM_CASE_COUNT_NOT_MET, MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET."""
    case = _safety_case_v12(case_id="c-1", group_id="g-1")
    res = _case_result(case)
    sig = _grounding_signal(case)
    sha_map = {case.case_id: case.input_sha256}

    # 1. MINIMUM_CASE_COUNT_NOT_MET: minimum_case_count = 5, but we only have 1 case
    scope_min_case = _scope("SAFETY_ROUTING_ACCURACY", "CASE", minimum_case_count=5)
    ds_min_case = _dataset_with_cases_and_scopes((case,), (scope_min_case,))
    m_case = build_safety_metrics(
        ds_min_case, (res,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert m_case.metrics[0].execution_status == ExecutionStatus.COMPLETED
    assert m_case.metrics[0].decision_status == DecisionStatus.INCONCLUSIVE
    assert m_case.metrics[0].reason_code == "MINIMUM_CASE_COUNT_NOT_MET"

    # 2. MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET: minimum_independent_group_count = 3, but 1 group
    scope_min_grp = _scope("SAFETY_ROUTING_ACCURACY", "CASE", minimum_independent_group_count=3)
    ds_min_grp = _dataset_with_cases_and_scopes((case,), (scope_min_grp,))
    m_grp = build_safety_metrics(
        ds_min_grp, (res,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert m_grp.metrics[0].reason_code == "MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET"
    assert m_grp.metrics[0].decision_status == DecisionStatus.INCONCLUSIVE


def test_deterministic_fixed_seed_ci() -> None:
    """Bootstrap CI bounds must be identical across runs with the same seed."""
    c1 = _safety_case_v12(case_id="c-1", group_id="g-1")
    c2 = _safety_case_v12(case_id="c-2", group_id="g-2")
    dataset = _dataset_with_cases_and_scopes((c1, c2), (_scope("SAFETY_ROUTING_ACCURACY", "CASE", seed=777),))
    r1 = _case_result(c1, actual_response_level="ROUTINE")
    r2 = _case_result(c2, actual_response_level="EMERGENCY")  # 1 match, 1 mismatch
    s1 = _grounding_signal(c1)
    s2 = _grounding_signal(c2)
    sha_map = {c1.case_id: c1.input_sha256, c2.case_id: c2.input_sha256}

    run1 = build_safety_metrics(
        dataset, (r1, r2), (s1, s2), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    run2 = build_safety_metrics(
        dataset, (r1, r2), (s1, s2), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )

    m1 = run1.metrics[0]
    m2 = run2.metrics[0]
    assert m1.ci_lower is not None and m1.ci_upper is not None
    assert m1.ci_lower == m2.ci_lower
    assert m1.ci_upper == m2.ci_upper


def test_unsupported_algorithm_signature_returns_not_implemented() -> None:
    """Unsupported algorithm signatures return NOT_IMPLEMENTED, even when run integrity is invalid."""
    case = _safety_case_v12(case_id="c-1")
    bad_scope = _scope("SAFETY_ROUTING_ACCURACY", "CASE", estimator_id="UNSUPPORTED_ESTIMATOR")
    dataset = _dataset_with_cases_and_scopes((case,), (bad_scope,))

    # Pass an empty CaseResult tuple (run integrity failure)
    m = build_safety_metrics(dataset, (), (), expected_run_id=RUN_ID, expected_input_sha256_by_case={})
    assert m.metrics[0].execution_status == ExecutionStatus.NOT_IMPLEMENTED


def test_valid_legacy_case_returns_not_implemented() -> None:
    """A valid legacy Case lacking typed rule outcome or bundle eligibility returns NOT_IMPLEMENTED

    for affected scopes without crashing or returning INVALID.
    """
    path = EVALS_ROOT / "retrieval/cases/dev-foundation-v1/rag-dev-safety-001.json"
    legacy_payload = json.loads(path.read_text(encoding="utf-8"))
    legacy_case = cast(EvaluationCaseContract, EVALUATION_CASE_ADAPTER.validate_python(legacy_payload))

    dataset = _dataset_with_cases_and_scopes((legacy_case,))
    res = _case_result(legacy_case)
    sig = _grounding_signal(legacy_case)
    sha_map = {legacy_case.case_id: legacy_case.input_sha256}

    m = build_safety_metrics(dataset, (res,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map)
    metrics_by_id = {item.metric_id: item for item in m.metrics}

    # Affected scopes requiring typed fields:
    assert metrics_by_id["RULE_RECALL"].execution_status == ExecutionStatus.NOT_IMPLEMENTED
    assert metrics_by_id["RULE_REVERSAL_RATE"].execution_status == ExecutionStatus.NOT_IMPLEMENTED
    assert metrics_by_id["SCOPE_VIOLATION_RATE"].execution_status == ExecutionStatus.NOT_IMPLEMENTED
    assert metrics_by_id["CRITICAL_SAFETY_FAILURE_RATE"].execution_status == ExecutionStatus.NOT_IMPLEMENTED

    # Scopes NOT requiring those fields calculate normally:
    assert metrics_by_id["SAFETY_ROUTING_ACCURACY"].execution_status == ExecutionStatus.COMPLETED
    assert metrics_by_id["FALLBACK_MISMATCH_RATE"].execution_status == ExecutionStatus.COMPLETED


def test_suite_critical_invariants_mismatch_returns_not_implemented() -> None:
    """If suite critical_invariant_ids does not match approved set, CRITICAL_SAFETY_FAILURE_RATE is NOT_IMPLEMENTED."""
    case = _safety_case_v12(case_id="c-1")
    # Missing some invariants in suite definition
    unapproved_invariants = ("CRITICAL_UNSUPPORTED_CLAIM", "RULE_REVERSAL")
    dataset = _dataset_with_cases_and_scopes((case,), critical_invariants=unapproved_invariants)
    res = _case_result(case)
    sig = _grounding_signal(case)
    sha_map = {case.case_id: case.input_sha256}

    m = build_safety_metrics(dataset, (res,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map)
    metrics_by_id = {item.metric_id: item for item in m.metrics}

    assert metrics_by_id["CRITICAL_SAFETY_FAILURE_RATE"].execution_status == ExecutionStatus.NOT_IMPLEMENTED
    assert metrics_by_id["SAFETY_ROUTING_ACCURACY"].execution_status == ExecutionStatus.COMPLETED


def test_critical_safety_failure_rate_denominator_with_dev_required_false() -> None:
    """Verify that CRITICAL_SAFETY_FAILURE_RATE has denominator 1 per applicable scoped completed Case

    even when the policy scope has required=False.
    """
    case = _safety_case_v12(case_id="c-1")
    scope = _scope("CRITICAL_SAFETY_FAILURE_RATE", "CASE", required=False)
    dataset = _dataset_with_cases_and_scopes((case,), (scope,))
    res = _case_result(case)
    sig = _grounding_signal(case)
    sha_map = {case.case_id: case.input_sha256}

    m = build_safety_metrics(dataset, (res,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map)
    assert m.metrics[0].denominator == 1
    assert m.metrics[0].sample_case_count == 1


def test_privacy_and_sentinels() -> None:
    """Sentinel PII, credentials, and query bodies must never appear in MetricResults or errors."""
    sentinel_token = "TOP_SECRET_CREDENTIAL_OR_PII"
    case = _safety_case_v12(
        case_id="c-privacy",
        query=f"Patient confidential inquiry containing {sentinel_token}",
        forbidden_claims=(
            {
                "claim_id": "forbid-priv",
                "criticality": "CRITICAL",
                "reason_code": f"REASON_{sentinel_token}",
                "semantic_rule": f"RULE_{sentinel_token}",
            },
        ),
    )
    dataset = _dataset_with_cases_and_scopes((case,))
    res = _case_result(case)
    sig = _grounding_signal(case)
    sha_map = {case.case_id: case.input_sha256}

    # Verify that the sentinel is actually present in the input fixtures
    assert sentinel_token in case.query
    assert any(sentinel_token in claim.semantic_rule for claim in (case.expected.forbidden_claims or ()))

    # 1. Normal completed execution path
    results = build_safety_metrics(
        dataset, (res,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    json_repr = results.model_dump_json()

    # Privacy: no PII/credential/query leaks into output metrics
    assert sentinel_token not in json_repr
    assert "token" not in json_repr.lower()

    # Pure immutability: input structures are not mutated
    assert len(dataset.cases) == 1
    assert dataset.cases[0] is case

    # 2. Privacy on INVALID failure path with malformed CaseResult
    res_malformed = _case_result(case, run_id="99900000-0000-4000-8000-000000000000")
    results_invalid_res = build_safety_metrics(
        dataset, (res_malformed,), (sig,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert any(m.execution_status == ExecutionStatus.INVALID for m in results_invalid_res.metrics)
    assert sentinel_token not in results_invalid_res.model_dump_json()
    assert "token" not in results_invalid_res.model_dump_json().lower()

    # 3. Privacy on INVALID failure path with malformed GroundingSignal
    sig_malformed = _grounding_signal(case, corrupt_self_hash=True)
    results_invalid_sig = build_safety_metrics(
        dataset, (res,), (sig_malformed,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    crit_m = {m.metric_id: m for m in results_invalid_sig.metrics}["CRITICAL_SAFETY_FAILURE_RATE"]
    assert crit_m.execution_status == ExecutionStatus.INVALID
    assert sentinel_token not in results_invalid_sig.model_dump_json()
    assert "token" not in results_invalid_sig.model_dump_json().lower()


def test_foreign_grounding_signal_fails_closed_as_invalid() -> None:
    """Foreign GroundingSignal must fail-closed as INVALID, never NOT_EVALUATED, regardless of input order."""
    case = _safety_case_v12(case_id="c-1", input_sha256="1" * 64)
    scope = _scope("CRITICAL_SAFETY_FAILURE_RATE", "CASE")
    dataset = _dataset_with_cases_and_scopes((case,), (scope,))
    res = _case_result(case)
    sig_valid = _grounding_signal(case)
    sig_foreign = _grounding_signal(case, case_id="c-foreign-unscoped")
    sha_map = {case.case_id: case.input_sha256}

    # 1. Foreign signal alone -> must be INVALID, NOT NOT_EVALUATED
    m_alone = build_safety_metrics(
        dataset, (res,), (sig_foreign,), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert m_alone.metrics[0].execution_status == ExecutionStatus.INVALID

    # 2. Foreign signal first: (sig_foreign, sig_valid) -> must be INVALID
    m_first = build_safety_metrics(
        dataset, (res,), (sig_foreign, sig_valid), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert m_first.metrics[0].execution_status == ExecutionStatus.INVALID

    # 3. Foreign signal last: (sig_valid, sig_foreign) -> must be INVALID
    m_last = build_safety_metrics(
        dataset, (res,), (sig_valid, sig_foreign), expected_run_id=RUN_ID, expected_input_sha256_by_case=sha_map
    )
    assert m_last.metrics[0].execution_status == ExecutionStatus.INVALID


def test_incomplete_case_signal_binding_mismatch_is_invalid() -> None:
    """A GroundingSignal supplied for an incomplete CaseResult must still pass binding checks."""
    c2 = _safety_case_v12(case_id="c-2", input_sha256="2" * 64)

    # Policy scope covers c1 only (c1 completed, c2 incomplete)
    scope = _scope("CRITICAL_SAFETY_FAILURE_RATE", "CASE", slice_id="slice-1")
    c1_scoped = _safety_case_v12(case_id="c-1", slice_ids=("slice-1",), input_sha256="1" * 64)
    dataset = _dataset_with_cases_and_scopes((c1_scoped, c2), (scope,))

    r1_completed = _case_result(c1_scoped, execution_status="COMPLETED")
    r2_error = _case_result(c2, execution_status="ERROR", answer_sha256="a" * 64)

    s1_valid = _grounding_signal(c1_scoped)
    # Signal s2 has answer_sha256 that does NOT match r2_error.answer_sha256
    s2_mismatched = _grounding_signal(c2, answer_sha256="b" * 64)

    sha_map = {c1_scoped.case_id: c1_scoped.input_sha256, c2.case_id: c2.input_sha256}

    m = build_safety_metrics(
        dataset,
        (r1_completed, r2_error),
        (s1_valid, s2_mismatched),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case=sha_map,
    )
    # Scope for c1 fails with INVALID because supplied signal s2 fails binding integrity!
    assert m.metrics[0].execution_status == ExecutionStatus.INVALID
