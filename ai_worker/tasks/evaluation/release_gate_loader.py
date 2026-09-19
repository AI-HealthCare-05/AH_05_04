"""Actual evaluation artifact loader and assembler for Release Gate evaluation."""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.evaluation.comparison import load_published_run_bundle
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import ValidatedDataset, load_json_object
from ai_worker.tasks.evaluation.release_gate import (
    ControlSettingEvidence,
    GateEvidence,
    MetricEvidence,
    PairedCaseEvidence,
    ReceiptEvidence,
    ReleaseGatePolicy,
    SuiteEvidence,
    paired_case_manifest_hash,
)
from ai_worker.tasks.evaluation.schemas.artifacts import (
    RagEvaluationRun,
    SuiteResults,
)
from ai_worker.tasks.evaluation.schemas.authoring import DatasetManifest
from ai_worker.tasks.evaluation.schemas.authoring_v1_1 import DatasetManifestV11
from ai_worker.tasks.evaluation.schemas.authoring_v1_2 import DatasetManifestV12
from ai_worker.tasks.evaluation.schemas.authoring_v1_3 import DatasetManifestV13
from ai_worker.tasks.evaluation.schemas.common import (
    ExperimentType,
    ImmutableReference,
    JsonValue,
    Partition,
)
from ai_worker.tasks.evaluation.schemas.policy import SuiteDefinition
from ai_worker.tasks.evaluation.schemas.policy_v1_2 import SuiteDefinitionV12

type AnyDatasetManifest = DatasetManifest | DatasetManifestV11 | DatasetManifestV12 | DatasetManifestV13


def load_suite_definition(path: Path) -> SuiteDefinition | SuiteDefinitionV12:
    """Load a versioned suite definition and verify its self-hash."""

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


def _extract_case_ids_and_partitions(
    manifest: AnyDatasetManifest | ValidatedDataset,
) -> tuple[tuple[str, str], ...]:
    if hasattr(manifest, "cases"):
        return tuple((c.case_id, getattr(c.partition, "value", None) or str(c.partition)) for c in manifest.cases)
    return tuple((r.case_id, getattr(r.partition, "value", None) or str(r.partition)) for r in manifest.case_resources)


def derive_required_case_ids(
    manifest: AnyDatasetManifest | ValidatedDataset,
    target_partitions: Sequence[Partition],
) -> tuple[str, ...]:
    """Derive required case IDs from an approved dataset manifest or validated dataset for specified partitions.

    Preserves canonical validated order and rejects duplicate case IDs with CASE_DUPLICATE.
    """

    pairs = _extract_case_ids_and_partitions(manifest)
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


def load_paired_case_evidence(path: Path) -> PairedCaseEvidence:
    """Load paired case comparison evidence from an explicit JSON artifact."""

    if not path.is_file():
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING)

    raw_bytes = path.read_bytes()
    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None

    if not isinstance(data, dict):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)

    try:
        control_settings = tuple(
            ControlSettingEvidence(
                variable_key=item["variable_key"],
                baseline_hash=item["baseline_hash"],
                candidate_hash=item["candidate_hash"],
                final_hash=item["final_hash"],
            )
            for item in data.get("control_settings", [])
        )
        paired_delta_refs = tuple(ImmutableReference.model_validate(ref) for ref in data.get("paired_delta_refs", []))
        evidence = PairedCaseEvidence(
            receipt_id=data["receipt_id"],
            receipt_hash=data["receipt_hash"],
            baseline_case_ids=tuple(data.get("baseline_case_ids", [])),
            candidate_case_ids=tuple(data.get("candidate_case_ids", [])),
            final_case_ids=tuple(data.get("final_case_ids", [])),
            control_settings=control_settings,
            paired_delta_refs=paired_delta_refs,
        )
    except KeyError:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None

    if paired_case_manifest_hash(evidence) != evidence.receipt_hash:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    return evidence


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
    paired_case_evidence_path: Path | None = None,
    dataset_manifest_path: Path | None = None,
) -> GateEvidence:
    """Load actual published evaluation artifacts and assemble GateEvidence."""

    bundle = load_published_run_bundle(result_root, run_id)
    run = bundle.run
    if run.run_id != run_id:
        raise EvaluationValidationError(EvaluationErrorCode.BASELINE_ARTIFACT_INVALID)

    _validate_run_policy_bindings(run, policy)
    _validate_run_dataset_and_partitions(run, dataset_manifest_path)

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

    paired_case: PairedCaseEvidence | None = None
    if paired_case_evidence_path is not None:
        paired_case = load_paired_case_evidence(paired_case_evidence_path)

    return GateEvidence(
        run_id=run_id,
        required_scope_manifest_hash=policy.required_scope_manifest_hash,
        completed_experiment_types=(ExperimentType(run.experiment_type),),
        completed_partitions=tuple(Partition(p) for p in run.evaluated_partitions),
        metrics=metric_evidences,
        suites=suite_evidences,
        receipts=receipt_evidences,
        paired_case_evidence=paired_case,
    )
