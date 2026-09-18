"""MFDS 제품 허가 Endpoint 전체를 수집해 전용 Source Writer로 적재·commit·재조회합니다.

Endpoint Receipt는 `--receipt-path`로 반드시 지정한다. 저장소에 보관된 과거 Receipt를
기본값으로 쓰지 않으며, 특정 record/page 수를 코드에 고정하지 않는다.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.local_private_source_artifact_finalizer import (
    FinalizingLocalPrivateSourceArtifactStore,
    LocalPrivateSourceArtifactReader,
)
from ai_worker.adapters.local_private_source_cleanup import LocalPrivateCleanupRequestJournal
from ai_worker.adapters.sqlalchemy_orphan_artifact_references import lock_source_artifact_mutation
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.admin.mfds_label_writer import record_transaction_cleanup_request
from ai_worker.admin.source_writer import WriterConfig, validate_source_writer_session
from ai_worker.tasks.rag.source_ingestion.failure_runs import FailedIngestionRunResult
from ai_worker.tasks.rag.source_ingestion.mfds_product import (
    PRODUCT_IDENTITY,
    MfdsProductAcquisition,
    acquire_mfds_product,
    build_product_snapshot_metadata,
    ingest_and_persist_mfds_product,
    load_product_receipt,
    write_product_report,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotPersistenceResult,
    SnapshotProvenanceReceipt,
)

type ProductIngestionOutcome = SnapshotPersistenceResult | FailedIngestionRunResult

LOCAL_PRIVATE_STORAGE_BACKEND = "LOCAL_PRIVATE"
_SECRET_ENVIRONMENT_NAME = "RAG_MFDS_API_KEY"


@dataclass(frozen=True, slots=True)
class MfdsProductIngestionConfig:
    """Source Writer 자격과 read-only artifact 경계만 담는다. 최종 artifact root는 받지 않는다."""

    writer: WriterConfig = field(repr=False)
    artifact_reader_root: Path = field(repr=False)
    artifact_finalizer_command: Path = field(repr=False)
    cleanup_journal_root: Path = field(repr=False)
    spool_root: Path = field(repr=False)
    secret_value: str = field(repr=False)

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "MfdsProductIngestionConfig":
        writer = WriterConfig.from_environment(env)
        if env.get("SOURCE_ARTIFACT_STORAGE_BACKEND") != LOCAL_PRIVATE_STORAGE_BACKEND:
            raise ValueError("MFDS product ingestion requires LOCAL_PRIVATE artifact storage")
        if env.get("SOURCE_ARTIFACT_LOCAL_ROOT"):
            raise ValueError("MFDS product ingestion must not receive the writable final artifact root")
        reader_root = _require_absolute_path(env, "SOURCE_ARTIFACT_READER_ROOT", "read-only artifact root")
        finalizer_command = _require_absolute_path(env, "SOURCE_ARTIFACT_FINALIZER_COMMAND", "artifact finalizer")
        cleanup_root = _require_absolute_path(env, "SOURCE_CLEANUP_JOURNAL_ROOT", "cleanup journal root")
        spool_root = _require_absolute_path(env, "SOURCE_ACQUISITION_SPOOL_ROOT", "acquisition spool root")
        for name, candidate in (("cleanup journal", cleanup_root), ("acquisition spool", spool_root)):
            if _paths_overlap(candidate, reader_root):
                raise ValueError(f"MFDS product ingestion requires a separate {name} root")
        if _paths_overlap(cleanup_root, spool_root):
            raise ValueError("MFDS product ingestion requires separate cleanup and spool roots")
        secret_value = env.get(_SECRET_ENVIRONMENT_NAME, "")
        if not secret_value.strip():
            raise ValueError("MFDS product ingestion requires the provider service key")
        return cls(
            writer=writer,
            artifact_reader_root=reader_root,
            artifact_finalizer_command=finalizer_command,
            cleanup_journal_root=cleanup_root,
            spool_root=spool_root,
            secret_value=secret_value,
        )


def _require_absolute_path(env: Mapping[str, str], name: str, label: str) -> Path:
    value = env.get(name, "")
    path = Path(value)
    if not value.strip() or not path.is_absolute():
        raise ValueError(f"MFDS product ingestion requires an absolute {label}")
    return path


def _paths_overlap(left: Path, right: Path) -> bool:
    resolved_left = left.resolve(strict=False)
    resolved_right = right.resolve(strict=False)
    return (
        resolved_left == resolved_right
        or resolved_left.is_relative_to(resolved_right)
        or resolved_right.is_relative_to(resolved_left)
    )


@dataclass(frozen=True, slots=True)
class MfdsProductIngestionReceipt:
    persistence: ProductIngestionOutcome
    provenance: SnapshotProvenanceReceipt | None
    record_count: int
    report_path: Path = field(repr=False)

    @property
    def snapshot_id(self) -> UUID | None:
        return self.persistence.snapshot_id if isinstance(self.persistence, SnapshotPersistenceResult) else None

    @property
    def failure_code(self) -> str | None:
        return self.persistence.failure_code


class MfdsProductPostCommitVerificationError(RuntimeError):
    """DB commit 뒤 재조회 검증 실패를 rollback 실패와 구분합니다."""

    def __init__(self, persistence: ProductIngestionOutcome) -> None:
        self.persistence = persistence
        super().__init__("MFDS_PRODUCT_POST_COMMIT_VERIFICATION_FAILED")


class MfdsProductTransactionCleanupRequiredError(RuntimeError):
    """DB rollback 뒤 private cleanup 요청이 durable 기록됐음을 알립니다."""

    def __init__(self, request_id: UUID) -> None:
        self.request_id = request_id
        super().__init__("MFDS_PRODUCT_TRANSACTION_CLEANUP_REQUIRED")


async def run_ingestion(
    config: MfdsProductIngestionConfig,
    *,
    receipt_path: Path,
    repository_root: Path,
    client: httpx.AsyncClient | None = None,
) -> MfdsProductIngestionReceipt:
    """수집과 적재를 하나의 Writer transaction에서 수행한 뒤 commit 결과를 재조회합니다.

    Endpoint Receipt는 외부 호출 전에 먼저 검증한다. 증빙이 없으면 provider를 부르지 않는다.
    """
    receipt = load_product_receipt(receipt_path=receipt_path, repository_root=repository_root)
    run_group_key = f"mfds-product-{uuid4().hex[:16]}"
    artifact_reader = LocalPrivateSourceArtifactReader(config.artifact_reader_root)
    artifact_store = FinalizingLocalPrivateSourceArtifactStore(
        finalizer_command=config.artifact_finalizer_command,
        reader=artifact_reader,
    )
    cleanup_journal = LocalPrivateCleanupRequestJournal(config.cleanup_journal_root)
    engine = create_async_engine(config.writer.url, hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        attempted_run_id: UUID | None = None
        try:
            async with sessions.begin() as session:
                await validate_source_writer_session(session)
                await lock_source_artifact_mutation(session)
                repository = SqlAlchemySourceSnapshotRepository(session)
                acquisition = await _acquire(config, client=client, gate=repository)
                report_path = write_product_report(acquisition, receipt=receipt)
                metadata = build_product_snapshot_metadata(
                    acquisition=acquisition,
                    run_group_key=run_group_key,
                    verified_by=config.writer.actor,
                )
                persistence = await ingest_and_persist_mfds_product(
                    acquisition=acquisition,
                    repository=repository,
                    artifact_store=artifact_store,
                    metadata=metadata,
                    receipt_path=receipt_path,
                    repository_root=repository_root,
                )
                attempted_run_id = persistence.ingestion_run_id
                record_count = acquisition.result.record_count
        except Exception:
            cleanup_request_id = record_transaction_cleanup_request(
                journal=cleanup_journal,
                run_group_key=run_group_key,
                ingestion_run_id=attempted_run_id,
                stored_artifacts=artifact_store.finalized,
                requested_at=datetime.now(UTC),
            )
            if cleanup_request_id is not None:
                raise MfdsProductTransactionCleanupRequiredError(cleanup_request_id) from None
            raise
        try:
            async with sessions() as session:
                provenance = await _requery(session, persistence)
        except Exception:
            raise MfdsProductPostCommitVerificationError(persistence) from None
        return MfdsProductIngestionReceipt(persistence, provenance, record_count, report_path)
    finally:
        await engine.dispose()


async def _acquire(
    config: MfdsProductIngestionConfig,
    *,
    client: httpx.AsyncClient | None,
    gate: SqlAlchemySourceSnapshotRepository,
) -> MfdsProductAcquisition:
    if client is not None:
        return await acquire_mfds_product(
            gate=gate,
            client=client,
            secret_value=config.secret_value,
            spool_parent=config.spool_root,
        )
    async with httpx.AsyncClient() as owned:
        return await acquire_mfds_product(
            gate=gate,
            client=owned,
            secret_value=config.secret_value,
            spool_parent=config.spool_root,
        )


async def _requery(session: AsyncSession, persistence: ProductIngestionOutcome) -> SnapshotProvenanceReceipt | None:
    """commit된 상태만 다시 읽어 provenance를 확인한다. 여기서 CURRENT를 바꾸지 않는다."""
    await validate_source_writer_session(session)
    repository = SqlAlchemySourceSnapshotRepository(session)
    snapshot_id = persistence.snapshot_id if isinstance(persistence, SnapshotPersistenceResult) else None
    if snapshot_id is None:
        # 실패 Run도 commit된 증적이 남아야 한다. Snapshot이 없다고 검증을 건너뛰지 않는다.
        if await repository.get_attempt_receipt(ingestion_run_id=persistence.ingestion_run_id) is None:
            raise ValueError("Attempt receipt is missing after commit")
        return None
    provenance = await repository.get_snapshot_receipt(snapshot_id=snapshot_id)
    if provenance is None:
        raise ValueError("Snapshot provenance is missing after commit")
    provenance.validate_provenance()
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire and persist the full MFDS product approval endpoint")
    parser.add_argument("--receipt-path", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            run_ingestion(
                MfdsProductIngestionConfig.from_environment(os.environ),
                receipt_path=args.receipt_path,
                repository_root=args.repository_root,
            )
        )
    except MfdsProductPostCommitVerificationError as exc:
        print(
            "MFDS product ingestion committed but post-commit verification failed; "
            f"ingestion_run_id={exc.persistence.ingestion_run_id} "
            f"failure_code={exc.persistence.failure_code}. "
            "Do not rerun until the committed state is investigated.",
            file=sys.stderr,
        )
        return 1
    except MfdsProductTransactionCleanupRequiredError as exc:
        print(
            "MFDS product ingestion rolled back after preserving private artifacts; "
            f"cleanup_request_id={exc.request_id}.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        # Provider·DB 오류 원문에는 service key나 연결 정보가 포함될 수 있습니다.
        print(
            "MFDS product ingestion did not confirm a database commit; an opened transaction was rolled back. "
            "Immutable artifact objects may require the approved cleanup procedure.",
            file=sys.stderr,
        )
        return 1
    if result.snapshot_id is None:
        print(
            "MFDS product ingestion committed a failed run without a Snapshot: "
            f"operation={PRODUCT_IDENTITY.operation_code} "
            f"ingestion_run_id={result.persistence.ingestion_run_id} "
            f"failure_code={result.failure_code} "
            f"record_count={result.record_count} "
            f"report={result.report_path}",
            file=sys.stderr,
        )
        return 1
    print(
        "MFDS product ingestion committed: "
        f"operation={PRODUCT_IDENTITY.operation_code} "
        f"snapshot_id={result.snapshot_id} "
        f"canonical_checksum={result.provenance.canonical_checksum if result.provenance else None} "
        f"record_count={result.record_count} "
        f"report={result.report_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
