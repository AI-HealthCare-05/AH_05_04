from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.config import (
    RepositoryState,
    load_dev_execution_request,
    preflight_dev_manifest,
    validate_loaded_bindings,
)
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import (
    _SnapshotReader,
    load_dataset,
    parse_json_object_bytes,
    safe_path_under_root,
)
from ai_worker.tasks.evaluation.schemas.common import ExperimentType, Partition

AUTHORITY_MANIFEST_HASH = "f2c98884c841d3fccdbec552f14aad1fd471730eae6d80c472c1b332ed95a570"
REPOSITORY_ROOT = Path(__file__).parents[3]
SOURCE_MANIFEST = REPOSITORY_ROOT / "evals/retrieval/manifests/dev-foundation-v1.dataset.json"


def _retrieval_variant() -> dict[str, Any]:
    return {
        "variant_id": "dev-synthetic-retrieval-v1",
        "variant_version": "1.0.0",
        "kind": "RETRIEVAL",
        "model_config": {"adapter_id": "validation-only.v1", "provider_invocation": False},
        "prompt_version": "synthetic-no-provider-v1",
        "parameters": {"seed": 157},
    }


def _answer_variant() -> dict[str, Any]:
    return {
        "variant_id": "dev-synthetic-answer-v1",
        "variant_version": "1.0.0",
        "kind": "ANSWER",
        "model_config": {"adapter_id": "validation-only.v1", "provider_invocation": False},
        "prompt_version": "synthetic-no-provider-v1",
        "parameters": {"seed": 157},
    }


def _replay_variant(*, replay_path: str = "evals/retrieval/replays/test.replay.json") -> dict[str, Any]:
    return {
        "variant_id": "RET-L",
        "variant_version": "1.0.0",
        "kind": "RETRIEVAL",
        "model_config": {
            "adapter_id": "retrieval-replay.v1",
            "provider_invocation": False,
            "source_snapshot_ref": {"id": "source", "version": "1.0.0", "hash": "1" * 64},
            "knowledge_index_ref": {"id": "index", "version": "1.0.0", "hash": "2" * 64},
            "embedding_model_ref": {"id": "embedding", "version": "1.0.0", "hash": "3" * 64},
            "parser_ref": {"id": "parser", "version": "1.0.0", "hash": "4" * 64},
            "filter_snapshot_hash": "5" * 64,
        },
        "prompt_version": "synthetic-replay-v1",
        "parameters": {"reranker_enabled": False, "retrieval_strategy": "LEXICAL"},
        "replay_artifact_path": replay_path,
    }


def _request_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "config_id": "rag-dev-foundation-knowledge-retrieval",
        "config_version": "1.0.0",
        "experiment_id": "rag-dev-foundation-infrastructure",
        "experiment_type": "KNOWLEDGE_RETRIEVAL",
        "variant_id": "dev-synthetic-adapter-v1",
        "evaluated_partitions": ["DEV"],
        "environment": "LOCAL",
        "dataset_manifest_path": "evals/retrieval/manifests/dev.dataset.json",
        "profile_path": "evals/profiles/dev.profile.json",
        "comparison_policy_path": "evals/policies/dev.comparison-policy.json",
        "evaluation_policy_path": "evals/policies/dev.evaluation-policy.json",
        "suite_path": "evals/suites/dev.suite.json",
        "upstream_contract_manifest_hash": AUTHORITY_MANIFEST_HASH,
        "retrieval_variant": _retrieval_variant(),
        "answer_variant": None,
        "seed": 157,
        "retry_policy": "NO_AUTOMATIC_RETRY",
        "max_attempts": 1,
    }
    payload.update(overrides)
    return payload


def _write_references(root: Path, payload: dict[str, Any]) -> None:
    for field in (
        "dataset_manifest_path",
        "profile_path",
        "comparison_policy_path",
        "evaluation_policy_path",
        "suite_path",
    ):
        path = root / payload[field]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes({"reference": field}))


def _write_request(root: Path, **overrides: Any) -> Path:
    payload = _request_payload(**overrides)
    _write_references(root, payload)
    path = root / "evals/configs/dev.execution.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))
    return path


def _load_request(root: Path, **overrides: Any):
    return load_dev_execution_request(
        _write_request(root, **overrides),
        repository_root=root,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )


def _manifest_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "dataset_code": "another-synthetic-dev",
        "dataset_version": "9.8.7",
        "status": "REVIEWED",
        "data_classification": "SYNTHETIC",
        "partition_counts": {
            "AUTHORING": 0,
            "DEV": 1,
            "HOLDOUT": 0,
            "SAFETY_REGRESSION": 0,
        },
        "case_resources": [
            {
                "case_id": "different-case-count-001",
                "partition": "DEV",
                "path": "retrieval/cases/not-created.json",
                "sha256": "1" * 64,
            }
        ],
    }
    payload.update(overrides)
    return payload


def _resolved_for_manifest(root: Path, manifest: dict[str, Any]):
    request_path = _write_request(root)
    (root / "evals/retrieval/manifests/dev.dataset.json").write_bytes(canonical_json_bytes(manifest))
    return load_dev_execution_request(
        request_path,
        repository_root=root,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )


def test_parse_json_object_bytes_rejects_duplicate_keys() -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        parse_json_object_bytes(b'{"seed":1,"seed":2}')

    assert caught.value.code is EvaluationErrorCode.JSON_DUPLICATE_KEY


def test_safe_path_under_root_rejects_parent_escape(tmp_path: Path) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        safe_path_under_root(tmp_path, tmp_path / "../outside.json")

    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID


def test_load_dev_execution_request_binds_actual_variant_and_runner_hashes(tmp_path: Path) -> None:
    resolved = _load_request(tmp_path)

    assert resolved.runner_commit_sha == "a" * 40
    assert resolved.retrieval_variant_manifest_hash == canonical_sha256(
        resolved.request.retrieval_variant.model_dump(mode="json", by_alias=True)
    )
    assert resolved.answer_variant_manifest_hash is None


def test_resolved_hash_changes_when_seed_changes(tmp_path: Path) -> None:
    first = _load_request(tmp_path / "first", seed=157)
    second = _load_request(tmp_path / "second", seed=158)

    assert first.resolved_evaluation_config_hash != second.resolved_evaluation_config_hash


def test_resolved_hash_changes_when_referenced_file_changes(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_path = _write_request(first_root)
    second_path = _write_request(second_root)
    (second_root / "evals/profiles/dev.profile.json").write_bytes(b'{"reference":"changed"}')

    first = load_dev_execution_request(
        first_path,
        repository_root=first_root,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    second = load_dev_execution_request(
        second_path,
        repository_root=second_root,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )

    assert first.resolved_evaluation_config_hash != second.resolved_evaluation_config_hash


def test_production_request_rejects_dirty_repository(tmp_path: Path) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        load_dev_execution_request(
            _write_request(tmp_path),
            repository_root=tmp_path,
            repository_state_provider=lambda _root: RepositoryState("a" * 40, False),
        )

    assert caught.value.code is EvaluationErrorCode.REPOSITORY_STATE_INVALID


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"unexpected": True}, EvaluationErrorCode.SCHEMA_INVALID),
        ({"evaluated_partitions": ["HOLDOUT"]}, EvaluationErrorCode.PARTITION_INVALID),
        ({"retry_policy": "EXPONENTIAL"}, EvaluationErrorCode.STATE_COMBINATION_INVALID),
        ({"max_attempts": 2}, EvaluationErrorCode.STATE_COMBINATION_INVALID),
        ({"upstream_contract_manifest_hash": "0" * 64}, EvaluationErrorCode.HASH_MISMATCH),
    ],
)
def test_execution_request_rejects_invalid_contract_values(
    tmp_path: Path,
    overrides: dict[str, Any],
    expected_code: EvaluationErrorCode,
) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        _load_request(tmp_path, **overrides)

    assert caught.value.code is expected_code


@pytest.mark.parametrize(
    "overrides",
    [
        {"retrieval_variant": None},
        {"answer_variant": _answer_variant()},
        {"retrieval_variant": _answer_variant()},
        {
            "experiment_type": "ANSWER_GROUNDING_SAFETY",
            "answer_variant": None,
        },
    ],
)
def test_execution_request_rejects_variant_matrix_mismatch(
    tmp_path: Path,
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        _load_request(tmp_path, **overrides)

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


@pytest.mark.parametrize("field", ["model_config", "prompt_version"])
def test_execution_request_rejects_inconsistent_active_variants(tmp_path: Path, field: str) -> None:
    answer = _answer_variant()
    answer[field] = {"adapter_id": "different"} if field == "model_config" else "different-prompt-v1"

    with pytest.raises(EvaluationValidationError) as caught:
        _load_request(
            tmp_path,
            experiment_type="END_TO_END_RAG",
            answer_variant=answer,
        )

    assert caught.value.code is EvaluationErrorCode.STATE_COMBINATION_INVALID


def test_execution_request_rejects_absolute_reference_path(tmp_path: Path) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        _load_request(tmp_path, profile_path=str(tmp_path / "profile.json"))

    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID


@pytest.mark.parametrize(
    "field",
    [
        "dataset_manifest_path",
        "profile_path",
        "comparison_policy_path",
        "evaluation_policy_path",
        "suite_path",
    ],
)
def test_execution_request_rejects_reference_outside_evals(tmp_path: Path, field: str) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        _load_request(tmp_path, **{field: f"backend/{field}.json"})

    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID


def test_execution_request_rejects_symlinked_reference(tmp_path: Path) -> None:
    request_path = _write_request(tmp_path)
    profile_path = tmp_path / "evals/profiles/dev.profile.json"
    profile_path.unlink()
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"{}")
    profile_path.symlink_to(outside)

    with pytest.raises(EvaluationValidationError) as caught:
        load_dev_execution_request(
            request_path,
            repository_root=tmp_path,
            repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
        )

    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID


def test_execution_request_rejects_missing_reference(tmp_path: Path) -> None:
    request_path = _write_request(tmp_path)
    (tmp_path / "evals/suites/dev.suite.json").unlink()

    with pytest.raises(EvaluationValidationError) as caught:
        load_dev_execution_request(
            request_path,
            repository_root=tmp_path,
            repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
        )

    assert caught.value.code is EvaluationErrorCode.RESOURCE_MISSING


@pytest.mark.parametrize(
    ("name", "experiment_type", "has_answer_variant"),
    [
        ("dev-foundation-knowledge-retrieval-v1.execution.json", "KNOWLEDGE_RETRIEVAL", False),
        ("dev-foundation-answer-grounding-safety-v1.execution.json", "ANSWER_GROUNDING_SAFETY", True),
        ("dev-foundation-end-to-end-rag-v1.execution.json", "END_TO_END_RAG", True),
    ],
)
def test_checked_in_execution_request_is_canonical_and_loadable(
    name: str,
    experiment_type: str,
    has_answer_variant: bool,
) -> None:
    path = REPOSITORY_ROOT / "evals/configs" / name
    raw_bytes = path.read_bytes()
    payload = parse_json_object_bytes(raw_bytes)

    assert raw_bytes == canonical_json_bytes(payload)
    resolved = load_dev_execution_request(
        path,
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    assert resolved.request.experiment_type.value == experiment_type
    assert (resolved.request.answer_variant is not None) is has_answer_variant


@pytest.mark.parametrize(
    ("name", "variant_id", "strategy", "reranker_enabled", "replay_path"),
    [
        (
            "rag-retrieval-dev-ret-l-v1.execution.json",
            "RET-L",
            "SYNTHETIC_LEXICAL_REPLAY",
            False,
            "evals/retrieval/replays/rag-retrieval-dev-v1/ret-l-v1.replay.json",
        ),
        (
            "rag-retrieval-dev-ret-hr-v1.execution.json",
            "RET-HR",
            "SYNTHETIC_HYBRID_RERANK_REPLAY",
            True,
            "evals/retrieval/replays/rag-retrieval-dev-v1/ret-hr-v1.replay.json",
        ),
    ],
)
def test_checked_in_retrieval_replay_config_binds_provenance_and_replay_bytes(
    name: str,
    variant_id: str,
    strategy: str,
    reranker_enabled: bool,
    replay_path: str,
) -> None:
    path = REPOSITORY_ROOT / "evals/configs" / name
    raw_bytes = path.read_bytes()
    assert raw_bytes == canonical_json_bytes(parse_json_object_bytes(raw_bytes))
    resolved = load_dev_execution_request(
        path,
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )

    variant = resolved.request.retrieval_variant
    assert variant is not None
    assert variant.variant_id == variant_id
    assert variant.replay_artifact_path == replay_path
    assert variant.parameters == {
        "reranker_enabled": reranker_enabled,
        "retrieval_strategy": strategy,
    }
    assert dict(resolved.referenced_file_hashes)[replay_path] == sha256_hex(
        REPOSITORY_ROOT.joinpath(replay_path).read_bytes()
    )


def test_retrieval_replay_configs_keep_identical_model_provenance() -> None:
    resolved = [
        load_dev_execution_request(
            REPOSITORY_ROOT / "evals/configs" / name,
            repository_root=REPOSITORY_ROOT,
            repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
        )
        for name in (
            "rag-retrieval-dev-ret-l-v1.execution.json",
            "rag-retrieval-dev-ret-hr-v1.execution.json",
        )
    ]
    first = resolved[0].request.retrieval_variant
    second = resolved[1].request.retrieval_variant

    assert first is not None and second is not None
    assert canonical_json_bytes(first.model_config_payload) == canonical_json_bytes(second.model_config_payload)
    assert resolved[0].model_config_hash == resolved[1].model_config_hash


def test_replay_adapter_config_requires_replay_path_and_full_provenance(tmp_path: Path) -> None:
    variant = _retrieval_variant()
    variant["model_config"] = {"adapter_id": "retrieval-replay.v1", "provider_invocation": False}

    with pytest.raises(EvaluationValidationError) as caught:
        _load_request(tmp_path, retrieval_variant=variant)

    assert caught.value.code is EvaluationErrorCode.RETRIEVAL_REPLAY_INVALID


def test_replay_artifact_path_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        _load_request(tmp_path, retrieval_variant=_replay_variant(replay_path="evals/../replay.json"))

    assert caught.value.code is EvaluationErrorCode.RESOURCE_PATH_INVALID


def test_resolved_hash_changes_when_replay_bytes_change(tmp_path: Path) -> None:
    roots = (tmp_path / "first", tmp_path / "second")
    resolved = []
    for root, evidence_id in zip(roots, ("evidence-first", "evidence-second"), strict=True):
        request_path = _write_request(root, retrieval_variant=_replay_variant())
        dataset_path = root / "evals/retrieval/manifests/dev.dataset.json"
        dataset_path.write_bytes(
            canonical_json_bytes(
                {
                    "case_resources": [{"case_id": "case-1", "partition": "DEV"}],
                    "dataset_code": "rag-retrieval-dev",
                    "dataset_version": "1.0.0",
                }
            )
        )
        replay_path = root / "evals/retrieval/replays/test.replay.json"
        replay_path.parent.mkdir(parents=True, exist_ok=True)
        replay_payload: dict[str, JsonValue] = {
            "case_results": [{"case_id": "case-1", "ranked_evidence_ids": [evidence_id]}],
            "dataset_code": "rag-retrieval-dev",
            "dataset_version": "1.0.0",
            "replay_sha256": "0" * 64,
            "schema_id": "rag-eval.retrieval-replay",
            "schema_version": "1.0.0",
            "top_k": 5,
            "variant_id": "RET-L",
        }
        replay_payload["replay_sha256"] = canonical_sha256(
            replay_payload,
            excluded_top_level_keys=frozenset({"replay_sha256"}),
        )
        replay_path.write_bytes(canonical_json_bytes(replay_payload))
        resolved.append(
            load_dev_execution_request(
                request_path,
                repository_root=root,
                repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
            )
        )

    assert resolved[0].resolved_evaluation_config_hash != resolved[1].resolved_evaluation_config_hash


def test_preflight_is_not_bound_to_foundation_id_status_or_case_count(tmp_path: Path) -> None:
    resolved = _resolved_for_manifest(tmp_path, _manifest_payload())

    preflight_dev_manifest(resolved)


def test_preflight_uses_manifest_snapshot_bound_during_config_resolution(tmp_path: Path) -> None:
    manifest_path = tmp_path / "evals/retrieval/manifests/dev.dataset.json"
    resolved = _resolved_for_manifest(tmp_path, _manifest_payload())
    manifest_path.write_bytes(
        canonical_json_bytes(
            _manifest_payload(
                partition_counts={"AUTHORING": 0, "DEV": 0, "HOLDOUT": 1, "SAFETY_REGRESSION": 0},
                case_resources=[
                    {
                        "case_id": "holdout-001",
                        "partition": "HOLDOUT",
                        "path": "retrieval/cases/not-created.json",
                        "sha256": "1" * 64,
                    }
                ],
            )
        )
    )

    preflight_dev_manifest(resolved)

    assert parse_json_object_bytes(resolved.dataset_manifest_bytes)["partition_counts"] == {
        "AUTHORING": 0,
        "DEV": 1,
        "HOLDOUT": 0,
        "SAFETY_REGRESSION": 0,
    }


def test_snapshot_reader_does_not_reopen_seeded_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    dev_bytes = canonical_json_bytes(_manifest_payload())
    manifest_path.write_bytes(canonical_json_bytes({"partition_counts": {"HOLDOUT": 1}}))
    reader = _SnapshotReader(tmp_path)

    seeded = reader.seed_path(manifest_path, dev_bytes)
    reread = reader.read_path(manifest_path)

    assert reread is seeded
    assert reread.raw_bytes == dev_bytes


@pytest.mark.parametrize(
    "manifest",
    [
        _manifest_payload(
            partition_counts={"AUTHORING": 0, "DEV": 0, "HOLDOUT": 1, "SAFETY_REGRESSION": 0},
            case_resources=[
                {
                    "case_id": "holdout-001",
                    "partition": "HOLDOUT",
                    "path": "retrieval/cases/not-created.json",
                    "sha256": "1" * 64,
                }
            ],
        ),
        _manifest_payload(
            partition_counts={"AUTHORING": 1, "DEV": 1, "HOLDOUT": 0, "SAFETY_REGRESSION": 0},
        ),
        _manifest_payload(
            partition_counts={"AUTHORING": 0, "DEV": 1, "HOLDOUT": 0, "SAFETY_REGRESSION": 1},
        ),
        _manifest_payload(partition_counts={"AUTHORING": 0, "DEV": 0, "HOLDOUT": 0, "SAFETY_REGRESSION": 0}),
        _manifest_payload(data_classification="APPROVED_DEIDENTIFIED"),
        _manifest_payload(
            case_resources=[
                {
                    "case_id": "wrong-partition-001",
                    "partition": "SAFETY_REGRESSION",
                    "path": "retrieval/cases/not-created.json",
                    "sha256": "1" * 64,
                }
            ]
        ),
    ],
)
def test_preflight_rejects_non_dev_or_non_synthetic_manifest(
    tmp_path: Path,
    manifest: dict[str, Any],
) -> None:
    resolved = _resolved_for_manifest(tmp_path, manifest)

    with pytest.raises(EvaluationValidationError) as caught:
        preflight_dev_manifest(resolved)

    assert caught.value.code is EvaluationErrorCode.PARTITION_INVALID


def test_validate_loaded_bindings_accepts_checked_in_dev_graph() -> None:
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    dataset = load_dataset(SOURCE_MANIFEST, evals_root=REPOSITORY_ROOT / "evals")

    validate_loaded_bindings(resolved, dataset)


def test_validate_loaded_bindings_rejects_reference_hash_mismatch() -> None:
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    dataset = load_dataset(SOURCE_MANIFEST, evals_root=REPOSITORY_ROOT / "evals")
    changed = replace(
        resolved,
        referenced_file_hashes=tuple(
            (path, "0" * 64 if path == resolved.request.profile_path else value)
            for path, value in resolved.referenced_file_hashes
        ),
    )

    with pytest.raises(EvaluationValidationError) as caught:
        validate_loaded_bindings(changed, dataset)

    assert caught.value.code is EvaluationErrorCode.HASH_MISMATCH


def test_validate_loaded_bindings_rejects_role_paths_miswired_to_one_case() -> None:
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    dataset = load_dataset(SOURCE_MANIFEST, evals_root=REPOSITORY_ROOT / "evals")
    case_path = f"evals/{dataset.manifest.case_resources[0].path}"
    case_hash = dict(dataset.resource_hashes)[dataset.manifest.case_resources[0].path]
    miswired_request = resolved.request.model_copy(
        update={
            "profile_path": case_path,
            "comparison_policy_path": case_path,
            "evaluation_policy_path": case_path,
            "suite_path": case_path,
        }
    )
    miswired = replace(
        resolved,
        request=miswired_request,
        referenced_file_hashes=(
            (
                resolved.request.dataset_manifest_path,
                dict(resolved.referenced_file_hashes)[resolved.request.dataset_manifest_path],
            ),
            (case_path, case_hash),
            (case_path, case_hash),
            (case_path, case_hash),
            (case_path, case_hash),
        ),
    )

    with pytest.raises(EvaluationValidationError) as caught:
        validate_loaded_bindings(miswired, dataset)

    assert caught.value.code is EvaluationErrorCode.MANIFEST_INVALID


def test_validate_loaded_bindings_rejects_dataset_manifest_path_miswired_to_case() -> None:
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    dataset = load_dataset(SOURCE_MANIFEST, evals_root=REPOSITORY_ROOT / "evals")
    case_path = f"evals/{dataset.manifest.case_resources[0].path}"
    case_hash = dict(dataset.resource_hashes)[dataset.manifest.case_resources[0].path]
    miswired = replace(
        resolved,
        request=resolved.request.model_copy(update={"dataset_manifest_path": case_path}),
        referenced_file_hashes=tuple(
            (case_path, case_hash) if path == resolved.request.dataset_manifest_path else (path, value)
            for path, value in resolved.referenced_file_hashes
        ),
    )

    with pytest.raises(EvaluationValidationError) as caught:
        validate_loaded_bindings(miswired, dataset)

    assert caught.value.code is EvaluationErrorCode.MANIFEST_INVALID


@pytest.mark.parametrize(
    ("dataset_change", "expected_code"),
    [
        ("runtime_eligible", EvaluationErrorCode.STATE_COMBINATION_INVALID),
        ("profile_partition", EvaluationErrorCode.PARTITION_INVALID),
        ("suite_partition", EvaluationErrorCode.PARTITION_INVALID),
        ("experiment_type", EvaluationErrorCode.STATE_COMBINATION_INVALID),
        ("dataset_selector", EvaluationErrorCode.MANIFEST_INVALID),
    ],
)
def test_validate_loaded_bindings_rejects_incompatible_loaded_graph(
    dataset_change: str,
    expected_code: EvaluationErrorCode,
) -> None:
    resolved = load_dev_execution_request(
        REPOSITORY_ROOT / "evals/configs/dev-foundation-knowledge-retrieval-v1.execution.json",
        repository_root=REPOSITORY_ROOT,
        repository_state_provider=lambda _root: RepositoryState("a" * 40, True),
    )
    dataset = load_dataset(SOURCE_MANIFEST, evals_root=REPOSITORY_ROOT / "evals")
    if dataset_change == "runtime_eligible":
        dataset = replace(dataset, profile=dataset.profile.model_copy(update={"runtime_eligible": True}))
    elif dataset_change == "profile_partition":
        dataset = replace(
            dataset, profile=dataset.profile.model_copy(update={"required_partitions": (Partition.HOLDOUT,)})
        )
    elif dataset_change == "suite_partition":
        selector = dataset.suite.input_selector.model_copy(update={"partitions": (Partition.HOLDOUT,)})
        dataset = replace(dataset, suite=dataset.suite.model_copy(update={"input_selector": selector}))
    elif dataset_change == "experiment_type":
        dataset = replace(
            dataset,
            profile=dataset.profile.model_copy(update={"required_experiment_types": (ExperimentType.END_TO_END_RAG,)}),
        )
    else:
        selector = dataset.suite.input_selector.model_copy(update={"dataset_code": "different-dataset"})
        dataset = replace(dataset, suite=dataset.suite.model_copy(update={"input_selector": selector}))

    with pytest.raises(EvaluationValidationError) as caught:
        validate_loaded_bindings(resolved, dataset)

    assert caught.value.code is expected_code
