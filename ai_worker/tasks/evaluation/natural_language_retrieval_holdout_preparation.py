from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.natural_language_retrieval_dev_authoring import DATASET_MANIFEST_PATH

HOLDOUT_PREPARATION_JSON_PATH = "docs/validation/rag/issue-273/holdout-freeze-preparation.json"
HOLDOUT_PREPARATION_MARKDOWN_PATH = "docs/validation/rag/issue-273/holdout-freeze-preparation.md"

_EXPECTED_DATASET_MANIFEST_SHA256 = "b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2"
_IMPLEMENTATION_OWNER: dict[str, JsonValue] = {
    "actor_id": "ceohwj",
    "namespace": "GITHUB_LOGIN",
    "role": "EVALUATION_IMPLEMENTER",
}
_PRODUCT_EVALUATION_REVIEWER: dict[str, JsonValue] = {
    "actor_id": "hazelnutflavoured",
    "namespace": "GITHUB_LOGIN",
    "role": "EVALUATION_REVIEWER",
}
_REQUESTED_DATASET_CUSTODIAN: dict[str, JsonValue] = {
    "actor_id": "phina-io",
    "namespace": "GITHUB_LOGIN",
    "role": "DATASET_CUSTODIAN",
}
_CUSTODIAN_CONFLICT_APPROVER: dict[str, JsonValue] = {
    "actor_id": "Jye-rookie",
    "namespace": "GITHUB_LOGIN",
    "role": "DATASET_CUSTODIAN",
}
_TOPIC_COUNTS: list[JsonValue] = [
    {"count": 8, "topic": "TOPIC_LIFESTYLE_MANAGEMENT"},
    {"count": 8, "topic": "TOPIC_MEDICATION_INFORMATION"},
    {"count": 8, "topic": "TOPIC_MISSED_DOSE"},
    {"count": 8, "topic": "TOPIC_PRECAUTIONS"},
    {"count": 8, "topic": "TOPIC_STORAGE"},
]
_LEAKAGE_AXES: list[JsonValue] = [
    "question_template",
    "source_segment",
    "medication_family",
    "transform_origin",
]
_BLOCKING_CODES: list[JsonValue] = [
    "BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER",
    "BLOCKED_BY_RAG_14_ADAPTER",
    "WAITING_FOR_HOLDOUT_ACCESS_AUTHORIZATION",
    "WAITING_FOR_HOLDOUT_FREEZE",
]
_FORBIDDEN_PUBLIC_KEY_FRAGMENTS = (
    "query",
    "gold_body",
    "record_label",
    "fingerprint_value",
    "hmac_value",
    "key_material",
    "credential",
    "protected_path",
)
_ACCESS_CONTROL_REQUIREMENTS: list[JsonValue] = [
    "AUDIT_GRANT_REVOKE_READ_WRITE_FREEZE_AND_RUN",
    "DEFAULT_DENY",
    "DENY_GENERAL_CI_AND_DEVELOPER_CHECKOUT",
    "EXPLICIT_AUTHOR_CUSTODIAN_AND_RUNNER_IDENTITIES_ONLY",
    "NO_SELF_APPROVAL",
]
_AUTHORING_START_GATE: list[JsonValue] = [
    "ACCESS_CONTROL_IMPLEMENTED",
    "ACCESS_AUTHORIZATION_RECEIPT_RECORDED",
]
_ACCESS_CONTROL_START_GATE: list[JsonValue] = ["PROTECTED_RETRIEVAL_RUNNER_ISSUE_CREATED"]
_FREEZE_START_GATE: list[JsonValue] = [
    "DATASET_CUSTODIAN_REVIEW_COMPLETE",
    "FOUR_AXIS_INTERSECTIONS_ZERO",
    "HOLDOUT_40_AUTHORED_IN_PROTECTED_BOUNDARY",
]
_FUTURE_PRIVATE_STUDY_SPLIT_RECEIPT: dict[str, JsonValue] = {
    "availability": "NOT_CREATED",
    "boundary": "PROTECTED_ONLY",
    "required_private_inputs": [
        "AUTHORIZATION_RECEIPT_REF",
        "CANONICAL_IDENTITY_HMAC_ALGORITHM_REF",
        "DEV_AND_HOLDOUT_AUTHORING_IDENTITY_MANIFEST_REFS",
        "DEV_AND_HOLDOUT_DATASET_REFS",
        "EVALUATION_CONFIG_AND_GOLD_SCHEMA_REFS",
        "FINGERPRINT_ALGORITHM_REFS",
        "HMAC_KEY_VERSION",
        "ZERO_INTERSECTION_AXIS_SUMMARIES",
    ],
    "schema_id": "rag-eval.study-split-receipt",
    "schema_version": "1.0.0",
}
_FUTURE_PUBLIC_FREEZE_EVIDENCE_FIELDS: list[JsonValue] = [
    "actor",
    "axis_intersection_counts",
    "dev_count",
    "holdout_count",
    "receipt_id",
    "receipt_raw_sha256",
    "receipt_version",
    "recorded_at",
]
_PUBLIC_PACKET_KEYS = {
    "access_authorized",
    "access_control_requirements",
    "access_control_start_gate",
    "actual_run_ref",
    "authoring_start_gate",
    "blocking_codes",
    "contract_status",
    "custodian_conflict_approver",
    "dataset",
    "format_id",
    "format_version",
    "freeze_recorded",
    "freeze_start_gate",
    "future_private_study_split_receipt",
    "future_public_freeze_evidence_fields",
    "holdout_authored",
    "holdout_plan",
    "implementation_owner",
    "issue",
    "leakage_axes",
    "preparation_sha256",
    "preparation_status",
    "product_evaluation_reviewer",
    "protected_runner_issue_status",
    "purpose",
    "release_eligible",
    "requested_dataset_custodian",
}


def _read_manifest(evals_root: Path) -> dict[str, Any]:
    value = json.loads((evals_root / DATASET_MANIFEST_PATH).read_bytes())
    if not isinstance(value, dict):
        raise RuntimeError("Issue 273 Dataset manifest must be a JSON object")
    return value


def _require_approved_dev_manifest(manifest: dict[str, Any]) -> None:
    if canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    ) != manifest.get("manifest_sha256"):
        raise RuntimeError("Issue 273 HOLDOUT preparation Dataset manifest self hash mismatch")
    provenance = manifest.get("review_provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("team_gold_status") != "APPROVED"
        or provenance.get("authored_by") != _IMPLEMENTATION_OWNER
        or provenance.get("reviewed_by") != _PRODUCT_EVALUATION_REVIEWER
        or provenance.get("approved_by") != _REQUESTED_DATASET_CUSTODIAN
        or not isinstance(provenance.get("approved_at"), str)
    ):
        raise RuntimeError("Issue 273 HOLDOUT preparation requires approved Dataset provenance")


def _reject_protected_public_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).casefold()
            if any(fragment in lowered for fragment in _FORBIDDEN_PUBLIC_KEY_FRAGMENTS):
                raise RuntimeError("Issue 273 public preparation contains a protected HOLDOUT field")
            _reject_protected_public_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_protected_public_keys(nested)


def _validate_roles(packet: dict[str, JsonValue]) -> None:
    actors = (
        packet.get("implementation_owner"),
        packet.get("product_evaluation_reviewer"),
        packet.get("requested_dataset_custodian"),
        packet.get("custodian_conflict_approver"),
    )
    actor_ids = [actor.get("actor_id") for actor in actors if isinstance(actor, dict)]
    if len(actor_ids) != 4 or len(set(actor_ids)) != 4:
        raise RuntimeError("Issue 273 preparation requires separate human roles")
    if any(
        packet.get(field) != expected
        for field, expected in (
            ("implementation_owner", _IMPLEMENTATION_OWNER),
            ("product_evaluation_reviewer", _PRODUCT_EVALUATION_REVIEWER),
            ("requested_dataset_custodian", _REQUESTED_DATASET_CUSTODIAN),
            ("custodian_conflict_approver", _CUSTODIAN_CONFLICT_APPROVER),
        )
    ):
        raise RuntimeError("Issue 273 preparation requires the fixed role assignment")


def _validate_static_contract(packet: dict[str, JsonValue]) -> None:
    if set(packet) != _PUBLIC_PACKET_KEYS:
        raise RuntimeError("Issue 273 preparation requires the exact public preparation fields")
    expected_state: dict[str, JsonValue] = {
        "actual_run_ref": None,
        "contract_status": "REPOSITORY_LOCAL_NON_RUNTIME_PROJECTION",
        "dataset": {
            "manifest_sha256": _EXPECTED_DATASET_MANIFEST_SHA256,
            "ref": "rag-natural-language-retrieval-dev@1.0.0",
            "status": "DRAFT",
        },
        "format_id": "issue-273.holdout-freeze-preparation",
        "format_version": "1.0.0",
        "issue": "#273",
        "preparation_status": "PREPARATION_READY",
        "protected_runner_issue_status": "NOT_CREATED",
        "purpose": "PREPARATION_ONLY",
        "release_eligible": False,
    }
    if any(packet.get(field) != expected for field, expected in expected_state.items()):
        raise RuntimeError("Issue 273 preparation requires the exact public preparation state")
    protected_contract = (
        ("access_control_requirements", _ACCESS_CONTROL_REQUIREMENTS),
        ("access_control_start_gate", _ACCESS_CONTROL_START_GATE),
        ("authoring_start_gate", _AUTHORING_START_GATE),
        ("freeze_start_gate", _FREEZE_START_GATE),
        ("future_private_study_split_receipt", _FUTURE_PRIVATE_STUDY_SPLIT_RECEIPT),
        ("future_public_freeze_evidence_fields", _FUTURE_PUBLIC_FREEZE_EVIDENCE_FIELDS),
    )
    if any(packet.get(field) != expected for field, expected in protected_contract):
        raise RuntimeError("Issue 273 preparation requires the exact protected-boundary contract")


def _validate_public_packet(packet: dict[str, JsonValue]) -> None:
    _reject_protected_public_keys(packet)
    if any(packet.get(field) is not False for field in ("access_authorized", "holdout_authored", "freeze_recorded")):
        raise RuntimeError("Issue 273 preparation cannot record authorization, authoring, or Freeze")
    _validate_roles(packet)
    if packet.get("leakage_axes") != _LEAKAGE_AXES:
        raise RuntimeError("Issue 273 preparation requires the four ordered leakage axes")
    holdout_plan = packet.get("holdout_plan")
    if not isinstance(holdout_plan, dict) or holdout_plan != {
        "authored_questions": 0,
        "planned_questions": 40,
        "topic_counts": _TOPIC_COUNTS,
    }:
        raise RuntimeError("Issue 273 preparation requires the exact 40-question HOLDOUT plan")
    if packet.get("blocking_codes") != _BLOCKING_CODES:
        raise RuntimeError("Issue 273 preparation requires the exact remaining blockers")
    _validate_static_contract(packet)
    if canonical_sha256(
        packet,
        excluded_top_level_keys=frozenset({"preparation_sha256"}),
    ) != packet.get("preparation_sha256"):
        raise RuntimeError("Issue 273 HOLDOUT preparation self hash mismatch")


def build_holdout_freeze_preparation(evals_root: Path) -> dict[str, JsonValue]:
    manifest = _read_manifest(evals_root)
    _require_approved_dev_manifest(manifest)
    if (
        manifest.get("dataset_code") != "rag-natural-language-retrieval-dev"
        or manifest.get("dataset_version") != "1.0.0"
    ):
        raise RuntimeError("Issue 273 HOLDOUT preparation requires the approved DEV Dataset")
    if manifest.get("manifest_sha256") != _EXPECTED_DATASET_MANIFEST_SHA256:
        raise RuntimeError("Issue 273 HOLDOUT preparation Dataset manifest hash mismatch")
    if manifest.get("status") != "DRAFT" or manifest.get("frozen_at") is not None:
        raise RuntimeError("Issue 273 HOLDOUT preparation requires an unfrozen DRAFT Dataset")
    partition_counts = manifest.get("partition_counts")
    if (
        not isinstance(partition_counts, dict)
        or partition_counts.get("DEV") != 60
        or partition_counts.get("HOLDOUT") != 0
    ):
        raise RuntimeError("Issue 273 HOLDOUT preparation requires DEV 60 and HOLDOUT 0")

    payload: dict[str, JsonValue] = {
        "access_authorized": False,
        "actual_run_ref": None,
        "access_control_requirements": deepcopy(_ACCESS_CONTROL_REQUIREMENTS),
        "access_control_start_gate": deepcopy(_ACCESS_CONTROL_START_GATE),
        "authoring_start_gate": deepcopy(_AUTHORING_START_GATE),
        "blocking_codes": deepcopy(_BLOCKING_CODES),
        "contract_status": "REPOSITORY_LOCAL_NON_RUNTIME_PROJECTION",
        "custodian_conflict_approver": cast(JsonValue, deepcopy(_CUSTODIAN_CONFLICT_APPROVER)),
        "dataset": {
            "manifest_sha256": cast(str, manifest["manifest_sha256"]),
            "ref": f"{manifest['dataset_code']}@{manifest['dataset_version']}",
            "status": "DRAFT",
        },
        "format_id": "issue-273.holdout-freeze-preparation",
        "format_version": "1.0.0",
        "freeze_recorded": False,
        "freeze_start_gate": deepcopy(_FREEZE_START_GATE),
        "future_private_study_split_receipt": deepcopy(_FUTURE_PRIVATE_STUDY_SPLIT_RECEIPT),
        "future_public_freeze_evidence_fields": deepcopy(_FUTURE_PUBLIC_FREEZE_EVIDENCE_FIELDS),
        "holdout_authored": False,
        "holdout_plan": {
            "authored_questions": 0,
            "planned_questions": 40,
            "topic_counts": deepcopy(_TOPIC_COUNTS),
        },
        "implementation_owner": cast(JsonValue, deepcopy(_IMPLEMENTATION_OWNER)),
        "issue": "#273",
        "leakage_axes": deepcopy(_LEAKAGE_AXES),
        "preparation_sha256": "0" * 64,
        "preparation_status": "PREPARATION_READY",
        "product_evaluation_reviewer": cast(JsonValue, deepcopy(_PRODUCT_EVALUATION_REVIEWER)),
        "protected_runner_issue_status": "NOT_CREATED",
        "purpose": "PREPARATION_ONLY",
        "release_eligible": False,
        "requested_dataset_custodian": cast(JsonValue, deepcopy(_REQUESTED_DATASET_CUSTODIAN)),
    }
    payload["preparation_sha256"] = canonical_sha256(
        payload,
        excluded_top_level_keys=frozenset({"preparation_sha256"}),
    )
    _validate_public_packet(payload)
    return payload


def render_holdout_freeze_preparation_markdown(packet: dict[str, JsonValue]) -> str:
    _validate_public_packet(packet)
    dataset = cast(dict[str, Any], packet["dataset"])
    holdout_plan = cast(dict[str, Any], packet["holdout_plan"])
    lines = [
        "# Issue #273 HOLDOUT Freeze Preparation",
        "",
        "> 이 문서는 저장소 로컬 비런타임 준비 projection입니다.",
        "> `PREPARATION_READY`는 접근 승인이나 Freeze 완료가 아닙니다.",
        "",
        "## 현재 상태",
        "",
        f"- Dataset: `{dataset['ref']}` (`{dataset['status']}`, unfrozen)",
        f"- Dataset manifest SHA-256: `{dataset['manifest_sha256']}`",
        f"- HOLDOUT: 계획 `{holdout_plan['planned_questions']}` / 작성 `{holdout_plan['authored_questions']}`",
        "- 전용 protected Retrieval Runner Issue: `NOT_CREATED`",
        "- 접근 승인: `false`; Freeze 기록: `false`; Actual Run: `NOT_CREATED`",
        "- Release eligible: `false`; Production은 닫혀 있습니다.",
        "",
        "## 담당자와 역할 분리",
        "",
        "- 구현 담당: 정현우 (`@ceohwj`, `EVALUATION_IMPLEMENTER`)",
        "- Product/Evaluation 검토: 권가빈 (`@hazelnutflavoured`, `EVALUATION_REVIEWER`)",
        "- 요청 Dataset Custodian: 송은영 (`@phina-io`, `DATASET_CUSTODIAN`)",
        "- `@phina-io`가 접근 통제를 구현하면 김지혜 (`@Jye-rookie`)가 독립 승인합니다.",
        "",
        "## HOLDOUT 계획",
        "",
        *(f"- `{row['topic']}`: `{row['count']}`" for row in holdout_plan["topic_counts"]),
        "",
        "Leakage 검증 축은 `question_template`, `source_segment`, `medication_family`, `transform_origin` 네 개입니다.",
        "",
        "## 접근 통제 완료 조건",
        "",
        "- 기본 거부와 명시적 작성자·Custodian·보호 Runner identity만 허용합니다.",
        "- 일반 개발 checkout, 일반 CI, PR artifact, Issue와 로그에는 HOLDOUT을 노출하지 않습니다.",
        "- grant, revoke, read, write, freeze, run을 actor·UTC timestamp·logical ID로 감사합니다.",
        "- 접근 통제 구현자는 자신의 구현을 최종 승인할 수 없습니다.",
        "",
        "## 정확한 후속 시점",
        "",
        "1. 이 준비 PR 병합 직후 전용 protected Retrieval Runner Issue를 생성합니다.",
        "2. 전용 Issue에서 보호 환경 ACL·감사·service identity를 구현합니다.",
        "3. 독립 Dataset Custodian의 접근 승인 event가 생성된 뒤에만 HOLDOUT 40개 작성을 시작합니다.",
        "4. 보호 환경에서 네 leakage 축의 교집합이 모두 0이고 40개 검토가 끝난 뒤 Freeze합니다.",
        "5. 저장소에는 raw content 없이 receipt id/version/raw SHA-256, actor, timestamp와 집계만 기록합니다.",
        "6. #178의 실제 Retriever Adapter와 보호 Runner가 준비된 뒤에만 DEV 60 + HOLDOUT 40을 실행합니다.",
        "",
        "## 공개 금지",
        "",
        "HOLDOUT 질문·Gold·hard negative label·authoring identity digest·fingerprint/HMAC 값·key material·"
        "credential·보호 저장 위치는 이 저장소에 기록하지 않습니다.",
        "OTC 범위는 Issue #278에서 별도로 진행하며 #273 HOLDOUT에 혼합하지 않습니다.",
        "",
        "## 남은 Blocker",
        "",
        *(f"- `{code}`" for code in cast(list[str], packet["blocking_codes"])),
        "",
        f"Preparation self hash: `{packet['preparation_sha256']}`",
        "",
    ]
    return "\n".join(lines)


def write_holdout_freeze_preparation(repository_root: Path) -> None:
    packet = build_holdout_freeze_preparation(repository_root / "evals")
    json_path = repository_root / HOLDOUT_PREPARATION_JSON_PATH
    markdown_path = repository_root / HOLDOUT_PREPARATION_MARKDOWN_PATH
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_bytes(canonical_json_bytes(packet))
    markdown_path.write_text(render_holdout_freeze_preparation_markdown(packet), encoding="utf-8")


__all__ = [
    "HOLDOUT_PREPARATION_JSON_PATH",
    "HOLDOUT_PREPARATION_MARKDOWN_PATH",
    "build_holdout_freeze_preparation",
    "render_holdout_freeze_preparation_markdown",
    "write_holdout_freeze_preparation",
]
