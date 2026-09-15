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
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.admin.source_writer import WriterConfig, validate_source_writer_session
from ai_worker.tasks.rag.source_client.contracts import SourceOperationIdentity
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
    metadata = SnapshotIngestionMetadata(
        source_version=plan.source_version,
        schema_version=SCHEMA_VERSION,
        parser_version=PARSER_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        rejected_record_count=0,
        run_group_key=f"mfds-label-{item_seq}-{uuid4().hex[:16]}",
        attempt_number=1,
        started_at=started_at,
        finished_at=finished_at,
        collected_at=collected_at,
        duration_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
        verified_by=config.writer.actor,
    )
    artifact_store = LocalPrivateSourceArtifactStore(config.artifact_root)
    engine = create_async_engine(config.writer.url, hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            await validate_source_writer_session(session)
            persistence = await persist_mfds_label_plan(
                plan=plan,
                repository=SqlAlchemySourceSnapshotRepository(session),
                artifact_store=artifact_store,
                metadata=metadata,
            )
        async with sessions() as session:
            await validate_source_writer_session(session)
            requery = await requery_mfds_label_persistence(
                plan=plan,
                receipt=persistence,
                repository=SqlAlchemySourceSnapshotRepository(session),
                artifact_reader=artifact_store,
            )
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
    except Exception:
        print("MFDS label ingestion failed; database transaction rolled back.", file=sys.stderr)
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
