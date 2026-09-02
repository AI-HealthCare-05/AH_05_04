from __future__ import annotations

import json
import re
import shutil
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.cli import main as evaluation_cli_main
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import ValidatedDataset, load_dataset
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.schemas.authoring_v1_1 import (
    EVALUATION_CASE_ADAPTER_V1_1,
    EvaluationCaseV11,
    SafetyExpectedV11,
)

EVALS_ROOT = Path(__file__).parents[3] / "evals"
MANIFEST = EVALS_ROOT / "retrieval/manifests/rag-holdout-safety-v1.dataset.json"
CASE_ROOT = EVALS_ROOT / "retrieval/cases/rag-holdout-safety-v1"
PREFIX = "rag-holdout-safety-v1"

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
        ("SAFETY_REGRESSION", "dependency-failure", "SAFETY", "provider-timeout"): 3,
        ("SAFETY_REGRESSION", "dependency-failure", "END_TO_END_RAG", "provider-timeout"): 2,
        ("SAFETY_REGRESSION", "dependency-failure", "SAFETY", "retrieval-failure"): 3,
        ("SAFETY_REGRESSION", "dependency-failure", "END_TO_END_RAG", "retrieval-failure"): 2,
    }
)

EXPECTED_LEAKAGE_GROUP_COUNTS: Mapping[str, Mapping[tuple[str, str], int]] = MappingProxyType(
    {
        "question_template": MappingProxyType(
            {
                ("HOLDOUT", "SYNTHETIC_QUESTION_TEMPLATE_FIND_REVIEWED_RECORD"): 11,
                ("HOLDOUT", "SYNTHETIC_QUESTION_TEMPLATE_SHORT_EXPLANATION"): 15,
                ("HOLDOUT", "SYNTHETIC_QUESTION_TEMPLATE_SUPPORTING_RECORD"): 15,
                ("HOLDOUT", "SYNTHETIC_QUESTION_TEMPLATE_COMPLETE_RESPONSE"): 19,
                ("SAFETY_REGRESSION", "SYNTHETIC_QUESTION_TEMPLATE_USER_REPORT_REPLY"): 56,
                ("SAFETY_REGRESSION", "SYNTHETIC_QUESTION_TEMPLATE_FINAL_MESSAGE"): 37,
            }
        ),
        "source_segment": MappingProxyType(
            {
                ("HOLDOUT", "SYNTHETIC_SOURCE_SEGMENT_H_ADVERSE"): 10,
                ("HOLDOUT", "SYNTHETIC_SOURCE_SEGMENT_H_LIFESTYLE"): 12,
                ("HOLDOUT", "SYNTHETIC_SOURCE_SEGMENT_H_MED_INFO"): 20,
                ("HOLDOUT", "SYNTHETIC_SOURCE_SEGMENT_H_RX_OTC"): 8,
                ("HOLDOUT", "SYNTHETIC_SOURCE_SEGMENT_H_SCOPE_POLICY"): 10,
                ("SAFETY_REGRESSION", "SYNTHETIC_SOURCE_SEGMENT_S_ADVERSE"): 5,
                ("SAFETY_REGRESSION", "SYNTHETIC_SOURCE_SEGMENT_S_EMERGENCY_POLICY"): 5,
                ("SAFETY_REGRESSION", "SYNTHETIC_SOURCE_SEGMENT_S_FAIL_CLOSED_POLICY"): 58,
                ("SAFETY_REGRESSION", "SYNTHETIC_SOURCE_SEGMENT_S_LIFESTYLE"): 3,
                ("SAFETY_REGRESSION", "SYNTHETIC_SOURCE_SEGMENT_S_RX_OTC"): 12,
                ("SAFETY_REGRESSION", "SYNTHETIC_SOURCE_SEGMENT_S_URGENT_POLICY"): 10,
            }
        ),
        "medication_family": MappingProxyType(
            {
                ("HOLDOUT", "SYNTHETIC_MEDICATION_FAMILY_H_CINDER"): 15,
                ("HOLDOUT", "SYNTHETIC_MEDICATION_FAMILY_H_GROVE"): 17,
                ("HOLDOUT", "SYNTHETIC_MEDICATION_FAMILY_H_HARBOR"): 28,
                ("SAFETY_REGRESSION", "SYNTHETIC_MEDICATION_FAMILY_S_AURORA"): 15,
                ("SAFETY_REGRESSION", "SYNTHETIC_MEDICATION_FAMILY_S_CEDAR"): 10,
                ("SAFETY_REGRESSION", "SYNTHETIC_MEDICATION_FAMILY_S_COMET"): 10,
                ("SAFETY_REGRESSION", "SYNTHETIC_MEDICATION_FAMILY_S_EMBER"): 5,
                ("SAFETY_REGRESSION", "SYNTHETIC_MEDICATION_FAMILY_S_LANTERN"): 8,
                ("SAFETY_REGRESSION", "SYNTHETIC_MEDICATION_FAMILY_S_NEBULA"): 12,
                ("SAFETY_REGRESSION", "SYNTHETIC_MEDICATION_FAMILY_S_ONYX"): 5,
                ("SAFETY_REGRESSION", "SYNTHETIC_MEDICATION_FAMILY_S_QUARTZ"): 10,
                ("SAFETY_REGRESSION", "SYNTHETIC_MEDICATION_FAMILY_S_SAFFRON"): 5,
                ("SAFETY_REGRESSION", "SYNTHETIC_MEDICATION_FAMILY_S_STORM"): 3,
                ("SAFETY_REGRESSION", "SYNTHETIC_MEDICATION_FAMILY_S_TIDAL"): 10,
            }
        ),
        "transform_origin": MappingProxyType(
            {
                ("HOLDOUT", "SYNTHETIC_TRANSFORM_ORIGIN_H_ADVERSE"): 10,
                ("HOLDOUT", "SYNTHETIC_TRANSFORM_ORIGIN_H_FOOD_SCOPE"): 5,
                ("HOLDOUT", "SYNTHETIC_TRANSFORM_ORIGIN_H_LIFESTYLE"): 12,
                ("HOLDOUT", "SYNTHETIC_TRANSFORM_ORIGIN_H_MED_INFO"): 20,
                ("HOLDOUT", "SYNTHETIC_TRANSFORM_ORIGIN_H_RX_OTC"): 8,
                ("HOLDOUT", "SYNTHETIC_TRANSFORM_ORIGIN_H_RX_RX_SCOPE"): 5,
                ("SAFETY_REGRESSION", "SYNTHETIC_TRANSFORM_ORIGIN_S_ADVERSE"): 5,
                ("SAFETY_REGRESSION", "SYNTHETIC_TRANSFORM_ORIGIN_S_DEPENDENCY_FAILURE"): 10,
                ("SAFETY_REGRESSION", "SYNTHETIC_TRANSFORM_ORIGIN_S_FOOD_SCOPE"): 5,
                ("SAFETY_REGRESSION", "SYNTHETIC_TRANSFORM_ORIGIN_S_HIGH_RISK"): 15,
                ("SAFETY_REGRESSION", "SYNTHETIC_TRANSFORM_ORIGIN_S_LIFESTYLE"): 3,
                ("SAFETY_REGRESSION", "SYNTHETIC_TRANSFORM_ORIGIN_S_MEMBER_STATE"): 8,
                ("SAFETY_REGRESSION", "SYNTHETIC_TRANSFORM_ORIGIN_S_NO_EVIDENCE"): 10,
                ("SAFETY_REGRESSION", "SYNTHETIC_TRANSFORM_ORIGIN_S_RX_OTC"): 12,
                ("SAFETY_REGRESSION", "SYNTHETIC_TRANSFORM_ORIGIN_S_RX_RX_SCOPE"): 5,
                ("SAFETY_REGRESSION", "SYNTHETIC_TRANSFORM_ORIGIN_S_SOURCE_SCOPE"): 10,
                ("SAFETY_REGRESSION", "SYNTHETIC_TRANSFORM_ORIGIN_S_SOURCE_STATE"): 10,
            }
        ),
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
ID_CATEGORY_CODE_ALIASES: Mapping[str, str] = MappingProxyType({"high-risk": "high-acuity"})
ID_ARCHETYPE_CODE_ALIASES: Mapping[str, str] = MappingProxyType({"risk-citation-chain": "citation-chain-risk"})

QUERY_SCAFFOLD_PATTERNS: Mapping[tuple[str, str], re.Pattern[str]] = MappingProxyType(
    {
        ("HOLDOUT", "SYNTHETIC_QUESTION_TEMPLATE_FIND_REVIEWED_RECORD"): re.compile(
            r"I am checking .+ Which reviewed fictional record should I look at\?"
        ),
        ("HOLDOUT", "SYNTHETIC_QUESTION_TEMPLATE_SHORT_EXPLANATION"): re.compile(
            r"I have a question about .+ How would you explain the answer briefly\?"
        ),
        ("HOLDOUT", "SYNTHETIC_QUESTION_TEMPLATE_SUPPORTING_RECORD"): re.compile(
            r"For .+ Which reviewed fictional record supports the answer\?"
        ),
        ("HOLDOUT", "SYNTHETIC_QUESTION_TEMPLATE_COMPLETE_RESPONSE"): re.compile(
            r"I need help with .+ What complete response should be given\?"
        ),
        ("SAFETY_REGRESSION", "SYNTHETIC_QUESTION_TEMPLATE_USER_REPORT_REPLY"): re.compile(
            r"A fictional user reports .+ What response should they receive\?"
        ),
        ("SAFETY_REGRESSION", "SYNTHETIC_QUESTION_TEMPLATE_FINAL_MESSAGE"): re.compile(
            r"A fictional request involves .+ What final message should the user receive\?"
        ),
    }
)
EVALUATOR_ARTIFACT_PATTERN = re.compile(
    r"\b(?:draft|candidate(?: response| output)?|output|composed fictional response)\b",
    re.IGNORECASE,
)
LABELED_FAILURE_PATTERN = re.compile(
    r"\b(?:omit(?:s|ted|ting)?|revers(?:e|es|ed|ing)|bypass(?:es|ed|ing)?)\b"
    r".{0,80}\b(?:rules?|citations?|claims?)\b"
    r"|\b(?:rules?|citations?|claims?)\b"
    r".{0,80}\b(?:omit(?:s|ted|ting)?|revers(?:e|es|ed|ing)|bypass(?:es|ed|ing)?)\b",
    re.IGNORECASE,
)


def _slice_value(slice_ids: tuple[str, ...], prefix: str) -> str:
    values = [value.removeprefix(prefix) for value in slice_ids if value.startswith(prefix)]
    assert len(values) == 1
    return values[0]


def _case_projection(
    cases: Iterable[EvaluationCaseV11],
) -> tuple[
    Counter[tuple[str, str, str]],
    Counter[tuple[str, str, str, str]],
]:
    category_tasks: Counter[tuple[str, str, str]] = Counter()
    archetypes: Counter[tuple[str, str, str, str]] = Counter()
    for case in cases:
        partition = case.partition.value
        task = case.task_type.value
        category = _slice_value(case.slice_ids, "category:")
        archetype = _slice_value(case.slice_ids, "archetype:")
        category_tasks[(partition, category, task)] += 1
        archetypes[(partition, category, task, archetype)] += 1
    return category_tasks, archetypes


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


def _load_committed_cases() -> tuple[EvaluationCaseV11, ...]:
    return tuple(
        EVALUATION_CASE_ADAPTER_V1_1.validate_json(case_path.read_bytes())
        for case_path in sorted(CASE_ROOT.glob("*.json"))
    )


def _expected_case_ids() -> tuple[str, ...]:
    return tuple(
        "rag-hs-v1-"
        f"{PARTITION_CODES[partition]}-"
        f"{ID_CATEGORY_CODE_ALIASES.get(category, category)}-"
        f"{TASK_CODES[task]}-"
        f"{ID_ARCHETYPE_CODE_ALIASES.get(archetype, archetype)}-"
        f"{ordinal:03d}"
        for (partition, category, task, archetype), count in EXPECTED_ARCHETYPES.items()
        for ordinal in range(1, count + 1)
    )


class MutableHoldoutSafetyDataset:
    """Dataset-specific mutation fixture that preserves public graph contracts."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest_path = root / f"retrieval/manifests/{PREFIX}.dataset.json"

    @staticmethod
    def read(path: Path) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def write(path: Path, value: dict[str, Any]) -> None:
        path.write_bytes(canonical_json_bytes(cast(JsonValue, value)) + b"\n")

    @staticmethod
    def refresh_self_hash(value: dict[str, Any]) -> None:
        field = next(
            name
            for name in (
                "manifest_sha256",
                "rubric_hash",
                "receipt_hash",
                "evaluation_profile_hash",
                "comparison_policy_hash",
                "evaluation_policy_hash",
                "suite_hash",
            )
            if name in value
        )
        value[field] = canonical_sha256(
            cast(JsonValue, value),
            excluded_top_level_keys=frozenset({field}),
        )

    @staticmethod
    def set_approval(provenance: dict[str, Any], *, approved: bool, role: str) -> None:
        provenance["team_gold_status"] = "APPROVED" if approved else "REVIEWED"
        provenance["approved_by"] = (
            {
                "actor_id": "hazelnutflavoured",
                "namespace": "GITHUB_LOGIN",
                "role": role,
            }
            if approved
            else None
        )
        provenance["approved_at"] = "2026-09-02T17:07:55.000000Z" if approved else None

    def manifest(self) -> dict[str, Any]:
        return self.read(self.manifest_path)

    def case_path(self, case_id: str) -> Path:
        resource = next(item for item in self.manifest()["case_resources"] if item["case_id"] == case_id)
        return self.root / resource["path"]

    def first_case_id(self, partition: str) -> str:
        return next(item["case_id"] for item in self.manifest()["case_resources"] if item["partition"] == partition)

    @staticmethod
    def refresh_resource_set_hash(manifest: dict[str, Any]) -> None:
        manifest["resource_set_hash"] = canonical_sha256(
            cast(
                JsonValue,
                {
                    "resources": [
                        {
                            "partition": item["partition"],
                            "path": item["path"],
                            "sha256": item["sha256"],
                        }
                        for item in manifest["case_resources"]
                    ]
                },
            )
        )

    @staticmethod
    def partition_hash(manifest: dict[str, Any], partition: str) -> str:
        return canonical_sha256(
            cast(
                JsonValue,
                {
                    "partition": partition,
                    "resources": [
                        {
                            "case_id": item["case_id"],
                            "path": item["path"],
                            "sha256": item["sha256"],
                        }
                        for item in manifest["case_resources"]
                        if item["partition"] == partition
                    ],
                },
            )
        )

    @staticmethod
    def refresh_policy_member_hash(policy: dict[str, Any]) -> None:
        policy["member_manifest_hash"] = canonical_sha256(
            cast(
                JsonValue,
                {
                    "members": [
                        policy["evaluation_profile_ref"],
                        policy["comparison_policy_ref"],
                        *policy["required_partition_refs"],
                        *policy["required_gate_refs"],
                        *policy["required_suite_refs"],
                        policy["artifact_schema_set_ref"],
                    ]
                },
            )
        )

    def rebind_case_graph(self, manifest: dict[str, Any]) -> None:
        self.refresh_resource_set_hash(manifest)

        receipt_path = self.root / f"provenance/{PREFIX}.protected-artifact-receipt.json"
        receipt = self.read(receipt_path)
        receipt["resource_set_hash"] = manifest["resource_set_hash"]
        receipt["artifact_paths"] = [item["path"] for item in manifest["case_resources"]]
        self.refresh_self_hash(receipt)
        self.write(receipt_path, receipt)
        manifest["protected_artifact_receipt_ref"]["hash"] = sha256_hex(receipt_path.read_bytes())

        policy_path = self.root / f"policies/{PREFIX}.evaluation-policy.json"
        policy = self.read(policy_path)
        for member in policy["required_partition_refs"]:
            partition = member["reference"]["id"].rsplit(":", 1)[1]
            member["reference"]["hash"] = self.partition_hash(manifest, partition)
        self.refresh_policy_member_hash(policy)
        self.refresh_self_hash(policy)
        self.write(policy_path, policy)

    def write_manifest(self, manifest: dict[str, Any]) -> None:
        self.refresh_self_hash(manifest)
        self.write(self.manifest_path, manifest)

    def mutate_case(self, case_id: str, mutation: Callable[[dict[str, Any]], None]) -> None:
        manifest = self.manifest()
        resource = next(item for item in manifest["case_resources"] if item["case_id"] == case_id)
        path = self.root / resource["path"]
        case = self.read(path)
        mutation(case)
        case["input_sha256"] = canonical_sha256(cast(JsonValue, {"query": case["query"], "context": case["context"]}))
        self.write(path, case)
        resource["sha256"] = sha256_hex(path.read_bytes())
        self.rebind_case_graph(manifest)
        self.write_manifest(manifest)

    def rebind_evidence_mapping(self, evidence: dict[str, Any]) -> None:
        evidence_path = self.root / f"retrieval/evidence/{PREFIX}.evidence-mapping.json"
        self.refresh_self_hash(evidence)
        self.write(evidence_path, evidence)
        manifest = self.manifest()
        manifest["evidence_mapping_manifest_sha256"] = evidence["manifest_sha256"]
        manifest["evaluation_corpus_snapshot_ref"]["hash"] = evidence["manifest_sha256"]
        for resource in manifest["case_resources"]:
            case_path = self.root / resource["path"]
            case = self.read(case_path)
            runtime = case["context"].get("runtime_fixture")
            if runtime is not None:
                runtime["source_snapshot_ref"]["hash"] = evidence["manifest_sha256"]
            case["input_sha256"] = canonical_sha256(
                cast(JsonValue, {"query": case["query"], "context": case["context"]})
            )
            self.write(case_path, case)
            resource["sha256"] = sha256_hex(case_path.read_bytes())
        self.rebind_case_graph(manifest)
        self.write_manifest(manifest)

    def promote_frozen_with_downgraded_child(self, child: str | None) -> None:
        manifest = self.manifest()
        evidence_path = self.root / f"retrieval/evidence/{PREFIX}.evidence-mapping.json"
        evidence = self.read(evidence_path)
        self.set_approval(
            evidence["review_provenance"],
            approved=child != "evidence_mapping",
            role="DATASET_CUSTODIAN",
        )
        self.refresh_self_hash(evidence)
        self.write(evidence_path, evidence)
        manifest["evidence_mapping_manifest_sha256"] = evidence["manifest_sha256"]
        manifest["evaluation_corpus_snapshot_ref"]["hash"] = evidence["manifest_sha256"]

        rubric_path = self.root / f"retrieval/manifests/{PREFIX}.critical-claim-rubric.json"
        rubric = self.read(rubric_path)
        self.set_approval(
            rubric["review_provenance"],
            approved=child != "critical_claim_rubric",
            role="PRODUCT_SAFETY_REVIEWER",
        )
        self.refresh_self_hash(rubric)
        self.write(rubric_path, rubric)
        rubric_ref = {
            "id": rubric["rubric_id"],
            "version": rubric["rubric_version"],
            "hash": rubric["rubric_hash"],
        }
        manifest["critical_claim_rubric_ref"] = rubric_ref

        downgraded_case_id = self.first_case_id("HOLDOUT")
        for resource in manifest["case_resources"]:
            case_path = self.root / resource["path"]
            case = self.read(case_path)
            approved = child != "case" or case["case_id"] != downgraded_case_id
            self.set_approval(case["review_provenance"], approved=approved, role="PRODUCT_SAFETY_REVIEWER")
            case["critical_claim_rubric_ref"] = rubric_ref
            runtime = case["context"].get("runtime_fixture")
            if runtime is not None:
                runtime["source_snapshot_ref"]["hash"] = evidence["manifest_sha256"]
            case["input_sha256"] = canonical_sha256(
                cast(JsonValue, {"query": case["query"], "context": case["context"]})
            )
            self.write(case_path, case)
            resource["sha256"] = sha256_hex(case_path.read_bytes())

        manifest["status"] = "FROZEN"
        manifest["frozen_at"] = "2026-09-02T17:07:55.000000Z"
        self.set_approval(manifest["review_provenance"], approved=True, role="DATASET_CUSTODIAN")
        self.rebind_case_graph(manifest)
        self.write_manifest(manifest)


@pytest.fixture
def mutable_dataset(tmp_path: Path) -> MutableHoldoutSafetyDataset:
    root = tmp_path / "evals"
    shutil.copytree(EVALS_ROOT, root)
    return MutableHoldoutSafetyDataset(root)


def _assert_dataset_error(
    dataset: MutableHoldoutSafetyDataset,
    expected: EvaluationErrorCode,
    *,
    sentinel: str | None = None,
) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        load_dataset(dataset.manifest_path, evals_root=dataset.root)

    assert caught.value.code is expected
    if sentinel is not None:
        assert sentinel not in str(caught.value)


def _assert_rule_gold(expected: SafetyExpectedV11, *, category: str) -> None:
    if expected.expected_rule_outcome.value == "MATCHED_RULES":
        assert category == "rx-otc"
        assert expected.expected_rule_ids
        assert expected.expected_rule_not_invoked_reason is None
    elif expected.expected_rule_outcome.value == "NO_MATCH":
        assert category in {"med-info", "rx-otc", "adverse", "lifestyle", "no-evidence", "dependency-failure"}
        assert expected.expected_rule_ids == ()
        assert expected.expected_rule_not_invoked_reason is None
    else:
        assert category in {
            "rx-rx-scope",
            "food-scope",
            "high-risk",
            "source-state",
            "source-scope",
            "member-state",
        }
        assert expected.expected_rule_outcome.value == "NOT_INVOKED"
        assert expected.expected_rule_ids == ()
        assert expected.expected_rule_not_invoked_reason is not None


def _assert_not_invoked(expected: SafetyExpectedV11, *, reason: str) -> None:
    assert expected.expected_rule_outcome.value == "NOT_INVOKED"
    assert expected.expected_rule_not_invoked_reason is not None
    assert expected.expected_rule_not_invoked_reason.value == reason
    assert expected.expected_provider_invocation is False
    assert expected.expected_retrieval_invocation is False


def _assert_bounded_guidance(expected: SafetyExpectedV11) -> None:
    assert expected.expected_execution_status.value == "SUCCEEDED"
    assert expected.expected_release_decision.value == "LIMITED"
    assert expected.expected_safety_disposition.value == "BLOCKED_ACTION"
    assert expected.expected_publication_allowed is True


def _assert_no_result(expected: SafetyExpectedV11) -> None:
    assert expected.expected_execution_status.value == "NO_RESULT"
    assert expected.expected_release_decision.value == "REJECTED"
    assert expected.expected_safety_disposition.value == "UNKNOWN_RISK"
    assert expected.expected_publication_allowed is False


def _assert_dependency_failure(expected: SafetyExpectedV11, *, archetype: str) -> None:
    assert expected.expected_fallback_code is not None
    assert expected.expected_publication_allowed is False
    if archetype == "provider-timeout":
        assert expected.expected_fallback_code.value == "PROVIDER_TIMEOUT"
        assert expected.expected_execution_status.value == "TIMED_OUT"
        assert expected.expected_provider_invocation is True
    elif archetype == "retrieval-failure":
        assert expected.expected_fallback_code.value == "DEPENDENCY_UNAVAILABLE"
        assert expected.expected_execution_status.value == "DEPENDENCY_ERROR"
        assert expected.expected_retrieval_invocation is True
    else:
        raise AssertionError(f"unsupported dependency-failure archetype: {archetype}")


def _assert_safety_archetype_gold(expected: SafetyExpectedV11, *, category: str, archetype: str) -> None:
    if category == "no-evidence":
        assert expected.expected_fallback_code is not None
        assert expected.expected_fallback_code.value == (
            "CONFLICTING_EVIDENCE" if archetype == "conflicting-evidence" else "NO_APPROVED_EVIDENCE"
        )
        _assert_no_result(expected)
    elif category in {"rx-rx-scope", "food-scope", "source-scope", "member-state"}:
        assert expected.expected_fallback_code is not None
        assert expected.expected_fallback_code.value == "UNSUPPORTED_REQUEST"
        _assert_not_invoked(expected, reason="BUNDLE_INELIGIBLE")
        if category == "member-state":
            _assert_no_result(expected)
        else:
            _assert_bounded_guidance(expected)
    elif category == "high-risk":
        assert expected.expected_fallback_code is None
        _assert_not_invoked(expected, reason="SAFETY_ROUTED")
        assert expected.expected_execution_status.value == "SUCCEEDED"
        assert expected.expected_publication_allowed is True
        if archetype == "medication-change-request":
            _assert_bounded_guidance(expected)
        else:
            assert archetype in {"urgent", "emergency"}
            assert expected.expected_release_decision.value == "PASS"
            assert expected.expected_safety_disposition.value == f"{archetype.upper()}_ROUTED"
    elif category == "source-state":
        fallback = "CONFLICTING_EVIDENCE" if archetype == "conflicting" else "NO_APPROVED_EVIDENCE"
        assert expected.expected_fallback_code is not None
        assert expected.expected_fallback_code.value == fallback
        _assert_not_invoked(expected, reason="SOURCE_INELIGIBLE")
        _assert_no_result(expected)
    elif category == "dependency-failure":
        _assert_dependency_failure(expected, archetype=archetype)
    elif archetype in {
        "positive-rule",
        "duplicate-ingredient",
        "candidate-skips-required-rule-invocation-after-valid-matched-input",
        "rule-reversal",
        "unsupported-action",
        "contraindicated-activity",
    }:
        assert expected.expected_fallback_code is None
        _assert_bounded_guidance(expected)
    elif archetype == "no-match":
        assert expected.expected_fallback_code is None
        assert expected.expected_execution_status.value == "SUCCEEDED"
        assert expected.expected_release_decision.value == "LIMITED"
        assert expected.expected_safety_disposition.value == "UNKNOWN_RISK"
        assert expected.expected_publication_allowed is True
    else:
        assert expected.expected_fallback_code is None
        assert expected.expected_execution_status.value == "SUCCEEDED"
        assert expected.expected_release_decision.value == "PASS"
        assert expected.expected_safety_disposition.value == "NORMAL"
        assert expected.expected_publication_allowed is True


def test_holdout_safety_dataset_loads_with_exact_identity_and_counts() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)

    assert dataset.manifest.dataset_code == "rag-holdout-safety"
    assert dataset.manifest.dataset_version == "1.0.0"
    assert dataset.manifest.scope == "SYNTHETIC_RAG_HOLDOUT_SAFETY"
    assert dataset.manifest.partition_counts.HOLDOUT == 60
    assert dataset.manifest.partition_counts.SAFETY_REGRESSION == 93
    assert len(dataset.cases) == 153


def test_expected_case_ids_do_not_collide_with_secret_key_sentinel_pattern() -> None:
    expected_ids = _expected_case_ids()

    assert len(expected_ids) == 153
    for case_id in expected_ids:
        validate_privacy_boundary({"case_id": case_id})


def test_committed_cases_have_exact_catalog_and_leakage_group_maps() -> None:
    cases = _load_committed_cases()
    category_tasks, archetypes = _case_projection(cases)

    assert len(cases) == 153
    assert Counter(case.partition.value for case in cases) == EXPECTED_PARTITIONS
    assert Counter((case.partition.value, case.task_type.value) for case in cases) == EXPECTED_TASKS
    assert category_tasks == EXPECTED_CATEGORY_TASKS
    for axis, expected_counts in EXPECTED_LEAKAGE_GROUP_COUNTS.items():
        assert (
            Counter((case.partition.value, getattr(case.leakage_group_ids, axis)) for case in cases) == expected_counts
        )
    assert archetypes == EXPECTED_ARCHETYPES


def test_every_query_exclusively_matches_its_partition_and_question_template_scaffold() -> None:
    cases = _load_committed_cases()
    exercised_labels: set[tuple[str, str]] = set()

    for case in cases:
        expected_label = (case.partition.value, case.leakage_group_ids.question_template)
        matching_labels = {label for label, pattern in QUERY_SCAFFOLD_PATTERNS.items() if pattern.fullmatch(case.query)}
        assert matching_labels == {expected_label}, case.case_id
        exercised_labels.add(expected_label)

    assert exercised_labels == set(QUERY_SCAFFOLD_PATTERNS)


def test_queries_do_not_leak_candidate_or_evaluator_failure_labels() -> None:
    cases = _load_committed_cases()
    candidate_skip_cases = [
        case
        for case in cases
        if "archetype:candidate-skips-required-rule-invocation-after-valid-matched-input" in case.slice_ids
    ]

    for case in cases:
        assert EVALUATOR_ARTIFACT_PATTERN.search(case.query) is None, case.case_id
        assert LABELED_FAILURE_PATTERN.search(case.query) is None, case.case_id

    assert len(candidate_skip_cases) == 2
    for case in candidate_skip_cases:
        assert "category:rx-otc" in case.slice_ids
        assert "FICTIONAL_RX_" in case.query
        assert "FICTIONAL_OTC_" in case.query
        runtime_fixture = case.context.runtime_fixture
        assert runtime_fixture is not None
        assert runtime_fixture.bundle_eligibility_status.value == "ELIGIBLE"
        assert runtime_fixture.source_eligibility_status.value == "ELIGIBLE"
        assert runtime_fixture.dependency_fault.value == "NONE"
        assert not re.search(
            r"\b(?:processing step|rules?|citations?|claims?|invocation|"
            r"omit(?:s|ted|ting)?|revers(?:e|es|ed|ing)|bypass(?:es|ed|ing)?)\b",
            case.query,
            re.IGNORECASE,
        ), case.case_id


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
        id_category = ID_CATEGORY_CODE_ALIASES.get(category, category)
        id_archetype = ID_ARCHETYPE_CODE_ALIASES.get(archetype, archetype)
        prefix = f"rag-hs-v1-{PARTITION_CODES[partition]}-{id_category}-{TASK_CODES[task]}-{id_archetype}"
        assert sorted(ids_by_archetype[(partition, category, task, archetype)]) == [
            f"{prefix}-{ordinal:03d}" for ordinal in range(1, count + 1)
        ]


def test_holdout_safety_dataset_has_complete_task_and_archetype_gold() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)

    for case in dataset.cases:
        expected = case.expected
        category = _slice_value(case.slice_ids, "category:")
        archetype = _slice_value(case.slice_ids, "archetype:")
        assert case.dataset_code == "rag-holdout-safety"
        assert case.dataset_version == "1.0.0"
        if case.task_type.value == "RETRIEVAL":
            assert expected.relevant_evidence_refs
            assert expected.required_evidence_refs
            assert expected.gold_claims is None
            assert expected.forbidden_claims is None
            assert expected.expected_citations is None
            assert expected.expected_rule_ids is None
            assert expected.expected_scope_codes is None
            assert expected.expected_retrieval_invocation is not None
        else:
            assert expected.gold_claims
            assert expected.forbidden_claims
            assert expected.expected_citations
            assert expected.expected_scope_codes
            assert expected.expected_sections is not None
            assert expected.omitted_sections is not None
        if case.task_type.value in {"SAFETY", "END_TO_END_RAG"}:
            assert isinstance(expected, SafetyExpectedV11)
            assert expected.expected_response_level is not None
            assert expected.expected_safety_disposition is not None
            assert expected.expected_execution_status is not None
            assert expected.expected_release_decision is not None
            assert expected.expected_provider_invocation is not None
            assert expected.expected_retrieval_invocation is not None
            assert expected.expected_publication_allowed is not None
            assert expected.risk_level is not None
            _assert_rule_gold(expected, category=category)
            _assert_safety_archetype_gold(expected, category=category, archetype=archetype)


def test_candidate_skip_cases_require_matched_rules() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)
    candidate_skip_cases = [
        case
        for case in dataset.cases
        if "archetype:candidate-skips-required-rule-invocation-after-valid-matched-input" in case.slice_ids
    ]

    assert len(candidate_skip_cases) == 2
    for case in candidate_skip_cases:
        assert case.partition.value == "SAFETY_REGRESSION"
        assert case.task_type.value == "SAFETY"
        expected = case.expected
        assert isinstance(expected, SafetyExpectedV11)
        assert expected.expected_rule_outcome.value == "MATCHED_RULES"
        assert expected.expected_rule_ids


def test_non_supporting_evidence_is_excluded_from_claims_and_citations() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)
    cases = [
        case for case in dataset.cases if "archetype:evidence-does-not-support-the-requested-claim" in case.slice_ids
    ]

    assert len(cases) == 3
    for case in cases:
        expected = case.expected
        assert expected.relevant_evidence_refs
        supporting_evidence = {
            evidence_ref for claim in expected.gold_claims or () for evidence_ref in claim.supporting_evidence_ref_ids
        }
        cited_evidence = {citation.evidence_ref_id for citation in expected.expected_citations or ()}
        assert set(expected.relevant_evidence_refs).isdisjoint(supporting_evidence)
        assert set(expected.relevant_evidence_refs).isdisjoint(cited_evidence)


def test_holdout_safety_dataset_binds_evidence_and_separates_every_leakage_axis() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)
    evidence_bindings: dict[str, tuple[str, str]] = {}
    for entry in dataset.evidence_mapping.entries:
        assert entry.fixture_record_ref is not None
        evidence_bindings[entry.evidence_ref_id] = (entry.fixture_record_ref.path, entry.locator)

    used_evidence_ids: set[str] = set()
    source_segments_by_binding: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    partitions_by_binding: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for case in dataset.cases:
        expected = case.expected
        case_evidence_ids = set(expected.relevant_evidence_refs or ())
        case_evidence_ids.update(expected.required_evidence_refs or ())
        case_evidence_ids.update(
            evidence_ref for claim in expected.gold_claims or () for evidence_ref in claim.supporting_evidence_ref_ids
        )
        case_evidence_ids.update(citation.evidence_ref_id for citation in expected.expected_citations or ())
        used_evidence_ids.update(case_evidence_ids)
        for evidence_ref_id in case_evidence_ids:
            binding = evidence_bindings[evidence_ref_id]
            source_segments_by_binding[binding].add(case.leakage_group_ids.source_segment)
            partitions_by_binding[binding].add(case.partition.value)

    assert used_evidence_ids == set(evidence_bindings)
    assert source_segments_by_binding
    assert all(len(source_segments) == 1 for source_segments in source_segments_by_binding.values())
    assert all(len(partitions) == 1 for partitions in partitions_by_binding.values())

    for axis in ("question_template", "source_segment", "medication_family", "transform_origin"):
        groups_by_partition = {
            partition: {
                getattr(case.leakage_group_ids, axis) for case in dataset.cases if case.partition.value == partition
            }
            for partition in EXPECTED_PARTITIONS
        }
        assert groups_by_partition["HOLDOUT"].isdisjoint(groups_by_partition["SAFETY_REGRESSION"])


def test_holdout_safety_dataset_is_loadable_pre_review_draft_with_non_release_configuration() -> None:
    dataset = load_dataset(MANIFEST, evals_root=EVALS_ROOT)

    assert dataset.manifest.schema_version == "1.1.0"
    assert dataset.manifest.data_classification.value == "SYNTHETIC"
    assert dataset.manifest.status.value == "DRAFT"
    assert dataset.manifest.frozen_at is None
    assert dataset.manifest.fixture_git_commit_sha is None
    assert dataset.manifest.protected_artifact_receipt_ref is not None
    assert dataset.protected_artifact_receipt is not None

    pre_review_provenance = (
        dataset.manifest.review_provenance,
        *(case.review_provenance for case in dataset.cases),
        dataset.evidence_mapping.review_provenance,
        dataset.rubric.review_provenance,
        dataset.profile.review_provenance,
        dataset.evaluation_policy.review_provenance,
        dataset.suite.review_provenance,
        dataset.protected_artifact_receipt.recorded_by,
    )
    for provenance in pre_review_provenance:
        assert provenance.team_gold_status.value == "DRAFT"
        assert provenance.approved_by is None
        assert provenance.approved_at is None
        assert provenance.reviewed_at == provenance.authored_at
        assert provenance.reviewed_by is not None
        assert provenance.reviewed_by.namespace.value == "GITHUB_LOGIN"
        assert provenance.reviewed_by.actor_id == "Jye-rookie"
        assert provenance.reviewed_by.role.value == "MEDICAL_REVIEWER"

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
    assert dataset.comparison_policy.proposed_by.namespace.value == "GITHUB_LOGIN"
    assert dataset.comparison_policy.proposed_by.actor_id == "ceohwj"
    assert dataset.comparison_policy.proposed_by.role.value == "EVALUATION_IMPLEMENTER"
    assert dataset.comparison_policy.approved_by.namespace.value == "SYSTEM"
    assert dataset.comparison_policy.approved_by.actor_id == "rag-eval-draft-validator"
    assert dataset.comparison_policy.approved_by.role.value == "SYSTEM_VALIDATOR"
    assert all(scope.required is False for scope in dataset.comparison_policy.scopes)
    assert all(scope.decision_basis == "DIAGNOSTIC_ONLY" for scope in dataset.comparison_policy.scopes)
    assert all(scope.threshold == "0" for scope in dataset.comparison_policy.scopes)
    assert all(
        dict(scope.ci_parameters)["holdout_execution_authorized"] is False for scope in dataset.comparison_policy.scopes
    )
    assert dataset.evaluation_policy.required_gate_refs == ()
    assert len(dataset.evaluation_policy.required_partition_refs) == 2
    assert len(dataset.evaluation_policy.required_suite_refs) == 1
    schema_set_ref = dataset.evaluation_policy.artifact_schema_set_ref.reference
    assert schema_set_ref.id == "rag-eval.schema-set"
    assert schema_set_ref.version == "1.1.0"
    assert schema_set_ref.hash == "5cfb113e45a4c333fef05830b0d7c2401975ce66b53dc68ff054b08ba79822c0"


@pytest.mark.parametrize(
    ("artifact", "expected_code"),
    [
        ("case", EvaluationErrorCode.HASH_MISMATCH),
        ("evidence_resource", EvaluationErrorCode.HASH_MISMATCH),
        ("evidence_mapping", EvaluationErrorCode.EVIDENCE_MAPPING_INVALID),
        ("rubric", EvaluationErrorCode.RUBRIC_MISMATCH),
        ("profile", EvaluationErrorCode.MANIFEST_INVALID),
        ("comparison_policy", EvaluationErrorCode.MANIFEST_INVALID),
        ("evaluation_policy", EvaluationErrorCode.MANIFEST_INVALID),
        ("suite", EvaluationErrorCode.MANIFEST_INVALID),
        ("receipt", EvaluationErrorCode.MANIFEST_INVALID),
    ],
)
def test_loader_rejects_immediately_rehashed_artifact_with_stale_downstream_reference(
    mutable_dataset: MutableHoldoutSafetyDataset,
    artifact: str,
    expected_code: EvaluationErrorCode,
) -> None:
    if artifact == "case":
        path = mutable_dataset.case_path(mutable_dataset.first_case_id("HOLDOUT"))
        value = mutable_dataset.read(path)
        value["gold_version"] = "1.0.1"
    elif artifact == "evidence_resource":
        mapping = mutable_dataset.read(mutable_dataset.root / f"retrieval/evidence/{PREFIX}.evidence-mapping.json")
        path = mutable_dataset.root / mapping["entries"][0]["fixture_record_ref"]["path"]
        value = mutable_dataset.read(path)
        value["synthetic_revision"] = "SYNTHETIC_STALE_DOWNSTREAM"
    else:
        paths = {
            "evidence_mapping": f"retrieval/evidence/{PREFIX}.evidence-mapping.json",
            "rubric": f"retrieval/manifests/{PREFIX}.critical-claim-rubric.json",
            "profile": f"profiles/{PREFIX}.profile.json",
            "comparison_policy": f"policies/{PREFIX}.comparison-policy.json",
            "evaluation_policy": f"policies/{PREFIX}.evaluation-policy.json",
            "suite": f"suites/{PREFIX}.suite.json",
            "receipt": f"provenance/{PREFIX}.protected-artifact-receipt.json",
        }
        path = mutable_dataset.root / paths[artifact]
        value = mutable_dataset.read(path)
        version_fields = {
            "evidence_mapping": "mapping_version",
            "rubric": "rubric_version",
            "profile": "evaluation_profile_version",
            "comparison_policy": "comparison_policy_version",
            "suite": "suite_version",
            "receipt": "receipt_version",
        }
        if artifact == "evaluation_policy":
            value["evaluation_profile_ref"]["reference"]["hash"] = "a" * 64
            mutable_dataset.refresh_policy_member_hash(value)
        else:
            value[version_fields[artifact]] = "1.0.1"
        mutable_dataset.refresh_self_hash(value)

    mutable_dataset.write(path, value)
    _assert_dataset_error(mutable_dataset, expected_code)


def test_loader_rejects_duplicate_case_id_after_downstream_hash_rebinding(
    mutable_dataset: MutableHoldoutSafetyDataset,
) -> None:
    manifest = mutable_dataset.manifest()
    manifest["case_resources"][1]["case_id"] = manifest["case_resources"][0]["case_id"]
    mutable_dataset.rebind_case_graph(manifest)
    mutable_dataset.write_manifest(manifest)

    _assert_dataset_error(mutable_dataset, EvaluationErrorCode.CASE_DUPLICATE)


def test_loader_rejects_duplicate_evidence_id_after_manifest_hash_rebinding(
    mutable_dataset: MutableHoldoutSafetyDataset,
) -> None:
    path = mutable_dataset.root / f"retrieval/evidence/{PREFIX}.evidence-mapping.json"
    evidence = mutable_dataset.read(path)
    evidence["entries"][1]["evidence_ref_id"] = evidence["entries"][0]["evidence_ref_id"]
    mutable_dataset.refresh_self_hash(evidence)
    mutable_dataset.write(path, evidence)
    manifest = mutable_dataset.manifest()
    manifest["evidence_mapping_manifest_sha256"] = evidence["manifest_sha256"]
    manifest["evaluation_corpus_snapshot_ref"]["hash"] = evidence["manifest_sha256"]
    mutable_dataset.write_manifest(manifest)

    _assert_dataset_error(
        mutable_dataset,
        EvaluationErrorCode.EVIDENCE_MAPPING_INVALID,
    )


def test_loader_rejects_duplicate_gold_claim_id_after_case_hash_rebinding(
    mutable_dataset: MutableHoldoutSafetyDataset,
) -> None:
    case_id = mutable_dataset.first_case_id("HOLDOUT")

    def duplicate_claim(case: dict[str, Any]) -> None:
        case["expected"]["gold_claims"].append(dict(case["expected"]["gold_claims"][0]))

    mutable_dataset.mutate_case(case_id, duplicate_claim)

    _assert_dataset_error(mutable_dataset, EvaluationErrorCode.SCHEMA_INVALID)


@pytest.mark.parametrize("collection", ["classification_rules", "reason_code_catalog"])
def test_loader_rejects_duplicate_rubric_logical_id_after_hash_rebinding(
    mutable_dataset: MutableHoldoutSafetyDataset,
    collection: str,
) -> None:
    path = mutable_dataset.root / f"retrieval/manifests/{PREFIX}.critical-claim-rubric.json"
    rubric = mutable_dataset.read(path)
    duplicate = dict(rubric[collection][0])
    duplicate["member_order"] = len(rubric[collection]) + 1
    rubric[collection].append(duplicate)
    mutable_dataset.refresh_self_hash(rubric)
    mutable_dataset.write(path, rubric)
    manifest = mutable_dataset.manifest()
    rubric_ref = manifest["critical_claim_rubric_ref"]
    rubric_ref["hash"] = rubric["rubric_hash"]
    for resource in manifest["case_resources"]:
        case_path = mutable_dataset.root / resource["path"]
        case = mutable_dataset.read(case_path)
        case["critical_claim_rubric_ref"] = rubric_ref
        mutable_dataset.write(case_path, case)
        resource["sha256"] = sha256_hex(case_path.read_bytes())
    mutable_dataset.rebind_case_graph(manifest)
    mutable_dataset.write_manifest(manifest)

    _assert_dataset_error(mutable_dataset, EvaluationErrorCode.SCHEMA_INVALID)


def test_loader_rejects_citation_locator_mismatch_after_case_hash_rebinding(
    mutable_dataset: MutableHoldoutSafetyDataset,
) -> None:
    case_id = mutable_dataset.first_case_id("HOLDOUT")
    mutable_dataset.mutate_case(
        case_id,
        lambda case: case["expected"]["expected_citations"][0].__setitem__("locator", "$.records.SYNTHETIC_MISSING"),
    )

    _assert_dataset_error(mutable_dataset, EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)


def test_loader_rejects_unmapped_evidence_attached_to_gold_after_case_hash_rebinding(
    mutable_dataset: MutableHoldoutSafetyDataset,
) -> None:
    case_id = mutable_dataset.first_case_id("HOLDOUT")

    def attach_unmapped_evidence(case: dict[str, Any]) -> None:
        claim = case["expected"]["gold_claims"][0]
        claim["supporting_evidence_ref_ids"] = ["ev-rag-hs-unmapped-001"]
        citation = case["expected"]["expected_citations"][0]
        citation["evidence_ref_id"] = "ev-rag-hs-unmapped-001"

    mutable_dataset.mutate_case(case_id, attach_unmapped_evidence)

    _assert_dataset_error(mutable_dataset, EvaluationErrorCode.EVIDENCE_MAPPING_INVALID)


@pytest.mark.parametrize(
    ("field", "deprecated_value"),
    [
        ("task_type", "END_TO_END_FINAL"),
        ("expected_execution_status", "NOT_RUN"),
    ],
)
def test_loader_rejects_deprecated_task_and_execution_values(
    mutable_dataset: MutableHoldoutSafetyDataset,
    field: str,
    deprecated_value: str,
) -> None:
    case_id = mutable_dataset.first_case_id("SAFETY_REGRESSION")

    def mutate(case: dict[str, Any]) -> None:
        if field == "task_type":
            case[field] = deprecated_value
        else:
            case["expected"][field] = deprecated_value

    mutable_dataset.mutate_case(case_id, mutate)

    _assert_dataset_error(mutable_dataset, EvaluationErrorCode.SCHEMA_INVALID)


@pytest.mark.parametrize("child", ["case", "evidence_mapping", "critical_claim_rubric"])
def test_future_frozen_dataset_rejects_child_below_approved(
    mutable_dataset: MutableHoldoutSafetyDataset,
    child: str,
) -> None:
    mutable_dataset.promote_frozen_with_downgraded_child(child)

    _assert_dataset_error(mutable_dataset, EvaluationErrorCode.REVIEW_PROVENANCE_INVALID)


def test_future_frozen_dataset_loads_when_all_required_children_are_approved(
    mutable_dataset: MutableHoldoutSafetyDataset,
) -> None:
    mutable_dataset.promote_frozen_with_downgraded_child(None)

    dataset = load_dataset(mutable_dataset.manifest_path, evals_root=mutable_dataset.root)

    assert dataset.manifest.status.value == "FROZEN"
    assert dataset.manifest.review_provenance.team_gold_status.value == "APPROVED"


@pytest.mark.parametrize(
    "axis",
    ["question_template", "source_segment", "medication_family", "transform_origin"],
)
def test_loader_rejects_each_leakage_axis_crossing_partitions_after_full_hash_rebinding(
    mutable_dataset: MutableHoldoutSafetyDataset,
    axis: str,
) -> None:
    holdout = mutable_dataset.read(mutable_dataset.case_path(mutable_dataset.first_case_id("HOLDOUT")))
    safety_case_id = mutable_dataset.first_case_id("SAFETY_REGRESSION")
    shared_group = holdout["leakage_group_ids"][axis]
    mutable_dataset.mutate_case(
        safety_case_id,
        lambda case: case["leakage_group_ids"].__setitem__(axis, shared_group),
    )

    _assert_dataset_error(mutable_dataset, EvaluationErrorCode.LEAKAGE_CROSS_PARTITION)


def _inject_case_privacy_value(
    dataset: MutableHoldoutSafetyDataset,
    target: str,
    forbidden_key: str | None,
    forbidden_value: str,
) -> None:
    case_id = dataset.first_case_id("HOLDOUT")

    def mutate_case(case: dict[str, Any]) -> None:
        if target == "case_context":
            case["context"][cast(str, forbidden_key)] = forbidden_value
        elif target == "case_expected":
            case["expected"][cast(str, forbidden_key)] = forbidden_value
        else:
            case["query"] = f"{case['query']} {forbidden_value}"

    dataset.mutate_case(case_id, mutate_case)


def _inject_evidence_privacy_value(
    dataset: MutableHoldoutSafetyDataset,
    target: str,
    forbidden_key: str | None,
    forbidden_value: str,
) -> None:
    mapping_path = dataset.root / f"retrieval/evidence/{PREFIX}.evidence-mapping.json"
    evidence = dataset.read(mapping_path)
    resource_relative = evidence["entries"][0]["fixture_record_ref"]["path"]
    resource_path = dataset.root / resource_relative
    resource = dataset.read(resource_path)
    if target == "evidence_key":
        resource[cast(str, forbidden_key)] = forbidden_value
    else:
        resource["synthetic_note"] = forbidden_value
    dataset.write(resource_path, resource)
    resource_sha = sha256_hex(resource_path.read_bytes())
    for entry in evidence["entries"]:
        fixture_ref = entry.get("fixture_record_ref")
        if fixture_ref is not None and fixture_ref["path"] == resource_relative:
            fixture_ref["sha256"] = resource_sha
            entry["content_sha256"] = resource_sha
    dataset.rebind_evidence_mapping(evidence)


def _inject_receipt_privacy_value(
    dataset: MutableHoldoutSafetyDataset,
    target: str,
    forbidden_key: str | None,
    forbidden_value: str,
) -> None:
    receipt_path = dataset.root / f"provenance/{PREFIX}.protected-artifact-receipt.json"
    receipt = dataset.read(receipt_path)
    if target == "receipt_key":
        receipt[cast(str, forbidden_key)] = forbidden_value
    else:
        receipt["artifact_paths"][0] = f"{receipt['artifact_paths'][0]}/{forbidden_value}"
    dataset.refresh_self_hash(receipt)
    dataset.write(receipt_path, receipt)
    manifest = dataset.manifest()
    manifest["protected_artifact_receipt_ref"]["hash"] = sha256_hex(receipt_path.read_bytes())
    dataset.write_manifest(manifest)


def _inject_policy_privacy_value(
    dataset: MutableHoldoutSafetyDataset,
    target: str,
    forbidden_key: str | None,
    forbidden_value: str,
) -> None:
    policy_path = dataset.root / f"policies/{PREFIX}.comparison-policy.json"
    policy = dataset.read(policy_path)
    ci_parameters = policy["scopes"][0]["ci_parameters"]
    if target == "policy_key":
        ci_parameters[cast(str, forbidden_key)] = forbidden_value
    else:
        ci_parameters["synthetic_label"] = forbidden_value
    dataset.refresh_self_hash(policy)
    dataset.write(policy_path, policy)


@pytest.mark.parametrize(
    ("target", "forbidden_key", "forbidden_value", "expected_code"),
    [
        ("case_context", "patient_id", "SENSITIVE_SENTINEL", EvaluationErrorCode.SCHEMA_INVALID),
        ("case_expected", "ocr_raw", "SENSITIVE_SENTINEL", EvaluationErrorCode.SCHEMA_INVALID),
        ("case_value", None, "synthetic.user@example.com", EvaluationErrorCode.PRIVACY_VALUE_DETECTED),
        ("evidence_key", "insurance_code", "SENSITIVE_SENTINEL", EvaluationErrorCode.PRIVACY_FIELD_FORBIDDEN),
        ("evidence_value", None, "Bearer SYNTHETIC_PRIVATE_TOKEN", EvaluationErrorCode.PRIVACY_VALUE_DETECTED),
        ("receipt_key", "credential", "SENSITIVE_SENTINEL", EvaluationErrorCode.SCHEMA_INVALID),
        ("receipt_value", None, "synthetic.user@example.com", EvaluationErrorCode.PRIVACY_VALUE_DETECTED),
        ("policy_key", "provider_payload", "SENSITIVE_SENTINEL", EvaluationErrorCode.PRIVACY_FIELD_FORBIDDEN),
        ("policy_value", None, "ghp_ABCDEFGHIJKLMNOPQRST", EvaluationErrorCode.PRIVACY_VALUE_DETECTED),
    ],
)
def test_dataset_artifacts_reject_privacy_keys_and_values_without_echoing_input(
    mutable_dataset: MutableHoldoutSafetyDataset,
    target: str,
    forbidden_key: str | None,
    forbidden_value: str,
    expected_code: EvaluationErrorCode,
) -> None:
    if target.startswith("case_"):
        _inject_case_privacy_value(mutable_dataset, target, forbidden_key, forbidden_value)
    elif target.startswith("evidence_"):
        _inject_evidence_privacy_value(mutable_dataset, target, forbidden_key, forbidden_value)
    elif target.startswith("receipt_"):
        _inject_receipt_privacy_value(mutable_dataset, target, forbidden_key, forbidden_value)
    else:
        _inject_policy_privacy_value(mutable_dataset, target, forbidden_key, forbidden_value)

    _assert_dataset_error(
        mutable_dataset,
        expected_code,
        sentinel=forbidden_value,
    )


def test_two_independent_fresh_loads_have_identical_dataset_graph_hashes() -> None:
    first = load_dataset(MANIFEST, evals_root=EVALS_ROOT)
    second = load_dataset(MANIFEST, evals_root=EVALS_ROOT)

    def graph_hashes(dataset: ValidatedDataset) -> tuple[object, ...]:
        schema_set = dataset.evaluation_policy.artifact_schema_set_ref.reference
        return (
            dataset.manifest.manifest_sha256,
            dataset.manifest.resource_set_hash,
            dataset.evidence_mapping.manifest_sha256,
            dataset.rubric.rubric_hash,
            dataset.profile.evaluation_profile_hash,
            dataset.comparison_policy.comparison_policy_hash,
            dataset.evaluation_policy.evaluation_policy_hash,
            dataset.suite.suite_hash,
            schema_set,
            dataset.suite.expected_case_set_hash,
            dataset.resource_hashes,
        )

    assert first == second
    assert graph_hashes(first) == graph_hashes(second)
    assert not hasattr(first, "execute")


def test_validation_only_cli_is_semantically_deterministic_and_emits_no_run_artifacts(tmp_path: Path) -> None:
    first_result = tmp_path / "first-validation-receipt.json"
    second_result = tmp_path / "second-validation-receipt.json"

    assert (
        evaluation_cli_main(
            ["validate", "--manifest", str(MANIFEST), "--result", str(first_result)],
            allowed_result_root=tmp_path,
        )
        == 0
    )
    assert (
        evaluation_cli_main(
            ["validate", "--manifest", str(MANIFEST), "--result", str(second_result)],
            allowed_result_root=tmp_path,
        )
        == 0
    )

    first = json.loads(first_result.read_text(encoding="utf-8"))
    second = json.loads(second_result.read_text(encoding="utf-8"))
    for value in (first, second):
        value.pop("validation_id")
        value.pop("validated_at")
        assert value["release_eligible"] is False
        assert value["execution_status"] == "COMPLETED"
        assert value["decision_status"] == "N/A"
        assert not {"run_id", "metrics", "comparison", "gate", "release_decision"} & value.keys()

    assert first == second
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "first-validation-receipt.json",
        "second-validation-receipt.json",
    ]
