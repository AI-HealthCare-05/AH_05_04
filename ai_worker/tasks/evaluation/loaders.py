from __future__ import annotations

import errno
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from pydantic import BaseModel, TypeAdapter, ValidationError

from ai_worker.tasks.evaluation.canonical import (
    JsonValue,
    canonical_json_bytes,
    canonical_sha256,
    normalize_resource_path,
    sha256_hex,
)
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.schema_registry import SCHEMA_REGISTRIES
from ai_worker.tasks.evaluation.schemas.authoring import (
    EVALUATION_CASE_ADAPTER,
    CriticalClaimRubric,
    DatasetManifest,
    EvaluationCase,
    EvidenceMappingEntry,
    EvidenceMappingManifest,
    EvidenceTargetKind,
    EvidenceType,
    ProtectedArtifactReceipt,
)
from ai_worker.tasks.evaluation.schemas.authoring_v1_1 import (
    EVALUATION_CASE_ADAPTER_V1_1,
    DatasetManifestV11,
    EvaluationCaseV11,
)
from ai_worker.tasks.evaluation.schemas.authoring_v1_2 import (
    EVALUATION_CASE_ADAPTER_V1_2,
    CriticalClaimRubricV12,
    DatasetManifestV12,
    EvaluationCaseV12,
    EvidenceMappingManifestV12,
    ProtectedArtifactReceiptV12,
)
from ai_worker.tasks.evaluation.schemas.common import (
    ContentClassification,
    ImmutableReference,
    Partition,
    TeamGoldStatus,
)
from ai_worker.tasks.evaluation.schemas.policy import (
    ComparisonPolicy,
    EvaluationPolicy,
    EvaluationProfile,
    SuiteDefinition,
)
from ai_worker.tasks.evaluation.schemas.policy_v1_2 import (
    EvaluationPolicyV12,
    EvaluationProfileV12,
    SuiteDefinitionV12,
)

type DatasetManifestContract = DatasetManifest | DatasetManifestV11 | DatasetManifestV12
type EvaluationCaseContract = EvaluationCase | EvaluationCaseV11 | EvaluationCaseV12
type EvidenceMappingContract = EvidenceMappingManifest | EvidenceMappingManifestV12
type CriticalClaimRubricContract = CriticalClaimRubric | CriticalClaimRubricV12
type EvaluationProfileContract = EvaluationProfile | EvaluationProfileV12
type EvaluationPolicyContract = EvaluationPolicy | EvaluationPolicyV12
type SuiteDefinitionContract = SuiteDefinition | SuiteDefinitionV12
type ProtectedArtifactReceiptContract = ProtectedArtifactReceipt | ProtectedArtifactReceiptV12


class _SchemaVersioned(Protocol):
    @property
    def schema_version(self) -> str: ...


class _DuplicateKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedReference:
    kind: str
    id: str
    version: str
    hash: str
    resolved: Literal[True] = True


@dataclass(frozen=True, slots=True)
class ValidatedDataset:
    manifest: DatasetManifestContract
    cases: tuple[EvaluationCaseContract, ...]
    evidence_mapping: EvidenceMappingContract
    rubric: CriticalClaimRubricContract
    profile: EvaluationProfileContract
    comparison_policy: ComparisonPolicy
    evaluation_policy: EvaluationPolicyContract
    suite: SuiteDefinitionContract
    protected_artifact_receipt: ProtectedArtifactReceiptContract | None
    reference_graph: tuple[ResolvedReference, ...]
    resource_hashes: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class _JsonSnapshot:
    path: Path
    relative_path: str
    raw_bytes: bytes
    value: dict[str, JsonValue]

    @property
    def file_sha256(self) -> str:
        return sha256_hex(self.raw_bytes)


@dataclass(frozen=True, slots=True)
class _AuthoringContract:
    manifest_model: type[BaseModel]
    case_adapter: TypeAdapter[Any]
    evidence_mapping_model: type[BaseModel]
    rubric_model: type[BaseModel]
    profile_model: type[BaseModel]
    evaluation_policy_model: type[BaseModel]
    suite_model: type[BaseModel]
    protected_artifact_receipt_model: type[BaseModel]


_AUTHORING_CONTRACTS = {
    "1.0.0": _AuthoringContract(
        DatasetManifest,
        EVALUATION_CASE_ADAPTER,
        EvidenceMappingManifest,
        CriticalClaimRubric,
        EvaluationProfile,
        EvaluationPolicy,
        SuiteDefinition,
        ProtectedArtifactReceipt,
    ),
    "1.1.0": _AuthoringContract(
        DatasetManifestV11,
        EVALUATION_CASE_ADAPTER_V1_1,
        EvidenceMappingManifest,
        CriticalClaimRubric,
        EvaluationProfile,
        EvaluationPolicy,
        SuiteDefinition,
        ProtectedArtifactReceipt,
    ),
    "1.2.0": _AuthoringContract(
        DatasetManifestV12,
        EVALUATION_CASE_ADAPTER_V1_2,
        EvidenceMappingManifestV12,
        CriticalClaimRubricV12,
        EvaluationProfileV12,
        EvaluationPolicyV12,
        SuiteDefinitionV12,
        ProtectedArtifactReceiptV12,
    ),
}


class _SnapshotReader:
    def __init__(self, root: Path) -> None:
        root_absolute = root.absolute()
        _reject_symlink_components(root_absolute)
        self.root = root_absolute
        self._cache: dict[Path, _JsonSnapshot] = {}

    def path(self, relative_path: str) -> Path:
        normalized = normalize_resource_path(relative_path)
        return safe_path_under_root(self.root, self.root / normalized)

    def read_path(self, path: Path) -> _JsonSnapshot:
        safe_path = safe_path_under_root(self.root, path)
        cached = self._cache.get(safe_path)
        if cached is not None:
            return cached
        try:
            raw_bytes = safe_path.read_bytes()
        except OSError as error:
            if error.errno == errno.ENOENT:
                raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING) from error
            if error.errno in {errno.ENOTDIR, errno.EISDIR, errno.EACCES, errno.EPERM, errno.ELOOP}:
                raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
            raise
        value = parse_json_object_bytes(raw_bytes)
        relative = safe_path.relative_to(self.root).as_posix()
        snapshot = _JsonSnapshot(safe_path, relative, raw_bytes, value)
        self._cache[safe_path] = snapshot
        return snapshot

    def seed_path(self, path: Path, raw_bytes: bytes) -> _JsonSnapshot:
        safe_path = safe_path_under_root(self.root, path)
        relative = safe_path.relative_to(self.root).as_posix()
        snapshot = _JsonSnapshot(safe_path, relative, raw_bytes, parse_json_object_bytes(raw_bytes))
        self._cache[safe_path] = snapshot
        return snapshot

    def read(self, relative_path: str) -> _JsonSnapshot:
        return self.read_path(self.path(relative_path))

    @property
    def resource_hashes(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((item.relative_path, item.file_sha256) for item in self._cache.values()))


def _reject_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def parse_json_object_bytes(raw_bytes: bytes) -> dict[str, JsonValue]:
    try:
        decoded = raw_bytes.decode("utf-8", errors="strict")
        value = json.loads(decoded, object_pairs_hook=_reject_duplicate_keys)
    except _DuplicateKeyError as error:
        raise EvaluationValidationError(EvaluationErrorCode.JSON_DUPLICATE_KEY) from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise EvaluationValidationError(EvaluationErrorCode.JSON_INVALID) from error
    if type(value) is not dict:
        raise EvaluationValidationError(EvaluationErrorCode.JSON_INVALID)
    try:
        canonical_json_bytes(cast(JsonValue, value))
    except EvaluationValidationError as error:
        if error.code in {
            EvaluationErrorCode.JSON_NUMBER_INVALID,
            EvaluationErrorCode.JSON_UNICODE_INVALID,
            EvaluationErrorCode.JSON_TYPE_INVALID,
        }:
            raise EvaluationValidationError(EvaluationErrorCode.JSON_INVALID) from error
        raise
    return cast(dict[str, JsonValue], value)


def safe_path_under_root(root: Path, path: Path) -> Path:
    root_absolute = Path(os.path.abspath(root))
    path_absolute = Path(os.path.abspath(path))
    try:
        relative = path_absolute.relative_to(root_absolute)
    except ValueError as error:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
    current = root_absolute
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    return path_absolute


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)


def _model_error_code(
    model_name: str,
    details: list[dict[str, object]],
) -> EvaluationErrorCode | None:
    messages = tuple(str(cast(dict[str, object], item.get("ctx", {})).get("error", "")) for item in details)
    if model_name == "ValidationReceipt":
        return EvaluationErrorCode.STATE_COMBINATION_INVALID
    if model_name in {"EvidenceMappingManifest", "EvidenceMappingManifestV12"} and any(
        item["loc"] == () for item in details
    ):
        return EvaluationErrorCode.EVIDENCE_MAPPING_INVALID
    if model_name in {
        "EvaluationProfile",
        "EvaluationProfileV12",
        "ComparisonPolicy",
        "EvaluationPolicy",
        "EvaluationPolicyV12",
        "SuiteDefinition",
        "SuiteDefinitionV12",
    }:
        return EvaluationErrorCode.MANIFEST_INVALID
    if model_name in {"DatasetManifest", "DatasetManifestV11", "DatasetManifestV12"}:
        if any("Case resources must be unique" in message for message in messages):
            return EvaluationErrorCode.CASE_DUPLICATE
        if any("approved deidentified data requires" in message for message in messages):
            return EvaluationErrorCode.DEIDENTIFICATION_APPROVAL_REQUIRED
        if any(
            "author, reviewer, and approver" in message or "approval" in message or "provenance" in message
            for message in messages
        ):
            return EvaluationErrorCode.REVIEW_PROVENANCE_INVALID
    return None


def _schema_error_code(error: ValidationError, model: type[BaseModel] | None = None) -> EvaluationErrorCode:
    details = cast(list[dict[str, object]], error.errors(include_input=False))
    model_code = _model_error_code("" if model is None else model.__name__, details)
    if model_code is not None:
        return model_code
    invalid_locations = {
        str(cast(tuple[object, ...], item["loc"])[-1]) for item in details if item["loc"] and item["type"] != "missing"
    }
    if invalid_locations & {
        "sha256",
        "manifest_sha256",
        "rubric_hash",
        "snapshot_hash",
        "receipt_hash",
        "resource_set_hash",
        "input_sha256",
        "hash",
        "evaluation_profile_hash",
        "comparison_policy_hash",
        "evaluation_policy_hash",
        "suite_hash",
    }:
        return EvaluationErrorCode.HASH_INVALID
    if invalid_locations & {"path", "resource_path"}:
        return EvaluationErrorCode.RESOURCE_PATH_INVALID
    if "partition" in invalid_locations:
        return EvaluationErrorCode.PARTITION_INVALID
    if invalid_locations & {"execution_status", "decision_status"}:
        return EvaluationErrorCode.STATE_COMBINATION_INVALID
    return EvaluationErrorCode.SCHEMA_INVALID


def _validate_model[T: BaseModel](snapshot: _JsonSnapshot, model: type[T]) -> T:
    try:
        validated = model.model_validate(snapshot.value)
    except ValidationError as error:
        raise EvaluationValidationError(_schema_error_code(error, model)) from None
    _validate_privacy(validated.model_dump(mode="json"))
    return validated


def _authoring_contract(snapshot: _JsonSnapshot) -> _AuthoringContract:
    if snapshot.value.get("schema_id") != "rag-eval.dataset-manifest":
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)
    schema_version = snapshot.value.get("schema_version")
    if not isinstance(schema_version, str):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)
    contract = _AUTHORING_CONTRACTS.get(schema_version)
    if contract is None:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)
    return contract


def _validate_case(snapshot: _JsonSnapshot, adapter: TypeAdapter[Any]) -> EvaluationCaseContract:
    try:
        validated = adapter.validate_python(snapshot.value)
    except ValidationError as error:
        raise EvaluationValidationError(_schema_error_code(error)) from None
    _validate_privacy(validated.model_dump(mode="json"))
    return cast(EvaluationCaseContract, validated)


def _validate_privacy(value: JsonValue) -> None:
    try:
        validate_privacy_boundary(value)
    except EvaluationValidationError as error:
        if error.code is EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN:
            raise EvaluationValidationError(EvaluationErrorCode.PRIVACY_VALUE_DETECTED, error.safe_path) from None
        raise


def load_json_object[T: BaseModel](path: Path, model: type[T]) -> T:
    root = path.absolute().parent
    snapshot = _SnapshotReader(root).read_path(path)
    return _validate_model(snapshot, model)


def _verify_self_hash(model: BaseModel, field: str) -> None:
    payload = cast(dict[str, JsonValue], model.model_dump(mode="json"))
    expected = cast(str, payload[field])
    if canonical_sha256(payload, excluded_top_level_keys=frozenset({field})) != expected:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)


def _verify_file_hash(snapshot: _JsonSnapshot, expected: str) -> None:
    if snapshot.file_sha256 != expected:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)


def _prefix(manifest_path: Path) -> str:
    return manifest_path.name.removesuffix(".dataset.json")


def _resource_set_hash(manifest: DatasetManifestContract) -> str:
    resources: list[JsonValue] = [
        {"partition": item.partition.value, "path": item.path, "sha256": item.sha256}
        for item in manifest.case_resources
    ]
    return canonical_sha256({"resources": resources})


def _partition_reference_hash(manifest: DatasetManifestContract, partition: Partition) -> str:
    resources: list[JsonValue] = [
        {"case_id": item.case_id, "path": item.path, "sha256": item.sha256}
        for item in manifest.case_resources
        if item.partition is partition
    ]
    return canonical_sha256({"partition": partition.value, "resources": resources})


def _case_set_hash(cases: tuple[EvaluationCaseContract, ...]) -> str:
    return canonical_sha256({"case_ids": [case.case_id for case in cases]})


def _expected_evidence_refs(case: EvaluationCaseContract) -> tuple[str, ...]:
    expected = case.expected
    values: list[str] = []
    for field in (
        "relevant_evidence_refs",
        "required_evidence_refs",
        "expected_rule_ids",
    ):
        items = getattr(expected, field)
        if items is not None:
            values.extend(items)
    citations = expected.expected_citations
    if citations is not None:
        values.extend(item.evidence_ref_id for item in citations)
    claims = expected.gold_claims
    if claims is not None:
        for claim in claims:
            values.extend(claim.supporting_evidence_ref_ids)
    return tuple(values)


def _validate_case_evidence_references(
    case: EvaluationCaseContract,
    by_id: dict[str, EvidenceMappingEntry],
) -> None:
    if not set(_expected_evidence_refs(case)).issubset(by_id):
        raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
    citations = case.expected.expected_citations
    if citations is not None and any(by_id[item.evidence_ref_id].locator != item.locator for item in citations):
        raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
    rule_ids = case.expected.expected_rule_ids
    if rule_ids is not None and any(
        by_id[item].evidence_type is not EvidenceType.INTERACTION_RULE for item in rule_ids
    ):
        raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)


def _validate_cases(
    reader: _SnapshotReader,
    manifest: DatasetManifestContract,
    case_adapter: TypeAdapter[Any],
) -> tuple[EvaluationCaseContract, ...]:
    cases: list[EvaluationCaseContract] = []
    ids: set[str] = set()
    paths: set[str] = set()
    for resource in manifest.case_resources:
        if resource.case_id in ids or resource.path in paths:
            raise EvaluationValidationError(EvaluationErrorCode.CASE_DUPLICATE)
        ids.add(resource.case_id)
        paths.add(resource.path)
        snapshot = reader.read(resource.path)
        case = _validate_case(snapshot, case_adapter)
        _verify_file_hash(snapshot, resource.sha256)
        if case.partition is not resource.partition:
            raise EvaluationValidationError(EvaluationErrorCode.PARTITION_COUNT_MISMATCH)
        if (
            case.case_id != resource.case_id
            or case.dataset_code != manifest.dataset_code
            or case.dataset_version != manifest.dataset_version
            or case.data_classification is not manifest.data_classification
            or case.critical_claim_rubric_ref != manifest.critical_claim_rubric_ref
        ):
            raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
        input_value: JsonValue = {
            "query": case.query,
            "context": cast(JsonValue, case.context.model_dump(mode="json")),
        }
        if case.input_sha256 != canonical_sha256(input_value):
            raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
        cases.append(case)
    actual_counts = Counter(case.partition.value for case in cases)
    stored_counts = manifest.partition_counts.model_dump(mode="json")
    if any(actual_counts[partition.value] != stored_counts[partition.value] for partition in Partition):
        raise EvaluationValidationError(EvaluationErrorCode.PARTITION_COUNT_MISMATCH)
    return tuple(cases)


def _validate_leakage(cases: tuple[EvaluationCaseContract, ...]) -> None:
    for axis in ("question_template", "source_segment", "medication_family", "transform_origin"):
        partitions_by_value: defaultdict[str, set[Partition]] = defaultdict(set)
        for case in cases:
            partitions_by_value[getattr(case.leakage_group_ids, axis)].add(case.partition)
        if any(len(partitions) > 1 for partitions in partitions_by_value.values()):
            raise EvaluationValidationError(EvaluationErrorCode.LEAKAGE_CROSS_PARTITION)


def _load_evidence(
    reader: _SnapshotReader,
    prefix: str,
    manifest: DatasetManifestContract,
    cases: tuple[EvaluationCaseContract, ...],
    evidence_mapping_model: type[BaseModel],
) -> tuple[EvidenceMappingContract, dict[tuple[str, str, str], str]]:
    snapshot = reader.read(f"retrieval/evidence/{prefix}.evidence-mapping.json")
    evidence = cast(EvidenceMappingContract, _validate_model(snapshot, evidence_mapping_model))
    _verify_self_hash(evidence, "manifest_sha256")
    if evidence.manifest_sha256 != manifest.evidence_mapping_manifest_sha256:
        raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
    by_id = {item.evidence_ref_id: item for item in evidence.entries}
    if len(by_id) != len(evidence.entries):
        raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
    for case in cases:
        _validate_case_evidence_references(case, by_id)
    registry: dict[tuple[str, str, str], str] = {
        (evidence.mapping_id, evidence.mapping_version, evidence.manifest_sha256): "CORPUS_SNAPSHOT"
    }
    evidence_kinds = {
        EvidenceType.PRESCRIPTION: "PRESCRIPTION",
        EvidenceType.KNOWLEDGE_CHUNK: "KNOWLEDGE_INDEX",
        EvidenceType.INTERACTION_RULE: "RULE_SET",
        EvidenceType.LIFESTYLE_GUIDELINE: "GUIDELINE_SET",
        EvidenceType.SAFETY_POLICY: "SAFETY_POLICY_SET",
    }
    for entry in evidence.entries:
        registry[(entry.stable_key, entry.source_version, entry.content_sha256)] = evidence_kinds[entry.evidence_type]
        if entry.target_kind is EvidenceTargetKind.RUNTIME_TYPED_REF:
            raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
        if entry.fixture_record_ref is None:
            raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
        resource = reader.read(entry.fixture_record_ref.path)
        _verify_file_hash(resource, entry.fixture_record_ref.sha256)
        if resource.file_sha256 != entry.content_sha256:
            raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
        _validate_privacy(resource.value)
    corpus_ref = ImmutableReference(
        id=evidence.mapping_id,
        version=evidence.mapping_version,
        hash=evidence.manifest_sha256,
    )
    if manifest.evaluation_corpus_snapshot_ref != corpus_ref:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    return evidence, registry


def _load_rubric(
    reader: _SnapshotReader,
    prefix: str,
    manifest: DatasetManifestContract,
    cases: tuple[EvaluationCaseContract, ...],
    rubric_model: type[BaseModel],
) -> CriticalClaimRubricContract:
    snapshot = reader.read(f"retrieval/manifests/{prefix}.critical-claim-rubric.json")
    rubric = cast(CriticalClaimRubricContract, _validate_model(snapshot, rubric_model))
    _verify_self_hash(rubric, "rubric_hash")
    expected_ref = ImmutableReference(id=rubric.rubric_id, version=rubric.rubric_version, hash=rubric.rubric_hash)
    if manifest.critical_claim_rubric_ref != expected_ref:
        raise EvaluationValidationError(EvaluationErrorCode.RUBRIC_MISMATCH)
    if any(case.critical_claim_rubric_ref != expected_ref for case in cases):
        raise EvaluationValidationError(EvaluationErrorCode.RUBRIC_MISMATCH)
    if {case.task_type for case in cases} != set(rubric.applicable_task_types):
        raise EvaluationValidationError(EvaluationErrorCode.RUBRIC_MISMATCH)
    reason_codes = {item.reason_code for item in rubric.reason_code_catalog}
    scope_codes = set(rubric.applicable_scope_codes)
    for case in cases:
        forbidden_claims = case.expected.forbidden_claims
        if forbidden_claims is not None and any(item.reason_code not in reason_codes for item in forbidden_claims):
            raise EvaluationValidationError(EvaluationErrorCode.RUBRIC_MISMATCH)
        expected_scope_codes = case.expected.expected_scope_codes
        if expected_scope_codes is not None and not set(expected_scope_codes).issubset(scope_codes):
            raise EvaluationValidationError(EvaluationErrorCode.RUBRIC_MISMATCH)
    return rubric


def _load_receipt(
    reader: _SnapshotReader,
    prefix: str,
    manifest: DatasetManifestContract,
    protected_artifact_receipt_model: type[BaseModel],
) -> ProtectedArtifactReceiptContract:
    snapshot = reader.read(f"provenance/{prefix}.protected-artifact-receipt.json")
    receipt = cast(ProtectedArtifactReceiptContract, _validate_model(snapshot, protected_artifact_receipt_model))
    _verify_self_hash(receipt, "receipt_hash")
    expected = ImmutableReference(id=receipt.receipt_id, version=receipt.receipt_version, hash=snapshot.file_sha256)
    if manifest.protected_artifact_receipt_ref != expected:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    if (
        receipt.dataset_code != manifest.dataset_code
        or receipt.dataset_version != manifest.dataset_version
        or receipt.data_classification is not manifest.data_classification
        or receipt.resource_set_hash != manifest.resource_set_hash
        or receipt.artifact_paths != tuple(item.path for item in manifest.case_resources)
    ):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    return receipt


def _schema_set_hash(reader: _SnapshotReader, schema_set_version: str) -> str:
    entries: list[dict[str, str]] = []
    registry = SCHEMA_REGISTRIES.get(schema_set_version)
    if registry is None:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)
    schema_root = reader.root / "schemas" / schema_set_version
    actual_paths = (
        {path.relative_to(schema_root).as_posix() for path in schema_root.rglob("*.json")}
        if schema_root.exists()
        else set()
    )
    expected_paths = {entry.relative_path for entry in registry}
    if actual_paths != expected_paths:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)
    for registry_entry in registry:
        snapshot = reader.read(f"schemas/{schema_set_version}/{registry_entry.relative_path}")
        schema_urn = snapshot.value.get("$id")
        if schema_urn != registry_entry.urn:
            raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)
        entries.append(
            {
                "schema_id": registry_entry.schema_id,
                "schema_version": registry_entry.member_version,
                "schema_sha256": snapshot.file_sha256,
            }
        )
    entries.sort(key=lambda item: (item["schema_id"], item["schema_version"], item["schema_sha256"]))
    return canonical_sha256(cast(JsonValue, {"schemas": entries}))


def _load_configuration(
    reader: _SnapshotReader,
    prefix: str,
    authoring: _AuthoringContract,
) -> tuple[EvaluationProfileContract, ComparisonPolicy, EvaluationPolicyContract, SuiteDefinitionContract]:
    profile = cast(
        EvaluationProfileContract,
        _validate_model(reader.read(f"profiles/{prefix}.profile.json"), authoring.profile_model),
    )
    comparison = _validate_model(
        reader.read(f"policies/{prefix}.comparison-policy.json"),
        ComparisonPolicy,
    )
    policy = cast(
        EvaluationPolicyContract,
        _validate_model(reader.read(f"policies/{prefix}.evaluation-policy.json"), authoring.evaluation_policy_model),
    )
    suite = cast(
        SuiteDefinitionContract,
        _validate_model(reader.read(f"suites/{prefix}.suite.json"), authoring.suite_model),
    )
    for model, field in (
        (profile, "evaluation_profile_hash"),
        (comparison, "comparison_policy_hash"),
        (policy, "evaluation_policy_hash"),
        (suite, "suite_hash"),
    ):
        _verify_self_hash(model, field)
    return profile, comparison, policy, suite


def _resolve_reference(
    reference: ImmutableReference,
    expected_kind: str,
    registry: dict[tuple[str, str, str], str],
    graph: list[ResolvedReference],
    graph_kind: str | None = None,
) -> None:
    actual_kind = registry.get((reference.id, reference.version, reference.hash))
    if actual_kind != expected_kind:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    graph.append(ResolvedReference(graph_kind or expected_kind, reference.id, reference.version, reference.hash))


def _validate_reference_graph(
    manifest: DatasetManifestContract,
    cases: tuple[EvaluationCaseContract, ...],
    evidence_registry: dict[tuple[str, str, str], str],
    profile: EvaluationProfileContract,
    comparison: ComparisonPolicy,
    policy: EvaluationPolicyContract,
    suite: SuiteDefinitionContract,
    schema_set_hash: str,
    schema_set_version: str,
) -> tuple[ResolvedReference, ...]:
    registry = dict(evidence_registry)
    registry[(profile.evaluation_profile_id, profile.evaluation_profile_version, profile.evaluation_profile_hash)] = (
        "PROFILE"
    )
    registry[
        (comparison.comparison_policy_id, comparison.comparison_policy_version, comparison.comparison_policy_hash)
    ] = "COMPARISON_POLICY"
    registry[(suite.suite_id, suite.suite_version, suite.suite_hash)] = "SUITE"
    registry[("rag-eval.schema-set", schema_set_version, schema_set_hash)] = "ARTIFACT_SCHEMA_SET"
    for partition in Partition:
        registry[
            (
                f"{manifest.dataset_code}:{partition.value}",
                manifest.dataset_version,
                _partition_reference_hash(manifest, partition),
            )
        ] = "PARTITION"
    graph: list[ResolvedReference] = []
    _resolve_reference(
        manifest.evaluation_corpus_snapshot_ref,
        "CORPUS_SNAPSHOT",
        registry,
        graph,
    )
    runtime_kinds = {
        "source_snapshot_ref": "CORPUS_SNAPSHOT",
        "knowledge_index_ref": "KNOWLEDGE_INDEX",
        "rule_set_ref": "RULE_SET",
        "guideline_set_ref": "GUIDELINE_SET",
        "safety_policy_set_ref": "SAFETY_POLICY_SET",
    }
    for case in cases:
        runtime = case.context.runtime_fixture
        if runtime is not None:
            for field, expected_kind in runtime_kinds.items():
                reference = getattr(runtime, field)
                if reference is not None:
                    _resolve_reference(
                        reference,
                        expected_kind,
                        registry,
                        graph,
                        f"CASE_{field.upper()}",
                    )
    for reference in profile.required_gate_refs:
        _resolve_reference(reference, "GATE", registry, graph, "PROFILE_GATE")
    for reference in profile.required_suite_refs:
        _resolve_reference(reference, "SUITE", registry, graph, "PROFILE_SUITE")
    for member in policy.members:
        _resolve_reference(member.reference, member.member_type.value, registry, graph)
    return tuple(graph)


def _validate_configuration_graph(
    manifest: DatasetManifestContract,
    cases: tuple[EvaluationCaseContract, ...],
    profile: EvaluationProfileContract,
    comparison: ComparisonPolicy,
    policy: EvaluationPolicyContract,
    suite: SuiteDefinitionContract,
) -> None:
    dataset_partitions = {
        partition for partition in Partition if getattr(manifest.partition_counts, partition.value) > 0
    }
    profile_partitions = set(profile.required_partitions)
    suite_partitions = set(suite.input_selector.partitions)
    policy_partitions: set[Partition] = set()
    prefix = f"{manifest.dataset_code}:"
    for member in policy.required_partition_refs:
        if not member.reference.id.startswith(prefix):
            raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
        try:
            policy_partitions.add(Partition(member.reference.id.removeprefix(prefix)))
        except ValueError:
            raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID) from None
    if not (dataset_partitions == profile_partitions == suite_partitions == policy_partitions):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)

    suite_ref = (suite.suite_id, suite.suite_version, suite.suite_hash)
    profile_suites = {(item.id, item.version, item.hash) for item in profile.required_suite_refs}
    policy_suites = {
        (item.reference.id, item.reference.version, item.reference.hash) for item in policy.required_suite_refs
    }
    if profile_suites != policy_suites or profile_suites != {suite_ref}:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)

    profile_gates = {(item.id, item.version, item.hash) for item in profile.required_gate_refs}
    policy_gates = {
        (item.reference.id, item.reference.version, item.reference.hash) for item in policy.required_gate_refs
    }
    if profile_gates != policy_gates:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)

    selected_partitions = {case.partition for case in cases}
    if any(
        scope.partition not in profile_partitions
        or scope.partition not in suite_partitions
        or scope.partition not in policy_partitions
        or scope.partition not in selected_partitions
        for scope in comparison.scopes
    ):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)


def _validate_frozen_gold_closure(
    manifest: DatasetManifestContract,
    cases: tuple[EvaluationCaseContract, ...],
    evidence: EvidenceMappingContract,
    rubric: CriticalClaimRubricContract,
) -> None:
    if not isinstance(manifest, DatasetManifestV11) or manifest.status.value != "FROZEN":
        return
    provenance = (
        *(case.review_provenance for case in cases),
        evidence.review_provenance,
        rubric.review_provenance,
    )
    if any(item.team_gold_status is not TeamGoldStatus.APPROVED for item in provenance):
        raise EvaluationValidationError(EvaluationErrorCode.REVIEW_PROVENANCE_INVALID)


def load_dataset(
    manifest_path: Path,
    *,
    evals_root: Path,
    manifest_bytes: bytes | None = None,
) -> ValidatedDataset:
    reader = _SnapshotReader(evals_root)
    manifest_snapshot = (
        reader.read_path(manifest_path) if manifest_bytes is None else reader.seed_path(manifest_path, manifest_bytes)
    )
    authoring = _authoring_contract(manifest_snapshot)
    manifest = cast(
        DatasetManifestContract,
        _validate_model(manifest_snapshot, authoring.manifest_model),
    )
    _verify_self_hash(manifest, "manifest_sha256")
    if manifest.resource_set_hash != _resource_set_hash(manifest):
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    prefix = _prefix(manifest_path)
    cases = _validate_cases(reader, manifest, authoring.case_adapter)
    _validate_leakage(cases)
    evidence, evidence_registry = _load_evidence(
        reader,
        prefix,
        manifest,
        cases,
        authoring.evidence_mapping_model,
    )
    rubric = _load_rubric(reader, prefix, manifest, cases, authoring.rubric_model)
    _validate_frozen_gold_closure(manifest, cases, evidence, rubric)
    receipt = (
        _load_receipt(reader, prefix, manifest, authoring.protected_artifact_receipt_model)
        if manifest.protected_artifact_receipt_ref is not None
        else None
    )
    profile, comparison, policy, suite = _load_configuration(reader, prefix, authoring)
    _validate_configuration_graph(manifest, cases, profile, comparison, policy, suite)
    if (
        suite.input_selector.dataset_code != manifest.dataset_code
        or suite.input_selector.dataset_version != manifest.dataset_version
    ):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    selected_cases = tuple(
        case
        for case in cases
        if case.partition in suite.input_selector.partitions and case.task_type in suite.input_selector.task_types
    )
    if (
        {case.partition for case in selected_cases} != set(suite.input_selector.partitions)
        or {case.task_type for case in selected_cases} != set(suite.input_selector.task_types)
        or suite.expected_case_set_hash != _case_set_hash(selected_cases)
    ):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    schema_set_version = policy.artifact_schema_set_ref.reference.version
    registry = SCHEMA_REGISTRIES.get(schema_set_version)
    if registry is None:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)
    member_versions = {entry.schema_id: entry.member_version for entry in registry}
    graph_members: list[tuple[str, _SchemaVersioned]] = [
        ("rag-eval.dataset-manifest", manifest),
        *(("rag-eval.case", case) for case in cases),
        ("rag-eval.evidence-mapping-manifest", evidence),
        ("rag-eval.critical-claim-rubric", rubric),
        ("rag-eval.evaluation-profile", profile),
        ("rag-eval.comparison-policy", comparison),
        ("rag-eval.evaluation-policy", policy),
        ("rag-eval.suite-definition", suite),
    ]
    if receipt is not None:
        graph_members.append(("rag-eval.protected-artifact-receipt", receipt))
    if any(member_versions.get(schema_id) != resource.schema_version for schema_id, resource in graph_members):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    schema_set_hash = _schema_set_hash(reader, schema_set_version)
    graph = _validate_reference_graph(
        manifest,
        cases,
        evidence_registry,
        profile,
        comparison,
        policy,
        suite,
        schema_set_hash,
        schema_set_version,
    )
    if manifest.data_classification is ContentClassification.APPROVED_DEIDENTIFIED:
        if manifest.deidentification_approval_receipt_ref is None:
            raise EvaluationValidationError(EvaluationErrorCode.DEIDENTIFICATION_APPROVAL_REQUIRED)
    return ValidatedDataset(
        manifest=manifest,
        cases=cases,
        evidence_mapping=evidence,
        rubric=rubric,
        profile=profile,
        comparison_policy=comparison,
        evaluation_policy=policy,
        suite=suite,
        protected_artifact_receipt=receipt,
        reference_graph=graph,
        resource_hashes=reader.resource_hashes,
    )
