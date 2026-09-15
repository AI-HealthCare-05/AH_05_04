"""승인된 private XML을 전용 Source Writer로 적재·commit·재조회합니다."""

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.adapters.local_private_source_cleanup import LocalPrivateCleanupRequestJournal
from ai_worker.adapters.sqlalchemy_orphan_artifact_references import lock_source_artifact_mutation
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.admin.source_writer import WriterConfig, validate_source_writer_session
from ai_worker.tasks.rag.source_cleanup.orphan_artifact import CleanupRequest, CleanupTarget
from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    StoredRawArtifact,
)
from ai_worker.tasks.rag.source_ingestion.mfds_label import (
    LOCAL_PRIVATE_STORAGE_BACKEND,
    NORMALIZATION_VERSION,
    PARSER_VERSION,
    SCHEMA_VERSION,
    MfdsLabelPersistenceReceipt,
    MfdsLabelRequeryReceipt,
    load_mfds_label_plan,
    persist_mfds_label_plan,
    requery_mfds_label_persistence,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotIngestionMetadata

_CODE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{0,99}\Z")
_CHECKSUM_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class MfdsLabelWriterConfig:
    writer: WriterConfig = field(repr=False)
    artifact_root: Path = field(repr=False)
    cleanup_journal_root: Path = field(repr=False)
    identity: SourceOperationIdentity
    endpoint_receipt_hash: str = field(repr=False)

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "MfdsLabelWriterConfig":
        writer = WriterConfig.from_environment(env)
        if env.get("SOURCE_ARTIFACT_STORAGE_BACKEND") != LOCAL_PRIVATE_STORAGE_BACKEND:
            raise ValueError("MFDS label ingestion requires LOCAL_PRIVATE artifact storage")
        root_value = env.get("SOURCE_ARTIFACT_LOCAL_ROOT", "")
        root = Path(root_value)
        if not root_value.strip() or not root.is_absolute():
            raise ValueError("MFDS label ingestion requires an absolute artifact root")
        cleanup_root_value = env.get("SOURCE_CLEANUP_JOURNAL_ROOT", "")
        cleanup_root = Path(cleanup_root_value)
        if not cleanup_root_value.strip() or not cleanup_root.is_absolute() or cleanup_root == root:
            raise ValueError("MFDS label ingestion requires a separate absolute cleanup journal root")
        values = {
            name: env.get(name, "")
            for name in ("MFDS_LABEL_SOURCE_CODE", "MFDS_LABEL_ENDPOINT_CODE", "MFDS_LABEL_OPERATION_CODE")
        }
        if any(_CODE_PATTERN.fullmatch(value) is None for value in values.values()):
            raise ValueError("MFDS label Source identity is incomplete or invalid")
        receipt_hash = env.get("MFDS_LABEL_ENDPOINT_RECEIPT_HASH", "")
        if _CHECKSUM_PATTERN.fullmatch(receipt_hash) is None:
            raise ValueError("MFDS label Endpoint Receipt hash is invalid")
        return cls(
            writer=writer,
            artifact_root=root,
            cleanup_journal_root=cleanup_root,
            identity=SourceOperationIdentity(
                values["MFDS_LABEL_SOURCE_CODE"],
                values["MFDS_LABEL_ENDPOINT_CODE"],
                values["MFDS_LABEL_OPERATION_CODE"],
            ),
            endpoint_receipt_hash=receipt_hash,
        )


@dataclass(frozen=True, slots=True)
class MfdsLabelCommittedReceipt:
    persistence: MfdsLabelPersistenceReceipt
    requery: MfdsLabelRequeryReceipt


class MfdsLabelPostCommitVerificationError(RuntimeError):
    """DB commit 뒤 재조회 검증 실패를 rollback 실패와 구분합니다."""

    def __init__(self, persistence: MfdsLabelPersistenceReceipt) -> None:
        self.persistence = persistence
        super().__init__("MFDS_LABEL_POST_COMMIT_VERIFICATION_FAILED")


class MfdsLabelTransactionCleanupRequiredError(RuntimeError):
    """DB rollback 뒤 private cleanup 요청이 durable 기록됐음을 알립니다."""

    def __init__(self, request_id: UUID) -> None:
        self.request_id = request_id
        super().__init__("MFDS_LABEL_TRANSACTION_CLEANUP_REQUIRED")


class _CleanupTrackingArtifactStore:
    """현재 시도에서 실제로 보존 확인된 객체만 cleanup 후보로 기억합니다."""

    def __init__(self, store: LocalPrivateSourceArtifactStore) -> None:
        self._store = store
        self.stored: list[StoredRawArtifact] = []

    def put_verified(
        self,
        *,
        page_number: int | None,
        file_path: Path,
        metadata: RawArtifactMetadata,
        artifact_kind: IngestionArtifactKind = IngestionArtifactKind.RAW_RESPONSE,
        reject_code: str | None = None,
        parser_location: str | None = None,
    ) -> StoredRawArtifact:
        stored = self._store.put_verified(
            page_number=page_number,
            file_path=file_path,
            metadata=metadata,
            artifact_kind=artifact_kind,
            reject_code=reject_code,
            parser_location=parser_location,
        )
        self.stored.append(stored)
        return stored


def record_transaction_cleanup_request(
    *,
    journal: LocalPrivateCleanupRequestJournal,
    run_group_key: str,
    ingestion_run_id: UUID | None,
    stored_artifacts: list[StoredRawArtifact],
    requested_at: datetime,
) -> UUID | None:
    """rollback으로 Run ID가 사라져도 durable run group과 실제 객체를 기록합니다."""
    unique_artifacts = {stored.object_key: stored for stored in stored_artifacts}
    if not unique_artifacts:
        return None
    request_id = uuid4()
    journal.append_request(
        CleanupRequest(
            request_id=request_id,
            run_group_key=run_group_key,
            ingestion_run_id=ingestion_run_id,
            targets=tuple(
                CleanupTarget(
                    artifact_key=stored.metadata.artifact_key,
                    object_key=stored.object_key,
                    checksum=stored.metadata.raw_checksum,
                )
                for stored in unique_artifacts.values()
            ),
            failure_reason="MFDS_LABEL_TRANSACTION_FAILED",
            requested_at=requested_at,
        )
    )
    return request_id


def parse_collected_at(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError("collected-at must be an RFC3339 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("collected-at must include a timezone")
    return parsed


async def run_ingestion(
    config: MfdsLabelWriterConfig,
    *,
    item_seq: str,
    input_dir: Path,
    collected_at: datetime,
    include_e_drug: bool,
) -> MfdsLabelCommittedReceipt:
    started_at = datetime.now(UTC)
    plan = load_mfds_label_plan(
        item_seq=item_seq,
        input_dir=input_dir,
        identity=config.identity,
        endpoint_receipt_hash=config.endpoint_receipt_hash,
        collected_at=collected_at,
        include_e_drug=include_e_drug,
    )
    finished_at = datetime.now(UTC)
    run_group_key = f"mfds-label-{item_seq}-{uuid4().hex[:16]}"
    metadata = SnapshotIngestionMetadata(
        source_version=plan.source_version,
        schema_version=SCHEMA_VERSION,
        parser_version=PARSER_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        rejected_record_count=0,
        run_group_key=run_group_key,
        attempt_number=1,
        started_at=started_at,
        finished_at=finished_at,
        collected_at=collected_at,
        duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
        verified_by=config.writer.actor,
    )
    artifact_store = LocalPrivateSourceArtifactStore(config.artifact_root)
    tracked_artifacts = _CleanupTrackingArtifactStore(artifact_store)
    cleanup_journal = LocalPrivateCleanupRequestJournal(config.cleanup_journal_root)
    engine = create_async_engine(config.writer.url, hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        attempted_run_id: UUID | None = None
        try:
            async with sessions.begin() as session:
                await validate_source_writer_session(session)
                await lock_source_artifact_mutation(session)
                persistence = await persist_mfds_label_plan(
                    plan=plan,
                    repository=SqlAlchemySourceSnapshotRepository(session),
                    artifact_store=tracked_artifacts,
                    metadata=metadata,
                )
                attempted_run_id = persistence.persistence.ingestion_run_id
        except Exception:
            cleanup_request_id = record_transaction_cleanup_request(
                journal=cleanup_journal,
                run_group_key=run_group_key,
                ingestion_run_id=attempted_run_id,
                stored_artifacts=tracked_artifacts.stored,
                requested_at=datetime.now(UTC),
            )
            if cleanup_request_id is not None:
                raise MfdsLabelTransactionCleanupRequiredError(cleanup_request_id) from None
            raise
        try:
            async with sessions() as session:
                await validate_source_writer_session(session)
                requery = await requery_mfds_label_persistence(
                    plan=plan,
                    receipt=persistence,
                    repository=SqlAlchemySourceSnapshotRepository(session),
                    artifact_reader=artifact_store,
                )
        except Exception:
            raise MfdsLabelPostCommitVerificationError(persistence) from None
        return MfdsLabelCommittedReceipt(persistence, requery)
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("item_seq")
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--collected-at", required=True, type=parse_collected_at)
    parser.add_argument("--include-e-drug", action="store_true")
    args = parser.parse_args()
    try:
        result = asyncio.run(
            run_ingestion(
                MfdsLabelWriterConfig.from_environment(os.environ),
                item_seq=args.item_seq,
                input_dir=args.input_dir,
                collected_at=args.collected_at,
                include_e_drug=args.include_e_drug,
            )
        )
    except MfdsLabelPostCommitVerificationError as exc:
        persistence = exc.persistence.persistence
        print(
            "MFDS label ingestion committed but post-commit verification failed; "
            f"decision={persistence.decision.value} "
            f"snapshot_id={persistence.snapshot_id} "
            f"ingestion_run_id={persistence.ingestion_run_id}. "
            "Do not rerun until the committed state is investigated.",
            file=sys.stderr,
        )
        return 1
    except MfdsLabelTransactionCleanupRequiredError as exc:
        print(
            "MFDS label ingestion rolled back after preserving private artifacts; "
            f"cleanup_request_id={exc.request_id}.",
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            "MFDS label ingestion did not confirm a database commit; an opened transaction was rolled back. "
            "Immutable artifact objects may require the approved cleanup procedure.",
            file=sys.stderr,
        )
        return 1
    print(
        "MFDS label ingestion committed: "
        f"decision={result.persistence.persistence.decision.value} "
        f"snapshot_id={result.requery.snapshot_id} "
        f"canonical_checksum={result.requery.canonical_checksum} "
        f"member_count={result.requery.member_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
