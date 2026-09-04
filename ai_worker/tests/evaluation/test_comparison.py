from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.comparison import build_retrieval_comparison, load_published_run_bundle
from ai_worker.tasks.evaluation.errors import EvaluationValidationError
from ai_worker.tasks.evaluation.manifest import (
    ArtifactDraft,
    build_artifact_draft,
    finalize_artifacts,
    semantic_content_hash,
)
from ai_worker.tests.evaluation.test_result_manifest import (
    RUN_ID_A,
    RUN_ID_B,
    TIME_A,
    TIME_B,
    retrieval_run_material,
)


def _draft(variant: str, *, run_id: str) -> ArtifactDraft:
    return build_artifact_draft(retrieval_run_material(variant, run_id=run_id, started_at=TIME_A))


def _publish(root: Path, draft: ArtifactDraft) -> Path:
    artifacts = finalize_artifacts(draft, b"safe retrieval report\n", completed_at=TIME_B)
    run_root = root / draft.report_data.run_id
    run_root.mkdir()
    for name, payload in artifacts.files.items():
        (run_root / name).write_bytes(payload)
    return run_root


def _loaded_baseline(tmp_path: Path):
    _publish(tmp_path, _draft("RET-L", run_id=RUN_ID_A))
    return load_published_run_bundle(tmp_path, RUN_ID_A)


def test_comparison_binds_semantic_hashes_and_reports_metric_delta(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    finalized_candidate = finalize_artifacts(
        candidate,
        b"safe retrieval report\n",
        completed_at=TIME_B,
    )

    comparison = build_retrieval_comparison(baseline, candidate)
    recall = next(item for item in comparison.scope_comparisons if item.metric_id == "RECALL_AT_5")

    assert comparison.baseline_run_hash == semantic_content_hash(baseline.files)
    assert comparison.candidate_run_hash == semantic_content_hash(finalized_candidate.files)
    assert recall.baseline_value == "0.8"
    assert recall.candidate_value == "1"
    assert recall.absolute_delta == "0.2"
    assert recall.relative_delta == "0.25"
    assert recall.comparison_decision.value == "INCONCLUSIVE"
    assert recall.paired_test_method is None
    assert recall.p_value is None
    assert comparison.decision_status is not None
    assert comparison.decision_status.value == "INCONCLUSIVE"


@pytest.mark.parametrize(
    ("field", "expected_key"),
    [
        ("partition_manifest_hash", "CASE_SET"),
        ("dataset_manifest_sha256", "DATASET"),
        ("resource_set_hash", "GOLD"),
        ("evidence_mapping_manifest_sha256", "GOLD"),
        ("comparison_policy_ref", "METRIC_POLICY"),
        ("model_config_hash", "SOURCE_INDEX_FILTER_MODEL"),
    ],
)
def test_controlled_variable_mismatch_invalidates_comparison(
    tmp_path: Path,
    field: str,
    expected_key: str,
) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    run_payload = dict(candidate.run_payload)
    if field == "comparison_policy_ref":
        reference = dict(run_payload[field])
        reference["hash"] = "b" * 64
        run_payload[field] = reference
    else:
        run_payload[field] = "b" * 64
    candidate = replace(candidate, run_payload=run_payload)

    comparison = build_retrieval_comparison(baseline, candidate)

    checks = {check.variable_key: check for check in comparison.controlled_variable_checks}
    assert list(checks) == [
        "CASE_SET",
        "DATASET",
        "GOLD",
        "METRIC_POLICY",
        "SOURCE_INDEX_FILTER_MODEL",
    ]
    assert checks[expected_key].matched is False
    assert comparison.execution_status.value == "INVALID"
    assert comparison.decision_status is None


def test_metric_natural_key_mismatch_invalidates_comparison(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    candidate = replace(
        candidate,
        metrics=candidate.metrics.model_copy(update={"metrics": candidate.metrics.metrics[:-1]}),
    )

    comparison = build_retrieval_comparison(baseline, candidate)

    assert comparison.scope_comparisons == ()
    assert comparison.execution_status.value == "INVALID"
    assert comparison.decision_status is None


def test_metric_version_mismatch_invalidates_comparison(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    changed_metric = candidate.metrics.metrics[0].model_copy(update={"metric_version": "2.0.0"})
    candidate = replace(
        candidate,
        metrics=candidate.metrics.model_copy(update={"metrics": (changed_metric, *candidate.metrics.metrics[1:])}),
    )

    comparison = build_retrieval_comparison(baseline, candidate)

    assert comparison.scope_comparisons == ()
    assert comparison.execution_status.value == "INVALID"
    assert comparison.decision_status is None


@pytest.mark.parametrize("tampered_name", ["run.json", "metrics.json", "result-content-manifest.json"])
def test_loader_rejects_tampered_bundle(tmp_path: Path, tampered_name: str) -> None:
    run_root = _publish(tmp_path, _draft("RET-L", run_id=RUN_ID_A))
    path = run_root / tampered_name
    payload = json.loads(path.read_bytes())
    if tampered_name == "run.json":
        payload["result_content_manifest_hash"] = "b" * 64
    elif tampered_name == "metrics.json":
        payload["metrics"][0]["metric_value"] = "0.7"
    else:
        payload["artifacts"][0]["sha256"] = "b" * 64
        payload["manifest_sha256"] = canonical_sha256(
            payload,
            excluded_top_level_keys=frozenset({"manifest_sha256"}),
        )
    path.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(EvaluationValidationError) as caught:
        load_published_run_bundle(tmp_path, RUN_ID_A)

    assert str(caught.value) == "EVAL_BASELINE_ARTIFACT_INVALID"


def test_loader_rejects_noncanonical_run_id_and_symlink_directory(tmp_path: Path) -> None:
    target = _publish(tmp_path, _draft("RET-L", run_id=RUN_ID_A))
    link_name = RUN_ID_B
    os.symlink(target, tmp_path / link_name)

    for run_id in (RUN_ID_A.upper(), link_name):
        with pytest.raises(EvaluationValidationError) as caught:
            load_published_run_bundle(tmp_path, run_id)
        assert str(caught.value) == "EVAL_BASELINE_ARTIFACT_INVALID"
