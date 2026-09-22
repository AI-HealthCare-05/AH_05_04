"""Exact, fail-closed CLOSED_DEMO retrieval-binding manifest loader.

This module is deliberately an artifact reader, not a discovery mechanism.  It
never opens a database and it never chooses a current or latest source/index.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
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
_PROJECTION_VERSION = "sync-chat-closed-demo-17p-binding@2"
_ENVIRONMENT = "CLOSED_DEMO"
_ACCESS_MODE = "READ_ONLY"
_DATABASE = "source591_staging"
_SNAPSHOT_COUNT = 17
_MEMBER_COUNT = 51
_PRODUCT_COUNT = 17
_PRODUCT_MEMBER_COUNT = 3
_ITEM_SEQ_RE = re.compile(r"^[0-9]{9}$")
_APPROVED_BINDING_SHA256 = "ce14b2b314dd3ddd8e32e816559792711b3200fe1eb64b7c2d0c84916d31a0c4"


class ClosedDemoRetrievalBindingError(ValueError):
    """The sealed CLOSED_DEMO artifact is malformed or does not verify."""


@dataclass(frozen=True, slots=True)
class ClosedDemoSnapshotMemberPair:
    source_snapshot_id: UUID
    source_snapshot_member_id: UUID


@dataclass(frozen=True, slots=True)
class ClosedDemoProductScope:
    item_seq: str
    source_snapshot_id: UUID
    source_snapshot_member_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class ClosedDemoRetrievalBinding:
    artifact_ref: ImmutableArtifactRef
    database: str
    access_mode: str
    snapshot_member_pairs: tuple[ClosedDemoSnapshotMemberPair, ...]
    execution_binding: EvidenceSearchExecutionBinding
    product_scopes: tuple[ClosedDemoProductScope, ...] = ()

    def scope_for_item_seq(self, item_seq: str) -> ClosedDemoProductScope:
        if not isinstance(item_seq, str) or not _ITEM_SEQ_RE.fullmatch(item_seq):
            _fail(f"item_seq {item_seq!r} is invalid")
        for scope in self.product_scopes:
            if scope.item_seq == item_seq:
                return scope
        _fail(f"no product scope for item_seq {item_seq}")


def _fail(message: str) -> NoReturn:
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


def _parse_product_member_ids(
    raw_members: Any,
    snapshot_id: UUID,
    item_seq: str,
    allowed_members: set[UUID],
    allowed_pairs: set[tuple[UUID, UUID]],
) -> tuple[UUID, ...]:
    if not isinstance(raw_members, list) or len(raw_members) != _PRODUCT_MEMBER_COUNT:
        _fail(
            f"product_scopes[{item_seq!r}].source_snapshot_member_ids must contain exactly {_PRODUCT_MEMBER_COUNT} members"
        )
    try:
        product_member_ids = tuple(UUID(m) for m in raw_members)
    except (TypeError, ValueError) as exc:
        raise ClosedDemoRetrievalBindingError(f"product_scopes[{item_seq!r}] contains an invalid member UUID") from exc

    if len(set(product_member_ids)) != _PRODUCT_MEMBER_COUNT:
        _fail(f"product_scopes[{item_seq!r}] contains duplicate member UUIDs")

    for m_id in product_member_ids:
        if m_id not in allowed_members:
            _fail(f"product_scopes[{item_seq!r}] member {m_id} is outside allowed members")
        if (snapshot_id, m_id) not in allowed_pairs:
            _fail(f"product_scopes[{item_seq!r}] snapshot/member pair is not in snapshot_member_pairs")

    return product_member_ids


def _parse_product_scope_entry(
    item_seq: str,
    entry: Any,
    allowed_snapshots: set[UUID],
    allowed_members: set[UUID],
    allowed_pairs: set[tuple[UUID, UUID]],
) -> ClosedDemoProductScope:
    if not isinstance(item_seq, str) or not _ITEM_SEQ_RE.fullmatch(item_seq):
        _fail(f"product_scopes contains an invalid item_seq: {item_seq!r}")
    if not isinstance(entry, dict):
        _fail(f"product_scopes[{item_seq!r}] must be an object")

    try:
        snapshot_id = UUID(entry["source_snapshot_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ClosedDemoRetrievalBindingError(f"product_scopes[{item_seq!r}].source_snapshot_id is invalid") from exc

    if snapshot_id not in allowed_snapshots:
        _fail(f"product_scopes[{item_seq!r}] source_snapshot_id is outside allowed snapshots")

    product_member_ids = _parse_product_member_ids(
        raw_members=entry.get("source_snapshot_member_ids"),
        snapshot_id=snapshot_id,
        item_seq=item_seq,
        allowed_members=allowed_members,
        allowed_pairs=allowed_pairs,
    )

    return ClosedDemoProductScope(
        item_seq=item_seq,
        source_snapshot_id=snapshot_id,
        source_snapshot_member_ids=product_member_ids,
    )


def _product_scopes(
    raw: Any,
    pairs: tuple[ClosedDemoSnapshotMemberPair, ...],
    snapshot_ids: tuple[UUID, ...],
    member_ids: tuple[UUID, ...],
) -> tuple[ClosedDemoProductScope, ...]:
    if not isinstance(raw, dict) or len(raw) != _PRODUCT_COUNT:
        _fail(f"product_scopes must contain exactly {_PRODUCT_COUNT} products")

    allowed_pairs = {(p.source_snapshot_id, p.source_snapshot_member_id) for p in pairs}
    allowed_snapshots = set(snapshot_ids)
    allowed_members = set(member_ids)

    scopes: list[ClosedDemoProductScope] = []
    seen_snapshots: set[UUID] = set()
    seen_members: set[UUID] = set()

    for item_seq, entry in raw.items():
        scope = _parse_product_scope_entry(
            item_seq=item_seq,
            entry=entry,
            allowed_snapshots=allowed_snapshots,
            allowed_members=allowed_members,
            allowed_pairs=allowed_pairs,
        )
        if scope.source_snapshot_id in seen_snapshots:
            _fail(f"snapshot {scope.source_snapshot_id} is assigned to multiple product scopes")
        seen_snapshots.add(scope.source_snapshot_id)

        for m_id in scope.source_snapshot_member_ids:
            if m_id in seen_members:
                _fail(f"member {m_id} is assigned to multiple product scopes")
            seen_members.add(m_id)

        scopes.append(scope)

    if seen_snapshots != allowed_snapshots:
        _fail("product_scopes snapshots do not exactly cover allowed snapshots")
    if seen_members != allowed_members:
        _fail("product_scopes members do not exactly cover allowed members")

    return tuple(sorted(scopes, key=lambda s: s.item_seq))


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
    product_scopes = _product_scopes(
        manifest.get("product_scopes"),
        pairs=pairs,
        snapshot_ids=snapshot_ids,
        member_ids=member_ids,
    )

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
        product_scopes=product_scopes,
    )


def scope_for_item_seq(
    item_seq: str,
    binding: ClosedDemoRetrievalBinding | None = None,
) -> ClosedDemoProductScope:
    target = binding if binding is not None else load_closed_demo_retrieval_binding()
    return target.scope_for_item_seq(item_seq)
