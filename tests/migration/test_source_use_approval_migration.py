"""#807 migration metadata and schema-definition parity checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = PROJECT_ROOT / "backend" / "alembic" / "versions" / "807a1b2c3d4e_source_use_approval.py"


def _load_migration() -> Any:
    spec = importlib.util.spec_from_file_location("source_use_approval_migration", MIGRATION_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("#807 migration module을 불러올 수 없습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_has_expected_revision_and_single_parent() -> None:
    migration = _load_migration()

    assert migration.revision == "807a1b2c3d4e"
    assert migration.down_revision == "8f1c2d3e4a5b"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_migration_declares_exact_source_use_approval_constraints() -> None:
    migration_text = MIGRATION_PATH.read_text(encoding="utf-8")

    expected_fragments = (
        "source_snapshot_id",
        "source_code",
        "source_version",
        "environment IN ('LOCAL', 'TEST', 'CLOSED_DEMO', 'PRODUCTION')",
        "purpose IN ('PRODUCT_IDENTIFICATION', 'SAFETY_ROUTING', 'RULE_DERIVATION', 'RETRIEVAL', 'PATIENT_CITATION')",
        "approval_version",
        "expires_at > valid_from",
        "chk_rag_source_use_approval_revocation_shape",
        "fk_rag_source_use_approval_snapshot_version",
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
