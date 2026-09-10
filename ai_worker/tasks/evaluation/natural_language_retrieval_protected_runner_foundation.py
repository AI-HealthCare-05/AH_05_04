from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256

PROTECTED_RUNNER_FOUNDATION_JSON_PATH = "docs/validation/rag/issue-273/protected-runner-foundation.json"
PROTECTED_RUNNER_FOUNDATION_MARKDOWN_PATH = "docs/validation/rag/issue-273/protected-runner-foundation.md"

_KERNEL_PATH = "ai_worker/tasks/evaluation/protected_retrieval.py"
_SYNTHETIC_ADAPTER_PATH = "ai_worker/tasks/evaluation/protected_retrieval_synthetic.py"
_DATASET_MANIFEST_SHA256 = "b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2"
_PREPARATION_RAW_SHA256 = "40ea344c378298d99c14c372c27296322854d8e9b055fa179592568ca88bc192"
_PREPARATION_SELF_SHA256 = "b4a0a113d9efce867a434875f18ee259431d226a9cf1e4dcaed28152920600b6"
_ISSUE_SOURCE: dict[str, JsonValue] = {
    "api_resource_id": "I_kwDOT3EWNs8AAAABQTH1bg",
    "created_at": "2026-09-08T15:35:55.000000Z",
    "number": 368,
    "source_repository": "AI-HealthCare-05/AH_05_04",
    "state_at_capture": "OPEN",
    "url": "https://github.com/AI-HealthCare-05/AH_05_04/issues/368",
}
_IMPLEMENTATION_OWNER: dict[str, JsonValue] = {
    "actor_id": "ceohwj",
    "namespace": "GITHUB_LOGIN",
    "role": "EVALUATION_IMPLEMENTER",
}
_PRODUCT_REVIEWER: dict[str, JsonValue] = {
    "actor_id": "hazelnutflavoured",
    "namespace": "GITHUB_LOGIN",
    "role": "PRODUCT_PRIVACY_SAFETY_EVALUATION_REVIEWER",
}
_DATASET_CUSTODIAN: dict[str, JsonValue] = {
    "actor_id": "phina-io",
    "namespace": "GITHUB_LOGIN",
    "role": "DATASET_CUSTODIAN_BACKEND_SECURITY_REVIEWER",
}
_CONDITIONAL_CUSTODIAN: dict[str, JsonValue] = {
    "actor_id": "Jye-rookie",
    "namespace": "GITHUB_LOGIN",
    "role": "CONDITIONAL_INDEPENDENT_DATASET_CUSTODIAN",
}
_BLOCKING_CODES: list[JsonValue] = [
    "BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER",
    "BLOCKED_BY_RAG_14_ADAPTER",
    "WAITING_FOR_HOLDOUT_ACCESS_AUTHORIZATION",
    "WAITING_FOR_HOLDOUT_FREEZE",
]
_INFRASTRUCTURE_DECISIONS: list[JsonValue] = [
    "APPROVE_DATABASE_OR_SCHEMA_BOUNDARY",
    "APPROVE_OWNER_AUTHOR_CUSTODIAN_RUNNER_ROLES",
    "APPROVE_PROTECTED_CREDENTIAL_ENVIRONMENT",
    "APPROVE_APPEND_ONLY_AUDIT_AND_RETENTION",
    "APPROVE_BACKUP_REVOKE_INCIDENT_RESPONSE",
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
_PUBLIC_PACKET_KEYS = {
    "access_authorized",
    "actual_run_ref",
    "blocking_codes",
    "captured_at",
    "conditional_custodian",
    "dataset",
    "dataset_custodian",
    "effective_enforcement_status",
    "format_id",
    "format_version",
    "foundation_sha256",
    "freeze_recorded",
    "holdout_authored",
    "holdout_preparation_ref",
    "implementation_owner",
    "infrastructure_adapter_status",
    "infrastructure_decision_gate",
    "issue",
    "issue_completion_status",
    "issue_canonical_subset_sha256",
    "kernel",
    "phase",
    "policy_foundation_status",
    "product_reviewer",
    "reconciliation_adapter_status",
    "release_eligible",
}


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _reject_protected_public_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).casefold()
            if any(fragment in lowered for fragment in _FORBIDDEN_PUBLIC_KEY_FRAGMENTS):
                raise RuntimeError("Issue 368 public foundation contains a protected HOLDOUT field")
            _reject_protected_public_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_protected_public_keys(nested)


def _validate_foundation(packet: dict[str, JsonValue], repository_root: Path | None = None) -> None:
    _reject_protected_public_keys(packet)
    if set(packet) != _PUBLIC_PACKET_KEYS:
        raise RuntimeError("Issue 368 foundation requires the exact public fields")
    exact_state: dict[str, JsonValue] = {
        "access_authorized": False,
        "actual_run_ref": None,
        "blocking_codes": _BLOCKING_CODES,
        "conditional_custodian": _CONDITIONAL_CUSTODIAN,
        "dataset": {
            "holdout_questions": 0,
            "manifest_sha256": _DATASET_MANIFEST_SHA256,
            "ref": "rag-natural-language-retrieval-dev@1.0.0",
            "status": "DRAFT",
        },
        "dataset_custodian": _DATASET_CUSTODIAN,
        "effective_enforcement_status": "NOT_IMPLEMENTED",
        "format_id": "issue-273.protected-runner-foundation",
        "format_version": "1.0.0",
        "freeze_recorded": False,
        "holdout_authored": False,
        "holdout_preparation_ref": {
            "id": "issue-273-holdout-freeze-preparation",
            "raw_sha256": _PREPARATION_RAW_SHA256,
            "self_sha256": _PREPARATION_SELF_SHA256,
            "version": "1.0.0",
        },
        "implementation_owner": _IMPLEMENTATION_OWNER,
        "infrastructure_adapter_status": "NOT_IMPLEMENTED",
        "infrastructure_decision_gate": _INFRASTRUCTURE_DECISIONS,
        "issue": _ISSUE_SOURCE,
        "issue_completion_status": "IN_PROGRESS",
        "issue_canonical_subset_sha256": canonical_sha256(_ISSUE_SOURCE),
        "phase": "PHASE_B3_PROTECTED_RUNNER_FOUNDATION",
        "policy_foundation_status": "IMPLEMENTED",
        "product_reviewer": _PRODUCT_REVIEWER,
        "reconciliation_adapter_status": "NOT_IMPLEMENTED",
        "release_eligible": False,
    }
    if any(packet.get(key) != value for key, value in exact_state.items()):
        raise RuntimeError("Issue 368 foundation requires the exact incomplete state")
    roles = [
        packet["implementation_owner"],
        packet["product_reviewer"],
        packet["dataset_custodian"],
        packet["conditional_custodian"],
    ]
    actor_ids = [cast(dict[str, Any], role)["actor_id"] for role in roles]
    if len(set(actor_ids)) != 4:
        raise RuntimeError("Issue 368 foundation requires separate human roles")
    kernel = packet.get("kernel")
    if not isinstance(kernel, dict) or set(kernel) != {"contract_version", "modules"}:
        raise RuntimeError("Issue 368 foundation requires exact kernel evidence")
    modules = kernel.get("modules")
    if not isinstance(modules, list) or len(modules) != 2:
        raise RuntimeError("Issue 368 foundation requires both kernel modules")
    if repository_root is not None:
        expected_modules = [
            {"path": _KERNEL_PATH, "raw_sha256": _file_sha256(repository_root / _KERNEL_PATH)},
            {
                "path": _SYNTHETIC_ADAPTER_PATH,
                "raw_sha256": _file_sha256(repository_root / _SYNTHETIC_ADAPTER_PATH),
            },
        ]
        if modules != expected_modules:
            raise RuntimeError("Issue 368 foundation kernel hash mismatch")
    if canonical_sha256(packet, excluded_top_level_keys=frozenset({"foundation_sha256"})) != packet.get(
        "foundation_sha256"
    ):
        raise RuntimeError("Issue 368 foundation self hash mismatch")


def build_protected_runner_foundation(repository_root: Path) -> dict[str, JsonValue]:
    packet: dict[str, JsonValue] = {
        "access_authorized": False,
        "actual_run_ref": None,
        "blocking_codes": deepcopy(_BLOCKING_CODES),
        "captured_at": "2026-09-09T00:00:00.000000Z",
        "conditional_custodian": deepcopy(_CONDITIONAL_CUSTODIAN),
        "dataset": {
            "holdout_questions": 0,
            "manifest_sha256": _DATASET_MANIFEST_SHA256,
            "ref": "rag-natural-language-retrieval-dev@1.0.0",
            "status": "DRAFT",
        },
        "dataset_custodian": deepcopy(_DATASET_CUSTODIAN),
        "effective_enforcement_status": "NOT_IMPLEMENTED",
        "format_id": "issue-273.protected-runner-foundation",
        "format_version": "1.0.0",
        "foundation_sha256": "0" * 64,
        "freeze_recorded": False,
        "holdout_authored": False,
        "holdout_preparation_ref": {
            "id": "issue-273-holdout-freeze-preparation",
            "raw_sha256": _PREPARATION_RAW_SHA256,
            "self_sha256": _PREPARATION_SELF_SHA256,
            "version": "1.0.0",
        },
        "implementation_owner": deepcopy(_IMPLEMENTATION_OWNER),
        "infrastructure_adapter_status": "NOT_IMPLEMENTED",
        "infrastructure_decision_gate": deepcopy(_INFRASTRUCTURE_DECISIONS),
        "issue": deepcopy(_ISSUE_SOURCE),
        "issue_completion_status": "IN_PROGRESS",
        "issue_canonical_subset_sha256": canonical_sha256(_ISSUE_SOURCE),
        "kernel": {
            "contract_version": "1.0.0",
            "modules": [
                {"path": _KERNEL_PATH, "raw_sha256": _file_sha256(repository_root / _KERNEL_PATH)},
                {
                    "path": _SYNTHETIC_ADAPTER_PATH,
                    "raw_sha256": _file_sha256(repository_root / _SYNTHETIC_ADAPTER_PATH),
                },
            ],
        },
        "phase": "PHASE_B3_PROTECTED_RUNNER_FOUNDATION",
        "policy_foundation_status": "IMPLEMENTED",
        "product_reviewer": deepcopy(_PRODUCT_REVIEWER),
        "reconciliation_adapter_status": "NOT_IMPLEMENTED",
        "release_eligible": False,
    }
    packet["foundation_sha256"] = canonical_sha256(packet, excluded_top_level_keys=frozenset({"foundation_sha256"}))
    _validate_foundation(packet, repository_root)
    return packet


def render_protected_runner_foundation_markdown(packet: dict[str, JsonValue]) -> str:
    _validate_foundation(packet)
    issue = cast(dict[str, Any], packet["issue"])
    dataset = cast(dict[str, Any], packet["dataset"])
    lines = [
        "# Issue #273 Phase B3 Protected Runner Foundation",
        "",
        "> 이 문서는 인프라 독립 policy foundation의 공개 증빙입니다.",
        "> 실제 접근 통제, HOLDOUT 접근 승인, Freeze, Retrieval 실행 또는 Release 완료 증빙이 아닙니다.",
        "",
        "## 현재 상태",
        "",
        f"- Phase: `{packet['phase']}`",
        f"- Protected Runner Issue: [#{issue['number']}]({issue['url']}) (`{issue['state_at_capture']}` at capture)",
        f"- Issue API resource ID: `{issue['api_resource_id']}`",
        f"- Issue canonical subset SHA-256: `{packet['issue_canonical_subset_sha256']}`",
        f"- Dataset: `{dataset['ref']}` (`{dataset['status']}`)",
        f"- Dataset manifest SHA-256: `{dataset['manifest_sha256']}`",
        f"- Policy foundation: `{packet['policy_foundation_status']}`",
        f"- Issue completion: `{packet['issue_completion_status']}`",
        f"- Effective enforcement: `{packet['effective_enforcement_status']}`",
        f"- Infrastructure adapter: `{packet['infrastructure_adapter_status']}`",
        f"- Reconciliation adapter: `{packet['reconciliation_adapter_status']}`",
        "- HOLDOUT: `0`; access authorization: `false`; Freeze: `false`; actual run: `NOT_CREATED`",
        "- Release eligible: `false`; Production remains closed.",
        "",
        "## 역할 분리",
        "",
        "- 구현 담당: 정현우 (`@ceohwj`)",
        "- Product·Privacy·Safety·Evaluation 검토: 권가빈 (`@hazelnutflavoured`)",
        "- Dataset Custodian·Backend·Security 검토: 송은영 (`@phina-io`)",
        "- `@phina-io`가 실제 ACL 구현에 참여하면 독립 Custodian은 김지혜 (`@Jye-rookie`)로 전환합니다.",
        "",
        "## 구현된 Foundation",
        "",
        "- 승인 원문 provenance 검증 및 self-approval 차단",
        "- 역할·Dataset 상태·artifact digest·grant revision에 결속된 fail-closed authorization",
        "- authorization/operation 감사 이벤트 분리와 global append-CAS hash chain",
        "- single-use capability, revocation guard, 성공 결과 멱등 반환과 UNKNOWN 자동 재실행 차단",
        "- UNKNOWN 독립 승인 reconciliation adapter는 아직 구현하지 않음",
        "- 실제 저장 위치를 노출하지 않는 random UUIDv4 logical reference",
        "- production CLI에 등록되지 않은 synthetic adapter 검증",
        "",
        "## 실제 인프라 결정 요청",
        "",
        *(f"- `{decision}`" for decision in cast(list[str], packet["infrastructure_decision_gate"])),
        "",
        "위 결정과 독립 승인이 기록되기 전에는 PostgreSQL migration, credential, protected loader와",
        "`run-protected-holdout`을 구현하거나 HOLDOUT 작성을 시작하지 않습니다.",
        "",
        "## 남은 Blocker",
        "",
        *(f"- `{code}`" for code in cast(list[str], packet["blocking_codes"])),
        "",
        f"Captured at `{packet['captured_at']}`. Foundation self hash: `{packet['foundation_sha256']}`",
        "",
    ]
    return "\n".join(lines)


def write_protected_runner_foundation(repository_root: Path) -> None:
    packet = build_protected_runner_foundation(repository_root)
    json_path = repository_root / PROTECTED_RUNNER_FOUNDATION_JSON_PATH
    markdown_path = repository_root / PROTECTED_RUNNER_FOUNDATION_MARKDOWN_PATH
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_bytes(canonical_json_bytes(packet) + b"\n")
    markdown_path.write_text(render_protected_runner_foundation_markdown(packet), encoding="utf-8")


__all__ = [
    "PROTECTED_RUNNER_FOUNDATION_JSON_PATH",
    "PROTECTED_RUNNER_FOUNDATION_MARKDOWN_PATH",
    "build_protected_runner_foundation",
    "render_protected_runner_foundation_markdown",
    "write_protected_runner_foundation",
]
