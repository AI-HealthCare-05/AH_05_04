from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, cast
from uuid import UUID

from ai_worker.tasks.evaluation.canonical import (
    JsonValue,
)
from ai_worker.tasks.evaluation.canonical import (
    canonical_json_bytes as _canonical_json_bytes_eval,
)
from ai_worker.tasks.evaluation.canonical import (
    canonical_sha256 as _canonical_sha256_eval,
)


def canonical_json_bytes(payload: Any) -> bytes:
    """RFC 8785 JSON Canonicalization Scheme (JCS)."""
    return _canonical_json_bytes_eval(cast(JsonValue, payload))


def sha256_canonical_json(payload: Any) -> str:
    return _canonical_sha256_eval(cast(JsonValue, payload))


class BeginRetrievalRunFailureReason(StrEnum):
    CONFLICT = "CONFLICT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


class FinalizeRetrievalRunFailureReason(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    STATE_CONFLICT = "STATE_CONFLICT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CORRUPTED = "CORRUPTED"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


@dataclass(frozen=True, slots=True)
class BeginRetrievalRunRequest:
    job_id: UUID
    execution_context_id: UUID
    prescription_version_id: UUID
    runtime_release_bundle_id: UUID
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID
    runtime_execution_manifest_hash: str
    runtime_guard_decision_ref: str
    knowledge_index_id: UUID
    variant: str  # "RET-L", "RET-D", "RET-H"
    query_digest_algorithm: str
    query_digest_key_version: str
    query_digest: str
    filter_snapshot: dict[str, Any]
    filter_snapshot_hash: str
    source_manifest_hash: str
    retrieval_configuration_hash: str
    lexical_limit: int
    dense_limit: int
    hybrid_limit: int
    final_k: int
    node_id: str = "hybrid_retrieve"
    query_embedding_sha256: str | None = None


def _format_decimal(val: Decimal) -> str:
    return f"{val:.18f}"


@dataclass(frozen=True, slots=True)
class PersistedSignalInput:
    knowledge_chunk_id: UUID
    method: str  # "EXACT", "TRIGRAM", "FTS", "LEXICAL", "DENSE"
    raw_rank: int
    raw_score: Decimal
    score_projection_version: str

    def to_manifest_payload(self) -> dict[str, Any]:
        return {
            "knowledge_chunk_id": str(self.knowledge_chunk_id),
            "method": self.method,
            "raw_rank": self.raw_rank,
            "raw_score": _format_decimal(self.raw_score),
            "score_projection_version": self.score_projection_version,
        }


@dataclass(frozen=True, slots=True)
class PersistedHitInput:
    knowledge_chunk_id: UUID
    rrf_rank: int
    rrf_score: Decimal
    rrf_score_numerator: str
    rrf_score_denominator: str
    final_rank: int
    selected: bool
    lexical_rank: int | None = None
    dense_rank: int | None = None
    rerank_score: Decimal | None = None

    def to_manifest_payload(self) -> dict[str, Any]:
        return {
            "dense_rank": self.dense_rank,
            "final_rank": self.final_rank,
            "knowledge_chunk_id": str(self.knowledge_chunk_id),
            "lexical_rank": self.lexical_rank,
            "rerank_score": _format_decimal(self.rerank_score) if self.rerank_score is not None else None,
            "rrf_rank": self.rrf_rank,
            "rrf_score": _format_decimal(self.rrf_score),
            "rrf_score_denominator": self.rrf_score_denominator,
            "rrf_score_numerator": self.rrf_score_numerator,
            "selected": self.selected,
        }


def compute_signal_manifest_hash(signals: Sequence[PersistedSignalInput]) -> str:
    sorted_signals = sorted(signals, key=lambda s: (s.method, s.raw_rank, str(s.knowledge_chunk_id)))
    payload = [s.to_manifest_payload() for s in sorted_signals]
    return sha256_canonical_json(payload)


def compute_hit_manifest_hash(hits: Sequence[PersistedHitInput]) -> str:
    sorted_hits = sorted(hits, key=lambda h: h.final_rank)
    payload = [h.to_manifest_payload() for h in sorted_hits]
    return sha256_canonical_json(payload)


TERMINAL_REPLAY_PAYLOAD_PROJECTION_VERSION = "retrieval-terminal-replay-payload-v1"


@dataclass(frozen=True, slots=True)
class PersistedTerminalReplayPayload:
    """Versioned, immutable input required to replay a completed retrieval run."""

    search_receipt: dict[str, Any]
    ordered_selected_hits: tuple[dict[str, Any], ...]
    gate_status: str
    gate_reason: str
    gate_message: str = ""
    projection_version: str = TERMINAL_REPLAY_PAYLOAD_PROJECTION_VERSION

    def to_projection(self) -> dict[str, Any]:
        return {
            "gate_message": self.gate_message,
            "gate_reason": self.gate_reason,
            "gate_status": self.gate_status,
            "ordered_selected_hits": list(self.ordered_selected_hits),
            "projection_version": self.projection_version,
            "search_receipt": self.search_receipt,
        }

    @classmethod
    def from_projection(cls, value: object) -> PersistedTerminalReplayPayload:
        if not isinstance(value, dict):
            raise ValueError("Terminal replay payload must be an object")
        hits = value.get("ordered_selected_hits")
        receipt = value.get("search_receipt")
        if value.get("projection_version") != TERMINAL_REPLAY_PAYLOAD_PROJECTION_VERSION:
            raise ValueError("Unsupported terminal replay payload projection version")
        if (
            not isinstance(receipt, dict)
            or not isinstance(hits, list)
            or not all(isinstance(hit, dict) for hit in hits)
        ):
            raise ValueError("Invalid terminal replay payload shape")
        return cls(
            search_receipt=receipt,
            ordered_selected_hits=tuple(hits),
            gate_status=str(value.get("gate_status", "")),
            gate_reason=str(value.get("gate_reason", "")),
            gate_message=str(value.get("gate_message", "")),
        )


def compute_receipt_hash(
    *,
    run_id: UUID,
    job_id: UUID,
    node_id: str,
    variant: str,
    status: str,
    query_digest: str,
    retrieval_configuration_hash: str,
    source_manifest_hash: str,
    search_receipt_hash: str | None,
    total_signals: int,
    total_hits: int,
    selected_count: int,
    signal_manifest_hash: str,
    hit_manifest_hash: str,
    terminal_replay_payload_hash: str | None = None,
) -> str:
    envelope = {
        "hit_manifest_hash": hit_manifest_hash,
        "job_id": str(job_id),
        "node_id": node_id,
        "query_digest": query_digest,
        "retrieval_configuration_hash": retrieval_configuration_hash,
        "run_id": str(run_id),
        "search_receipt_hash": search_receipt_hash,
        "selected_count": selected_count,
        "signal_manifest_hash": signal_manifest_hash,
        "source_manifest_hash": source_manifest_hash,
        "status": status,
        "total_hits": total_hits,
        "total_signals": total_signals,
        "variant": variant,
    }
    if terminal_replay_payload_hash is not None:
        envelope["terminal_replay_payload_hash"] = terminal_replay_payload_hash
    return sha256_canonical_json(envelope)


@dataclass(frozen=True, slots=True)
class PersistedRetrievalRunReceipt:
    run_id: UUID
    job_id: UUID
    node_id: str
    variant: str
    status: str
    query_digest: str
    retrieval_configuration_hash: str
    source_manifest_hash: str
    receipt_hash: str
    total_signals: int
    total_hits: int
    selected_count: int
    signal_manifest_hash: str
    hit_manifest_hash: str
    search_receipt_hash: str | None = None
    diagnostic_code: str | None = None
    error_code: str | None = None
    terminal_replay_payload_hash: str | None = None


@dataclass(frozen=True, slots=True)
class FinalizeRetrievalRunRequest:
    run_id: UUID
    status: str  # "COMPLETED", "FAILED"
    diagnostic_code: str | None = None
    error_code: str | None = None
    search_receipt_hash: str | None = None
    signals: tuple[PersistedSignalInput, ...] = ()
    hits: tuple[PersistedHitInput, ...] = ()
    terminal_replay_payload: PersistedTerminalReplayPayload | None = None


@dataclass(frozen=True, slots=True)
class BeginRetrievalRunSuccess:
    run_id: UUID
    is_resumed: bool
    existing_receipt: PersistedRetrievalRunReceipt | None = None
    existing_terminal_replay_payload: PersistedTerminalReplayPayload | None = None


@dataclass(frozen=True, slots=True)
class BeginRetrievalRunFailure:
    reason: BeginRetrievalRunFailureReason
    message: str | None = None


BeginRetrievalRunOutcome = BeginRetrievalRunSuccess | BeginRetrievalRunFailure


@dataclass(frozen=True, slots=True)
class FinalizeRetrievalRunSuccess:
    receipt: PersistedRetrievalRunReceipt


@dataclass(frozen=True, slots=True)
class FinalizeRetrievalRunFailure:
    reason: FinalizeRetrievalRunFailureReason
    message: str | None = None


FinalizeRetrievalRunOutcome = FinalizeRetrievalRunSuccess | FinalizeRetrievalRunFailure


class RetrievalRunStorePort(Protocol):
    async def begin_run(self, request: BeginRetrievalRunRequest) -> BeginRetrievalRunOutcome: ...

    async def finalize_run(self, request: FinalizeRetrievalRunRequest) -> FinalizeRetrievalRunOutcome: ...

    async def get_run_receipt(self, run_id: UUID) -> PersistedRetrievalRunReceipt | None: ...
