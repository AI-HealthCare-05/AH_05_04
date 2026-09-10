"""Reject protected-table writes outside reviewed Python boundaries."""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOTS = ("backend/app/", "ai_worker/", "infra/python/", "scripts/", "tools/")

_SOURCE_WRITERS = frozenset(
    {
        "backend/app/repositories/rag_source_catalog_repository.py",
        "backend/app/admin/source_management_service.py",
        "ai_worker/adapters/sqlalchemy_source_snapshot_repository.py",
        "ai_worker/adapters/postgresql_source_cleanup.py",
        "ai_worker/tasks/rag/source_cleanup/execution.py",
    }
)
_CATALOG_WRITERS = frozenset(
    {"backend/app/repositories/rag_source_catalog_repository.py", "backend/app/admin/source_management_service.py"}
)
_PENDING_CATALOG_WRITERS: frozenset[str] = frozenset()

APPROVED_WRITERS: dict[str, frozenset[str]] = {
    "source_management_permission": frozenset({"backend/app/admin/source_management_permissions.py"}),
    "source_management_audit": frozenset(
        {"backend/app/admin/source_management_permissions.py", "backend/app/admin/source_management_service.py"}
    ),
    "rag_source": _SOURCE_WRITERS,
    "rag_source_endpoint": _SOURCE_WRITERS,
    "rag_source_operation": _SOURCE_WRITERS,
    "rag_source_snapshot": _SOURCE_WRITERS,
    "rag_source_ingestion_run": _SOURCE_WRITERS,
    "rag_source_ingestion_artifact": _SOURCE_WRITERS,
    "rag_source_snapshot_verification": _SOURCE_WRITERS,
    "rag_medication_product": _CATALOG_WRITERS,
    "rag_medication_ingredient": _CATALOG_WRITERS,
    "rag_medication_alias": _CATALOG_WRITERS,
    "rag_medication_product_component": _CATALOG_WRITERS,
    # Draft PR #372 tables stay fail-closed until its Python adapter and role policy are reviewed.
    "rag_entity_identity": _PENDING_CATALOG_WRITERS,
    "rag_medication_search_entry": _PENDING_CATALOG_WRITERS,
    "rag_catalog_set": _PENDING_CATALOG_WRITERS,
    "rag_catalog_set_source": _PENDING_CATALOG_WRITERS,
    "rag_catalog_set_member": _PENDING_CATALOG_WRITERS,
    "rag_catalog_set_hash": _PENDING_CATALOG_WRITERS,
    "prescription_version": frozenset({"backend/app/repositories/prescription_repository.py"}),
    "prescription_version_medication": frozenset({"backend/app/repositories/prescription_repository.py"}),
    "medication_candidate_search_result": frozenset({"backend/app/repositories/medication_candidate_repository.py"}),
    "checkin_audit": frozenset({"backend/app/repositories/medication_checkin_repository.py"}),
    "ai_job_intake_context": frozenset({"backend/app/repositories/rag_runtime_repository.py"}),
    "ai_job_execution_context": frozenset({"backend/app/repositories/rag_runtime_repository.py"}),
    "ai_job_execution_identification": frozenset({"backend/app/repositories/rag_runtime_repository.py"}),
    "rag_runtime_environment_transition": frozenset({"backend/app/repositories/rag_runtime_repository.py"}),
    "rag_evidence_knowledge": frozenset({"backend/app/repositories/rag_evidence_citation_repository.py"}),
    "rag_evidence": frozenset({"backend/app/repositories/rag_evidence_citation_repository.py"}),
    "rag_evidence_rule": frozenset({"backend/app/repositories/rag_evidence_citation_repository.py"}),
    "rag_evidence_guideline": frozenset({"backend/app/repositories/rag_evidence_citation_repository.py"}),
    "rag_citation": frozenset({"backend/app/repositories/rag_evidence_citation_repository.py"}),
}

MODEL_TABLES = {
    "SourceManagementPermission": "source_management_permission",
    "SourceManagementAudit": "source_management_audit",
    "RagSource": "rag_source",
    "RagSourceEndpoint": "rag_source_endpoint",
    "RagSourceOperation": "rag_source_operation",
    "RagSourceSnapshot": "rag_source_snapshot",
    "RagSourceIngestionRun": "rag_source_ingestion_run",
    "RagSourceIngestionArtifact": "rag_source_ingestion_artifact",
    "RagSourceSnapshotVerification": "rag_source_snapshot_verification",
    "RagMedicationProduct": "rag_medication_product",
    "RagMedicationIngredient": "rag_medication_ingredient",
    "RagMedicationAlias": "rag_medication_alias",
    "RagMedicationProductComponent": "rag_medication_product_component",
    "RagEntityIdentity": "rag_entity_identity",
    "RagMedicationSearchEntry": "rag_medication_search_entry",
    "RagCatalogSet": "rag_catalog_set",
    "RagCatalogSetSource": "rag_catalog_set_source",
    "RagCatalogSetMember": "rag_catalog_set_member",
    "RagCatalogSetHash": "rag_catalog_set_hash",
    "PrescriptionVersion": "prescription_version",
    "PrescriptionVersionMedication": "prescription_version_medication",
    "MedicationCandidateSearchResult": "medication_candidate_search_result",
    "CheckinAudit": "checkin_audit",
    "AiJobIntakeContext": "ai_job_intake_context",
    "AiJobExecutionContext": "ai_job_execution_context",
    "AiJobExecutionIdentification": "ai_job_execution_identification",
    "RagRuntimeEnvironmentTransition": "rag_runtime_environment_transition",
    "RagEvidenceKnowledge": "rag_evidence_knowledge",
    "RagEvidence": "rag_evidence",
    "RagEvidenceRule": "rag_evidence_rule",
    "RagEvidenceGuideline": "rag_evidence_guideline",
    "RagCitation": "rag_citation",
}

_RAW_DML = re.compile(
    r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+"
    r"(?:public\.)?(?P<table>" + "|".join(sorted(APPROVED_WRITERS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _table_bindings(tree: ast.AST) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call) or _call_name(value.func) != "Table" or not value.args:
            continue
        first = value.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue
        table = first.value.lower()
        if table not in APPROVED_WRITERS:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                bindings[target.id] = table
    return bindings


def protected_writes(path: Path, source: str) -> set[tuple[str, int]]:
    tree = ast.parse(source, filename=str(path))
    table_bindings = _table_bindings(tree)
    writes: set[tuple[str, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            table = MODEL_TABLES.get(call_name or "")
            if call_name in {"insert", "update", "delete"} and node.args:
                argument = node.args[0]
                argument_name = _call_name(argument)
                table = MODEL_TABLES.get(argument_name or "") or table_bindings.get(argument_name or "")
            if table is not None:
                writes.add((table, node.lineno))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            writes.update((match.group("table").lower(), node.lineno) for match in _RAW_DML.finditer(node.value))
    return writes


def violations(root: Path, paths: list[str]) -> list[str]:
    failures: list[str] = []
    for name in paths:
        if not name.endswith(".py") or not name.startswith(PRODUCTION_ROOTS) or "/tests" in name:
            continue
        path = root / name
        if not path.is_file():
            continue
        for table, line in protected_writes(path, path.read_text(encoding="utf-8")):
            if name not in APPROVED_WRITERS[table]:
                failures.append(f"{name}:{line}: unapproved write to {table}")
    return sorted(failures)


def main() -> int:
    paths = (
        subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT)
        .decode()
        .split("\0")
    )
    failures = violations(ROOT, paths)
    if failures:
        print("보호 테이블의 승인되지 않은 Python 쓰기 경로를 발견했습니다:")
        print("\n".join(failures))
        return 1
    print("보호 테이블 Python 쓰기 경계 검사 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
