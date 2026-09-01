from __future__ import annotations

# ruff: noqa: F401, F811, E402
# mypy: disable-error-code="arg-type, assignment, attr-defined, union-attr"
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ValidationError

from ai_worker.tasks.evaluation.canonical import (
    JsonValue,
    canonical_json_bytes,
    canonical_sha256,
    normalize_resource_path,
    sha256_hex,
)
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.schemas.authoring import (
    EVALUATION_CASE_ADAPTER,
    CriticalClaimRubric,
    DatasetManifest,
    EvaluationCase,
    EvidenceMappingManifest,
)
from ai_worker.tasks.evaluation.schemas.common import (
    ActorRole,
    ContentClassification,
    Partition,
    ReviewProvenance,
    TeamGoldStatus,
)
from ai_worker.tasks.evaluation.schemas.policy import (
    ComparisonPolicy,
    EvaluationPolicy,
    EvaluationProfile,
    SuiteDefinition,
)


class _DuplicateKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _LegacyValidatedDataset:
    manifest: DatasetManifest
    cases: tuple[EvaluationCase, ...]
    evidence_mapping: EvidenceMappingManifest
    rubric: CriticalClaimRubric
    profile: EvaluationProfile
    comparison_policy: ComparisonPolicy
    evaluation_policy: EvaluationPolicy
    suite: SuiteDefinition
    resource_hashes: tuple[tuple[str, str], ...]


def _reject_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError
        result[key] = value
    return result


def _read_json_object(path: Path) -> dict[str, JsonValue]:
    try:
        raw_bytes = path.read_bytes()
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as error:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING) from error
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


def _schema_error_code(
    error: ValidationError,
    model: type[BaseModel] | None = None,
    value: dict[str, JsonValue] | None = None,
) -> EvaluationErrorCode:
    details = error.errors(include_input=False)
    invalid_locations = {str(item["loc"][-1]) for item in details if item["loc"] and item["type"] != "missing"}
    if invalid_locations & {"sha256", "content_hash", "resource_hash", "hash", "input_hash"}:
        return EvaluationErrorCode.HASH_INVALID
    if "path" in invalid_locations or "resource_path" in invalid_locations:
        return EvaluationErrorCode.RESOURCE_PATH_INVALID
    if "partition" in invalid_locations:
        return EvaluationErrorCode.PARTITION_INVALID
    if invalid_locations & {
        "review_provenance",
        "authored_by",
        "reviewed_by",
        "approved_by",
        "proposed_by",
        "team_gold_status",
    }:
        return EvaluationErrorCode.REVIEW_PROVENANCE_INVALID
    if invalid_locations & {"deidentification_approval_receipt_ref", "content_classification"}:
        return EvaluationErrorCode.DEIDENTIFICATION_APPROVAL_REQUIRED
    if invalid_locations & {"execution_status", "decision_status"}:
        return EvaluationErrorCode.STATE_COMBINATION_INVALID
    contextual_code = _contextual_schema_error_code(model, value)
    return contextual_code or EvaluationErrorCode.SCHEMA_INVALID


def _contextual_schema_error_code(
    model: type[BaseModel] | None,
    value: dict[str, JsonValue] | None,
) -> EvaluationErrorCode | None:
    if model is DatasetManifest and value is not None:
        if (
            value.get("content_classification") == "APPROVED_DEIDENTIFIED"
            and value.get("deidentification_approval_receipt_ref") is None
        ):
            return EvaluationErrorCode.DEIDENTIFICATION_APPROVAL_REQUIRED
        if _review_provenance_invalid(
            value,
            allowed_approval_roles=frozenset({ActorRole.DATASET_CUSTODIAN}),
        ):
            return EvaluationErrorCode.REVIEW_PROVENANCE_INVALID
    if model is not None and model.__name__ == "ValidationReceipt" and value is not None:
        if "execution_status" in value and "decision_status" in value:
            return EvaluationErrorCode.STATE_COMBINATION_INVALID
    return None


def _review_provenance_invalid(
    value: dict[str, JsonValue],
    *,
    allowed_approval_roles: frozenset[ActorRole] | None = None,
) -> bool:
    raw_provenance = value.get("review_provenance")
    if not isinstance(raw_provenance, dict):
        return False
    try:
        provenance = ReviewProvenance.model_validate(raw_provenance)
    except ValidationError:
        return True
    return bool(
        allowed_approval_roles is not None
        and provenance.team_gold_status is TeamGoldStatus.APPROVED
        and (provenance.approved_by is None or provenance.approved_by.role not in allowed_approval_roles)
    )


_DIGEST_FIELDS = frozenset({"sha256", "content_hash", "resource_hash", "input_hash", "hash", "member_manifest_hash"})


def _privacy_view(value: JsonValue, key: str | None = None) -> JsonValue:
    if key in _DIGEST_FIELDS and isinstance(value, str):
        return "SYNTHETIC_DIGEST"
    if isinstance(value, dict):
        return {item_key: _privacy_view(item, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_privacy_view(item) for item in value]
    return value


def _legacy_load_json_object[T: BaseModel](path: Path, model: type[T]) -> T:
    value = _read_json_object(path)
    try:
        validate_privacy_boundary(_privacy_view(value))
        return model.model_validate(value)
    except EvaluationValidationError as error:
        if error.code is EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN:
            raise EvaluationValidationError(
                EvaluationErrorCode.PRIVACY_VALUE_DETECTED,
                error.safe_path,
            ) from error
        raise
    except ValidationError as error:
        raise EvaluationValidationError(_schema_error_code(error, model, value)) from None


def _safe_path(root: Path, path: Path) -> Path:
    root_absolute = root.absolute()
    path_absolute = path.absolute()
    try:
        path_absolute.relative_to(root_absolute)
    except ValueError as error:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
    relative = path_absolute.relative_to(root_absolute)
    current = root_absolute
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    return path_absolute


def _resource_path(root: Path, relative_path: str) -> Path:
    normalized = normalize_resource_path(relative_path)
    return _safe_path(root, root / normalized)


def _verify_file_hash(path: Path, expected: str) -> str:
    try:
        actual = sha256_hex(path.read_bytes())
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as error:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING) from error
    if actual != expected:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    return actual


def _verify_context_resources(root: Path, case: EvaluationCase) -> None:
    paths = [
        case.context.prescription_fixture,
        *case.context.medication_fixtures,
        case.context.patient_context_fixture,
        case.context.runtime_fixture,
    ]
    for relative_path in paths:
        if relative_path is None:
            continue
        path = _resource_path(root, relative_path)
        if not path.is_file():
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING)


def _verify_content_hash(value: BaseModel) -> None:
    payload = cast(dict[str, JsonValue], value.model_dump(mode="json"))
    expected = cast(str, payload["content_hash"])
    if canonical_sha256(payload, excluded_top_level_keys=frozenset({"content_hash"})) != expected:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)


def _load_case(path: Path) -> tuple[EvaluationCase, dict[str, JsonValue]]:
    value = _read_json_object(path)
    try:
        validate_privacy_boundary(_privacy_view(value))
        case = EVALUATION_CASE_ADAPTER.validate_python(value)
    except EvaluationValidationError as error:
        if error.code is EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN:
            raise EvaluationValidationError(
                EvaluationErrorCode.PRIVACY_VALUE_DETECTED,
                error.safe_path,
            ) from error
        raise
    except ValidationError as error:
        code = _schema_error_code(error)
        allowed_roles = None
        if value.get("task_type") in {"SAFETY", "END_TO_END_RAG"}:
            allowed_roles = frozenset({ActorRole.PRODUCT_SAFETY_REVIEWER, ActorRole.MEDICAL_REVIEWER})
        if code is EvaluationErrorCode.SCHEMA_INVALID and _review_provenance_invalid(
            value,
            allowed_approval_roles=allowed_roles,
        ):
            code = EvaluationErrorCode.REVIEW_PROVENANCE_INVALID
        raise EvaluationValidationError(code) from None
    return case, value


def _config_path(root: Path, manifest_path: Path, directory: str, suffix: str) -> Path:
    name = manifest_path.name.removesuffix(".dataset.json") + suffix
    return _safe_path(root, root / directory / name)


def _validate_unique_configuration(
    profile: EvaluationProfile,
    suite: SuiteDefinition,
    comparison_policy: ComparisonPolicy,
) -> None:
    profile_refs = [(ref.id, ref.version, ref.hash) for ref in profile.suite_references]
    if len(profile_refs) != len(set(profile_refs)):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    if len(suite.partitions) != len(set(suite.partitions)) or len(suite.task_types) != len(set(suite.task_types)):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    scope_keys = [
        (scope.metric_code, scope.metric_version, scope.partition, scope.slice_key)
        for scope in comparison_policy.scopes
    ]
    if len(scope_keys) != len(set(scope_keys)):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)


def _case_evidence_ids(case: EvaluationCase) -> set[str]:
    expected = case.expected
    ids: set[str] = set()
    for field in ("gold_evidence_ids", "gold_citation_evidence_ids", "gold_rule_ids"):
        values = getattr(expected, field)
        if values is not None:
            ids.update(values)
    return ids


def _has_duplicate_case_evidence_ids(case: EvaluationCase) -> bool:
    expected = case.expected
    for field in ("gold_evidence_ids", "gold_citation_evidence_ids", "gold_rule_ids"):
        values = getattr(expected, field)
        if values is not None and len(values) != len(set(values)):
            return True
    return False


def _case_claim_ids(case: EvaluationCase) -> set[str]:
    claims = case.expected.gold_claims
    return set() if claims is None else set(claims)


def _load_cases(
    root: Path,
    manifest: DatasetManifest,
) -> tuple[list[EvaluationCase], list[tuple[str, str]]]:
    case_ids: set[str] = set()
    case_paths: set[str] = set()
    cases: list[EvaluationCase] = []
    resource_hashes: list[tuple[str, str]] = []
    declared_counts = Counter(resource.partition for resource in manifest.case_resources)
    for resource in manifest.case_resources:
        if resource.case_id in case_ids or resource.path in case_paths:
            raise EvaluationValidationError(EvaluationErrorCode.CASE_DUPLICATE)
        case_ids.add(resource.case_id)
        case_paths.add(resource.path)
        case_path = _resource_path(root, resource.path)
        resource_hashes.append((resource.path, _verify_file_hash(case_path, resource.sha256)))
        case, raw_case = _load_case(case_path)
        if case.partition != resource.partition:
            raise EvaluationValidationError(EvaluationErrorCode.PARTITION_COUNT_MISMATCH)
        if (
            case.case_id != resource.case_id
            or case.task_type != resource.task_type
            or case.dataset_code != manifest.dataset_code
            or case.dataset_version != manifest.dataset_version
            or case.content_classification != manifest.content_classification
        ):
            raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
        if _has_duplicate_case_evidence_ids(case):
            raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
        _verify_context_resources(root, case)
        expected_input_hash = canonical_sha256({"question": raw_case["question"], "context": raw_case["context"]})
        if case.input_hash != expected_input_hash:
            raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
        cases.append(case)
    if Counter(case.partition for case in cases) != declared_counts:
        raise EvaluationValidationError(EvaluationErrorCode.PARTITION_COUNT_MISMATCH)
    return cases, resource_hashes


def _validate_leakage(cases: list[EvaluationCase]) -> None:
    for axis in ("question_template", "source_segment", "medication_family", "transform_origin"):
        partitions_by_value: defaultdict[str, set[Partition]] = defaultdict(set)
        for case in cases:
            partitions_by_value[case.leakage_groups[axis]].add(case.partition)
        if any(len(partitions) > 1 for partitions in partitions_by_value.values()):
            raise EvaluationValidationError(EvaluationErrorCode.LEAKAGE_CROSS_PARTITION)


def _load_evidence_mapping(
    root: Path,
    manifest: DatasetManifest,
    cases: list[EvaluationCase],
) -> tuple[EvidenceMappingManifest, list[tuple[str, str]]]:
    evidence_path = _resource_path(root, manifest.evidence_mapping.path)
    resource_hashes = [
        (
            manifest.evidence_mapping.path,
            _verify_file_hash(evidence_path, manifest.evidence_mapping.sha256),
        )
    ]
    try:
        evidence_mapping = _legacy_load_json_object(evidence_path, EvidenceMappingManifest)
    except EvaluationValidationError as error:
        if error.code is EvaluationErrorCode.SCHEMA_INVALID:
            raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID) from None
        raise
    _verify_content_hash(evidence_mapping)
    if (
        evidence_mapping.dataset_code != manifest.dataset_code
        or evidence_mapping.dataset_version != manifest.dataset_version
    ):
        raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
    mapped_ids = {entry.evidence_id for entry in evidence_mapping.evidence}
    mapped_paths = [entry.resource_path for entry in evidence_mapping.evidence]
    if len(mapped_paths) != len(set(mapped_paths)):
        raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
    if not set().union(*(_case_evidence_ids(case) for case in cases)).issubset(mapped_ids):
        raise EvaluationValidationError(EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)
    for entry in evidence_mapping.evidence:
        evidence_resource = _resource_path(root, entry.resource_path)
        resource_hashes.append((entry.resource_path, _verify_file_hash(evidence_resource, entry.resource_hash)))
    return evidence_mapping, resource_hashes


def _load_rubric(
    root: Path,
    manifest: DatasetManifest,
    cases: list[EvaluationCase],
) -> tuple[CriticalClaimRubric, tuple[str, str]]:
    rubric_path = _resource_path(root, manifest.critical_claim_rubric.path)
    resource_hash = (
        manifest.critical_claim_rubric.path,
        _verify_file_hash(rubric_path, manifest.critical_claim_rubric.sha256),
    )
    rubric = _legacy_load_json_object(rubric_path, CriticalClaimRubric)
    _verify_content_hash(rubric)
    claim_ids = set().union(*(_case_claim_ids(case) for case in cases))
    if (
        rubric.dataset_code != manifest.dataset_code
        or rubric.dataset_version != manifest.dataset_version
        or set(rubric.critical_claim_keys) != claim_ids
    ):
        raise EvaluationValidationError(EvaluationErrorCode.RUBRIC_MISMATCH)
    return rubric, resource_hash


@dataclass(frozen=True, slots=True)
class _ValidatedConfiguration:
    profile: EvaluationProfile
    comparison_policy: ComparisonPolicy
    evaluation_policy: EvaluationPolicy
    suite: SuiteDefinition
    resource_hashes: tuple[tuple[str, str], ...]


def _load_configuration(root: Path, manifest_path: Path) -> _ValidatedConfiguration:
    profile_path = _config_path(root, manifest_path, "profiles", ".profile.json")
    comparison_path = _config_path(root, manifest_path, "policies", ".comparison-policy.json")
    policy_path = _config_path(root, manifest_path, "policies", ".evaluation-policy.json")
    suite_path = _config_path(root, manifest_path, "suites", ".suite.json")
    profile = _legacy_load_json_object(profile_path, EvaluationProfile)
    comparison_policy = _legacy_load_json_object(comparison_path, ComparisonPolicy)
    evaluation_policy = _legacy_load_json_object(policy_path, EvaluationPolicy)
    suite = _legacy_load_json_object(suite_path, SuiteDefinition)
    paths_and_models = (
        (profile_path, profile),
        (comparison_path, comparison_policy),
        (policy_path, evaluation_policy),
        (suite_path, suite),
    )
    for _, config in paths_and_models:
        _verify_content_hash(config)
    _validate_unique_configuration(profile, suite, comparison_policy)
    _validate_configuration_references(profile, suite, comparison_policy, evaluation_policy)
    return _ValidatedConfiguration(
        profile=profile,
        comparison_policy=comparison_policy,
        evaluation_policy=evaluation_policy,
        suite=suite,
        resource_hashes=tuple(
            (str(path.relative_to(root)), sha256_hex(path.read_bytes())) for path, _ in paths_and_models
        ),
    )


def _validate_configuration_references(
    profile: EvaluationProfile,
    suite: SuiteDefinition,
    comparison_policy: ComparisonPolicy,
    evaluation_policy: EvaluationPolicy,
) -> None:
    suite_reference = (suite.suite_code, suite.suite_version, suite.content_hash)
    if [(ref.id, ref.version, ref.hash) for ref in profile.suite_references] != [suite_reference]:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    expected_policy_members = {
        ("PROFILE", profile.profile_code, profile.profile_version, profile.content_hash),
        ("SUITE", suite.suite_code, suite.suite_version, suite.content_hash),
        (
            "COMPARISON_POLICY",
            comparison_policy.policy_code,
            comparison_policy.policy_version,
            comparison_policy.content_hash,
        ),
    }
    actual_policy_members = {
        (
            member.member_type.value,
            member.reference.id,
            member.reference.version,
            member.reference.hash,
        )
        for member in evaluation_policy.members
    }
    if actual_policy_members != expected_policy_members:
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)


def _legacy_load_dataset(manifest_path: Path, *, evals_root: Path) -> _LegacyValidatedDataset:
    root = evals_root.absolute()
    safe_manifest_path = _safe_path(root, manifest_path)
    manifest = _legacy_load_json_object(safe_manifest_path, DatasetManifest)
    _verify_content_hash(manifest)

    cases, resource_hashes = _load_cases(root, manifest)
    _validate_leakage(cases)
    evidence_mapping, evidence_hashes = _load_evidence_mapping(root, manifest, cases)
    resource_hashes.extend(evidence_hashes)
    rubric, rubric_hash = _load_rubric(root, manifest, cases)
    resource_hashes.append(rubric_hash)
    configuration = _load_configuration(root, safe_manifest_path)
    resource_hashes.extend(configuration.resource_hashes)

    if manifest.content_classification is ContentClassification.APPROVED_DEIDENTIFIED:
        if manifest.deidentification_approval_receipt_ref is None:
            raise EvaluationValidationError(EvaluationErrorCode.DEIDENTIFICATION_APPROVAL_REQUIRED)

    return _LegacyValidatedDataset(
        manifest=manifest,
        cases=tuple(cases),
        evidence_mapping=evidence_mapping,
        rubric=rubric,
        profile=configuration.profile,
        comparison_policy=configuration.comparison_policy,
        evaluation_policy=configuration.evaluation_policy,
        suite=configuration.suite,
        resource_hashes=tuple(sorted(resource_hashes)),
    )


# The Section 17/20 one-read loader supersedes the reduced first-pass loader above.
from ai_worker.tasks.evaluation.loaders_contract import (  # noqa: E402
    ResolvedReference,
    SyntheticCorpusSnapshot,
    ValidatedDataset,
    load_dataset,
    load_json_object,
)
