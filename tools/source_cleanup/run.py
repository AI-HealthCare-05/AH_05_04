"""Explicit manual #347 Local synthetic workflow. No default database or existing-root adoption."""

import argparse
import asyncio
import json
import os
import re
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from ai_worker.adapters.postgresql_source_cleanup import (
    PostgresApprovalVerifier,
    PostgresAuditJournal,
    PostgresLocalCleanupGuard,
    create_synthetic_workspace,
    load_batch,
    record_review,
    require_synthetic_database,
    revoke_review_batch,
    survey_workspace,
)
from ai_worker.tasks.rag.source_cleanup.execution import execute_synthetic_batch


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("command", choices=("install", "create", "survey", "review", "revoke", "execute", "audit"))
    result.add_argument("--root", type=Path)
    result.add_argument("--workspace")
    result.add_argument("--batch-hash", help="Exact digest from the reviewed survey")
    result.add_argument("--review-role", choices=("PM", "DB_SECURITY"))
    result.add_argument("--executor")
    result.add_argument("--pm-role")
    result.add_argument("--db-security-role")
    result.add_argument("--retry", action="store_true")
    result.add_argument("--max-attempts", type=int, default=1)
    result.add_argument(
        "--simulate-elapsed-days",
        type=int,
        choices=(31,),
        help="Synthetic time simulation only. Does not alter receipt creation timestamps.",
    )
    return result


async def run(args: argparse.Namespace) -> int:
    url = os.environ.get("SOURCE_CLEANUP_TEST_DATABASE_URL")
    if not url:
        raise ValueError("Explicit synthetic database URL required")
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await require_synthetic_database(connection)
        if args.command == "install":
            async with engine.begin() as connection:
                raw = await connection.get_raw_connection()
                if raw.driver_connection is None:
                    raise ValueError("Database driver unavailable")
                await raw.driver_connection.execute(Path(__file__).with_name("synthetic_control.sql").read_text())
            print(json.dumps({"status": "SYNTHETIC_CONTROL_INSTALLED"}))
            return 0
        if args.command == "create":
            if args.root is None:
                raise ValueError("New absolute synthetic root required")
            workspace = await create_synthetic_workspace(
                engine, args.root, evaluation_offset_days=args.simulate_elapsed_days or 0
            )
            print(json.dumps({"workspace": workspace}))
            return 0
        return await handle_batch(engine, args)
    finally:
        await engine.dispose()


def require_reviewed_digest(args: argparse.Namespace, digest: str) -> None:
    if args.command in {"review", "execute"} and args.batch_hash != digest:
        raise ValueError("Reviewed batch digest required")


async def require_workspace_clock(engine: AsyncEngine, args: argparse.Namespace) -> None:
    if args.command in {"review", "execute", "survey"}:
        async with engine.connect() as connection:
            offset = await connection.scalar(
                text("SELECT evaluation_offset_days FROM source_cleanup.workspace WHERE id=:id"),
                {"id": args.workspace},
            )
        if offset != (args.simulate_elapsed_days or 0):
            raise ValueError("Synthetic clock differs from registered workspace")


async def handle_batch(engine: AsyncEngine, args: argparse.Namespace) -> int:
    if not args.workspace:
        raise ValueError("Workspace required")
    batch = await load_batch(engine, args.workspace)
    require_reviewed_digest(args, batch.digest())
    await require_workspace_clock(engine, args)
    started_at = datetime.now(UTC)
    now = started_at
    if args.simulate_elapsed_days:
        now += timedelta(days=args.simulate_elapsed_days)
    if args.command == "survey":
        report = await survey_workspace(engine, batch, now=now)
        print(json.dumps({"batch_hash": batch.digest(), "synthetic_time": now.isoformat(), **asdict(report)}))
        return 0 if report.complete else 2
    if args.command == "review":
        if not args.review_role or not args.executor:
            raise ValueError("Review role and executor required")
        await record_review(
            engine,
            batch_hash=batch.digest(),
            role=args.review_role,
            executor=args.executor,
            policy_version=batch.scope.policy_version,
            valid_from=now - timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
        )
        print(json.dumps({"batch_hash": batch.digest(), "status": "REVIEW_RECORDED"}))
        return 0
    if args.command == "revoke":
        if not args.batch_hash or re.fullmatch(r"[0-9a-f]{64}", args.batch_hash) is None:
            raise ValueError("Exact revocation digest required")
        await revoke_review_batch(engine, batch_hash=args.batch_hash)
        print(json.dumps({"status": "REVOKED"}))
        return 0
    if args.command == "audit":
        async with engine.connect() as connection:
            payloads = (
                await connection.scalars(
                    text("SELECT payload FROM source_cleanup.audit WHERE batch_hash=:hash ORDER BY sequence"),
                    {"hash": batch.digest()},
                )
            ).all()
        print(json.dumps(payloads))
        return 0
    if not args.executor or not args.pm_role or not args.db_security_role:
        raise ValueError("Trusted reviewer roles and executor required")
    outcome = await execute_synthetic_batch(
        batch=batch,
        executor=args.executor,
        clock=lambda: now + (datetime.now(UTC) - started_at),
        approvals=PostgresApprovalVerifier(engine, pm_role=args.pm_role, db_security_role=args.db_security_role),
        guard=PostgresLocalCleanupGuard(engine, PostgresAuditJournal(engine)),
        retry=args.retry,
        max_attempts=args.max_attempts,
    )
    print(json.dumps(asdict(outcome)))
    return 0 if outcome.complete else 2


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(run(parser().parse_args())))
    except Exception:
        # Never emit a DB URL, SQL parameters, raw object key or provider exception.
        print(json.dumps({"status": "BLOCKED", "reason": "SYNTHETIC_WORKFLOW_UNAVAILABLE"}))
        raise SystemExit(2) from None
