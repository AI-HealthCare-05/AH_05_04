"""Production Knowledge Evidence Search port, requests, configurations, and receipts."""

from __future__ import annotations

import math
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Protocol

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.rag.evidence_rank_fusion import (
    FractionReceipt,
    StableCoordinate,
)
from ai_worker.tasks.rag.evidence_retrieval import (
    ImmutableArtifactRef,
    QueryFingerprint,
    SensitiveText,
)

_DECIMAL_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)
_SCORE_QUANTUM = Decimal("0.000000000000000001")
_FORBIDDEN_CHARS = frozenset(
    [
        "\u0000",  # NUL
        "\u200b",  # zero-width space
        "\u200c",  # zero-width non-joiner
        "\u200d",  # zero-width joiner
        "\u202a",  # bidi embedding
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
        "\u2066",  # bidi isolate
        "\u2067",
        "\u2068",
        "\u2069",
        "\ufeff",  # BOM
    ]
)


class EvidenceSearchFailureReason(StrEnum):
    REQUEST_INVALID = "REQUEST_INVALID"
    FILTER_BINDING_INVALID = "FILTER_BINDING_INVALID"
    RETRIEVAL_CONFIG_INVALID = "RETRIEVAL_CONFIG_INVALID"
    INDEX_BINDING_INVALID = "INDEX_BINDING_INVALID"
    SOURCE_BINDING_INVALID = "SOURCE_BINDING_INVALID"
    SOURCE_NOT_CURRENT = "SOURCE_NOT_CURRENT"
    QUERY_EMBEDDING_INVALID = "QUERY_EMBEDDING_INVALID"
    LEXICAL_DEPENDENCY_ERROR = "LEXICAL_DEPENDENCY_ERROR"
    DENSE_DEPENDENCY_ERROR = "DENSE_DEPENDENCY_ERROR"
    SEARCH_RESULT_INVALID = "SEARCH_RESULT_INVALID"
    FUSION_INPUT_INVALID = "FUSION_INPUT_INVALID"


def format_observed_score(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("Observed score must be a finite number")
    with localcontext(_DECIMAL_CONTEXT):
        d = Decimal.from_float(value).quantize(_SCORE_QUANTUM)
        if d == 0:
            return "0"
        sign = "-" if d < 0 else ""
        d_abs = abs(d)
        s = f"{d_abs:f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return f"{sign}{s}"


class SensitiveVector:
    """Immutable sequence of finite floats redacted in ordinary representations."""

    __slots__ = ("__values",)

    def __init__(self, values: Sequence[float]) -> None:
        val_tuple = tuple(float(v) for v in values)
        if not val_tuple:
            raise ValueError("Vector cannot be empty")
        for v in val_tuple:
            if not math.isfinite(v):
                raise ValueError("Vector components must be finite")
        norm_sq = sum(v * v for v in val_tuple)
        if norm_sq <= 0:
            raise ValueError("Vector Euclidean norm must be > 0 (non-zero vector required)")
        self.__values = val_tuple

    def __setattr__(self, name: str, value: object) -> None:
        if name == "_SensitiveVector__values" and not hasattr(self, name):
            object.__setattr__(self, name, value)
            return
        raise AttributeError("SensitiveVector is immutable")

    def reveal(self) -> tuple[float, ...]:
        return self.__values

    def __len__(self) -> int:
        return len(self.__values)

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__


def validate_query_text(query: SensitiveText) -> None:
    if not isinstance(query, SensitiveText):
        raise ValueError("REQUEST_INVALID: Query must be SensitiveText")
    raw = query.reveal()
    if not raw or raw != raw.strip():
        raise ValueError("REQUEST_INVALID: Query must not be blank and have no leading/trailing whitespace")
    if not (1 <= len(raw) <= 2000):
        raise ValueError("REQUEST_INVALID: Query length must be between 1 and 2000 characters")
    if unicodedata.normalize("NFC", raw) != raw:
        raise ValueError("REQUEST_INVALID: Query must be Unicode NFC normalized")
    if any(ch in _FORBIDDEN_CHARS for ch in raw):
        raise ValueError("REQUEST_INVALID: Query contains forbidden control/bidi/zero-width character")


@dataclass(frozen=True, slots=True)
class QueryEmbeddingReceipt:
    query_fingerprint: QueryFingerprint
    model_ref: str
    model_version: str
    dimension: int
    embedding: SensitiveVector
    adapter_artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class VersionedLexicalSearchConfiguration:
    artifact_ref: ImmutableArtifactRef
    exact_strategy: str = "case-sensitive-substring-v1"
    query_normalization: str = "caller-supplied-nonblank-nfc-no-silent-transform-v1"
    trigram_match_operator: str = "%"
    trigram_score_function: str = "similarity"
    trigram_threshold: str = "0.3"
    fts_regconfig: str = "simple"
    fts_vector_expression: str = "to_tsvector('simple', chunk_text)"
    fts_query_constructor: str = "plainto_tsquery('simple', query)"
    fts_score_function: str = "ts_rank_cd"
    exact_limit: int = 20
    trigram_limit: int = 20
    fts_limit: int = 20

    def compute_canonical_hash(self) -> str:
        data: JsonValue = {
            "exact_limit": self.exact_limit,
            "exact_strategy": self.exact_strategy,
            "fts_limit": self.fts_limit,
            "fts_query_constructor": self.fts_query_constructor,
            "fts_regconfig": self.fts_regconfig,
            "fts_score_function": self.fts_score_function,
            "fts_vector_expression": self.fts_vector_expression,
            "query_normalization": self.query_normalization,
            "trigram_limit": self.trigram_limit,
            "trigram_match_operator": self.trigram_match_operator,
            "trigram_score_function": self.trigram_score_function,
            "trigram_threshold": self.trigram_threshold,
        }
        return canonical_sha256(data)

    def is_hash_valid(self) -> bool:
        return self.artifact_ref.content_sha256 == self.compute_canonical_hash()


@dataclass(frozen=True, slots=True)
class VersionedDenseSearchConfiguration:
    artifact_ref: ImmutableArtifactRef
    distance_metric: str = "COSINE"
    minimum_similarity: str | None = None
    cutoff_policy: str = "none-v1"
    dense_limit: int = 20

    def compute_canonical_hash(self) -> str:
        data: JsonValue = {
            "cutoff_policy": self.cutoff_policy,
            "dense_limit": self.dense_limit,
            "distance_metric": self.distance_metric,
            "minimum_similarity": self.minimum_similarity,
        }
        return canonical_sha256(data)

    def is_hash_valid(self) -> bool:
        return self.artifact_ref.content_sha256 == self.compute_canonical_hash()


class RetrievalExecutionMode(StrEnum):
    LEXICAL_ONLY = "LEXICAL_ONLY"
    DENSE_ONLY = "DENSE_ONLY"
    HYBRID_RRF = "HYBRID_RRF"


@dataclass(frozen=True, slots=True)
class VersionedEvidenceRetrievalConfiguration:
    artifact_ref: ImmutableArtifactRef
    lexical_config: VersionedLexicalSearchConfiguration
    dense_config: VersionedDenseSearchConfiguration | None
    expected_query_embedding_adapter_ref: ImmutableArtifactRef | None
    execution_mode: RetrievalExecutionMode = RetrievalExecutionMode.HYBRID_RRF
    algorithm_id: str = "rrf-rank-fusion@1"
    rrf_k: int = 60
    exact_limit: int = 20
    trigram_limit: int = 20
    fts_limit: int = 20
    lexical_limit: int = 20
    dense_limit: int = 20
    hybrid_limit: int = 30
    future_reranker_input_limit: int = 20
    stable_coordinate_fields: tuple[str, ...] = (
        "source_code",
        "source_version",
        "external_document_id",
        "chunk_index",
    )
    tie_break: str = "stable-coordinate-fieldwise-utf8-ascending"
    observed_score_projection: str = "observed-stage-score-decimal@1"
    observed_score_quantum: str = "0.000000000000000001"
    observed_score_rounding: str = "ROUND_HALF_EVEN"
    transaction_isolation: str = "REPEATABLE_READ"
    transaction_access: str = "READ_ONLY"

    def compute_canonical_hash(self) -> str:
        expected_adapter_val: JsonValue = (
            {
                "artifact_code": self.expected_query_embedding_adapter_ref.artifact_code,
                "content_sha256": self.expected_query_embedding_adapter_ref.content_sha256,
                "version": self.expected_query_embedding_adapter_ref.version,
            }
            if self.expected_query_embedding_adapter_ref
            else None
        )
        data: JsonValue = {
            "algorithm_id": self.algorithm_id,
            "dense_config_hash": self.dense_config.compute_canonical_hash() if self.dense_config else None,
            "dense_limit": self.dense_limit,
            "exact_limit": self.exact_limit,
            "execution_mode": (
                self.execution_mode.value
                if isinstance(self.execution_mode, RetrievalExecutionMode)
                else str(self.execution_mode)
            ),
            "expected_query_embedding_adapter_ref": expected_adapter_val,
            "fts_limit": self.fts_limit,
            "future_reranker_input_limit": self.future_reranker_input_limit,
            "hybrid_limit": self.hybrid_limit,
            "lexical_config_hash": self.lexical_config.compute_canonical_hash(),
            "lexical_limit": self.lexical_limit,
            "observed_score_projection": self.observed_score_projection,
            "observed_score_quantum": self.observed_score_quantum,
            "observed_score_rounding": self.observed_score_rounding,
            "rrf_k": self.rrf_k,
            "stable_coordinate_fields": list(self.stable_coordinate_fields),
            "tie_break": self.tie_break,
            "transaction_access": self.transaction_access,
            "transaction_isolation": self.transaction_isolation,
            "trigram_limit": self.trigram_limit,
        }
        return canonical_sha256(data)

    def is_hash_valid(self) -> bool:
        if not self.lexical_config.is_hash_valid():
            return False
        if self.dense_config and not self.dense_config.is_hash_valid():
            return False
        return self.artifact_ref.content_sha256 == self.compute_canonical_hash()


def project_versioned_evidence_retrieval_configuration(
    value: VersionedEvidenceRetrievalConfiguration,
) -> dict[str, JsonValue]:
    """Project the approved retrieval configuration for immutable persistence."""

    if type(value) is not VersionedEvidenceRetrievalConfiguration or not value.is_hash_valid():
        raise ValueError("retrieval configuration must be canonical and hash-valid")

    def artifact_ref_projection(ref: ImmutableArtifactRef) -> dict[str, JsonValue]:
        return {
            "artifact_code": ref.artifact_code,
            "content_sha256": ref.content_sha256,
            "version": ref.version,
        }

    lexical = value.lexical_config
    lexical_projection: dict[str, JsonValue] = {
        "artifact_ref": artifact_ref_projection(lexical.artifact_ref),
        "exact_limit": lexical.exact_limit,
        "exact_strategy": lexical.exact_strategy,
        "fts_limit": lexical.fts_limit,
        "fts_query_constructor": lexical.fts_query_constructor,
        "fts_regconfig": lexical.fts_regconfig,
        "fts_score_function": lexical.fts_score_function,
        "fts_vector_expression": lexical.fts_vector_expression,
        "query_normalization": lexical.query_normalization,
        "trigram_limit": lexical.trigram_limit,
        "trigram_match_operator": lexical.trigram_match_operator,
        "trigram_score_function": lexical.trigram_score_function,
        "trigram_threshold": lexical.trigram_threshold,
    }
    dense_projection: dict[str, JsonValue] | None = None
    if value.dense_config is not None:
        dense = value.dense_config
        dense_projection = {
            "artifact_ref": artifact_ref_projection(dense.artifact_ref),
            "cutoff_policy": dense.cutoff_policy,
            "dense_limit": dense.dense_limit,
            "distance_metric": dense.distance_metric,
            "minimum_similarity": dense.minimum_similarity,
        }
    return {
        "algorithm_id": value.algorithm_id,
        "artifact_ref": artifact_ref_projection(value.artifact_ref),
        "dense_config": dense_projection,
        "dense_limit": value.dense_limit,
        "exact_limit": value.exact_limit,
        "execution_mode": value.execution_mode.value,
        "expected_query_embedding_adapter_ref": (
            artifact_ref_projection(value.expected_query_embedding_adapter_ref)
            if value.expected_query_embedding_adapter_ref is not None
            else None
        ),
        "fts_limit": value.fts_limit,
        "future_reranker_input_limit": value.future_reranker_input_limit,
        "hybrid_limit": value.hybrid_limit,
        "lexical_config": lexical_projection,
        "lexical_limit": value.lexical_limit,
        "observed_score_projection": value.observed_score_projection,
        "observed_score_quantum": value.observed_score_quantum,
        "observed_score_rounding": value.observed_score_rounding,
        "rrf_k": value.rrf_k,
        "stable_coordinate_fields": list(value.stable_coordinate_fields),
        "tie_break": value.tie_break,
        "transaction_access": value.transaction_access,
        "transaction_isolation": value.transaction_isolation,
        "trigram_limit": value.trigram_limit,
    }


@dataclass(frozen=True, slots=True)
class EvidenceSearchExecutionBinding:
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    knowledge_index_id: uuid.UUID
    allowed_source_snapshot_ids: tuple[uuid.UUID, ...]
    allowed_source_snapshot_member_ids: tuple[uuid.UUID, ...]
    retrieval_config: VersionedEvidenceRetrievalConfiguration


@dataclass(frozen=True, slots=True)
class EvidenceSearchRequest:
    normalized_query: SensitiveText
    query_fingerprint: QueryFingerprint
    execution_binding: EvidenceSearchExecutionBinding
    query_embedding_receipt: QueryEmbeddingReceipt | None


@dataclass(frozen=True, slots=True)
class ProductionEvidenceProvenance:
    knowledge_index_id: uuid.UUID
    index_code: str
    index_version: str
    index_configuration_hash: str
    knowledge_chunk_id: uuid.UUID
    source_snapshot_id: uuid.UUID
    source_snapshot_member_id: uuid.UUID
    source_code: str
    source_version: str
    canonical_checksum: str
    external_document_id: str
    chunk_index: int
    locator: str
    content_hash: str
    canonicalization_spec_version: str
    normalization_version: str


class ProductionSearchMethod(StrEnum):
    EXACT = "EXACT"
    TRIGRAM = "TRIGRAM"
    FTS = "FTS"
    LEXICAL = "LEXICAL"
    DENSE = "DENSE"


@dataclass(frozen=True, slots=True)
class ProductionSearchSignal:
    provenance: ProductionEvidenceProvenance
    method: ProductionSearchMethod
    raw_rank: int
    observed_score: str


@dataclass(frozen=True, slots=True)
class ProductionSearchHit:
    provenance: ProductionEvidenceProvenance
    coordinate: StableCoordinate
    exact_hit: bool
    observed_trigram_score: str | None
    observed_fts_score: str | None
    observed_dense_score: str | None
    lexical_rank: int | None
    dense_rank: int | None
    fusion_rank: int
    fraction_receipt: FractionReceipt
    is_eligible_for_future_reranker: bool


@dataclass(frozen=True, slots=True)
class EvidenceSearchSuccess:
    request: EvidenceSearchRequest
    adapter_artifact_ref: ImmutableArtifactRef
    lexical_hits: tuple[ProductionSearchHit, ...]
    dense_hits: tuple[ProductionSearchHit, ...]
    hybrid_hits: tuple[ProductionSearchHit, ...]
    signals: tuple[ProductionSearchSignal, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceSearchFailure:
    reason: EvidenceSearchFailureReason


class EvidenceSearchPort(Protocol):
    async def search(
        self,
        request: EvidenceSearchRequest,
    ) -> EvidenceSearchSuccess | EvidenceSearchFailure: ...


def validate_retrieval_configuration(config: VersionedEvidenceRetrievalConfiguration) -> EvidenceSearchFailure | None:
    if not config.is_hash_valid():
        return EvidenceSearchFailure(EvidenceSearchFailureReason.RETRIEVAL_CONFIG_INVALID)
    if config.execution_mode == RetrievalExecutionMode.LEXICAL_ONLY:
        if config.dense_config is not None or config.expected_query_embedding_adapter_ref is not None:
            return EvidenceSearchFailure(EvidenceSearchFailureReason.RETRIEVAL_CONFIG_INVALID)
    elif config.execution_mode == RetrievalExecutionMode.DENSE_ONLY:
        if config.dense_config is None or config.expected_query_embedding_adapter_ref is None:
            return EvidenceSearchFailure(EvidenceSearchFailureReason.RETRIEVAL_CONFIG_INVALID)
    elif config.execution_mode == RetrievalExecutionMode.HYBRID_RRF:
        if config.dense_config is None or config.expected_query_embedding_adapter_ref is None:
            return EvidenceSearchFailure(EvidenceSearchFailureReason.RETRIEVAL_CONFIG_INVALID)
    return None


def validate_search_request(request: EvidenceSearchRequest) -> EvidenceSearchFailure | None:
    try:
        validate_query_text(request.normalized_query)
    except ValueError:
        return EvidenceSearchFailure(EvidenceSearchFailureReason.REQUEST_INVALID)

    binding = request.execution_binding
    if not binding.allowed_source_snapshot_ids or not binding.allowed_source_snapshot_member_ids:
        return EvidenceSearchFailure(EvidenceSearchFailureReason.FILTER_BINDING_INVALID)

    config_failure = validate_retrieval_configuration(binding.retrieval_config)
    if config_failure is not None:
        return config_failure

    mode = binding.retrieval_config.execution_mode
    if mode == RetrievalExecutionMode.LEXICAL_ONLY:
        if request.query_embedding_receipt is not None:
            return EvidenceSearchFailure(EvidenceSearchFailureReason.REQUEST_INVALID)
    else:
        # DENSE_ONLY or HYBRID_RRF
        if request.query_embedding_receipt is None:
            return EvidenceSearchFailure(EvidenceSearchFailureReason.QUERY_EMBEDDING_INVALID)
        receipt = request.query_embedding_receipt
        if receipt.query_fingerprint != request.query_fingerprint:
            return EvidenceSearchFailure(EvidenceSearchFailureReason.QUERY_EMBEDDING_INVALID)
        expected_adapter = binding.retrieval_config.expected_query_embedding_adapter_ref
        if expected_adapter is not None and receipt.adapter_artifact_ref != expected_adapter:
            return EvidenceSearchFailure(EvidenceSearchFailureReason.QUERY_EMBEDDING_INVALID)

    return None
