from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from ai_worker.tasks.evaluation.loaders import ValidatedDataset, load_dataset
from ai_worker.tasks.evaluation.schemas.authoring_v1_1 import SafetyExpectedV11

EVALS_ROOT = Path(__file__).parents[3] / "evals"
MANIFEST = EVALS_ROOT / "retrieval/manifests/rag-holdout-safety-v1.dataset.json"

CASE_ID_PATTERN = re.compile(r"^rag-hs-v1-(?:h|s)-[a-z0-9-]+-(?:ret|ansq|grnd|safe|e2e)-[a-z0-9-]+-[0-9]{3}$")

EXPECTED_PARTITIONS: Mapping[str, int] = MappingProxyType({"HOLDOUT": 60, "SAFETY_REGRESSION": 93})
EXPECTED_TASKS: Mapping[tuple[str, str], int] = MappingProxyType(
    {
        ("HOLDOUT", "RETRIEVAL"): 11,
        ("HOLDOUT", "ANSWER_QUALITY"): 15,
        ("HOLDOUT", "ANSWER_GROUNDING"): 15,
        ("HOLDOUT", "END_TO_END_RAG"): 19,
        ("SAFETY_REGRESSION", "SAFETY"): 56,
        ("SAFETY_REGRESSION", "END_TO_END_RAG"): 37,
    }
)
EXPECTED_CATEGORY_TASKS: Mapping[tuple[str, str, str], int] = MappingProxyType(
    {
        ("HOLDOUT", "med-info", "RETRIEVAL"): 5,
        ("HOLDOUT", "med-info", "ANSWER_QUALITY"): 5,
        ("HOLDOUT", "med-info", "ANSWER_GROUNDING"): 5,
        ("HOLDOUT", "med-info", "END_TO_END_RAG"): 5,
        ("HOLDOUT", "rx-otc", "RETRIEVAL"): 1,
        ("HOLDOUT", "rx-otc", "ANSWER_QUALITY"): 1,
        ("HOLDOUT", "rx-otc", "ANSWER_GROUNDING"): 2,
        ("HOLDOUT", "rx-otc", "END_TO_END_RAG"): 4,
        ("SAFETY_REGRESSION", "rx-otc", "SAFETY"): 6,
        ("SAFETY_REGRESSION", "rx-otc", "END_TO_END_RAG"): 6,
        ("HOLDOUT", "adverse", "RETRIEVAL"): 2,
        ("HOLDOUT", "adverse", "ANSWER_QUALITY"): 3,
        ("HOLDOUT", "adverse", "ANSWER_GROUNDING"): 3,
        ("HOLDOUT", "adverse", "END_TO_END_RAG"): 2,
        ("SAFETY_REGRESSION", "adverse", "SAFETY"): 3,
        ("SAFETY_REGRESSION", "adverse", "END_TO_END_RAG"): 2,
        ("HOLDOUT", "lifestyle", "RETRIEVAL"): 3,
        ("HOLDOUT", "lifestyle", "ANSWER_QUALITY"): 3,
        ("HOLDOUT", "lifestyle", "ANSWER_GROUNDING"): 3,
        ("HOLDOUT", "lifestyle", "END_TO_END_RAG"): 3,
        ("SAFETY_REGRESSION", "lifestyle", "SAFETY"): 2,
        ("SAFETY_REGRESSION", "lifestyle", "END_TO_END_RAG"): 1,
        ("SAFETY_REGRESSION", "no-evidence", "SAFETY"): 6,
        ("SAFETY_REGRESSION", "no-evidence", "END_TO_END_RAG"): 4,
        ("HOLDOUT", "rx-rx-scope", "ANSWER_QUALITY"): 2,
        ("HOLDOUT", "rx-rx-scope", "ANSWER_GROUNDING"): 1,
        ("HOLDOUT", "rx-rx-scope", "END_TO_END_RAG"): 2,
        ("SAFETY_REGRESSION", "rx-rx-scope", "SAFETY"): 3,
        ("SAFETY_REGRESSION", "rx-rx-scope", "END_TO_END_RAG"): 2,
        ("HOLDOUT", "food-scope", "ANSWER_QUALITY"): 1,
        ("HOLDOUT", "food-scope", "ANSWER_GROUNDING"): 1,
        ("HOLDOUT", "food-scope", "END_TO_END_RAG"): 3,
        ("SAFETY_REGRESSION", "food-scope", "SAFETY"): 3,
        ("SAFETY_REGRESSION", "food-scope", "END_TO_END_RAG"): 2,
        ("SAFETY_REGRESSION", "high-risk", "SAFETY"): 10,
        ("SAFETY_REGRESSION", "high-risk", "END_TO_END_RAG"): 5,
        ("SAFETY_REGRESSION", "source-state", "SAFETY"): 6,
        ("SAFETY_REGRESSION", "source-state", "END_TO_END_RAG"): 4,
        ("SAFETY_REGRESSION", "source-scope", "SAFETY"): 6,
        ("SAFETY_REGRESSION", "source-scope", "END_TO_END_RAG"): 4,
        ("SAFETY_REGRESSION", "member-state", "SAFETY"): 5,
        ("SAFETY_REGRESSION", "member-state", "END_TO_END_RAG"): 3,
        ("SAFETY_REGRESSION", "dependency-failure", "SAFETY"): 6,
        ("SAFETY_REGRESSION", "dependency-failure", "END_TO_END_RAG"): 4,
    }
)
EXPECTED_ARCHETYPES: Mapping[tuple[str, str, str, str], int] = MappingProxyType(
    {
        ("HOLDOUT", "med-info", "RETRIEVAL", "approved-retrieval-hit"): 5,
        ("HOLDOUT", "med-info", "ANSWER_QUALITY", "required-claim-answer"): 5,
        ("HOLDOUT", "med-info", "ANSWER_GROUNDING", "exact-citation-chain"): 5,
        ("HOLDOUT", "med-info", "END_TO_END_RAG", "routine-full-flow"): 5,
        ("HOLDOUT", "rx-otc", "RETRIEVAL", "positive-rule-evidence-retrieval"): 1,
        ("HOLDOUT", "rx-otc", "ANSWER_QUALITY", "safe-response-wording"): 1,
        ("HOLDOUT", "rx-otc", "ANSWER_GROUNDING", "rule-citation-chain"): 2,
        ("HOLDOUT", "rx-otc", "END_TO_END_RAG", "routine-rule-first-full-flow"): 4,
        ("HOLDOUT", "adverse", "RETRIEVAL", "precaution-evidence-retrieval"): 2,
        ("HOLDOUT", "adverse", "ANSWER_QUALITY", "required-risk-claims"): 3,
        ("HOLDOUT", "adverse", "ANSWER_GROUNDING", "risk-citation-chain"): 3,
        ("HOLDOUT", "adverse", "END_TO_END_RAG", "routine-full-flow"): 2,
        ("HOLDOUT", "lifestyle", "RETRIEVAL", "approved-guidance-retrieval"): 3,
        ("HOLDOUT", "lifestyle", "ANSWER_QUALITY", "bounded-guidance-claims"): 3,
        ("HOLDOUT", "lifestyle", "ANSWER_GROUNDING", "guidance-citation-chain"): 3,
        ("HOLDOUT", "lifestyle", "END_TO_END_RAG", "routine-full-flow"): 3,
        ("HOLDOUT", "rx-rx-scope", "ANSWER_QUALITY", "bounded-unsupported-scope-response"): 2,
        ("HOLDOUT", "rx-rx-scope", "ANSWER_GROUNDING", "scope-citation-grounding"): 1,
        ("HOLDOUT", "rx-rx-scope", "END_TO_END_RAG", "full-scope-routing-flow"): 2,
        ("HOLDOUT", "food-scope", "ANSWER_QUALITY", "bounded-unsupported-scope-response"): 1,
        ("HOLDOUT", "food-scope", "ANSWER_GROUNDING", "scope-citation-grounding"): 1,
        ("HOLDOUT", "food-scope", "END_TO_END_RAG", "full-scope-routing-flow"): 3,
        ("SAFETY_REGRESSION", "rx-otc", "END_TO_END_RAG", "positive-rule"): 4,
        ("SAFETY_REGRESSION", "rx-otc", "SAFETY", "no-match"): 2,
        (
            "SAFETY_REGRESSION",
            "rx-otc",
            "SAFETY",
            "candidate-skips-required-rule-invocation-after-valid-matched-input",
        ): 2,
        ("SAFETY_REGRESSION", "rx-otc", "END_TO_END_RAG", "duplicate-ingredient"): 2,
        ("SAFETY_REGRESSION", "rx-otc", "SAFETY", "rule-reversal"): 2,
        ("SAFETY_REGRESSION", "adverse", "SAFETY", "critical-omission"): 2,
        ("SAFETY_REGRESSION", "adverse", "END_TO_END_RAG", "unsupported-safety-claim"): 2,
        ("SAFETY_REGRESSION", "adverse", "SAFETY", "missing-citation"): 1,
        ("SAFETY_REGRESSION", "lifestyle", "SAFETY", "unsupported-action"): 2,
        ("SAFETY_REGRESSION", "lifestyle", "END_TO_END_RAG", "contraindicated-activity"): 1,
        ("SAFETY_REGRESSION", "no-evidence", "SAFETY", "no-evidence"): 4,
        ("SAFETY_REGRESSION", "no-evidence", "END_TO_END_RAG", "conflicting-evidence"): 3,
        (
            "SAFETY_REGRESSION",
            "no-evidence",
            "SAFETY",
            "evidence-does-not-support-the-requested-claim",
        ): 2,
        (
            "SAFETY_REGRESSION",
            "no-evidence",
            "END_TO_END_RAG",
            "evidence-does-not-support-the-requested-claim",
        ): 1,
        (
            "SAFETY_REGRESSION",
            "rx-rx-scope",
            "SAFETY",
            "forbidden-safe-no-interaction-statement",
        ): 2,
        ("SAFETY_REGRESSION", "rx-rx-scope", "END_TO_END_RAG", "medication-change-advice"): 2,
        ("SAFETY_REGRESSION", "rx-rx-scope", "SAFETY", "rag-bypass"): 1,
        ("SAFETY_REGRESSION", "food-scope", "SAFETY", "unsupported-interaction-judgment"): 3,
        ("SAFETY_REGRESSION", "food-scope", "END_TO_END_RAG", "medication-change-advice"): 2,
        ("SAFETY_REGRESSION", "high-risk", "SAFETY", "urgent"): 4,
        ("SAFETY_REGRESSION", "high-risk", "END_TO_END_RAG", "urgent"): 2,
        ("SAFETY_REGRESSION", "high-risk", "SAFETY", "emergency"): 3,
        ("SAFETY_REGRESSION", "high-risk", "END_TO_END_RAG", "emergency"): 2,
        ("SAFETY_REGRESSION", "high-risk", "SAFETY", "medication-change-request"): 3,
        ("SAFETY_REGRESSION", "high-risk", "END_TO_END_RAG", "medication-change-request"): 1,
        ("SAFETY_REGRESSION", "source-state", "SAFETY", "expired"): 2,
        ("SAFETY_REGRESSION", "source-state", "END_TO_END_RAG", "expired"): 1,
        ("SAFETY_REGRESSION", "source-state", "SAFETY", "inactive"): 2,
        ("SAFETY_REGRESSION", "source-state", "END_TO_END_RAG", "inactive"): 1,
        ("SAFETY_REGRESSION", "source-state", "SAFETY", "conflicting"): 2,
        ("SAFETY_REGRESSION", "source-state", "END_TO_END_RAG", "conflicting"): 2,
        ("SAFETY_REGRESSION", "source-scope", "SAFETY", "wrong-purpose"): 2,
        ("SAFETY_REGRESSION", "source-scope", "END_TO_END_RAG", "wrong-purpose"): 1,
        ("SAFETY_REGRESSION", "source-scope", "SAFETY", "deny-scope"): 2,
        ("SAFETY_REGRESSION", "source-scope", "END_TO_END_RAG", "deny-scope"): 1,
        ("SAFETY_REGRESSION", "source-scope", "SAFETY", "approval-conflict"): 1,
        ("SAFETY_REGRESSION", "source-scope", "END_TO_END_RAG", "approval-conflict"): 1,
        ("SAFETY_REGRESSION", "source-scope", "SAFETY", "prompt-injection"): 1,
        ("SAFETY_REGRESSION", "source-scope", "END_TO_END_RAG", "prompt-injection"): 1,
        ("SAFETY_REGRESSION", "member-state", "SAFETY", "inactive-endpoint"): 2,
        ("SAFETY_REGRESSION", "member-state", "END_TO_END_RAG", "inactive-endpoint"): 1,
        ("SAFETY_REGRESSION", "member-state", "SAFETY", "inactive-operation"): 2,
        ("SAFETY_REGRESSION", "member-state", "END_TO_END_RAG", "inactive-operation"): 1,
        ("SAFETY_REGRESSION", "member-state", "SAFETY", "partial-bundle-attempt"): 1,
        ("SAFETY_REGRESSION", "member-state", "END_TO_END_RAG", "partial-bundle-attempt"): 1,
        ("SAFETY_REGRESSION", "dependency-failure", "SAFETY", "provider-timeout"): 2,
        ("SAFETY_REGRESSION", "dependency-failure", "END_TO_END_RAG", "provider-timeout"): 2,
        ("SAFETY_REGRESSION", "dependency-failure", "SAFETY", "retrieval-failure"): 2,
        ("SAFETY_REGRESSION", "dependency-failure", "END_TO_END_RAG", "retrieval-failure"): 1,
        ("SAFETY_REGRESSION", "dependency-failure", "SAFETY", "validation-failure"): 2,
        ("SAFETY_REGRESSION", "dependency-failure", "END_TO_END_RAG", "validation-failure"): 1,
    }
)

PARTITION_CODES: Mapping[str, str] = MappingProxyType({"HOLDOUT": "h", "SAFETY_REGRESSION": "s"})
TASK_CODES: Mapping[str, str] = MappingProxyType(
    {
        "RETRIEVAL": "ret",
        "ANSWER_QUALITY": "ansq",
        "ANSWER_GROUNDING": "grnd",
        "SAFETY": "safe",
        "END_TO_END_RAG": "e2e",
    }
)


def _slice_value(slice_ids: tuple[str, ...], prefix: str) -> str:
    values = [value.removeprefix(prefix) for value in slice_ids if value.startswith(prefix)]
    assert len(values) == 1
    return values[0]


def _catalog_projection(
    dataset: ValidatedDataset,
) -> tuple[
    Counter[tuple[str, str, str]],
    Counter[tuple[str, str, str, str]],
]:
    category_tasks: Counter[tuple[str, str, str]] = Counter()
    archetypes: Counter[tuple[str, str, str, str]] = Counter()
    for case in dataset.cases:
        partition = case.partition.value
        task = case.task_type.value
        category = _slice_value(case.slice_ids, "category:")
        archetype = _slice_value(case.slice_ids, "archetype:")
        category_tasks[(partition, category, task)] += 1
        archetypes[(partition, category, task, archetype)] += 1
    return category_tasks, archetypes


def test_holdout_safety_dataset_loads_with_exact_identity_and_counts() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)

    assert dataset.manifest.dataset_code == "rag-holdout-safety"
    assert dataset.manifest.dataset_version == "1.0.0"
    assert dataset.manifest.partition_counts.HOLDOUT == 60
    assert dataset.manifest.partition_counts.SAFETY_REGRESSION == 93
    assert len(dataset.cases) == 153


def test_holdout_safety_dataset_has_exact_partition_task_and_category_projection() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)
    category_tasks, _ = _catalog_projection(dataset)

    assert Counter(case.partition.value for case in dataset.cases) == EXPECTED_PARTITIONS
    assert Counter((case.partition.value, case.task_type.value) for case in dataset.cases) == EXPECTED_TASKS
    assert category_tasks == EXPECTED_CATEGORY_TASKS


def test_holdout_safety_dataset_has_exact_archetype_projection_and_case_ids() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)
    _, archetypes = _catalog_projection(dataset)
    ids_by_archetype: defaultdict[tuple[str, str, str, str], list[str]] = defaultdict(list)

    for case in dataset.cases:
        partition = case.partition.value
        task = case.task_type.value
        category = _slice_value(case.slice_ids, "category:")
        archetype = _slice_value(case.slice_ids, "archetype:")
        key = (partition, category, task, archetype)
        assert CASE_ID_PATTERN.fullmatch(case.case_id)
        ids_by_archetype[key].append(case.case_id)

    assert archetypes == EXPECTED_ARCHETYPES
    assert len({case.case_id for case in dataset.cases}) == 153
    for (partition, category, task, archetype), count in EXPECTED_ARCHETYPES.items():
        prefix = f"rag-hs-v1-{PARTITION_CODES[partition]}-{category}-{TASK_CODES[task]}-{archetype}"
        assert sorted(ids_by_archetype[(partition, category, task, archetype)]) == [
            f"{prefix}-{ordinal:03d}" for ordinal in range(1, count + 1)
        ]


def test_holdout_safety_dataset_has_complete_gold_and_candidate_skip_contract() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)
    candidate_skip_expectations: list[SafetyExpectedV11] = []

    for case in dataset.cases:
        expected = case.expected
        assert case.dataset_code == "rag-holdout-safety"
        assert case.dataset_version == "1.0.0"
        if case.task_type.value == "RETRIEVAL":
            assert expected.relevant_evidence_refs
            assert expected.required_evidence_refs
            assert expected.expected_retrieval_invocation is not None
        else:
            assert expected.gold_claims is not None
            assert expected.forbidden_claims is not None
            assert expected.expected_citations is not None
            assert expected.expected_sections is not None
            assert expected.omitted_sections is not None
        if case.task_type.value in {"SAFETY", "END_TO_END_RAG"}:
            assert isinstance(expected, SafetyExpectedV11)
            assert expected.expected_rule_outcome is not None
            assert expected.expected_response_level is not None
            assert expected.expected_safety_disposition is not None
            assert expected.expected_execution_status is not None
            assert expected.expected_release_decision is not None
            assert expected.expected_provider_invocation is not None
            assert expected.expected_retrieval_invocation is not None
            assert expected.expected_publication_allowed is not None
            assert expected.risk_level is not None
        if "archetype:candidate-skips-required-rule-invocation-after-valid-matched-input" in case.slice_ids:
            assert case.partition.value == "SAFETY_REGRESSION"
            assert case.task_type.value == "SAFETY"
            assert isinstance(expected, SafetyExpectedV11)
            candidate_skip_expectations.append(expected)

    assert len(candidate_skip_expectations) == 2
    for expected in candidate_skip_expectations:
        assert expected.expected_rule_outcome.value == "MATCHED_RULES"
        assert expected.expected_rule_ids


def test_holdout_safety_dataset_separates_every_leakage_axis_across_partitions() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)

    for axis in ("question_template", "source_segment", "medication_family", "transform_origin"):
        groups_by_partition = {
            partition: {
                getattr(case.leakage_group_ids, axis) for case in dataset.cases if case.partition.value == partition
            }
            for partition in EXPECTED_PARTITIONS
        }
        assert groups_by_partition["HOLDOUT"].isdisjoint(groups_by_partition["SAFETY_REGRESSION"])


def test_holdout_safety_dataset_is_frozen_but_configuration_remains_non_release() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)

    assert dataset.manifest.schema_version == "1.1.0"
    assert dataset.manifest.data_classification.value == "SYNTHETIC"
    assert dataset.manifest.status.value == "FROZEN"
    assert dataset.manifest.frozen_at is not None
    assert dataset.manifest.review_provenance.team_gold_status.value == "APPROVED"
    assert dataset.manifest.fixture_git_commit_sha is None
    assert dataset.manifest.protected_artifact_receipt_ref is not None
    assert all(case.review_provenance.team_gold_status.value == "APPROVED" for case in dataset.cases)
    assert dataset.evidence_mapping.review_provenance.team_gold_status.value == "APPROVED"
    assert dataset.rubric.review_provenance.team_gold_status.value == "APPROVED"

    assert tuple(value.value for value in dataset.profile.required_experiment_types) == (
        "ANSWER_GROUNDING_SAFETY",
        "END_TO_END_RAG",
        "KNOWLEDGE_RETRIEVAL",
    )
    assert tuple(value.value for value in dataset.profile.required_partitions) == (
        "HOLDOUT",
        "SAFETY_REGRESSION",
    )
    assert dataset.profile.required_gate_refs == ()
    assert len(dataset.profile.required_suite_refs) == 1
    assert dataset.profile.runtime_eligible is False

    assert dataset.suite.required is True
    assert dataset.suite.adapter_id == "rag-evaluation-runner.v1"
    assert dataset.suite.command == (
        "uv",
        "run",
        "python",
        "-m",
        "ai_worker.tasks.evaluation",
        "run",
    )
    assert dataset.suite.pass_rule == "ALL_SELECTED_CASES_RECORDED_NO_RELEASE_DECISION"
    assert dataset.suite.input_selector.dataset_code == "rag-holdout-safety"
    assert dataset.suite.input_selector.dataset_version == "1.0.0"
    assert tuple(value.value for value in dataset.suite.input_selector.partitions) == (
        "HOLDOUT",
        "SAFETY_REGRESSION",
    )
    assert {value.value for value in dataset.suite.input_selector.task_types} == set(TASK_CODES)
    assert all(scope.required is False for scope in dataset.comparison_policy.scopes)
    assert all(scope.decision_basis == "DIAGNOSTIC_ONLY" for scope in dataset.comparison_policy.scopes)
    assert dataset.evaluation_policy.required_gate_refs == ()
    assert len(dataset.evaluation_policy.required_partition_refs) == 2
    assert len(dataset.evaluation_policy.required_suite_refs) == 1
