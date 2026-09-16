"""#613 LOCAL_PRIVATE cleanup 요청을 executor credential로 수동 처리합니다."""

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.local_private_source_cleanup import (
    LocalPrivateArtifactCleanupExecutor,
    LocalPrivateCleanupExecutorJournal,
)
from ai_worker.adapters.sqlalchemy_orphan_artifact_references import (
    SqlAlchemyOrphanArtifactReferenceInspector,
    lock_source_artifact_mutation,
)
from ai_worker.tasks.rag.source_cleanup.orphan_artifact import CleanupReceipt, execute_cleanup_request

_ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,99}\Z")


@dataclass(frozen=True, slots=True)
class CleanupExecutorConfig:
    url: URL = field(repr=False)
    artifact_root: Path = field(repr=False)
    journal_root: Path = field(repr=False)
    actor: str

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "CleanupExecutorConfig":
        if any(env.get(key) for key in ("SOURCE_WRITER_PASSWORD", "DB_PASSWORD", "DB_ADMIN_PASSWORD")):
            raise ValueError("Cleanup executor requires an isolated credential environment")
        values = {
            name: env.get(f"SOURCE_CLEANUP_EXECUTOR_{name}", "")
            for name in ("HOST", "PORT", "NAME", "USER", "PASSWORD", "ACTOR")
        }
        if any(not value.strip() for value in values.values()) or _ACTOR.fullmatch(values["ACTOR"]) is None:
            raise ValueError("Cleanup executor configuration is incomplete")
        port = int(values["PORT"])
        if not 1 <= port <= 65535:
            raise ValueError("Cleanup executor port is invalid")
        artifact_root = Path(env.get("SOURCE_ARTIFACT_LOCAL_ROOT", ""))
        journal_root = Path(env.get("SOURCE_CLEANUP_JOURNAL_ROOT", ""))
        if not artifact_root.is_absolute() or not journal_root.is_absolute() or artifact_root == journal_root:
            raise ValueError("Cleanup roots are invalid")
        return cls(
            URL.create(
                "postgresql+asyncpg",
                username=values["USER"],
                password=values["PASSWORD"],
                host=values["HOST"],
                port=port,
                database=values["NAME"],
            ),
            artifact_root,
            journal_root,
            values["ACTOR"],
        )


async def run_cleanup(config: CleanupExecutorConfig, request_id: UUID) -> CleanupReceipt:
    journal = LocalPrivateCleanupExecutorJournal(config.journal_root)
    request = journal.read_request(request_id)
    engine = create_async_engine(config.url, hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            await validate_cleanup_executor_session(session)
            await lock_source_artifact_mutation(session)
            return await execute_cleanup_request(
                request=request,
                executor=config.actor,
                executed_at=datetime.now(UTC),
                references=SqlAlchemyOrphanArtifactReferenceInspector(session),
                artifacts=LocalPrivateArtifactCleanupExecutor(config.artifact_root),
                receipts=journal,
            )
    finally:
        await engine.dispose()


async def validate_cleanup_executor_session(session: AsyncSession) -> None:
    """전용 executor가 관리자·DB 쓰기 권한을 함께 갖지 않는지 확인합니다."""
    unsafe_role = await session.scalar(
        text(
            "SELECT r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls OR r.rolreplication "
            "OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid) "
            "OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=r.oid) "
            "OR has_table_privilege(current_user,'rag_source_ingestion_artifact','INSERT,UPDATE,DELETE,TRUNCATE') "
            "OR has_table_privilege(current_user,'rag_source_ingestion_run','INSERT,UPDATE,DELETE,TRUNCATE') "
            "OR has_table_privilege(current_user,'rag_source_snapshot_member','INSERT,UPDATE,DELETE,TRUNCATE') "
            "FROM pg_roles r WHERE r.rolname=current_user"
        )
    )
    readable = await session.scalar(
        text(
            "SELECT has_table_privilege(current_user,'rag_source_ingestion_artifact','SELECT') "
            "AND has_table_privilege(current_user,'rag_source_ingestion_run','SELECT') "
            "AND has_table_privilege(current_user,'rag_source_snapshot_member','SELECT')"
        )
    )
    if unsafe_role is not False or readable is not True:
        raise ValueError("Cleanup executor requires a read-only, non-owner Source DB role")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_id", type=UUID)
    args = parser.parse_args()
    try:
        receipt = asyncio.run(run_cleanup(CleanupExecutorConfig.from_environment(os.environ), args.request_id))
    except Exception:
        print("Source artifact cleanup failed closed; inspect the private receipt journal.", file=sys.stderr)
        return 1
    outcomes = ",".join(sorted({item.result.value for item in receipt.items}))
    print(f"Source artifact cleanup recorded: request_id={receipt.request_id} outcomes={outcomes}")
    return 0 if all(item.result.value in {"DELETED", "NOT_FOUND"} for item in receipt.items) else 2


if __name__ == "__main__":
    raise SystemExit(main())
