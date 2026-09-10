"""전용 로그인으로 실행하는 일회성 Source Snapshot 선택 명령입니다."""

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotSelectionDecision,
    SnapshotSelectionResult,
    select_current_snapshot,
)


@dataclass(frozen=True)
class WriterConfig:
    url: URL = field(repr=False)
    actor: str

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "WriterConfig":
        # Runtime 또는 migration secret이 함께 주입된 프로세스는 실행하지 않습니다.
        if any(
            env.get(key) for key in ("DB_PASSWORD", "DB_APP_PASSWORD", "DB_MIGRATION_PASSWORD", "DB_ADMIN_PASSWORD")
        ):
            raise ValueError("Source Writer requires an isolated credential environment")
        required = ("HOST", "PORT", "NAME", "USER", "PASSWORD", "ACTOR")
        values = {key: env.get(f"SOURCE_WRITER_{key}", "") for key in required}
        if any(not value.strip() for value in values.values()):
            raise ValueError("Source Writer configuration is incomplete")
        if len(values["ACTOR"]) > 100:
            raise ValueError("Source Writer actor is too long")
        port = int(values["PORT"])
        if not 1 <= port <= 65535:
            raise ValueError("Source Writer port is invalid")
        return cls(
            URL.create(
                "postgresql+asyncpg",
                username=values["USER"],
                password=values["PASSWORD"],
                host=values["HOST"],
                port=port,
                database=values["NAME"],
            ),
            values["ACTOR"],
        )


async def select_snapshot(
    session: AsyncSession, *, snapshot_id: UUID, expected_checksum: str, actor: str, reason_code: str
) -> SnapshotSelectionResult:
    """호출자 transaction 안에서 대상 확인·교체·요청 감사를 함께 기록합니다."""
    if re.fullmatch(r"[a-f0-9]{64}", expected_checksum) is None:
        raise ValueError("Expected checksum must be a SHA-256 hex digest")
    if re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", reason_code) is None:
        raise ValueError("A bounded reason code is required")
    repository = SqlAlchemySourceSnapshotRepository(session)
    await repository.lock_snapshot_operation(snapshot_id=snapshot_id)
    checksum = await session.scalar(
        text("SELECT canonical_checksum FROM rag_source_snapshot WHERE id=:id FOR UPDATE"),
        {"id": str(snapshot_id)},
    )
    if checksum != expected_checksum:
        raise ValueError("Snapshot checksum changed or target is missing")
    selected_at = datetime.now(UTC)
    result = await select_current_snapshot(
        repository=repository, snapshot_id=snapshot_id, selected_at=selected_at, selected_by=actor
    )
    if result.decision is not SnapshotSelectionDecision.ALREADY_CURRENT:
        await repository.append_verification(
            snapshot_id=snapshot_id,
            check_name="snapshot-selection-request",
            result="PASSED",
            verified_at=selected_at,
            verified_by=actor,
            details_summary=reason_code,
        )
    return result


async def run_selection(config: WriterConfig, args: argparse.Namespace) -> SnapshotSelectionResult:
    engine = create_async_engine(config.url, hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            unsafe_role = await session.scalar(
                text(
                    "SELECT r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls OR r.rolreplication "
                    "OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid) "
                    "OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=r.oid) "
                    "OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspname=current_schema() AND nspowner=r.oid) "
                    "OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=current_schema() AND c.relowner=r.oid) "
                    "FROM pg_roles r WHERE r.rolname=current_user"
                )
            )
            if unsafe_role is not False:
                raise ValueError(
                    "Source Writer requires a non-owner role without administrative privileges or memberships"
                )
            return await select_snapshot(
                session,
                snapshot_id=args.snapshot_id,
                expected_checksum=args.expected_checksum,
                actor=config.actor,
                reason_code=args.reason_code,
            )
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Select a verified Source snapshot with dedicated Writer credentials")
    parser.add_argument("snapshot_id", type=UUID)
    parser.add_argument("--expected-checksum", required=True)
    parser.add_argument("--reason-code", required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(run_selection(WriterConfig.from_environment(os.environ), args))
    except Exception:
        # DB 오류 원문에는 SQL parameter나 연결 정보가 포함될 수 있습니다.
        print("Source Writer selection failed; transaction rolled back.", file=sys.stderr)
        return 1
    print(f"Source Writer selection committed: {result.decision.value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
