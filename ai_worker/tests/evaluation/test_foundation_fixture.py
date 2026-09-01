from __future__ import annotations

from pathlib import Path

from ai_worker.tasks.evaluation.loaders import load_dataset
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
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
    assert {case.data_classification for case in loaded.cases} == {ContentClassification.SYNTHETIC}


def test_foundation_dataset_uses_exact_case_and_evidence_ids() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    assert {case.case_id for case in loaded.cases} == {
        "rag-dev-retrieval-001",
        "rag-dev-answer-quality-001",
        "rag-dev-answer-grounding-001",
        "rag-dev-safety-001",
        "rag-dev-end-to-end-001",
    }
    assert {entry.evidence_ref_id for entry in loaded.evidence_mapping.entries} == {
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
    assert loaded.suite.adapter_id == "validation-only.v1"
    assert loaded.suite.input_selector.partitions == (Partition.DEV,)
    assert loaded.manifest.fixture_git_commit_sha is None
    assert loaded.manifest.protected_artifact_receipt_ref is not None


def test_foundation_cases_preserve_discriminator_and_explicit_null_contracts() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    for case in loaded.cases:
        expected = case.expected.model_dump(mode="json", exclude_unset=False)
        assert set(expected) == {
            "relevant_evidence_refs",
            "required_evidence_refs",
            "gold_claims",
            "forbidden_claims",
            "expected_citations",
            "expected_rule_ids",
            "expected_scope_codes",
            "expected_response_level",
            "expected_safety_disposition",
            "expected_execution_status",
            "expected_release_decision",
            "expected_fallback_code",
            "expected_provider_invocation",
            "expected_retrieval_invocation",
            "expected_publication_allowed",
            "expected_sections",
            "omitted_sections",
            "risk_level",
        }
        assert case.query.startswith("SYNTHETIC_")


def test_foundation_load_is_hash_deterministic() -> None:
    first = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)
    second = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)

    assert first == second
    assert first.manifest.manifest_sha256 == second.manifest.manifest_sha256
    assert first.resource_hashes == second.resource_hashes
    assert not hasattr(first, "execute")


def test_foundation_fixture_is_privacy_clean_and_uses_only_known_github_actors() -> None:
    loaded = load_dataset(FOUNDATION_MANIFEST, evals_root=EVALS_ROOT)
    assert loaded.protected_artifact_receipt is not None
    models = (
        loaded.manifest,
        *loaded.cases,
        loaded.evidence_mapping,
        loaded.rubric,
        loaded.profile,
        loaded.comparison_policy,
        loaded.evaluation_policy,
        loaded.suite,
        loaded.protected_artifact_receipt,
    )
    for model in models:
        validate_privacy_boundary(model.model_dump(mode="json"))

    provenance_values = [
        loaded.manifest.review_provenance,
        *(case.review_provenance for case in loaded.cases),
        loaded.evidence_mapping.review_provenance,
        loaded.rubric.review_provenance,
        loaded.profile.review_provenance,
        loaded.evaluation_policy.review_provenance,
        loaded.suite.review_provenance,
        loaded.protected_artifact_receipt.recorded_by,
    ]
    github_actor_ids = {
        actor.actor_id
        for provenance in provenance_values
        for actor in (provenance.authored_by, provenance.reviewed_by)
        if actor.namespace.value == "GITHUB_LOGIN"
    }
    github_actor_ids.update(
        {
            loaded.comparison_policy.proposed_by.actor_id,
            loaded.comparison_policy.approved_by.actor_id,
        }
    )
    assert github_actor_ids == {"ceohwj", "hazelnutflavoured"}
