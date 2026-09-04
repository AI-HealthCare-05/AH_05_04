from __future__ import annotations

import errno
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, cast

from pydantic import BeforeValidator, Field, ValidationError

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import (
    ValidatedDataset,
    parse_json_object_bytes,
    safe_path_under_root,
)
from ai_worker.tasks.evaluation.retrieval_replay import RetrievalReplayManifest, parse_retrieval_replay_bytes
from ai_worker.tasks.evaluation.schemas.common import (
    ImmutableReference,
    Partition,
    ResourcePath,
    SafeInteger,
    SemanticVersion,
    Sha256Hex,
    StableId,
    StrictContractModel,
)
from ai_worker.tasks.evaluation.schemas.policy import ExperimentType, ExperimentTypeValue

AUTHORITY_MANIFEST_HASH = "f2c98884c841d3fccdbec552f14aad1fd471730eae6d80c472c1b332ed95a570"
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_REFERENCE_FIELDS = (
    "dataset_manifest_path",
    "profile_path",
    "comparison_policy_path",
    "evaluation_policy_path",
    "suite_path",
)


class DevVariant(StrictContractModel):
    variant_id: StableId
    variant_version: SemanticVersion
    kind: Literal["RETRIEVAL", "ANSWER"]
    model_config_payload: dict[str, JsonValue] = Field(alias="model_config")
    prompt_version: StableId
    parameters: dict[str, JsonValue]
    replay_artifact_path: ResourcePath | None = None


class RetrievalReplayModelConfig(StrictContractModel):
    adapter_id: Literal["retrieval-replay.v1"]
    provider_invocation: Literal[False]
    source_snapshot_ref: ImmutableReference
    knowledge_index_ref: ImmutableReference
    embedding_model_ref: ImmutableReference
    parser_ref: ImmutableReference
    filter_snapshot_hash: Sha256Hex


class DevExecutionRequest(StrictContractModel):
    config_id: StableId
    config_version: SemanticVersion
    experiment_id: StableId
    experiment_type: ExperimentTypeValue
    variant_id: StableId
    evaluated_partitions: Annotated[
        tuple[Literal["DEV"]],
        BeforeValidator(lambda value: tuple(value) if isinstance(value, list) else value),
    ]
    environment: Literal["LOCAL", "CI"]
    dataset_manifest_path: ResourcePath
    profile_path: ResourcePath
    comparison_policy_path: ResourcePath
    evaluation_policy_path: ResourcePath
    suite_path: ResourcePath
    upstream_contract_manifest_hash: Sha256Hex
    retrieval_variant: DevVariant | None
    answer_variant: DevVariant | None
    seed: SafeInteger
    retry_policy: Literal["NO_AUTOMATIC_RETRY"]
    max_attempts: Literal[1]


@dataclass(frozen=True, slots=True)
class RepositoryState:
    commit_sha: str
    clean: bool


type RepositoryStateProvider = Callable[[Path], RepositoryState]


@dataclass(frozen=True, slots=True)
class ResolvedDevExecution:
    request: DevExecutionRequest
    dataset_manifest_path: Path
    dataset_manifest_bytes: bytes
    referenced_file_hashes: Sequence[tuple[str, str]]
    resolved_evaluation_config_hash: str
    retrieval_variant_manifest_hash: str | None
    answer_variant_manifest_hash: str | None
    model_config_hash: str
    prompt_version: str
    runner_commit_sha: str
    repository_root: Path
    replay_artifact_path: Path | None
    replay_artifact_bytes: bytes | None
    retrieval_replay: RetrievalReplayManifest | None


def git_repository_state(repository_root: Path) -> RepositoryState:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise EvaluationValidationError(EvaluationErrorCode.REPOSITORY_STATE_INVALID) from error
    return RepositoryState(commit_sha=commit, clean=not status)


def _request_error_code(error: ValidationError) -> EvaluationErrorCode:
    locations = {str(location) for item in error.errors(include_input=False) for location in item["loc"]}
    if locations & {*_REFERENCE_FIELDS, "replay_artifact_path"}:
        return EvaluationErrorCode.RESOURCE_PATH_INVALID
    if "evaluated_partitions" in locations:
        return EvaluationErrorCode.PARTITION_INVALID
    if locations & {"retry_policy", "max_attempts"}:
        return EvaluationErrorCode.STATE_COMBINATION_INVALID
    return EvaluationErrorCode.SCHEMA_INVALID


def _read_file_under_root(repository_root: Path, path: Path) -> bytes:
    safe_path = safe_path_under_root(repository_root, path)
    try:
        return safe_path.read_bytes()
    except OSError as error:
        if error.errno == errno.ENOENT:
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING) from error
        if error.errno in {errno.ENOTDIR, errno.EISDIR, errno.EACCES, errno.EPERM, errno.ELOOP}:
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
        raise


def _validate_replay_dataset_binding(
    replay: RetrievalReplayManifest,
    dataset_manifest_bytes: bytes,
    variant: DevVariant | None,
) -> None:
    manifest_payload = parse_json_object_bytes(dataset_manifest_bytes)
    if (
        replay.dataset_code != manifest_payload.get("dataset_code")
        or replay.dataset_version != manifest_payload.get("dataset_version")
        or variant is None
        or replay.variant_id != variant.variant_id
    ):
        raise EvaluationValidationError(EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID)


def _validate_request_semantics(request: DevExecutionRequest) -> tuple[tuple[DevVariant, ...], bytes, str]:
    if any(not cast(str, getattr(request, field)).startswith("evals/") for field in _REFERENCE_FIELDS):
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    if request.upstream_contract_manifest_hash != AUTHORITY_MANIFEST_HASH:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    retrieval = request.retrieval_variant
    answer = request.answer_variant
    valid_matrix = (
        request.experiment_type is ExperimentType.KNOWLEDGE_RETRIEVAL
        and retrieval is not None
        and answer is None
        or request.experiment_type in {ExperimentType.ANSWER_GROUNDING_SAFETY, ExperimentType.END_TO_END_RAG}
        and retrieval is not None
        and answer is not None
    )
    if (
        not valid_matrix
        or retrieval is not None
        and retrieval.kind != "RETRIEVAL"
        or answer is not None
        and answer.kind != "ANSWER"
    ):
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)

    active_variants = tuple(variant for variant in (retrieval, answer) if variant is not None)
    if retrieval is not None and retrieval.model_config_payload.get("adapter_id") == "retrieval-replay.v1":
        if request.experiment_type is not ExperimentType.KNOWLEDGE_RETRIEVAL:
            raise EvaluationValidationError(EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID)
        if request.variant_id != retrieval.variant_id or retrieval.replay_artifact_path is None:
            raise EvaluationValidationError(EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID)
        try:
            RetrievalReplayModelConfig.model_validate(retrieval.model_config_payload)
        except ValidationError:
            raise EvaluationValidationError(EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID) from None
    elif retrieval is not None and retrieval.replay_artifact_path is not None:
        raise EvaluationValidationError(EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID)
    model_payloads = {canonical_json_bytes(variant.model_config_payload) for variant in active_variants}
    prompt_versions = {variant.prompt_version for variant in active_variants}
    if len(model_payloads) != 1 or len(prompt_versions) != 1:
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)
    return active_variants, next(iter(model_payloads)), next(iter(prompt_versions))


def load_dev_execution_request(
    path: Path,
    *,
    repository_root: Path,
    repository_state_provider: RepositoryStateProvider = git_repository_state,
) -> ResolvedDevExecution:
    root = repository_root.absolute()
    payload = parse_json_object_bytes(_read_file_under_root(root, path))
    try:
        request = DevExecutionRequest.model_validate(payload)
    except ValidationError as error:
        raise EvaluationValidationError(_request_error_code(error)) from None

    active_variants, model_config_bytes, prompt_version = _validate_request_semantics(request)
    repository_state = repository_state_provider(root)
    if not repository_state.clean or _COMMIT_SHA.fullmatch(repository_state.commit_sha) is None:
        raise EvaluationValidationError(EvaluationErrorCode.REPOSITORY_STATE_INVALID)

    referenced_file_hashes: list[tuple[str, str]] = []
    dataset_manifest_bytes: bytes | None = None
    for field in _REFERENCE_FIELDS:
        relative_path = cast(str, getattr(request, field))
        raw_bytes = _read_file_under_root(root, root / relative_path)
        referenced_file_hashes.append((relative_path, sha256_hex(raw_bytes)))
        if field == "dataset_manifest_path":
            dataset_manifest_bytes = raw_bytes
    replay_artifact_path: Path | None = None
    replay_artifact_bytes: bytes | None = None
    retrieval_replay: RetrievalReplayManifest | None = None
    if request.retrieval_variant is not None and request.retrieval_variant.replay_artifact_path is not None:
        replay_relative = request.retrieval_variant.replay_artifact_path
        if not replay_relative.startswith("evals/"):
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
        replay_artifact_path = safe_path_under_root(root, root / replay_relative)
        replay_artifact_bytes = _read_file_under_root(root, replay_artifact_path)
        retrieval_replay = parse_retrieval_replay_bytes(replay_artifact_bytes)
        referenced_file_hashes.append((replay_relative, sha256_hex(replay_artifact_bytes)))
    if dataset_manifest_bytes is None:
        raise EvaluationValidationError(EvaluationErrorCode.INTERNAL_ERROR)
    if retrieval_replay is not None:
        _validate_replay_dataset_binding(retrieval_replay, dataset_manifest_bytes, request.retrieval_variant)

    retrieval_hash = (
        canonical_sha256(cast(JsonValue, request.retrieval_variant.model_dump(mode="json", by_alias=True)))
        if request.retrieval_variant is not None
        else None
    )
    answer_hash = (
        canonical_sha256(cast(JsonValue, request.answer_variant.model_dump(mode="json", by_alias=True)))
        if request.answer_variant is not None
        else None
    )
    resolved_preimage: JsonValue = {
        "request": cast(JsonValue, request.model_dump(mode="json", by_alias=True)),
        "referenced_files": [
            {"path": reference_path, "sha256": file_hash} for reference_path, file_hash in referenced_file_hashes
        ],
        "retrieval_variant_manifest_hash": retrieval_hash,
        "answer_variant_manifest_hash": answer_hash,
        "runner_commit_sha": repository_state.commit_sha,
    }
    del active_variants
    return ResolvedDevExecution(
        request=request,
        dataset_manifest_path=safe_path_under_root(root, root / request.dataset_manifest_path),
        dataset_manifest_bytes=dataset_manifest_bytes,
        referenced_file_hashes=tuple(referenced_file_hashes),
        resolved_evaluation_config_hash=canonical_sha256(resolved_preimage),
        retrieval_variant_manifest_hash=retrieval_hash,
        answer_variant_manifest_hash=answer_hash,
        model_config_hash=sha256_hex(model_config_bytes),
        prompt_version=prompt_version,
        runner_commit_sha=repository_state.commit_sha,
        repository_root=root,
        replay_artifact_path=replay_artifact_path,
        replay_artifact_bytes=replay_artifact_bytes,
        retrieval_replay=retrieval_replay,
    )


def preflight_dev_manifest(resolved: ResolvedDevExecution) -> None:
    payload = parse_json_object_bytes(resolved.dataset_manifest_bytes)
    counts = payload.get("partition_counts")
    resources = payload.get("case_resources")
    if type(counts) is not dict or type(resources) is not list:
        raise EvaluationValidationError(EvaluationErrorCode.PARTITION_INVALID)
    partition_counts = cast(dict[str, JsonValue], counts)
    case_resources = cast(list[JsonValue], resources)
    dev_count = partition_counts.get("DEV")
    if type(dev_count) is not int:
        raise EvaluationValidationError(EvaluationErrorCode.PARTITION_INVALID)
    exact_dev_count = cast(int, dev_count)
    valid = (
        resolved.request.evaluated_partitions == ("DEV",)
        and exact_dev_count > 0
        and all(partition_counts.get(name) == 0 for name in ("AUTHORING", "HOLDOUT", "SAFETY_REGRESSION"))
        and len(case_resources) == exact_dev_count
        and all(type(item) is dict and item.get("partition") == "DEV" for item in case_resources)
        and payload.get("data_classification") == "SYNTHETIC"
    )
    if not valid:
        raise EvaluationValidationError(EvaluationErrorCode.PARTITION_INVALID)


def _validate_configuration_resource_bindings(
    resolved: ResolvedDevExecution,
    dataset: ValidatedDataset,
) -> None:
    requested_hashes = dict(resolved.referenced_file_hashes)
    role_bindings = {
        "dataset_manifest_path": dataset.dataset_manifest_resource,
        "profile_path": dataset.configuration_resources.profile,
        "comparison_policy_path": dataset.configuration_resources.comparison_policy,
        "evaluation_policy_path": dataset.configuration_resources.evaluation_policy,
        "suite_path": dataset.configuration_resources.suite,
    }
    for field, binding in role_bindings.items():
        requested_path = cast(str, getattr(resolved.request, field))
        if requested_path != f"evals/{binding.relative_path}":
            raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
        if requested_hashes.get(requested_path) != binding.sha256:
            raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)


def validate_loaded_bindings(resolved: ResolvedDevExecution, dataset: ValidatedDataset) -> None:
    _validate_configuration_resource_bindings(resolved, dataset)
    loaded_hashes = dict(dataset.resource_hashes)
    if dataset.profile.required_partitions != (Partition.DEV,):
        raise EvaluationValidationError(EvaluationErrorCode.PARTITION_INVALID)
    if dataset.profile.runtime_eligible:
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)
    if resolved.request.experiment_type not in dataset.profile.required_experiment_types:
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)
    if dataset.suite.input_selector.partitions != (Partition.DEV,):
        raise EvaluationValidationError(EvaluationErrorCode.PARTITION_INVALID)
    if (
        dataset.suite.input_selector.dataset_code != dataset.manifest.dataset_code
        or dataset.suite.input_selector.dataset_version != dataset.manifest.dataset_version
    ):
        raise EvaluationValidationError(EvaluationErrorCode.MANIFEST_INVALID)
    for path, expected_hash in resolved.referenced_file_hashes:
        if (
            resolved.request.retrieval_variant is not None
            and path == resolved.request.retrieval_variant.replay_artifact_path
        ):
            continue
        try:
            evals_relative = Path(path).relative_to("evals").as_posix()
        except ValueError as error:
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
        if loaded_hashes.get(evals_relative) != expected_hash:
            raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
