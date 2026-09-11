"""최신 Alembic head와 #398 최종 PostgreSQL 카탈로그 상태를 검증합니다.

`--heads-only`는 DB 연결 없이 Alembic head 개수만 확인합니다. 병렬 브랜치가 각자 작성
시점의 develop head를 `down_revision`으로 잡으면 head가 갈라지는데(#439), 그 상태는 지금
`alembic upgrade head`의 "Multiple head revisions are present" 로만 드러납니다. 그 실패는
migration을 이미 적용하기 시작한 뒤에 나오고, `test` 집계 job이 먼저 죽어 원인을 가립니다.
CI 초반에 이 모드를 한 번 실행하면 수 초 안에 원인과 대응을 직접 알려줍니다.
"""

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

ROOT = Path(__file__).resolve().parents[2]
APPLICATION_SCHEMAS = ("public", "source_cleanup")
CATALOG_REMOVED_FUNCTIONS = (
    "bind_rag_catalog_identity",
    "validate_rag_medication_search_entry",
    "reject_rag_catalog_set_mutation",
    "validate_rag_catalog_set_child_insert",
    "reject_bound_rag_catalog_member_mutation",
)
REMOVED_FUNCTIONS = (
    "transition_rag_source_snapshot",
    "guard_rag_snapshot_state_write",
    "prevent_rag_source_snapshot_mutation",
    "prevent_rag_source_ingestion_artifact_mutation",
    "prevent_rag_snapshot_verification_mutation",
    "prevent_rag_runtime_transition_mutation",
    "prevent_checkin_audit_mutation",
    "prevent_rag_evidence_citation_mutation",
    "prevent_prescription_version_mutation",
    "stamp_prescription_version_assembly_xid",
    "prevent_frozen_prescription_version_medication_insert",
    "check_prescription_version_medications",
    "check_prescription_active_version_medications",
    "check_medication_candidate_search_displayed_count",
    *CATALOG_REMOVED_FUNCTIONS,
)


@dataclass(frozen=True)
class DatabaseHeadState:
    revisions: tuple[str, ...]
    user_triggers: tuple[str, ...]
    rls_tables: tuple[str, ...]
    policies: tuple[str, ...]
    removed_functions: tuple[str, ...]
    assembly_xid_present: bool
    runtime_revision_unique_present: bool


def alembic_paths(root: Path = ROOT) -> tuple[Path, Path]:
    """저장소와 app 이미지에서 Alembic 설정 경로를 찾습니다."""
    candidates = (
        (root / "backend/alembic.ini", root / "backend/alembic"),
        (root / "alembic.ini", root / "alembic"),
    )
    for config_path, script_path in candidates:
        if config_path.is_file() and script_path.is_dir():
            return config_path, script_path
    raise FileNotFoundError(f"Alembic 설정과 migration 디렉터리를 찾을 수 없습니다: {root}")


def migration_heads(root: Path = ROOT) -> tuple[str, ...]:
    config_path, script_path = alembic_paths(root)
    alembic_config = Config(config_path)
    alembic_config.set_main_option("script_location", str(script_path))
    return tuple(ScriptDirectory.from_config(alembic_config).get_heads())


def validation_errors(expected_head: str, state: DatabaseHeadState) -> list[str]:
    errors = []
    if state.revisions != (expected_head,):
        errors.append(f"DB revision {state.revisions!r} != code head {(expected_head,)!r}")
    if state.user_triggers:
        errors.append(f"user-defined triggers remain: {', '.join(state.user_triggers)}")
    if state.rls_tables:
        errors.append(f"RLS remains enabled: {', '.join(state.rls_tables)}")
    if state.policies:
        errors.append(f"RLS policies remain: {', '.join(state.policies)}")
    if state.removed_functions:
        errors.append(f"removed functions remain: {', '.join(state.removed_functions)}")
    if state.assembly_xid_present:
        errors.append("removed column remains: public.prescription_version.assembly_xid")
    if not state.runtime_revision_unique_present:
        errors.append("runtime revision UNIQUE constraint is missing")
    return errors


async def read_database_head_state(connection: AsyncConnection) -> DatabaseHeadState:
    revisions = tuple(
        (await connection.scalars(text("SELECT version_num FROM alembic_version ORDER BY version_num"))).all()
    )
    user_triggers = tuple(
        (
            await connection.scalars(
                text(
                    "SELECT format('%I.%I.%I', n.nspname, c.relname, t.tgname) "
                    "FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=ANY(:schemas) AND NOT t.tgisinternal "
                    "ORDER BY n.nspname,c.relname,t.tgname"
                ),
                {"schemas": list(APPLICATION_SCHEMAS)},
            )
        ).all()
    )
    rls_tables = tuple(
        (
            await connection.scalars(
                text(
                    "SELECT format('%I.%I', n.nspname, c.relname) "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=ANY(:schemas) AND c.relkind IN ('r','p') "
                    "AND (c.relrowsecurity OR c.relforcerowsecurity) ORDER BY n.nspname,c.relname"
                ),
                {"schemas": list(APPLICATION_SCHEMAS)},
            )
        ).all()
    )
    policies = tuple(
        (
            await connection.scalars(
                text(
                    "SELECT format('%I.%I.%I', n.nspname, c.relname, p.polname) "
                    "FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=ANY(:schemas) ORDER BY n.nspname,c.relname,p.polname"
                ),
                {"schemas": list(APPLICATION_SCHEMAS)},
            )
        ).all()
    )
    removed_functions = tuple(
        (
            await connection.scalars(
                text(
                    "SELECT format('%I.%I', n.nspname, p.proname) "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE (n.nspname='public' AND p.proname=ANY(:functions)) "
                    "OR n.nspname='source_cleanup' ORDER BY n.nspname,p.proname"
                ),
                {"functions": list(REMOVED_FUNCTIONS)},
            )
        ).all()
    )
    assembly_xid_present = bool(
        await connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='prescription_version' "
                "AND column_name='assembly_xid')"
            )
        )
    )
    runtime_revision_unique_present = bool(
        await connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_schema='public' AND table_name='rag_runtime_environment_transition' "
                "AND constraint_name='uq_rag_runtime_transition_environment_revision' "
                "AND constraint_type='UNIQUE')"
            )
        )
    )
    return DatabaseHeadState(
        revisions=revisions,
        user_triggers=user_triggers,
        rls_tables=rls_tables,
        policies=policies,
        removed_functions=removed_functions,
        assembly_xid_present=assembly_xid_present,
        runtime_revision_unique_present=runtime_revision_unique_present,
    )


async def verify_database_head() -> int:
    from app.core import config as app_config

    heads = migration_heads()
    if len(heads) != 1:
        print(f"Alembic head가 하나가 아닙니다: {heads!r}")
        return 1

    engine = create_async_engine(app_config.database_url)
    try:
        async with engine.connect() as connection:
            state = await read_database_head_state(connection)
    finally:
        await engine.dispose()

    errors = validation_errors(heads[0], state)
    if errors:
        print("최신 DB head 종합 검증 실패:")
        print("\n".join(f"- {error}" for error in errors))
        return 1
    print(f"최신 DB head 종합 검증 통과 ({heads[0]}; Trigger/RLS/제거 함수 0개)")
    return 0


def verify_single_head() -> int:
    """DB 없이 코드 기준 Alembic head가 하나인지 확인합니다."""
    heads = migration_heads()
    if len(heads) == 1:
        print(f"Alembic head 단일 확인 ({heads[0]})")
        return 0

    print(f"Alembic head가 {len(heads)}개입니다: {heads!r}")
    print()
    print("병렬 브랜치가 같은 down_revision을 잡아 chain이 갈라졌습니다.")
    print("병합된 쪽이 기준이므로, 미병합 브랜치의 migration을 현재 develop head 뒤로 옮깁니다.")
    print()
    print("  1. git fetch origin && git merge/rebase origin/develop")
    print("  2. 내 migration의 down_revision을 현재 develop head로 변경")
    print("     (컬럼·제약 정의는 바꾸지 않습니다)")
    print("  3. uv run alembic -c backend/alembic.ini heads  # 단일인지 확인")
    print()
    print("downgrade/upgrade 테스트에 부모 revision을 하드코딩했다면, migration 모듈의")
    print("down_revision을 읽도록 바꾸면 재연결마다 테스트를 고치지 않아도 됩니다.")
    print()
    print("로컬 재현은 test DB를 초기화한 뒤 migration lane만 돌립니다. backend lane을")
    print("먼저 돌리면 conftest의 drop_all teardown이 테이블을 지워")
    print('relation "user" does not exist 로 오진하게 됩니다.')
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--heads-only",
        action="store_true",
        help="DB 연결 없이 Alembic head 개수만 확인합니다 (#439).",
    )
    arguments = parser.parse_args(argv)
    if arguments.heads_only:
        return verify_single_head()
    return asyncio.run(verify_database_head())


if __name__ == "__main__":
    raise SystemExit(main())
