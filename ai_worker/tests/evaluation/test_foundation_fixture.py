from __future__ import annotations

from pathlib import Path

from ai_worker.tasks.evaluation.loaders import load_dataset
from ai_worker.tasks.evaluation.schemas import ContentClassification, Partition, TaskType

EVALS_ROOT = Path(__file__).parents[3] / "evals"
FOUNDATION_MANIFEST = EVALS_ROOT / "retrieval/manifests/dev-foundation-v1.dataset.json"


def test_foundation_dataset_has_one_case_per_task_and_only_dev() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    assert len(loaded.cases) == 5
    assert {case.task_type for case in loaded.cases} == {
        TaskType.RETRIEVAL,
        TaskType.ANSWER_QUALITY,
        TaskType.ANSWER_GROUNDING,
        TaskType.SAFETY,
        TaskType.END_TO_END_RAG,
    }
    assert {case.partition for case in loaded.cases} == {Partition.DEV}
    assert {case.content_classification for case in loaded.cases} == {ContentClassification.SYNTHETIC}


def test_foundation_dataset_uses_exact_case_and_evidence_ids() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    assert [case.case_id for case in loaded.cases] == [
        "rag-dev-retrieval-001",
        "rag-dev-answer-quality-001",
        "rag-dev-answer-grounding-001",
        "rag-dev-safety-001",
        "rag-dev-end-to-end-001",
    ]
    assert {entry.evidence_id for entry in loaded.evidence_mapping.evidence} == {
        "ev-synthetic-prescription-001",
        "ev-synthetic-chunk-001",
        "ev-synthetic-rule-001",
        "ev-synthetic-guideline-001",
        "ev-synthetic-safety-policy-001",
    }


def test_foundation_configuration_is_validation_only_and_non_release() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    assert loaded.profile.runtime_eligible is False
    assert loaded.profile.required_partitions == (Partition.DEV,)
    assert loaded.suite.required is False
    assert loaded.suite.suite_code == "validation-only.v1"
    assert loaded.suite.partitions == (Partition.DEV,)
    assert loaded.manifest.fixture_git_commit_sha is None
    assert loaded.manifest.protected_artifact_receipt_ref is not None


def test_foundation_cases_preserve_discriminator_and_explicit_null_contracts() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    for case in loaded.cases:
        expected = case.expected.model_dump(mode="json", exclude_unset=False)
        assert set(expected) == {
            "gold_evidence_ids",
            "gold_claims",
            "gold_citation_evidence_ids",
            "gold_rule_ids",
            "expected_scope",
            "expected_safety_disposition",
        }
        assert case.question.startswith("SYNTHETIC_")


def test_foundation_load_is_hash_deterministic() -> None:
    first = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)
    second = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    assert first == second
    assert first.manifest.content_hash == second.manifest.content_hash
    assert first.resource_hashes == second.resource_hashes
    assert not hasattr(first, "execute")
