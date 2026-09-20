"""Tests for protected release gate CLI subcommand."""

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes
from ai_worker.tasks.evaluation.cli import main
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode
from ai_worker.tasks.evaluation.manifest import build_content_manifest
from ai_worker.tasks.evaluation.release_gate import (
    build_release_gate,
)
from ai_worker.tasks.evaluation.schemas.artifacts import (
    DecisionStatus,
    ExecutionStatus,
    RagEvaluationRun,
)
from ai_worker.tests.evaluation.test_cli import _run_retrieval_cli
from ai_worker.tests.evaluation.test_release_gate_loader import _setup_approved_policy_graph

SUITE_ARG = "evals/suites/rag-retrieval-dev-v1.suite.json"
DATASET_ARG = "evals/retrieval/manifests/rag-retrieval-dev-v1.dataset.json"


def _setup_retrieval_bundle(tmp_path: Path) -> tuple[str, Path, Path, Path]:
    run_id = str(uuid4())
    exit_code = _run_retrieval_cli(
        tmp_path,
        "rag-retrieval-dev-ret-l-v1.execution.json",
        run_id,
    )
    assert exit_code == 0
    policy_path = Path("evals/policies/rag-retrieval-dev-v1.evaluation-policy.json")
    profile_path = Path("evals/profiles/rag-retrieval-dev-v1.profile.json")
    comparison_path = Path("evals/policies/rag-retrieval-dev-v1.comparison-policy.json")
    return run_id, policy_path, profile_path, comparison_path


def _setup_approved_cli_fixture(
    tmp_path: Path,
) -> tuple[str, Path, Path, Path, Path]:
    policy_dir = tmp_path / "policies"
    pol_path, prof_path, comp_path, suite_path = _setup_approved_policy_graph(policy_dir)

    run_id = str(uuid4())
    exit_code = _run_retrieval_cli(
        tmp_path,
        "rag-retrieval-dev-ret-l-v1.execution.json",
        run_id,
    )
    assert exit_code == 0

    pol = json.loads(pol_path.read_bytes())
    prof = json.loads(prof_path.read_bytes())
    comp = json.loads(comp_path.read_bytes())

    run_dir = tmp_path / run_id
    run_data = json.loads((run_dir / "run.json").read_bytes())
    run_data.update(
        {
            "experiment_type": "END_TO_END_RAG",
            "task_types": ["END_TO_END_RAG"],
            "runtime_eligible": True,
            "environment": "LOCAL",
            "execution_status": "COMPLETED",
            "decision_status": "PASS",
            "blocking_execution_statuses": [],
            "completed_at": "2026-09-16T14:47:00.000000Z",
            "candidate_bundle_id": "candidate-bundle-001",
            "candidate_bundle_manifest_hash": "a" * 64,
            "candidate_guard_decision_id": "guard-decision-001",
            "candidate_guard_decision": "PASS",
            "required_case_guard_coverage_manifest_hash": "b" * 64,
            "evaluation_policy_ref": {
                "id": pol["evaluation_policy_id"],
                "version": pol["evaluation_policy_version"],
                "hash": pol["evaluation_policy_hash"],
            },
            "evaluation_profile_ref": {
                "id": prof["evaluation_profile_id"],
                "version": prof["evaluation_profile_version"],
                "hash": prof["evaluation_profile_hash"],
            },
            "comparison_policy_ref": {
                "id": comp["comparison_policy_id"],
                "version": comp["comparison_policy_version"],
                "hash": comp["comparison_policy_hash"],
            },
        }
    )

    file_names = ("cases.jsonl", "metrics.json", "suite-results.json", "failures.jsonl", "report.md")
    files = {name: (run_dir / name).read_bytes() for name in file_names}
    manifest, content_bytes = build_content_manifest(run_id, files)
    run_data["result_content_manifest_hash"] = manifest.manifest_sha256

    validated_run = RagEvaluationRun.model_validate(run_data)
    (run_dir / "run.json").write_bytes(canonical_json_bytes(validated_run.model_dump(mode="json")))
    (run_dir / "result-content-manifest.json").write_bytes(content_bytes)

    return run_id, pol_path, prof_path, comp_path, suite_path


def test_gate_cli_invalid_on_diagnostic_dev_policy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_worker.tests.evaluation.test_release_gate import _evidence

    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)

    with patch("ai_worker.tasks.evaluation.cli.load_gate_evidence", return_value=_evidence()):
        exit_code = main(
            [
                "gate",
                "--run-id",
                run_id,
                "--policy",
                str(policy_path),
                "--profile",
                str(profile_path),
                "--comparison-policy",
                str(comparison_path),
                "--suite",
                str(suite_path),
            ],
            allowed_result_root=tmp_path,
        )

    captured = capsys.readouterr()
    assert exit_code == 2
    digest = captured.out.strip()
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
    assert "REQUIRED_SUITE_MISSING:rag-retrieval-dev-suite" in captured.err


def test_gate_cli_missing_suite_fails_closed_without_implicit_discovery(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id, policy_path, profile_path, comparison_path, _suite_path = _setup_approved_cli_fixture(tmp_path)

    # Omitting --suite must fail closed (no directory scanning)
    exit_code = main(
        [
            "gate",
            "--run-id",
            run_id,
            "--policy",
            str(policy_path),
            "--profile",
            str(profile_path),
            "--comparison-policy",
            str(comparison_path),
        ],
        allowed_result_root=tmp_path,
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert EvaluationErrorCode.RESOURCE_MISSING.value in captured.err


def test_gate_cli_pass_exit_code_0(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_worker.tests.evaluation.test_release_gate import _evidence, _policy

    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)
    pass_gate = build_release_gate(_policy(), _evidence())

    with (
        patch("ai_worker.tasks.evaluation.cli.load_gate_evidence", return_value=_evidence()),
        patch("ai_worker.tasks.evaluation.cli.build_release_gate", return_value=pass_gate),
    ):
        exit_code = main(
            [
                "gate",
                "--run-id",
                run_id,
                "--policy",
                str(policy_path),
                "--profile",
                str(profile_path),
                "--comparison-policy",
                str(comparison_path),
                "--suite",
                str(suite_path),
            ],
            allowed_result_root=tmp_path,
        )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(captured.out.strip()) == 64
    assert captured.err == ""


def test_gate_cli_fail_exit_code_1(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dataclasses import replace

    from ai_worker.tests.evaluation.test_release_gate import _evidence, _policy

    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)
    ev = _evidence()
    failed_receipt = replace(ev.receipts[0], decision_status=DecisionStatus.FAIL)
    fail_gate = build_release_gate(_policy(), replace(ev, receipts=(failed_receipt, ev.receipts[1])))

    with (
        patch("ai_worker.tasks.evaluation.cli.load_gate_evidence", return_value=_evidence()),
        patch("ai_worker.tasks.evaluation.cli.build_release_gate", return_value=fail_gate),
    ):
        exit_code = main(
            [
                "gate",
                "--run-id",
                run_id,
                "--policy",
                str(policy_path),
                "--profile",
                str(profile_path),
                "--comparison-policy",
                str(comparison_path),
                "--suite",
                str(suite_path),
            ],
            allowed_result_root=tmp_path,
        )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert len(captured.out.strip()) == 64


def test_gate_cli_completed_fail_with_required_metric_failed_returns_1(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ensure that COMPLETED + DecisionStatus.FAIL with REQUIRED_METRIC_FAILED returns exit code 1."""
    from ai_worker.tests.evaluation.test_release_gate import _evidence, _policy

    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)
    base_gate = build_release_gate(_policy(), _evidence())
    fail_gate = base_gate.model_copy(
        update={
            "aggregate_execution_status": ExecutionStatus.COMPLETED,
            "aggregate_decision_status": DecisionStatus.FAIL,
            "blocking_reason_codes": ("REQUIRED_METRIC_FAILED:context_precision",),
        }
    )

    with (
        patch("ai_worker.tasks.evaluation.cli.load_gate_evidence", return_value=_evidence()),
        patch("ai_worker.tasks.evaluation.cli.build_release_gate", return_value=fail_gate),
    ):
        exit_code = main(
            [
                "gate",
                "--run-id",
                run_id,
                "--policy",
                str(policy_path),
                "--profile",
                str(profile_path),
                "--comparison-policy",
                str(comparison_path),
                "--suite",
                str(suite_path),
            ],
            allowed_result_root=tmp_path,
        )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "REQUIRED_METRIC_FAILED:context_precision" in captured.err


def test_gate_cli_exit_2_on_missing_run_bundle(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy_dir = tmp_path / "policies"
    policy_path, profile_path, comparison_path, suite_path = _setup_approved_policy_graph(policy_dir)
    missing_run_id = str(uuid4())

    exit_code = main(
        [
            "gate",
            "--run-id",
            missing_run_id,
            "--policy",
            str(policy_path),
            "--profile",
            str(profile_path),
            "--comparison-policy",
            str(comparison_path),
            "--suite",
            str(suite_path),
        ],
        allowed_result_root=tmp_path,
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert EvaluationErrorCode.RESOURCE_MISSING.value in captured.err


def test_gate_cli_exit_2_on_invalid_arguments(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["gate", "--invalid-argument"], allowed_result_root=tmp_path)
    captured = capsys.readouterr()
    assert exit_code == 2
    assert EvaluationErrorCode.SCHEMA_INVALID.value in captured.err


def test_gate_cli_publishes_canonical_json_and_markdown(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dataclasses import replace

    from ai_worker.tests.evaluation.test_release_gate import _evidence

    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)
    output_dir = tmp_path / run_id
    ev = replace(_evidence(), run_id=run_id)

    with patch("ai_worker.tasks.evaluation.cli.load_gate_evidence", return_value=ev):
        exit_code = main(
            [
                "gate",
                "--run-id",
                run_id,
                "--policy",
                str(policy_path),
                "--profile",
                str(profile_path),
                "--comparison-policy",
                str(comparison_path),
                "--suite",
                str(suite_path),
                "--output-dir",
                str(output_dir),
            ],
            allowed_result_root=tmp_path,
        )

    assert exit_code == 2  # INVALID because diagnostic dev suite has required=false
    json_path = output_dir / "release-gate.json"
    md_path = output_dir / "release-gate.md"
    assert json_path.is_file()
    assert md_path.is_file()

    # Verify JSON content matches GateResult schema
    data = json.loads(json_path.read_bytes())
    assert data["schema_id"] == "rag-eval.gate"
    assert data["run_id"] == run_id
    assert "PROFILE_NOT_RUNTIME_ELIGIBLE" in data["blocking_reason_codes"]

    # Verify Markdown contains summary header
    md_content = md_path.read_text(encoding="utf-8")
    assert "# RAG Evaluation Release Gate" in md_content
    assert run_id in md_content


def test_gate_cli_exit_2_on_output_root_escape(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verifies that an output path escaping the approved result root is rejected."""
    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)
    invalid_dir = tmp_path / "arbitrary_location"

    exit_code = main(
        [
            "gate",
            "--run-id",
            run_id,
            "--policy",
            str(policy_path),
            "--profile",
            str(profile_path),
            "--comparison-policy",
            str(comparison_path),
            "--suite",
            str(suite_path),
            "--output-dir",
            str(invalid_dir),
        ],
        allowed_result_root=tmp_path,
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert EvaluationErrorCode.RESOURCE_PATH_INVALID.value in captured.err


def test_gate_cli_publication_rollback_on_partial_failure(tmp_path: Path) -> None:
    """Verify that JSON link success followed by Markdown link failure rolls back cleanly."""
    from ai_worker.tasks.evaluation import cli as cli_module
    from ai_worker.tests.evaluation.test_release_gate import _evidence

    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)
    target_dir = tmp_path / run_id

    original_link = cli_module._atomic_link

    def mock_link(directory_fd: int, temporary_name: str, destination_name: str) -> None:
        if destination_name == "release-gate.md":
            raise OSError("Injected disk error on Markdown link")
        original_link(directory_fd, temporary_name, destination_name)

    with (
        patch("ai_worker.tasks.evaluation.cli.load_gate_evidence", return_value=_evidence()),
        patch("ai_worker.tasks.evaluation.cli._atomic_link", side_effect=mock_link),
    ):
        exit_code = main(
            [
                "gate",
                "--run-id",
                run_id,
                "--policy",
                str(policy_path),
                "--profile",
                str(profile_path),
                "--comparison-policy",
                str(comparison_path),
                "--suite",
                str(suite_path),
            ],
            allowed_result_root=tmp_path,
        )

    assert exit_code in (2, 3)
    # The JSON artifact must NOT be left behind in target_dir!
    assert not (target_dir / "release-gate.json").exists()
    assert not (target_dir / "release-gate.md").exists()


def test_gate_cli_exit_2_on_destination_conflict(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)
    target_dir = tmp_path / run_id
    (target_dir / "release-gate.json").write_bytes(b"existing")

    exit_code = main(
        [
            "gate",
            "--run-id",
            run_id,
            "--policy",
            str(policy_path),
            "--profile",
            str(profile_path),
            "--comparison-policy",
            str(comparison_path),
            "--suite",
            str(suite_path),
        ],
        allowed_result_root=tmp_path,
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert EvaluationErrorCode.RESULT_PATH_CONFLICT.value in captured.err


def test_gate_cli_exit_3_on_unexpected_internal_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from ai_worker.tests.evaluation.test_release_gate import _evidence

    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)

    with (
        patch("ai_worker.tasks.evaluation.cli.load_gate_evidence", return_value=_evidence()),
        patch(
            "ai_worker.tasks.evaluation.cli.build_release_gate",
            side_effect=RuntimeError("Unexpected kernel crash"),
        ),
    ):
        exit_code = main(
            [
                "gate",
                "--run-id",
                run_id,
                "--policy",
                str(policy_path),
                "--profile",
                str(profile_path),
                "--comparison-policy",
                str(comparison_path),
                "--suite",
                str(suite_path),
            ],
            allowed_result_root=tmp_path,
        )

    captured = capsys.readouterr()
    assert exit_code == 3
    assert EvaluationErrorCode.INTERNAL_ERROR.value in captured.err


def test_gate_cli_privacy_no_sensitive_leak(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)

    exit_code = main(
        [
            "gate",
            "--run-id",
            run_id,
            "--policy",
            str(policy_path),
            "--profile",
            str(profile_path),
            "--comparison-policy",
            str(comparison_path),
            "--suite",
            str(suite_path),
        ],
        allowed_result_root=tmp_path,
    )
    assert exit_code == 2

    captured = capsys.readouterr()
    for channel in (captured.out, captured.err):
        assert "patient" not in channel.lower()
        assert "prescription" not in channel.lower()
        assert "diagnosis" not in channel.lower()
        assert "prompt" not in channel.lower()
        assert "credential" not in channel.lower()
        assert "api_key" not in channel.lower()


def test_gate_cli_rejects_paired_case_evidence_flag(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)

    exit_code = main(
        [
            "gate",
            "--run-id",
            run_id,
            "--policy",
            str(policy_path),
            "--profile",
            str(profile_path),
            "--comparison-policy",
            str(comparison_path),
            "--suite",
            str(suite_path),
            "--paired-case-evidence",
            "unsupported.json",
        ],
        allowed_result_root=tmp_path,
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert EvaluationErrorCode.SCHEMA_INVALID.value in captured.err


def test_gate_cli_rejects_unapproved_policy(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id, _pol, prof_path, comp_path, suite_path = _setup_approved_cli_fixture(tmp_path)
    unapproved_policy_path = Path("evals/policies/rag-retrieval-dev-v1.evaluation-policy.json")

    exit_code = main(
        [
            "gate",
            "--run-id",
            run_id,
            "--policy",
            str(unapproved_policy_path),
            "--profile",
            str(prof_path),
            "--comparison-policy",
            str(comp_path),
            "--suite",
            str(suite_path),
        ],
        allowed_result_root=tmp_path,
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert EvaluationErrorCode.REVIEW_PROVENANCE_INVALID.value in captured.err


def test_gate_cli_rejects_non_candidate_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A standard retrieval run has experiment_type == KNOWLEDGE_RETRIEVAL and runtime_eligible == False
    retrieval_run_id, _, _, _ = _setup_retrieval_bundle(tmp_path)
    _, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)

    exit_code = main(
        [
            "gate",
            "--run-id",
            retrieval_run_id,
            "--policy",
            str(policy_path),
            "--profile",
            str(profile_path),
            "--comparison-policy",
            str(comparison_path),
            "--suite",
            str(suite_path),
        ],
        allowed_result_root=tmp_path,
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert EvaluationErrorCode.BASELINE_ARTIFACT_INVALID.value in captured.err


def test_gate_cli_fail_closed_without_canonical_guard_authority(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify that an unmocked candidate run with PASS guard claims fails closed."""
    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)
    output_dir = tmp_path / run_id

    exit_code = main(
        [
            "gate",
            "--run-id",
            run_id,
            "--policy",
            str(policy_path),
            "--profile",
            str(profile_path),
            "--comparison-policy",
            str(comparison_path),
            "--suite",
            str(suite_path),
            "--output-dir",
            str(output_dir),
        ],
        allowed_result_root=tmp_path,
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert EvaluationErrorCode.STATE_COMBINATION_INVALID.value in captured.err
    assert not (output_dir / "release-gate.json").exists()
    assert not (output_dir / "release-gate.md").exists()


def test_gate_cli_rejects_draft_dataset_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id, policy_path, profile_path, comparison_path, suite_path = _setup_approved_cli_fixture(tmp_path)

    exit_code = main(
        [
            "gate",
            "--run-id",
            run_id,
            "--policy",
            str(policy_path),
            "--profile",
            str(profile_path),
            "--comparison-policy",
            str(comparison_path),
            "--suite",
            str(suite_path),
            "--dataset-manifest",
            DATASET_ARG,
        ],
        allowed_result_root=tmp_path,
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert EvaluationErrorCode.REVIEW_PROVENANCE_INVALID.value in captured.err
