from pathlib import Path

from scripts.ci.verify_database_head import DatabaseHeadState, migration_heads, validation_errors

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
    assert migration_heads() == ("398f60718293",)


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
