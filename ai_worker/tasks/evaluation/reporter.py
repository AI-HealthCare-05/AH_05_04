from __future__ import annotations

from collections.abc import Sequence

from ai_worker.tasks.evaluation.manifest import ReportData
from ai_worker.tasks.evaluation.schemas.artifacts import (
    ComparisonResult,
    ContentArtifact,
    MetricResult,
    MetricResults,
    SuiteResults,
)

_RETRIEVAL_METRIC_LABELS = {
    "MRR": "MRR",
    "NDCG_AT_5": "nDCG@5",
    "NO_HIT_RATE": "No-hit Rate",
    "PRECISION_AT_5": "Precision@5",
    "RECALL_AT_5": "Recall@5",
}


def _metric_row(metric: MetricResult) -> str:
    decision = metric.decision_status.value if metric.decision_status is not None else "null"
    count = f"{metric.numerator}/{metric.denominator}" if metric.numerator is not None else "N/A"
    sample = (
        f"{metric.sample_case_count}/{metric.sample_independent_group_count}"
        if metric.sample_case_count is not None
        else "N/A"
    )
    interval = f"[{metric.ci_lower}, {metric.ci_upper}]" if metric.ci_lower is not None else "N/A"
    reason = metric.reason_code or "N/A"
    label = _RETRIEVAL_METRIC_LABELS.get(metric.metric_id, metric.metric_id)
    return (
        f"| {label} (`{metric.metric_id}@{metric.metric_version}`) | "
        f"`{metric.estimator_id}@{metric.estimator_version}` | {sample} | {count} | "
        f"{metric.metric_value or 'N/A'} | {interval} | {metric.execution_status.value} | {decision} | {reason} |"
    )


def _retrieval_sections(
    report_data: ReportData,
    metrics: MetricResults,
    comparison: ComparisonResult | None,
    baseline_variant_id: str | None,
    baseline_metrics: MetricResults | None,
) -> list[str]:
    lines = [
        "",
        "## Retrieval Metrics",
        "",
        "- Data Source: `SYNTHETIC_REPLAY_DEV`",
        "- HOLDOUT Baseline Freeze: `NOT_PERFORMED`",
        "- Production Integration: `BLOCKED_BY_RAG_07A_07B_OR_08`",
        "",
        "| Metric ID@Version | Estimator@Version | Cases/Groups | Numerator/Denominator | Value | 95% CI | Execution | Decision | Reason |",
        "| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
    ]
    lines.extend(_metric_row(metric) for metric in metrics.metrics)
    if comparison is None:
        return lines
    if not baseline_variant_id or baseline_metrics is None:
        raise ValueError("comparison requires complete baseline report context")
    baseline_variant = baseline_variant_id
    comparison_decision = comparison.decision_status.value if comparison.decision_status is not None else "null"
    lines.extend(
        [
            "",
            "## Baseline Comparison",
            "",
            f"- Baseline: `{baseline_variant}` Run `{comparison.baseline_run_id}` Semantic Hash `{comparison.baseline_run_hash}`",
            f"- Candidate: `{report_data.variant_id}` Run `{comparison.candidate_run_id}` Semantic Hash `{comparison.candidate_run_hash}`",
            f"- Comparison Execution: `{comparison.execution_status.value}`",
            f"- Comparison Decision: `{comparison_decision}`",
            "",
            f"### Baseline Retrieval Metrics (`{baseline_variant}`)",
            "",
            "| Metric ID@Version | Estimator@Version | Cases/Groups | Numerator/Denominator | Value | 95% CI | Execution | Decision | Reason |",
            "| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
            *[_metric_row(metric) for metric in baseline_metrics.metrics],
            "",
            "| Metric ID | Baseline | Candidate | Absolute Delta | Decision |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    lines.extend(
        f"| {_RETRIEVAL_METRIC_LABELS.get(scope.metric_id, scope.metric_id)} (`{scope.metric_id}`) | "
        f"{scope.baseline_value or 'N/A'} | {scope.candidate_value or 'N/A'} | "
        f"{scope.absolute_delta or 'N/A'} | {scope.comparison_decision.value} |"
        for scope in comparison.scope_comparisons
    )
    return lines


def render_report(
    report_data: ReportData,
    metrics: MetricResults,
    suite_results: SuiteResults,
    entries: Sequence[ContentArtifact],
    comparison: ComparisonResult | None = None,
    *,
    baseline_variant_id: str | None = None,
    baseline_metrics: MetricResults | None = None,
) -> bytes:
    if comparison is not None and (not baseline_variant_id or baseline_metrics is None):
        raise ValueError("comparison requires complete baseline report context")
    decision = report_data.decision_status.value if report_data.decision_status is not None else "null"
    lines = [
        (
            "# RAG Evaluation DEV Retrieval Report"
            if report_data.experiment_type.value == "KNOWLEDGE_RETRIEVAL"
            else "# RAG Evaluation DEV Report"
        ),
        "",
        "> DEV validation only — Not a Release decision",
        "",
        f"- Run ID: `{report_data.run_id}`",
        f"- Experiment Type: `{report_data.experiment_type.value}`",
        f"- Variant ID: `{report_data.variant_id}`",
        f"- Execution Status: `{report_data.execution_status.value}`",
        f"- Decision Status: `{decision}`",
        f"- Dataset: `{report_data.dataset_code}@{report_data.dataset_version}`",
        (
            f"- Evaluation Profile: `{report_data.evaluation_profile_ref.id}@"
            f"{report_data.evaluation_profile_ref.version}` `{report_data.evaluation_profile_ref.hash}`"
        ),
        (
            f"- Comparison Policy: `{report_data.comparison_policy_ref.id}@"
            f"{report_data.comparison_policy_ref.version}` `{report_data.comparison_policy_ref.hash}`"
        ),
        (
            f"- Evaluation Policy: `{report_data.evaluation_policy_ref.id}@"
            f"{report_data.evaluation_policy_ref.version}` `{report_data.evaluation_policy_ref.hash}`"
        ),
        f"- Suite: `{report_data.suite_ref.id}@{report_data.suite_ref.version}` `{report_data.suite_ref.hash}`",
        f"- Metric Records: `{len(metrics.metrics)}`",
        f"- Suite Status: `{suite_results.aggregate_execution_status.value}`",
        "",
        "## Task Counts",
        "",
    ]
    lines.extend(
        f"- `{task_type}`: {count}"
        for task_type, count in sorted(
            report_data.task_case_counts.items(),
            key=lambda item: item[0].encode("utf-16-be"),
        )
    )
    lines.extend(["", "## Blocking and Failures", ""])
    lines.extend(f"- `{status.value}`" for status in report_data.blocking_execution_statuses)
    lines.extend(f"- `{code}`" for code in report_data.failure_codes)
    suite_failures = sorted(
        (item for item in suite_results.case_results if item.failure_code is not None),
        key=lambda item: item.case_code.encode("utf-16-be"),
    )
    if suite_failures:
        lines.extend(
            [
                "",
                "### Suite Failure Rows",
                "",
                "| Case Code | Failure Code |",
                "| --- | --- |",
                *(f"| `{item.case_code}` | `{item.failure_code}` |" for item in suite_failures),
            ]
        )
    if report_data.experiment_type.value == "KNOWLEDGE_RETRIEVAL":
        lines.extend(_retrieval_sections(report_data, metrics, comparison, baseline_variant_id, baseline_metrics))
    lines.extend(["", "## Machine Artifacts", ""])
    lines.extend(f"- `{entry.relative_path}` `{entry.sha256}`" for entry in entries)
    return ("\n".join(lines) + "\n").encode("utf-8")
