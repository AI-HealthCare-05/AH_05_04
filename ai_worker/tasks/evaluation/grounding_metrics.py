from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, TypedDict, cast

from pydantic import BaseModel

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.loaders import EvaluationCaseContract, ValidatedDataset
from ai_worker.tasks.evaluation.metric_support import (
    RatioContribution,
    canonical_ratio,
    percentile_cluster_bootstrap_ratio_ci,
)
from ai_worker.tasks.evaluation.schemas.artifacts import CaseResult, MetricResult, MetricResults
from ai_worker.tasks.evaluation.schemas.authoring import EvidenceMappingEntry
from ai_worker.tasks.evaluation.schemas.common import DecisionStatus, ExecutionStatus, Partition, TaskType
from ai_worker.tasks.evaluation.schemas.grounding_v1 import (
    CitationEdgeObservation,
    ClaimCitationObservation,
    ClaimCriticality,
    ClaimKind,
    ClaimObservation,
    ClaimSupportStatus,
    CriticalitySource,
    GroundingSignal,
    GroundingSignalStatus,
)
from ai_worker.tasks.evaluation.schemas.policy import ComparisonScope

_METRIC_UNITS = {
    "CITATION_PRECISION": "CITATION",
    "CITATION_COVERAGE": "EXPECTED_CITATION",
    "UNSUPPORTED_CLAIM_RATE": "CLAIM",
    "CRITICAL_UNSUPPORTED_CLAIM_RATE": "CRITICAL_CLAIM",
    "UNCITED_MEDICAL_CLAIM_RATE": "MEDICAL_CLAIM",
}
_GROUNDING_TASK_TYPES = frozenset({TaskType.ANSWER_GROUNDING, TaskType.SAFETY, TaskType.END_TO_END_RAG})
_SAFETY_TASK_TYPES = frozenset({TaskType.SAFETY, TaskType.END_TO_END_RAG})
_INPUT_STATUS_PRIORITY = {
    ExecutionStatus.INVALID: 0,
    ExecutionStatus.ERROR: 1,
    ExecutionStatus.NOT_IMPLEMENTED: 2,
    ExecutionStatus.NOT_EVALUATED: 3,
}


class MetricScopeFields(TypedDict):
    metric_id: str
    metric_version: str
    partition: Partition
    slice_id: str
    required: bool
    unit_of_analysis: str
    estimator_id: str
    estimator_version: str
    independence_unit: str | None
    cluster_dimension: str | None
    ci_method_id: str
    ci_method_version: str
    ci_level: str | None
    ci_sidedness: str | None
    threshold: str


def _scope_fields(scope: ComparisonScope) -> MetricScopeFields:
    parameters = dict(scope.ci_parameters)
    ci_level = parameters.get("level")
    ci_sidedness = parameters.get("sidedness")
    return {
        "metric_id": scope.metric_id,
        "metric_version": scope.metric_version,
        "partition": scope.partition,
        "slice_id": scope.slice_id,
        "required": scope.required,
        "unit_of_analysis": scope.unit_of_analysis,
        "estimator_id": scope.estimator_id,
        "estimator_version": scope.estimator_version,
        "independence_unit": scope.independence_unit,
        "cluster_dimension": None if scope.cluster_dimension is None else scope.cluster_dimension.value,
        "ci_method_id": scope.ci_method_id,
        "ci_method_version": scope.ci_method_version,
        "ci_level": ci_level if isinstance(ci_level, str) else None,
        "ci_sidedness": ci_sidedness if isinstance(ci_sidedness, str) else None,
        "threshold": scope.threshold,
    }


def _incomplete_metric(scope: ComparisonScope, status: ExecutionStatus) -> MetricResult:
    return MetricResult(
        **_scope_fields(scope),
        execution_status=status,
        decision_status=None,
        sample_case_count=None,
        sample_independent_group_count=None,
        numerator=None,
        denominator=None,
        metric_value=None,
        ci_lower=None,
        ci_upper=None,
        reason_code=None,
    )


def _matches_scope(case: EvaluationCaseContract, scope: ComparisonScope) -> bool:
    return case.partition is scope.partition and (scope.slice_id == "ALL" or scope.slice_id in case.slice_ids)


def _algorithm_signature_supported(scope: ComparisonScope) -> bool:
    parameters = dict(scope.ci_parameters)
    iterations = parameters.get("iterations")
    return (
        scope.metric_id in _METRIC_UNITS
        and scope.metric_version == "1.0.0"
        and scope.partition is Partition.DEV
        and scope.unit_of_analysis == _METRIC_UNITS[scope.metric_id]
        and not scope.required
        and scope.estimator_id == "MICRO_RATIO"
        and scope.estimator_version == "1.0.0"
        and scope.independence_unit is not None
        and scope.cluster_dimension is not None
        and scope.ci_method_id == "PERCENTILE_CLUSTER_BOOTSTRAP"
        and scope.ci_method_version == "1.0.0"
        and set(parameters) == {"iterations", "level", "sidedness"}
        and type(iterations) is int
        and iterations > 0
        and parameters.get("level") == "0.95"
        and parameters.get("sidedness") == "TWO_SIDED"
        and type(scope.seed) is int
        and scope.decision_basis == "DIAGNOSTIC_ONLY"
        and scope.threshold == "0"
    )


def _verify_self_hash(model: BaseModel, hash_field: str) -> bool:
    canonical_payload = cast(dict[str, JsonValue], model.model_dump(mode="json"))
    expected = canonical_sha256(canonical_payload, excluded_top_level_keys=frozenset({hash_field}))
    return bool(canonical_payload[hash_field] == expected)


def _is_edge_exact_match(
    edge: CitationEdgeObservation,
    expected_citations_tuples: set[tuple[str, str, str]],
    evidence_index: Mapping[str, EvidenceMappingEntry],
) -> bool:
    if (edge.claim_key, edge.evidence_ref_id, edge.locator) not in expected_citations_tuples:
        return False
    entry = evidence_index.get(edge.evidence_ref_id)
    if entry is None:
        return False
    if entry.evidence_type.value != edge.source_type.value:
        return False
    if entry.source_version != edge.source_version:
        return False
    if entry.locator != edge.locator:
        return False
    if entry.content_sha256 != edge.content_sha256:
        return False
    return True


def _is_claim_publishable(claim: ClaimObservation, valid_citation_count: int) -> bool:
    if claim.claim_kind is ClaimKind.MEDICAL:
        return claim.support_status is ClaimSupportStatus.SUPPORTED and valid_citation_count > 0
    if claim.claim_kind is ClaimKind.AUXILIARY:
        return claim.support_status in (ClaimSupportStatus.SUPPORTED, ClaimSupportStatus.PARTIALLY_SUPPORTED)
    if claim.claim_kind is ClaimKind.SAFETY_FALLBACK:
        return claim.support_status is ClaimSupportStatus.SUPPORTED
    return False


def _scope_execution_status(
    cases: tuple[EvaluationCaseContract, ...],
    results_by_case: Mapping[str, CaseResult],
) -> ExecutionStatus | None:
    incomplete_statuses: set[ExecutionStatus] = set()
    for case in cases:
        status = results_by_case[case.case_id].execution_status
        if not isinstance(status, ExecutionStatus):
            return ExecutionStatus.INVALID
        if status is not ExecutionStatus.COMPLETED:
            incomplete_statuses.add(status)
    if incomplete_statuses:
        return min(incomplete_statuses, key=_INPUT_STATUS_PRIORITY.__getitem__)
    return None


class _CaseMetricContributions:
    __slots__ = (
        "case_id",
        "group_id",
        "precision",
        "coverage",
        "unsupported",
        "critical_unsupported",
        "uncited_medical",
        "critical_not_evaluable",
        "safety_not_evaluable",
    )

    def __init__(
        self,
        case_id: str,
        group_id: str,
        precision: RatioContribution,
        coverage: RatioContribution,
        unsupported: RatioContribution,
        critical_unsupported: RatioContribution,
        uncited_medical: RatioContribution,
        critical_not_evaluable: bool = False,
        safety_not_evaluable: bool = False,
    ) -> None:
        self.case_id = case_id
        self.group_id = group_id
        self.precision = precision
        self.coverage = coverage
        self.unsupported = unsupported
        self.critical_unsupported = critical_unsupported
        self.uncited_medical = uncited_medical
        self.critical_not_evaluable = critical_not_evaluable
        self.safety_not_evaluable = safety_not_evaluable


def _build_completed_metric(
    scope: ComparisonScope,
    cases: tuple[EvaluationCaseContract, ...],
    case_contributions: list[_CaseMetricContributions],
) -> MetricResult:
    getter_map = {
        "CITATION_PRECISION": lambda c: c.precision,
        "CITATION_COVERAGE": lambda c: c.coverage,
        "UNSUPPORTED_CLAIM_RATE": lambda c: c.unsupported,
        "CRITICAL_UNSUPPORTED_CLAIM_RATE": lambda c: c.critical_unsupported,
        "UNCITED_MEDICAL_CLAIM_RATE": lambda c: c.uncited_medical,
    }
    getter = getter_map[scope.metric_id]

    grouped: defaultdict[str, list[RatioContribution]] = defaultdict(list)
    contributions: list[RatioContribution] = []
    for item in case_contributions:
        contrib = getter(item)
        contributions.append(contrib)
        grouped[item.group_id].append(contrib)

    numerator = sum(c.numerator for c in contributions)
    denominator = sum(c.denominator for c in contributions)

    # Sampling frame: leakage groups with positive denominator for this metric
    active_grouped = {gid: vals for gid, vals in grouped.items() if sum(c.denominator for c in vals) > 0}
    group_count = len(active_grouped)

    reason_code: str | None = None
    if denominator == 0:
        reason_code = "ZERO_DENOMINATOR"
    elif len(cases) < scope.minimum_case_count:
        reason_code = "MINIMUM_CASE_COUNT_NOT_MET"
    elif scope.minimum_independent_group_count is not None and group_count < scope.minimum_independent_group_count:
        reason_code = "MINIMUM_INDEPENDENT_GROUP_COUNT_NOT_MET"

    ci_lower: str | None = None
    ci_upper: str | None = None
    metric_value: str | None = None
    if denominator > 0:
        parameters = dict(scope.ci_parameters)
        metric_value = canonical_ratio(numerator, denominator)
        ci_lower, ci_upper = percentile_cluster_bootstrap_ratio_ci(
            {group_id: tuple(values) for group_id, values in active_grouped.items()},
            seed=cast(int, scope.seed),
            iterations=cast(int, parameters["iterations"]),
            level=Decimal(cast(str, parameters["level"])),
        )

    return MetricResult(
        **_scope_fields(scope),
        execution_status=ExecutionStatus.COMPLETED,
        decision_status=DecisionStatus.INCONCLUSIVE if reason_code else DecisionStatus.NOT_APPLICABLE,
        sample_case_count=len(cases),
        sample_independent_group_count=group_count,
        numerator=numerator,
        denominator=denominator,
        metric_value=metric_value,
        ci_lower=ci_lower,
        ci_upper=ci_upper,
        reason_code=reason_code,
    )


def build_grounding_metrics(  # noqa: C901
    dataset: ValidatedDataset,
    case_results: tuple[CaseResult, ...],
    observations: tuple[ClaimCitationObservation, ...],
    grounding_signals: tuple[GroundingSignal, ...],
    *,
    expected_run_id: str,
    expected_input_sha256_by_case: Mapping[str, str],
    expected_answer_variant_manifest_hash: str,
) -> MetricResults:
    """Build approved Grounding and Citation metrics with pure deterministic verification."""
    target_cases = tuple(
        case for case in dataset.cases if case.task_type in _GROUNDING_TASK_TYPES and case.partition is Partition.DEV
    )
    cases_by_id = {case.case_id: case for case in target_cases}

    # Step 1: Input cardinality and Run identity check
    run_integrity_status: ExecutionStatus | None = None
    if set(expected_input_sha256_by_case) != set(cases_by_id):
        run_integrity_status = ExecutionStatus.INVALID
    result_ids = [result.case_id for result in case_results]
    if len(result_ids) != len(set(result_ids)) or set(result_ids) != set(cases_by_id):
        run_integrity_status = ExecutionStatus.INVALID
    if any(result.run_id != expected_run_id for result in case_results):
        run_integrity_status = ExecutionStatus.INVALID
    for result in case_results:
        case = cases_by_id.get(result.case_id)
        if case is None:
            run_integrity_status = ExecutionStatus.INVALID
            break
        if (
            result.task_type is not case.task_type
            or result.dataset_code != case.dataset_code
            or result.dataset_version != case.dataset_version
            or result.partition is not case.partition
            or result.input_sha256 != expected_input_sha256_by_case.get(result.case_id)
        ):
            run_integrity_status = ExecutionStatus.INVALID
            break

    results_by_case = {result.case_id: result for result in case_results}

    # Step 2: Index and validate observations
    observations_by_case: dict[str, ClaimCitationObservation] = {}
    obs_integrity_invalid = False
    for obs in observations:
        if obs.case_id in observations_by_case or obs.case_id not in cases_by_id:
            obs_integrity_invalid = True
            break
        observations_by_case[obs.case_id] = obs
        if not _verify_self_hash(obs, "observation_sha256"):
            obs_integrity_invalid = True
            break
        obs_res = results_by_case.get(obs.case_id)
        case_item = cases_by_id.get(obs.case_id)
        if obs_res is None or case_item is None:
            obs_integrity_invalid = True
            break
        if (
            obs.run_id != expected_run_id
            or obs.task_type is not case_item.task_type
            or obs.dataset_code != case_item.dataset_code
            or obs.dataset_version != case_item.dataset_version
            or obs.input_sha256 != expected_input_sha256_by_case.get(obs.case_id)
            or obs.answer_sha256 != obs_res.answer_sha256
            or obs.answer_variant_manifest_hash != expected_answer_variant_manifest_hash
        ):
            obs_integrity_invalid = True
            break
        # Claim keys uniqueness and exact-match with actual_claim_ids
        actual_claims = tuple(obs_res.actual_claim_ids or ())
        obs_claim_keys = tuple(claim_item.claim_key for claim_item in obs.claims)
        if len(obs_claim_keys) != len(set(obs_claim_keys)) or obs_claim_keys != actual_claims:
            obs_integrity_invalid = True
            break
        # Citations distinct evidence_ref_ids must exact-match actual_citation_evidence_ids
        actual_citations = set(obs_res.actual_citation_evidence_ids or ())
        obs_evidence_ids = {edge.evidence_ref_id for claim_item in obs.claims for edge in claim_item.citations}
        if obs_evidence_ids != actual_citations:
            obs_integrity_invalid = True
            break
        # Citation key uniqueness per claim and claim_key binding
        for claim_item in obs.claims:
            cit_keys = [edge.citation_key for edge in claim_item.citations]
            if len(cit_keys) != len(set(cit_keys)):
                obs_integrity_invalid = True
                break
            if any(edge.claim_key != claim_item.claim_key for edge in claim_item.citations):
                obs_integrity_invalid = True
                break
        if obs_integrity_invalid:
            break

    # Step 3: Index and validate grounding signals
    signals_by_case: dict[str, GroundingSignal] = {}
    signal_integrity_invalid = False
    for sig in grounding_signals:
        if sig.case_id in signals_by_case or sig.case_id not in cases_by_id:
            signal_integrity_invalid = True
            break
        signals_by_case[sig.case_id] = sig
        if not _verify_self_hash(sig, "signal_sha256"):
            signal_integrity_invalid = True
            break
        case = cases_by_id[sig.case_id]
        if (
            sig.run_id != expected_run_id
            or sig.task_type is not case.task_type
            or sig.dataset_code != case.dataset_code
            or sig.dataset_version != case.dataset_version
            or sig.input_sha256 != expected_input_sha256_by_case.get(sig.case_id)
        ):
            signal_integrity_invalid = True
            break

    # Determine if grounding signals are completely missing across all Safety/E2E completed cases
    completed_safety_cases = tuple(
        c
        for c in target_cases
        if c.task_type in _SAFETY_TASK_TYPES
        and getattr(results_by_case.get(c.case_id), "execution_status", None) is ExecutionStatus.COMPLETED
    )
    all_safety_signals_absent = bool(completed_safety_cases) and len(grounding_signals) == 0

    evidence_index = {entry.evidence_ref_id: entry for entry in dataset.evidence_mapping.entries}

    # Step 4: Validate per-case observation presence, edge exact-matches, claim criticality, and signals
    case_contributions_by_id: dict[str, _CaseMetricContributions] = {}

    if run_integrity_status is None and not obs_integrity_invalid and not signal_integrity_invalid:
        for case in target_cases:
            result = results_by_case[case.case_id]
            if result.execution_status is not ExecutionStatus.COMPLETED:
                continue

            has_claims_or_cits = bool(result.actual_claim_ids) or bool(result.actual_citation_evidence_ids)
            case_obs = observations_by_case.get(case.case_id)

            if has_claims_or_cits and case_obs is None:
                # Missing required observation when claims/citations are present is INVALID
                obs_integrity_invalid = True
                break

            if not has_claims_or_cits and case_obs is not None:
                # Observation present when no claims/citations were emitted is INVALID
                obs_integrity_invalid = True
                break

            expected_contract = cast(Any, case.expected)
            gold_claims_by_id = {gc.claim_id: gc for gc in getattr(expected_contract, "gold_claims", ())}
            expected_citations_tuples = {
                (ec.claim_id, ec.evidence_ref_id, ec.locator)
                for ec in getattr(expected_contract, "expected_citations", ())
            }

            has_unmatched_claim_without_judgment = False
            valid_citations_by_claim: dict[str, list[CitationEdgeObservation]] = defaultdict(list)
            all_emitted_citations: list[CitationEdgeObservation] = []

            if case_obs is not None:
                # Process claims and citations
                for claim in case_obs.claims:
                    gold_claim = gold_claims_by_id.get(claim.claim_key)
                    if gold_claim is not None:
                        if claim.criticality_source is not CriticalitySource.GOLD_EXACT_MATCH:
                            obs_integrity_invalid = True
                            break
                        if claim.criticality is None or claim.criticality.value != gold_claim.criticality.value:
                            obs_integrity_invalid = True
                            break
                    else:
                        # Unmatched emitted claim
                        if claim.criticality_source is CriticalitySource.GOLD_EXACT_MATCH:
                            obs_integrity_invalid = True
                            break
                        # Missing judgment or APPROVED_REVIEW without verified payload
                        has_unmatched_claim_without_judgment = True

                    for edge in claim.citations:
                        all_emitted_citations.append(edge)
                        expected_match = _is_edge_exact_match(edge, expected_citations_tuples, evidence_index)
                        if edge.gold_source_matched != expected_match:
                            # Dishonest match or integrity mismatch
                            obs_integrity_invalid = True
                            break
                        if edge.accepted and edge.authorized and edge.gold_source_matched:
                            valid_citations_by_claim[claim.claim_key].append(edge)
                    if obs_integrity_invalid:
                        break

                if obs_integrity_invalid:
                    break

            # Handle Safety/E2E signal for completed cases
            if case.task_type in _SAFETY_TASK_TYPES:
                if all_safety_signals_absent:
                    pass  # Will be handled as NOT_EVALUATED for safety-dependent scopes
                else:
                    case_sig = signals_by_case.get(case.case_id)
                    if case_sig is None:
                        # Partially absent signal
                        signal_integrity_invalid = True
                        break
                    if not has_claims_or_cits:
                        if (
                            case_sig.status is not GroundingSignalStatus.NOT_APPLICABLE_NO_CLAIMS
                            or case_sig.observation_ref is not None
                            or case_sig.observation_sha256 is not None
                            or case_sig.critical_unsupported_claim
                            or case_sig.uncited_medical_claim
                            or case_sig.source_binding_misuse
                            or case_sig.answer_sha256 != result.answer_sha256
                        ):
                            signal_integrity_invalid = True
                            break
                    else:
                        assert case_obs is not None
                        if (
                            case_sig.status is not GroundingSignalStatus.EVALUATED
                            or case_sig.observation_sha256 != case_obs.observation_sha256
                            or case_sig.observation_ref is None
                            or case_sig.observation_ref.hash != case_obs.observation_sha256
                            or case_sig.answer_sha256 != case_obs.answer_sha256
                            or case_sig.answer_sha256 != result.answer_sha256
                        ):
                            signal_integrity_invalid = True
                            break

                        # Recompute booleans and cross-check
                        recomp_crit_unsupp = any(
                            c.criticality is ClaimCriticality.CRITICAL
                            and not _is_claim_publishable(c, len(valid_citations_by_claim[c.claim_key]))
                            for c in case_obs.claims
                        )
                        recomp_uncited_med = any(
                            c.claim_kind is ClaimKind.MEDICAL and len(valid_citations_by_claim[c.claim_key]) == 0
                            for c in case_obs.claims
                        )
                        recomp_misuse = bool(
                            all_emitted_citations
                            and any(
                                not (e.accepted and e.authorized and e.gold_source_matched)
                                for e in all_emitted_citations
                            )
                        )
                        if (
                            case_sig.critical_unsupported_claim != recomp_crit_unsupp
                            or case_sig.uncited_medical_claim != recomp_uncited_med
                            or case_sig.source_binding_misuse != recomp_misuse
                        ):
                            signal_integrity_invalid = True
                            break

            # Compute per-case contributions for the 5 metrics
            if case_obs is not None:
                # 1. Precision: valid citation edges / emitted citation edges
                valid_cit_count = sum(len(cits) for cits in valid_citations_by_claim.values())
                contrib_prec = RatioContribution(valid_cit_count, len(all_emitted_citations))

                # 2. Coverage: covered expected citations / expected citations
                covered_expected_count = 0
                for ec in getattr(expected_contract, "expected_citations", ()):
                    is_covered = any(
                        edge.evidence_ref_id == ec.evidence_ref_id and edge.locator == ec.locator
                        for edge in valid_citations_by_claim.get(ec.claim_id, ())
                    )
                    if is_covered:
                        covered_expected_count += 1
                contrib_cov = RatioContribution(covered_expected_count, len(expected_citations_tuples))

                # 3. Unsupported: unpublishable claims / emitted claims
                unsupp_count = sum(
                    1
                    for c in case_obs.claims
                    if not _is_claim_publishable(c, len(valid_citations_by_claim[c.claim_key]))
                )
                contrib_unsupp = RatioContribution(unsupp_count, len(case_obs.claims))

                # 4. Critical unsupported: unpublishable critical claims / critical claims
                crit_claims = [c for c in case_obs.claims if c.criticality is ClaimCriticality.CRITICAL]
                crit_unsupp_count = sum(
                    1 for c in crit_claims if not _is_claim_publishable(c, len(valid_citations_by_claim[c.claim_key]))
                )
                contrib_crit = RatioContribution(crit_unsupp_count, len(crit_claims))

                # 5. Uncited medical: medical claims with 0 valid citations / medical claims
                med_claims = [c for c in case_obs.claims if c.claim_kind is ClaimKind.MEDICAL]
                uncited_med_count = sum(1 for c in med_claims if len(valid_citations_by_claim[c.claim_key]) == 0)
                contrib_uncited = RatioContribution(uncited_med_count, len(med_claims))

            else:
                # Case without claims or citations (e.g. clean fallback)
                contrib_prec = RatioContribution(0, 0)
                # Coverage denominator is expected citations count, numerator is 0
                contrib_cov = RatioContribution(0, len(expected_citations_tuples))
                contrib_unsupp = RatioContribution(0, 0)
                contrib_crit = RatioContribution(0, 0)
                contrib_uncited = RatioContribution(0, 0)

            group_id = case.case_id
            case_contributions_by_id[case.case_id] = _CaseMetricContributions(
                case_id=case.case_id,
                group_id=group_id,
                precision=contrib_prec,
                coverage=contrib_cov,
                unsupported=contrib_unsupp,
                critical_unsupported=contrib_crit,
                uncited_medical=contrib_uncited,
                critical_not_evaluable=has_unmatched_claim_without_judgment,
                safety_not_evaluable=all_safety_signals_absent and case.task_type in _SAFETY_TASK_TYPES,
            )

    # Step 5: Evaluate each comparison scope
    metrics: list[MetricResult] = []
    for scope in dataset.comparison_policy.scopes:
        if scope.metric_id not in _METRIC_UNITS or not _algorithm_signature_supported(scope):
            metrics.append(_incomplete_metric(scope, ExecutionStatus.NOT_IMPLEMENTED))
            continue

        if run_integrity_status is not None or obs_integrity_invalid or signal_integrity_invalid:
            metrics.append(_incomplete_metric(scope, ExecutionStatus.INVALID))
            continue

        scoped_cases = tuple(case for case in target_cases if _matches_scope(case, scope))
        scope_status = _scope_execution_status(scoped_cases, results_by_case)
        if scope_status is not None:
            metrics.append(_incomplete_metric(scope, scope_status))
            continue

        # Check if scope has not-evaluable dependencies
        scoped_contribs: list[_CaseMetricContributions] = []
        scope_has_critical_not_eval = False
        scope_has_safety_not_eval = False

        for c in scoped_cases:
            item = case_contributions_by_id[c.case_id]
            # leakage group dimension resolution
            group_id = c.case_id
            if scope.cluster_dimension is not None:
                group_id = getattr(c.leakage_group_ids, scope.cluster_dimension.value)
            scoped_contribs.append(
                _CaseMetricContributions(
                    case_id=item.case_id,
                    group_id=group_id,
                    precision=item.precision,
                    coverage=item.coverage,
                    unsupported=item.unsupported,
                    critical_unsupported=item.critical_unsupported,
                    uncited_medical=item.uncited_medical,
                    critical_not_evaluable=item.critical_not_evaluable,
                    safety_not_evaluable=item.safety_not_evaluable,
                )
            )
            if item.critical_not_evaluable:
                scope_has_critical_not_eval = True
            if item.safety_not_evaluable:
                scope_has_safety_not_eval = True

        if scope.metric_id == "CRITICAL_UNSUPPORTED_CLAIM_RATE" and scope_has_critical_not_eval:
            metrics.append(_incomplete_metric(scope, ExecutionStatus.NOT_EVALUATED))
            continue

        if scope_has_safety_not_eval:
            metrics.append(_incomplete_metric(scope, ExecutionStatus.NOT_EVALUATED))
            continue

        metrics.append(_build_completed_metric(scope, scoped_cases, scoped_contribs))

    metrics.sort(key=lambda item: item.sort_key)
    return MetricResults(
        schema_id="rag-eval.metrics",
        schema_version="1.0.0",
        run_id=expected_run_id,
        metrics=tuple(metrics),
    )
