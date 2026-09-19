"""Catalog 승인 발급·철회 one-shot operator command (#526 Phase 2).

Catalog Writer는 승인을 읽기만 하고 만들지 못합니다. 승인 발급·철회는 오직 이 command가
승인 전용 credential로 수행하며, 권한 부여·발급·철회를 각각 별도 audit event로 남깁니다.

`issue-product-catalog`는 승인 대상 checksum을 호출자에게서 받지 않습니다. 실제 Product Source
authority를 다시 검증하고 Catalog export를 직접 재계산한 결과만 승인 대상으로 삼습니다.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, LargeBinary, String, column, func, select, table, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql import Select

from ai_worker.adapters.catalog_approval_advisory_lock import (
    acquire_catalog_approval_advisory_locks,
    acquire_catalog_approval_target_locks,
)
from ai_worker.adapters.local_private_source_artifact_finalizer import LocalPrivateSourceArtifactReader
from ai_worker.adapters.sqlalchemy_catalog_approval_verifier import CATALOG_SOURCE_USE_PURPOSE
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.tasks.rag.catalog.build import build_catalog_members
from ai_worker.tasks.rag.catalog.export import CATALOG_MANIFEST_SPEC_VERSION, create_catalog_export
from ai_worker.tasks.rag.catalog.mfds_product_source import ProductSourceBindingError, read_product_input
from ai_worker.tasks.rag.catalog.types import CandidateCatalogSourceRef
from ai_worker.tasks.rag.catalog.validate import validate_catalog_members

#: 승인 command가 절대 함께 실행되어서는 안 되는 다른 책임의 credential.
FORBIDDEN_CREDENTIAL_KEYS = (
    "DB_PASSWORD",
    "DB_APP_PASSWORD",
    "DB_ADMIN_PASSWORD",
    "DB_MIGRATION_PASSWORD",
    "SOURCE_WRITER_PASSWORD",
    "SOURCE_MANAGEMENT_PASSWORD",
    "CATALOG_WRITER_PASSWORD",
    "CANDIDATE_INDEX_BUILDER_PASSWORD",
    "KNOWLEDGE_INDEX_BUILDER_PASSWORD",
)

#: 철회 사유는 고정 문구만 씁니다. 자유서술·경로·원문을 남기지 않습니다.
REVOCATION_REASON = "OPERATOR_REVOCATION"

AUDIT_GRANT_PERMISSION = "GRANT_PERMISSION"
AUDIT_REVOKE_PERMISSION = "REVOKE_PERMISSION"
AUDIT_ISSUE_SOURCE = "ISSUE_SOURCE"
AUDIT_ISSUE_CATALOG = "ISSUE_CATALOG"
AUDIT_REVOKE_SOURCE = "REVOKE_SOURCE"
AUDIT_REVOKE_CATALOG = "REVOKE_CATALOG"

_PERMISSION = table(
    "catalog_approval_permission",
    column("user_id", String(36)),
    column("enabled", Boolean()),
    column("evidence_ref", String(500)),
    column("revision", Integer()),
    column("updated_at", DateTime(timezone=True)),
)
_AUDIT = table(
    "catalog_approval_audit",
    column("id", String(36)),
    column("request_id", String(36)),
    column("event_kind", String(20)),
    column("actor_id", String(36)),
    column("subject_user_id", String(36)),
    column("source_approval_id", String(36)),
    column("build_approval_id", String(36)),
    column("source_snapshot_id", String(36)),
    column("source_version", String(200)),
    column("purpose", String(60)),
    column("catalog_version", String(100)),
    column("export_checksum", String(64)),
    column("request_fingerprint", String(64)),
)
_SOURCE_APPROVAL = table(
    "catalog_source_approval",
    column("id", String(36)),
    column("source_snapshot_id", String(36)),
    column("source_version", String(200)),
    column("purpose", String(60)),
    column("actor_id", String(36)),
    column("evidence_ref", String(500)),
    column("valid_from", DateTime(timezone=True)),
    column("expires_at", DateTime(timezone=True)),
    column("revoked_at", DateTime(timezone=True)),
    column("revoked_by", String(36)),
    column("revoked_reason", String(200)),
    column("issued_revision", Integer()),
)
_BUILD_APPROVAL = table(
    "catalog_build_approval",
    column("id", String(36)),
    column("catalog_version", String(100)),
    column("export_checksum", String(64)),
    column("schema_version", String(100)),
    column("manifest_spec_version", String(100)),
    column("approved_export_bytes", LargeBinary()),
    column("is_complete", Boolean()),
    column("actor_id", String(36)),
    column("evidence_ref", String(500)),
    column("valid_from", DateTime(timezone=True)),
    column("expires_at", DateTime(timezone=True)),
    column("revoked_at", DateTime(timezone=True)),
    column("revoked_by", String(36)),
    column("revoked_reason", String(200)),
    column("issued_revision", Integer()),
)
_BUILD_APPROVAL_SOURCE = table(
    "catalog_build_approval_source",
    column("id", String(36)),
    column("build_approval_id", String(36)),
    column("source_approval_id", String(36)),
    column("source_snapshot_id", String(36)),
    column("source_version", String(200)),
)
_USER = table("user", column("id", String(36)))


class CatalogApprovalCommandError(ValueError):
    """운영자에게 보여줄 sanitized 실패 사유입니다. 원문·secret을 담지 않습니다."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def approval_url(environment: Mapping[str, str]) -> URL:
    """승인 전용 credential만 허용합니다. 다른 책임의 credential이 보이면 즉시 거부합니다."""
    if any(environment.get(key) for key in FORBIDDEN_CREDENTIAL_KEYS):
        raise CatalogApprovalCommandError("BLOCKED_BY_CREDENTIAL_ISOLATION")
    values = {
        key: environment.get(f"CATALOG_APPROVAL_{key}", "") for key in ("HOST", "PORT", "NAME", "USER", "PASSWORD")
    }
    if any(not value.strip() for value in values.values()):
        raise CatalogApprovalCommandError("BLOCKED_BY_APPROVAL_CONFIGURATION")
    try:
        port = int(values["PORT"])
    except ValueError:
        raise CatalogApprovalCommandError("BLOCKED_BY_APPROVAL_CONFIGURATION") from None
    if not 1 <= port <= 65535:
        raise CatalogApprovalCommandError("BLOCKED_BY_APPROVAL_CONFIGURATION")
    return URL.create(
        "postgresql+asyncpg",
        username=values["USER"],
        password=values["PASSWORD"],
        host=values["HOST"],
        port=port,
        database=values["NAME"],
    )


async def validate_approval_connection(connection: AsyncConnection) -> None:
    """승인 role이 소유자·관리 권한 없이 승인 범위만 갖는지 확인합니다."""
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
        raise CatalogApprovalCommandError("BLOCKED_BY_APPROVAL_ROLE_BOUNDARY")
    forbidden = await connection.scalar(
        text(
            "SELECT has_table_privilege(current_user,'catalog_approval_audit','UPDATE,DELETE,TRUNCATE') "
            "OR has_table_privilege(current_user,'catalog_source_approval','DELETE,TRUNCATE') "
            "OR has_table_privilege(current_user,'catalog_build_approval','DELETE,TRUNCATE') "
            "OR has_table_privilege(current_user,'catalog_build_approval_source','DELETE,TRUNCATE') "
            "OR has_table_privilege(current_user,'rag_catalog_set','INSERT,UPDATE,DELETE') "
            "OR has_table_privilege(current_user,'rag_medication_product','INSERT,UPDATE,DELETE')"
        )
    )
    if forbidden is not False:
        raise CatalogApprovalCommandError("BLOCKED_BY_APPROVAL_ROLE_BOUNDARY")


def fingerprint(payload: Mapping[str, object]) -> str:
    """요청 본문의 결정적 지문. 같은 request_id에 다른 요청이 오면 구별하기 위한 값입니다."""
    canonical = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    actor_id: UUID
    request_id: UUID
    evidence_ref: str


async def _require_user(session: AsyncSession, user_id: UUID) -> None:
    if await session.scalar(select(_USER.c.id).where(_USER.c.id == str(user_id))) is None:
        raise CatalogApprovalCommandError("BLOCKED_BY_UNKNOWN_OPERATOR")


async def _require_permission(session: AsyncSession, actor_id: UUID) -> None:
    """발급·철회는 현재 enabled 권한이 있어야 합니다. 권한 row가 없으면 차단합니다."""
    enabled = await session.scalar(select(_PERMISSION.c.enabled).where(_PERMISSION.c.user_id == str(actor_id)))
    if enabled is not True:
        raise CatalogApprovalCommandError("BLOCKED_BY_APPROVAL_PERMISSION")


async def _replayed_event(
    session: AsyncSession, *, actor_id: UUID, request_id: UUID, event_kind: str, request_fingerprint: str
) -> str | None:
    """같은 요청의 재실행이면 기존 결과를 재사용하고, 내용이 다르면 fail closed합니다."""
    row = (
        await session.execute(
            select(_AUDIT.c.id, _AUDIT.c.request_fingerprint).where(
                _AUDIT.c.actor_id == str(actor_id),
                _AUDIT.c.request_id == str(request_id),
                _AUDIT.c.event_kind == event_kind,
            )
        )
    ).first()
    if row is None:
        return None
    if row[1] != request_fingerprint:
        raise CatalogApprovalCommandError("BLOCKED_BY_REQUEST_FINGERPRINT_CONFLICT")
    return str(row[0])


async def _append_audit(session: AsyncSession, *, event_kind: str, request_fingerprint: str, **fields: object) -> UUID:
    audit_id = uuid4()
    await session.execute(
        _AUDIT.insert().values(
            id=str(audit_id),
            event_kind=event_kind,
            request_fingerprint=request_fingerprint,
            **fields,
        )
    )
    return audit_id


async def set_permission(
    session: AsyncSession, *, request: ApprovalRequest, user_id: UUID, enabled: bool
) -> dict[str, object]:
    """운영자 권한 현재 상태를 바꾸고 별도 audit event를 남깁니다.

    데모 기간에는 actor와 대상이 같을 수 있지만, 권한 부여는 언제나 발급과 분리된 event입니다.
    """
    event_kind = AUDIT_GRANT_PERMISSION if enabled else AUDIT_REVOKE_PERMISSION
    request_fingerprint = fingerprint(
        {
            "actor_id": str(request.actor_id),
            "request_id": str(request.request_id),
            "evidence_ref": request.evidence_ref,
            "user_id": str(user_id),
            "enabled": enabled,
            "event_kind": event_kind,
        }
    )
    replayed = await _replayed_event(
        session,
        actor_id=request.actor_id,
        request_id=request.request_id,
        event_kind=event_kind,
        request_fingerprint=request_fingerprint,
    )
    if replayed is not None:
        return {"status": "REPLAYED", "event_kind": event_kind, "audit_id": replayed}

    await _require_user(session, request.actor_id)
    await _require_user(session, user_id)
    current = (
        await session.execute(select(_PERMISSION.c.revision).where(_PERMISSION.c.user_id == str(user_id)))
    ).first()
    now = datetime.now(UTC)
    if current is None:
        await session.execute(
            _PERMISSION.insert().values(
                user_id=str(user_id),
                enabled=enabled,
                evidence_ref=request.evidence_ref,
                revision=1,
                updated_at=now,
            )
        )
        revision = 1
    else:
        revision = int(current[0]) + 1
        await session.execute(
            _PERMISSION.update()
            .where(_PERMISSION.c.user_id == str(user_id))
            .values(enabled=enabled, evidence_ref=request.evidence_ref, revision=revision, updated_at=now)
        )
    audit_id = await _append_audit(
        session,
        event_kind=event_kind,
        request_fingerprint=request_fingerprint,
        request_id=str(request.request_id),
        actor_id=str(request.actor_id),
        subject_user_id=str(user_id),
    )
    return {"status": "APPLIED", "event_kind": event_kind, "audit_id": str(audit_id), "revision": revision}


@dataclass(frozen=True, slots=True)
class ProductCatalogTarget:
    source_snapshot_id: str
    ingestion_run_id: str
    item_seq: str
    catalog_version: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RecomputedCatalogApprovalTarget:
    """command가 직접 재계산한 승인 대상. 호출자가 주장한 값은 하나도 쓰지 않습니다."""

    source_ref: CandidateCatalogSourceRef
    catalog_jsonl: bytes
    export_checksum: str
    schema_version: str
    manifest_spec_version: str
    is_complete: bool


async def recompute_product_catalog_target(
    session: AsyncSession,
    *,
    artifact_reader: LocalPrivateSourceArtifactReader,
    target: ProductCatalogTarget,
) -> RecomputedCatalogApprovalTarget:
    """실제 Product Source authority를 다시 검증하고 Catalog export를 재계산합니다.

    checksum·schema_version·manifest spec·승인 대상 bytes는 CLI 입력이 아니라 여기서
    기존 순수 builder가 만들어 낸 값입니다.
    """
    product, receipt = await read_product_input(
        repository=SqlAlchemySourceSnapshotRepository(session),
        artifact_reader=artifact_reader,
        source_snapshot_id=target.source_snapshot_id,
        ingestion_run_id=target.ingestion_run_id,
        item_seq=target.item_seq,
    )
    source_ref = CandidateCatalogSourceRef(str(receipt.source_snapshot_id), receipt.source_version)
    members = build_catalog_members(products=(product,), ingredients=(), components=(), aliases=())
    validation = validate_catalog_members(members)
    if not validation.is_valid:
        raise CatalogApprovalCommandError("BLOCKED_BY_CATALOG_VALIDATION")
    artifacts = create_catalog_export(
        catalog_version=target.catalog_version,
        source_refs=(source_ref,),
        members=members,
        validation=validation,
        # 승인 자체를 만드는 중이므로 receipt 없이 계산합니다. 승인이 승인을 전제하지 않습니다.
        approval_receipt=None,
    )
    return RecomputedCatalogApprovalTarget(
        source_ref=source_ref,
        catalog_jsonl=artifacts.catalog_jsonl,
        export_checksum=artifacts.export_checksum,
        schema_version=artifacts.catalog.schema_version,
        manifest_spec_version=CATALOG_MANIFEST_SPEC_VERSION,
        is_complete=bool(members.search_entries),
    )


async def _next_revision(session: AsyncSession, statement: Select[tuple[int | None]]) -> int:
    return int(await session.scalar(statement) or 0) + 1


async def _reject_active_source_approval(session: AsyncSession, *, snapshot_id: str, now: datetime) -> None:
    active = await session.scalar(
        select(func.count())
        .select_from(_SOURCE_APPROVAL)
        .where(
            _SOURCE_APPROVAL.c.source_snapshot_id == snapshot_id,
            _SOURCE_APPROVAL.c.purpose == CATALOG_SOURCE_USE_PURPOSE,
            _SOURCE_APPROVAL.c.revoked_at.is_(None),
            _SOURCE_APPROVAL.c.expires_at > now,
            _SOURCE_APPROVAL.c.valid_from <= now,
        )
    )
    if active:
        # 살아 있는 승인이 둘이 되면 verifier가 어느 쪽을 쓸지 정할 수 없습니다.
        raise CatalogApprovalCommandError("BLOCKED_BY_ACTIVE_SOURCE_APPROVAL")


async def _reject_active_build_approval(
    session: AsyncSession, *, catalog_version: str, export_checksum: str, now: datetime
) -> None:
    active = await session.scalar(
        select(func.count())
        .select_from(_BUILD_APPROVAL)
        .where(
            _BUILD_APPROVAL.c.catalog_version == catalog_version,
            _BUILD_APPROVAL.c.export_checksum == export_checksum,
            _BUILD_APPROVAL.c.revoked_at.is_(None),
            _BUILD_APPROVAL.c.expires_at > now,
            _BUILD_APPROVAL.c.valid_from <= now,
        )
    )
    if active:
        raise CatalogApprovalCommandError("BLOCKED_BY_ACTIVE_CATALOG_APPROVAL")


async def issue_product_catalog(
    session: AsyncSession,
    *,
    request: ApprovalRequest,
    artifact_reader: LocalPrivateSourceArtifactReader,
    target: ProductCatalogTarget,
) -> dict[str, object]:
    """Source 승인과 Catalog 승인을 한 transaction에서 함께 발급합니다.

    둘 중 하나만 남는 partial success를 만들지 않습니다. ISSUE_SOURCE와 ISSUE_CATALOG은 같은
    request_id를 쓰지만 event_kind가 달라 각각 별도 audit row로 남습니다.
    """
    request_fingerprint = fingerprint(
        {
            "actor_id": str(request.actor_id),
            "request_id": str(request.request_id),
            "evidence_ref": request.evidence_ref,
            "source_snapshot_id": target.source_snapshot_id,
            "ingestion_run_id": target.ingestion_run_id,
            "item_seq": target.item_seq,
            "catalog_version": target.catalog_version,
            "expires_at": target.expires_at.isoformat(),
            "purpose": CATALOG_SOURCE_USE_PURPOSE,
        }
    )
    replayed = await _replayed_event(
        session,
        actor_id=request.actor_id,
        request_id=request.request_id,
        event_kind=AUDIT_ISSUE_CATALOG,
        request_fingerprint=request_fingerprint,
    )
    if replayed is not None:
        return {"status": "REPLAYED", "event_kind": AUDIT_ISSUE_CATALOG, "audit_id": replayed}

    await _require_user(session, request.actor_id)
    await _require_permission(session, request.actor_id)
    recomputed = await recompute_product_catalog_target(session, artifact_reader=artifact_reader, target=target)
    await acquire_catalog_approval_target_locks(
        session,
        (
            f"snapshot:{recomputed.source_ref.snapshot_id}:{CATALOG_SOURCE_USE_PURPOSE}",
            f"catalog:{target.catalog_version}:{recomputed.export_checksum}",
        ),
    )
    now = datetime.now(UTC)
    if target.expires_at <= now:
        raise CatalogApprovalCommandError("BLOCKED_BY_APPROVAL_VALIDITY_WINDOW")
    await _reject_active_source_approval(session, snapshot_id=recomputed.source_ref.snapshot_id, now=now)
    await _reject_active_build_approval(
        session,
        catalog_version=target.catalog_version,
        export_checksum=recomputed.export_checksum,
        now=now,
    )

    source_approval_id = uuid4()
    source_revision = await _next_revision(
        session,
        select(func.max(_SOURCE_APPROVAL.c.issued_revision)).where(
            _SOURCE_APPROVAL.c.source_snapshot_id == recomputed.source_ref.snapshot_id,
            _SOURCE_APPROVAL.c.purpose == CATALOG_SOURCE_USE_PURPOSE,
        ),
    )
    await session.execute(
        _SOURCE_APPROVAL.insert().values(
            id=str(source_approval_id),
            source_snapshot_id=recomputed.source_ref.snapshot_id,
            source_version=recomputed.source_ref.source_version,
            purpose=CATALOG_SOURCE_USE_PURPOSE,
            actor_id=str(request.actor_id),
            evidence_ref=request.evidence_ref,
            valid_from=now,
            expires_at=target.expires_at,
            issued_revision=source_revision,
        )
    )

    build_approval_id = uuid4()
    build_revision = await _next_revision(
        session,
        select(func.max(_BUILD_APPROVAL.c.issued_revision)).where(
            _BUILD_APPROVAL.c.catalog_version == target.catalog_version,
            _BUILD_APPROVAL.c.export_checksum == recomputed.export_checksum,
        ),
    )
    await session.execute(
        _BUILD_APPROVAL.insert().values(
            id=str(build_approval_id),
            catalog_version=target.catalog_version,
            export_checksum=recomputed.export_checksum,
            schema_version=recomputed.schema_version,
            manifest_spec_version=recomputed.manifest_spec_version,
            # 재계산한 export 원본 그대로를 승인 대상 bytes로 고정합니다.
            approved_export_bytes=recomputed.catalog_jsonl,
            is_complete=recomputed.is_complete,
            actor_id=str(request.actor_id),
            evidence_ref=request.evidence_ref,
            valid_from=now,
            expires_at=target.expires_at,
            issued_revision=build_revision,
        )
    )
    await session.execute(
        _BUILD_APPROVAL_SOURCE.insert().values(
            id=str(uuid4()),
            build_approval_id=str(build_approval_id),
            source_approval_id=str(source_approval_id),
            source_snapshot_id=recomputed.source_ref.snapshot_id,
            source_version=recomputed.source_ref.source_version,
        )
    )
    await _append_audit(
        session,
        event_kind=AUDIT_ISSUE_SOURCE,
        request_fingerprint=request_fingerprint,
        request_id=str(request.request_id),
        actor_id=str(request.actor_id),
        source_approval_id=str(source_approval_id),
        source_snapshot_id=recomputed.source_ref.snapshot_id,
        source_version=recomputed.source_ref.source_version,
        purpose=CATALOG_SOURCE_USE_PURPOSE,
    )
    catalog_audit_id = await _append_audit(
        session,
        event_kind=AUDIT_ISSUE_CATALOG,
        request_fingerprint=request_fingerprint,
        request_id=str(request.request_id),
        actor_id=str(request.actor_id),
        build_approval_id=str(build_approval_id),
        source_approval_id=str(source_approval_id),
        catalog_version=target.catalog_version,
        export_checksum=recomputed.export_checksum,
    )
    return {
        "status": "APPLIED",
        "event_kind": AUDIT_ISSUE_CATALOG,
        "audit_id": str(catalog_audit_id),
        "source_approval_id": str(source_approval_id),
        "build_approval_id": str(build_approval_id),
        "export_checksum": recomputed.export_checksum,
        "catalog_version": target.catalog_version,
    }


async def revoke_approval(
    session: AsyncSession,
    *,
    request: ApprovalRequest,
    approval_id: UUID,
    event_kind: str,
) -> dict[str, object]:
    """Source 또는 Catalog 승인을 철회하고 별도 audit event를 남깁니다.

    저장·소비와 같은 승인 ID advisory lock을 같은 순서로 잡습니다. 그래서 철회가 진행 중이면
    저장/소비의 재검증이 기다렸다가 철회된 상태를 보게 됩니다.
    """
    approvals = {AUDIT_REVOKE_SOURCE: _SOURCE_APPROVAL, AUDIT_REVOKE_CATALOG: _BUILD_APPROVAL}
    approval = approvals[event_kind]
    request_fingerprint = fingerprint(
        {
            "actor_id": str(request.actor_id),
            "request_id": str(request.request_id),
            "evidence_ref": request.evidence_ref,
            "approval_id": str(approval_id),
            "event_kind": event_kind,
        }
    )
    replayed = await _replayed_event(
        session,
        actor_id=request.actor_id,
        request_id=request.request_id,
        event_kind=event_kind,
        request_fingerprint=request_fingerprint,
    )
    if replayed is not None:
        return {"status": "REPLAYED", "event_kind": event_kind, "audit_id": replayed}

    await _require_user(session, request.actor_id)
    await _require_permission(session, request.actor_id)
    await acquire_catalog_approval_advisory_locks(session, (approval_id,))
    row = (
        await session.execute(select(approval.c.id, approval.c.revoked_at).where(approval.c.id == str(approval_id)))
    ).first()
    if row is None:
        raise CatalogApprovalCommandError("BLOCKED_BY_UNKNOWN_APPROVAL")
    if row[1] is not None:
        # 다른 요청으로 이미 철회된 승인입니다. 철회 사실을 덮어쓰지 않고 현재 상태를 알립니다.
        raise CatalogApprovalCommandError("BLOCKED_BY_ALREADY_REVOKED")

    now = datetime.now(UTC)
    await session.execute(
        approval.update()
        .where(approval.c.id == str(approval_id), approval.c.revoked_at.is_(None))
        .values(revoked_at=now, revoked_by=str(request.actor_id), revoked_reason=REVOCATION_REASON)
    )
    fields: dict[str, object] = {
        "request_id": str(request.request_id),
        "actor_id": str(request.actor_id),
    }
    if event_kind == AUDIT_REVOKE_SOURCE:
        fields["source_approval_id"] = str(approval_id)
        fields["purpose"] = CATALOG_SOURCE_USE_PURPOSE
    else:
        fields["build_approval_id"] = str(approval_id)
    audit_id = await _append_audit(session, event_kind=event_kind, request_fingerprint=request_fingerprint, **fields)
    return {"status": "APPLIED", "event_kind": event_kind, "audit_id": str(audit_id)}


def _artifact_reader(environment: Mapping[str, str]) -> LocalPrivateSourceArtifactReader:
    root_value = environment.get("CATALOG_SOURCE_ARTIFACT_READER_ROOT", "").strip()
    if not root_value:
        raise CatalogApprovalCommandError("BLOCKED_BY_PRODUCT_ARTIFACT")
    try:
        return LocalPrivateSourceArtifactReader(Path(root_value))
    except (OSError, ValueError):
        raise CatalogApprovalCommandError("BLOCKED_BY_PRODUCT_ARTIFACT") from None


async def _run(environment: Mapping[str, str], args: argparse.Namespace) -> dict[str, object]:
    engine = create_async_engine(approval_url(environment), hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await validate_approval_connection(connection)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        request = ApprovalRequest(actor_id=args.actor_id, request_id=args.request_id, evidence_ref=args.evidence_ref)
        async with sessions() as session, session.begin():
            if args.command == "grant-permission":
                return await set_permission(session, request=request, user_id=args.user_id, enabled=True)
            if args.command == "revoke-permission":
                return await set_permission(session, request=request, user_id=args.user_id, enabled=False)
            if args.command == "issue-product-catalog":
                return await issue_product_catalog(
                    session,
                    request=request,
                    artifact_reader=_artifact_reader(environment),
                    target=ProductCatalogTarget(
                        source_snapshot_id=args.source_snapshot_id,
                        ingestion_run_id=args.ingestion_run_id,
                        item_seq=args.item_seq,
                        catalog_version=args.catalog_version,
                        expires_at=args.expires_at,
                    ),
                )
            event_kind = AUDIT_REVOKE_SOURCE if args.command == "revoke-source" else AUDIT_REVOKE_CATALOG
            return await revoke_approval(session, request=request, approval_id=args.approval_id, event_kind=event_kind)
    finally:
        await engine.dispose()


def _expires_at(value: str) -> datetime:
    """만료 시각은 반드시 CLI에서 명시합니다. 기본 만료일을 하드코딩하지 않습니다."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expires-at must be an ISO 8601 timestamp") from None
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("expires-at must include a timezone offset")
    return parsed.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in (
        "grant-permission",
        "revoke-permission",
        "issue-product-catalog",
        "revoke-source",
        "revoke-catalog",
    ):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--actor-id", required=True, type=UUID)
        subparser.add_argument("--request-id", required=True, type=UUID)
        subparser.add_argument("--evidence-ref", required=True, type=str)
        if name.endswith("-permission"):
            subparser.add_argument("--user-id", required=True, type=UUID)
        elif name == "issue-product-catalog":
            subparser.add_argument("--source-snapshot-id", required=True, type=str)
            subparser.add_argument("--ingestion-run-id", required=True, type=str)
            subparser.add_argument("--item-seq", required=True, type=str)
            subparser.add_argument("--catalog-version", required=True, type=str)
            subparser.add_argument("--expires-at", required=True, type=_expires_at)
        else:
            subparser.add_argument("--approval-id", required=True, type=UUID)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.evidence_ref.strip():
        print(json.dumps({"status": "BLOCKED", "blocker_reason": "BLOCKED_BY_EVIDENCE_REF"}))
        return 1
    try:
        result = asyncio.run(_run(os.environ, args))
    except (CatalogApprovalCommandError, ProductSourceBindingError) as exc:
        print(json.dumps({"status": "BLOCKED", "blocker_reason": exc.code}))
        return 1
    except Exception:
        # 저장소·드라이버 예외 원문을 운영 로그로 흘리지 않습니다.
        print(json.dumps({"status": "FAILED", "error": "Catalog approval command failed"}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
