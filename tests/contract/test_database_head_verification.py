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
    assert migration_heads() == ("175a1b2c3d4e",)


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
    """전체 DB 검증은 `upgrade head` 뒤에 실행되어야 한다.

    #439에서 같은 스크립트를 `--heads-only`로 앞단에도 호출하게 되었으므로, 두 호출을
    구분해 판정한다. 앞단 호출은 DB 없이 head 개수만 보고, 뒤쪽 호출이 실제 카탈로그
    상태를 검증한다.
    """
    command = "python scripts/ci/verify_database_head.py"
    for relative_path in (".github/workflows/checks.yml", "scripts/ci/run_test.sh"):
        source = (ROOT / relative_path).read_text()
        invocations = [line.strip() for line in source.splitlines() if command in line]
        full_verifications = [line for line in invocations if "--heads-only" not in line]
        assert len(full_verifications) == 1, invocations

        full_at = source.index(full_verifications[0])
        assert source.index("upgrade head") < full_at


def test_heads_only_runs_before_any_alembic_upgrade() -> None:
    """진단이 `upgrade` 뒤에 있으면 도달하지 못해 의미가 없다 (#439).

    #398이 `test-migration`의 첫 단계를 고정 revision으로 바꾼 뒤, 기존
    `verify_database_head.py` 호출은 `upgrade head` 다음에 놓여 head 분기 시 실행되지
    않았다. 실패는 `alembic upgrade`의 "Multiple head revisions"로만 드러났고 `test`
    집계 job이 먼저 죽어 원인을 가렸다. 순서를 계약으로 고정한다.
    """
    for relative_path in (".github/workflows/checks.yml", "scripts/ci/run_test.sh"):
        source = (ROOT / relative_path).read_text()
        heads_only_at = source.find("--heads-only")
        first_upgrade_at = source.find("alembic -c backend/alembic.ini upgrade")

        assert heads_only_at != -1, relative_path
        assert first_upgrade_at != -1, relative_path
        assert heads_only_at < first_upgrade_at, relative_path


def test_heads_only_mode_reports_single_head_without_database() -> None:
    """`--heads-only`는 DB 없이 head 개수만 확인한다 (#439).

    CI는 이 모드를 `alembic upgrade` 앞에 둔다. head가 갈라진 상태는 원래
    `upgrade head`의 "Multiple head revisions"로만 드러나고, migration을 적용하기
    시작한 뒤에 실패해 원인이 가려진다.
    """
    from scripts.ci.verify_database_head import verify_single_head

    assert verify_single_head() == 0
