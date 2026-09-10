from pathlib import Path

from scripts.ci.verify_database_head import (
    CATALOG_REMOVED_FUNCTIONS,
    DatabaseHeadState,
    alembic_paths,
    migration_heads,
    validation_errors,
)

ROOT = Path(__file__).resolve().parents[2]


def valid_state() -> DatabaseHeadState:
    return DatabaseHeadState(
        revisions=("head",),
        user_triggers=(),
        rls_tables=(),
        policies=(),
        removed_functions=(),
        assembly_xid_present=False,
        runtime_revision_unique_present=True,
    )


def test_current_migration_tree_has_one_head() -> None:
    assert migration_heads() == ("423a1b2c3d4e",)


def test_draft_pr_372_catalog_functions_are_in_final_removal_inventory() -> None:
    assert set(CATALOG_REMOVED_FUNCTIONS) == {
        "bind_rag_catalog_identity",
        "validate_rag_medication_search_entry",
        "reject_rag_catalog_set_mutation",
        "validate_rag_catalog_set_child_insert",
        "reject_bound_rag_catalog_member_mutation",
    }


def test_finds_alembic_tree_in_repository_and_app_image_layout(tmp_path: Path) -> None:
    assert alembic_paths(ROOT) == (ROOT / "backend/alembic.ini", ROOT / "backend/alembic")
    (tmp_path / "alembic.ini").touch()
    (tmp_path / "alembic").mkdir()
    assert alembic_paths(tmp_path) == (tmp_path / "alembic.ini", tmp_path / "alembic")


def test_accepts_complete_final_database_state() -> None:
    assert validation_errors("head", valid_state()) == []


def test_reports_every_unsafe_final_database_state() -> None:
    state = DatabaseHeadState(
        revisions=("old",),
        user_triggers=("public.table.trigger",),
        rls_tables=("public.table",),
        policies=("public.table.policy",),
        removed_functions=("public.legacy",),
        assembly_xid_present=True,
        runtime_revision_unique_present=False,
    )

    errors = validation_errors("head", state)

    assert len(errors) == 7
    assert all(
        marker in "\n".join(errors)
        for marker in (
            "DB revision",
            "triggers remain",
            "RLS remains",
            "RLS policies",
            "functions remain",
            "assembly_xid",
            "UNIQUE",
        )
    )


def test_ci_and_local_runner_verify_database_after_upgrade_to_head() -> None:
    command = "python scripts/ci/verify_database_head.py"
    for relative_path in (".github/workflows/checks.yml", "scripts/ci/run_test.sh"):
        source = (ROOT / relative_path).read_text()
        assert command in source
        assert source.index("upgrade head") < source.index(command)
