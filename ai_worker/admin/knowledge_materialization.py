"""Admin runner for MFDS Knowledge Materialization.

Binds verified Source Snapshot Members to KnowledgeDocument and KnowledgeChunk
using pre-existing #634 seams and the builder role.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.local_private_source_artifact_finalizer import LocalPrivateSourceArtifactReader
from ai_worker.adapters.sqlalchemy_knowledge_materialization import SqlAlchemyKnowledgeMaterializationRepository
from ai_worker.adapters.sqlalchemy_source_snapshot_repository import SqlAlchemySourceSnapshotRepository
from ai_worker.tasks.rag.knowledge_materialization import (
    CHUNK_POLICY_VERSION,
    SECTION_ORDER,
    KnowledgeDocumentDraft,
    KnowledgeMaterializationError,
    KnowledgeMaterializationFailureReason,
    KnowledgeMaterializationRequest,
    KnowledgeMaterializationResult,
    MaterializationOutcome,
    MaterializationSourceDocument,
    materialize_documents,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SourceSnapshotMemberKind

_ITEM_SEQ_PATTERN = re.compile(r"[0-9]{9}\Z")
_CHECKSUM_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_SECTIONS = frozenset({"EE", "UD", "NB"})
_ISOLATED_PASSWORDS = (
    "DB_PASSWORD",
    "DB_APP_PASSWORD",
    "DB_MIGRATION_PASSWORD",
    "DB_ADMIN_PASSWORD",
    "SOURCE_WRITER_PASSWORD",
    "SOURCE_CLEANUP_EXECUTOR_PASSWORD",
    "CATALOG_WRITER_PASSWORD",
)


@dataclass(frozen=True, slots=True)
class MaterializationRunnerConfig:
    url: URL = field(repr=False)
    builder_user: str
    artifact_root: Path = field(repr=False)

    @classmethod
    def from_environment(cls, env: Mapping[str, str]) -> MaterializationRunnerConfig:
        if any(env.get(key) for key in _ISOLATED_PASSWORDS):
            raise ValueError("Knowledge materialization builder requires an isolated credential environment")

        db_host = env.get("DB_HOST", "").strip()
        db_port_str = env.get("DB_PORT", "").strip()
        db_name = env.get("DB_NAME", "").strip()
        if not db_host or not db_port_str or not db_name:
            raise ValueError("Database endpoint configuration (DB_HOST, DB_PORT, DB_NAME) is incomplete")

        try:
            port = int(db_port_str)
        except ValueError as exc:
            raise ValueError("DB_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("DB_PORT must be between 1 and 65535")

        builder_user = env.get("KNOWLEDGE_INDEX_BUILDER_USER", "").strip()
        builder_password = env.get("KNOWLEDGE_INDEX_BUILDER_PASSWORD", "").strip()
        if not builder_user or not builder_password:
            raise ValueError(
                "Builder credentials (KNOWLEDGE_INDEX_BUILDER_USER, KNOWLEDGE_INDEX_BUILDER_PASSWORD) are incomplete"
            )

        storage_backend = env.get("SOURCE_ARTIFACT_STORAGE_BACKEND", "").strip()
        if storage_backend != "LOCAL_PRIVATE":
            raise ValueError("SOURCE_ARTIFACT_STORAGE_BACKEND must be 'LOCAL_PRIVATE'")

        artifact_root_str = env.get("SOURCE_ARTIFACT_LOCAL_ROOT", "").strip()
        if not artifact_root_str:
            raise ValueError("SOURCE_ARTIFACT_LOCAL_ROOT is required")
        artifact_root = Path(artifact_root_str)
        if not artifact_root.is_absolute():
            raise ValueError("SOURCE_ARTIFACT_LOCAL_ROOT must be an absolute path")

        url = URL.create(
            "postgresql+asyncpg",
            username=builder_user,
            password=builder_password,
            host=db_host,
            port=port,
            database=db_name,
        )
        return cls(
            url=url,
            builder_user=builder_user,
            artifact_root=artifact_root,
        )


async def validate_builder_session(session: AsyncSession, expected_user: str) -> None:
    bind = session.bind
    if bind is not None and bind.dialect.name == "postgresql":
        current_user = await session.scalar(text("SELECT current_user"))
        if current_user != expected_user:
            raise ValueError(f"Session user '{current_user}' does not match configured builder '{expected_user}'")

        unsafe_role = await session.scalar(
            text(
                "SELECT r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls OR r.rolreplication "
                "OR EXISTS (SELECT 1 FROM pg_auth_members WHERE member=r.oid) "
                "OR EXISTS (SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=r.oid) "
                "OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='public' AND nspowner=r.oid) "
                "OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relowner=r.oid) "
                "OR has_schema_privilege(current_user, 'public', 'CREATE') "
                "FROM pg_roles r WHERE r.rolname=current_user"
            )
        )
        if unsafe_role is not False:
            raise ValueError(
                "Knowledge materialization builder requires a non-owner role without administrative privileges"
            )


async def discover_authoritative_members(
    session: AsyncSession,
    *,
    snapshot_id: UUID,
    expected_item_seq: str,
) -> tuple[UUID, ...]:
    snapshot_repo = SqlAlchemySourceSnapshotRepository(session)
    bindings = await snapshot_repo.get_snapshot_member_bindings(snapshot_id=snapshot_id)
    if not bindings:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)

    observed_sections: dict[str, UUID] = {}

    for binding in bindings:
        if binding.source_snapshot_id != snapshot_id:
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
        if binding.member_kind is not SourceSnapshotMemberKind.ARTIFACT:
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)

        parts = binding.locator.split("/")
        if len(parts) != 3 or parts[0] != "mfds-label" or parts[1] != expected_item_seq:
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
        section = parts[2]
        if section not in _ALLOWED_SECTIONS:
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)
        if section in observed_sections:
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.REQUEST_INVALID)
        observed_sections[section] = binding.source_snapshot_member_id

    if set(observed_sections.keys()) != _ALLOWED_SECTIONS:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.REQUEST_INVALID)

    return tuple(observed_sections[section] for section in SECTION_ORDER if section in observed_sections)


def build_sanitized_summary(
    result: KnowledgeMaterializationResult,
    *,
    post_commit_audit_passed: bool,
    exact_replay_verified: bool | None,
) -> dict[str, Any]:
    receipt = result.receipt
    doc_ids = [str(d.knowledge_document_id) for d in receipt.documents]
    chunk_ids = [str(c.knowledge_chunk_id) for d in receipt.documents for c in d.chunks]
    doc_hashes = [d.document_content_hash for d in receipt.documents]
    chunk_hashes = [c.content_hash for d in receipt.documents for c in d.chunks]

    return {
        "execution_status": "SUCCESS",
        "outcome": result.outcome.value,
        "snapshot_id": str(receipt.snapshot_id),
        "item_seq": receipt.item_seq,
        "source_code": receipt.source_code,
        "source_version": receipt.source_version,
        "snapshot_canonical_checksum": receipt.snapshot_canonical_checksum,
        "canonicalization_spec_version": receipt.canonicalization_spec_version,
        "chunk_policy_version": receipt.chunk_policy_version,
        "document_count": len(receipt.documents),
        "chunk_count": len(chunk_ids),
        "knowledge_document_ids": doc_ids,
        "knowledge_chunk_ids": chunk_ids,
        "document_content_hashes": doc_hashes,
        "chunk_content_hashes": chunk_hashes,
        "post_commit_audit_passed": post_commit_audit_passed,
        "exact_replay_verified": exact_replay_verified,
    }


def _validate_source_documents(
    source_docs: Sequence[MaterializationSourceDocument],
    member_ids: Sequence[UUID],
    expected_canonical_checksum: str,
) -> None:
    if len(source_docs) != len(member_ids):
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)

    first_doc = source_docs[0]
    if first_doc.snapshot_verification_status != "CURRENT":
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_NOT_ELIGIBLE)
    if first_doc.canonical_checksum != expected_canonical_checksum:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.ARTIFACT_INTEGRITY_MISMATCH)
    if first_doc.source_code != "MFDS":
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.SOURCE_BINDING_INVALID)


async def _verify_replay_consistency(
    mat_repo: SqlAlchemyKnowledgeMaterializationRepository,
    *,
    request: KnowledgeMaterializationRequest,
    drafts: Sequence[KnowledgeDocumentDraft],
    source_docs: Sequence[MaterializationSourceDocument],
    original_result: KnowledgeMaterializationResult,
) -> bool:
    second_result = await mat_repo.persist_materialization(
        request=request,
        drafts=drafts,
        source_docs=source_docs,
    )
    if second_result.outcome != MaterializationOutcome.EXACT_REPLAY:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.CONTENT_CONFLICT)
    if second_result.receipt != original_result.receipt:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.RECEIPT_MISMATCH)
    return True


async def execute_materialization(
    *,
    config: MaterializationRunnerConfig,
    snapshot_id: UUID,
    expected_item_seq: str,
    expected_canonical_checksum: str,
    verify_replay: bool = False,
) -> dict[str, Any]:
    if _ITEM_SEQ_PATTERN.fullmatch(expected_item_seq) is None:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.REQUEST_INVALID)
    if _CHECKSUM_PATTERN.fullmatch(expected_canonical_checksum) is None:
        raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.REQUEST_INVALID)

    engine = create_async_engine(config.url, hide_parameters=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    try:
        async with session_factory() as session:
            await validate_builder_session(session, config.builder_user)
            member_ids = await discover_authoritative_members(
                session,
                snapshot_id=snapshot_id,
                expected_item_seq=expected_item_seq,
            )

        mat_repo = SqlAlchemyKnowledgeMaterializationRepository(session_factory)
        source_docs = await mat_repo.fetch_source_documents(snapshot_id, member_ids)
        _validate_source_documents(source_docs, member_ids, expected_canonical_checksum)

        try:
            artifact_reader = LocalPrivateSourceArtifactReader(config.artifact_root)
        except ValueError as exc:
            raise KnowledgeMaterializationError(KnowledgeMaterializationFailureReason.DEPENDENCY_ERROR) from exc

        request = KnowledgeMaterializationRequest(
            snapshot_id=snapshot_id,
            member_ids=member_ids,
            expected_item_seq=expected_item_seq,
            chunk_policy_version=CHUNK_POLICY_VERSION,
        )
        drafts = materialize_documents(
            request=request,
            source_docs=source_docs,
            artifact_reader=artifact_reader,
        )

        result = await mat_repo.persist_materialization(
            request=request,
            drafts=drafts,
            source_docs=source_docs,
        )

        audit_ok = await mat_repo.audit_post_commit(
            receipt=result.receipt,
            request=request,
            drafts=drafts,
            source_docs=source_docs,
        )
        if not audit_ok:
            return {
                "execution_status": "FAILED",
                "failure_reason": "POST_COMMIT_AUDIT_FAILED",
                "snapshot_id": str(snapshot_id),
                "item_seq": expected_item_seq,
            }

        exact_replay_verified: bool | None = None
        if verify_replay:
            exact_replay_verified = await _verify_replay_consistency(
                mat_repo,
                request=request,
                drafts=drafts,
                source_docs=source_docs,
                original_result=result,
            )

        return build_sanitized_summary(
            result,
            post_commit_audit_passed=audit_ok,
            exact_replay_verified=exact_replay_verified,
        )

    finally:
        await engine.dispose()


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize verified MFDS Source Snapshot into KnowledgeDocument and KnowledgeChunk"
    )
    parser.add_argument("snapshot_id", type=UUID, help="Target Source Snapshot UUID")
    parser.add_argument("--expected-item-seq", required=True, help="Expected 9-digit MFDS item sequence")
    parser.add_argument("--expected-canonical-checksum", required=True, help="Expected SHA-256 canonical checksum")
    parser.add_argument(
        "--verify-replay",
        action="store_true",
        default=False,
        help="Verify exact replay in an immediately following execution",
    )
    return parser.parse_args(args)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = MaterializationRunnerConfig.from_environment(os.environ)
        summary = asyncio.run(
            execute_materialization(
                config=config,
                snapshot_id=args.snapshot_id,
                expected_item_seq=args.expected_item_seq,
                expected_canonical_checksum=args.expected_canonical_checksum,
                verify_replay=args.verify_replay,
            )
        )
    except KnowledgeMaterializationError as exc:
        print(
            json.dumps({"execution_status": "FAILED", "failure_reason": exc.reason.value}, indent=2),
            file=sys.stderr,
        )
        return 1
    except ValueError:
        print(
            json.dumps({"execution_status": "FAILED", "failure_reason": "CONFIG_INVALID"}, indent=2),
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(
            json.dumps({"execution_status": "FAILED", "failure_reason": "UNEXPECTED_ERROR"}, indent=2),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(summary, indent=2))
    return 0 if summary.get("execution_status") == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
