import argparse
import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker, create_async_engine

from ai_worker.adapters.local_private_source_artifact_finalizer import LocalPrivateSourceArtifactReader
from ai_worker.adapters.sqlalchemy_catalog_approval_verifier import SqlAlchemyCatalogApprovalVerifier
from ai_worker.adapters.sqlalchemy_catalog_write_support import SqlAlchemyCatalogBuildRepository
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.tasks.rag.catalog.mfds_product_source import ProductSourceBindingError, read_product_input
from ai_worker.tasks.rag.catalog.service import CatalogBuildRequest, CatalogBuildResult, build_catalog_candidate
from ai_worker.tasks.rag.catalog.types import CandidateCatalogSourceRef, CatalogVerificationStatus
from infra.python.catalog_role_policy import CATALOG_LOCK_COLUMNS, CATALOG_READ_TABLES, CATALOG_WRITE_TABLES


def writer_url(environment: Mapping[str, str]) -> URL:
    if any(
        environment.get(key)
        for key in (
            "DB_PASSWORD",
            "DB_APP_PASSWORD",
            "DB_ADMIN_PASSWORD",
            "DB_MIGRATION_PASSWORD",
            "SOURCE_WRITER_PASSWORD",
            "SOURCE_MANAGEMENT_PASSWORD",
            "KNOWLEDGE_INDEX_BUILDER_PASSWORD",
            "CANDIDATE_INDEX_BUILDER_PASSWORD",
        )
    ):
        raise ValueError("Catalog Writer requires an isolated credential environment")
    values = {key: environment.get(f"CATALOG_WRITER_{key}", "") for key in ("HOST", "PORT", "NAME", "USER", "PASSWORD")}
    if any(not value.strip() for value in values.values()):
        raise ValueError("Catalog Writer configuration is incomplete")
    port = int(values["PORT"])
    if not 1 <= port <= 65535:
        raise ValueError("Catalog Writer port is invalid")
    return URL.create(
        "postgresql+asyncpg",
        username=values["USER"],
        password=values["PASSWORD"],
        host=values["HOST"],
        port=port,
        database=values["NAME"],
    )


async def validate_catalog_writer(connection: AsyncConnection) -> None:
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
        raise ValueError("Catalog Writer requires a non-owner login without administrative privileges or memberships")
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
    expected = {(name, "SELECT") for name in CATALOG_READ_TABLES} | {(name, "INSERT") for name in CATALOG_WRITE_TABLES}
    if set(rows) != expected:
        raise ValueError("Catalog Writer table privileges do not match the allowlist")
    # Include effective column grants so PUBLIC and independently granted privileges cannot bypass table REVOKE.
    columns = (
        await connection.execute(
            text(
                "SELECT c.relname,a.attname,p.privilege FROM pg_attribute a "
                "JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE'),('REFERENCES')) p(privilege) "
                "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped "
                "AND has_column_privilege(current_user,c.oid,a.attnum,p.privilege)"
            )
        )
    ).all()
    for table, column, privilege in columns:
        if (table, privilege) in expected or (privilege == "UPDATE" and CATALOG_LOCK_COLUMNS.get(table) == column):
            continue
        raise ValueError("Catalog Writer column privileges do not match the allowlist")
    if not {(table, column, "UPDATE") for table, column in CATALOG_LOCK_COLUMNS.items()} <= set(columns):
        raise ValueError("Catalog Writer row-lock privileges are missing")


@asynccontextmanager
async def catalog_writer_repository(environment: Mapping[str, str]) -> AsyncIterator[SqlAlchemyCatalogBuildRepository]:
    engine = create_async_engine(writer_url(environment), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await validate_catalog_writer(connection)
        yield SqlAlchemyCatalogBuildRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()


def summarize_catalog_execution(result: CatalogBuildResult) -> dict[str, object]:
    """Report the approval verifier's own outcome without reinterpreting it.

    An absent or non-APPROVED export is the canonical fail-closed result of the
    existing approval gate, not a Catalog Writer defect.
    """
    export = result.export
    if export is None or export.catalog.verification_status is not CatalogVerificationStatus.APPROVED:
        return {"execution_status": "BLOCKED", "blocker_reason": "CATALOG_NOT_APPROVED"}
    return {"execution_status": result.decision.value}


async def _execute_catalog_build(
    *,
    environment: Mapping[str, str],
    source_snapshot_id: str,
    ingestion_run_id: str,
    item_seq: str,
    catalog_version: str,
) -> dict[str, object]:
    root_value = environment.get("CATALOG_SOURCE_ARTIFACT_READER_ROOT", "").strip()
    if not root_value:
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_ARTIFACT")
    try:
        reader = LocalPrivateSourceArtifactReader(Path(root_value))
    except (OSError, ValueError):
        raise ProductSourceBindingError("BLOCKED_BY_PRODUCT_ARTIFACT") from None

    engine = create_async_engine(writer_url(environment), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await validate_catalog_writer(connection)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as source_session:
            product, receipt = await read_product_input(
                repository=SqlAlchemySourceSnapshotRepository(source_session),
                artifact_reader=reader,
                source_snapshot_id=source_snapshot_id,
                ingestion_run_id=ingestion_run_id,
                item_seq=item_seq,
            )
        request = CatalogBuildRequest(
            catalog_version=catalog_version,
            source_refs=(CandidateCatalogSourceRef(str(receipt.source_snapshot_id), receipt.source_version),),
            products=(product,),
            ingredients=(),
            components=(),
            aliases=(),
        )
        repository = SqlAlchemyCatalogBuildRepository(sessions)
        result = await build_catalog_candidate(
            request=request,
            repository=repository,
            approval_verifier=SqlAlchemyCatalogApprovalVerifier(sessions),
        )
        return summarize_catalog_execution(result)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and persist Medication Catalog with dedicated Catalog Writer credentials"
    )
    parser.add_argument(
        "--source-snapshot-id",
        type=str,
        default=None,
        help="Authoritative Product Source snapshot ID",
    )
    parser.add_argument("--ingestion-run-id", type=str, default=None)
    parser.add_argument("--item-seq", type=str, default=None)
    parser.add_argument("--catalog-version", type=str, default=None)
    args = parser.parse_args(argv)

    try:
        writer_url(os.environ)
    except Exception as exc:
        print(json.dumps({"execution_status": "FAILED", "error": str(exc)}), file=sys.stderr)
        return 1

    if not all((args.source_snapshot_id, args.ingestion_run_id, args.item_seq, args.catalog_version)):
        print(json.dumps({"execution_status": "BLOCKED", "blocker_reason": "BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY"}))
        return 1
    try:
        result = asyncio.run(
            _execute_catalog_build(
                environment=os.environ,
                source_snapshot_id=args.source_snapshot_id,
                ingestion_run_id=args.ingestion_run_id,
                item_seq=args.item_seq,
                catalog_version=args.catalog_version,
            )
        )
    except ProductSourceBindingError as exc:
        print(json.dumps({"execution_status": "BLOCKED", "blocker_reason": exc.code}))
        return 1
    except Exception:
        print(json.dumps({"execution_status": "FAILED", "error": "Catalog Writer execution failed"}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0 if result["execution_status"] == "ACTIVATION_CANDIDATE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
