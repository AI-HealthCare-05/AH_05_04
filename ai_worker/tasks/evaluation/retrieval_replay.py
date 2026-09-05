from __future__ import annotations

import errno
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, cast

from pydantic import BeforeValidator, Field, ValidationError, model_validator

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256, normalize_resource_path
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import parse_json_object_bytes, safe_path_under_root
from ai_worker.tasks.evaluation.schemas.artifacts import CASE_RESULT_ADAPTER, CaseResult
from ai_worker.tasks.evaluation.schemas.common import (
    SemanticVersion,
    Sha256Hex,
    StableId,
    StrictContractModel,
    TaskType,
)

if TYPE_CHECKING:
    from ai_worker.tasks.evaluation.config import ResolvedDevExecution
    from ai_worker.tasks.evaluation.runner import AdapterRequest, EvaluationAdapter


def _tuple_from_wire(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


class ReplayCaseResult(StrictContractModel):
    case_id: StableId
    case_resource_sha256: Sha256Hex
    ranked_evidence_ids: Annotated[
        tuple[StableId, ...],
        BeforeValidator(_tuple_from_wire),
        Field(max_length=5),
    ]

    @model_validator(mode="after")
    def validate_unique_ranks(self) -> ReplayCaseResult:
        if len(self.ranked_evidence_ids) != len(set(self.ranked_evidence_ids)):
            raise ValueError("ranked evidence ids must be unique")
        return self


class RetrievalReplayManifest(StrictContractModel):
    schema_id: Literal["rag-eval.retrieval-replay"]
    schema_version: Literal["1.0.0"]
    dataset_code: StableId
    dataset_version: SemanticVersion
    variant_id: StableId
    top_k: Literal[5]
    case_results: Annotated[
        tuple[ReplayCaseResult, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    replay_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_case_order(self) -> RetrievalReplayManifest:
        case_ids = tuple(item.case_id for item in self.case_results)
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("replay case ids must be unique")
        if case_ids != tuple(sorted(case_ids, key=lambda value: value.encode("utf-16-be"))):
            raise ValueError("replay case results must be sorted")
        return self


def _read_replay_bytes(path: Path, repository_root: Path) -> bytes:
    try:
        relative = normalize_resource_path(path.as_posix())
    except EvaluationValidationError:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from None
    if path.is_absolute() or Path(relative) != path:
        raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID)
    safe_path = safe_path_under_root(repository_root.absolute(), repository_root.absolute() / relative)
    try:
        return safe_path.read_bytes()
    except OSError as error:
        if error.errno == errno.ENOENT:
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_MISSING) from error
        if error.errno in {errno.ENOTDIR, errno.EISDIR, errno.EACCES, errno.EPERM, errno.ELOOP}:
            raise EvaluationValidationError(EvaluationErrorCode.RESOURCE_PATH_INVALID) from error
        raise


def parse_retrieval_replay_bytes(raw_bytes: bytes) -> RetrievalReplayManifest:
    payload = parse_json_object_bytes(raw_bytes)
    try:
        replay = RetrievalReplayManifest.model_validate(payload)
    except ValidationError:
        raise EvaluationValidationError(EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID) from None
    expected_hash = canonical_sha256(
        cast(JsonValue, payload),
        excluded_top_level_keys=frozenset({"replay_sha256"}),
    )
    if replay.replay_sha256 != expected_hash:
        raise EvaluationValidationError(EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID)
    return replay


def load_retrieval_replay(path: Path, *, repository_root: Path) -> RetrievalReplayManifest:
    return parse_retrieval_replay_bytes(_read_replay_bytes(path, repository_root))


class ReplayRetrievalAdapter:
    def __init__(
        self,
        replay: RetrievalReplayManifest,
        *,
        variant_manifest_hash: str | None = None,
    ) -> None:
        self._replay = replay
        self._variant_manifest_hash = variant_manifest_hash
        self._case_results = {
            item.case_id: (item.case_resource_sha256, item.ranked_evidence_ids) for item in replay.case_results
        }

    def validate_case_set(self, case_ids: Sequence[str]) -> None:
        selected = tuple(sorted(case_ids, key=lambda value: value.encode("utf-16-be")))
        if tuple(self._case_results) != selected:
            raise EvaluationValidationError(EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID)

    def execute(self, request: AdapterRequest) -> CaseResult:
        binding_valid = (
            request.task_type is TaskType.RETRIEVAL
            and request.case.dataset_code == self._replay.dataset_code
            and request.case.dataset_version == self._replay.dataset_version
            and request.variant_id == self._replay.variant_id
            and (self._variant_manifest_hash is None or request.variant_manifest_hash == self._variant_manifest_hash)
        )
        replay_case = self._case_results.get(request.case.case_id)
        if not binding_valid or replay_case is None or replay_case[0] != request.case_resource_sha256:
            raise EvaluationValidationError(EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID)
        ranked = replay_case[1]
        return CASE_RESULT_ADAPTER.validate_python(
            {
                "schema_id": "rag-eval.case-result",
                "schema_version": "1.0.0",
                "run_id": request.run_id,
                "case_id": request.case.case_id,
                "dataset_code": request.case.dataset_code,
                "dataset_version": request.case.dataset_version,
                "task_type": "RETRIEVAL",
                "partition": request.case.partition.value,
                "input_sha256": request.input_sha256,
                "execution_status": "COMPLETED",
                "decision_status": "N/A",
                "failure_codes": [],
                "retrieved_evidence_ids": list(ranked),
                "selected_evidence_ids": list(ranked[:5]),
                "actual_claim_ids": None,
                "actual_citation_evidence_ids": None,
                "actual_rule_ids": None,
                "actual_scope_codes": None,
                "actual_response_level": None,
                "actual_safety_disposition": None,
                "actual_execution_status": None,
                "actual_release_decision": None,
                "actual_fallback_code": None,
                "actual_provider_invocation": None,
                "actual_retrieval_invocation": True,
                "actual_publication_allowed": None,
                "actual_sections": None,
                "omitted_sections": None,
                "risk_level": None,
                "answer_sha256": None,
                "latency_ms": 0,
                "input_token_count": None,
                "output_token_count": None,
                "estimated_cost": None,
            }
        )


class _ReplayAdapterRegistry:
    def __init__(self, adapter: EvaluationAdapter | None) -> None:
        self._adapter = adapter

    def resolve(self, adapter_id: str) -> EvaluationAdapter | None:
        return self._adapter if adapter_id == "retrieval-replay.v1" else None


def build_adapter_registry(resolved: ResolvedDevExecution) -> _ReplayAdapterRegistry:
    variant = resolved.request.retrieval_variant
    if variant is None or variant.model_config_payload.get("adapter_id") != "retrieval-replay.v1":
        return _ReplayAdapterRegistry(None)
    replay = resolved.retrieval_replay
    variant_hash = resolved.retrieval_variant_manifest_hash
    if replay is None or variant.replay_artifact_path is None or variant_hash is None:
        raise EvaluationValidationError(EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID)
    return _ReplayAdapterRegistry(ReplayRetrievalAdapter(replay, variant_manifest_hash=variant_hash))
