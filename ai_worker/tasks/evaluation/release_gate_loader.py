"""Actual evaluation artifact loader and assembler for Release Gate evaluation."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn, cast

from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.evaluation.comparison import load_published_run_bundle
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import ValidatedDataset, load_json_object
from ai_worker.tasks.evaluation.release_gate import (
    GateEvidence,
    MetricEvidence,
    ReceiptEvidence,
    ReleaseGatePolicy,
    SuiteEvidence,
)
from ai_worker.tasks.evaluation.release_policy import validate_release_review_provenance
from ai_worker.tasks.evaluation.schemas.artifacts import (
    CandidateGuardDecision,
    RagEvaluationRun,
    RuntimeEnvironment,
    SuiteResults,
)
from ai_worker.tasks.evaluation.schemas.authoring import DatasetManifest, DatasetStatus
from ai_worker.tasks.evaluation.schemas.authoring_v1_1 import DatasetManifestV11
from ai_worker.tasks.evaluation.schemas.authoring_v1_2 import DatasetManifestV12
from ai_worker.tasks.evaluation.schemas.authoring_v1_3 import DatasetManifestV13
from ai_worker.tasks.evaluation.schemas.common import (
    DecisionStatus,
    ExecutionStatus,
    ExperimentType,
    ImmutableReference,
    JsonValue,
    Partition,
)
from ai_worker.tasks.evaluation.schemas.policy import SuiteDefinition
from ai_worker.tasks.evaluation.schemas.policy_v1_2 import SuiteDefinitionV12

type AnyDatasetManifest = DatasetManifest | DatasetManifestV11 | DatasetManifestV12 | DatasetManifestV13


def load_suite_definition(path: Path) -> SuiteDefinition | SuiteDefinitionV12:
    """Load a versioned suite definition, verify its self-hash, and validate release review provenance."""

    if not path.is_file():
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING)

    raw_bytes = path.read_bytes()
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None

    if not isinstance(data, dict):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)

    model: type[SuiteDefinition | SuiteDefinitionV12] = (
        SuiteDefinitionV12 if data.get("schema_version") == "1.2.0" else SuiteDefinition
    )
    definition = load_json_object(path, model)
    payload = cast(JsonValue, definition.model_dump(mode="json"))
    expected_hash = canonical_sha256(payload, excluded_top_level_keys=frozenset({"suite_hash"}))
    if definition.suite_hash != expected_hash:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    validate_release_review_provenance(definition.review_provenance)

    return definition


def load_dataset_manifest(path: Path) -> AnyDatasetManifest:
    """Load an approved dataset manifest and verify its self-hash."""

    if not path.is_file():
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING)

    raw_bytes = path.read_bytes()
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None

    if not isinstance(data, dict):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)

    schema_version = data.get("schema_version")
    models: dict[str, type[AnyDatasetManifest]] = {
        "1.3.0": DatasetManifestV13,
        "1.2.0": DatasetManifestV12,
        "1.1.0": DatasetManifestV11,
        "1.0.0": DatasetManifest,
    }
    model = models.get(str(schema_version), DatasetManifest)
    manifest = load_json_object(path, model)
    payload = cast(dict[str, JsonValue], manifest.model_dump(mode="json"))
    expected_hash = canonical_sha256(payload, excluded_top_level_keys=frozenset({"manifest_sha256"}))
    if manifest.manifest_sha256 != expected_hash:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    return manifest


def derive_partition_manifest_hash(manifest: AnyDatasetManifest, partition: Partition) -> str:
    """Derive canonical partition manifest hash using the repository reference recipe."""

    resources: list[JsonValue] = [
        {"case_id": item.case_id, "path": item.path, "sha256": item.sha256}
        for item in manifest.case_resources
        if (
            item.partition is partition
            or item.partition == partition
            or getattr(item.partition, "value", None) == partition.value
        )
    ]
    return canonical_sha256({"partition": partition.value, "resources": resources})


def validate_release_dataset_authority(
    dataset: ValidatedDataset,
) -> None:
    """Validate that the dataset possesses approved FROZEN authority.

    Reuses existing Freeze contract (PD-216 / PD-241). Only FROZEN datasets loaded
    via load_dataset() (with complete Gold, Evidence, and Rubric approval closure)
    are authoritative required-case sources.
    """
    status = getattr(dataset.manifest, "status", None)
    if status is not DatasetStatus.FROZEN and status != "FROZEN" and getattr(status, "value", None) != "FROZEN":
        raise EvaluationValidationError(EvaluationErrorCode.REVIEW_PROVENANCE_INVALID)


def derive_required_case_ids(
    dataset: ValidatedDataset,
    target_partitions: Sequence[Partition],
) -> tuple[str, ...]:
    """Derive required case IDs from a validated dataset for specified partitions.

    Preserves canonical validated order and rejects duplicate case IDs with CASE_DUPLICATE.
    """
    validate_release_dataset_authority(dataset)
    pairs = tuple((c.case_id, getattr(c.partition, "value", None) or str(c.partition)) for c in dataset.cases)
    all_seen: set[str] = set()
    for case_id, _ in pairs:
        if case_id in all_seen:
            raise EvaluationValidationError(EvaluationErrorCode.CASE_DUPLICATE)
        all_seen.add(case_id)

    target_values = {p.value if hasattr(p, "value") else str(p) for p in target_partitions}
    case_ids = [case_id for case_id, partition_val in pairs if partition_val in target_values]
    if len(case_ids) != len(set(case_ids)):
        raise EvaluationValidationError(EvaluationErrorCode.CASE_DUPLICATE)
    return tuple(case_ids)


def _derive_required_case_ids_from_manifest(
    manifest: AnyDatasetManifest,
    target_partitions: Sequence[Partition],
) -> tuple[str, ...]:
    """Test helper: derive required case IDs from a raw dataset manifest."""
    pairs = tuple((r.case_id, getattr(r.partition, "value", None) or str(r.partition)) for r in manifest.case_resources)
    all_seen: set[str] = set()
    for case_id, _ in pairs:
        if case_id in all_seen:
            raise EvaluationValidationError(EvaluationErrorCode.CASE_DUPLICATE)
        all_seen.add(case_id)

    target_values = {p.value if hasattr(p, "value") else str(p) for p in target_partitions}
    case_ids = [case_id for case_id, partition_val in pairs if partition_val in target_values]
    if len(case_ids) != len(set(case_ids)):
        raise EvaluationValidationError(EvaluationErrorCode.CASE_DUPLICATE)
    return tuple(case_ids)


def load_receipt_evidence(path: Path) -> ReceiptEvidence:
    """Load and validate a versioned receipt, converting it to ReceiptEvidence.

    Rejects integrity-only, schema-validation-only, or diagnostic receipts that lack canonical
    release decision authority (PASS | FAIL | INCONCLUSIVE) or canonical currentness authority.
    Currently, the repository does not have a repository-defined Release Receipt schema;
    hence all receipts fail closed pending upstream contract (#162).
    """

    if not path.is_file():
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING)

    raw_bytes = path.read_bytes()
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None

    if not isinstance(data, dict):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)

    schema_id = data.get("schema_id")
    # 1. ProtectedArtifactReceipt is an integrity receipt and must not represent release approval
    if schema_id in {"rag-eval.protected-artifact-receipt", "rag-eval.protected-artifact-receipt.v1.2"}:
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    # 2. ValidationReceipt represents input validation (release_eligible=false, decision_status=N/A), not release approval
    if schema_id == "rag-eval.validation-receipt":
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    # 3. Release authority requires a known schema_id, schema_version, and repository-defined typed model
    # possessing canonical decision/currentness semantics. Currently, no such schema exists in the repository.
    # Arbitrary shape-compatible receipts without a registered typed contract fail closed.
    raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)


def _validate_release_run_structure(run: RagEvaluationRun) -> None:
    """Validate structural Run prerequisites only.

    This checks execution status, runtime eligibility, experiment type, and presence of
    persisted Guard fields, but does not establish authoritative Guard binding.
    """

    if run.execution_status is not ExecutionStatus.COMPLETED and run.execution_status != "COMPLETED":
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    if run.decision_status is not DecisionStatus.PASS and run.decision_status != "PASS":
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    if tuple(run.blocking_execution_statuses) != ():
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    if not run.runtime_eligible:
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    if run.experiment_type is not ExperimentType.END_TO_END_RAG and run.experiment_type != "END_TO_END_RAG":
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    if run.environment is not RuntimeEnvironment.LOCAL and run.environment != "LOCAL":
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    guard_fields = (
        run.candidate_bundle_id,
        run.candidate_bundle_manifest_hash,
        run.candidate_guard_decision_id,
        run.candidate_guard_decision,
        run.required_case_guard_coverage_manifest_hash,
    )
    if any(f is None or f == "" for f in guard_fields):
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    if run.candidate_guard_decision is not CandidateGuardDecision.PASS and run.candidate_guard_decision != "PASS":
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)


_validate_release_candidate_run = _validate_release_run_structure


def _require_canonical_release_guard_authority() -> NoReturn:
    """Fail closed because canonical #162 Guard authority binding is unavailable.

    Persisted Guard fields inside RagEvaluationRun are unverified claims and cannot
    establish Release Candidate authority until canonical #162 Guard artifacts are defined.
    """
    raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)


def _locate_suite_definition(
    expected_ref: ImmutableReference,
    suite_paths: Sequence[Path] | None,
) -> SuiteDefinition | SuiteDefinitionV12:
    if not suite_paths:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING)

    for path in suite_paths:
        definition = load_suite_definition(path)
        if (
            definition.suite_id == expected_ref.id
            and definition.suite_version == expected_ref.version
            and definition.suite_hash == expected_ref.hash
        ):
            return definition

    raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING)


def _validate_run_policy_bindings(run: RagEvaluationRun, policy: ReleaseGatePolicy) -> None:
    if (
        run.evaluation_policy_ref != policy.evaluation_policy_ref
        or run.evaluation_profile_ref != policy.evaluation_profile_ref
        or run.comparison_policy_ref != policy.comparison_policy_ref
    ):
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)


def _validate_run_dataset_and_partitions(run: RagEvaluationRun, dataset_manifest_path: Path | None) -> None:
    if dataset_manifest_path is None:
        return
    manifest = load_dataset_manifest(dataset_manifest_path)
    if manifest.manifest_sha256 != run.dataset_manifest_sha256:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    if manifest.dataset_code != run.dataset_code or manifest.dataset_version != run.dataset_version:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    manifest_partitions = {getattr(item.partition, "value", str(item.partition)) for item in manifest.case_resources}
    if not set(run.evaluated_partitions).issubset(manifest_partitions):
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    # run.partition_manifest_hash exact-match is verified only when an existing canonical recipe exists
    # (producer code + consumer code + same semantic domain: single partition reference hash).
    if len(run.evaluated_partitions) == 1:
        single_partition = Partition(run.evaluated_partitions[0])
        expected_hash = derive_partition_manifest_hash(manifest, single_partition)
        if run.partition_manifest_hash != expected_hash:
            raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    else:
        # Multi-partition actual release binding contract is pending upstream (#162).
        # We do not invent a combined partition_manifest_hash recipe; fail closed on actual execution.
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)


def _assemble_suite_evidences(
    suite_results: SuiteResults,
    suite_results_hash: str,
    required_suites: Sequence[ImmutableReference],
    suite_paths: Sequence[Path] | None,
) -> tuple[SuiteEvidence, ...]:
    suite_evidences: list[SuiteEvidence] = []
    for expected_suite in required_suites:
        definition = _locate_suite_definition(expected_suite, suite_paths)
        if suite_results.suite_id == expected_suite.id:
            suite_evidences.append(
                SuiteEvidence(
                    suite=suite_results,
                    definition=definition,
                    artifact_ref=ImmutableReference(
                        id=suite_results.suite_id,
                        version=suite_results.suite_version,
                        hash=suite_results_hash,
                    ),
                )
            )
    return tuple(suite_evidences)


def load_gate_evidence(
    *,
    result_root: Path,
    run_id: str,
    policy: ReleaseGatePolicy,
    suite_paths: Sequence[Path] | None = None,
    receipt_paths: Sequence[Path] | None = None,
    dataset_manifest_path: Path | None = None,
) -> GateEvidence:
    """Load actual published evaluation artifacts and assemble GateEvidence."""

    bundle = load_published_run_bundle(result_root, run_id)
    run = bundle.run
    if run.run_id != run_id:
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    _validate_release_run_structure(run)

    _validate_run_policy_bindings(run, policy)
    _validate_run_dataset_and_partitions(run, dataset_manifest_path)

    _require_canonical_release_guard_authority()

    metrics_artifact = bundle.metrics
    metrics_digest = canonical_sha256(cast(JsonValue, metrics_artifact.model_dump(mode="json")))
    metric_evidences = tuple(
        MetricEvidence(
            metric=metric,
            artifact=metrics_artifact,
            artifact_ref=ImmutableReference(
                id=metric.metric_id,
                version=metric.metric_version,
                hash=metrics_digest,
            ),
        )
        for metric in metrics_artifact.metrics
    )

    suite_results = SuiteResults.model_validate_json(bundle.files["suite-results.json"])
    suite_payload = cast(JsonValue, suite_results.model_dump(mode="json"))
    suite_results_hash = canonical_sha256(suite_payload)
    suite_evidences = _assemble_suite_evidences(
        suite_results,
        suite_results_hash,
        policy.required_suites,
        suite_paths,
    )

    receipt_evidences = tuple(load_receipt_evidence(p) for p in (receipt_paths or ()))

    return GateEvidence(
        run_id=run_id,
        required_scope_manifest_hash=policy.required_scope_manifest_hash,
        completed_experiment_types=(ExperimentType(run.experiment_type),),
        completed_partitions=tuple(Partition(p) for p in run.evaluated_partitions),
        metrics=metric_evidences,
        suites=suite_evidences,
        receipts=receipt_evidences,
        paired_case_evidence=None,
    )
