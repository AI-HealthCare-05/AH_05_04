"""#806 migration metadata and schema-definition parity checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "backend" / "alembic" / "versions" / "806a1b2c3d4e_request_guard_runtime_binding.py"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("request_guard_runtime_binding_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("#806 migration module을 불러올 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_has_expected_revision_and_single_parent() -> None:
    migration = _load_migration()

    assert migration.revision == "806a1b2c3d4e"
    assert migration.down_revision == "810a1b2c3d4e"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_migration_declares_expected_canonical_constraints() -> None:
    migration_text = MIGRATION_PATH.read_text(encoding="utf-8")

    expected_fragments = (
        "actual_decision_outcome IN ('PASS', 'FAIL')",
        "decision_stage = 'REQUEST'",
        "environment_code IN ('LOCAL', 'TEST', 'CLOSED_DEMO', 'PRODUCTION')",
        "bundle_id",
        "bundle_manifest_hash",
        "request_scope_codes",
        "scope_manifest_hash",
        "rag_request_guard_authority.artifact_content_sha256",
        "LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE",
    )
    for fragment in expected_fragments:
        assert fragment in migration_text


@pytest.mark.parametrize(
    ("forbidden",),
    (("CREATE " + "TRIGGER",), ("CREATE " + "POLICY",), ("CREATE " + "FUNCTION",)),
)
def test_migration_does_not_add_database_business_logic(forbidden: str) -> None:
    assert forbidden not in MIGRATION_PATH.read_text(encoding="utf-8").upper()
