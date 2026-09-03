from pathlib import Path
from typing import Any

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.config import (
    RepositoryState,
    load_dev_execution_request,
)
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import parse_json_object_bytes, safe_path_under_root

AUTHORITY_MANIFEST_HASH = "f2c98884c841d3fccdbec552f14aad1fd471730eae6d80c472c1b332ed95a570"
REPOSITORY_ROOT = Path(__file__).parents[3]


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
