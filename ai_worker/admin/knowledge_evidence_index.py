"""Admin runner for MFDS Knowledge Evidence Index build.

Connects materialized KnowledgeChunk records to the production Knowledge Evidence Index
using pre-existing #178/#634 seams and the builder role.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import Integer, String, column, select, table, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ai_worker.adapters.openai_text_embedding import OpenAITextEmbeddingAdapter
from ai_worker.adapters.sqlalchemy_knowledge_evidence_index import (
    SqlAlchemyKnowledgeEvidenceIndexRepository,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, SensitiveText
from ai_worker.tasks.rag.knowledge_evidence_index import (
    DistanceMetric,
    KnowledgeChunkIdentity,
    KnowledgeEvidenceIndexFailureReason,
    KnowledgeEvidenceIndexRepository,
    KnowledgeEvidenceIndexValidationError,
    KnowledgeIndexBuildRequest,
    KnowledgeIndexMemberDraft,
    KnowledgeIndexReceipt,
    SensitiveEvidenceText,
    build_knowledge_evidence_index,
    canonical_embedding_sha256,
)
from ai_worker.tasks.rag.text_embedding import (
    TextEmbeddingPort,
    TextEmbeddingSuccess,
)

# --------------------------------------------------------------------------------------
# Internal Read-Only Table Definitions (Runner Queries)
# --------------------------------------------------------------------------------------

_SOURCE = table(
    "rag_source",
    column("id", String(36)),
    column("source_code", String(100)),
    column("lifecycle_status", String(20)),
)
_ENDPOINT = table(
    "rag_source_endpoint",
    column("id", String(36)),
    column("source_id", String(36)),
    column("lifecycle_status", String(20)),
    column("runtime_status", String(20)),
    column("acquisition_status", String(20)),
)
_OPERATION = table(
    "rag_source_operation",
    column("id", String(36)),
    column("endpoint_id", String(36)),
    column("runtime_status", String(20)),
    column("acquisition_status", String(20)),
)
_SNAPSHOT = table(
    "rag_source_snapshot",
    column("id", String(36)),
    column("operation_id", String(36)),
    column("source_version", String(200)),
    column("canonical_checksum", String(64)),
    column("verification_status", String(20)),
)
_SNAPSHOT_MEMBER = table(
    "rag_source_snapshot_member",
    column("id", String(36)),
    column("source_snapshot_id", String(36)),
    column("member_kind", String(30)),
    column("locator", String(500)),
    column("content_sha256", String(64)),
)
_DOCUMENT = table(
    "knowledge_document",
    column("id", String(36)),
    column("source_snapshot_member_id", String(36)),
    column("record_contract_version", String(40)),
    column("document_status", String(20)),
    column("external_document_id", String(300)),
    column("document_content_hash", String(64)),
)
_CHUNK = table(
    "knowledge_chunk",
    column("id", String(36)),
    column("knowledge_document_id", String(36)),
    column("chunk_index", Integer),
    column("chunk_text", String),
    column("content_hash", String(64)),
    column("normalization_version", String(100)),
)
_INDEX = table(
    "rag_knowledge_index",
    column("id", String(36)),
    column("index_code", String(120)),
    column("index_version", String(80)),
    column("corpus_manifest_hash", String(64)),
    column("embedding_manifest_hash", String(64)),
    column("index_configuration_hash", String(64)),
    column("embedding_model_ref", String(255)),
    column("embedding_model_version", String(80)),
    column("embedding_dimension", Integer),
    column("distance_metric", String(20)),
    column("member_count", Integer),
)
_INDEX_MEMBER = table(
    "rag_knowledge_index_member",
    column("id", String(36)),
    column("knowledge_index_id", String(36)),
    column("knowledge_chunk_id", String(36)),
    column("source_snapshot_id", String(36)),
    column("source_snapshot_member_id", String(36)),
    column("source_code", String(100)),
    column("source_version", String(200)),
    column("canonical_checksum", String(64)),
    column("external_document_id", String(300)),
    column("chunk_index", Integer),
    column("content_hash", String(64)),
    column("embedding", VECTOR()),
    column("embedding_sha256", String(64)),
    column("member_order", Integer),
)

# --------------------------------------------------------------------------------------
# Confirmed Production Constants
# --------------------------------------------------------------------------------------

EXPECTED_SOURCE_CODE = "MFDS_PRODUCT_LABEL"
EXPECTED_INDEX_CODE = "mfds-product-label-evidence"
EXPECTED_INDEX_VERSION = "v1.0.0"
EXPECTED_MODEL_REF = "openai:text-embedding-3-large"
EXPECTED_MODEL_VERSION = "text-embedding-3-large"
EXPECTED_DIMENSION = 1536
EXPECTED_DISTANCE_METRIC = DistanceMetric.COSINE
SECTION_ORDER = ("EE", "UD", "NB")

NOVASC_ITEM_SEQ = "200610660"
NOVASC_SNAPSHOT_ID = UUID("073ee706-d039-49b5-8ca5-e92cd021f087")
NOVASC_CANONICAL_CHECKSUM = "a5df76494560f07db5f919b5c37dce4c681cbc214b27450466348a1849039cd3"
NOVASC_SOURCE_VERSION = (
    "api:2026-09-16T01:16:39.187000Z:a5df76494560f07db5f919b5c37dce4c681cbc214b27450466348a1849039cd3"
)

_ITEM_SEQ_PATTERN = re.compile(r"^[0-9]{9}$")
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ISOLATED_PASSWORDS = (
    "DB_PASSWORD",
    "DB_APP_PASSWORD",
    "DB_MIGRATION_PASSWORD",
    "DB_ADMIN_PASSWORD",
    "SOURCE_WRITER_PASSWORD",
    "SOURCE_CLEANUP_EXECUTOR_PASSWORD",
    "CATALOG_WRITER_PASSWORD",
)


# --------------------------------------------------------------------------------------
# Runner Failure Reasons & Exception
# --------------------------------------------------------------------------------------


class KnowledgeEvidenceIndexRunnerFailureReason(StrEnum):
    CONFIG_INVALID = "CONFIG_INVALID"
    EMBEDDING_CREDENTIAL_MISSING = "EMBEDDING_CREDENTIAL_MISSING"
    BLOCKED_BY_EMBEDDING_ADAPTER_ARTIFACT_IDENTITY = "BLOCKED_BY_EMBEDDING_ADAPTER_ARTIFACT_IDENTITY"
    PRECHECK_FAILED = "PRECHECK_FAILED"
    REQUEST_INVALID = "REQUEST_INVALID"
    SOURCE_BINDING_INVALID = "SOURCE_BINDING_INVALID"
    CONTENT_HASH_MISMATCH = "CONTENT_HASH_MISMATCH"
    EMBEDDING_INVALID = "EMBEDDING_INVALID"
    RECEIPT_MISMATCH = "RECEIPT_MISMATCH"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


class KnowledgeEvidenceIndexRunnerError(Exception):
    def __init__(
        self,
        reason: KnowledgeEvidenceIndexRunnerFailureReason | KnowledgeEvidenceIndexFailureReason | str,
    ) -> None:
        if isinstance(reason, KnowledgeEvidenceIndexRunnerFailureReason):
            self.reason = reason
        elif isinstance(reason, KnowledgeEvidenceIndexFailureReason):
            self.reason = KnowledgeEvidenceIndexRunnerFailureReason(reason.value)
        else:
            try:
                self.reason = KnowledgeEvidenceIndexRunnerFailureReason(reason)
            except ValueError:
                self.reason = KnowledgeEvidenceIndexRunnerFailureReason.DEPENDENCY_ERROR
        super().__init__(self.reason.value)


# --------------------------------------------------------------------------------------
# Runner Configuration
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KnowledgeEvidenceIndexRunnerConfig:
    url: URL = field(repr=False)
    builder_user: str
    openai_api_key: str | None = field(default=None, repr=False)

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str],
        *,
        require_openai_key: bool = True,
    ) -> KnowledgeEvidenceIndexRunnerConfig:
        for key in _ISOLATED_PASSWORDS:
            if key in env and env[key]:
                raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.CONFIG_INVALID)

        db_host = env.get("DB_HOST", "").strip()
        db_port_str = env.get("DB_PORT", "").strip()
        db_name = env.get("DB_NAME", "").strip()
        if not db_host or not db_port_str or not db_name:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.CONFIG_INVALID)

        try:
            port = int(db_port_str)
        except ValueError as exc:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.CONFIG_INVALID) from exc
        if not 1 <= port <= 65535:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.CONFIG_INVALID)

        builder_user = env.get("KNOWLEDGE_INDEX_BUILDER_USER", "").strip()
        builder_password = env.get("KNOWLEDGE_INDEX_BUILDER_PASSWORD", "").strip()
        if not builder_user or not builder_password:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.CONFIG_INVALID)

        openai_api_key = env.get("OPENAI_API_KEY", "").strip() or None
        if require_openai_key and not openai_api_key:
            raise KnowledgeEvidenceIndexRunnerError(
                KnowledgeEvidenceIndexRunnerFailureReason.EMBEDDING_CREDENTIAL_MISSING
            )

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
            openai_api_key=openai_api_key,
        )


# --------------------------------------------------------------------------------------
# Authoritative Discovered Chunk Model
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthoritativeDiscoveredChunk:
    knowledge_chunk_id: UUID
    section: str
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID
    source_code: str
    source_version: str
    canonical_checksum: str
    external_document_id: str
    chunk_index: int
    content_hash: str
    locator: str = field(repr=False)
    chunk_text: str = field(repr=False)
    normalization_version: str


# --------------------------------------------------------------------------------------
# Builder Session Verification
# --------------------------------------------------------------------------------------


async def validate_builder_session(session: AsyncSession, expected_user: str) -> None:
    bind = session.bind
    if bind is not None and bind.dialect.name == "postgresql":
        current_user = await session.scalar(text("SELECT current_user"))
        if current_user != expected_user:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.CONFIG_INVALID)

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
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.CONFIG_INVALID)


# --------------------------------------------------------------------------------------
# Authoritative Preflight Query & Validation
# --------------------------------------------------------------------------------------


async def preflight_authoritative_corpus(  # noqa: C901
    session: AsyncSession,
    *,
    snapshot_id: UUID,
    expected_item_seq: str,
    expected_canonical_checksum: str,
    expected_source_version: str | None = None,
) -> tuple[AuthoritativeDiscoveredChunk, ...]:
    """Authoritatively queries and verifies Snapshot to KnowledgeChunk binding before embedding."""
    source_chain = (
        _SOURCE.join(_ENDPOINT, _ENDPOINT.c.source_id == _SOURCE.c.id)
        .join(_OPERATION, _OPERATION.c.endpoint_id == _ENDPOINT.c.id)
        .join(_SNAPSHOT, _SNAPSHOT.c.operation_id == _OPERATION.c.id)
        .join(_SNAPSHOT_MEMBER, _SNAPSHOT_MEMBER.c.source_snapshot_id == _SNAPSHOT.c.id)
        .join(_DOCUMENT, _DOCUMENT.c.source_snapshot_member_id == _SNAPSHOT_MEMBER.c.id)
        .join(_CHUNK, _CHUNK.c.knowledge_document_id == _DOCUMENT.c.id)
    )

    query = (
        select(
            _SOURCE.c.source_code,
            _SOURCE.c.lifecycle_status.label("source_lifecycle"),
            _ENDPOINT.c.lifecycle_status.label("endpoint_lifecycle"),
            _ENDPOINT.c.runtime_status.label("endpoint_runtime"),
            _ENDPOINT.c.acquisition_status.label("endpoint_acquisition"),
            _OPERATION.c.runtime_status.label("operation_runtime"),
            _OPERATION.c.acquisition_status.label("operation_acquisition"),
            _SNAPSHOT.c.id.label("snapshot_id"),
            _SNAPSHOT.c.source_version,
            _SNAPSHOT.c.canonical_checksum,
            _SNAPSHOT.c.verification_status,
            _SNAPSHOT_MEMBER.c.id.label("member_id"),
            _SNAPSHOT_MEMBER.c.member_kind,
            _SNAPSHOT_MEMBER.c.locator,
            _SNAPSHOT_MEMBER.c.content_sha256.label("member_content_sha256"),
            _DOCUMENT.c.id.label("document_id"),
            _DOCUMENT.c.record_contract_version,
            _DOCUMENT.c.document_status,
            _DOCUMENT.c.external_document_id,
            _DOCUMENT.c.document_content_hash,
            _CHUNK.c.id.label("chunk_id"),
            _CHUNK.c.chunk_index,
            _CHUNK.c.chunk_text,
            _CHUNK.c.content_hash,
            _CHUNK.c.normalization_version,
        )
        .select_from(source_chain)
        .where(_SNAPSHOT.c.id == str(snapshot_id))
    )

    rows = (await session.execute(query)).mappings().all()
    if not rows:
        raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)

    seen_sections: dict[str, AuthoritativeDiscoveredChunk] = {}

    for row in rows:
        # Snapshot verification checks
        if row["verification_status"] != "CURRENT":
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)
        if row["canonical_checksum"] != expected_canonical_checksum:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)
        if expected_source_version is not None and row["source_version"] != expected_source_version:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)

        # Source / Endpoint / Operation checks
        if row["source_code"] != EXPECTED_SOURCE_CODE or row["source_lifecycle"] != "ACTIVE":
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)
        if (
            row["endpoint_lifecycle"] != "VERIFIED"
            or row["endpoint_runtime"] != "ENABLED"
            or row["endpoint_acquisition"] != "APPROVED"
        ):
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)
        if row["operation_runtime"] != "ENABLED" or row["operation_acquisition"] != "APPROVED":
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)

        # Member checks
        if row["member_kind"] != "ARTIFACT":
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)

        parts = row["locator"].split("/")
        if len(parts) != 3 or parts[0] != "mfds-label" or parts[1] != expected_item_seq:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)
        section = parts[2]
        if section not in SECTION_ORDER:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)
        if section in seen_sections:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.REQUEST_INVALID)

        # Document checks
        if row["record_contract_version"] != "KNOWLEDGE_EVIDENCE_V1" or row["document_status"] != "ACTIVE":
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)
        if row["document_content_hash"] != row["member_content_sha256"]:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)
        if row["external_document_id"] != f"mfds-label:{expected_item_seq}:{section}":
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)

        # Chunk checks
        if row["chunk_index"] != 0:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)
        if not row["normalization_version"] or not str(row["normalization_version"]).strip():
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)

        chunk_text = str(row["chunk_text"])
        computed_hash = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
        if computed_hash != row["content_hash"]:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.CONTENT_HASH_MISMATCH)
        if computed_hash != row["document_content_hash"]:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID)

        chunk_id = UUID(str(row["chunk_id"]))

        seen_sections[section] = AuthoritativeDiscoveredChunk(
            knowledge_chunk_id=chunk_id,
            section=section,
            source_snapshot_id=UUID(str(row["snapshot_id"])),
            source_snapshot_member_id=UUID(str(row["member_id"])),
            source_code=str(row["source_code"]),
            source_version=str(row["source_version"]),
            canonical_checksum=str(row["canonical_checksum"]),
            external_document_id=str(row["external_document_id"]),
            chunk_index=int(row["chunk_index"]),
            content_hash=str(row["content_hash"]),
            locator=str(row["locator"]),
            chunk_text=chunk_text,
            normalization_version=str(row["normalization_version"]),
        )

    if set(seen_sections.keys()) != set(SECTION_ORDER):
        raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.REQUEST_INVALID)

    return tuple(seen_sections[sec] for sec in SECTION_ORDER)


# --------------------------------------------------------------------------------------
# Existing Index Reuse & Repository Re-validation
# --------------------------------------------------------------------------------------


async def check_and_revalidate_existing_index(  # noqa: C901
    session: AsyncSession | None,
    *,
    repository: KnowledgeEvidenceIndexRepository,
    discovered_chunks: tuple[AuthoritativeDiscoveredChunk, ...],
    existing_embeddings_override: tuple[tuple[float, ...], ...] | None = None,
) -> KnowledgeIndexReceipt | None:
    """Read-only check for existing Index, re-passing it through repository persistence on match."""
    if existing_embeddings_override is not None:
        embeddings = existing_embeddings_override
    else:
        if session is None:
            return None
        index_row = (
            (
                await session.execute(
                    select(_INDEX).where(
                        _INDEX.c.index_code == EXPECTED_INDEX_CODE,
                        _INDEX.c.index_version == EXPECTED_INDEX_VERSION,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )

        if index_row is None:
            return None
        if (
            int(index_row["member_count"]) != len(discovered_chunks)
            or str(index_row["embedding_model_ref"]) != EXPECTED_MODEL_REF
            or str(index_row["embedding_model_version"]) != EXPECTED_MODEL_VERSION
            or int(index_row["embedding_dimension"]) != EXPECTED_DIMENSION
            or str(index_row["distance_metric"]) != EXPECTED_DISTANCE_METRIC.value
        ):
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.VERSION_CONFLICT)

        member_rows = (
            (
                await session.execute(
                    select(_INDEX_MEMBER)
                    .where(_INDEX_MEMBER.c.knowledge_index_id == str(index_row["id"]))
                    .order_by(_INDEX_MEMBER.c.member_order)
                )
            )
            .mappings()
            .all()
        )

        if len(member_rows) != len(discovered_chunks):
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.VERSION_CONFLICT)

        member_row_by_chunk_id = {UUID(str(row["knowledge_chunk_id"])): row for row in member_rows}
        if len(member_row_by_chunk_id) != len(discovered_chunks):
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.VERSION_CONFLICT)

        embeddings_list: list[tuple[float, ...]] = []
        for chunk in discovered_chunks:
            member_row = member_row_by_chunk_id.get(chunk.knowledge_chunk_id)
            if member_row is None:
                raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.VERSION_CONFLICT)
            if (
                UUID(str(member_row["source_snapshot_id"])) != chunk.source_snapshot_id
                or UUID(str(member_row["source_snapshot_member_id"])) != chunk.source_snapshot_member_id
                or str(member_row["source_code"]) != chunk.source_code
                or str(member_row["source_version"]) != chunk.source_version
                or str(member_row["canonical_checksum"]) != chunk.canonical_checksum
                or str(member_row["external_document_id"]) != chunk.external_document_id
                or int(member_row["chunk_index"]) != chunk.chunk_index
                or str(member_row["content_hash"]) != chunk.content_hash
            ):
                raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.VERSION_CONFLICT)

            raw_emb = tuple(float(x) for x in member_row["embedding"])
            if len(raw_emb) != EXPECTED_DIMENSION:
                raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.EMBEDDING_INVALID)
            if canonical_embedding_sha256(raw_emb) != str(member_row["embedding_sha256"]):
                raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.VERSION_CONFLICT)
            embeddings_list.append(raw_emb)
        embeddings = tuple(embeddings_list)

    # Reconstruct request from authoritative chunk text and persisted embeddings
    try:
        drafts = tuple(
            KnowledgeIndexMemberDraft(
                identity=KnowledgeChunkIdentity(
                    knowledge_chunk_id=chunk.knowledge_chunk_id,
                    source_snapshot_id=chunk.source_snapshot_id,
                    source_snapshot_member_id=chunk.source_snapshot_member_id,
                    source_code=chunk.source_code,
                    source_version=chunk.source_version,
                    canonical_checksum=chunk.canonical_checksum,
                    external_document_id=chunk.external_document_id,
                    chunk_index=chunk.chunk_index,
                    content_hash=chunk.content_hash,
                    locator=chunk.locator,
                ),
                content_text=SensitiveEvidenceText(chunk.chunk_text),
                embedding=emb,
            )
            for chunk, emb in zip(discovered_chunks, embeddings, strict=True)
        )
        reconstructed_request = KnowledgeIndexBuildRequest(
            index_code=EXPECTED_INDEX_CODE,
            index_version=EXPECTED_INDEX_VERSION,
            embedding_model_ref=EXPECTED_MODEL_REF,
            embedding_model_version=EXPECTED_MODEL_VERSION,
            embedding_dimension=EXPECTED_DIMENSION,
            distance_metric=EXPECTED_DISTANCE_METRIC,
            members=drafts,
        )
    except KnowledgeEvidenceIndexValidationError as exc:
        raise KnowledgeEvidenceIndexRunnerError(exc.reason) from exc

    # Pass reconstructed request through repository validation without provider calls
    try:
        receipt = await build_knowledge_evidence_index(
            reconstructed_request,
            repository=repository,
        )
    except KnowledgeEvidenceIndexValidationError as exc:
        raise KnowledgeEvidenceIndexRunnerError(exc.reason) from exc
    except Exception as exc:
        raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.DEPENDENCY_ERROR) from exc

    return receipt


# --------------------------------------------------------------------------------------
# Sanitized Summary Builder
# --------------------------------------------------------------------------------------


def build_sanitized_summary(
    receipt: KnowledgeIndexReceipt,
    *,
    outcome: str,
    snapshot_id: UUID,
    source_code: str,
    post_persist_readback_passed: bool,
    exact_replay_verified: bool | None,
    provider_call_count: int,
) -> dict[str, Any]:
    return {
        "execution_status": "SUCCESS",
        "outcome": outcome,
        "index_code": receipt.index_code,
        "index_version": receipt.index_version,
        "source_snapshot_id": str(snapshot_id),
        "source_code": source_code,
        "member_count": receipt.member_count,
        "embedding_model_ref": receipt.embedding_model_ref,
        "embedding_model_version": receipt.embedding_model_version,
        "embedding_dimension": receipt.embedding_dimension,
        "distance_metric": receipt.distance_metric.value,
        "corpus_manifest_hash": receipt.corpus_manifest_hash,
        "embedding_manifest_hash": receipt.embedding_manifest_hash,
        "index_configuration_hash": receipt.index_configuration_hash,
        "post_persist_readback_passed": post_persist_readback_passed,
        "exact_replay_verified": exact_replay_verified,
        "provider_call_count": provider_call_count,
    }


# --------------------------------------------------------------------------------------
# Core Execution Function
# --------------------------------------------------------------------------------------


async def execute_knowledge_evidence_index_build(  # noqa: C901
    *,
    config: KnowledgeEvidenceIndexRunnerConfig,
    snapshot_id: UUID,
    expected_item_seq: str,
    expected_canonical_checksum: str,
    expected_source_version: str | None = None,
    expected_embedding_adapter_ref: str | None = None,
    verify_replay: bool = False,
    preflight_chunks_override: tuple[AuthoritativeDiscoveredChunk, ...] | None = None,
    preflight_failure_reason: KnowledgeEvidenceIndexRunnerFailureReason | None = None,
    repository_override: KnowledgeEvidenceIndexRepository | None = None,
    embedding_port_override: TextEmbeddingPort | None = None,
    existing_embeddings_override: tuple[tuple[float, ...], ...] | None = None,
    session_factory_override: Any | None = None,
) -> dict[str, Any]:
    if _ITEM_SEQ_PATTERN.fullmatch(expected_item_seq) is None:
        raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.REQUEST_INVALID)
    if _CHECKSUM_PATTERN.fullmatch(expected_canonical_checksum) is None:
        raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.REQUEST_INVALID)

    session_factory = None
    engine = None
    if session_factory_override is not None:
        session_factory = session_factory_override
    elif repository_override is None or preflight_chunks_override is None:
        engine = create_async_engine(config.url, hide_parameters=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)

    try:
        # Step 1: Authoritative Preflight
        if preflight_failure_reason is not None:
            raise KnowledgeEvidenceIndexRunnerError(preflight_failure_reason)

        if preflight_chunks_override is not None:
            discovered_chunks = preflight_chunks_override
            # Check length constraint
            if len(discovered_chunks) != len(SECTION_ORDER):
                raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.REQUEST_INVALID)
            for c in discovered_chunks:
                if c.source_code != EXPECTED_SOURCE_CODE:
                    raise KnowledgeEvidenceIndexRunnerError(
                        KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID
                    )
                if c.canonical_checksum != expected_canonical_checksum:
                    raise KnowledgeEvidenceIndexRunnerError(
                        KnowledgeEvidenceIndexRunnerFailureReason.SOURCE_BINDING_INVALID
                    )
                computed_hash = hashlib.sha256(c.chunk_text.encode("utf-8")).hexdigest()
                if computed_hash != c.content_hash:
                    raise KnowledgeEvidenceIndexRunnerError(
                        KnowledgeEvidenceIndexRunnerFailureReason.CONTENT_HASH_MISMATCH
                    )
        else:
            assert session_factory is not None
            async with session_factory() as session:
                await validate_builder_session(session, config.builder_user)
                discovered_chunks = await preflight_authoritative_corpus(
                    session,
                    snapshot_id=snapshot_id,
                    expected_item_seq=expected_item_seq,
                    expected_canonical_checksum=expected_canonical_checksum,
                    expected_source_version=expected_source_version,
                )

        if repository_override is not None:
            repository = repository_override
        else:
            if session_factory is None:
                engine = create_async_engine(config.url, hide_parameters=True)
                session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            repository = SqlAlchemyKnowledgeEvidenceIndexRepository(session_factory)

        # Step 2: Check Existing Index for EXACT_REUSE
        existing_receipt = None
        if existing_embeddings_override is not None:
            existing_receipt = await check_and_revalidate_existing_index(
                None,
                repository=repository,
                discovered_chunks=discovered_chunks,
                existing_embeddings_override=existing_embeddings_override,
            )
        elif session_factory is not None:
            async with session_factory() as session:
                existing_receipt = await check_and_revalidate_existing_index(
                    session,
                    repository=repository,
                    discovered_chunks=discovered_chunks,
                    existing_embeddings_override=None,
                )

        if existing_receipt is not None:
            return build_sanitized_summary(
                existing_receipt,
                outcome="EXACT_REUSE",
                snapshot_id=snapshot_id,
                source_code=discovered_chunks[0].source_code,
                post_persist_readback_passed=True,
                exact_replay_verified=None,
                provider_call_count=0,
            )

        # Step 3: Fresh Build - Resolve Embedding Port
        if embedding_port_override is not None:
            port = embedding_port_override
        else:
            if not config.openai_api_key:
                raise KnowledgeEvidenceIndexRunnerError(
                    KnowledgeEvidenceIndexRunnerFailureReason.EMBEDDING_CREDENTIAL_MISSING
                )
            if not expected_embedding_adapter_ref:
                raise KnowledgeEvidenceIndexRunnerError(
                    KnowledgeEvidenceIndexRunnerFailureReason.BLOCKED_BY_EMBEDDING_ADAPTER_ARTIFACT_IDENTITY
                )

            ref_clean = expected_embedding_adapter_ref.strip()
            if (
                len(ref_clean) != 64
                or ref_clean == "0" * 64
                or ref_clean == "e" * 64
                or not all(c in "0123456789abcdefABCDEF" for c in ref_clean)
            ):
                raise KnowledgeEvidenceIndexRunnerError(
                    KnowledgeEvidenceIndexRunnerFailureReason.BLOCKED_BY_EMBEDDING_ADAPTER_ARTIFACT_IDENTITY
                )

            from openai import AsyncOpenAI

            port = OpenAITextEmbeddingAdapter(
                client=AsyncOpenAI(api_key=config.openai_api_key),
                adapter_artifact_ref=ImmutableArtifactRef(
                    "openai-text-embedding-adapter",
                    "1.0.0",
                    ref_clean.lower(),
                ),
            )

        # Step 4: Execute Embeddings for all 3 chunks
        member_drafts = []
        for chunk in discovered_chunks:
            embed_res = await port.embed(
                SensitiveText(chunk.chunk_text),
                model_ref=EXPECTED_MODEL_REF,
                model_version=EXPECTED_MODEL_VERSION,
                dimension=EXPECTED_DIMENSION,
            )
            if not isinstance(embed_res, TextEmbeddingSuccess):
                raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.EMBEDDING_INVALID)
            vector = embed_res.embedding.reveal()
            if len(vector) != EXPECTED_DIMENSION:
                raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.EMBEDDING_INVALID)

            identity = KnowledgeChunkIdentity(
                knowledge_chunk_id=chunk.knowledge_chunk_id,
                source_snapshot_id=chunk.source_snapshot_id,
                source_snapshot_member_id=chunk.source_snapshot_member_id,
                source_code=chunk.source_code,
                source_version=chunk.source_version,
                canonical_checksum=chunk.canonical_checksum,
                external_document_id=chunk.external_document_id,
                chunk_index=chunk.chunk_index,
                content_hash=chunk.content_hash,
                locator=chunk.locator,
            )
            draft = KnowledgeIndexMemberDraft(
                identity=identity,
                content_text=SensitiveEvidenceText(chunk.chunk_text),
                embedding=vector,
            )
            member_drafts.append(draft)

        build_request = KnowledgeIndexBuildRequest(
            index_code=EXPECTED_INDEX_CODE,
            index_version=EXPECTED_INDEX_VERSION,
            embedding_model_ref=EXPECTED_MODEL_REF,
            embedding_model_version=EXPECTED_MODEL_VERSION,
            embedding_dimension=EXPECTED_DIMENSION,
            distance_metric=EXPECTED_DISTANCE_METRIC,
            members=tuple(member_drafts),
        )

        # Step 5: Persist Index Build
        try:
            receipt = await build_knowledge_evidence_index(
                build_request,
                repository=repository,
            )
        except KnowledgeEvidenceIndexValidationError as exc:
            raise KnowledgeEvidenceIndexRunnerError(exc.reason) from exc
        except Exception as exc:
            raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.DEPENDENCY_ERROR) from exc

        # Step 6: Post-persist read-back verification
        post_persist_readback_passed = True
        if repository_override is None and session_factory is not None:
            async with session_factory() as session:
                idx_row = (
                    (
                        await session.execute(
                            select(_INDEX).where(
                                _INDEX.c.index_code == EXPECTED_INDEX_CODE,
                                _INDEX.c.index_version == EXPECTED_INDEX_VERSION,
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if (
                    idx_row is None
                    or str(idx_row["corpus_manifest_hash"]) != receipt.corpus_manifest_hash
                    or str(idx_row["embedding_manifest_hash"]) != receipt.embedding_manifest_hash
                    or str(idx_row["index_configuration_hash"]) != receipt.index_configuration_hash
                    or int(idx_row["member_count"]) != len(discovered_chunks)
                ):
                    raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.RECEIPT_MISMATCH)

        # Step 7: Exact Replay (uses same in-memory request, 0 additional provider calls)
        exact_replay_verified: bool | None = None
        if verify_replay:
            second_receipt = await build_knowledge_evidence_index(
                build_request,
                repository=repository,
            )
            if second_receipt != receipt:
                raise KnowledgeEvidenceIndexRunnerError(KnowledgeEvidenceIndexRunnerFailureReason.RECEIPT_MISMATCH)
            exact_replay_verified = True

        return build_sanitized_summary(
            receipt,
            outcome="BUILT",
            snapshot_id=snapshot_id,
            source_code=discovered_chunks[0].source_code,
            post_persist_readback_passed=post_persist_readback_passed,
            exact_replay_verified=exact_replay_verified,
            provider_call_count=3,
        )

    finally:
        if engine is not None:
            await engine.dispose()


# --------------------------------------------------------------------------------------
# CLI Interface
# --------------------------------------------------------------------------------------


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build immutable Knowledge Evidence Index for verified MFDS Source Snapshot"
    )
    parser.add_argument("snapshot_id", type=UUID, help="Target Source Snapshot UUID")
    parser.add_argument(
        "--expected-item-seq",
        required=True,
        help="Expected 9-digit MFDS item sequence (e.g. 200610660)",
    )
    parser.add_argument(
        "--expected-canonical-checksum",
        required=True,
        help="Expected 64-character SHA-256 canonical checksum",
    )
    parser.add_argument(
        "--expected-source-version",
        default=None,
        help="Expected authoritative source version string",
    )
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
        config = KnowledgeEvidenceIndexRunnerConfig.from_environment(os.environ)
        summary = asyncio.run(
            execute_knowledge_evidence_index_build(
                config=config,
                snapshot_id=args.snapshot_id,
                expected_item_seq=args.expected_item_seq,
                expected_canonical_checksum=args.expected_canonical_checksum,
                expected_source_version=args.expected_source_version,
                verify_replay=args.verify_replay,
            )
        )
    except KnowledgeEvidenceIndexRunnerError as exc:
        print(
            json.dumps({"execution_status": "FAILED", "failure_reason": exc.reason.value}, indent=2),
            file=sys.stderr,
        )
        return 1
    except KnowledgeEvidenceIndexValidationError as exc:
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
