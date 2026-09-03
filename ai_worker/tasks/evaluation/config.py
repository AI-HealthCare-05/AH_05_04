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
from ai_worker.tasks.evaluation.loaders import parse_json_object_bytes, safe_path_under_root
from ai_worker.tasks.evaluation.schemas.common import (
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
    referenced_file_hashes: Sequence[tuple[str, str]]
    resolved_evaluation_config_hash: str
    retrieval_variant_manifest_hash: str | None
    answer_variant_manifest_hash: str | None
    model_config_hash: str
    prompt_version: str
    runner_commit_sha: str


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
    locations = {
        str(location)
        for item in error.errors(include_input=False)
        for location in item["loc"]
    }
    if locations & set(_REFERENCE_FIELDS):
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


def _validate_request_semantics(request: DevExecutionRequest) -> tuple[tuple[DevVariant, ...], bytes, str]:
    if request.upstream_contract_manifest_hash != AUTHORITY_MANIFEST_HASH:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)

    retrieval = request.retrieval_variant
    answer = request.answer_variant
    valid_matrix = (
        request.experiment_type is ExperimentType.KNOWLEDGE_RETRIEVAL
        and retrieval is not None
        and answer is None
        or request.experiment_type
        in {ExperimentType.ANSWER_GROUNDING_SAFETY, ExperimentType.END_TO_END_RAG}
        and retrieval is not None
        and answer is not None
    )
    if not valid_matrix or retrieval is not None and retrieval.kind != "RETRIEVAL" or answer is not None and answer.kind != "ANSWER":
        raise EvaluationValidationError(EvaluationErrorCode.STATE_COMBINATION_INVALID)

    active_variants = tuple(variant for variant in (retrieval, answer) if variant is not None)
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
    for field in _REFERENCE_FIELDS:
        relative_path = cast(str, getattr(request, field))
        raw_bytes = _read_file_under_root(root, root / relative_path)
        referenced_file_hashes.append((relative_path, sha256_hex(raw_bytes)))

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
            {"path": reference_path, "sha256": file_hash}
            for reference_path, file_hash in referenced_file_hashes
        ],
        "retrieval_variant_manifest_hash": retrieval_hash,
        "answer_variant_manifest_hash": answer_hash,
        "runner_commit_sha": repository_state.commit_sha,
    }
    del active_variants
    return ResolvedDevExecution(
        request=request,
        dataset_manifest_path=safe_path_under_root(root, root / request.dataset_manifest_path),
        referenced_file_hashes=tuple(referenced_file_hashes),
        resolved_evaluation_config_hash=canonical_sha256(resolved_preimage),
        retrieval_variant_manifest_hash=retrieval_hash,
        answer_variant_manifest_hash=answer_hash,
        model_config_hash=sha256_hex(model_config_bytes),
        prompt_version=prompt_version,
        runner_commit_sha=repository_state.commit_sha,
    )
