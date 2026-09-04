from dataclasses import replace
from pathlib import Path

import pytest

from ai_worker.tasks.evaluation.comparison import build_retrieval_comparison, load_published_run_bundle
from ai_worker.tasks.evaluation.manifest import (
    build_artifact_draft,
    content_artifact_entries,
    finalize_artifacts,
    machine_artifact_files,
    semantic_content_hash,
)
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.reporter import render_report
from ai_worker.tests.evaluation.test_result_manifest import (
    RUN_ID_A,
    RUN_ID_B,
    TIME_A,
    TIME_B,
    _material,
    retrieval_run_material,
)

REPOSITORY_ROOT = Path(__file__).parents[3]
RUN_ID = "123e4567-e89b-42d3-a456-426614174000"


def test_report_contains_only_machine_summary() -> None:
    draft = build_artifact_draft(_material(run_id=RUN_ID, started_at=TIME_B, complete=True))
    entries = content_artifact_entries(machine_artifact_files(draft))

    report = render_report(
        draft.report_data,
        draft.metrics,
        draft.suite_results,
        draft.failures,
        entries,
    ).decode()

    assert f"Run ID: `{RUN_ID}`" in report
    assert "DEV validation only" in report
    assert "Not a Release decision" in report
    assert "cases.jsonl" in report
    assert "report.md" not in report
    assert "result-content-manifest.json" not in report


def test_report_never_projects_dataset_query_or_gold_text() -> None:
    material = _material(run_id=RUN_ID, started_at=TIME_B, complete=True)
    draft = build_artifact_draft(material)

    report = render_report(
        draft.report_data,
        draft.metrics,
        draft.suite_results,
        draft.failures,
        content_artifact_entries(machine_artifact_files(draft)),
    )

    forbidden = [case.query for case in material.dataset.cases] + [
        claim.claim_text for case in material.dataset.cases for claim in (case.expected.gold_claims or ())
    ]
    assert all(value.encode() not in report for value in forbidden)
    validate_privacy_boundary({"report": report.decode("utf-8")})


def test_editing_report_does_not_change_semantic_hash() -> None:
    draft = build_artifact_draft(_material(run_id=RUN_ID, started_at=TIME_B, complete=True))
    artifacts = finalize_artifacts(draft, b"safe report\n", completed_at=TIME_B)
    before = semantic_content_hash(artifacts.files)
    edited = dict(artifacts.files)
    edited["report.md"] += b"\noperator note\n"

    assert semantic_content_hash(edited) == before


def test_candidate_report_projects_metric_counts_ci_and_dev_boundary(tmp_path: Path) -> None:
    baseline_draft = build_artifact_draft(retrieval_run_material("RET-L", run_id=RUN_ID_A))
    baseline_artifacts = finalize_artifacts(baseline_draft, b"safe report\n", completed_at=TIME_B)
    baseline_root = tmp_path / RUN_ID_A
    baseline_root.mkdir()
    for name, payload in baseline_artifacts.files.items():
        (baseline_root / name).write_bytes(payload)
    baseline = load_published_run_bundle(tmp_path, RUN_ID_A)
    candidate_material = retrieval_run_material("RET-HR", run_id=RUN_ID_B, started_at=TIME_A)
    candidate_draft = build_artifact_draft(candidate_material)
    comparison = build_retrieval_comparison(baseline, candidate_draft)
    candidate_draft = replace(candidate_draft, comparison=comparison)

    report = render_report(
        candidate_draft.report_data,
        candidate_draft.metrics,
        candidate_draft.suite_results,
        candidate_draft.failures,
        content_artifact_entries(machine_artifact_files(candidate_draft)),
        comparison,
        baseline_variant_id=baseline.run.variant_id,
        baseline_metrics=baseline.metrics,
    ).decode("utf-8")

    assert "# RAG Evaluation DEV Retrieval Report" in report
    assert "SYNTHETIC_REPLAY_DEV" in report
    assert "Recall@5" in report
    assert "`RECALL_AT_5@1.0.0`" in report
    assert "`CASE_MEAN@1.0.0`" in report
    assert "4/5" in report
    assert "95% CI" in report
    assert "RET-L" in report and "RET-HR" in report
    assert comparison.baseline_run_id in report
    assert comparison.baseline_run_hash in report
    assert comparison.candidate_run_id in report
    assert comparison.candidate_run_hash in report
    assert "Absolute Delta" in report
    assert "INCONCLUSIVE" in report
    assert "HOLDOUT Baseline Freeze: `NOT_PERFORMED`" in report
    assert "BLOCKED_BY_RAG_07A_07B_OR_08" in report
    forbidden = [case.query for case in candidate_material.dataset.cases] + [
        claim.claim_text for case in candidate_material.dataset.cases for claim in (case.expected.gold_claims or ())
    ]
    assert all(value not in report for value in forbidden)


@pytest.mark.parametrize(
    ("baseline_variant_id", "baseline_metrics"),
    [(None, "present"), ("", "present"), ("RET-L", None), ("RET-L", "empty")],
)
def test_comparison_report_rejects_missing_baseline_context(
    tmp_path: Path,
    baseline_variant_id: str | None,
    baseline_metrics: str | None,
) -> None:
    baseline_draft = build_artifact_draft(retrieval_run_material("RET-L", run_id=RUN_ID_A))
    baseline_artifacts = finalize_artifacts(baseline_draft, b"safe report\n", completed_at=TIME_B)
    baseline_root = tmp_path / RUN_ID_A
    baseline_root.mkdir()
    for name, payload in baseline_artifacts.files.items():
        (baseline_root / name).write_bytes(payload)
    baseline = load_published_run_bundle(tmp_path, RUN_ID_A)
    candidate = build_artifact_draft(retrieval_run_material("RET-HR", run_id=RUN_ID_B))
    comparison = build_retrieval_comparison(baseline, candidate)

    with pytest.raises(ValueError, match="comparison requires complete baseline report context"):
        render_report(
            candidate.report_data,
            candidate.metrics,
            candidate.suite_results,
            candidate.failures,
            content_artifact_entries(machine_artifact_files(candidate)),
            comparison,
            baseline_variant_id=baseline_variant_id,
            baseline_metrics=(
                baseline.metrics
                if baseline_metrics == "present"
                else baseline.metrics.model_copy(update={"metrics": ()})
                if baseline_metrics == "empty"
                else None
            ),
        )


def test_report_projects_real_retrieval_failure_row() -> None:
    draft = build_artifact_draft(retrieval_run_material("RET-L", run_id=RUN_ID_A))

    report = render_report(
        draft.report_data,
        draft.metrics,
        draft.suite_results,
        draft.failures,
        content_artifact_entries(machine_artifact_files(draft)),
    ).decode("utf-8")

    assert "| `rag-ret-dev-004` | `REQUIRED_EVIDENCE_NOT_IN_TOP_5` |" in report
