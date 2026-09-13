from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.release_policy import load_release_policy

EVALS_ROOT = Path(__file__).parents[3] / "evals"


def _copy_policy_graph(tmp_path: Path) -> tuple[Path, Path, Path]:
    paths = (
        EVALS_ROOT / "policies/dev-foundation-v1.evaluation-policy.json",
        EVALS_ROOT / "profiles/dev-foundation-v1.profile.json",
        EVALS_ROOT / "policies/dev-foundation-v1.comparison-policy.json",
    )
    copied: list[Path] = []
    for source in paths:
        destination = tmp_path / source.name
        destination.write_bytes(source.read_bytes())
        copied.append(destination)
    return copied[0], copied[1], copied[2]


def test_release_policy_loader_binds_existing_policy_profile_and_comparison(tmp_path: Path) -> None:
    policy_path, profile_path, comparison_path = _copy_policy_graph(tmp_path)

    loaded = load_release_policy(policy_path, profile_path, comparison_path)

    assert loaded.evaluation_policy_ref.id == "rag-dev-foundation-policy"
    assert loaded.evaluation_profile_ref.id == "rag-dev-foundation-profile"
    assert loaded.comparison_policy_ref.id == "rag-dev-foundation-comparison"
    assert [item.metric_id for item in loaded.required_metrics] == []


def test_release_policy_loader_accepts_frozen_v1_2_policy_graph() -> None:
    loaded = load_release_policy(
        EVALS_ROOT / "policies/rag-holdout-safety-v1.evaluation-policy.json",
        EVALS_ROOT / "profiles/rag-holdout-safety-v1.profile.json",
        EVALS_ROOT / "policies/rag-holdout-safety-v1.comparison-policy.json",
    )

    assert loaded.evaluation_policy_ref.id == "rag-holdout-safety-policy"
    assert [item.value for item in loaded.required_partitions] == ["HOLDOUT", "SAFETY_REGRESSION"]


def test_release_policy_loader_rejects_exact_reference_hash_mismatch(tmp_path: Path) -> None:
    policy_path, profile_path, comparison_path = _copy_policy_graph(tmp_path)
    policy = json.loads(policy_path.read_bytes())
    policy["evaluation_profile_ref"]["reference"]["hash"] = "f" * 64
    policy["member_manifest_hash"] = canonical_sha256(
        {
            "members": [
                policy["evaluation_profile_ref"],
                policy["comparison_policy_ref"],
                *policy["required_partition_refs"],
                *policy["required_gate_refs"],
                *policy["required_suite_refs"],
                policy["artifact_schema_set_ref"],
            ]
        }
    )
    policy["evaluation_policy_hash"] = canonical_sha256(
        policy,
        excluded_top_level_keys=frozenset({"evaluation_policy_hash"}),
    )
    policy_path.write_bytes(canonical_json_bytes(policy))

    with pytest.raises(EvaluationValidationError) as caught:
        load_release_policy(policy_path, profile_path, comparison_path)

    assert caught.value.code is EvaluationErrorCode.HASH_MISMATCH
