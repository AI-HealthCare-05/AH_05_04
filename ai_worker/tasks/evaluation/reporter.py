from __future__ import annotations

from collections.abc import Sequence

from ai_worker.tasks.evaluation.manifest import ReportData
from ai_worker.tasks.evaluation.schemas.artifacts import ContentArtifact, MetricResults, SuiteResults


def render_report(
    report_data: ReportData,
    metrics: MetricResults,
    suite_results: SuiteResults,
    entries: Sequence[ContentArtifact],
) -> bytes:
    decision = report_data.decision_status.value if report_data.decision_status is not None else "null"
    lines = [
        "# RAG Evaluation DEV Report",
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
    lines.extend(["", "## Machine Artifacts", ""])
    lines.extend(f"- `{entry.relative_path}` `{entry.sha256}`" for entry in entries)
    return ("\n".join(lines) + "\n").encode("utf-8")
