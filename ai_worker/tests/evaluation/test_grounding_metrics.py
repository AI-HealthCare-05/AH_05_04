from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.grounding_metrics import build_grounding_metrics
from ai_worker.tasks.evaluation.loaders import EvaluationCaseContract, ValidatedDataset, load_dataset
from ai_worker.tasks.evaluation.schemas.artifacts import CASE_RESULT_ADAPTER, CaseResult
from ai_worker.tasks.evaluation.schemas.authoring import (
    Criticality,
    EvidenceMappingEntry,
    EvidenceMappingManifest,
    EvidenceTargetKind,
    EvidenceType,
    ExpectedCitation,
    GoldClaim,
)
from ai_worker.tasks.evaluation.schemas.common import (
    DecisionStatus,
    ExecutionStatus,
    ImmutableReference,
    Partition,
    TaskType,
)
from ai_worker.tasks.evaluation.schemas.grounding_v1 import (
    ClaimCitationObservation,
    GroundingSignal,
)
from ai_worker.tasks.evaluation.schemas.policy import ComparisonPolicy, ComparisonScope

EVALS_ROOT = Path(__file__).parents[3] / "evals"
BASE_DATASET = load_dataset(
    EVALS_ROOT / "retrieval/manifests/dev-foundation-v1.dataset.json",
    evals_root=EVALS_ROOT,
)
RUN_ID = "16000000-0000-4000-8000-000000000001"
DATASET_CODE = BASE_DATASET.manifest.dataset_code
DATASET_VERSION = BASE_DATASET.manifest.dataset_version
VARIANT_HASH = "a" * 64
EVIDENCE_REF_1 = "ev-synthetic-rule-001"
EVIDENCE_REF_2 = "ev-synthetic-rule-002"
CONTENT_SHA_1 = "1" * 64
CONTENT_SHA_2 = "2" * 64


def _scope(
    metric_id: str,
    unit_of_analysis: str,
    *,
    minimum_case_count: int = 1,
    minimum_independent_group_count: int = 1,
    estimator_id: str = "MICRO_RATIO",
    ci_method_id: str = "PERCENTILE_CLUSTER_BOOTSTRAP",
    seed: int = 160,
) -> ComparisonScope:
    return ComparisonScope.model_validate(
        {
            "metric_id": metric_id,
            "metric_version": "1.0.0",
            "partition": "DEV",
            "slice_id": "ALL",
            "required": False,
            "unit_of_analysis": unit_of_analysis,
            "estimator_id": estimator_id,
            "estimator_version": "1.0.0",
            "minimum_case_count": minimum_case_count,
            "independence_unit": "question_template",
            "cluster_dimension": "question_template",
            "minimum_independent_group_count": minimum_independent_group_count,
            "threshold": "0",
            "decision_basis": "DIAGNOSTIC_ONLY",
            "ci_method_id": ci_method_id,
            "ci_method_version": "1.0.0",
            "ci_parameters": {"iterations": 200, "level": "0.95", "sidedness": "TWO_SIDED"},
            "seed": seed,
        }
    )


ALL_FIVE_SCOPES = (
    _scope("CITATION_PRECISION", "CITATION"),
    _scope("CITATION_COVERAGE", "EXPECTED_CITATION"),
    _scope("UNSUPPORTED_CLAIM_RATE", "CLAIM"),
    _scope("CRITICAL_UNSUPPORTED_CLAIM_RATE", "CRITICAL_CLAIM"),
    _scope("UNCITED_MEDICAL_CLAIM_RATE", "MEDICAL_CLAIM"),
)


def _make_observation(data: dict[str, Any]) -> ClaimCitationObservation:
    payload = dict(data)
    payload["schema_id"] = "rag-eval.claim-citation-observation"
    payload["schema_version"] = "1.0.0"
    payload["observation_sha256"] = "0" * 64
    temp = ClaimCitationObservation.model_validate(payload)
    canon_dump = cast(dict[str, JsonValue], temp.model_dump(mode="json"))
    real_sha = canonical_sha256(canon_dump, excluded_top_level_keys=frozenset({"observation_sha256"}))
    payload["observation_sha256"] = real_sha
    return ClaimCitationObservation.model_validate(payload)


def _make_grounding_signal(data: dict[str, Any]) -> GroundingSignal:
    payload = dict(data)
    payload["schema_id"] = "rag-eval.grounding-signal"
    payload["schema_version"] = "1.0.0"
    payload["signal_sha256"] = "0" * 64
    temp = GroundingSignal.model_validate(payload)
    canon_dump = cast(dict[str, JsonValue], temp.model_dump(mode="json"))
    real_sha = canonical_sha256(canon_dump, excluded_top_level_keys=frozenset({"signal_sha256"}))
    payload["signal_sha256"] = real_sha
    return GroundingSignal.model_validate(payload)


def _grounding_case(
    *,
    case_id: str,
    input_sha256: str,
    group_id: str,
    gold_claims: tuple[GoldClaim, ...],
    expected_citations: tuple[ExpectedCitation, ...],
    task_type: TaskType = TaskType.ANSWER_GROUNDING,
    partition: Partition = Partition.DEV,
) -> EvaluationCaseContract:
    base = next(case for case in BASE_DATASET.cases if case.task_type.value == task_type.value)
    expected = base.expected.model_copy(
        update={
            "gold_claims": gold_claims,
            "expected_citations": expected_citations,
        }
    )
    leakage_groups = base.leakage_group_ids.model_copy(update={"question_template": group_id})
    return cast(
        EvaluationCaseContract,
        base.model_copy(
            update={
                "case_id": case_id,
                "input_sha256": input_sha256,
                "partition": partition,
                "slice_ids": ("SYNTHETIC_GROUNDING",),
                "leakage_group_ids": leakage_groups,
                "expected": expected,
            }
        ),
    )


def _evidence_mapping() -> EvidenceMappingManifest:
    ref_1 = ImmutableReference(id="ref-1", version="1.0.0", hash=CONTENT_SHA_1)
    ref_2 = ImmutableReference(id="ref-2", version="1.0.0", hash=CONTENT_SHA_2)
    entries = (
        EvidenceMappingEntry(
            evidence_ref_id=EVIDENCE_REF_2,
            evidence_type=EvidenceType.INTERACTION_RULE,
            stable_key="SYNTHETIC_RULE_B",
            source_version="1.0.0",
            locator="$.rule_2",
            content_sha256=CONTENT_SHA_2,
            target_kind=EvidenceTargetKind.RUNTIME_TYPED_REF,
            runtime_typed_ref=ref_2,
            fixture_record_ref=None,
        ),
        EvidenceMappingEntry(
            evidence_ref_id=EVIDENCE_REF_1,
            evidence_type=EvidenceType.PRESCRIPTION,
            stable_key="SYNTHETIC_MED_A",
            source_version="1.0.0",
            locator="$.section_1",
            content_sha256=CONTENT_SHA_1,
            target_kind=EvidenceTargetKind.RUNTIME_TYPED_REF,
            runtime_typed_ref=ref_1,
            fixture_record_ref=None,
        ),
    )
    payload: dict[str, Any] = {
        "schema_id": "rag-eval.evidence-mapping-manifest",
        "schema_version": "1.0.0",
        "mapping_id": "synthetic-evidence-map",
        "mapping_version": "1.0.0",
        "entries": [e.model_dump(mode="json") for e in entries],
        "review_provenance": BASE_DATASET.evidence_mapping.review_provenance.model_dump(mode="json"),
        "manifest_sha256": "0" * 64,
    }
    temp = EvidenceMappingManifest.model_validate(payload)
    canon_dump = cast(dict[str, JsonValue], temp.model_dump(mode="json"))
    real_sha = canonical_sha256(canon_dump, excluded_top_level_keys=frozenset({"manifest_sha256"}))
    payload["manifest_sha256"] = real_sha
    return EvidenceMappingManifest.model_validate(payload)


def _dataset_with_cases_and_scopes(
    cases: tuple[EvaluationCaseContract, ...],
    scopes: tuple[ComparisonScope, ...] = ALL_FIVE_SCOPES,
    evidence_map: EvidenceMappingManifest | None = None,
) -> ValidatedDataset:
    policy = ComparisonPolicy.model_validate(
        {
            **BASE_DATASET.comparison_policy.model_dump(mode="json"),
            "scopes": [scope.model_dump(mode="json") for scope in scopes],
        }
    )
    return replace(
        BASE_DATASET,
        cases=cases,
        comparison_policy=policy,
        evidence_mapping=evidence_map or _evidence_mapping(),
    )


def _grounding_case_result(
    case: EvaluationCaseContract,
    *,
    actual_claim_ids: tuple[str, ...],
    actual_citation_evidence_ids: tuple[str, ...],
    answer_sha256: str = "a" * 64,
    execution_status: str = "COMPLETED",
) -> CaseResult:
    return CASE_RESULT_ADAPTER.validate_python(
        {
            "schema_id": "rag-eval.case-result",
            "schema_version": "1.0.0",
            "run_id": RUN_ID,
            "case_id": case.case_id,
            "dataset_code": case.dataset_code,
            "dataset_version": case.dataset_version,
            "task_type": case.task_type.value,
            "partition": case.partition.value,
            "input_sha256": case.input_sha256,
            "execution_status": execution_status,
            "decision_status": "N/A",
            "failure_codes": [],
            "retrieved_evidence_ids": None,
            "selected_evidence_ids": None,
            "actual_claim_ids": list(actual_claim_ids),
            "actual_citation_evidence_ids": list(actual_citation_evidence_ids),
            "actual_rule_ids": () if case.task_type is not TaskType.ANSWER_GROUNDING else None,
            "actual_scope_codes": () if case.task_type is not TaskType.ANSWER_GROUNDING else None,
            "actual_response_level": None if case.task_type is TaskType.ANSWER_GROUNDING else "ROUTINE",
            "actual_safety_disposition": None if case.task_type is TaskType.ANSWER_GROUNDING else "NORMAL",
            "actual_execution_status": None if case.task_type is TaskType.ANSWER_GROUNDING else "SUCCEEDED",
            "actual_release_decision": None if case.task_type is TaskType.ANSWER_GROUNDING else "PASS",
            "actual_fallback_code": None,
            "actual_provider_invocation": None if case.task_type is TaskType.ANSWER_GROUNDING else True,
            "actual_retrieval_invocation": None if case.task_type is TaskType.ANSWER_GROUNDING else True,
            "actual_publication_allowed": None if case.task_type is TaskType.ANSWER_GROUNDING else True,
            "actual_sections": [],
            "omitted_sections": [],
            "risk_level": None if case.task_type is TaskType.ANSWER_GROUNDING else "GENERAL",
            "answer_sha256": answer_sha256,
            "latency_ms": 0,
            "input_token_count": 0,
            "output_token_count": 0,
            "estimated_cost": "0",
        }
    )


def _case_a() -> tuple[EvaluationCaseContract, CaseResult, ClaimCitationObservation]:
    gold_claim_a = GoldClaim(
        claim_id="claim-a1",
        claim_text="SYNTHETIC_MEDICAL_CLAIM_A",
        required=True,
        criticality=Criticality.NON_CRITICAL,
        supporting_evidence_ref_ids=(EVIDENCE_REF_1, EVIDENCE_REF_2),
    )
    expected_cit_a1 = ExpectedCitation(claim_id="claim-a1", evidence_ref_id=EVIDENCE_REF_1, locator="$.section_1")
    expected_cit_a2 = ExpectedCitation(claim_id="claim-a1", evidence_ref_id=EVIDENCE_REF_2, locator="$.rule_2")
    case_a = _grounding_case(
        case_id="case-a",
        input_sha256="1" * 64,
        group_id="group-a",
        gold_claims=(gold_claim_a,),
        expected_citations=(expected_cit_a1, expected_cit_a2),
        task_type=TaskType.ANSWER_GROUNDING,
    )
    result_a = _grounding_case_result(
        case_a,
        actual_claim_ids=("claim-a1",),
        actual_citation_evidence_ids=(EVIDENCE_REF_1, EVIDENCE_REF_2),
        answer_sha256="a" * 64,
    )
    obs_a = _make_observation(
        {
            "run_id": RUN_ID,
            "case_id": "case-a",
            "task_type": "ANSWER_GROUNDING",
            "dataset_code": DATASET_CODE,
            "dataset_version": DATASET_VERSION,
            "input_sha256": "1" * 64,
            "answer_sha256": "a" * 64,
            "answer_variant_manifest_hash": VARIANT_HASH,
            "validation_execution_status": "EVALUATED",
            "validation_decision": "VALIDATED",
            "validation_reason_codes": [],
            "validated_selection_sha256": "3" * 64,
            "authorization_decision": "AUTHORIZED",
            "authorization_reason_codes": [],
            "authorization_receipt_ref": {"id": "auth-receipt-1", "version": "1.0.0", "hash": "4" * 64},
            "authorization_receipt_sha256": "4" * 64,
            "claims": [
                {
                    "claim_key": "claim-a1",
                    "claim_kind": "MEDICAL",
                    "criticality": "NON_CRITICAL",
                    "criticality_source": "GOLD_EXACT_MATCH",
                    "criticality_review_ref": None,
                    "support_status": "SUPPORTED",
                    "support_receipt_sha256": "5" * 64,
                    "citations": [
                        {
                            "citation_key": "cit-a1",
                            "claim_key": "claim-a1",
                            "source_type": "PRESCRIPTION",
                            "evidence_ref_id": EVIDENCE_REF_1,
                            "source_version": "1.0.0",
                            "locator": "$.section_1",
                            "content_sha256": CONTENT_SHA_1,
                            "accepted": True,
                            "validation_reason_code": None,
                            "authorized": True,
                            "authorization_reason_code": None,
                            "authorization_selection_sha256": "6" * 64,
                            "gold_source_matched": True,
                        },
                        {
                            "citation_key": "cit-a2",
                            "claim_key": "claim-a1",
                            "source_type": "INTERACTION_RULE",
                            "evidence_ref_id": EVIDENCE_REF_2,
                            "source_version": "1.0.0",
                            "locator": "$.wrong_locator",
                            "content_sha256": CONTENT_SHA_2,
                            "accepted": True,
                            "validation_reason_code": None,
                            "authorized": True,
                            "authorization_reason_code": None,
                            "authorization_selection_sha256": "7" * 64,
                            "gold_source_matched": False,
                        },
                    ],
                }
            ],
        }
    )
    return case_a, result_a, obs_a


def _case_b() -> tuple[EvaluationCaseContract, CaseResult, ClaimCitationObservation]:
    gold_claim_b = GoldClaim(
        claim_id="claim-b1",
        claim_text="SYNTHETIC_MEDICAL_CLAIM_B",
        required=True,
        criticality=Criticality.CRITICAL,
        supporting_evidence_ref_ids=(EVIDENCE_REF_1,),
    )
    expected_cit_b1 = ExpectedCitation(claim_id="claim-b1", evidence_ref_id=EVIDENCE_REF_1, locator="$.section_1")
    case_b = _grounding_case(
        case_id="case-b",
        input_sha256="2" * 64,
        group_id="group-b",
        gold_claims=(gold_claim_b,),
        expected_citations=(expected_cit_b1,),
        task_type=TaskType.ANSWER_GROUNDING,
    )
    result_b = _grounding_case_result(
        case_b,
        actual_claim_ids=("claim-b1",),
        actual_citation_evidence_ids=(),
        answer_sha256="b" * 64,
    )
    obs_b = _make_observation(
        {
            "run_id": RUN_ID,
            "case_id": "case-b",
            "task_type": "ANSWER_GROUNDING",
            "dataset_code": DATASET_CODE,
            "dataset_version": DATASET_VERSION,
            "input_sha256": "2" * 64,
            "answer_sha256": "b" * 64,
            "answer_variant_manifest_hash": VARIANT_HASH,
            "validation_execution_status": "EVALUATED",
            "validation_decision": "VALIDATED",
            "validation_reason_codes": [],
            "validated_selection_sha256": "3" * 64,
            "authorization_decision": None,
            "authorization_reason_codes": [],
            "authorization_receipt_ref": None,
            "authorization_receipt_sha256": None,
            "claims": [
                {
                    "claim_key": "claim-b1",
                    "claim_kind": "MEDICAL",
                    "criticality": "CRITICAL",
                    "criticality_source": "GOLD_EXACT_MATCH",
                    "criticality_review_ref": None,
                    "support_status": "SUPPORTED",
                    "support_receipt_sha256": "8" * 64,
                    "citations": [],
                }
            ],
        }
    )
    return case_b, result_b, obs_b


def _case_c() -> tuple[EvaluationCaseContract, CaseResult, ClaimCitationObservation]:
    gold_claim_c = GoldClaim(
        claim_id="claim-c1",
        claim_text="SYNTHETIC_AUXILIARY_CLAIM_C",
        required=False,
        criticality=Criticality.NON_CRITICAL,
        supporting_evidence_ref_ids=(),
    )
    case_c = _grounding_case(
        case_id="case-c",
        input_sha256="3" * 64,
        group_id="group-c",
        gold_claims=(gold_claim_c,),
        expected_citations=(),
        task_type=TaskType.ANSWER_GROUNDING,
    )
    result_c = _grounding_case_result(
        case_c,
        actual_claim_ids=("claim-c1",),
        actual_citation_evidence_ids=(),
        answer_sha256="c" * 64,
    )
    obs_c = _make_observation(
        {
            "run_id": RUN_ID,
            "case_id": "case-c",
            "task_type": "ANSWER_GROUNDING",
            "dataset_code": DATASET_CODE,
            "dataset_version": DATASET_VERSION,
            "input_sha256": "3" * 64,
            "answer_sha256": "c" * 64,
            "answer_variant_manifest_hash": VARIANT_HASH,
            "validation_execution_status": "EVALUATED",
            "validation_decision": "VALIDATED",
            "validation_reason_codes": [],
            "validated_selection_sha256": "3" * 64,
            "authorization_decision": None,
            "authorization_reason_codes": [],
            "authorization_receipt_ref": None,
            "authorization_receipt_sha256": None,
            "claims": [
                {
                    "claim_key": "claim-c1",
                    "claim_kind": "AUXILIARY",
                    "criticality": "NON_CRITICAL",
                    "criticality_source": "GOLD_EXACT_MATCH",
                    "criticality_review_ref": None,
                    "support_status": "PARTIALLY_SUPPORTED",
                    "support_receipt_sha256": "9" * 64,
                    "citations": [],
                }
            ],
        }
    )
    return case_c, result_c, obs_c


def _case_safety(
    *,
    case_id: str = "case-s1",
    input_sha256: str = "4" * 64,
    answer_sha256: str = "d" * 64,
    group_id: str = "group-s",
    has_claims: bool = True,
    uncited_medical: bool = False,
    critical_unsupported: bool = False,
    source_binding_misuse: bool = False,
    signal_status: str = "EVALUATED",
    observation_ref: dict[str, Any] | None = None,
) -> tuple[EvaluationCaseContract, CaseResult, ClaimCitationObservation | None, GroundingSignal]:
    gold_claims: tuple[GoldClaim, ...] = ()
    expected_citations: tuple[ExpectedCitation, ...] = ()
    actual_claim_ids: tuple[str, ...] = ()
    actual_citation_evidence_ids: tuple[str, ...] = ()

    if has_claims:
        gold_claims = (
            GoldClaim(
                claim_id="claim-s1",
                claim_text="SYNTHETIC_SAFETY_CLAIM",
                required=True,
                criticality=Criticality.CRITICAL if critical_unsupported else Criticality.NON_CRITICAL,
                supporting_evidence_ref_ids=(EVIDENCE_REF_1,) if not uncited_medical else (),
            ),
        )
        if not uncited_medical:
            expected_citations = (
                ExpectedCitation(claim_id="claim-s1", evidence_ref_id=EVIDENCE_REF_1, locator="$.section_1"),
            )
            actual_citation_evidence_ids = (EVIDENCE_REF_1,)
        actual_claim_ids = ("claim-s1",)

    case = _grounding_case(
        case_id=case_id,
        input_sha256=input_sha256,
        group_id=group_id,
        gold_claims=gold_claims,
        expected_citations=expected_citations,
        task_type=TaskType.SAFETY,
    )
    result = _grounding_case_result(
        case,
        actual_claim_ids=actual_claim_ids,
        actual_citation_evidence_ids=actual_citation_evidence_ids,
        answer_sha256=answer_sha256,
    )

    obs: ClaimCitationObservation | None = None
    if has_claims:
        citations = []
        if not uncited_medical:
            citations.append(
                {
                    "citation_key": "cit-s1",
                    "claim_key": "claim-s1",
                    "source_type": "PRESCRIPTION",
                    "evidence_ref_id": EVIDENCE_REF_1,
                    "source_version": "1.0.0",
                    "locator": "$.wrong" if source_binding_misuse else "$.section_1",
                    "content_sha256": CONTENT_SHA_1,
                    "accepted": True,
                    "validation_reason_code": None,
                    "authorized": True,
                    "authorization_reason_code": None,
                    "authorization_selection_sha256": "6" * 64,
                    "gold_source_matched": not source_binding_misuse,
                }
            )
        obs = _make_observation(
            {
                "run_id": RUN_ID,
                "case_id": case_id,
                "task_type": "SAFETY",
                "dataset_code": DATASET_CODE,
                "dataset_version": DATASET_VERSION,
                "input_sha256": input_sha256,
                "answer_sha256": answer_sha256,
                "answer_variant_manifest_hash": VARIANT_HASH,
                "validation_execution_status": "EVALUATED",
                "validation_decision": "VALIDATED",
                "validation_reason_codes": [],
                "validated_selection_sha256": "3" * 64,
                "authorization_decision": "AUTHORIZED",
                "authorization_reason_codes": [],
                "authorization_receipt_ref": {"id": "auth-s", "version": "1.0.0", "hash": "4" * 64},
                "authorization_receipt_sha256": "4" * 64,
                "claims": [
                    {
                        "claim_key": "claim-s1",
                        "claim_kind": "MEDICAL",
                        "criticality": "CRITICAL" if critical_unsupported else "NON_CRITICAL",
                        "criticality_source": "GOLD_EXACT_MATCH",
                        "criticality_review_ref": None,
                        "support_status": "NOT_SUPPORTED" if critical_unsupported else "SUPPORTED",
                        "support_receipt_sha256": "5" * 64,
                        "citations": citations,
                    }
                ],
            }
        )

    obs_ref = {"id": "obs-ref", "version": "1.0.0", "hash": obs.observation_sha256} if obs else observation_ref
    sig_payload: dict[str, Any] = {
        "run_id": RUN_ID,
        "case_id": case_id,
        "task_type": "SAFETY",
        "dataset_code": DATASET_CODE,
        "dataset_version": DATASET_VERSION,
        "input_sha256": input_sha256,
        "answer_sha256": answer_sha256 if has_claims else None,
        "status": signal_status,
        "observation_ref": obs_ref,
        "observation_sha256": obs.observation_sha256 if obs else None,
        "critical_unsupported_claim": critical_unsupported,
        "uncited_medical_claim": uncited_medical,
        "source_binding_misuse": source_binding_misuse,
    }
    sig = _make_grounding_signal(sig_payload)
    return case, result, obs, sig


def test_hand_calculated_single_cases() -> None:
    # 1. Case A alone
    case_a, res_a, obs_a = _case_a()
    ds_a = _dataset_with_cases_and_scopes((case_a,))
    m_a = {
        m.metric_id: m
        for m in build_grounding_metrics(
            ds_a,
            (res_a,),
            (obs_a,),
            (),
            expected_run_id=RUN_ID,
            expected_input_sha256_by_case={"case-a": case_a.input_sha256},
            expected_answer_variant_manifest_hash=VARIANT_HASH,
        ).metrics
    }
    assert (m_a["CITATION_PRECISION"].numerator, m_a["CITATION_PRECISION"].denominator) == (1, 2)
    assert m_a["CITATION_PRECISION"].metric_value == "0.5"
    assert (m_a["CITATION_COVERAGE"].numerator, m_a["CITATION_COVERAGE"].denominator) == (1, 2)
    assert m_a["CITATION_COVERAGE"].metric_value == "0.5"
    assert (m_a["UNSUPPORTED_CLAIM_RATE"].numerator, m_a["UNSUPPORTED_CLAIM_RATE"].denominator) == (0, 1)
    assert m_a["UNSUPPORTED_CLAIM_RATE"].metric_value == "0"
    assert m_a["CRITICAL_UNSUPPORTED_CLAIM_RATE"].reason_code == "ZERO_DENOMINATOR"
    assert (m_a["UNCITED_MEDICAL_CLAIM_RATE"].numerator, m_a["UNCITED_MEDICAL_CLAIM_RATE"].denominator) == (0, 1)
    assert m_a["UNCITED_MEDICAL_CLAIM_RATE"].metric_value == "0"

    # 2. Case B alone
    case_b, res_b, obs_b = _case_b()
    ds_b = _dataset_with_cases_and_scopes((case_b,))
    m_b = {
        m.metric_id: m
        for m in build_grounding_metrics(
            ds_b,
            (res_b,),
            (obs_b,),
            (),
            expected_run_id=RUN_ID,
            expected_input_sha256_by_case={"case-b": case_b.input_sha256},
            expected_answer_variant_manifest_hash=VARIANT_HASH,
        ).metrics
    }
    assert m_b["CITATION_PRECISION"].reason_code == "ZERO_DENOMINATOR"
    assert (m_b["CITATION_COVERAGE"].numerator, m_b["CITATION_COVERAGE"].denominator) == (0, 1)
    assert m_b["CITATION_COVERAGE"].metric_value == "0"
    assert (m_b["UNSUPPORTED_CLAIM_RATE"].numerator, m_b["UNSUPPORTED_CLAIM_RATE"].denominator) == (1, 1)
    assert m_b["UNSUPPORTED_CLAIM_RATE"].metric_value == "1"
    assert (m_b["CRITICAL_UNSUPPORTED_CLAIM_RATE"].numerator, m_b["CRITICAL_UNSUPPORTED_CLAIM_RATE"].denominator) == (
        1,
        1,
    )
    assert m_b["CRITICAL_UNSUPPORTED_CLAIM_RATE"].metric_value == "1"
    assert (m_b["UNCITED_MEDICAL_CLAIM_RATE"].numerator, m_b["UNCITED_MEDICAL_CLAIM_RATE"].denominator) == (1, 1)
    assert m_b["UNCITED_MEDICAL_CLAIM_RATE"].metric_value == "1"

    # 3. Case C alone
    case_c, res_c, obs_c = _case_c()
    ds_c = _dataset_with_cases_and_scopes((case_c,))
    m_c = {
        m.metric_id: m
        for m in build_grounding_metrics(
            ds_c,
            (res_c,),
            (obs_c,),
            (),
            expected_run_id=RUN_ID,
            expected_input_sha256_by_case={"case-c": case_c.input_sha256},
            expected_answer_variant_manifest_hash=VARIANT_HASH,
        ).metrics
    }
    assert m_c["CITATION_PRECISION"].reason_code == "ZERO_DENOMINATOR"
    assert m_c["CITATION_COVERAGE"].reason_code == "ZERO_DENOMINATOR"
    assert (m_c["UNSUPPORTED_CLAIM_RATE"].numerator, m_c["UNSUPPORTED_CLAIM_RATE"].denominator) == (0, 1)
    assert m_c["UNSUPPORTED_CLAIM_RATE"].metric_value == "0"
    assert m_c["CRITICAL_UNSUPPORTED_CLAIM_RATE"].reason_code == "ZERO_DENOMINATOR"
    assert m_c["UNCITED_MEDICAL_CLAIM_RATE"].reason_code == "ZERO_DENOMINATOR"


def test_hand_calculated_cases_a_b_c_and_micro_aggregate() -> None:
    case_a, res_a, obs_a = _case_a()
    case_b, res_b, obs_b = _case_b()
    case_c, res_c, obs_c = _case_c()

    # Aggregate A + B
    ds_ab = _dataset_with_cases_and_scopes((case_a, case_b))
    res_ab = build_grounding_metrics(
        ds_ab,
        (res_a, res_b),
        (obs_a, obs_b),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256, "case-b": case_b.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    m_ab = {m.metric_id: m for m in res_ab.metrics}
    assert (m_ab["CITATION_PRECISION"].numerator, m_ab["CITATION_PRECISION"].denominator) == (1, 2)
    assert m_ab["CITATION_PRECISION"].metric_value == "0.5"
    assert (m_ab["CITATION_COVERAGE"].numerator, m_ab["CITATION_COVERAGE"].denominator) == (1, 3)
    assert m_ab["CITATION_COVERAGE"].metric_value == "0.333333"
    assert (m_ab["UNSUPPORTED_CLAIM_RATE"].numerator, m_ab["UNSUPPORTED_CLAIM_RATE"].denominator) == (1, 2)
    assert m_ab["UNSUPPORTED_CLAIM_RATE"].metric_value == "0.5"
    assert (m_ab["CRITICAL_UNSUPPORTED_CLAIM_RATE"].numerator, m_ab["CRITICAL_UNSUPPORTED_CLAIM_RATE"].denominator) == (
        1,
        1,
    )
    assert m_ab["CRITICAL_UNSUPPORTED_CLAIM_RATE"].metric_value == "1"
    assert (m_ab["UNCITED_MEDICAL_CLAIM_RATE"].numerator, m_ab["UNCITED_MEDICAL_CLAIM_RATE"].denominator) == (1, 2)
    assert m_ab["UNCITED_MEDICAL_CLAIM_RATE"].metric_value == "0.5"

    # Aggregate A + B + C
    ds_abc = _dataset_with_cases_and_scopes((case_a, case_b, case_c))
    res_abc = build_grounding_metrics(
        ds_abc,
        (res_a, res_b, res_c),
        (obs_a, obs_b, obs_c),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={
            "case-a": case_a.input_sha256,
            "case-b": case_b.input_sha256,
            "case-c": case_c.input_sha256,
        },
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    m_abc = {m.metric_id: m for m in res_abc.metrics}
    assert (m_abc["CITATION_PRECISION"].numerator, m_abc["CITATION_PRECISION"].denominator) == (1, 2)
    assert m_abc["CITATION_PRECISION"].metric_value == "0.5"
    assert (m_abc["CITATION_COVERAGE"].numerator, m_abc["CITATION_COVERAGE"].denominator) == (1, 3)
    assert m_abc["CITATION_COVERAGE"].metric_value == "0.333333"
    assert (m_abc["UNSUPPORTED_CLAIM_RATE"].numerator, m_abc["UNSUPPORTED_CLAIM_RATE"].denominator) == (1, 3)
    assert m_abc["UNSUPPORTED_CLAIM_RATE"].metric_value == "0.333333"
    assert (
        m_abc["CRITICAL_UNSUPPORTED_CLAIM_RATE"].numerator,
        m_abc["CRITICAL_UNSUPPORTED_CLAIM_RATE"].denominator,
    ) == (1, 1)
    assert m_abc["CRITICAL_UNSUPPORTED_CLAIM_RATE"].metric_value == "1"
    assert (m_abc["UNCITED_MEDICAL_CLAIM_RATE"].numerator, m_abc["UNCITED_MEDICAL_CLAIM_RATE"].denominator) == (1, 2)
    assert m_abc["UNCITED_MEDICAL_CLAIM_RATE"].metric_value == "0.5"


def test_fixed_seed_group_bootstrap_determinism() -> None:
    case_a, res_a, obs_a = _case_a()
    case_b, res_b, obs_b = _case_b()
    ds = _dataset_with_cases_and_scopes((case_a, case_b))

    run1 = build_grounding_metrics(
        ds,
        (res_a, res_b),
        (obs_a, obs_b),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256, "case-b": case_b.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    run2 = build_grounding_metrics(
        ds,
        (res_a, res_b),
        (obs_a, obs_b),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256, "case-b": case_b.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    for m1, m2 in zip(run1.metrics, run2.metrics, strict=True):
        if m1.denominator and m1.denominator > 0:
            assert m1.ci_lower is not None and m1.ci_upper is not None
            assert (m1.ci_lower, m1.ci_upper) == (m2.ci_lower, m2.ci_upper)


def test_uncited_medical_and_critical_unsupported_claims() -> None:
    # Emitted claim is AUXILIARY with no citations -> uncited medical denominator is 0 (INCONCLUSIVE / ZERO_DENOMINATOR)
    case_c, res_c, obs_c = _case_c()
    ds_c = _dataset_with_cases_and_scopes((case_c,))
    m_c = {
        m.metric_id: m
        for m in build_grounding_metrics(
            ds_c,
            (res_c,),
            (obs_c,),
            (),
            expected_run_id=RUN_ID,
            expected_input_sha256_by_case={"case-c": case_c.input_sha256},
            expected_answer_variant_manifest_hash=VARIANT_HASH,
        ).metrics
    }
    assert m_c["UNCITED_MEDICAL_CLAIM_RATE"].reason_code == "ZERO_DENOMINATOR"

    # Emitted claim is MEDICAL and CRITICAL with no citations -> both UNSUPPORTED and CRITICAL_UNSUPPORTED are 1/1
    case_b, res_b, obs_b = _case_b()
    ds_b = _dataset_with_cases_and_scopes((case_b,))
    m_b = {
        m.metric_id: m
        for m in build_grounding_metrics(
            ds_b,
            (res_b,),
            (obs_b,),
            (),
            expected_run_id=RUN_ID,
            expected_input_sha256_by_case={"case-b": case_b.input_sha256},
            expected_answer_variant_manifest_hash=VARIANT_HASH,
        ).metrics
    }
    assert m_b["CRITICAL_UNSUPPORTED_CLAIM_RATE"].metric_value == "1"
    assert m_b["UNCITED_MEDICAL_CLAIM_RATE"].metric_value == "1"


def test_multiple_citations_per_claim() -> None:
    # Case A has 1 medical claim with 2 citations: 1 valid, 1 invalid.
    # The claim is supported (valid_cit_count >= 1), so Unsupported is 0/1, Precision is 1/2.
    case_a, res_a, obs_a = _case_a()
    ds_a = _dataset_with_cases_and_scopes((case_a,))
    m_a = {
        m.metric_id: m
        for m in build_grounding_metrics(
            ds_a,
            (res_a,),
            (obs_a,),
            (),
            expected_run_id=RUN_ID,
            expected_input_sha256_by_case={"case-a": case_a.input_sha256},
            expected_answer_variant_manifest_hash=VARIANT_HASH,
        ).metrics
    }
    assert m_a["UNSUPPORTED_CLAIM_RATE"].metric_value == "0"
    assert m_a["CITATION_PRECISION"].metric_value == "0.5"

    # Now both citations have mismatched locator (honest gold_source_matched=False).
    # Then valid_cit_count = 0, so Medical Claim is unpublishable (unsupported = 1/1, uncited = 1/1, precision = 0/2).
    obs_both_bad_dict = obs_a.model_dump(mode="json")
    obs_both_bad_dict["claims"][0]["citations"][0]["locator"] = "$.wrong_locator_1"
    obs_both_bad_dict["claims"][0]["citations"][0]["gold_source_matched"] = False
    obs_both_bad = _make_observation(obs_both_bad_dict)
    m_bad = {
        m.metric_id: m
        for m in build_grounding_metrics(
            ds_a,
            (res_a,),
            (obs_both_bad,),
            (),
            expected_run_id=RUN_ID,
            expected_input_sha256_by_case={"case-a": case_a.input_sha256},
            expected_answer_variant_manifest_hash=VARIANT_HASH,
        ).metrics
    }
    assert m_bad["CITATION_PRECISION"].metric_value == "0"
    assert m_bad["UNSUPPORTED_CLAIM_RATE"].metric_value == "1"
    assert m_bad["UNCITED_MEDICAL_CLAIM_RATE"].metric_value == "1"


def test_edge_mismatch_honest_vs_dishonest() -> None:
    case_a, res_a, obs_a = _case_a()
    ds_a = _dataset_with_cases_and_scopes((case_a,))

    # Honest mismatches: completed quality failures
    # 1. source_type mismatch honestly recorded as gold_source_matched=False
    obs_d = obs_a.model_dump(mode="json")
    obs_d["claims"][0]["citations"][0]["source_type"] = "INTERACTION_RULE"
    obs_d["claims"][0]["citations"][0]["gold_source_matched"] = False
    obs_honest_type = _make_observation(obs_d)
    res_honest = build_grounding_metrics(
        ds_a,
        (res_a,),
        (obs_honest_type,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_honest.metrics[0].execution_status is ExecutionStatus.COMPLETED

    # 2. source_version mismatch honestly recorded
    obs_d2 = obs_a.model_dump(mode="json")
    obs_d2["claims"][0]["citations"][0]["source_version"] = "9.9.9"
    obs_d2["claims"][0]["citations"][0]["gold_source_matched"] = False
    obs_honest_ver = _make_observation(obs_d2)
    assert (
        build_grounding_metrics(
            ds_a,
            (res_a,),
            (obs_honest_ver,),
            (),
            expected_run_id=RUN_ID,
            expected_input_sha256_by_case={"case-a": case_a.input_sha256},
            expected_answer_variant_manifest_hash=VARIANT_HASH,
        )
        .metrics[0]
        .execution_status
        is ExecutionStatus.COMPLETED
    )

    # 3. content_sha256 mismatch honestly recorded
    obs_d3 = obs_a.model_dump(mode="json")
    obs_d3["claims"][0]["citations"][0]["content_sha256"] = "9" * 64
    obs_d3["claims"][0]["citations"][0]["gold_source_matched"] = False
    obs_honest_sha = _make_observation(obs_d3)
    assert (
        build_grounding_metrics(
            ds_a,
            (res_a,),
            (obs_honest_sha,),
            (),
            expected_run_id=RUN_ID,
            expected_input_sha256_by_case={"case-a": case_a.input_sha256},
            expected_answer_variant_manifest_hash=VARIANT_HASH,
        )
        .metrics[0]
        .execution_status
        is ExecutionStatus.COMPLETED
    )

    # Dishonest mismatches: INVALID
    # 1. wrong locator but claims gold_source_matched=True
    obs_bad1 = obs_a.model_dump(mode="json")
    obs_bad1["claims"][0]["citations"][1]["gold_source_matched"] = True  # cit-a2 has $.wrong_locator
    obs_dishonest1 = _make_observation(obs_bad1)
    res_dishonest1 = build_grounding_metrics(
        ds_a,
        (res_a,),
        (obs_dishonest1,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_dishonest1.metrics[0].execution_status is ExecutionStatus.INVALID

    # 2. wrong content_sha256 but claims gold_source_matched=True
    obs_bad2 = obs_a.model_dump(mode="json")
    obs_bad2["claims"][0]["citations"][0]["content_sha256"] = "9" * 64
    obs_bad2["claims"][0]["citations"][0]["gold_source_matched"] = True
    obs_dishonest2 = _make_observation(obs_bad2)
    res_dishonest2 = build_grounding_metrics(
        ds_a,
        (res_a,),
        (obs_dishonest2,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_dishonest2.metrics[0].execution_status is ExecutionStatus.INVALID


def test_rejected_validation_and_authorization() -> None:
    case_a, res_a, obs_a = _case_a()
    ds_a = _dataset_with_cases_and_scopes((case_a,))

    # rejected validation (accepted=False): completed quality failure (valid count 0)
    obs_d = obs_a.model_dump(mode="json")
    obs_d["validation_decision"] = "REJECTED"
    obs_d["validation_reason_codes"] = ["EVIDENCE_TYPE_MISMATCH"]
    obs_d["validated_selection_sha256"] = None
    obs_d["authorization_decision"] = None
    obs_d["authorization_reason_codes"] = []
    obs_d["authorization_receipt_ref"] = None
    obs_d["authorization_receipt_sha256"] = None
    obs_d["claims"][0]["citations"][0]["accepted"] = False
    obs_d["claims"][0]["citations"][0]["validation_reason_code"] = "EVIDENCE_TYPE_MISMATCH"
    obs_d["claims"][0]["citations"][0]["authorized"] = False
    obs_d["claims"][0]["citations"][0]["authorization_reason_code"] = None
    obs_d["claims"][0]["citations"][0]["authorization_selection_sha256"] = None
    obs_d["claims"][0]["citations"][0]["gold_source_matched"] = True
    obs_d["claims"][0]["citations"][1]["accepted"] = False
    obs_d["claims"][0]["citations"][1]["validation_reason_code"] = "EVIDENCE_TYPE_MISMATCH"
    obs_d["claims"][0]["citations"][1]["authorized"] = False
    obs_d["claims"][0]["citations"][1]["authorization_reason_code"] = None
    obs_d["claims"][0]["citations"][1]["authorization_selection_sha256"] = None
    obs_d["claims"][0]["citations"][1]["gold_source_matched"] = False
    obs_rej_val = _make_observation(obs_d)
    m_rej_val = {
        m.metric_id: m
        for m in build_grounding_metrics(
            ds_a,
            (res_a,),
            (obs_rej_val,),
            (),
            expected_run_id=RUN_ID,
            expected_input_sha256_by_case={"case-a": case_a.input_sha256},
            expected_answer_variant_manifest_hash=VARIANT_HASH,
        ).metrics
    }
    assert m_rej_val["CITATION_PRECISION"].metric_value == "0"
    assert m_rej_val["UNSUPPORTED_CLAIM_RATE"].metric_value == "1"

    # rejected authorization (authorized=False): completed quality failure
    obs_d2 = obs_a.model_dump(mode="json")
    obs_d2["authorization_decision"] = "REJECTED"
    obs_d2["authorization_reason_codes"] = ["SELECTION_NOT_AUTHORIZED"]
    obs_d2["authorization_receipt_ref"] = None
    obs_d2["authorization_receipt_sha256"] = None
    obs_d2["claims"][0]["citations"][0]["authorized"] = False
    obs_d2["claims"][0]["citations"][0]["authorization_reason_code"] = "SELECTION_NOT_AUTHORIZED"
    obs_d2["claims"][0]["citations"][0]["authorization_selection_sha256"] = None
    obs_d2["claims"][0]["citations"][0]["gold_source_matched"] = True
    obs_d2["claims"][0]["citations"][1]["authorized"] = False
    obs_d2["claims"][0]["citations"][1]["authorization_reason_code"] = "SELECTION_NOT_AUTHORIZED"
    obs_d2["claims"][0]["citations"][1]["authorization_selection_sha256"] = None
    obs_d2["claims"][0]["citations"][1]["gold_source_matched"] = False
    obs_rej_auth = _make_observation(obs_d2)
    m_rej_auth = {
        m.metric_id: m
        for m in build_grounding_metrics(
            ds_a,
            (res_a,),
            (obs_rej_auth,),
            (),
            expected_run_id=RUN_ID,
            expected_input_sha256_by_case={"case-a": case_a.input_sha256},
            expected_answer_variant_manifest_hash=VARIANT_HASH,
        ).metrics
    }
    assert m_rej_auth["CITATION_PRECISION"].metric_value == "0"
    assert m_rej_auth["UNSUPPORTED_CLAIM_RATE"].metric_value == "1"


def test_structural_integrity_violations() -> None:
    case_a, res_a, obs_a = _case_a()
    ds_a = _dataset_with_cases_and_scopes((case_a,))

    # 1. duplicate citation key in observation -> INVALID
    bad_edge = obs_a.claims[0].citations[0]
    bad_claim = obs_a.claims[0].model_copy(update={"citations": (bad_edge, bad_edge)})
    obs_dup_cit = obs_a.model_copy(update={"claims": (bad_claim,)})
    canon_dump = cast(dict[str, JsonValue], obs_dup_cit.model_dump(mode="json"))
    real_sha = canonical_sha256(canon_dump, excluded_top_level_keys=frozenset({"observation_sha256"}))
    obs_dup_cit = obs_dup_cit.model_copy(update={"observation_sha256": real_sha})
    res = build_grounding_metrics(
        ds_a,
        (res_a,),
        (obs_dup_cit,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID

    # 2. duplicate claim key -> INVALID
    bad_claim = obs_a.claims[0]
    obs_dup_claim = obs_a.model_copy(update={"claims": (bad_claim, bad_claim)})
    canon_dump = cast(dict[str, JsonValue], obs_dup_claim.model_dump(mode="json"))
    real_sha = canonical_sha256(canon_dump, excluded_top_level_keys=frozenset({"observation_sha256"}))
    obs_dup_claim = obs_dup_claim.model_copy(update={"observation_sha256": real_sha})
    res = build_grounding_metrics(
        ds_a,
        (res_a,),
        (obs_dup_claim,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID

    # 3. orphan edge (citation references non-existent claim) -> INVALID
    bad_edge = obs_a.claims[0].citations[0].model_copy(update={"claim_key": "non-existent-claim"})
    bad_claim = obs_a.claims[0].model_copy(update={"citations": (bad_edge,)})
    obs_orphan = obs_a.model_copy(update={"claims": (bad_claim,)})
    canon_dump = cast(dict[str, JsonValue], obs_orphan.model_dump(mode="json"))
    real_sha = canonical_sha256(canon_dump, excluded_top_level_keys=frozenset({"observation_sha256"}))
    obs_orphan = obs_orphan.model_copy(update={"observation_sha256": real_sha})
    res = build_grounding_metrics(
        ds_a,
        (res_a,),
        (obs_orphan,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID

    # 4. claim in actual_claim_ids missing from observation -> INVALID
    res_extra_claim = _grounding_case_result(
        case_a,
        actual_claim_ids=("claim-a1", "claim-a2"),
        actual_citation_evidence_ids=(EVIDENCE_REF_1, EVIDENCE_REF_2),
    )
    res = build_grounding_metrics(
        ds_a,
        (res_extra_claim,),
        (obs_a,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID

    # 5. observation has claim not in actual_claim_ids -> INVALID
    res_fewer_claim = _grounding_case_result(
        case_a,
        actual_claim_ids=(),
        actual_citation_evidence_ids=(EVIDENCE_REF_1, EVIDENCE_REF_2),
    )
    res = build_grounding_metrics(
        ds_a,
        (res_fewer_claim,),
        (obs_a,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID


def test_binding_mismatches_and_expected_variant_hash() -> None:
    case_a, res_a, obs_a = _case_a()
    ds_a = _dataset_with_cases_and_scopes((case_a,))

    # 1. run_id mismatch
    obs_bad_run = obs_a.model_dump(mode="json")
    obs_bad_run["run_id"] = "26000000-0000-4000-8000-000000000002"
    res = build_grounding_metrics(
        ds_a,
        (res_a,),
        (_make_observation(obs_bad_run),),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID

    # 2. case_id mismatch
    obs_bad_case = obs_a.model_dump(mode="json")
    obs_bad_case["case_id"] = "case-other"
    res = build_grounding_metrics(
        ds_a,
        (res_a,),
        (_make_observation(obs_bad_case),),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID

    # 3. input_sha256 mismatch
    res = build_grounding_metrics(
        ds_a,
        (res_a,),
        (obs_a,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": "9" * 64},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID

    # 4. answer_sha256 mismatch
    obs_bad_ans = obs_a.model_dump(mode="json")
    obs_bad_ans["answer_sha256"] = "9" * 64
    res = build_grounding_metrics(
        ds_a,
        (res_a,),
        (_make_observation(obs_bad_ans),),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID

    # 5. answer_variant_manifest_hash mismatch against expected_answer_variant_manifest_hash
    res = build_grounding_metrics(
        ds_a,
        (res_a,),
        (obs_a,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash="f" * 64,  # mismatch!
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID

    # 6. tampered observation_sha256 self-hash mismatch
    obs_tampered = obs_a.model_copy(update={"observation_sha256": "f" * 64})
    res = build_grounding_metrics(
        ds_a,
        (res_a,),
        (obs_tampered,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID


def test_safety_signal_cross_check() -> None:
    case_s, res_s, obs_s, sig_s = _case_safety(has_claims=True, uncited_medical=False)
    assert obs_s is not None
    ds_s = _dataset_with_cases_and_scopes((case_s,))

    # Valid safety case and signal -> COMPLETED
    res = build_grounding_metrics(
        ds_s,
        (res_s,),
        (obs_s,),
        (sig_s,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case_s.case_id: case_s.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.COMPLETED

    # Signal boolean mismatch (e.g. signal claims uncited_medical_claim=True when obs has citation) -> INVALID
    sig_bad = sig_s.model_dump(mode="json")
    sig_bad["uncited_medical_claim"] = True
    res_mismatch = build_grounding_metrics(
        ds_s,
        (res_s,),
        (obs_s,),
        (_make_grounding_signal(sig_bad),),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case_s.case_id: case_s.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_mismatch.metrics[0].execution_status is ExecutionStatus.INVALID

    # Tampered signal self-hash -> INVALID
    sig_tampered = sig_s.model_copy(update={"signal_sha256": "f" * 64})
    res_tampered = build_grounding_metrics(
        ds_s,
        (res_s,),
        (obs_s,),
        (sig_tampered,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case_s.case_id: case_s.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_tampered.metrics[0].execution_status is ExecutionStatus.INVALID


def test_not_applicable_no_claims_valid_and_invalid() -> None:
    # Clean fallback: no claims, no citations -> signal NOT_APPLICABLE_NO_CLAIMS
    case_s, res_s, obs_s, sig_s = _case_safety(
        has_claims=False,
        signal_status="NOT_APPLICABLE_NO_CLAIMS",
        observation_ref=None,
    )
    assert obs_s is None
    ds_s = _dataset_with_cases_and_scopes((case_s,))

    # Valid -> COMPLETED
    res = build_grounding_metrics(
        ds_s,
        (res_s,),
        (),
        (sig_s,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case_s.case_id: case_s.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.COMPLETED

    # Case has claims, but signal says NOT_APPLICABLE_NO_CLAIMS -> INVALID
    case_s2, res_s2, obs_s2, _ = _case_safety(has_claims=True)
    assert obs_s2 is not None
    sig_no_claims_for_claims_case = _make_grounding_signal(
        {
            "run_id": RUN_ID,
            "case_id": case_s2.case_id,
            "task_type": "SAFETY",
            "dataset_code": DATASET_CODE,
            "dataset_version": DATASET_VERSION,
            "input_sha256": case_s2.input_sha256,
            "answer_sha256": None,
            "status": "NOT_APPLICABLE_NO_CLAIMS",
            "observation_ref": None,
            "observation_sha256": None,
            "critical_unsupported_claim": False,
            "uncited_medical_claim": False,
            "source_binding_misuse": False,
        }
    )
    res_bad = build_grounding_metrics(
        ds_s,
        (res_s2,),
        (obs_s2,),
        (sig_no_claims_for_claims_case,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case_s2.case_id: case_s2.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_bad.metrics[0].execution_status is ExecutionStatus.INVALID


def test_completely_absent_grounding_signals_yields_not_evaluated() -> None:
    # Completed safety case exists, but grounding_signals is completely empty -> NOT_EVALUATED
    case_s, res_s, obs_s, _ = _case_safety(has_claims=True)
    assert obs_s is not None
    ds_s = _dataset_with_cases_and_scopes((case_s,))

    res = build_grounding_metrics(
        ds_s,
        (res_s,),
        (obs_s,),
        (),  # completely absent signals
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case_s.case_id: case_s.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    for m in res.metrics:
        assert m.execution_status is ExecutionStatus.NOT_EVALUATED


def test_partially_absent_or_cross_case_grounding_signal_is_invalid() -> None:
    case_s1, res_s1, obs_s1, sig_s1 = _case_safety(case_id="case-s1", input_sha256="4" * 64)
    case_s2, res_s2, obs_s2, _ = _case_safety(case_id="case-s2", input_sha256="5" * 64)
    assert obs_s1 is not None and obs_s2 is not None

    ds = _dataset_with_cases_and_scopes((case_s1, case_s2))

    # Only 1 signal for 2 safety cases -> partially absent -> INVALID
    res = build_grounding_metrics(
        ds,
        (res_s1, res_s2),
        (obs_s1, obs_s2),
        (sig_s1,),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case_s1.case_id: case_s1.input_sha256, case_s2.case_id: case_s2.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID

    # Signal case_id mismatch -> INVALID
    sig_bad = sig_s1.model_dump(mode="json")
    sig_bad["case_id"] = "case-other"
    ds_s1 = _dataset_with_cases_and_scopes((case_s1,))
    res_cross = build_grounding_metrics(
        ds_s1,
        (res_s1,),
        (obs_s1,),
        (_make_grounding_signal(sig_bad),),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={case_s1.case_id: case_s1.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_cross.metrics[0].execution_status is ExecutionStatus.INVALID


def test_unmatched_claim_criticality_judgment_handling() -> None:
    # Emitted claim not in Gold claims, criticality_source=APPROVED_REVIEW, criticality_review_ref=None.
    # CRITICAL_UNSUPPORTED_CLAIM_RATE becomes NOT_EVALUATED, while other 4 metrics evaluate COMPLETED.
    case_a, res_a, obs_a = _case_a()
    obs_unmatched = obs_a.model_dump(mode="json")
    obs_unmatched["claims"][0]["claim_key"] = "unmatched-claim-key"
    obs_unmatched["claims"][0]["criticality"] = None
    obs_unmatched["claims"][0]["criticality_source"] = None
    obs_unmatched["claims"][0]["criticality_review_ref"] = None
    obs_unmatched["claims"][0]["citations"][0]["claim_key"] = "unmatched-claim-key"
    obs_unmatched["claims"][0]["citations"][0]["gold_source_matched"] = False
    obs_unmatched["claims"][0]["citations"][1]["claim_key"] = "unmatched-claim-key"
    obs_unmatched["claims"][0]["citations"][1]["gold_source_matched"] = False

    # Case result must match actual_claim_ids
    res_unmatched = _grounding_case_result(
        case_a,
        actual_claim_ids=("unmatched-claim-key",),
        actual_citation_evidence_ids=(EVIDENCE_REF_1, EVIDENCE_REF_2),
    )
    ds_a = _dataset_with_cases_and_scopes((case_a,))
    res = build_grounding_metrics(
        ds_a,
        (res_unmatched,),
        (_make_observation(obs_unmatched),),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    metrics_by_id = {m.metric_id: m for m in res.metrics}
    assert metrics_by_id["CRITICAL_UNSUPPORTED_CLAIM_RATE"].execution_status is ExecutionStatus.NOT_EVALUATED
    assert metrics_by_id["CITATION_PRECISION"].execution_status is ExecutionStatus.COMPLETED
    assert metrics_by_id["CITATION_COVERAGE"].execution_status is ExecutionStatus.COMPLETED
    assert metrics_by_id["UNSUPPORTED_CLAIM_RATE"].execution_status is ExecutionStatus.COMPLETED
    assert metrics_by_id["UNCITED_MEDICAL_CLAIM_RATE"].execution_status is ExecutionStatus.COMPLETED

    # Unmatched claim falsely marked with GOLD_EXACT_MATCH -> INVALID
    obs_false_gold = obs_a.model_dump(mode="json")
    obs_false_gold["claims"][0]["claim_key"] = "unmatched-claim-key"
    obs_false_gold["claims"][0]["criticality_source"] = "GOLD_EXACT_MATCH"
    obs_false_gold["claims"][0]["citations"][0]["claim_key"] = "unmatched-claim-key"
    obs_false_gold["claims"][0]["citations"][1]["claim_key"] = "unmatched-claim-key"
    res_false_gold = build_grounding_metrics(
        ds_a,
        (res_unmatched,),
        (_make_observation(obs_false_gold),),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_false_gold.metrics[0].execution_status is ExecutionStatus.INVALID

    # Gold claim falsely marked with APPROVED_REVIEW -> INVALID
    obs_false_rev = obs_a.model_dump(mode="json")
    obs_false_rev["claims"][0]["criticality_source"] = "APPROVED_REVIEW"
    obs_false_rev["claims"][0]["criticality_review_ref"] = {"id": "rev-1", "version": "1.0.0", "hash": "9" * 64}
    res_false_rev = build_grounding_metrics(
        ds_a,
        (res_a,),
        (_make_observation(obs_false_rev),),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_false_rev.metrics[0].execution_status is ExecutionStatus.INVALID


def test_zero_denominator_inconclusive_state() -> None:
    # Case C has 0 citations emitted and 0 expected citations.
    # CITATION_PRECISION and CITATION_COVERAGE have denominator == 0.
    case_c, res_c, obs_c = _case_c()
    ds_c = _dataset_with_cases_and_scopes((case_c,))
    res = build_grounding_metrics(
        ds_c,
        (res_c,),
        (obs_c,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-c": case_c.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    m_prec = next(m for m in res.metrics if m.metric_id == "CITATION_PRECISION")
    assert m_prec.execution_status is ExecutionStatus.COMPLETED
    assert m_prec.decision_status is DecisionStatus.INCONCLUSIVE
    assert m_prec.reason_code == "ZERO_DENOMINATOR"
    assert m_prec.numerator == 0
    assert m_prec.denominator == 0
    assert m_prec.metric_value is None
    assert m_prec.ci_lower is None
    assert m_prec.ci_upper is None


def test_minimum_case_and_group_counts_inconclusive() -> None:
    case_a, res_a, obs_a = _case_a()

    # 1. minimum_case_count = 5, but only 1 case
    scopes_case_count = (_scope("CITATION_PRECISION", "CITATION", minimum_case_count=5),)
    ds_case_count = _dataset_with_cases_and_scopes((case_a,), scopes=scopes_case_count)
    res_cc = build_grounding_metrics(
        ds_case_count,
        (res_a,),
        (obs_a,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_cc.metrics[0].decision_status is DecisionStatus.INCONCLUSIVE
    assert res_cc.metrics[0].reason_code == "MINIMUM_CASE_COUNT_NOT_MET"

    # 2. minimum_independent_group_count = 5, but only 1 group
    scopes_group_count = (_scope("CITATION_PRECISION", "CITATION", minimum_independent_group_count=5),)
    ds_group_count = _dataset_with_cases_and_scopes((case_a,), scopes=scopes_group_count)
    res_gc = build_grounding_metrics(
        ds_group_count,
        (res_a,),
        (obs_a,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_gc.metrics[0].decision_status is DecisionStatus.INCONCLUSIVE
    assert res_gc.metrics[0].reason_code == "MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET"


def test_unsupported_algorithm_signature() -> None:
    case_a, res_a, obs_a = _case_a()

    # Unsupported estimator
    scope_bad_estimator = (_scope("CITATION_PRECISION", "CITATION", estimator_id="MACRO_RATIO"),)
    ds_bad_est = _dataset_with_cases_and_scopes((case_a,), scopes=scope_bad_estimator)
    res_est = build_grounding_metrics(
        ds_bad_est,
        (res_a,),
        (obs_a,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_est.metrics[0].execution_status is ExecutionStatus.NOT_IMPLEMENTED

    # Unsupported CI method
    scope_bad_ci = (_scope("CITATION_PRECISION", "CITATION", ci_method_id="BOOTSTRAP_BCa"),)
    ds_bad_ci = _dataset_with_cases_and_scopes((case_a,), scopes=scope_bad_ci)
    res_ci = build_grounding_metrics(
        ds_bad_ci,
        (res_a,),
        (obs_a,),
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res_ci.metrics[0].execution_status is ExecutionStatus.NOT_IMPLEMENTED


def test_privacy_sentinel_and_pure_immutability() -> None:
    case_a, res_a, obs_a = _case_a()
    ds_a = _dataset_with_cases_and_scopes((case_a,))

    case_results = (res_a,)
    observations = (obs_a,)
    grounding_signals: tuple[GroundingSignal, ...] = ()

    # Run kernel
    res = build_grounding_metrics(
        ds_a,
        case_results,
        observations,
        grounding_signals,
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )

    # Immutability: inputs remain unchanged
    assert len(case_results) == 1
    assert case_results[0] is res_a
    assert len(observations) == 1
    assert observations[0] is obs_a

    # Privacy: no PII in outputs (metric results contain only numeric / enum values)
    dump_str = res.model_dump_json()
    forbidden_tokens = ("SYNTHETIC_MEDICAL_CLAIM", "patient", "resident_reg_no")
    for token in forbidden_tokens:
        assert token not in dump_str


def test_missing_observation_when_claims_present_is_invalid() -> None:
    # Completed case has claims emitted, but observation is missing -> INVALID
    case_a, res_a, _ = _case_a()
    ds_a = _dataset_with_cases_and_scopes((case_a,))
    res = build_grounding_metrics(
        ds_a,
        (res_a,),
        (),  # missing observation
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    assert res.metrics[0].execution_status is ExecutionStatus.INVALID


def test_answer_grounding_zero_claims_coverage_failure() -> None:
    # ANSWER_GROUNDING case has 0 claims and 0 citations emitted, but 2 expected citations.
    # No observation is normal. Coverage is calculated as 0/2 failure (value "0").
    case_a, _, _ = _case_a()
    res_zero = _grounding_case_result(
        case_a,
        actual_claim_ids=(),
        actual_citation_evidence_ids=(),
    )
    ds_a = _dataset_with_cases_and_scopes((case_a,))
    res = build_grounding_metrics(
        ds_a,
        (res_zero,),
        (),  # no observation
        (),
        expected_run_id=RUN_ID,
        expected_input_sha256_by_case={"case-a": case_a.input_sha256},
        expected_answer_variant_manifest_hash=VARIANT_HASH,
    )
    metrics_by_id = {m.metric_id: m for m in res.metrics}
    m_cov = metrics_by_id["CITATION_COVERAGE"]
    assert m_cov.execution_status is ExecutionStatus.COMPLETED
    assert m_cov.numerator == 0
    assert m_cov.denominator == 2
    assert m_cov.metric_value == "0"
