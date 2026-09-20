"""Unit and parameterized tests for Canonical Evaluation Guard Evidence (#162 Phase A2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from ai_worker.adapters.sqlalchemy_evaluation_guard_authority import (
    EvaluationEnvironmentFenceObservation,
    EvaluationRuntimeAuthorityObservation,
)
from ai_worker.tasks.evaluation.canonical import canonical_json_bytes
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.guard_evidence import (
    EvaluationCandidateGuardEvaluator,
    EvaluationRequestGuardEvaluator,
    compute_candidate_guard_hash,
    compute_required_case_set_hash,
    finalize_evaluation_guard_evidence,
    load_evaluation_guard_evidence,
    validate_candidate_guard_ref,
    validate_case_guard_ref,
    validate_environment_fence,
    validate_evaluation_guard_evidence,
    validate_run_guard_binding,
    verify_environment_revision_fence,
)
from ai_worker.tasks.evaluation.schemas.artifacts import RagEvaluationRun
from ai_worker.tasks.evaluation.schemas.authoring import DatasetStatus
from ai_worker.tasks.evaluation.schemas.common import Partition
from ai_worker.tasks.evaluation.schemas.guard_evidence_v1 import (
    EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE,
    EVALUATION_CANDIDATE_OPERATION,
    EVALUATION_GUARD_ARTIFACT_VERSION,
    EVALUATION_GUARD_PASS_DECISION,
    CandidateSourceGovernanceObservation,
    EvaluationCandidateGuard,
    EvaluationCaseGuardBinding,
    EvaluationGuardArtifactRef,
    EvaluationGuardEvidence,
)
from rag_runtime.request_guard_runtime_binding import canonical_scope_manifest_hash


@dataclass(frozen=True, slots=True)
class DummyCase:
    case_id: str
    partition: Partition


class DummyDataset:
    def __init__(self, manifest: Any, cases: tuple[DummyCase, ...]) -> None:
        self.manifest = manifest
        self.cases = cases


@dataclass
class DummyManifest:
    dataset_code: str = "dataset-eval-162"
    dataset_version: str = "1.0.0"
    manifest_sha256: str = "a" * 64
    status: DatasetStatus = DatasetStatus.FROZEN


@pytest.fixture
def sample_dataset() -> Any:
    manifest = DummyManifest()
    cases = (
        DummyCase(case_id="case-001", partition=Partition.HOLDOUT),
        DummyCase(case_id="case-002", partition=Partition.HOLDOUT),
        DummyCase(case_id="case-003", partition=Partition.SAFETY_REGRESSION),
    )
    return DummyDataset(manifest=manifest, cases=cases)


@pytest.fixture
def runtime_authority() -> EvaluationRuntimeAuthorityObservation:
    return EvaluationRuntimeAuthorityObservation(
        bundle_id=UUID("11111111-1111-4111-8111-111111111111"),
        bundle_manifest_hash="c" * 64,
        bundle_status="BUILDING",
        environment_code="LOCAL",
        runtime_execution_manifest_id=UUID("22222222-2222-4222-8222-222222222222"),
        runtime_execution_manifest_hash="d" * 64,
        governance_revision_ref="GOV-REV-20260920",
        environment_revision=10,
        safety_epoch=4,
    )


@pytest.fixture
def source_governance_pass() -> CandidateSourceGovernanceObservation:
    return CandidateSourceGovernanceObservation(decision="PASS")


@pytest.fixture
def end_fence() -> EvaluationEnvironmentFenceObservation:
    return EvaluationEnvironmentFenceObservation(
        environment_code="LOCAL",
        environment_revision=10,
        governance_revision_ref="GOV-REV-20260920",
        safety_epoch=4,
    )


# ---------------------------------------------------------------------------
# Task 1: Hash Kernel Tests
# ---------------------------------------------------------------------------


def test_required_case_set_hash_kernel() -> None:
    manifest_hash = "f" * 64
    partitions = [Partition.SAFETY_REGRESSION, Partition.HOLDOUT]
    case_ids = ["case-b", "case-a"]

    hash_val = compute_required_case_set_hash(
        dataset_manifest_sha256=manifest_hash,
        required_partitions=partitions,
        case_ids=case_ids,
    )
    assert len(hash_val) == 64

    # Order of inputs shouldn't matter as kernel canonicalizes
    hash_val2 = compute_required_case_set_hash(
        dataset_manifest_sha256=manifest_hash,
        required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
        case_ids=["case-a", "case-b"],
    )
    assert hash_val == hash_val2

    # Duplicate case IDs must be rejected
    with pytest.raises(EvaluationValidationError) as exc:
        compute_required_case_set_hash(
            dataset_manifest_sha256=manifest_hash,
            required_partitions=partitions,
            case_ids=["case-a", "case-a"],
        )
    assert exc.value.code == EvaluationErrorCode.CASE_DUPLICATE


# ---------------------------------------------------------------------------
# Test Candidate Guard Artifact Fixture Helper
# (Decoupled from production evaluator to prevent authority overclaim)
# ---------------------------------------------------------------------------


def _test_candidate_guard_fixture(
    *,
    evaluation_run_id: UUID | str = "33333333-3333-4333-8333-333333333333",
    candidate_guard_decision_id: UUID | str = "44444444-4444-4444-8444-444444444444",
    candidate_bundle_id: UUID | str = "11111111-1111-4111-8111-111111111111",
    candidate_bundle_manifest_hash: str = "c" * 64,
    runtime_execution_manifest_id: UUID | str = "22222222-2222-4222-8222-222222222222",
    runtime_execution_manifest_hash: str = "d" * 64,
    environment: str = "LOCAL",
    environment_revision_fence: int = 10,
    governance_revision_ref: str = "GOV-REV-20260920",
    safety_epoch: int = 4,
    dataset_code: str = "dataset-eval-162",
    dataset_version: str = "1.0.0",
    dataset_manifest_sha256: str = "a" * 64,
    required_partitions: tuple[Partition, ...] = (Partition.HOLDOUT, Partition.SAFETY_REGRESSION),
    required_case_ids: tuple[str, ...] = ("case-001", "case-002", "case-003"),
) -> EvaluationCandidateGuard:
    decision_id_str = str(candidate_guard_decision_id)
    run_id_str = str(evaluation_run_id)
    bundle_id_str = str(candidate_bundle_id).lower()
    manifest_id_str = str(runtime_execution_manifest_id).lower()

    sorted_partitions = tuple(
        sorted(
            (Partition(p.value if hasattr(p, "value") else str(p)) for p in required_partitions),
            key=lambda p: p.value.encode("utf-16-be"),
        )
    )
    req_hash = compute_required_case_set_hash(
        dataset_manifest_sha256=dataset_manifest_sha256,
        required_partitions=sorted_partitions,
        case_ids=required_case_ids,
    )
    content_sha256 = compute_candidate_guard_hash(
        candidate_guard_decision_id=decision_id_str,
        operation=EVALUATION_CANDIDATE_OPERATION,
        decision=EVALUATION_GUARD_PASS_DECISION,
        evaluation_run_id=run_id_str,
        environment=environment,
        environment_revision_fence=environment_revision_fence,
        governance_revision_ref=governance_revision_ref,
        safety_epoch=safety_epoch,
        candidate_bundle_id=bundle_id_str,
        candidate_bundle_manifest_hash=candidate_bundle_manifest_hash,
        runtime_execution_manifest_id=manifest_id_str,
        runtime_execution_manifest_hash=runtime_execution_manifest_hash,
        dataset_code=dataset_code,
        dataset_version=dataset_version,
        dataset_manifest_sha256=dataset_manifest_sha256,
        required_partitions=sorted_partitions,
        required_case_set_hash=req_hash,
    )
    ref = EvaluationGuardArtifactRef(
        artifact_code=EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE,
        version=EVALUATION_GUARD_ARTIFACT_VERSION,
        content_sha256=content_sha256,
    )
    return EvaluationCandidateGuard(
        candidate_guard_decision_id=decision_id_str,
        operation=EVALUATION_CANDIDATE_OPERATION,
        decision=EVALUATION_GUARD_PASS_DECISION,
        evaluation_run_id=run_id_str,
        environment=environment,
        environment_revision_fence=environment_revision_fence,
        governance_revision_ref=governance_revision_ref,
        safety_epoch=safety_epoch,
        candidate_bundle_id=bundle_id_str,
        candidate_bundle_manifest_hash=candidate_bundle_manifest_hash,
        runtime_execution_manifest_id=manifest_id_str,
        runtime_execution_manifest_hash=runtime_execution_manifest_hash,
        dataset_code=dataset_code,
        dataset_version=dataset_version,
        dataset_manifest_sha256=dataset_manifest_sha256,
        required_partitions=sorted_partitions,
        required_case_set_hash=req_hash,
        candidate_guard_ref=ref,
    )


def test_candidate_guard_fixture_produces_ref_valid_artifact() -> None:
    candidate = _test_candidate_guard_fixture()
    assert candidate.operation == "EVALUATION_CANDIDATE"
    assert candidate.decision == "PASS"
    assert candidate.environment == "LOCAL"
    assert candidate.environment_revision_fence == 10
    assert candidate.governance_revision_ref == "GOV-REV-20260920"
    assert candidate.safety_epoch == 4
    assert candidate.candidate_guard_ref.artifact_code == EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE
    assert candidate.candidate_guard_ref.version == "1.0"
    validate_candidate_guard_ref(candidate)


# ---------------------------------------------------------------------------
# Task 2 & 7: Candidate Evaluator Authority Boundary (Fail-Closed)
# ---------------------------------------------------------------------------


def test_candidate_evaluator_source_authority_unavailable_returns_none(
    sample_dataset: Any,
    runtime_authority: EvaluationRuntimeAuthorityObservation,
) -> None:
    # 1. Missing source authority observation (None) -> returns None
    candidate_none = EvaluationCandidateGuardEvaluator.evaluate_candidate_start(
        evaluation_run_id=uuid4(),
        dataset=sample_dataset,
        required_partitions=[Partition.HOLDOUT],
        runtime_authority=runtime_authority,
        source_governance_authority=None,
    )
    assert candidate_none is None

    # 2. Source authority observation UNAVAILABLE -> returns None
    obs_unavail = CandidateSourceGovernanceObservation(
        decision="UNAVAILABLE",
        failure_reasons=("SOURCE_GOVERNANCE_AUTHORITY_UNAVAILABLE",),
    )
    candidate_unavail = EvaluationCandidateGuardEvaluator.evaluate_candidate_start(
        evaluation_run_id=uuid4(),
        dataset=sample_dataset,
        required_partitions=[Partition.HOLDOUT],
        runtime_authority=runtime_authority,
        source_governance_authority=obs_unavail,
    )
    assert candidate_unavail is None


def test_candidate_evaluator_source_authority_fail_returns_none(
    sample_dataset: Any,
    runtime_authority: EvaluationRuntimeAuthorityObservation,
) -> None:
    # Source authority observation FAIL -> returns None
    obs_fail = CandidateSourceGovernanceObservation(
        decision="FAIL",
        failure_reasons=("SOURCE_REVOKED",),
    )
    candidate_fail = EvaluationCandidateGuardEvaluator.evaluate_candidate_start(
        evaluation_run_id=uuid4(),
        dataset=sample_dataset,
        required_partitions=[Partition.HOLDOUT],
        runtime_authority=runtime_authority,
        source_governance_authority=obs_fail,
    )
    assert candidate_fail is None


def test_candidate_evaluator_caller_asserted_pass_does_not_issue_production_candidate_pass(
    sample_dataset: Any,
    runtime_authority: EvaluationRuntimeAuthorityObservation,
) -> None:
    # Caller-created PASS observation only -> production Candidate Guard PASS 반환 없음
    # In Phase A2, canonical production Source Governance authority bridge is unavailable.
    # Therefore caller-asserted PASS observation is rejected as an authority source and returns None.
    obs_caller_pass = CandidateSourceGovernanceObservation(decision="PASS")
    candidate = EvaluationCandidateGuardEvaluator.evaluate_candidate_start(
        evaluation_run_id=uuid4(),
        dataset=sample_dataset,
        required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
        runtime_authority=runtime_authority,
        source_governance_authority=obs_caller_pass,
    )
    assert candidate is None


def test_candidate_evaluator_fails_closed_on_corrupt_runtime_authority(
    sample_dataset: Any,
) -> None:
    # Negative structural checks fail closed with EvaluationValidationError
    bad_runtime = EvaluationRuntimeAuthorityObservation(
        bundle_id=UUID("11111111-1111-4111-8111-111111111111"),
        bundle_manifest_hash="c" * 64,
        bundle_status="READY",  # Must be BUILDING
        environment_code="LOCAL",
        runtime_execution_manifest_id=UUID("22222222-2222-4222-8222-222222222222"),
        runtime_execution_manifest_hash="d" * 64,
        governance_revision_ref="GOV-REV-20260920",
        environment_revision=10,
        safety_epoch=4,
    )
    with pytest.raises(EvaluationValidationError) as exc:
        EvaluationCandidateGuardEvaluator.evaluate_candidate_start(
            evaluation_run_id=uuid4(),
            dataset=sample_dataset,
            required_partitions=[Partition.HOLDOUT],
            runtime_authority=bad_runtime,
            source_governance_authority=CandidateSourceGovernanceObservation(decision="PASS"),
        )
    assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


# ---------------------------------------------------------------------------
# Task 8: Case Evaluator & Coordinate Inheritance
# ---------------------------------------------------------------------------


def test_case_evaluator_inherits_coordinates_and_validates_scope() -> None:
    run_id = uuid4()
    candidate = _test_candidate_guard_fixture(evaluation_run_id=run_id)

    scopes = ("GUIDE", "PATIENT_CITATION")
    case_binding = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate,
        case_id="case-001",
        request_scope_codes=scopes,
    )

    assert case_binding.operation == "EVALUATION_REQUEST"
    assert case_binding.decision == "PASS"
    assert case_binding.evaluation_run_id == candidate.evaluation_run_id
    assert case_binding.candidate_bundle_id == candidate.candidate_bundle_id
    assert case_binding.candidate_bundle_manifest_hash == candidate.candidate_bundle_manifest_hash
    assert case_binding.candidate_guard_ref == candidate.candidate_guard_ref
    assert case_binding.environment == candidate.environment
    assert case_binding.environment_revision_fence == candidate.environment_revision_fence
    assert case_binding.governance_revision_ref == candidate.governance_revision_ref
    assert case_binding.safety_epoch == candidate.safety_epoch
    assert case_binding.required_case_set_hash == candidate.required_case_set_hash
    assert case_binding.scope_manifest_hash == canonical_scope_manifest_hash(scopes)

    # Recomputed case ref matches
    validate_case_guard_ref(case_binding)


# ---------------------------------------------------------------------------
# Task 5: Environment Fence Tests
# ---------------------------------------------------------------------------


def test_environment_fence_stability_and_rejections() -> None:
    assert verify_environment_revision_fence is validate_environment_fence
    candidate = _test_candidate_guard_fixture()

    # 1. Stable fence passes
    stable_fence = EvaluationEnvironmentFenceObservation(
        environment_code="LOCAL",
        environment_revision=10,
        governance_revision_ref="GOV-REV-20260920",
        safety_epoch=4,
    )
    validate_environment_fence(candidate_guard=candidate, end_fence=stable_fence)

    # 2. Revision incremented during run -> reject
    rev_changed = EvaluationEnvironmentFenceObservation(
        environment_code="LOCAL",
        environment_revision=11,
        governance_revision_ref="GOV-REV-20260920",
        safety_epoch=4,
    )
    with pytest.raises(EvaluationValidationError) as exc:
        validate_environment_fence(candidate_guard=candidate, end_fence=rev_changed)
    assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID

    # 3. Governance changed -> reject
    gov_changed = EvaluationEnvironmentFenceObservation(
        environment_code="LOCAL",
        environment_revision=10,
        governance_revision_ref="GOV-REV-CHANGED",
        safety_epoch=4,
    )
    with pytest.raises(EvaluationValidationError) as exc:
        validate_environment_fence(candidate_guard=candidate, end_fence=gov_changed)
    assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID

    # 4. Safety epoch changed -> reject
    epoch_changed = EvaluationEnvironmentFenceObservation(
        environment_code="LOCAL",
        environment_revision=10,
        governance_revision_ref="GOV-REV-20260920",
        safety_epoch=5,
    )
    with pytest.raises(EvaluationValidationError) as exc:
        validate_environment_fence(candidate_guard=candidate, end_fence=epoch_changed)
    assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID


# ---------------------------------------------------------------------------
# Task 9: Finalization & Independent Evidence Validator
# ---------------------------------------------------------------------------


def test_finalization_and_independent_validation_pipeline(
    sample_dataset: Any,
    end_fence: EvaluationEnvironmentFenceObservation,
) -> None:
    run_id = uuid4()
    candidate = _test_candidate_guard_fixture(evaluation_run_id=run_id)

    scopes = ("GUIDE", "PATIENT_CITATION")
    case_1 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate,
        case_id="case-001",
        request_scope_codes=scopes,
    )
    case_2 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate,
        case_id="case-002",
        request_scope_codes=scopes,
    )
    case_3 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate,
        case_id="case-003",
        request_scope_codes=scopes,
    )

    evidence = finalize_evaluation_guard_evidence(
        candidate_guard=candidate,
        case_guards=(case_1, case_2, case_3),
        dataset=sample_dataset,
        required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
        end_environment_fence=end_fence,
    )

    assert evidence.schema_id == "rag-eval.evaluation-guard-evidence"
    assert evidence.schema_version == "1.0.0"
    assert len(evidence.guard_coverage_manifest_hash) == 64

    # Independent validator verification
    validate_evaluation_guard_evidence(
        evidence,
        dataset=sample_dataset,
        required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
    )


# ---------------------------------------------------------------------------
# Section 39: Candidate 17-Field Mutation Parameterized Test
# ---------------------------------------------------------------------------


CANDIDATE_MUTATIONS = [
    ("candidate_guard_decision_id", str(uuid4())),
    ("operation", "OTHER_OP"),
    ("decision", "FAIL"),
    ("evaluation_run_id", str(uuid4())),
    ("environment", "TEST"),
    ("environment_revision_fence", 99),
    ("governance_revision_ref", "MUTATED_GOV"),
    ("safety_epoch", 99),
    ("candidate_bundle_id", str(uuid4())),
    ("candidate_bundle_manifest_hash", "9" * 64),
    ("runtime_execution_manifest_id", str(uuid4())),
    ("runtime_execution_manifest_hash", "8" * 64),
    ("dataset_code", "mutated-dataset"),
    ("dataset_version", "9.9.9"),
    ("dataset_manifest_sha256", "7" * 64),
    ("required_partitions", (Partition.DEV,)),
    ("required_case_set_hash", "6" * 64),
]


@pytest.mark.parametrize("field_name, mutated_value", CANDIDATE_MUTATIONS)
def test_candidate_field_mutation_matrix(
    field_name: str,
    mutated_value: Any,
) -> None:
    candidate = _test_candidate_guard_fixture()

    data = candidate.model_dump()
    data[field_name] = mutated_value

    try:
        mutated = EvaluationCandidateGuard.model_validate(data)
        # If schema allowed it, ref check must reject
        with pytest.raises(EvaluationValidationError) as exc:
            validate_candidate_guard_ref(mutated)
        assert exc.value.code == EvaluationErrorCode.HASH_MISMATCH
    except (ValidationError, EvaluationValidationError, ValueError):
        # Schema validation rejected the mutation (e.g. Literal["PASS"], CanonicalUuid)
        pass


# ---------------------------------------------------------------------------
# Section 40: Case 17-Field Mutation Parameterized Test
# ---------------------------------------------------------------------------


CASE_MUTATIONS = [
    ("case_guard_decision_id", str(uuid4())),
    ("operation", "OTHER_OP"),
    ("decision", "FAIL"),
    ("evaluation_run_id", str(uuid4())),
    ("case_id", "mutated-case-id"),
    (
        "candidate_guard_ref",
        {
            "artifact_code": EVALUATION_CANDIDATE_GUARD_ARTIFACT_CODE,
            "version": "1.0",
            "content_sha256": "0" * 64,
        },
    ),
    ("environment", "TEST"),
    ("environment_revision_fence", 99),
    ("governance_revision_ref", "MUTATED_GOV"),
    ("safety_epoch", 99),
    ("candidate_bundle_id", str(uuid4())),
    ("candidate_bundle_manifest_hash", "9" * 64),
    ("runtime_execution_manifest_id", str(uuid4())),
    ("runtime_execution_manifest_hash", "8" * 64),
    ("required_case_set_hash", "7" * 64),
    ("request_scope_codes", ("GUIDE",)),
    ("scope_manifest_hash", "6" * 64),
]


@pytest.mark.parametrize("field_name, mutated_value", CASE_MUTATIONS)
def test_case_field_mutation_matrix(
    field_name: str,
    mutated_value: Any,
) -> None:
    candidate = _test_candidate_guard_fixture()
    binding = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate,
        case_id="case-001",
        request_scope_codes=("GUIDE", "PATIENT_CITATION"),
    )

    data = binding.model_dump()
    data[field_name] = mutated_value

    try:
        mutated = EvaluationCaseGuardBinding.model_validate(data)
        with pytest.raises(EvaluationValidationError) as exc:
            validate_case_guard_ref(mutated)
        assert exc.value.code == EvaluationErrorCode.HASH_MISMATCH
    except (ValidationError, EvaluationValidationError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Section 41: Mandatory Exact-Set Tests
# ---------------------------------------------------------------------------


def test_mandatory_exact_set_coverage_failures(
    sample_dataset: Any,
    end_fence: EvaluationEnvironmentFenceObservation,
) -> None:
    candidate = _test_candidate_guard_fixture()
    scopes = ("GUIDE", "PATIENT_CITATION")
    c1 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate, case_id="case-001", request_scope_codes=scopes
    )
    c2 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate, case_id="case-002", request_scope_codes=scopes
    )
    c3 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate, case_id="case-003", request_scope_codes=scopes
    )

    # 1. Missing case (case-003 omitted) -> BASELINE_ARTIFACT_INVALID
    with pytest.raises(EvaluationValidationError) as exc:
        finalize_evaluation_guard_evidence(
            candidate_guard=candidate,
            case_guards=(c1, c2),
            dataset=sample_dataset,
            required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
            end_environment_fence=end_fence,
        )
    assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID

    # 2. Duplicate case (case-001 repeated) -> CASE_DUPLICATE
    with pytest.raises(EvaluationValidationError) as exc:
        finalize_evaluation_guard_evidence(
            candidate_guard=candidate,
            case_guards=(c1, c1, c2, c3),
            dataset=sample_dataset,
            required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
            end_environment_fence=end_fence,
        )
    assert exc.value.code == EvaluationErrorCode.CASE_DUPLICATE

    # 3. Extra case not in dataset -> BASELINE_ARTIFACT_INVALID
    extra_case = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate, case_id="case-999", request_scope_codes=scopes
    )
    with pytest.raises(EvaluationValidationError) as exc:
        finalize_evaluation_guard_evidence(
            candidate_guard=candidate,
            case_guards=(c1, c2, c3, extra_case),
            dataset=sample_dataset,
            required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
            end_environment_fence=end_fence,
        )
    assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID

    # 4. Reversed wire order -> BASELINE_ARTIFACT_INVALID
    with pytest.raises(EvaluationValidationError) as exc:
        finalize_evaluation_guard_evidence(
            candidate_guard=candidate,
            case_guards=(c3, c2, c1),
            dataset=sample_dataset,
            required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
            end_environment_fence=end_fence,
        )
    assert exc.value.code == EvaluationErrorCode.BASELINE_ARTIFACT_INVALID

    # 5. Spliced candidate ref (binding created with a different candidate)
    other_candidate = _test_candidate_guard_fixture(evaluation_run_id=uuid4())
    spliced_c3 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=other_candidate, case_id="case-003", request_scope_codes=scopes
    )
    with pytest.raises(EvaluationValidationError) as exc:
        finalize_evaluation_guard_evidence(
            candidate_guard=candidate,
            case_guards=(c1, c2, spliced_c3),
            dataset=sample_dataset,
            required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
            end_environment_fence=end_fence,
        )
    assert exc.value.code == EvaluationErrorCode.HASH_MISMATCH


# ---------------------------------------------------------------------------
# Section 44: Canonical Ordering Tests (UTF-16 BE)
# ---------------------------------------------------------------------------


def test_utf16_be_canonical_ordering() -> None:
    # Test strings where UTF-8 byte order and UTF-16 BE order diverge or have boundary characters
    cases = ["a", "z", "A", "Z", "0", "9", "가", "힣", "α", "ω"]
    utf16_sorted = sorted(cases, key=lambda s: s.encode("utf-16-be"))
    assert compute_required_case_set_hash(
        dataset_manifest_sha256="a" * 64,
        required_partitions=[Partition.HOLDOUT],
        case_ids=cases,
    ) == compute_required_case_set_hash(
        dataset_manifest_sha256="a" * 64,
        required_partitions=[Partition.HOLDOUT],
        case_ids=utf16_sorted,
    )


# ---------------------------------------------------------------------------
# Section 49: Privacy Boundary Tests
# ---------------------------------------------------------------------------


def test_privacy_boundary_forbids_sensitive_fields() -> None:
    candidate_fields = set(EvaluationCandidateGuard.model_fields.keys())
    case_fields = set(EvaluationCaseGuardBinding.model_fields.keys())
    evidence_fields = set(EvaluationGuardEvidence.model_fields.keys())

    forbidden = {
        "patient_id",
        "patient_context",
        "question",
        "query",
        "prompt",
        "answer",
        "response",
        "provider_payload",
        "credential",
        "password",
        "token",
    }

    assert not candidate_fields.intersection(forbidden)
    assert not case_fields.intersection(forbidden)
    assert not evidence_fields.intersection(forbidden)


# ---------------------------------------------------------------------------
# Section 36: Run Binding Validator Tests
# ---------------------------------------------------------------------------


def test_validate_run_guard_binding(
    sample_dataset: Any,
    end_fence: EvaluationEnvironmentFenceObservation,
) -> None:
    run_id = str(uuid4())
    candidate = _test_candidate_guard_fixture(evaluation_run_id=run_id)
    scopes = ("GUIDE", "PATIENT_CITATION")
    c1 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate, case_id="case-001", request_scope_codes=scopes
    )
    c2 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate, case_id="case-002", request_scope_codes=scopes
    )
    c3 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate, case_id="case-003", request_scope_codes=scopes
    )
    evidence = finalize_evaluation_guard_evidence(
        candidate_guard=candidate,
        case_guards=(c1, c2, c3),
        dataset=sample_dataset,
        required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
        end_environment_fence=end_fence,
    )

    # Construct matching RagEvaluationRun
    run_dict: dict[str, Any] = {
        "schema_id": "rag-eval.run",
        "schema_version": "1.0.0",
        "run_id": evidence.evaluation_run_id,
        "experiment_id": "exp-1",
        "variant_id": "var-1",
        "experiment_type": "END_TO_END_RAG",
        "task_types": ["END_TO_END_RAG"],
        "evaluation_profile_ref": {"id": "p1", "version": "1.0.0", "hash": "1" * 64},
        "comparison_policy_ref": {"id": "cp1", "version": "1.0.0", "hash": "2" * 64},
        "evaluation_policy_ref": {"id": "ep1", "version": "1.0.0", "hash": "3" * 64},
        "artifact_schema_set_ref": {"id": "ss1", "version": "1.0.0", "hash": "4" * 64},
        "dataset_code": evidence.dataset_code,
        "dataset_version": evidence.dataset_version,
        "dataset_manifest_sha256": evidence.dataset_manifest_sha256,
        "resource_set_hash": "5" * 64,
        "evidence_mapping_manifest_sha256": "6" * 64,
        "critical_claim_rubric_ref": {"id": "r1", "version": "1.0.0", "hash": "7" * 64},
        "fixture_git_commit_sha": "0" * 40,
        "protected_artifact_receipt_ref": None,
        "resolved_evaluation_config_hash": "8" * 64,
        "upstream_contract_manifest_hash": "9" * 64,
        "retrieval_variant_manifest_hash": None,
        "answer_variant_manifest_hash": None,
        "model_config_hash": "0" * 64,
        "prompt_version": "v1",
        "evaluated_partitions": ["HOLDOUT", "SAFETY_REGRESSION"],
        "partition_manifest_hash": "a" * 64,
        "environment": "LOCAL",
        "runtime_eligible": True,
        "candidate_bundle_id": evidence.candidate_bundle_id,
        "candidate_bundle_manifest_hash": evidence.candidate_bundle_manifest_hash,
        "candidate_guard_decision_id": evidence.candidate_guard_decision_id,
        "candidate_guard_decision": "PASS",
        "required_case_guard_coverage_manifest_hash": evidence.guard_coverage_manifest_hash,
        "executed_by": {"actor_id": "tester", "role": "EVALUATION_IMPLEMENTER", "namespace": "SYSTEM"},
        "started_at": "2026-09-20T00:00:00.000000Z",
        "completed_at": "2026-09-20T00:01:00.000000Z",
        "execution_status": "COMPLETED",
        "decision_status": "PASS",
        "blocking_execution_statuses": [],
        "result_content_manifest_hash": "b" * 64,
    }
    run = RagEvaluationRun.model_validate(run_dict)

    # Valid binding passes
    validate_run_guard_binding(run, evidence)

    # Mutated coverage hash -> HASH_MISMATCH
    tampered_run = run.model_copy(update={"required_case_guard_coverage_manifest_hash": "0" * 64})
    with pytest.raises(EvaluationValidationError) as exc:
        validate_run_guard_binding(tampered_run, evidence)
    assert exc.value.code == EvaluationErrorCode.HASH_MISMATCH


# ---------------------------------------------------------------------------
# Section 37 & 38: File Serialization Round-Trip
# ---------------------------------------------------------------------------


def test_file_serialization_round_trip(
    tmp_path: Path,
    sample_dataset: Any,
    end_fence: EvaluationEnvironmentFenceObservation,
) -> None:
    candidate = _test_candidate_guard_fixture()
    scopes = ("GUIDE", "PATIENT_CITATION")
    c1 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate, case_id="case-001", request_scope_codes=scopes
    )
    c2 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate, case_id="case-002", request_scope_codes=scopes
    )
    c3 = EvaluationRequestGuardEvaluator.evaluate(
        candidate_guard=candidate, case_id="case-003", request_scope_codes=scopes
    )
    evidence = finalize_evaluation_guard_evidence(
        candidate_guard=candidate,
        case_guards=(c1, c2, c3),
        dataset=sample_dataset,
        required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
        end_environment_fence=end_fence,
    )

    artifact_path = tmp_path / "evaluation_guard_evidence.json"
    raw_bytes = canonical_json_bytes(evidence.model_dump(mode="json"))
    artifact_path.write_bytes(raw_bytes)

    loaded = load_evaluation_guard_evidence(artifact_path)
    assert loaded == evidence

    validate_evaluation_guard_evidence(
        loaded,
        dataset=sample_dataset,
        required_partitions=[Partition.HOLDOUT, Partition.SAFETY_REGRESSION],
    )
