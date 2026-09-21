"""Exact, fail-closed CLOSED_DEMO retrieval-binding manifest loader.

This module is deliberately an artifact reader, not a discovery mechanism.  It
never opens a database and it never chooses a current or latest source/index.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from ai_worker.adapters.openai_text_embedding import OPENAI_TEXT_EMBEDDING_ADAPTER_REF
from ai_worker.adapters.postgresql_evidence_search import POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF
from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.evidence_search import (
    EvidenceSearchExecutionBinding,
    RetrievalExecutionMode,
    VersionedDenseSearchConfiguration,
    VersionedEvidenceRetrievalConfiguration,
    VersionedLexicalSearchConfiguration,
)

CLOSED_DEMO_BINDING_RESOURCE = (
    Path(__file__).resolve().parent / "resources" / "sync-chat-closed-demo-17p-binding-v1.json"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROJECTION_VERSION = "sync-chat-closed-demo-17p-binding@1"
_ENVIRONMENT = "CLOSED_DEMO"
_ACCESS_MODE = "READ_ONLY"
_DATABASE = "source591_staging"
_SNAPSHOT_COUNT = 17
_MEMBER_COUNT = 51
_APPROVED_BINDING_SHA256 = "96f14993377cc2416c42ff12219d77eb189adb8f6d6c6003e9f56c312f1d7886"


class ClosedDemoRetrievalBindingError(ValueError):
    """The sealed CLOSED_DEMO artifact is malformed or does not verify."""


@dataclass(frozen=True, slots=True)
class ClosedDemoSnapshotMemberPair:
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID


@dataclass(frozen=True, slots=True)
class ClosedDemoRetrievalBinding:
    artifact_ref: ImmutableArtifactRef
    database: str
    access_mode: str
    snapshot_member_pairs: tuple[ClosedDemoSnapshotMemberPair, ...]
    execution_binding: EvidenceSearchExecutionBinding


def _fail(message: str) -> None:
    raise ClosedDemoRetrievalBindingError(message)


def _artifact_ref(raw: Any, name: str) -> ImmutableArtifactRef:
    if not isinstance(raw, dict):
        _fail(f"{name} must be an artifact reference")
    code = raw.get("artifact_code")
    version = raw.get("version")
    content_sha256 = raw.get("content_sha256")
    if not isinstance(code, str) or not code.strip():
        _fail(f"{name}.artifact_code is invalid")
    if not isinstance(version, str) or not version.strip():
        _fail(f"{name}.version is invalid")
    if not isinstance(content_sha256, str) or not _SHA256_RE.fullmatch(content_sha256):
        _fail(f"{name}.content_sha256 is invalid")
    return ImmutableArtifactRef(code, version, content_sha256)


def _uuid_tuple(raw: Any, name: str, expected_count: int) -> tuple[UUID, ...]:
    if not isinstance(raw, list) or len(raw) != expected_count:
        _fail(f"{name} must contain exactly {expected_count} UUIDs")
    try:
        result = tuple(UUID(value) for value in raw)
    except (TypeError, ValueError) as exc:
        raise ClosedDemoRetrievalBindingError(f"{name} contains an invalid UUID") from exc
    if len(set(result)) != expected_count:
        _fail(f"{name} contains duplicates")
    return result


def _snapshot_member_pairs(raw: Any) -> tuple[ClosedDemoSnapshotMemberPair, ...]:
    if not isinstance(raw, list) or len(raw) != _MEMBER_COUNT:
        _fail(f"snapshot_member_pairs must contain exactly {_MEMBER_COUNT} pairs")
    pairs: list[ClosedDemoSnapshotMemberPair] = []
    for item in raw:
        if not isinstance(item, dict):
            _fail("snapshot_member_pairs contains an invalid pair")
        try:
            pairs.append(
                ClosedDemoSnapshotMemberPair(
                    source_snapshot_id=UUID(item["source_snapshot_id"]),
                    source_snapshot_member_id=UUID(item["source_snapshot_member_id"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ClosedDemoRetrievalBindingError("snapshot_member_pairs contains an invalid UUID") from exc
    if len({pair.source_snapshot_member_id for pair in pairs}) != _MEMBER_COUNT:
        _fail("snapshot_member_pairs contains duplicate members")
    return tuple(pairs)


def _retrieval_config(
    *,
    lexical_ref: ImmutableArtifactRef,
    dense_ref: ImmutableArtifactRef,
    retrieval_ref: ImmutableArtifactRef,
    embedding_ref: ImmutableArtifactRef,
) -> VersionedEvidenceRetrievalConfiguration:
    lexical = VersionedLexicalSearchConfiguration(artifact_ref=lexical_ref)
    dense = VersionedDenseSearchConfiguration(artifact_ref=dense_ref)
    configuration = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=retrieval_ref,
        lexical_config=lexical,
        dense_config=dense,
        expected_query_embedding_adapter_ref=embedding_ref,
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
    )
    if not configuration.is_hash_valid():
        _fail("retrieval configuration hash is invalid")
    if embedding_ref != OPENAI_TEXT_EMBEDDING_ADAPTER_REF:
        _fail("embedding adapter reference does not match the approved adapter")
    return configuration


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ClosedDemoRetrievalBindingError("binding manifest is unavailable") from exc
    except json.JSONDecodeError as exc:
        raise ClosedDemoRetrievalBindingError("binding manifest is not valid JSON") from exc
    if not isinstance(raw, dict):
        _fail("binding manifest root must be an object")
    return raw


def load_closed_demo_retrieval_binding(path: Path | str | None = None) -> ClosedDemoRetrievalBinding:
    """Load one sealed 17-product binding with no runtime DB discovery."""
    target = Path(path) if path is not None else CLOSED_DEMO_BINDING_RESOURCE
    manifest = _load_manifest(target)

    if manifest.get("projection_version") != _PROJECTION_VERSION:
        _fail("binding projection version is invalid")
    if manifest.get("environment") != _ENVIRONMENT:
        _fail("binding environment is invalid")
    if manifest.get("access_mode") != _ACCESS_MODE or manifest.get("database") != _DATABASE:
        _fail("binding database access boundary is invalid")

    artifact_ref = _artifact_ref(manifest.get("artifact_ref"), "artifact_ref")
    if artifact_ref.content_sha256 != _APPROVED_BINDING_SHA256:
        _fail("binding artifact is not the approved CLOSED_DEMO manifest")
    if canonical_sha256(manifest, excluded_top_level_keys=frozenset({"artifact_ref"})) != artifact_ref.content_sha256:
        _fail("manifest hash does not match the sealed artifact reference")

    index_ref = _artifact_ref(manifest.get("evidence_index_ref"), "evidence_index_ref")
    filter_ref = _artifact_ref(manifest.get("filter_snapshot_ref"), "filter_snapshot_ref")
    lexical_ref = _artifact_ref(manifest.get("lexical_config_ref"), "lexical_config_ref")
    dense_ref = _artifact_ref(manifest.get("dense_config_ref"), "dense_config_ref")
    retrieval_ref = _artifact_ref(manifest.get("retrieval_config_ref"), "retrieval_config_ref")
    embedding_ref = _artifact_ref(manifest.get("embedding_adapter_ref"), "embedding_adapter_ref")
    search_ref = _artifact_ref(manifest.get("search_adapter_ref"), "search_adapter_ref")
    if search_ref != POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF:
        _fail("search adapter reference does not match the approved adapter")

    try:
        knowledge_index_id = UUID(manifest["knowledge_index_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ClosedDemoRetrievalBindingError("knowledge_index_id is invalid") from exc
    snapshot_ids = _uuid_tuple(
        manifest.get("allowed_source_snapshot_ids"), "allowed_source_snapshot_ids", _SNAPSHOT_COUNT
    )
    member_ids = _uuid_tuple(
        manifest.get("allowed_source_snapshot_member_ids"),
        "allowed_source_snapshot_member_ids",
        _MEMBER_COUNT,
    )
    pairs = _snapshot_member_pairs(manifest.get("snapshot_member_pairs"))
    if {pair.source_snapshot_id for pair in pairs} != set(snapshot_ids):
        _fail("snapshot_member_pairs do not exactly cover the allowed snapshots")
    if {pair.source_snapshot_member_id for pair in pairs} != set(member_ids):
        _fail("snapshot_member_pairs do not exactly cover the allowed members")

    return ClosedDemoRetrievalBinding(
        artifact_ref=artifact_ref,
        database=_DATABASE,
        access_mode=_ACCESS_MODE,
        snapshot_member_pairs=pairs,
        execution_binding=EvidenceSearchExecutionBinding(
            filter_snapshot_ref=filter_ref,
            evidence_index_ref=index_ref,
            knowledge_index_id=knowledge_index_id,
            allowed_source_snapshot_ids=snapshot_ids,
            allowed_source_snapshot_member_ids=member_ids,
            retrieval_config=_retrieval_config(
                lexical_ref=lexical_ref,
                dense_ref=dense_ref,
                retrieval_ref=retrieval_ref,
                embedding_ref=embedding_ref,
            ),
        ),
    )
