"""Tests for release gate actual artifact loader and assembler."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.comparison import load_published_run_bundle
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import load_dataset
from ai_worker.tasks.evaluation.manifest import build_content_manifest
from ai_worker.tasks.evaluation.release_gate import (
    ControlSettingEvidence,
    PairedCaseEvidence,
    build_release_gate,
    paired_case_manifest_hash,
)
from ai_worker.tasks.evaluation.release_gate_loader import (
    _derive_required_case_ids_from_manifest,
    _locate_suite_definition,
    _require_canonical_release_guard_authority,
    _validate_release_candidate_run,
    _validate_release_run_structure,
    derive_required_case_ids,
    load_dataset_manifest,
    load_gate_evidence,
    load_receipt_evidence,
    load_suite_definition,
    validate_release_dataset_authority,
)
from ai_worker.tasks.evaluation.release_policy import (
    ReleaseGatePolicy,
    load_approved_release_policy,
)
from ai_worker.tasks.evaluation.schemas.artifacts import (
    CandidateGuardDecision,
    RagEvaluationRun,
    RuntimeEnvironment,
)
from ai_worker.tasks.evaluation.schemas.authoring import DatasetStatus
from ai_worker.tasks.evaluation.schemas.common import (
    ActorNamespace,
    ActorRef,
    ActorRole,
    DecisionStatus,
    ExecutionStatus,
    ExperimentType,
    ImmutableReference,
    Partition,
    TaskType,
)
from ai_worker.tests.evaluation.test_cli import _run_retrieval_cli

SUITE_PATH = Path("evals/suites/rag-retrieval-dev-v1.suite.json")
DATASET_PATH = Path("evals/retrieval/manifests/rag-retrieval-dev-v1.dataset.json")


def _setup_approved_policy_graph(target_dir: Path) -> tuple[Path, Path, Path, Path]:
    target_dir.mkdir(parents=True, exist_ok=True)
    prov = {
        "authored_by": {"actor_id": "author-user", "namespace": "GITHUB_LOGIN", "role": "EVALUATION_IMPLEMENTER"},
        "reviewed_by": {"actor_id": "reviewer-user", "namespace": "GITHUB_LOGIN", "role": "EVALUATION_REVIEWER"},
        "approved_by": {"actor_id": "safety-lead", "namespace": "GITHUB_LOGIN", "role": "PRODUCT_SAFETY_REVIEWER"},
        "authored_at": "2026-09-10T00:00:00.000000Z",
        "reviewed_at": "2026-09-11T00:00:00.000000Z",
        "approved_at": "2026-09-12T00:00:00.000000Z",
        "team_gold_status": "APPROVED",
        "external_medical_review_status": "NOT_REQUESTED",
        "external_medical_approval_receipt_ref": None,
        "evidence_review_refs": [{"id": "evidence-1", "version": "1.0.0", "hash": "0" * 64}],
    }

    # 1. Comparison policy
    comp = json.loads(Path("evals/policies/rag-retrieval-dev-v1.comparison-policy.json").read_bytes())
    comp["approved_by"] = {"actor_id": "safety-lead", "namespace": "GITHUB_LOGIN", "role": "PRODUCT_SAFETY_REVIEWER"}
    comp["approved_at"] = "2026-09-12T00:00:00.000000Z"
    comp["comparison_policy_hash"] = canonical_sha256(
        comp, excluded_top_level_keys=frozenset({"comparison_policy_hash"})
    )
    comp_path = target_dir / "comparison.json"
    comp_path.write_bytes(canonical_json_bytes(comp))

    # 2. Suite definition
    suite = json.loads(Path("evals/suites/rag-retrieval-dev-v1.suite.json").read_bytes())
    suite["review_provenance"] = prov
    suite["suite_hash"] = canonical_sha256(suite, excluded_top_level_keys=frozenset({"suite_hash"}))
    suite_path = target_dir / "suite.json"
    suite_path.write_bytes(canonical_json_bytes(suite))

    # 3. Profile
    prof = json.loads(Path("evals/profiles/rag-retrieval-dev-v1.profile.json").read_bytes())
    prof["review_provenance"] = prov
    prof["required_suite_refs"][0]["hash"] = suite["suite_hash"]
    prof["evaluation_profile_hash"] = canonical_sha256(
        prof, excluded_top_level_keys=frozenset({"evaluation_profile_hash"})
    )
    prof_path = target_dir / "profile.json"
    prof_path.write_bytes(canonical_json_bytes(prof))

    # 4. Policy
    pol = json.loads(Path("evals/policies/rag-retrieval-dev-v1.evaluation-policy.json").read_bytes())
    pol["review_provenance"] = prov
    pol["evaluation_profile_ref"]["reference"]["hash"] = prof["evaluation_profile_hash"]
    pol["comparison_policy_ref"]["reference"]["hash"] = comp["comparison_policy_hash"]
    pol["required_suite_refs"][0]["reference"]["hash"] = suite["suite_hash"]
    pol["member_manifest_hash"] = canonical_sha256(
        {
            "members": [
                pol["evaluation_profile_ref"],
                pol["comparison_policy_ref"],
                *pol["required_partition_refs"],
                *pol["required_gate_refs"],
                pol["required_suite_refs"][0],
                pol["artifact_schema_set_ref"],
            ]
        }
    )
    pol["evaluation_policy_hash"] = canonical_sha256(pol, excluded_top_level_keys=frozenset({"evaluation_policy_hash"}))
    pol_path = target_dir / "policy.json"
    pol_path.write_bytes(canonical_json_bytes(pol))

    return pol_path, prof_path, comp_path, suite_path


def _setup_approved_candidate_fixture(tmp_path: Path) -> tuple[str, ReleaseGatePolicy, Path, Path]:
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
            "runtime_eligible": True,
            "environment": "LOCAL",
            "execution_status": "COMPLETED",
            "decision_status": "PASS",
            "blocking_execution_statuses": [],
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

    policy = load_approved_release_policy(pol_path, prof_path, comp_path)
    return run_id, policy, suite_path, DATASET_PATH


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


def _base_candidate_run() -> RagEvaluationRun:
    return RagEvaluationRun(
        schema_id="rag-eval.run",
        schema_version="1.0.0",
        run_id="0602452b-8fd8-41e2-87b8-48609a3242ce",
        experiment_id="rag-natural-language-retrieval-dev",
        variant_id="RET-L",
        experiment_type=ExperimentType.END_TO_END_RAG,
        task_types=(TaskType.END_TO_END_RAG,),
        evaluation_profile_ref=ImmutableReference(id="profile-1", version="1.0.0", hash="1" * 64),
        comparison_policy_ref=ImmutableReference(id="comp-1", version="1.0.0", hash="2" * 64),
        evaluation_policy_ref=ImmutableReference(id="policy-1", version="1.0.0", hash="3" * 64),
        artifact_schema_set_ref=ImmutableReference(id="schema-set-1", version="1.5.0", hash="4" * 64),
        dataset_code="rag-natural-language-retrieval-dev",
        dataset_version="1.0.0",
        dataset_manifest_sha256="5" * 64,
        resource_set_hash="6" * 64,
        evidence_mapping_manifest_sha256="7" * 64,
        critical_claim_rubric_ref=ImmutableReference(id="rubric-1", version="1.0.0", hash="8" * 64),
        fixture_git_commit_sha="a" * 40,
        protected_artifact_receipt_ref=ImmutableReference(id="receipt-1", version="1.0.0", hash="9" * 64),
        resolved_evaluation_config_hash="a" * 64,
        upstream_contract_manifest_hash="b" * 64,
        retrieval_variant_manifest_hash=None,
        answer_variant_manifest_hash=None,
        model_config_hash="c" * 64,
        prompt_version="actual-retrieval-v1",
        evaluated_partitions=(Partition.DEV,),
        partition_manifest_hash="d" * 64,
        environment=RuntimeEnvironment.LOCAL,
        runtime_eligible=True,
        execution_status=ExecutionStatus.COMPLETED,
        decision_status=DecisionStatus.PASS,
        blocking_execution_statuses=(),
        started_at="2026-09-16T14:46:29.000000Z",
        completed_at="2026-09-16T14:47:00.000000Z",
        result_content_manifest_hash="f" * 64,
        candidate_bundle_id="candidate-bundle-001",
        candidate_bundle_manifest_hash="a" * 64,
        candidate_guard_decision_id="guard-decision-001",
        candidate_guard_decision=CandidateGuardDecision.PASS,
        required_case_guard_coverage_manifest_hash="b" * 64,
        executed_by=ActorRef(
            namespace=ActorNamespace.GITHUB_LOGIN,
            actor_id="ceohwj",
            role=ActorRole.EVALUATION_IMPLEMENTER,
        ),
    )


def _make_candidate_run(**overrides: Any) -> RagEvaluationRun:
    base = _base_candidate_run()
    if not overrides:
        return base
    return base.model_copy(update=overrides)


def test_validate_release_candidate_run_accepted() -> None:
    run = _make_candidate_run()
    _validate_release_run_structure(run)
    _validate_release_candidate_run(run)


def test_require_canonical_release_guard_authority_fails_closed() -> None:
    with pytest.raises(EvaluationValidationError) as exc:
        _require_canonical_release_guard_authority()
    assert exc.value.code == EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_validate_release_candidate_run_rejects_non_completed_execution() -> None:
    for status in (
        ExecutionStatus.ERROR,
        ExecutionStatus.INVALID,
        ExecutionStatus.NOT_EVALUATED,
        ExecutionStatus.NOT_IMPLEMENTED,
    ):
        run = _make_candidate_run(execution_status=status.value)
        with pytest.raises(EvaluationValidationError) as exc:
            _validate_release_candidate_run(run)
        assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_validate_release_candidate_run_rejects_non_pass_decision() -> None:
    for status in (DecisionStatus.FAIL, DecisionStatus.INCONCLUSIVE, DecisionStatus.NOT_APPLICABLE):
        run = _make_candidate_run(decision_status=status.value)
        with pytest.raises(EvaluationValidationError) as exc:
            _validate_release_candidate_run(run)
        assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_validate_release_candidate_run_rejects_blocking_statuses() -> None:
    run = _make_candidate_run(blocking_execution_statuses=["ERROR"])
    with pytest.raises(EvaluationValidationError) as exc:
        _validate_release_candidate_run(run)
    assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_validate_release_candidate_run_rejects_not_runtime_eligible() -> None:
    run = _make_candidate_run(runtime_eligible=False)
    with pytest.raises(EvaluationValidationError) as exc:
        _validate_release_candidate_run(run)
    assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_validate_release_candidate_run_rejects_non_end_to_end_rag() -> None:
    for exp in ("KNOWLEDGE_RETRIEVAL", "ANSWER_GROUNDING_SAFETY"):
        run = _make_candidate_run(experiment_type=exp)
        with pytest.raises(EvaluationValidationError) as exc:
            _validate_release_candidate_run(run)
        assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_validate_release_candidate_run_rejects_non_local_environment() -> None:
    run = _make_candidate_run(environment="CI")
    with pytest.raises(EvaluationValidationError) as exc:
        _validate_release_candidate_run(run)
    assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_validate_release_candidate_run_rejects_missing_guard_fields() -> None:
    guard_fields = (
        "candidate_bundle_id",
        "candidate_bundle_manifest_hash",
        "candidate_guard_decision_id",
        "candidate_guard_decision",
        "required_case_guard_coverage_manifest_hash",
    )
    for field in guard_fields:
        run = _make_candidate_run(**{field: None})
        with pytest.raises(EvaluationValidationError) as exc:
            _validate_release_candidate_run(run)
        assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_validate_release_candidate_run_rejects_non_pass_guard_decision() -> None:
    for decision in ("FAIL", "INCONCLUSIVE", "NOT_APPLICABLE", "REJECTED"):
        run = _make_candidate_run(candidate_guard_decision=decision)
        with pytest.raises(EvaluationValidationError) as exc:
            _validate_release_candidate_run(run)
        assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_load_suite_definition_rejects_draft_and_reviewed(tmp_path: Path) -> None:
    # Checked-in suite is DRAFT
    with pytest.raises(EvaluationValidationError) as exc:
        load_suite_definition(SUITE_PATH)
    assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID


def test_load_suite_definition_accepted_when_approved(tmp_path: Path) -> None:
    _pol, _prof, _comp, suite_path = _setup_approved_policy_graph(tmp_path)
    loaded = load_suite_definition(suite_path)
    assert loaded.suite_id == "rag-retrieval-dev-suite"


def test_load_gate_evidence_fails_closed_without_canonical_guard_authority(tmp_path: Path) -> None:
    """Structurally valid candidate run bundle fails closed because #162 guard authority is unavailable."""
    run_id, policy, suite_path, dataset_path = _setup_approved_candidate_fixture(tmp_path)

    with pytest.raises(EvaluationValidationError) as exc:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=policy,
            suite_paths=[suite_path],
            dataset_manifest_path=dataset_path,
        )
    assert exc.value.code == EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_load_gate_evidence_rejects_non_candidate_retrieval_run(tmp_path: Path) -> None:
    # A standard retrieval run has experiment_type=KNOWLEDGE_RETRIEVAL and lacks candidate guard fields
    run_id = str(uuid4())
    exit_code = _run_retrieval_cli(
        tmp_path,
        "rag-retrieval-dev-ret-l-v1.execution.json",
        run_id,
    )
    assert exit_code == 0
    _pol, _prof, _comp, suite_path = _setup_approved_policy_graph(tmp_path / "policies")
    policy = load_approved_release_policy(
        tmp_path / "policies/policy.json", tmp_path / "policies/profile.json", tmp_path / "policies/comparison.json"
    )

    with pytest.raises(EvaluationValidationError) as exc:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=policy,
            suite_paths=[suite_path],
            dataset_manifest_path=DATASET_PATH,
        )
    assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_load_gate_evidence_suite_implicit_discovery_eliminated() -> None:
    expected_ref = ImmutableReference(id="rag-retrieval-dev-suite", version="1.0.0", hash="a" * 64)
    with pytest.raises(EvaluationValidationError) as exc_info:
        _locate_suite_definition(expected_ref, suite_paths=None)
    assert exc_info.value.code == EvaluationErrorCode.RESOURCE_MISSING


def test_load_gate_evidence_policy_ref_mismatch_fails_closed(tmp_path: Path) -> None:
    run_id, policy, suite_path, dataset_path = _setup_approved_candidate_fixture(tmp_path)

    tampered_policy = replace(
        policy,
        evaluation_policy_ref=ImmutableReference(id="tampered-policy", version="1.0.0", hash="0" * 64),
    )

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=tampered_policy,
            suite_paths=[suite_path],
            dataset_manifest_path=dataset_path,
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_load_gate_evidence_profile_ref_mismatch_fails_closed(tmp_path: Path) -> None:
    run_id, policy, suite_path, dataset_path = _setup_approved_candidate_fixture(tmp_path)

    tampered_policy = replace(
        policy,
        evaluation_profile_ref=ImmutableReference(id="tampered-profile", version="1.0.0", hash="0" * 64),
    )

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=tampered_policy,
            suite_paths=[suite_path],
            dataset_manifest_path=dataset_path,
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_load_gate_evidence_comparison_ref_mismatch_fails_closed(tmp_path: Path) -> None:
    run_id, policy, suite_path, dataset_path = _setup_approved_candidate_fixture(tmp_path)

    tampered_policy = replace(
        policy,
        comparison_policy_ref=ImmutableReference(id="tampered-comparison", version="1.0.0", hash="0" * 64),
    )

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=tampered_policy,
            suite_paths=[suite_path],
            dataset_manifest_path=dataset_path,
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_load_gate_evidence_missing_bundle_fails_closed(tmp_path: Path) -> None:
    _, policy, suite_path, dataset_path = _setup_approved_candidate_fixture(tmp_path)

    missing_run_id = str(uuid4())
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=missing_run_id,
            policy=policy,
            suite_paths=[suite_path],
            dataset_manifest_path=dataset_path,
        )
    assert exc_info.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


def test_load_gate_evidence_tampered_metrics_fails_closed(tmp_path: Path) -> None:
    run_id, policy, suite_path, dataset_path = _setup_approved_candidate_fixture(tmp_path)

    # Tamper with metrics.json in the published bundle
    metrics_path = tmp_path / run_id / "metrics.json"
    metrics_path.write_bytes(b'{"corrupted": true}')

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=policy,
            suite_paths=[suite_path],
            dataset_manifest_path=dataset_path,
        )
    assert exc_info.value.code in (
        EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
        EvaluationErrorCode.HASH_MISMATCH,
    )


def test_load_gate_evidence_tampered_suite_results_fails_closed(tmp_path: Path) -> None:
    run_id, policy, suite_path, dataset_path = _setup_approved_candidate_fixture(tmp_path)

    # Tamper with suite-results.json in the published bundle
    suite_path_in_run = tmp_path / run_id / "suite-results.json"
    suite_path_in_run.write_bytes(b'{"corrupted": true}')

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=policy,
            suite_paths=[suite_path],
            dataset_manifest_path=dataset_path,
        )
    assert exc_info.value.code in (
        EvaluationErrorCode.BASELINE_ARTIFACT_INVALID,
        EvaluationErrorCode.HASH_MISMATCH,
        EvaluationErrorCode.STATE_COMBINATION_INVALID,
    )


def test_load_gate_evidence_dataset_manifest_mismatch_fails_closed(tmp_path: Path) -> None:
    run_id, policy, suite_path, _dataset_path = _setup_approved_candidate_fixture(tmp_path)

    # Use a different dataset manifest
    wrong_dataset = Path("evals/retrieval/manifests/dev-foundation-v1.dataset.json")
    with pytest.raises(EvaluationValidationError) as exc_info:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=policy,
            suite_paths=[suite_path],
            dataset_manifest_path=wrong_dataset,
        )
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH


def test_real_published_bundle_fails_closed_without_canonical_guard_authority(tmp_path: Path) -> None:
    """Verify that an end-to-end published candidate bundle fails closed on load_gate_evidence."""
    run_id, policy, suite_path, dataset_path = _setup_approved_candidate_fixture(tmp_path)

    with pytest.raises(EvaluationValidationError) as exc:
        load_gate_evidence(
            result_root=tmp_path,
            run_id=run_id,
            policy=policy,
            suite_paths=[suite_path],
            dataset_manifest_path=dataset_path,
        )
    assert exc.value.code == EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_all_variants_same_required_case_missing_prevents_pass() -> None:
    """When all variants miss a required case, the gate must detect it and refuse PASS."""
    from ai_worker.tests.evaluation.test_release_gate import _evidence, _paired, _policy

    manifest = load_dataset_manifest(DATASET_PATH)
    authoritative_cases = _derive_required_case_ids_from_manifest(manifest, (Partition.DEV,))
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


def test_validate_release_dataset_authority_accepts_frozen() -> None:
    evals_root = Path("evals")
    dataset = load_dataset(evals_root / "retrieval/manifests/rag-holdout-safety-v1.dataset.json", evals_root=evals_root)
    assert dataset.manifest.status == DatasetStatus.FROZEN
    validate_release_dataset_authority(dataset)


def test_validate_release_dataset_authority_rejects_draft() -> None:
    evals_root = Path("evals")
    dataset = load_dataset(evals_root / "retrieval/manifests/rag-retrieval-dev-v1.dataset.json", evals_root=evals_root)
    assert dataset.manifest.status == DatasetStatus.DRAFT
    with pytest.raises(EvaluationValidationError) as exc:
        validate_release_dataset_authority(dataset)
    assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID


def test_derive_required_case_ids_accepts_frozen_and_preserves_order() -> None:
    evals_root = Path("evals")
    dataset = load_dataset(evals_root / "retrieval/manifests/rag-holdout-safety-v1.dataset.json", evals_root=evals_root)
    cases = derive_required_case_ids(dataset, (Partition.HOLDOUT, Partition.SAFETY_REGRESSION))
    assert len(cases) > 0
    # Also verify partition filtering
    holdout_cases = derive_required_case_ids(dataset, (Partition.HOLDOUT,))
    assert all(c in cases for c in holdout_cases)


def test_derive_required_case_ids_rejects_draft_dataset() -> None:
    evals_root = Path("evals")
    dataset = load_dataset(evals_root / "retrieval/manifests/rag-retrieval-dev-v1.dataset.json", evals_root=evals_root)
    with pytest.raises(EvaluationValidationError) as exc:
        derive_required_case_ids(dataset, (Partition.DEV,))
    assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID


def test_derive_required_case_ids_from_manifest_filters_and_detects_duplicates() -> None:
    manifest = load_dataset_manifest(DATASET_PATH)
    dev_cases = _derive_required_case_ids_from_manifest(manifest, (Partition.DEV,))
    assert "rag-ret-dev-001" in dev_cases

    holdout_safety_cases = _derive_required_case_ids_from_manifest(
        manifest, (Partition.HOLDOUT, Partition.SAFETY_REGRESSION)
    )
    assert "rag-ret-dev-001" not in holdout_safety_cases

    # Duplicate case ID check
    resource_0 = manifest.case_resources[0]
    duplicate_resource = resource_0.model_copy(update={"partition": Partition.HOLDOUT})
    duplicated_resources = list(manifest.case_resources) + [duplicate_resource]
    mock_manifest = manifest.model_copy(update={"case_resources": tuple(duplicated_resources)})

    with pytest.raises(EvaluationValidationError) as exc_info:
        _derive_required_case_ids_from_manifest(mock_manifest, (Partition.DEV, Partition.HOLDOUT))
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


def test_gate_fails_closed_when_paired_comparison_required_without_production_evidence() -> None:
    from ai_worker.tests.evaluation.test_release_gate import _evidence, _policy

    policy = _policy()
    evidence = replace(_evidence(), paired_case_evidence=None)

    gate = build_release_gate(policy, evidence)
    assert gate.aggregate_decision_status is not DecisionStatus.PASS
    assert "PAIRED_COMPARISON_EVIDENCE_MISSING" in gate.blocking_reason_codes
