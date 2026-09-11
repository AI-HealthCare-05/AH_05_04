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
)


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _validate(evidence: dict[str, JsonValue], repository_root: Path | None = None) -> None:
    expected_state: dict[str, JsonValue] = {
        "access_authorized": False,
        "actual_run_ref": None,
        "disposal_status": "BLOCKED_BY_ISSUE_425",
        "effective_enforcement_status": "NOT_IMPLEMENTED",
        "freeze_recorded": False,
        "holdout_authored": False,
        "release_eligible": False,
        "repository_adapter_status": "PARTIALLY_IMPLEMENTED",
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
            "REAL_ENVIRONMENT_PROVISIONING",
            "INDEPENDENT_BACKEND_SECURITY_VERIFICATION",
            "BACKUP_RESTORE_AND_ROTATION_EVIDENCE",
            "TRACK_F_EXTERNAL_GATE",
        ],
        "actual_run_ref": None,
        "captured_at": "2026-09-10T00:00:00.000000Z",
        "decision": {
            "id": "PD-368-20260909",
            "path": "docs/governance/decisions/2026-09-09-protected-retrieval-runner-access-control.md",
            "status": "APPROVED_TARGET_NOT_IMPLEMENTED_IN_REAL_ENVIRONMENT",
        },
        "disposal_status": "BLOCKED_BY_ISSUE_425",
        "effective_enforcement_status": "NOT_IMPLEMENTED",
        "evidence_sha256": "0" * 64,
        "format_id": "issue-273.protected-runner-infrastructure-adapter",
        "format_version": "1.0.0",
        "freeze_recorded": False,
        "holdout_authored": False,
        "implementation_files": [
            {"component": component, "path": path, "raw_sha256": _file_sha256(repository_root / path)}
            for component, path in _IMPLEMENTATION_FILES
        ],
        "issue": "#368",
        "release_eligible": False,
        "remaining_repository_scope": [
            "APPROVAL_EVIDENCE_INGESTION_SERVICE",
            "GRANT_REVOKE_EXPIRE_SERVICE",
            "DATASET_TRANSITION_AND_FREEZE_SERVICE",
        ],
        "repository_adapter_status": "PARTIALLY_IMPLEMENTED",
        "required_reviewers": [
            {"actor_id": "hazelnutflavoured", "scope": "PRODUCT_PRIVACY_SAFETY_EVALUATION"},
            {"actor_id": "phina-io", "scope": "BACKEND_SECURITY"},
        ],
        "verification": [
            {
                "command_id": "KERNEL_CONFIG_RUNTIME",
                "result": "163_PASSED",
            },
            {
                "command_id": "DISPOSABLE_POSTGRESQL_MIGRATION_ADAPTER",
                "result": "11_PASSED",
            },
            {
                "command_id": "DATABASE_LOGIC_POLICY_AND_SINGLE_HEAD",
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
            "- Implemented scope: data-plane READ/WRITE/RUN transaction and audit boundary",
            "- Remaining scope: approval ingestion, grant/revoke/expire, Dataset transition/FREEZE services",
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
            "- Kernel·config·runtime focused suite: `163 passed`",
            "- Disposable PostgreSQL migration·ACL·adapter suite: `11 passed`",
            "- Database logic policy and protected Alembic single head: `passed`",
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
