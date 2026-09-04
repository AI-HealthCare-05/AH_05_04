from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.loaders import load_dataset

EVALS_ROOT = Path(__file__).parents[3] / "evals"
MANIFEST = EVALS_ROOT / "retrieval/manifests/rag-retrieval-dev-v1.dataset.json"
REPLAYS = EVALS_ROOT / "retrieval/replays/rag-retrieval-dev-v1"
EVIDENCE_INDEX = EVALS_ROOT / "retrieval/evidence/resources/rag-retrieval-dev-v1/synthetic-retrieval-index.json"
CASES = EVALS_ROOT / "retrieval/cases/rag-retrieval-dev-v1"


def test_retrieval_dev_dataset_is_synthetic_dev_only_with_five_independent_cases() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)

    assert dataset.manifest.dataset_code == "rag-retrieval-dev"
    assert dataset.manifest.partition_counts.model_dump() == {
        "AUTHORING": 0,
        "DEV": 5,
        "HOLDOUT": 0,
        "SAFETY_REGRESSION": 0,
    }
    assert {case.task_type.value for case in dataset.cases} == {"RETRIEVAL"}
    assert {case.data_classification.value for case in dataset.cases} == {"SYNTHETIC"}
    assert all(len(case.expected.required_evidence_refs or ()) == 1 for case in dataset.cases)


def test_retrieval_dev_cases_use_four_independent_leakage_axes() -> None:
    cases = [json.loads(path.read_bytes()) for path in sorted(CASES.glob("*.json"))]

    for axis in ("medication_family", "question_template", "source_segment", "transform_origin"):
        assert len({case["leakage_group_ids"][axis] for case in cases}) == 5


def test_retrieval_dev_queries_and_evidence_index_are_synthetic_only() -> None:
    cases = [json.loads(path.read_bytes()) for path in sorted(CASES.glob("*.json"))]
    index = json.loads(EVIDENCE_INDEX.read_bytes())

    assert all(case["query"].startswith("SYNTHETIC_") for case in cases)
    assert {record["evidence_ref_id"] for record in index["records"]} == {
        "ev-ret-dev-lifestyle-c",
        "ev-ret-dev-med-a",
        "ev-ret-dev-med-a-detail",
        "ev-ret-dev-missed-dose-e",
        "ev-ret-dev-noise-01",
        "ev-ret-dev-noise-02",
        "ev-ret-dev-noise-03",
        "ev-ret-dev-noise-04",
        "ev-ret-dev-noise-05",
        "ev-ret-dev-precaution-b",
        "ev-ret-dev-storage-d",
    }
    assert all(record["statement"].startswith("SYNTHETIC_") for record in index["records"])


def test_retrieval_dev_gold_matches_the_diagnostic_matrix() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)

    assert {
        case.case_id: (
            list(case.expected.required_evidence_refs or ()),
            list(case.expected.relevant_evidence_refs or ()),
        )
        for case in dataset.cases
    } == {
        "rag-ret-dev-001": (["ev-ret-dev-med-a"], ["ev-ret-dev-med-a", "ev-ret-dev-med-a-detail"]),
        "rag-ret-dev-002": (["ev-ret-dev-precaution-b"], ["ev-ret-dev-precaution-b"]),
        "rag-ret-dev-003": (["ev-ret-dev-lifestyle-c"], ["ev-ret-dev-lifestyle-c"]),
        "rag-ret-dev-004": (["ev-ret-dev-storage-d"], ["ev-ret-dev-storage-d"]),
        "rag-ret-dev-005": (["ev-ret-dev-missed-dose-e"], ["ev-ret-dev-missed-dose-e"]),
    }


@pytest.mark.parametrize(
    ("filename", "variant_id", "ranked_evidence_ids"),
    [
        (
            "ret-l-v1.replay.json",
            "RET-L",
            {
                "rag-ret-dev-001": (
                    "ev-ret-dev-med-a",
                    "ev-ret-dev-noise-01",
                    "ev-ret-dev-noise-02",
                    "ev-ret-dev-noise-03",
                    "ev-ret-dev-noise-04",
                ),
                "rag-ret-dev-002": (
                    "ev-ret-dev-noise-01",
                    "ev-ret-dev-precaution-b",
                    "ev-ret-dev-noise-02",
                    "ev-ret-dev-noise-03",
                    "ev-ret-dev-noise-04",
                ),
                "rag-ret-dev-003": (
                    "ev-ret-dev-noise-01",
                    "ev-ret-dev-noise-02",
                    "ev-ret-dev-noise-03",
                    "ev-ret-dev-lifestyle-c",
                    "ev-ret-dev-noise-04",
                ),
                "rag-ret-dev-004": (
                    "ev-ret-dev-noise-01",
                    "ev-ret-dev-noise-02",
                    "ev-ret-dev-noise-03",
                    "ev-ret-dev-noise-04",
                    "ev-ret-dev-noise-05",
                ),
                "rag-ret-dev-005": (
                    "ev-ret-dev-noise-01",
                    "ev-ret-dev-noise-02",
                    "ev-ret-dev-missed-dose-e",
                    "ev-ret-dev-noise-03",
                    "ev-ret-dev-noise-04",
                ),
            },
        ),
        (
            "ret-hr-v1.replay.json",
            "RET-HR",
            {
                "rag-ret-dev-001": (
                    "ev-ret-dev-med-a",
                    "ev-ret-dev-med-a-detail",
                    "ev-ret-dev-noise-01",
                    "ev-ret-dev-noise-02",
                    "ev-ret-dev-noise-03",
                ),
                "rag-ret-dev-002": (
                    "ev-ret-dev-precaution-b",
                    "ev-ret-dev-noise-01",
                    "ev-ret-dev-noise-02",
                    "ev-ret-dev-noise-03",
                    "ev-ret-dev-noise-04",
                ),
                "rag-ret-dev-003": (
                    "ev-ret-dev-lifestyle-c",
                    "ev-ret-dev-noise-01",
                    "ev-ret-dev-noise-02",
                    "ev-ret-dev-noise-03",
                    "ev-ret-dev-noise-04",
                ),
                "rag-ret-dev-004": (
                    "ev-ret-dev-noise-01",
                    "ev-ret-dev-storage-d",
                    "ev-ret-dev-noise-02",
                    "ev-ret-dev-noise-03",
                    "ev-ret-dev-noise-04",
                ),
                "rag-ret-dev-005": (
                    "ev-ret-dev-missed-dose-e",
                    "ev-ret-dev-noise-01",
                    "ev-ret-dev-noise-02",
                    "ev-ret-dev-noise-03",
                    "ev-ret-dev-noise-04",
                ),
            },
        ),
    ],
)
def test_retrieval_dev_replay_envelope_and_self_hash_are_canonical(
    filename: str,
    variant_id: str,
    ranked_evidence_ids: dict[str, tuple[str, ...]],
) -> None:
    payload = json.loads((REPLAYS / filename).read_bytes())

    assert set(payload) == {
        "case_results",
        "dataset_code",
        "dataset_version",
        "replay_sha256",
        "schema_id",
        "schema_version",
        "top_k",
        "variant_id",
    }
    assert payload["schema_id"] == "rag-eval.retrieval-replay"
    assert payload["schema_version"] == "1.0.0"
    assert payload["dataset_code"] == "rag-retrieval-dev"
    assert payload["dataset_version"] == "1.0.0"
    assert payload["variant_id"] == variant_id
    assert payload["top_k"] == 5
    assert payload["case_results"] == [
        {"case_id": case_id, "ranked_evidence_ids": list(ranks)} for case_id, ranks in ranked_evidence_ids.items()
    ]
    assert payload["replay_sha256"] == canonical_sha256(
        cast(JsonValue, payload),
        excluded_top_level_keys=frozenset({"replay_sha256"}),
    )


def test_retrieval_dev_comparison_policy_is_diagnostic_cluster_bootstrap() -> None:
    policy = load_dataset(MANIFEST, evals_root=EVALS_ROOT).comparison_policy

    assert [scope.metric_id for scope in policy.scopes] == [
        "MRR",
        "NDCG_AT_5",
        "NO_HIT_RATE",
        "PRECISION_AT_5",
        "RECALL_AT_5",
    ]
    for scope in policy.scopes:
        assert scope.partition.value == "DEV"
        assert scope.slice_id == "ALL"
        assert scope.unit_of_analysis == "CASE"
        assert scope.independence_unit == "question_template"
        assert scope.cluster_dimension is not None
        assert scope.cluster_dimension.value == "question_template"
        assert scope.minimum_case_count == 5
        assert scope.minimum_independent_group_count == 5
        assert scope.ci_method_id == "PERCENTILE_CLUSTER_BOOTSTRAP"
        assert scope.ci_method_version == "1.0.0"
        assert dict(scope.ci_parameters) == {
            "iterations": 10000,
            "level": "0.95",
            "sidedness": "TWO_SIDED",
        }
        assert scope.seed == 158
        assert scope.decision_basis == "DIAGNOSTIC_ONLY"
        assert scope.required is False
        assert scope.threshold == "0"
        assert scope.metric_version == "1.0.0"
    assert policy.approved_by.model_dump(mode="json") == {
        "namespace": "SYSTEM",
        "actor_id": "rag-eval-draft-validator",
        "role": "SYSTEM_VALIDATOR",
    }
