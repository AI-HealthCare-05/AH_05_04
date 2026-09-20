"""Revalidate and materialize an already-persisted MFDS product acquisition.

This one-shot operator command never calls MFDS and never copies Source rows.  A private
manifest names the exact immutable objects to read; the existing decoder, receipt checks,
checksums, parser, lifecycle service, and repository remain the authority.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import Column, Integer, MetaData, Numeric, String, Table, Text, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.local_private_source_artifact_finalizer import LocalPrivateSourceArtifactReader
from ai_worker.adapters.sqlalchemy_orphan_artifact_references import lock_source_artifact_mutation
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.admin.source_writer import WriterConfig, validate_source_writer_session
from ai_worker.tasks.rag.source_client.contracts import (
    PrimaryKeyValidationResult,
    ProviderPage,
    SourceRunResult,
    SourceRunStatus,
)
from ai_worker.tasks.rag.source_client.decoders import decode_mfds_json
from ai_worker.tasks.rag.source_client.endpoints import MFDS_ENDPOINT_CANDIDATES
from ai_worker.tasks.rag.source_ingestion.artifacts import (
    IngestionArtifactKind,
    RawArtifactMetadata,
    StoredRawArtifact,
    validate_artifact_binding,
)
from ai_worker.tasks.rag.source_ingestion.checksums import product_canonical_checksum, raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.failure_runs import FailedIngestionRunResult
from ai_worker.tasks.rag.source_ingestion.mfds_product import (
    PRODUCT_IDENTITY,
    MfdsProductAcquisition,
    build_product_snapshot_metadata,
    ingest_and_persist_mfds_product,
    load_product_receipt,
    observed_canonical_checksum,
)
from ai_worker.tasks.rag.source_ingestion.product_rejections import classify_product_rejections
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotIngestionDecision,
    SnapshotPersistenceResult,
)

BLOCKED_BY_PRODUCT_SOURCE_HIERARCHY_CONFLICT = "BLOCKED_BY_PRODUCT_SOURCE_HIERARCHY_CONFLICT"
BLOCKED_BY_PRODUCT_ARTIFACT = "BLOCKED_BY_PRODUCT_ARTIFACT"
_MANIFEST_SCHEMA = "mfds-product-rematerialization-manifest@1"
_STORAGE_BACKEND = "LOCAL_PRIVATE"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_CONTRACT = MFDS_ENDPOINT_CANDIDATES["LIST_APPROVED_PRODUCTS"].contract

SOURCE_VALUES: dict[str, object] = {
    "source_code": "MFDS_PRODUCT_APPROVAL",
    "display_name": "MFDS Product Approval",
    "owner_name": None,
    "license_name": None,
    "attribution_text": None,
    "purpose": None,
    "lifecycle_status": "ACTIVE",
    "max_rejected_records": 0,
    "max_rejection_rate": Decimal("0"),
    "empty_result_policy": "REJECT",
    "knowledge_index_lock_marker": 0,
}
ENDPOINT_VALUES: dict[str, object] = {
    "endpoint_code": "MFDS_PRODUCT_APPROVAL_API",
    "display_name": "MFDS Product Approval API",
    "official_url": "https://www.data.go.kr/data/15095677/openapi.do",
    "lifecycle_status": "VERIFIED",
    "runtime_status": "ENABLED",
    "acquisition_status": "APPROVED",
    "knowledge_index_lock_marker": 0,
}
OPERATION_VALUES: dict[str, object] = {
    "operation_code": "LIST_APPROVED_PRODUCTS",
    "display_name": "List Approved Products",
    "runtime_status": "ENABLED",
    "acquisition_status": "APPROVED",
    "knowledge_index_lock_marker": 0,
}

_metadata = MetaData()
_SOURCE = Table(
    "rag_source",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("source_code", String(100)),
    Column("display_name", String(255)),
    Column("owner_name", String(255)),
    Column("license_name", String(255)),
    Column("attribution_text", Text),
    Column("purpose", String(255)),
    Column("lifecycle_status", String(20)),
    Column("max_rejected_records", Integer),
    Column("max_rejection_rate", Numeric),
    Column("empty_result_policy", String(20)),
    Column("knowledge_index_lock_marker", Integer),
)
_ENDPOINT = Table(
    "rag_source_endpoint",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("source_id", String(36)),
    Column("endpoint_code", String(100)),
    Column("display_name", String(255)),
    Column("official_url", String(500)),
    Column("lifecycle_status", String(20)),
    Column("runtime_status", String(20)),
    Column("acquisition_status", String(20)),
    Column("knowledge_index_lock_marker", Integer),
)
_OPERATION = Table(
    "rag_source_operation",
    _metadata,
    Column("id", String(36), primary_key=True),
    Column("endpoint_id", String(36)),
    Column("operation_code", String(100)),
    Column("display_name", String(255)),
    Column("runtime_status", String(20)),
    Column("acquisition_status", String(20)),
    Column("knowledge_index_lock_marker", Integer),
)
_SNAPSHOT = Table(
    "rag_source_snapshot",
    _metadata,
    Column("id", String(36)),
    Column("operation_id", String(36)),
    Column("source_version", String(200)),
    Column("raw_manifest_checksum", String(64)),
    Column("canonical_checksum", String(64)),
    Column("endpoint_receipt_hash", String(64)),
    Column("record_count", Integer),
    Column("rejected_record_count", Integer),
    Column("verification_status", String(20)),
)
_RUN = Table(
    "rag_source_ingestion_run",
    _metadata,
    Column("id", String(36)),
    Column("operation_id", String(36)),
    Column("snapshot_id", String(36)),
    Column("run_status", String(40)),
    Column("failure_code", String(100)),
)
_ARTIFACT = Table(
    "rag_source_ingestion_artifact",
    _metadata,
    Column("id", String(36)),
    Column("ingestion_run_id", String(36)),
    Column("artifact_kind", String(20)),
)


class ProductRematerializationBlockedError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class MfdsProductRematerializationConfig:
    writer: WriterConfig = field(repr=False)
    artifact_reader_root: Path = field(repr=False)

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> "MfdsProductRematerializationConfig":
        if env.get("RAG_MFDS_API_KEY"):
            raise ValueError("MFDS product rematerialization rejects a provider credential")
        root_value = env.get("SOURCE_ARTIFACT_READER_ROOT", "")
        root = Path(root_value)
        if not root_value.strip() or not root.is_absolute():
            raise ValueError("MFDS product rematerialization requires an absolute read-only artifact root")
        return cls(WriterConfig.from_environment(env), root)


@dataclass(frozen=True, slots=True)
class ManifestArtifact:
    page_number: int
    object_key: str
    metadata: RawArtifactMetadata


@dataclass(frozen=True, slots=True)
class ProductArtifactManifest:
    source_version: str
    canonical_checksum: str
    raw_manifest_checksum: str
    endpoint_receipt_hash: str
    record_count: int
    started_at: datetime
    finished_at: datetime
    artifacts: tuple[ManifestArtifact, ...]


@dataclass(frozen=True, slots=True)
class PreparedProductRematerialization:
    acquisition: MfdsProductAcquisition
    metadata: Any
    artifact_store: "ExistingProductRawArtifactStore"
    manifest: ProductArtifactManifest


@dataclass(frozen=True, slots=True)
class ProductSourceHierarchy:
    source_id: UUID
    endpoint_id: UUID
    operation_id: UUID


@dataclass(frozen=True, slots=True)
class ProductRematerializationReceipt:
    persistence: SnapshotPersistenceResult
    artifact_count: int
    record_count: int


class _ArtifactReader(Protocol):
    def read_verified(self, *, object_key: str, metadata: RawArtifactMetadata) -> bytes: ...


class ExistingProductRawArtifactStore:
    """RawArtifactStore adapter that returns the already-existing content-addressed object."""

    def __init__(self, *, artifact_root: Path, reader: _ArtifactReader, artifacts: tuple[ManifestArtifact, ...]) -> None:
        self._root = artifact_root
        self._reader = reader
        self._by_page = {artifact.page_number: artifact for artifact in artifacts}

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
        validate_artifact_binding(
            page_number=page_number,
            artifact_kind=artifact_kind,
            reject_code=reject_code,
            parser_location=parser_location,
        )
        if artifact_kind is not IngestionArtifactKind.RAW_RESPONSE or page_number not in self._by_page:
            raise ProductRematerializationBlockedError(BLOCKED_BY_PRODUCT_ARTIFACT)
        expected = self._by_page[page_number]
        expected_path = self._root / expected.object_key
        if file_path != expected_path or metadata != expected.metadata:
            raise ProductRematerializationBlockedError(BLOCKED_BY_PRODUCT_ARTIFACT)
        self._reader.read_verified(object_key=expected.object_key, metadata=metadata)
        return StoredRawArtifact(page_number, metadata, _STORAGE_BACKEND, expected.object_key)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp")
    return parsed


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("sha256")
    return value


def load_product_artifact_manifest(path: Path) -> ProductArtifactManifest:
    try:
        payload = json.loads(path.read_bytes(), object_pairs_hook=_unique_object)
        required = {
            "schema",
            "source_code",
            "endpoint_code",
            "operation_code",
            "source_version",
            "canonical_checksum",
            "raw_manifest_checksum",
            "endpoint_receipt_hash",
            "record_count",
            "started_at",
            "finished_at",
            "artifacts",
        }
        if not isinstance(payload, dict) or set(payload) != required:
            raise ValueError("manifest shape")
        if (
            payload["schema"] != _MANIFEST_SCHEMA
            or payload["source_code"] != PRODUCT_IDENTITY.source_code
            or payload["endpoint_code"] != PRODUCT_IDENTITY.endpoint_code
            or payload["operation_code"] != PRODUCT_IDENTITY.operation_code
            or type(payload["record_count"]) is not int
            or payload["record_count"] <= 0
            or not isinstance(payload["source_version"], str)
            or not isinstance(payload["artifacts"], list)
            or not payload["artifacts"]
        ):
            raise ValueError("manifest values")
        artifacts = []
        for entry in payload["artifacts"]:
            if not isinstance(entry, dict) or set(entry) != {
                "page_number",
                "artifact_key",
                "object_key",
                "raw_checksum",
                "byte_size",
                "content_type",
            }:
                raise ValueError("artifact shape")
            checksum = _require_sha256(entry["raw_checksum"])
            page = entry["page_number"]
            if type(page) is not int or page < 1:
                raise ValueError("page")
            expected_key = f"sha256/{checksum[:2]}/{checksum}.artifact"
            if (
                entry["object_key"] != expected_key
                or entry["artifact_key"] != f"product-page-{page}.json"
                or entry["content_type"] not in _CONTRACT.allowed_content_types
            ):
                raise ValueError("artifact binding")
            metadata = RawArtifactMetadata(
                entry["artifact_key"], checksum, entry["byte_size"], entry["content_type"]
            )
            artifacts.append(ManifestArtifact(page, expected_key, metadata))
        if [item.page_number for item in artifacts] != list(range(1, len(artifacts) + 1)):
            raise ValueError("page sequence")
        return ProductArtifactManifest(
            payload["source_version"],
            _require_sha256(payload["canonical_checksum"]),
            _require_sha256(payload["raw_manifest_checksum"]),
            _require_sha256(payload["endpoint_receipt_hash"]),
            payload["record_count"],
            _parse_timestamp(payload["started_at"]),
            _parse_timestamp(payload["finished_at"]),
            tuple(artifacts),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        raise ProductRematerializationBlockedError(BLOCKED_BY_PRODUCT_ARTIFACT) from None


def prepare_product_rematerialization(
    *,
    manifest_path: Path,
    receipt_path: Path,
    repository_root: Path,
    artifact_root: Path,
    reader: _ArtifactReader,
    verified_by: str | None = None,
) -> PreparedProductRematerialization:
    manifest = load_product_artifact_manifest(manifest_path)
    try:
        receipt = load_product_receipt(receipt_path=receipt_path, repository_root=repository_root)
        if receipt.receipt_hash != manifest.endpoint_receipt_hash or receipt.validated_record_count != manifest.record_count:
            raise ValueError("receipt parity")
        acquisition, records = _reconstruct_acquisition(manifest, artifact_root=artifact_root, reader=reader)
        if product_canonical_checksum(records) != manifest.canonical_checksum:
            raise ValueError("canonical checksum")
        if observed_canonical_checksum(acquisition) != manifest.canonical_checksum:
            raise ValueError("canonical checksum")
        if raw_manifest_checksum(item.metadata for item in manifest.artifacts) != manifest.raw_manifest_checksum:
            raise ValueError("manifest checksum")
        metadata = build_product_snapshot_metadata(
            acquisition=acquisition,
            run_group_key=f"mfds-product-rematerialization-{uuid4().hex[:16]}",
            verified_by=verified_by,
        )
        if metadata.source_version != manifest.source_version:
            raise ValueError("source version")
        store = ExistingProductRawArtifactStore(
            artifact_root=artifact_root, reader=reader, artifacts=manifest.artifacts
        )
        return PreparedProductRematerialization(acquisition, metadata, store, manifest)
    except ProductRematerializationBlockedError:
        raise
    except (OSError, TypeError, ValueError):
        raise ProductRematerializationBlockedError(BLOCKED_BY_PRODUCT_ARTIFACT) from None


def _reconstruct_acquisition(
    manifest: ProductArtifactManifest, *, artifact_root: Path, reader: _ArtifactReader
) -> tuple[MfdsProductAcquisition, tuple[Mapping[str, object], ...]]:
    pages: list[ProviderPage] = []
    acquisition_artifacts = []
    records: list[Mapping[str, object]] = []
    totals = set()
    for artifact in manifest.artifacts:
        content = reader.read_verified(object_key=artifact.object_key, metadata=artifact.metadata)
        decoded = decode_mfds_json(content, artifact.metadata.content_type)
        if decoded.body_code not in _CONTRACT.body_codes.success_codes or decoded.page_number != artifact.page_number:
            raise ValueError("page binding")
        pages.append(
            ProviderPage(
                decoded.page_number,
                decoded.records,
                artifact.metadata.raw_checksum,
                artifact.metadata.content_type,
                decoded.total_count,
            )
        )
        records.extend(decoded.records)
        totals.add(decoded.total_count)
        acquisition_artifacts.append((artifact.page_number, artifact_root / artifact.object_key, artifact.metadata))
    if totals != {len(records)} or len(records) != manifest.record_count:
        raise ValueError("record count")
    if classify_product_rejections((page.page_number, page.records) for page in pages):
        raise ValueError("identity")
    result = SourceRunResult(
        PRODUCT_IDENTITY,
        SourceRunStatus.SUCCEEDED,
        tuple(pages),
        None,
        PrimaryKeyValidationResult(True, len(records), 0, 0),
        True,
    )
    return (
        MfdsProductAcquisition(
            result,
            tuple(acquisition_artifacts),
            artifact_root,
            manifest.started_at,
            manifest.finished_at,
        ),
        tuple(records),
    )


def _row_matches(row: Mapping[Any, Any], expected: Mapping[str, object], *, parent: tuple[str, UUID] | None = None) -> bool:
    if parent is not None and str(row[parent[0]]) != str(parent[1]):
        return False
    return all(row[key] == value for key, value in expected.items())


async def _ensure_row(
    session: AsyncSession,
    *,
    table: Table,
    code_column: Column[Any],
    code: str,
    values: Mapping[str, object],
    constraint: str,
    parent: tuple[str, UUID] | None = None,
) -> UUID:
    condition = code_column == code
    if parent is not None:
        condition = condition & (table.c[parent[0]] == str(parent[1]))
    row = (await session.execute(select(table).where(condition).with_for_update())).mappings().one_or_none()
    if row is not None:
        if not _row_matches(row, values, parent=parent):
            raise ProductRematerializationBlockedError(BLOCKED_BY_PRODUCT_SOURCE_HIERARCHY_CONFLICT)
        return UUID(str(row["id"]))
    row_id = uuid4()
    insert_values = {"id": str(row_id), **values}
    if parent is not None:
        insert_values[parent[0]] = str(parent[1])
    inserted = (
        await session.execute(
            postgresql_insert(table)
            .values(**insert_values)
            .on_conflict_do_nothing(constraint=constraint)
            .returning(table.c.id)
        )
    ).scalar_one_or_none()
    if inserted is not None:
        return UUID(str(inserted))
    raced = (await session.execute(select(table).where(condition).with_for_update())).mappings().one_or_none()
    if raced is None or not _row_matches(raced, values, parent=parent):
        raise ProductRematerializationBlockedError(BLOCKED_BY_PRODUCT_SOURCE_HIERARCHY_CONFLICT)
    return UUID(str(raced["id"]))


async def ensure_product_source_hierarchy(session: AsyncSession) -> ProductSourceHierarchy:
    source_id = await _ensure_row(
        session,
        table=_SOURCE,
        code_column=_SOURCE.c.source_code,
        code=PRODUCT_IDENTITY.source_code,
        values=SOURCE_VALUES,
        constraint="uq_rag_source_code",
    )
    endpoint_id = await _ensure_row(
        session,
        table=_ENDPOINT,
        code_column=_ENDPOINT.c.endpoint_code,
        code=PRODUCT_IDENTITY.endpoint_code,
        values=ENDPOINT_VALUES,
        constraint="uq_rag_source_endpoint_code",
        parent=("source_id", source_id),
    )
    operation_id = await _ensure_row(
        session,
        table=_OPERATION,
        code_column=_OPERATION.c.operation_code,
        code=PRODUCT_IDENTITY.operation_code,
        values=OPERATION_VALUES,
        constraint="uq_rag_source_operation_code",
        parent=("endpoint_id", endpoint_id),
    )
    return ProductSourceHierarchy(source_id, endpoint_id, operation_id)


async def _verify_committed(
    session: AsyncSession,
    *,
    persistence: SnapshotPersistenceResult,
    manifest: ProductArtifactManifest,
) -> None:
    row = (
        await session.execute(
            select(
                _SOURCE.c.source_code,
                _ENDPOINT.c.endpoint_code,
                _OPERATION.c.operation_code,
                _SNAPSHOT.c.source_version,
                _SNAPSHOT.c.raw_manifest_checksum,
                _SNAPSHOT.c.canonical_checksum,
                _SNAPSHOT.c.endpoint_receipt_hash,
                _SNAPSHOT.c.record_count,
                _SNAPSHOT.c.rejected_record_count,
                _SNAPSHOT.c.verification_status,
            )
            .select_from(
                _SNAPSHOT.join(_OPERATION, _SNAPSHOT.c.operation_id == _OPERATION.c.id)
                .join(_ENDPOINT, _OPERATION.c.endpoint_id == _ENDPOINT.c.id)
                .join(_SOURCE, _ENDPOINT.c.source_id == _SOURCE.c.id)
            )
            .where(_SNAPSHOT.c.id == str(persistence.snapshot_id))
        )
    ).mappings().one_or_none()
    expected = {
        "source_code": PRODUCT_IDENTITY.source_code,
        "endpoint_code": PRODUCT_IDENTITY.endpoint_code,
        "operation_code": PRODUCT_IDENTITY.operation_code,
        "source_version": manifest.source_version,
        "raw_manifest_checksum": manifest.raw_manifest_checksum,
        "canonical_checksum": manifest.canonical_checksum,
        "endpoint_receipt_hash": manifest.endpoint_receipt_hash,
        "record_count": manifest.record_count,
        "rejected_record_count": 0,
    }
    if row is None or not all(row[key] == value for key, value in expected.items()):
        raise ProductRematerializationBlockedError(BLOCKED_BY_PRODUCT_ARTIFACT)
    allowed_statuses = (
        {"PENDING"}
        if persistence.decision is SnapshotIngestionDecision.CREATED
        else {"PENDING", "CURRENT"}
    )
    if row["verification_status"] not in allowed_statuses:
        raise ProductRematerializationBlockedError(BLOCKED_BY_PRODUCT_ARTIFACT)
    run = (
        await session.execute(
            select(
                _RUN.c.operation_id,
                _RUN.c.snapshot_id,
                _RUN.c.run_status,
                _RUN.c.failure_code,
            ).where(_RUN.c.id == str(persistence.ingestion_run_id))
        )
    ).mappings().one_or_none()
    expected_run_status = (
        "SUCCEEDED" if persistence.decision is SnapshotIngestionDecision.CREATED else "NO_CHANGE"
    )
    if (
        run is None
        or str(run["operation_id"]) != str(persistence.operation_id)
        or str(run["snapshot_id"]) != str(persistence.snapshot_id)
        or run["run_status"] != expected_run_status
        or run["failure_code"] is not None
    ):
        raise ProductRematerializationBlockedError(BLOCKED_BY_PRODUCT_ARTIFACT)
    successful = await session.scalar(
        select(_RUN.c.id)
        .where(_RUN.c.snapshot_id == str(persistence.snapshot_id), _RUN.c.run_status == "SUCCEEDED")
        .limit(1)
    )
    artifact_count = await session.scalar(
        select(func.count(_ARTIFACT.c.id))
        .where(
            _ARTIFACT.c.ingestion_run_id == str(persistence.ingestion_run_id),
            _ARTIFACT.c.artifact_kind == IngestionArtifactKind.RAW_RESPONSE,
        )
    )
    if successful is None or artifact_count != len(manifest.artifacts):
        raise ProductRematerializationBlockedError(BLOCKED_BY_PRODUCT_ARTIFACT)


async def run_rematerialization(
    config: MfdsProductRematerializationConfig,
    *,
    manifest_path: Path,
    receipt_path: Path,
    repository_root: Path,
) -> ProductRematerializationReceipt:
    reader = LocalPrivateSourceArtifactReader(config.artifact_reader_root)
    engine = create_async_engine(config.writer.url, hide_parameters=True)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions.begin() as session:
            await validate_source_writer_session(session)
            await lock_source_artifact_mutation(session)
            await ensure_product_source_hierarchy(session)
            prepared = prepare_product_rematerialization(
                manifest_path=manifest_path,
                receipt_path=receipt_path,
                repository_root=repository_root,
                artifact_root=config.artifact_reader_root,
                reader=reader,
                verified_by=config.writer.actor,
            )
            persistence = await ingest_and_persist_mfds_product(
                acquisition=prepared.acquisition,
                repository=SqlAlchemySourceSnapshotRepository(session),
                artifact_store=prepared.artifact_store,
                metadata=prepared.metadata,
                receipt_path=receipt_path,
                repository_root=repository_root,
            )
            if isinstance(persistence, FailedIngestionRunResult) or persistence.snapshot_id is None:
                raise ProductRematerializationBlockedError(BLOCKED_BY_PRODUCT_ARTIFACT)
        async with sessions() as session:
            await validate_source_writer_session(session)
            await _verify_committed(session, persistence=persistence, manifest=prepared.manifest)
        return ProductRematerializationReceipt(
            persistence, len(prepared.manifest.artifacts), prepared.manifest.record_count
        )
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rematerialize verified MFDS product artifacts into Source lifecycle")
    parser.add_argument("--manifest-path", required=True, type=Path)
    parser.add_argument("--receipt-path", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = asyncio.run(
            run_rematerialization(
                MfdsProductRematerializationConfig.from_environment(os.environ),
                manifest_path=args.manifest_path,
                receipt_path=args.receipt_path,
                repository_root=args.repository_root,
            )
        )
    except ProductRematerializationBlockedError as exc:
        print(exc.code, file=sys.stderr)
        return 1
    except Exception:
        print("MFDS_PRODUCT_REMATERIALIZATION_FAILED", file=sys.stderr)
        return 1
    print(
        "MFDS product rematerialization committed: "
        f"snapshot_id={result.persistence.snapshot_id} "
        f"ingestion_run_id={result.persistence.ingestion_run_id} "
        f"decision={result.persistence.decision.value} "
        f"record_count={result.record_count} artifact_count={result.artifact_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
