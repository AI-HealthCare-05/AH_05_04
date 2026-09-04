from __future__ import annotations

from pathlib import Path

from ai_worker.tasks.evaluation.loaders import load_dataset
from ai_worker.tasks.evaluation.retrieval_replay import load_replay

EVALS_ROOT = Path(__file__).parents[3] / "evals"
MANIFEST = EVALS_ROOT / "retrieval/manifests/rag-retrieval-dev-v1.dataset.json"
REPLAYS = EVALS_ROOT / "retrieval/replays/rag-retrieval-dev-v1"


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
    assert len({case.leakage_group_ids.question_template for case in dataset.cases}) == 5
    assert all(len(case.expected.required_evidence_refs or ()) == 1 for case in dataset.cases)


def test_retrieval_dev_gold_and_replay_ranks_match_the_diagnostic_matrix() -> None:
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
    assert load_replay(REPLAYS / "ret-l-v1.replay.json") == {
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
    }
    assert load_replay(REPLAYS / "ret-hr-v1.replay.json") == {
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
    }


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
