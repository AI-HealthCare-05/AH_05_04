from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.cli import main
from ai_worker.tasks.evaluation.comparison import build_retrieval_comparison, load_published_run_bundle
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.manifest import (
    ArtifactDraft,
    build_artifact_draft,
    finalize_artifacts,
    semantic_content_hash,
)
from ai_worker.tasks.evaluation.schemas.artifacts import ComparisonResult, FailureRecord, FailureSummary
from ai_worker.tasks.evaluation.schemas.common import ExecutionStatus, ExperimentType
from ai_worker.tests.evaluation.test_result_manifest import (
    RUN_ID_A,
    RUN_ID_B,
    TIME_A,
    TIME_B,
    retrieval_run_material,
)

CONTROLLED_VARIABLE_KEYS = (
    "CASE_SET",
    "DATASET",
    "GOLD",
    "METRIC_POLICY",
    "SOURCE_INDEX_FILTER_MODEL",
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


def _comparison(run_id: str) -> ComparisonResult:
    return ComparisonResult.model_validate(
        {
            "schema_id": "rag-eval.comparison",
            "schema_version": "1.0.0",
            "run_id": run_id,
            "experiment_id": "rag-retrieval-dev",
            "baseline_run_id": RUN_ID_A,
            "baseline_run_hash": "a" * 64,
            "candidate_run_id": run_id,
            "candidate_run_hash": "b" * 64,
            "controlled_variable_checks": [
                {
                    "variable_key": "DATASET",
                    "baseline_value_hash": "c" * 64,
                    "candidate_value_hash": "c" * 64,
                    "matched": True,
                }
            ],
            "scope_comparisons": [
                {
                    "metric_id": "RECALL_AT_5",
                    "partition": "DEV",
                    "slice_id": "ALL",
                    "baseline_value": "0.8",
                    "candidate_value": "1",
                    "absolute_delta": "0.2",
                    "relative_delta": "0.25",
                    "paired_test_method": None,
                    "p_value": None,
                    "comparison_decision": "INCONCLUSIVE",
                }
            ],
            "execution_status": "COMPLETED",
            "decision_status": "INCONCLUSIVE",
        }
    )


def _failure(run_id: str) -> FailureRecord:
    return FailureRecord(
        schema_id="rag-eval.failure",
        schema_version="1.0.0",
        run_id=run_id,
        case_id="rag-ret-dev-001",
        failure_code="SYNTHETIC_FAILURE",
        failure_stage="RETRIEVAL",
        expected_summary=FailureSummary.EXPECTED_REQUIRED_EVIDENCE,
        actual_summary=FailureSummary.ACTUAL_REQUIRED_EVIDENCE_MISSING,
        root_cause_code=None,
        followup_issue_ref=None,
        created_at=TIME_A,
    )


def _rehash_bundle_payload(run_root: Path, payload_name: str) -> None:
    payload = (run_root / payload_name).read_bytes()
    manifest_path = run_root / "result-content-manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    entry = next(item for item in manifest["artifacts"] if item["relative_path"] == payload_name)
    entry["sha256"] = sha256_hex(payload)
    entry["size_bytes"] = len(payload)
    manifest["manifest_sha256"] = canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    run_path = run_root / "run.json"
    run = json.loads(run_path.read_bytes())
    run["result_content_manifest_hash"] = manifest["manifest_sha256"]
    run_path.write_bytes(canonical_json_bytes(run))


def test_comparison_binds_semantic_hashes_and_reports_metric_delta(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    finalized_candidate = finalize_artifacts(
        candidate,
        b"safe retrieval report\n",
        completed_at=TIME_B,
    )

    comparison = build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)
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


def test_comparison_builder_rejects_different_experiment_id(tmp_path: Path) -> None:
    baseline_draft = _draft("RET-L", run_id=RUN_ID_A)
    baseline_draft = replace(
        baseline_draft,
        report_data=replace(baseline_draft.report_data, experiment_id="unrelated-experiment"),
        run_payload={**baseline_draft.run_payload, "experiment_id": "unrelated-experiment"},
    )
    _publish(tmp_path, baseline_draft)
    baseline = load_published_run_bundle(tmp_path, RUN_ID_A)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)

    with pytest.raises(EvaluationValidationError) as caught:
        build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_comparison_builder_rejects_same_run(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)

    with pytest.raises(EvaluationValidationError) as caught:
        build_retrieval_comparison(baseline, baseline, CONTROLLED_VARIABLE_KEYS)

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_comparison_builder_rejects_same_variant(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-L", run_id=RUN_ID_B)

    with pytest.raises(EvaluationValidationError) as caught:
        build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_comparison_builder_rejects_relabeled_variant_with_same_manifest_hash(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-L", run_id=RUN_ID_B)
    candidate = replace(
        candidate,
        report_data=replace(candidate.report_data, variant_id="RET-ALIAS"),
        run_payload={**candidate.run_payload, "variant_id": "RET-ALIAS"},
    )

    with pytest.raises(EvaluationValidationError) as caught:
        build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_comparison_builder_rejects_non_retrieval_candidate(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    candidate = replace(
        candidate,
        report_data=replace(candidate.report_data, experiment_type=ExperimentType.END_TO_END_RAG),
        run_payload={**candidate.run_payload, "experiment_type": ExperimentType.END_TO_END_RAG.value},
    )

    with pytest.raises(EvaluationValidationError) as caught:
        build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_comparison_builder_rejects_inconsistent_draft_identity(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    candidate = replace(
        candidate,
        run_payload={**candidate.run_payload, "experiment_id": "unrelated-experiment"},
    )

    with pytest.raises(EvaluationValidationError) as caught:
        build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_comparison_builder_rejects_inconsistent_published_artifact(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = finalize_artifacts(
        _draft("RET-HR", run_id=RUN_ID_B),
        b"safe retrieval report\n",
        completed_at=TIME_B,
    )
    files = dict(candidate.files)
    run_payload = json.loads(files["run.json"])
    run_payload["experiment_id"] = "unrelated-experiment"
    files["run.json"] = canonical_json_bytes(run_payload)
    candidate = replace(candidate, files=files)

    with pytest.raises(EvaluationValidationError) as caught:
        build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


@pytest.mark.parametrize(
    "controlled_variable_keys",
    [
        CONTROLLED_VARIABLE_KEYS[:-1],
        tuple(reversed(CONTROLLED_VARIABLE_KEYS)),
        (*CONTROLLED_VARIABLE_KEYS, "DATASET"),
        (*CONTROLLED_VARIABLE_KEYS[:-1], "UNKNOWN"),
    ],
)
def test_comparison_builder_requires_exact_control_signature(
    tmp_path: Path,
    controlled_variable_keys: tuple[str, ...],
) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)

    with pytest.raises(EvaluationValidationError) as caught:
        build_retrieval_comparison(baseline, candidate, controlled_variable_keys)

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


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
        reference_value = run_payload[field]
        assert isinstance(reference_value, dict)
        reference = dict(reference_value)
        reference["hash"] = "b" * 64
        run_payload[field] = reference
    else:
        run_payload[field] = "b" * 64
    candidate = replace(candidate, run_payload=run_payload)

    comparison = build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)

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

    comparison = build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)
    _publish(tmp_path, replace(candidate, comparison=comparison))
    loaded = load_published_run_bundle(tmp_path, RUN_ID_B)

    assert comparison.scope_comparisons == ()
    assert comparison.execution_status.value == "INVALID"
    assert comparison.decision_status is None
    assert loaded.comparison == comparison


def test_metric_version_mismatch_invalidates_comparison(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    changed_metric = candidate.metrics.metrics[0].model_copy(update={"metric_version": "2.0.0"})
    candidate = replace(
        candidate,
        metrics=candidate.metrics.model_copy(update={"metrics": (changed_metric, *candidate.metrics.metrics[1:])}),
    )

    comparison = build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)
    _publish(tmp_path, replace(candidate, comparison=comparison))
    loaded = load_published_run_bundle(tmp_path, RUN_ID_B)

    assert comparison.scope_comparisons == ()
    assert comparison.execution_status.value == "INVALID"
    assert comparison.decision_status is None
    assert loaded.comparison == comparison


def test_incomplete_candidate_metric_invalidates_comparison(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    incomplete = candidate.metrics.metrics[0].model_copy(
        update={
            "execution_status": ExecutionStatus.ERROR,
            "decision_status": None,
            "sample_case_count": None,
            "sample_independent_group_count": None,
            "numerator": None,
            "denominator": None,
            "metric_value": None,
            "ci_lower": None,
            "ci_upper": None,
            "reason_code": None,
        }
    )
    candidate = replace(
        candidate,
        metrics=candidate.metrics.model_copy(update={"metrics": (incomplete, *candidate.metrics.metrics[1:])}),
    )

    comparison = build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)

    assert comparison.execution_status is ExecutionStatus.INVALID
    assert comparison.decision_status is None


@pytest.mark.parametrize("metric_status", [ExecutionStatus.ERROR, ExecutionStatus.NOT_IMPLEMENTED])
def test_incomplete_metric_invalid_comparison_survives_publish_reload(
    tmp_path: Path,
    metric_status: ExecutionStatus,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    incomplete = candidate.metrics.metrics[0].model_copy(
        update={
            "execution_status": metric_status,
            "decision_status": None,
            "sample_case_count": None,
            "sample_independent_group_count": None,
            "numerator": None,
            "denominator": None,
            "metric_value": None,
            "ci_lower": None,
            "ci_upper": None,
            "reason_code": None,
        }
    )
    candidate = replace(
        candidate,
        metrics=candidate.metrics.model_copy(update={"metrics": (incomplete, *candidate.metrics.metrics[1:])}),
    )
    comparison = build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)
    _publish(tmp_path, replace(candidate, comparison=comparison))

    loaded = load_published_run_bundle(tmp_path, RUN_ID_B)
    exit_code = main(["verify-result", "--run-id", RUN_ID_B], allowed_result_root=tmp_path)
    captured = capsys.readouterr()

    assert comparison.execution_status is ExecutionStatus.INVALID
    assert comparison.decision_status is None
    assert comparison.scope_comparisons == ()
    assert loaded.comparison == comparison
    assert exit_code == 0
    assert captured.err == ""
    assert captured.out == f"{loaded.semantic_hash}\n"


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


def test_baseline_artifact_error_uses_shared_error_catalog() -> None:
    assert EvaluationErrorCode("EVAL_BASELINE_ARTIFACT_INVALID") is EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


@pytest.mark.parametrize("mixed_name", ["cases.jsonl", "failures.jsonl", "comparison.json"])
def test_loader_rejects_correctly_rehashed_cross_run_records(tmp_path: Path, mixed_name: str) -> None:
    draft_a = _draft("RET-L", run_id=RUN_ID_A)
    draft_b = _draft("RET-L", run_id=RUN_ID_B)
    if mixed_name == "failures.jsonl":
        draft_a = replace(draft_a, failures=(_failure(RUN_ID_A),))
        draft_b = replace(draft_b, failures=(_failure(RUN_ID_B),))
    elif mixed_name == "comparison.json":
        draft_a = replace(draft_a, comparison=_comparison(RUN_ID_A))
        draft_b = replace(draft_b, comparison=_comparison(RUN_ID_B))
    root_a = _publish(tmp_path, draft_a)
    root_b = _publish(tmp_path, draft_b)
    (root_a / mixed_name).write_bytes((root_b / mixed_name).read_bytes())
    _rehash_bundle_payload(root_a, mixed_name)

    with pytest.raises(EvaluationValidationError) as caught:
        load_published_run_bundle(tmp_path, RUN_ID_A)

    assert caught.value.code is EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


@pytest.mark.parametrize("identity_field", ["run_id", "candidate_run_id"])
def test_loader_binds_each_comparison_identity_to_directory_run(
    tmp_path: Path,
    identity_field: str,
) -> None:
    draft = _draft("RET-L", run_id=RUN_ID_A)
    comparison = _comparison(RUN_ID_A).model_copy(update={identity_field: RUN_ID_B})
    _publish(tmp_path, replace(draft, comparison=comparison))

    with pytest.raises(EvaluationValidationError) as caught:
        load_published_run_bundle(tmp_path, RUN_ID_A)

    assert caught.value.code is EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_loader_rejects_rehashed_comparison_with_wrong_candidate_semantic_hash(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    comparison = build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS).model_copy(
        update={"candidate_run_hash": "b" * 64}
    )
    run_root = _publish(tmp_path, replace(candidate, comparison=comparison))
    _rehash_bundle_payload(run_root, "comparison.json")

    with pytest.raises(EvaluationValidationError) as caught:
        load_published_run_bundle(tmp_path, RUN_ID_B)

    assert caught.value.code is EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


@pytest.mark.parametrize(
    "tamper_kind",
    [
        "baseline_run_id",
        "baseline_run_hash",
        "baseline_control_value",
        "baseline_metric_value",
        "missing_control",
        "decision_semantics",
        "delta",
    ],
)
def test_loader_rejects_rehashed_comparison_with_wrong_baseline_binding(
    tmp_path: Path,
    tamper_kind: str,
) -> None:
    baseline = _loaded_baseline(tmp_path)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    comparison = build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)
    run_root = _publish(tmp_path, replace(candidate, comparison=comparison))
    comparison_path = run_root / "comparison.json"
    payload = json.loads(comparison_path.read_bytes())
    if tamper_kind == "baseline_run_id":
        payload["baseline_run_id"] = RUN_ID_B
    elif tamper_kind == "baseline_run_hash":
        payload["baseline_run_hash"] = "b" * 64
    elif tamper_kind == "baseline_control_value":
        payload["controlled_variable_checks"][0]["baseline_value_hash"] = "b" * 64
    elif tamper_kind == "baseline_metric_value":
        payload["scope_comparisons"][0]["baseline_value"] = "0.123456"
    elif tamper_kind == "decision_semantics":
        for scope in payload["scope_comparisons"]:
            scope["comparison_decision"] = "IMPROVED"
        payload["decision_status"] = "PASS"
    elif tamper_kind == "delta":
        payload["scope_comparisons"][0]["absolute_delta"] = "999"
        payload["scope_comparisons"][0]["relative_delta"] = "999"
    else:
        payload["controlled_variable_checks"].pop()
    comparison_path.write_bytes(canonical_json_bytes(payload))
    _rehash_bundle_payload(run_root, "comparison.json")

    with pytest.raises(EvaluationValidationError) as caught:
        load_published_run_bundle(tmp_path, RUN_ID_B)

    assert caught.value.code is EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_loader_rejects_candidate_when_referenced_baseline_is_not_in_result_root(tmp_path: Path) -> None:
    external_root = tmp_path / "external"
    external_root.mkdir()
    _publish(external_root, _draft("RET-L", run_id=RUN_ID_A))
    baseline = load_published_run_bundle(external_root, RUN_ID_A)
    candidate = _draft("RET-HR", run_id=RUN_ID_B)
    comparison = build_retrieval_comparison(baseline, candidate, CONTROLLED_VARIABLE_KEYS)
    _publish(tmp_path, replace(candidate, comparison=comparison))

    with pytest.raises(EvaluationValidationError) as caught:
        load_published_run_bundle(tmp_path, RUN_ID_B)

    assert caught.value.code is EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_loader_retains_validated_case_failure_and_comparison_records(tmp_path: Path) -> None:
    baseline = _loaded_baseline(tmp_path)
    draft = replace(_draft("RET-HR", run_id=RUN_ID_B), failures=(_failure(RUN_ID_B),))
    comparison = build_retrieval_comparison(baseline, draft, CONTROLLED_VARIABLE_KEYS)
    run_root = _publish(
        tmp_path,
        replace(
            draft,
            comparison=comparison,
        ),
    )

    loaded = load_published_run_bundle(tmp_path, RUN_ID_B)

    assert loaded.root == run_root
    assert {case.run_id for case in loaded.cases} == {RUN_ID_B}
    assert tuple(failure.run_id for failure in loaded.failures) == (RUN_ID_B,)
    assert loaded.comparison is not None
    assert loaded.comparison.run_id == RUN_ID_B
    assert loaded.comparison.candidate_run_id == RUN_ID_B
