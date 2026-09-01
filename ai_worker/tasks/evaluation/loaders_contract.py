from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import BaseModel, BeforeValidator, Field, ValidationError

from ai_worker.tasks.evaluation.canonical import (
    JsonValue,
    canonical_json_bytes,
    canonical_sha256,
    normalize_resource_path,
    sha256_hex,
)
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.schemas.authoring_contract import (
    EVALUATION_CASE_ADAPTER,
    CriticalClaimRubric,
    DatasetManifest,
    EvaluationCase,
    EvidenceMappingManifest,
    ProtectedArtifactReceipt,
    SyntheticToken,
)
from ai_worker.tasks.evaluation.schemas.common import (
    ContentClassification,
    ImmutableReference,
    Partition,
    SemanticVersion,
    Sha256Hex,
    StableId,
    StrictContractModel,
)
from ai_worker.tasks.evaluation.schemas.policy_contract import (
    ComparisonPolicy,
    EvaluationPolicy,
    EvaluationProfile,
    SuiteDefinition,
)


class _DuplicateKeyError(ValueError):
    pass


def _tuple_from_wire(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


class SyntheticCorpusSnapshot(StrictContractModel):
    schema_id: Literal["rag-eval.synthetic-corpus-snapshot"]
    schema_version: Literal["1.0.0"]
    snapshot_id: StableId
    snapshot_version: SemanticVersion
    reference_alias_ids: Annotated[tuple[StableId, ...], BeforeValidator(_tuple_from_wire), Field(min_length=1)]
    records: Annotated[tuple[SyntheticToken, ...], BeforeValidator(_tuple_from_wire), Field(min_length=1)]
    snapshot_hash: Sha256Hex


@dataclass(frozen=True, slots=True)
class ResolvedReference:
    kind: str
    id: str
    version: str
    hash: str
    resolved: Literal[True] = True


@dataclass(frozen=True, slots=True)
class ValidatedDataset:
    manifest: DatasetManifest
    cases: tuple[EvaluationCase, ...]
    evidence_mapping: EvidenceMappingManifest
    rubric: CriticalClaimRubric
    profile: EvaluationProfile
    comparison_policy: ComparisonPolicy
    evaluation_policy: EvaluationPolicy
    suite: SuiteDefinition
    protected_artifact_receipt: ProtectedArtifactReceipt
    corpus_snapshot: SyntheticCorpusSnapshot
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


class _SnapshotReader:
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()
        self._cache: dict[Path, _JsonSnapshot] = {}

    def path(self, relative_path: str) -> Path:
        normalized = normalize_resource_path(relative_path)
        return _safe_path(self.root, self.root / normalized)

    def read_path(self, path: Path) -> _JsonSnapshot:
        safe_path = _safe_path(self.root, path)
        cached = self._cache.get(safe_path)
        if cached is not None:
            return cached
        try:
            raw_bytes = safe_path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as error:
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING) from error
        value = _parse_json_object(raw_bytes)
        relative = safe_path.relative_to(self.root).as_posix()
        snapshot = _JsonSnapshot(safe_path, relative, raw_bytes, value)
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


def _parse_json_object(raw_bytes: bytes) -> dict[str, JsonValue]:
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


def _safe_path(root: Path, path: Path) -> Path:
    root_absolute = root.absolute()
    path_absolute = path.absolute()
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


def _model_error_code(
    model_name: str,
    details: list[dict[str, object]],
) -> EvaluationErrorCode | None:
    messages = tuple(str(cast(dict[str, object], item.get("ctx", {})).get("error", "")) for item in details)
    if model_name == "ValidationReceipt":
        return EvaluationErrorCode.STATE_COMBINATION_INVALID
    if model_name == "EvidenceMappingManifest" and any(item["loc"] == () for item in details):
        return EvaluationErrorCode.EVIDENCE_MAPPING_INVALID
    if model_name in {"EvaluationProfile", "ComparisonPolicy", "EvaluationPolicy", "SuiteDefinition"}:
        return EvaluationErrorCode.MANIFEST_INVALID
    if model_name == "DatasetManifest":
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


def _validate_case(snapshot: _JsonSnapshot) -> EvaluationCase:
    try:
        validated = EVALUATION_CASE_ADAPTER.validate_python(snapshot.value)
    except ValidationError as error:
        raise EvaluationValidationError(_schema_error_code(error)) from None
    _validate_privacy(validated.model_dump(mode="json"))
    return validated


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


def _resource_set_hash(manifest: DatasetManifest) -> str:
    resources: list[JsonValue] = [
        {"partition": item.partition.value, "path": item.path, "sha256": item.sha256}
        for item in manifest.case_resources
    ]
    return canonical_sha256({"resources": resources})


def _partition_reference_hash(manifest: DatasetManifest, partition: Partition) -> str:
    resources: list[JsonValue] = [
        {"case_id": item.case_id, "path": item.path, "sha256": item.sha256}
        for item in manifest.case_resources
        if item.partition is partition
    ]
    return canonical_sha256({"partition": partition.value, "resources": resources})


def _case_set_hash(cases: tuple[EvaluationCase, ...]) -> str:
    return canonical_sha256({"case_ids": [case.case_id for case in cases]})


def _expected_evidence_refs(case: EvaluationCase) -> tuple[str, ...]:
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


def _validate_cases(
    reader: _SnapshotReader,
    manifest: DatasetManifest,
) -> tuple[EvaluationCase, ...]:
    cases: list[EvaluationCase] = []
    ids: set[str] = set()
    paths: set[str] = set()
    for resource in manifest.case_resources:
        if resource.case_id in ids or resource.path in paths:
            raise EvaluationValidationError(EvaluationErrorCode.CASE_DUPLICATE)
        ids.add(resource.case_id)
        paths.add(resource.path)
        snapshot = reader.read(resource.path)
        _verify_file_hash(snapshot, resource.sha256)
        case = _validate_case(snapshot)
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


def _validate_leakage(cases: tuple[EvaluationCase, ...]) -> None:
    for axis in ("question_template", "source_segment", "medication_family", "transform_origin"):
        partitions_by_value: defaultdict[str, set[Partition]] = defaultdict(set)
        for case in cases:
            partitions_by_value[getattr(case.leakage_group_ids, axis)].add(case.partition)
        if any(len(partitions) > 1 for partitions in partitions_by_value.values()):
            raise EvaluationValidationError(EvaluationErrorCode.LEAKAGE_CROSS_PARTITION)


def _load_evidence(
    reader: _SnapshotReader,
    prefix: str,
    manifest: DatasetManifest,
    cases: tuple[EvaluationCase, ...],
) -> tuple[EvidenceMappingManifest, dict[tuple[str, str, str], str]]:
    snapshot = reader.read(f"retrieval/evidence/{prefix}.evidence-mapping.json")
    evidence = _validate_model(snapshot, EvidenceMappingManifest)
    _verify_self_hash(evidence, "manifest_sha256")
    if evidence.manifest_sha256 != manifest.evidence_mapping_manifest_sha256:
        raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
    by_id = {item.evidence_ref_id: item for item in evidence.entries}
    if len(by_id) != len(evidence.entries):
        raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
    for case in cases:
        if not set(_expected_evidence_refs(case)).issubset(by_id):
            raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
    registry: dict[tuple[str, str, str], str] = {}
    for entry in evidence.entries:
        registry[(entry.stable_key, entry.source_version, entry.content_sha256)] = "EVIDENCE"
        if entry.fixture_record_ref is not None:
            resource = reader.read(entry.fixture_record_ref.path)
            _verify_file_hash(resource, entry.fixture_record_ref.sha256)
            if resource.file_sha256 != entry.content_sha256:
                raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
            _validate_privacy(resource.value)
    return evidence, registry


def _load_rubric(
    reader: _SnapshotReader,
    prefix: str,
    manifest: DatasetManifest,
    cases: tuple[EvaluationCase, ...],
) -> CriticalClaimRubric:
    snapshot = reader.read(f"retrieval/manifests/{prefix}.critical-claim-rubric.json")
    rubric = _validate_model(snapshot, CriticalClaimRubric)
    _verify_self_hash(rubric, "rubric_hash")
    expected_ref = ImmutableReference(id=rubric.rubric_id, version=rubric.rubric_version, hash=rubric.rubric_hash)
    if manifest.critical_claim_rubric_ref != expected_ref:
        raise EvaluationValidationError(EvaluationErrorCode.RUBRIC_MISMATCH)
    if any(case.critical_claim_rubric_ref != expected_ref for case in cases):
        raise EvaluationValidationError(EvaluationErrorCode.RUBRIC_MISMATCH)
    if {case.task_type for case in cases} != set(rubric.applicable_task_types):
        raise EvaluationValidationError(EvaluationErrorCode.RUBRIC_MISMATCH)
    return rubric


def _load_snapshot(
    reader: _SnapshotReader,
    prefix: str,
    manifest: DatasetManifest,
) -> SyntheticCorpusSnapshot:
    snapshot = reader.read(f"retrieval/snapshots/{prefix}.corpus-snapshot.json")
    corpus = _validate_model(snapshot, SyntheticCorpusSnapshot)
    _verify_self_hash(corpus, "snapshot_hash")
    expected = ImmutableReference(id=corpus.snapshot_id, version=corpus.snapshot_version, hash=corpus.snapshot_hash)
    if manifest.evaluation_corpus_snapshot_ref != expected:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    return corpus


def _load_receipt(
    reader: _SnapshotReader,
    prefix: str,
    manifest: DatasetManifest,
) -> ProtectedArtifactReceipt:
    snapshot = reader.read(f"provenance/{prefix}.protected-artifact-receipt.json")
    receipt = _validate_model(snapshot, ProtectedArtifactReceipt)
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


def _schema_set_hash(reader: _SnapshotReader) -> str:
    entries: list[JsonValue] = []
    schema_root = reader.root / "schemas/1.0.0"
    for path in sorted(schema_root.rglob("*.json")):
        snapshot = reader.read_path(path)
        schema_id = snapshot.value.get("$id")
        if not isinstance(schema_id, str):
            raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)
        entries.append(
            {
                "schema_id": schema_id,
                "schema_version": "1.0.0",
                "schema_sha256": snapshot.file_sha256,
            }
        )
    return canonical_sha256({"schemas": entries})


def _load_configuration(
    reader: _SnapshotReader,
    prefix: str,
) -> tuple[EvaluationProfile, ComparisonPolicy, EvaluationPolicy, SuiteDefinition]:
    profile = _validate_model(reader.read(f"profiles/{prefix}.profile.json"), EvaluationProfile)
    comparison = _validate_model(
        reader.read(f"policies/{prefix}.comparison-policy.json"),
        ComparisonPolicy,
    )
    policy = _validate_model(reader.read(f"policies/{prefix}.evaluation-policy.json"), EvaluationPolicy)
    suite = _validate_model(reader.read(f"suites/{prefix}.suite.json"), SuiteDefinition)
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
    kind: str,
    registry: dict[tuple[str, str, str], str],
    graph: list[ResolvedReference],
) -> None:
    if (reference.id, reference.version, reference.hash) not in registry:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    graph.append(ResolvedReference(kind, reference.id, reference.version, reference.hash))


def _validate_reference_graph(
    manifest: DatasetManifest,
    cases: tuple[EvaluationCase, ...],
    evidence_registry: dict[tuple[str, str, str], str],
    corpus: SyntheticCorpusSnapshot,
    profile: EvaluationProfile,
    comparison: ComparisonPolicy,
    policy: EvaluationPolicy,
    suite: SuiteDefinition,
    schema_set_hash: str,
) -> tuple[ResolvedReference, ...]:
    registry = dict(evidence_registry)
    for alias in corpus.reference_alias_ids:
        registry[(alias, corpus.snapshot_version, corpus.snapshot_hash)] = "CORPUS_SNAPSHOT"
    registry[(profile.evaluation_profile_id, profile.evaluation_profile_version, profile.evaluation_profile_hash)] = (
        "PROFILE"
    )
    registry[
        (comparison.comparison_policy_id, comparison.comparison_policy_version, comparison.comparison_policy_hash)
    ] = "COMPARISON_POLICY"
    registry[(suite.suite_id, suite.suite_version, suite.suite_hash)] = "SUITE"
    registry[("rag-eval.schema-set", "1.0.0", schema_set_hash)] = "ARTIFACT_SCHEMA_SET"
    for partition in Partition:
        registry[
            (
                f"{manifest.dataset_code}:{partition.value}",
                manifest.dataset_version,
                _partition_reference_hash(manifest, partition),
            )
        ] = "PARTITION"
    graph: list[ResolvedReference] = []
    for case in cases:
        runtime = case.context.runtime_fixture
        if runtime is not None:
            for field in (
                "source_snapshot_ref",
                "knowledge_index_ref",
                "rule_set_ref",
                "guideline_set_ref",
                "safety_policy_set_ref",
            ):
                reference = getattr(runtime, field)
                if reference is not None:
                    _resolve_reference(reference, f"CASE_{field.upper()}", registry, graph)
    for reference in profile.required_gate_refs:
        _resolve_reference(reference, "PROFILE_GATE", registry, graph)
    for reference in profile.required_suite_refs:
        _resolve_reference(reference, "PROFILE_SUITE", registry, graph)
    for member in policy.members:
        _resolve_reference(member.reference, member.member_type.value, registry, graph)
    return tuple(graph)


def load_dataset(manifest_path: Path, *, evals_root: Path) -> ValidatedDataset:
    reader = _SnapshotReader(evals_root)
    manifest_snapshot = reader.read_path(manifest_path)
    manifest = _validate_model(manifest_snapshot, DatasetManifest)
    _verify_self_hash(manifest, "manifest_sha256")
    if manifest.resource_set_hash != _resource_set_hash(manifest):
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    prefix = _prefix(manifest_path)
    cases = _validate_cases(reader, manifest)
    _validate_leakage(cases)
    evidence, evidence_registry = _load_evidence(reader, prefix, manifest, cases)
    rubric = _load_rubric(reader, prefix, manifest, cases)
    corpus = _load_snapshot(reader, prefix, manifest)
    receipt = _load_receipt(reader, prefix, manifest)
    profile, comparison, policy, suite = _load_configuration(reader, prefix)
    if (
        suite.input_selector.dataset_code != manifest.dataset_code
        or suite.input_selector.dataset_version != manifest.dataset_version
    ):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    if suite.expected_case_set_hash != _case_set_hash(cases):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    schema_set_hash = _schema_set_hash(reader)
    graph = _validate_reference_graph(
        manifest,
        cases,
        evidence_registry,
        corpus,
        profile,
        comparison,
        policy,
        suite,
        schema_set_hash,
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
        corpus_snapshot=corpus,
        reference_graph=graph,
        resource_hashes=reader.resource_hashes,
    )
