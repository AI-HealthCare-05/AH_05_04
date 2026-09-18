#!/usr/bin/env python3
"""Isolated Candidate Index Builder orchestration script.

Executes under dedicated CANDIDATE_INDEX_BUILDER credentials with least privilege.
Runs inside the app image as a one-shot operator command; joins Backend repository
and service models with ai_worker catalog approval verification and export support
without widening the backend production package import boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.sqlalchemy_catalog_approval_verifier import SqlAlchemyCatalogApprovalVerifier
from ai_worker.adapters.sqlalchemy_catalog_write_support import SqlAlchemyCatalogBuildRepository
from ai_worker.tasks.rag.candidate_index import (
    CandidateEmbeddingPort,
    CandidateIndexBuildConfig,
    CandidateIndexBuildFailure,
)
from app.models.rag_candidate_index import RagCandidateIndexStatus
from app.repositories.rag_candidate_index_repository import RagCandidateIndexRepository
from app.services.rag_candidate_index_build import execute_candidate_index_build
from infra.python.candidate_index_role_policy import (
    CANDIDATE_INDEX_CATALOG_READ_TABLES,
    CANDIDATE_INDEX_WRITE_TABLES,
)


def builder_url(environment: Mapping[str, str]) -> URL:
    """Derive connection URL and assert strict credential isolation."""
    if any(
        environment.get(key)
        for key in (
            "DB_PASSWORD",
            "DB_APP_PASSWORD",
            "DB_ADMIN_PASSWORD",
            "DB_MIGRATION_PASSWORD",
            "SOURCE_WRITER_PASSWORD",
            "SOURCE_MANAGEMENT_PASSWORD",
            "CATALOG_WRITER_PASSWORD",
            "KNOWLEDGE_INDEX_BUILDER_PASSWORD",
        )
    ):
        raise ValueError("Candidate Index Builder requires an isolated credential environment")
    values = {
        key: environment.get(f"CANDIDATE_INDEX_BUILDER_{key}", "")
        for key in ("HOST", "PORT", "NAME", "USER", "PASSWORD")
    }
    if any(not value.strip() for value in values.values()):
        raise ValueError("Candidate Index Builder configuration is incomplete")
    port = int(values["PORT"])
    if not 1 <= port <= 65535:
        raise ValueError("Candidate Index Builder port is invalid")
    return URL.create(
        "postgresql+asyncpg",
        username=values["USER"],
        password=values["PASSWORD"],
        host=values["HOST"],
        port=port,
        database=values["NAME"],
    )


async def validate_candidate_index_builder(connection: AsyncConnection, *, expected_user: str | None = None) -> None:
    """Validate that the active connection conforms to the least-privilege Candidate Builder policy."""
    safe = await connection.scalar(
        text(
            "SELECT NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls OR r.rolreplication "
            "OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid) "
            "OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=r.oid) "
            "OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='public' AND nspowner=r.oid) "
            "OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname='public' AND c.relowner=r.oid) "
            "OR has_schema_privilege(current_user, 'public', 'CREATE')) "
            "AND current_user=session_user AND current_schema()='public' FROM pg_roles r WHERE rolname=current_user"
        )
    )
    if safe is not True:
        raise ValueError(
            "Candidate Index Builder requires a non-owner login without administrative privileges or memberships"
        )
    if expected_user:
        current = await connection.scalar(text("SELECT current_user"))
        if current != expected_user:
            raise ValueError(f"Expected current_user={expected_user}, observed {current}")

    rows = (
        await connection.execute(
            text(
                "SELECT c.relname, p.privilege FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('DELETE'),('TRUNCATE'),('REFERENCES'),('TRIGGER')) p(privilege) "
                "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') "
                "AND has_table_privilege(current_user,c.oid,p.privilege)"
            )
        )
    ).all()
    expected = (
        {(name, "SELECT") for name in CANDIDATE_INDEX_CATALOG_READ_TABLES}
        | {(name, "SELECT") for name in CANDIDATE_INDEX_WRITE_TABLES}
        | {(name, "INSERT") for name in CANDIDATE_INDEX_WRITE_TABLES}
    )
    if set(rows) != expected:
        raise ValueError("Candidate Index Builder table privileges do not match the allowlist")

    # Column privileges: UPDATE allowed ONLY on rag_candidate_index_version.status
    has_status_update = await connection.scalar(
        text("SELECT has_column_privilege(current_user, 'rag_candidate_index_version', 'status', 'UPDATE')")
    )
    if has_status_update is not True:
        raise ValueError("Candidate Index Builder lacks UPDATE privilege on rag_candidate_index_version.status")

    # Prohibited columns must NOT have UPDATE privilege
    for col in (
        "content_hash",
        "configuration_hash",
        "member_count",
        "index_code",
        "index_version",
        "candidate_index_lock_marker",
    ):
        has_col_update = await connection.scalar(
            text("SELECT has_column_privilege(current_user, 'rag_candidate_index_version', :col, 'UPDATE')"),
            {"col": col},
        )
        if has_col_update:
            raise ValueError(f"Candidate Index Builder must not have UPDATE privilege on prohibited column '{col}'")


@asynccontextmanager
async def candidate_index_builder_session_factory(
    environment: Mapping[str, str],
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Context manager yielding a validated AsyncSession factory for the Candidate Builder."""
    url = builder_url(environment)
    engine = create_async_engine(url, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await validate_candidate_index_builder(connection)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def execute_candidate_index_builder_command(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    catalog_set_id: UUID | str,
    config: CandidateIndexBuildConfig | None = None,
    embedding_port: CandidateEmbeddingPort | None = None,
) -> dict[str, Any]:
    """Execute Candidate Index build and activation with single-transaction integrity and replay handling."""
    set_uuid = UUID(str(catalog_set_id))

    # 1. Verify Catalog and approvals exist
    async with session_factory() as session:
        verifier = SqlAlchemyCatalogApprovalVerifier(session_factory)
        catalog_repo = SqlAlchemyCatalogBuildRepository(session_factory)
        catalog_build = await catalog_repo.load_build(set_uuid, approval_verifier=verifier)
        if catalog_build is None:
            raise ValueError(f"Catalog Set {set_uuid} could not be loaded or approval verification failed")

    # 2. Check Candidate Config Authority
    if config is None:
        return {
            "execution_status": "BLOCKED",
            "blocker_reason": "BLOCKED_BY_CANDIDATE_CONFIG_AUTHORITY",
        }

    # 3. Single-transaction build + promotion
    async with session_factory() as session:
        async with session.begin():
            execution = await execute_candidate_index_build(
                session,
                artifacts=catalog_build,
                config=config,
                catalog_set_id=set_uuid,
                embedding_port=embedding_port,
            )
            if isinstance(execution.outcome, CandidateIndexBuildFailure):
                raise RuntimeError(
                    f"Candidate Index build failed: {execution.outcome.reason} - {execution.outcome.details}"
                )
            if execution.persisted is None:
                raise RuntimeError("Candidate Index build produced no persisted version")

            version = execution.persisted.version
            repo = RagCandidateIndexRepository(session)
            reused_existing = False

            if version.status is RagCandidateIndexStatus.READY:
                # Same build was already READY; do not re-activate
                reused_existing = True
            elif version.status is RagCandidateIndexStatus.BUILDING:
                activated = await repo.activate_ready_version(version.id)
                version = activated
            elif version.status in (RagCandidateIndexStatus.RETIRED, RagCandidateIndexStatus.FAILED):
                raise ValueError(f"Existing version {version.id} has non-promotable terminal status {version.status}")

    # 4. Post-commit integrity readback in a fresh session
    async with session_factory() as verify_session:
        verify_repo = RagCandidateIndexRepository(verify_session)
        verified = await verify_repo.get_verified_ready_index_snapshot(
            index_code=version.index_code,
            expected_index_version=version.index_version,
        )

    return {
        "execution_status": "SUCCESS",
        "catalog_set_id": str(set_uuid),
        "candidate_index_version_id": str(verified.version.id),
        "index_code": verified.version.index_code,
        "index_version": verified.version.index_version,
        "status": verified.version.status.value,
        "member_count": verified.version.member_count,
        "reused_existing": reused_existing,
        "verification_passed": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and activate Candidate Index with dedicated Candidate Builder credentials"
    )
    parser.add_argument(
        "--catalog-set-id",
        type=str,
        default=None,
        help="Authoritative Catalog Set ID",
    )
    _ = parser.parse_args(argv)

    try:
        builder_url(os.environ)
    except Exception as exc:
        print(json.dumps({"execution_status": "FAILED", "error": str(exc)}), file=sys.stderr)
        return 1

    # In Phase A, actual Candidate configuration authority is absent / pending.
    # Without authoritative config, fail closed with BLOCKED_BY_CANDIDATE_CONFIG_AUTHORITY.
    print(
        json.dumps(
            {
                "execution_status": "BLOCKED",
                "blocker_reason": "BLOCKED_BY_CANDIDATE_CONFIG_AUTHORITY",
            }
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
