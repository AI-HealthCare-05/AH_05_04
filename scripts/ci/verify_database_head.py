"""최신 Alembic head와 #398 최종 PostgreSQL 카탈로그 상태를 검증합니다."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.core import config as app_config

ROOT = Path(__file__).resolve().parents[2]
APPLICATION_SCHEMAS = ("public", "source_cleanup")
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


def migration_heads(root: Path = ROOT) -> tuple[str, ...]:
    alembic_config = Config(root / "backend/alembic.ini")
    alembic_config.set_main_option("script_location", str(root / "backend/alembic"))
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


if __name__ == "__main__":
    raise SystemExit(asyncio.run(verify_database_head()))
