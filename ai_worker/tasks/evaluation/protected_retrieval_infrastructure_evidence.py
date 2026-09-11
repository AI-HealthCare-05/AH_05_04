"""Deterministic, non-sensitive evidence for the Issue #368 repository adapter."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256

EVIDENCE_JSON_PATH = "docs/validation/rag/issue-273/protected-runner-infrastructure-adapter.json"
EVIDENCE_MARKDOWN_PATH = "docs/validation/rag/issue-273/protected-runner-infrastructure-adapter.md"

_IMPLEMENTATION_FILES = (
    ("ASYNC_KERNEL_SEAM", "ai_worker/tasks/evaluation/protected_retrieval.py"),
    ("POSTGRESQL_ADAPTER", "ai_worker/adapters/postgresql_protected_retrieval.py"),
    ("FAIL_CLOSED_CONFIG", "ai_worker/core/config.py"),
    ("EXPLICIT_RUNTIME_ASSEMBLY", "ai_worker/core/runtime_assembly.py"),
    ("PROTECTED_ROLE_POLICY", "infra/python/protected_retrieval_role_policy.py"),
    ("ISOLATED_MIGRATION_ENV", "infra/protected_retrieval/env.py"),
    ("ISOLATED_MIGRATION", "infra/protected_retrieval/versions/368000000001_create_protected_retrieval.py"),
    ("AUTHORIZATION_CONTROL_CONTRACT", "ai_worker/tasks/evaluation/protected_retrieval_control.py"),
    ("POSTGRESQL_CONTROL_ADAPTER", "ai_worker/adapters/postgresql_protected_retrieval_control.py"),
    (
        "AUTHORIZATION_CONTROL_MIGRATION",
        "infra/protected_retrieval/versions/368000000002_add_authorization_control.py",
    ),
)

_TOP_LEVEL_FIELDS = frozenset(
    {
        "access_authorized",
        "activation_blockers",
        "actual_run_ref",
        "approval_ingestion_status",
        "authorization_control_c1_status",
        "captured_at",
        "dataset_lifecycle_freeze_status",
        "decision",
        "disposal_status",
        "effective_enforcement_status",
        "evidence_sha256",
        "format_id",
        "format_version",
        "freeze_recorded",
        "grant_revoke_expire_status",
        "holdout_authored",
        "implementation_files",
        "issue",
        "production_approval_source_connector_status",
        "release_eligible",
        "remaining_repository_scope",
        "repository_adapter_status",
        "verification",
    }
)


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _validate(evidence: dict[str, JsonValue], repository_root: Path | None = None) -> None:
    if set(evidence) != _TOP_LEVEL_FIELDS:
        raise RuntimeError("Issue 368 evidence contains fields outside the allowlisted schema")
    expected_state: dict[str, JsonValue] = {
        "access_authorized": False,
        "approval_ingestion_status": "IMPLEMENTED_IN_REPOSITORY",
        "actual_run_ref": None,
        "authorization_control_c1_status": "IMPLEMENTED_IN_REPOSITORY",
        "disposal_status": "BLOCKED_BY_ISSUE_425",
        "effective_enforcement_status": "NOT_IMPLEMENTED",
        "freeze_recorded": False,
        "grant_revoke_expire_status": "IMPLEMENTED_IN_REPOSITORY",
        "holdout_authored": False,
        "release_eligible": False,
        "repository_adapter_status": "PARTIALLY_IMPLEMENTED",
        "production_approval_source_connector_status": "NOT_IMPLEMENTED",
        "dataset_lifecycle_freeze_status": "NOT_IMPLEMENTED",
    }
    if any(evidence.get(key) != value for key, value in expected_state.items()):
        raise RuntimeError("Issue 368 evidence cannot promote an operational activation state")
    if repository_root is not None:
        expected_files = [
            {"component": component, "path": path, "raw_sha256": _file_sha256(repository_root / path)}
            for component, path in _IMPLEMENTATION_FILES
        ]
        if evidence.get("implementation_files") != expected_files:
            raise RuntimeError("Issue 368 implementation file hashes do not match")
    if canonical_sha256(evidence, excluded_top_level_keys=frozenset({"evidence_sha256"})) != evidence.get(
        "evidence_sha256"
    ):
        raise RuntimeError("Issue 368 evidence self hash does not match")


def build_protected_retrieval_infrastructure_evidence(repository_root: Path) -> dict[str, JsonValue]:
    evidence: dict[str, JsonValue] = {
        "access_authorized": False,
        "activation_blockers": [
            "EXT_PRIV_001",
            "PRODUCTION_APPROVAL_SOURCE_CONNECTOR",
            "REAL_ENVIRONMENT_PROVISIONING",
            "INDEPENDENT_BACKEND_SECURITY_VERIFICATION",
            "BACKUP_RESTORE_AND_ROTATION_EVIDENCE",
            "TRACK_F_EXTERNAL_GATE",
        ],
        "actual_run_ref": None,
        "approval_ingestion_status": "IMPLEMENTED_IN_REPOSITORY",
        "authorization_control_c1_status": "IMPLEMENTED_IN_REPOSITORY",
        "captured_at": "2026-09-11T00:00:00.000000Z",
        "dataset_lifecycle_freeze_status": "NOT_IMPLEMENTED",
        "decision": {
            "id": "PD-368-R1",
            "path": "docs/governance/decisions/2026-09-11-protected-retrieval-authorization-control.md",
            "status": "CANDIDATE_COORDINATION_CONFIRMED_PR_REVIEW_REQUIRED",
        },
        "disposal_status": "BLOCKED_BY_ISSUE_425",
        "effective_enforcement_status": "NOT_IMPLEMENTED",
        "evidence_sha256": "0" * 64,
        "format_id": "issue-273.protected-runner-infrastructure-adapter",
        "format_version": "1.0.0",
        "freeze_recorded": False,
        "grant_revoke_expire_status": "IMPLEMENTED_IN_REPOSITORY",
        "holdout_authored": False,
        "implementation_files": [
            {"component": component, "path": path, "raw_sha256": _file_sha256(repository_root / path)}
            for component, path in _IMPLEMENTATION_FILES
        ],
        "issue": "#368",
        "production_approval_source_connector_status": "NOT_IMPLEMENTED",
        "release_eligible": False,
        "remaining_repository_scope": [
            "PRODUCTION_APPROVAL_SOURCE_CONNECTOR",
            "DATASET_TRANSITION_AND_FREEZE_SERVICE",
        ],
        "repository_adapter_status": "PARTIALLY_IMPLEMENTED",
        "verification": [
            {
                "command_id": "AUTHORIZATION_CONTROL_RELATED",
                "result": "107_PASSED",
            },
            {
                "command_id": "RUNTIME_ASSEMBLY",
                "result": "24_PASSED",
            },
            {
                "command_id": "WORKER_IMAGE_PROTECTED_OFF_IMPORT",
                "result": "PASSED",
            },
        ],
    }
    evidence["evidence_sha256"] = canonical_sha256(evidence, excluded_top_level_keys=frozenset({"evidence_sha256"}))
    _validate(evidence, repository_root)
    return evidence


def render_protected_retrieval_infrastructure_evidence(evidence: dict[str, JsonValue]) -> str:
    _validate(evidence)
    return "\n".join(
        [
            "# Issue #368 Protected Retrieval Infrastructure Adapter",
            "",
            "> 저장소 구현 증빙입니다. 실제 protected 환경 활성화나 HOLDOUT 접근 승인 증빙이 아닙니다.",
            "",
            "## 상태",
            "",
            "- Repository adapter: `PARTIALLY_IMPLEMENTED`",
            "- Authorization control C1: `IMPLEMENTED_IN_REPOSITORY`",
            "- Implemented scope: approval ingestion and grant/revoke/expire transaction·audit services",
            "- Production approval source connector: `NOT_IMPLEMENTED`",
            "- Dataset lifecycle/FREEZE service: `NOT_IMPLEMENTED`",
            "- Effective enforcement: `NOT_IMPLEMENTED`",
            "- Access authorized: `false`",
            "- HOLDOUT authored: `false`",
            "- Freeze recorded: `false`",
            "- Actual run: `NOT_CREATED`",
            "- Disposal: `BLOCKED_BY_ISSUE_425`",
            "- Release eligible: `false`",
            "",
            "## 검증",
            "",
            "- Authorization-control related suite: `107 passed`",
            "- Runtime assembly suite: `24 passed`",
            "- Protected-off Worker image import: `passed`",
            "- 실제 환경 좌표와 보호 데이터는 사용하지 않았습니다.",
            "",
            "## 활성화 전 필수 조건",
            "",
            "- `EXT-PRIV-001` 승인",
            "- 실제 환경 provisioning 및 독립 Backend·Security 검증",
            "- backup·restore·rotation 운영 증빙",
            "- Track F external gate 충족",
            "",
            f"Evidence self hash: `{evidence['evidence_sha256']}`",
            "",
        ]
    )


def write_protected_retrieval_infrastructure_evidence(repository_root: Path) -> None:
    evidence = build_protected_retrieval_infrastructure_evidence(repository_root)
    (repository_root / EVIDENCE_JSON_PATH).write_bytes(canonical_json_bytes(evidence))
    (repository_root / EVIDENCE_MARKDOWN_PATH).write_text(
        render_protected_retrieval_infrastructure_evidence(evidence), encoding="utf-8"
    )


__all__ = [
    "EVIDENCE_JSON_PATH",
    "EVIDENCE_MARKDOWN_PATH",
    "build_protected_retrieval_infrastructure_evidence",
    "render_protected_retrieval_infrastructure_evidence",
    "write_protected_retrieval_infrastructure_evidence",
]
