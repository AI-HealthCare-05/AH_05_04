"""Tests for release gate actual artifact loader and assembler."""

import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from ai_worker.tasks.evaluation.comparison import load_published_run_bundle
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.manifest import build_artifact_draft, finalize_artifacts
from ai_worker.tasks.evaluation.publisher import publish_run_directory
from ai_worker.tasks.evaluation.release_gate import (
    ControlSettingEvidence,
    PairedCaseEvidence,
    build_release_gate,
    paired_case_manifest_hash,
)
from ai_worker.tasks.evaluation.release_gate_loader import (
    derive_partition_manifest_hash,
    derive_required_case_ids,
    load_dataset_manifest,
    load_gate_evidence,
    load_paired_case_evidence,
    load_receipt_evidence,
)
from ai_worker.tasks.evaluation.release_policy import load_release_policy
from ai_worker.tasks.evaluation.schemas.common import (
    DecisionStatus,
    ExperimentType,
    ImmutableReference,
    Partition,
)
from ai_worker.tests.evaluation.test_cli import _run_retrieval_cli
from ai_worker.tests.evaluation.test_result_manifest import retrieval_run_material

SUITE_PATH = Path("evals/suites/rag-retrieval-dev-v1.suite.json")
DATASET_PATH = Path("evals/retrieval/manifests/rag-retrieval-dev-v1.dataset.json")


def _setup_retrieval_run(tmp_path: Path) -> tuple[str, Path, Path, Path]:
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


def test_load_gate_evidence_assembles_valid_evidence(tmp_path: Path) -> None:
    run_id, policy_path, profile_path, comparison_path = _setup_retrieval_run(tmp_path)
    policy = load_release_policy(policy_path, profile_path, comparison_path)

    evidence = load_gate_evidence(
        result_root=tmp_path,
        run_id=run_id,
        policy=policy,
        suite_paths=[SUITE_PATH],
        dataset_manifest_path=DATASET_PATH,
    )

    assert evidence.run_id == run_id
    assert evidence.required_scope_manifest_hash == policy.required_scope_manifest_hash
    assert evidence.completed_experiment_types == (ExperimentType.KNOWLEDGE_RETRIEVAL,)
    assert evidence.completed_partitions == (Partition.DEV,)
    assert len(evidence.metrics) > 0
    assert len(evidence.suites) == 1
    assert evidence.suites[0].suite.suite_id == "rag-retrieval-dev-suite"
    assert evidence.receipts == ()
    assert evidence.paired_case_evidence is None

    gate = build_release_gate(policy, evidence)
    assert gate.run_id == run_id
    assert "PROFILE_NOT_RUNTIME_ELIGIBLE" in gate.blocking_reason_codes


def test_load_gate_evidence_suite_implicit_discovery_eliminated(tmp_path: Path) -> None:
    run_id, policy_path, profile_path, comparison_path = _setup_retrieval_run(tmp_path)
    policy = load_release_policy(policy_path, profile_path, comparison_path)

    # Missing explicit --suite path must fail closed without auto-discovery
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=policy,
            suite_paths=None,
        )
    assert exc_info.value.code == EvaluationErrorCode.RESOURCE_MISSING


def test_load_gate_evidence_policy_ref_mismatch_fails_closed(tmp_path: Path) -> None:
    run_id, policy_path, profile_path, comparison_path = _setup_retrieval_run(tmp_path)
    policy = load_release_policy(policy_path, profile_path, comparison_path)

    tampered_policy = replace(
        policy,
        evaluation_policy_ref=ImmutableReference(id="tampered-policy", version="1.0.0", hash="0" * 64),
    )

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=tampered_policy,
            suite_paths=[SUITE_PATH],
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_load_gate_evidence_profile_ref_mismatch_fails_closed(tmp_path: Path) -> None:
    run_id, policy_path, profile_path, comparison_path = _setup_retrieval_run(tmp_path)
    policy = load_release_policy(policy_path, profile_path, comparison_path)

    tampered_policy = replace(
        policy,
        evaluation_profile_ref=ImmutableReference(id="tampered-profile", version="1.0.0", hash="0" * 64),
    )

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=tampered_policy,
            suite_paths=[SUITE_PATH],
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_load_gate_evidence_comparison_ref_mismatch_fails_closed(tmp_path: Path) -> None:
    run_id, policy_path, profile_path, comparison_path = _setup_retrieval_run(tmp_path)
    policy = load_release_policy(policy_path, profile_path, comparison_path)

    tampered_policy = replace(
        policy,
        comparison_policy_ref=ImmutableReference(id="tampered-comparison", version="1.0.0", hash="0" * 64),
    )

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=tampered_policy,
            suite_paths=[SUITE_PATH],
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_load_gate_evidence_missing_bundle_fails_closed(tmp_path: Path) -> None:
    _, policy_path, profile_path, comparison_path = _setup_retrieval_run(tmp_path)
    policy = load_release_policy(policy_path, profile_path, comparison_path)

    missing_run_id = str(uuid4())
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=missing_run_id,
            policy=policy,
            suite_paths=[SUITE_PATH],
        )
    assert exc_info.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_load_gate_evidence_tampered_metrics_fails_closed(tmp_path: Path) -> None:
    run_id, policy_path, profile_path, comparison_path = _setup_retrieval_run(tmp_path)
    policy = load_release_policy(policy_path, profile_path, comparison_path)

    # Tamper with metrics.json in the published bundle
    metrics_path = tmp_path / run_id / "metrics.json"
    metrics_path.write_bytes(b'{"corrupted": true}')

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=policy,
            suite_paths=[SUITE_PATH],
        )
    assert exc_info.value.code in (
        EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
        EvaluationErrorCode.HASH_MISMATCH,
    )


def test_load_gate_evidence_tampered_suite_results_fails_closed(tmp_path: Path) -> None:
    run_id, policy_path, profile_path, comparison_path = _setup_retrieval_run(tmp_path)
    policy = load_release_policy(policy_path, profile_path, comparison_path)

    # Tamper with suite-results.json in the published bundle
    suite_path = tmp_path / run_id / "suite-results.json"
    suite_path.write_bytes(b'{"corrupted": true}')

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=policy,
            suite_paths=[SUITE_PATH],
        )
    assert exc_info.value.code in (
        EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
        EvaluationErrorCode.HASH_MISMATCH,
    )


def test_load_gate_evidence_dataset_manifest_mismatch_fails_closed(tmp_path: Path) -> None:
    run_id, policy_path, profile_path, comparison_path = _setup_retrieval_run(tmp_path)
    policy = load_release_policy(policy_path, profile_path, comparison_path)

    # Use a different dataset manifest
    wrong_dataset = Path("evals/retrieval/manifests/dev-foundation-v1.dataset.json")
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=policy,
            suite_paths=[SUITE_PATH],
            dataset_manifest_path=wrong_dataset,
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_real_published_bundle_end_to_end_compatibility(tmp_path: Path) -> None:
    """Verify that a real PublishedArtifacts pipeline bundle loads seamlessly into GateEvidence."""
    run_id = str(uuid4())
    material = retrieval_run_material("RET-L", run_id=run_id)
    draft = build_artifact_draft(material)
    artifacts = finalize_artifacts(draft, b"# Safe Evaluation Report\n", completed_at="2026-09-13T00:00:00.000000Z")
    publish_run_directory(allowed_root=tmp_path, run_id=run_id, files=artifacts.files)

    policy_path = Path("evals/policies/rag-retrieval-dev-v1.evaluation-policy.json")
    profile_path = Path("evals/profiles/rag-retrieval-dev-v1.profile.json")
    comparison_path = Path("evals/policies/rag-retrieval-dev-v1.comparison-policy.json")

    manifest = load_dataset_manifest(DATASET_PATH)
    derived_case_ids = derive_required_case_ids(manifest, (Partition.DEV,))
    assert len(derived_case_ids) > 0

    derived_part_hash = derive_partition_manifest_hash(manifest, Partition.DEV)
    assert len(derived_part_hash) == 64

    policy = load_release_policy(
        policy_path,
        profile_path,
        comparison_path,
        required_case_ids=derived_case_ids,
    )

    evidence = load_gate_evidence(
        result_root=tmp_path,
        run_id=run_id,
        policy=policy,
        suite_paths=[SUITE_PATH],
        dataset_manifest_path=DATASET_PATH,
    )

    assert evidence.run_id == run_id
    assert evidence.required_scope_manifest_hash == policy.required_scope_manifest_hash
    assert evidence.completed_experiment_types == (ExperimentType.KNOWLEDGE_RETRIEVAL,)
    assert evidence.completed_partitions == (Partition.DEV,)
    assert len(evidence.metrics) > 0
    assert len(evidence.suites) == 1
    assert evidence.suites[0].suite.suite_id == "rag-retrieval-dev-suite"


def test_all_variants_same_required_case_missing_prevents_pass() -> None:
    """When all variants miss a required case, the gate must detect it and refuse PASS."""
    from ai_worker.tests.evaluation.test_release_gate import _evidence, _paired, _policy

    manifest = load_dataset_manifest(DATASET_PATH)
    authoritative_cases = derive_required_case_ids(manifest, (Partition.DEV,))
    assert "rag-ret-dev-001" in authoritative_cases

    # Incomplete paired evidence: ALL variants (baseline, candidate, final) miss rag-ret-dev-001
    incomplete_cases = tuple(c for c in authoritative_cases if c != "rag-ret-dev-001")
    draft_paired = replace(
        _paired(),
        baseline_case_ids=incomplete_cases,
        candidate_case_ids=incomplete_cases,
        final_case_ids=incomplete_cases,
    )
    incomplete_paired = replace(draft_paired, receipt_hash=paired_case_manifest_hash(draft_paired))

    # Policy requires the authoritative case set from the dataset manifest
    base_policy = _policy()
    paired_receipt_ref = ImmutableReference(
        id="ans-base-to-ans-final-comparison",
        version="1.0.0",
        hash=incomplete_paired.receipt_hash,
    )
    policy = replace(
        base_policy,
        required_case_ids=authoritative_cases,
        required_receipts=(base_policy.required_receipts[0], paired_receipt_ref),
    )

    # Even though baseline == candidate == final (all variants consistent), required case is missing!
    base_ev = _evidence()
    updated_receipt = replace(
        base_ev.receipts[1],
        reference=paired_receipt_ref,
        artifact_ref=paired_receipt_ref,
    )
    evidence = replace(
        base_ev,
        receipts=(base_ev.receipts[0], updated_receipt),
        paired_case_evidence=incomplete_paired,
    )
    gate = build_release_gate(policy, evidence)

    assert gate.aggregate_decision_status is not DecisionStatus.PASS
    assert "PAIRED_CASE_SET_MISMATCH" in gate.blocking_reason_codes


def test_load_receipt_evidence_protected_artifact_receipt_fails_closed() -> None:
    receipt_path = Path("evals/provenance/rag-retrieval-dev-v1.protected-artifact-receipt.json")
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_receipt_evidence(receipt_path)
    assert exc_info.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_load_receipt_evidence_validation_receipt_fails_closed(tmp_path: Path) -> None:
    validation_receipt_path = tmp_path / "test.validation-receipt.json"
    validation_receipt_path.write_text(
        json.dumps(
            {
                "schema_id": "rag-eval.validation-receipt",
                "schema_version": "1.0.0",
                "validation_id": "test-validation-receipt",
                "validated_at": "2026-09-04T00:02:00.000000Z",
                "validator_version": "1.0.0",
                "manifest_path": "evals/manifests/test.dataset.json",
                "dataset_code": "test-dataset",
                "dataset_version": "1.0.0",
                "dataset_manifest_sha256": None,
                "evaluation_profile_ref": None,
                "comparison_policy_ref": None,
                "execution_status": "COMPLETED",
                "decision_status": "NOT_APPLICABLE",
                "release_eligible": False,
                "error_codes": [],
                "invalid_resource_paths": [],
            }
        )
    )
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_receipt_evidence(validation_receipt_path)
    assert exc_info.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_load_receipt_evidence_unsupported_schema_fails_closed(tmp_path: Path) -> None:
    unsupported_path = tmp_path / "unsupported-receipt.json"
    unsupported_path.write_text(
        json.dumps(
            {
                "schema_id": "rag-eval.unsupported-diagnostic-receipt",
                "receipt_id": "test-unsupported",
            }
        )
    )
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_receipt_evidence(unsupported_path)
    assert exc_info.value.code == EvaluationErrorCode.SCHEMA_INVALID


def test_load_receipt_evidence_missing_currentness_authority_fails_closed(tmp_path: Path) -> None:
    no_currentness = tmp_path / "no-currentness.json"
    no_currentness.write_text(
        json.dumps(
            {
                "reference": {"id": "rec-1", "version": "1.0.0", "hash": "a" * 64},
                "execution_status": "COMPLETED",
                "decision_status": "PASS",
            }
        )
    )
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_receipt_evidence(no_currentness)
    assert exc_info.value.code == EvaluationErrorCode.SCHEMA_INVALID


def test_load_receipt_evidence_non_pass_fail_inconclusive_decision_fails_closed(tmp_path: Path) -> None:
    na_decision = tmp_path / "na-decision.json"
    na_decision.write_text(
        json.dumps(
            {
                "reference": {"id": "rec-1", "version": "1.0.0", "hash": "a" * 64},
                "execution_status": "COMPLETED",
                "decision_status": "NOT_APPLICABLE",
                "is_current": True,
            }
        )
    )
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_receipt_evidence(na_decision)
    assert exc_info.value.code == EvaluationErrorCode.SCHEMA_INVALID


def test_shape_compatible_but_unregistered_release_receipt_fails_closed(tmp_path: Path) -> None:
    receipt_path = tmp_path / "synthetic-release-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_id": "synthetic-release-receipt",
                "schema_version": "1.0.0",
                "reference": {"id": "rec-1", "version": "1.0.0", "hash": "a" * 64},
                "artifact_ref": {"id": "rec-1", "version": "1.0.0", "hash": "a" * 64},
                "execution_status": "COMPLETED",
                "decision_status": "PASS",
                "is_current": True,
            }
        )
    )
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_receipt_evidence(receipt_path)
    assert exc_info.value.code == EvaluationErrorCode.SCHEMA_INVALID


def test_derive_required_case_ids_filters_by_required_partitions_excluding_dev() -> None:
    manifest = load_dataset_manifest(DATASET_PATH)
    dev_cases = derive_required_case_ids(manifest, (Partition.DEV,))
    assert "rag-ret-dev-001" in dev_cases

    holdout_safety_cases = derive_required_case_ids(manifest, (Partition.HOLDOUT, Partition.SAFETY_REGRESSION))
    assert "rag-ret-dev-001" not in holdout_safety_cases


def test_derive_required_case_ids_duplicate_case_id_fails_closed() -> None:
    manifest = load_dataset_manifest(DATASET_PATH)
    resource_0 = manifest.case_resources[0]
    duplicate_resource = resource_0.model_copy(update={"partition": Partition.HOLDOUT})
    duplicated_resources = list(manifest.case_resources) + [duplicate_resource]
    mock_manifest = manifest.model_copy(update={"case_resources": tuple(duplicated_resources)})

    with pytest.raises(EvaluationValidationError) as exc_info:
        derive_required_case_ids(mock_manifest, (Partition.DEV, Partition.HOLDOUT))
    assert exc_info.value.code == EvaluationErrorCode.CASE_DUPLICATE


def test_multi_partition_all_variants_missing_case_prevents_pass() -> None:
    from ai_worker.tests.evaluation.test_release_gate import _evidence, _policy

    required_cases = ("case-A", "case-B", "case-C", "case-D")
    observed_cases = ("case-A", "case-B", "case-C")

    control_setting = ControlSettingEvidence(
        variable_key="chunking_strategy",
        baseline_hash="a" * 64,
        candidate_hash="b" * 64,
        final_hash="c" * 64,
    )
    draft_paired = PairedCaseEvidence(
        receipt_id="ans-base-to-ans-final-comparison",
        receipt_hash="",
        baseline_case_ids=observed_cases,
        candidate_case_ids=observed_cases,
        final_case_ids=observed_cases,
        control_settings=(control_setting,),
        paired_delta_refs=(),
    )
    paired_evidence = replace(draft_paired, receipt_hash=paired_case_manifest_hash(draft_paired))

    paired_receipt_ref = ImmutableReference(
        id="ans-base-to-ans-final-comparison",
        version="1.0.0",
        hash=paired_evidence.receipt_hash,
    )
    base_policy = _policy()
    policy = replace(
        base_policy,
        required_partitions=(Partition.HOLDOUT, Partition.SAFETY_REGRESSION),
        required_case_ids=required_cases,
        required_receipts=(base_policy.required_receipts[0], paired_receipt_ref),
    )

    base_ev = _evidence()
    updated_receipt = replace(
        base_ev.receipts[1],
        reference=paired_receipt_ref,
        artifact_ref=paired_receipt_ref,
    )
    evidence = replace(
        base_ev,
        completed_partitions=(Partition.HOLDOUT, Partition.SAFETY_REGRESSION),
        receipts=(base_ev.receipts[0], updated_receipt),
        paired_case_evidence=paired_evidence,
    )

    gate = build_release_gate(policy, evidence)
    assert gate.aggregate_decision_status is not DecisionStatus.PASS
    assert "PAIRED_CASE_SET_MISMATCH" in gate.blocking_reason_codes


def test_multi_partition_run_fails_closed_pending_upstream_contract(tmp_path: Path) -> None:
    from ai_worker.tasks.evaluation.release_gate_loader import _validate_run_dataset_and_partitions

    run_id, _policy_path, _profile_path, _comparison_path = _setup_retrieval_run(tmp_path)
    bundle = load_published_run_bundle(tmp_path, run_id)
    multi_part_run = bundle.run.model_copy(update={"evaluated_partitions": ("HOLDOUT", "SAFETY_REGRESSION")})

    with pytest.raises(EvaluationValidationError) as exc_info:
        _validate_run_dataset_and_partitions(multi_part_run, DATASET_PATH)
    assert exc_info.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_load_paired_case_evidence_valid_and_tampered(tmp_path: Path) -> None:
    from ai_worker.tasks.evaluation.canonical import canonical_json_bytes

    control_setting = ControlSettingEvidence(
        variable_key="chunking_strategy",
        baseline_hash="a" * 64,
        candidate_hash="b" * 64,
        final_hash="c" * 64,
    )
    draft = PairedCaseEvidence(
        receipt_id="test-paired-receipt",
        receipt_hash="",
        baseline_case_ids=("case-1", "case-2"),
        candidate_case_ids=("case-1", "case-2"),
        final_case_ids=("case-1", "case-2"),
        control_settings=(control_setting,),
        paired_delta_refs=(),
    )
    digest = paired_case_manifest_hash(draft)
    valid_evidence = replace(draft, receipt_hash=digest)

    evidence_file = tmp_path / "paired-case.json"
    evidence_file.write_bytes(
        canonical_json_bytes(
            {
                "receipt_id": valid_evidence.receipt_id,
                "receipt_hash": valid_evidence.receipt_hash,
                "baseline_case_ids": list(valid_evidence.baseline_case_ids),
                "candidate_case_ids": list(valid_evidence.candidate_case_ids),
                "final_case_ids": list(valid_evidence.final_case_ids),
                "control_settings": [
                    {
                        "variable_key": "chunking_strategy",
                        "baseline_hash": "a" * 64,
                        "candidate_hash": "b" * 64,
                        "final_hash": "c" * 64,
                    }
                ],
                "paired_delta_refs": [],
            }
        )
    )

    loaded = load_paired_case_evidence(evidence_file)
    assert loaded.receipt_id == "test-paired-receipt"
    assert loaded.receipt_hash == digest

    # Tamper with the file
    evidence_file.write_bytes(
        canonical_json_bytes(
            {
                "receipt_id": valid_evidence.receipt_id,
                "receipt_hash": "0" * 64,
                "baseline_case_ids": ["case-1"],
                "candidate_case_ids": ["case-1"],
                "final_case_ids": ["case-1"],
                "control_settings": [],
                "paired_delta_refs": [],
            }
        )
    )
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_paired_case_evidence(evidence_file)
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH
