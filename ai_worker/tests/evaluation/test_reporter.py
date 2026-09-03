from pathlib import Path

from ai_worker.tasks.evaluation.manifest import (
    build_artifact_draft,
    content_artifact_entries,
    finalize_artifacts,
    machine_artifact_files,
    semantic_content_hash,
)
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.reporter import render_report
from ai_worker.tests.evaluation.test_result_manifest import TIME_B, _material

REPOSITORY_ROOT = Path(__file__).parents[3]
RUN_ID = "123e4567-e89b-42d3-a456-426614174000"


def test_report_contains_only_machine_summary() -> None:
    draft = build_artifact_draft(_material(run_id=RUN_ID, started_at=TIME_B, complete=True))
    entries = content_artifact_entries(machine_artifact_files(draft))

    report = render_report(draft.report_data, draft.metrics, draft.suite_results, entries).decode()

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
