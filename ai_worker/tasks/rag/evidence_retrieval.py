"""Non-authoritative, synthetic-first Knowledge Evidence retrieval kernel.

This module deliberately stops before source governance, persistence, public
citations, and medical-answer safety decisions.  Its outputs are untrusted
retrieval candidates only.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RERANK_INPUT_PROJECTION_VERSION = "knowledge-rerank-input-v1"


class SensitiveText:
    """A query wrapper that redacts itself in ordinary representations."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        self.__value = value

    def reveal(self) -> str:
        return self.__value

    def __repr__(self) -> str:
        return "<redacted>"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class QueryFingerprint:
    algorithm: str
    key_version: str
    digest: str


@dataclass(frozen=True, slots=True)
class ImmutableArtifactRef:
    artifact_code: str
    version: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class EvidenceRetrievalKernelRequest:
    normalized_query: SensitiveText
    query_fingerprint: QueryFingerprint
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    retrieval_config_ref: ImmutableArtifactRef
    lexical_config_ref: ImmutableArtifactRef
    dense_config_ref: ImmutableArtifactRef | None
    rerank_config_ref: ImmutableArtifactRef
    rerank_input_projection_version: str
    lexical_limit: int
    dense_limit: int
    selection_limit: int


class KernelExecutionStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


class KernelDiagnosticCode(StrEnum):
    CANDIDATES_RERANKED = "CANDIDATES_RERANKED"
    NO_HITS = "NO_HITS"
    QUERY_BINDING_INVALID = "QUERY_BINDING_INVALID"
    REQUEST_INVALID = "REQUEST_INVALID"
    QUERY_BINDING_RECEIPT_MISMATCH = "QUERY_BINDING_RECEIPT_MISMATCH"
    QUERY_BINDING_DEPENDENCY_ERROR = "QUERY_BINDING_DEPENDENCY_ERROR"
    SEARCH_DEPENDENCY_ERROR = "SEARCH_DEPENDENCY_ERROR"
    SEARCH_RECEIPT_MISMATCH = "SEARCH_RECEIPT_MISMATCH"
    SEARCH_RESULT_INVALID = "SEARCH_RESULT_INVALID"
    RERANK_DEPENDENCY_ERROR = "RERANK_DEPENDENCY_ERROR"
    RERANK_RECEIPT_MISMATCH = "RERANK_RECEIPT_MISMATCH"
    RERANK_RESULT_INVALID = "RERANK_RESULT_INVALID"


class QueryBindingFailureReason(StrEnum):
    INVALID_BINDING = "INVALID_BINDING"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"


@dataclass(frozen=True, slots=True)
class QueryBindingVerificationSuccess:
    query_fingerprint: QueryFingerprint
    verifier_artifact_ref: ImmutableArtifactRef


@dataclass(frozen=True, slots=True)
class QueryBindingVerificationFailure:
    reason: QueryBindingFailureReason


class QueryBindingVerifierPort(Protocol):
    def verify(
        self,
        query: SensitiveText,
        query_fingerprint: QueryFingerprint,
    ) -> QueryBindingVerificationSuccess | QueryBindingVerificationFailure: ...


class EvidenceSearchStage(StrEnum):
    LEXICAL = "LEXICAL"
    DENSE = "DENSE"


@dataclass(frozen=True, slots=True)
class CanonicalScore:
    value: str


@dataclass(frozen=True, slots=True)
class KnowledgeEvidenceProvenance:
    evidence_key: str
    knowledge_chunk_ref: str
    evidence_index_ref: ImmutableArtifactRef
    source_snapshot_ref: ImmutableArtifactRef
    source_version: str
    locator: str
    content_sha256: str
    canonicalization_spec_version: str


@dataclass(frozen=True, slots=True)
class KnowledgeEvidenceSearchHit:
    provenance: KnowledgeEvidenceProvenance
    stage: EvidenceSearchStage
    rank: int
    stage_score: CanonicalScore
    content_text: SensitiveText


@dataclass(frozen=True, slots=True)
class EvidenceSearchSuccess:
    query_fingerprint: QueryFingerprint
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    retrieval_config_ref: ImmutableArtifactRef
    stage_config_ref: ImmutableArtifactRef
    stage: EvidenceSearchStage
    adapter_artifact_ref: ImmutableArtifactRef
    hits: tuple[KnowledgeEvidenceSearchHit, ...]

    @classmethod
    def from_request(
        cls,
        request: EvidenceRetrievalKernelRequest,
        stage: EvidenceSearchStage,
        hits: tuple[KnowledgeEvidenceSearchHit, ...],
    ) -> EvidenceSearchSuccess:
        stage_config_ref = (
            request.lexical_config_ref
            if stage is EvidenceSearchStage.LEXICAL
            else request.dense_config_ref
        )
        if stage_config_ref is None:
            raise ValueError("dense search requires a dense configuration reference")
        return cls(
            request.query_fingerprint,
            request.filter_snapshot_ref,
            request.evidence_index_ref,
            request.retrieval_config_ref,
            stage_config_ref,
            stage,
            ImmutableArtifactRef("search-adapter", "search-adapter@1", "d" * 64),
            hits,
        )


@dataclass(frozen=True, slots=True)
class EvidenceSearchFailure:
    pass


class EvidenceSearchPort(Protocol):
    def search(
        self,
        request: EvidenceRetrievalKernelRequest,
        stage: EvidenceSearchStage,
    ) -> EvidenceSearchSuccess | EvidenceSearchFailure: ...


@dataclass(frozen=True, slots=True)
class StageSignal:
    stage: EvidenceSearchStage
    rank: int
    score: CanonicalScore


@dataclass(frozen=True, slots=True)
class KnowledgeEvidenceCandidate:
    provenance: KnowledgeEvidenceProvenance
    content_text: SensitiveText
    stage_signals: tuple[StageSignal, ...]


@dataclass(frozen=True, slots=True)
class EvidenceRerankRequest:
    query_fingerprint: QueryFingerprint
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    retrieval_config_ref: ImmutableArtifactRef
    rerank_config_ref: ImmutableArtifactRef
    projection_version: str
    input_set_hash: str
    candidates: tuple[KnowledgeEvidenceCandidate, ...]


@dataclass(frozen=True, slots=True)
class EvidenceRerankFailure:
    pass


@dataclass(frozen=True, slots=True)
class EvidenceRerankSelection:
    evidence_key: str
    rerank_rank: int
    rerank_score: CanonicalScore


@dataclass(frozen=True, slots=True)
class EvidenceRerankSuccess:
    query_fingerprint: QueryFingerprint
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    retrieval_config_ref: ImmutableArtifactRef
    rerank_config_ref: ImmutableArtifactRef
    projection_version: str
    input_set_hash: str
    adapter_artifact_ref: ImmutableArtifactRef
    selections: tuple[EvidenceRerankSelection, ...]


@dataclass(frozen=True, slots=True)
class UntrustedKnowledgeEvidenceSelection:
    candidate: KnowledgeEvidenceCandidate
    rerank_rank: int
    rerank_score: CanonicalScore


@dataclass(frozen=True, slots=True)
class EvidenceRetrievalDiagnosticTrace:
    query_fingerprint: QueryFingerprint
    filter_snapshot_ref: ImmutableArtifactRef
    evidence_index_ref: ImmutableArtifactRef
    retrieval_config_ref: ImmutableArtifactRef
    lexical_config_ref: ImmutableArtifactRef
    dense_config_ref: ImmutableArtifactRef | None
    rerank_config_ref: ImmutableArtifactRef
    execution_status: KernelExecutionStatus
    diagnostic_code: KernelDiagnosticCode
    evidence_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceRetrievalKernelOutcome:
    execution_status: KernelExecutionStatus
    diagnostic_code: KernelDiagnosticCode
    untrusted_selections: tuple[UntrustedKnowledgeEvidenceSelection, ...] = ()
    failure_details: tuple[str, ...] = ()
    trace: EvidenceRetrievalDiagnosticTrace | None = None


def retrieve_knowledge_evidence(
    request: EvidenceRetrievalKernelRequest,
    *,
    query_verifier: QueryBindingVerifierPort,
    search_port: EvidenceSearchPort,
    rerank_port: object,
) -> EvidenceRetrievalKernelOutcome:
    """Validate and bind a query before later retrieval stages are allowed."""
    if not _is_valid_request(request):
        return _outcome(
            KernelExecutionStatus.VALIDATION_ERROR,
            KernelDiagnosticCode.REQUEST_INVALID,
        )

    binding_failure = _query_binding_failure(request, query_verifier)
    if binding_failure is not None:
        return binding_failure

    candidates = _search_candidates(request, search_port)
    if candidates is None:
        return _outcome(KernelExecutionStatus.DEPENDENCY_ERROR, KernelDiagnosticCode.SEARCH_RESULT_INVALID)
    if not candidates:
        return _outcome(KernelExecutionStatus.SUCCEEDED, KernelDiagnosticCode.NO_HITS)
    rerank = getattr(rerank_port, "rerank", None)
    if not callable(rerank):
        return _outcome(KernelExecutionStatus.DEPENDENCY_ERROR, KernelDiagnosticCode.RERANK_DEPENDENCY_ERROR)
    rerank_request = _rerank_request(request, candidates)
    try:
        response = rerank(rerank_request)
    except Exception:
        return _outcome(KernelExecutionStatus.DEPENDENCY_ERROR, KernelDiagnosticCode.RERANK_DEPENDENCY_ERROR)
    selections = _validated_selections(request, rerank_request, response)
    if selections is None:
        return _outcome(KernelExecutionStatus.DEPENDENCY_ERROR, KernelDiagnosticCode.RERANK_RESULT_INVALID)
    return EvidenceRetrievalKernelOutcome(
        KernelExecutionStatus.SUCCEEDED,
        KernelDiagnosticCode.CANDIDATES_RERANKED,
        selections,
        trace=_trace(
            request,
            KernelExecutionStatus.SUCCEEDED,
            KernelDiagnosticCode.CANDIDATES_RERANKED,
            candidates,
        ),
    )


def to_sanitized_trace_dict(trace: EvidenceRetrievalDiagnosticTrace) -> dict[str, object]:
    """Render an explicit, non-sensitive diagnostic representation."""
    return {
        "query_fingerprint": {
            "algorithm": trace.query_fingerprint.algorithm,
            "key_version": trace.query_fingerprint.key_version,
            "digest": trace.query_fingerprint.digest,
        },
        "filter_snapshot_ref": _artifact_dict(trace.filter_snapshot_ref),
        "evidence_index_ref": _artifact_dict(trace.evidence_index_ref),
        "retrieval_config_ref": _artifact_dict(trace.retrieval_config_ref),
        "lexical_config_ref": _artifact_dict(trace.lexical_config_ref),
        "dense_config_ref": _artifact_dict(trace.dense_config_ref),
        "rerank_config_ref": _artifact_dict(trace.rerank_config_ref),
        "execution_status": trace.execution_status.value,
        "diagnostic_code": trace.diagnostic_code.value,
        "evidence_keys": list(trace.evidence_keys),
    }


def _trace(
    request: EvidenceRetrievalKernelRequest,
    status: KernelExecutionStatus,
    diagnostic: KernelDiagnosticCode,
    candidates: tuple[KnowledgeEvidenceCandidate, ...],
) -> EvidenceRetrievalDiagnosticTrace:
    return EvidenceRetrievalDiagnosticTrace(
        request.query_fingerprint, request.filter_snapshot_ref, request.evidence_index_ref,
        request.retrieval_config_ref, request.lexical_config_ref, request.dense_config_ref,
        request.rerank_config_ref, status, diagnostic,
        tuple(item.provenance.evidence_key for item in candidates),
    )


def _artifact_dict(reference: ImmutableArtifactRef | None) -> dict[str, str] | None:
    if reference is None:
        return None
    return {
        "artifact_code": reference.artifact_code,
        "version": reference.version,
        "content_sha256": reference.content_sha256,
    }


def canonical_rerank_input_hash(
    projection_version: str, candidates: tuple[KnowledgeEvidenceCandidate, ...]
) -> str:
    payload = {
        "projection_version": projection_version,
        "candidates": [
            {
                "evidence_key": item.provenance.evidence_key,
                "knowledge_chunk_ref": item.provenance.knowledge_chunk_ref,
                "content_sha256": item.provenance.content_sha256,
                "signals": [
                    {"stage": signal.stage.value, "rank": signal.rank, "score": signal.score.value}
                    for signal in item.stage_signals
                ],
            }
            for item in sorted(candidates, key=lambda value: value.provenance.evidence_key.encode())
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _rerank_request(
    request: EvidenceRetrievalKernelRequest,
    candidates: tuple[KnowledgeEvidenceCandidate, ...],
) -> EvidenceRerankRequest:
    return EvidenceRerankRequest(
        request.query_fingerprint, request.filter_snapshot_ref, request.evidence_index_ref,
        request.retrieval_config_ref, request.rerank_config_ref,
        request.rerank_input_projection_version,
        canonical_rerank_input_hash(request.rerank_input_projection_version, candidates), candidates,
    )


def _validated_selections(
    request: EvidenceRetrievalKernelRequest,
    rerank_request: EvidenceRerankRequest,
    response: object,
) -> tuple[UntrustedKnowledgeEvidenceSelection, ...] | None:
    if not isinstance(response, EvidenceRerankSuccess):
        return None
    if (
        response.query_fingerprint != request.query_fingerprint
        or response.filter_snapshot_ref != request.filter_snapshot_ref
        or response.evidence_index_ref != request.evidence_index_ref
        or response.retrieval_config_ref != request.retrieval_config_ref
        or response.rerank_config_ref != request.rerank_config_ref
        or response.projection_version != rerank_request.projection_version
        or response.input_set_hash != rerank_request.input_set_hash
        or not _is_valid_artifact_ref(response.adapter_artifact_ref)
        or len(response.selections) > request.selection_limit
    ):
        return None
    candidates = {item.provenance.evidence_key: item for item in rerank_request.candidates}
    if len({item.evidence_key for item in response.selections}) != len(response.selections):
        return None
    resolved: list[UntrustedKnowledgeEvidenceSelection] = []
    for rank, item in enumerate(response.selections, start=1):
        candidate = candidates.get(item.evidence_key)
        if (
            not isinstance(item, EvidenceRerankSelection)
            or item.rerank_rank != rank
            or candidate is None
            or not _is_valid_score(item.rerank_score)
        ):
            return None
        resolved.append(UntrustedKnowledgeEvidenceSelection(candidate, item.rerank_rank, item.rerank_score))
    return tuple(resolved)


def _outcome(
    execution_status: KernelExecutionStatus,
    diagnostic_code: KernelDiagnosticCode,
) -> EvidenceRetrievalKernelOutcome:
    return EvidenceRetrievalKernelOutcome(execution_status, diagnostic_code)


def _query_binding_failure(
    request: EvidenceRetrievalKernelRequest,
    query_verifier: QueryBindingVerifierPort,
) -> EvidenceRetrievalKernelOutcome | None:
    try:
        verification = query_verifier.verify(
            request.normalized_query, request.query_fingerprint
        )
    except Exception:
        return _outcome(
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.QUERY_BINDING_DEPENDENCY_ERROR,
        )
    if isinstance(verification, QueryBindingVerificationFailure):
        diagnostic = (
            KernelDiagnosticCode.QUERY_BINDING_INVALID
            if verification.reason is QueryBindingFailureReason.INVALID_BINDING
            else KernelDiagnosticCode.QUERY_BINDING_DEPENDENCY_ERROR
        )
        status = (
            KernelExecutionStatus.VALIDATION_ERROR
            if verification.reason is QueryBindingFailureReason.INVALID_BINDING
            else KernelExecutionStatus.DEPENDENCY_ERROR
        )
        return _outcome(status, diagnostic)
    if not _is_matching_verification(verification, request.query_fingerprint):
        return _outcome(
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.QUERY_BINDING_RECEIPT_MISMATCH,
        )
    return None


def _is_matching_verification(
    verification: object,
    expected_fingerprint: QueryFingerprint,
) -> bool:
    return (
        isinstance(verification, QueryBindingVerificationSuccess)
        and verification.query_fingerprint == expected_fingerprint
        and _is_valid_fingerprint(verification.query_fingerprint)
        and _is_valid_artifact_ref(verification.verifier_artifact_ref)
    )


def _search_candidates(
    request: EvidenceRetrievalKernelRequest, search_port: EvidenceSearchPort
) -> tuple[KnowledgeEvidenceCandidate, ...] | None:
    all_hits: list[KnowledgeEvidenceSearchHit] = []
    stages = [EvidenceSearchStage.LEXICAL]
    if request.dense_limit:
        stages.append(EvidenceSearchStage.DENSE)
    for stage in stages:
        try:
            response = search_port.search(request, stage)
        except Exception:
            return None
        if not isinstance(response, EvidenceSearchSuccess) or not _is_valid_search_response(
            request, stage, response
        ):
            return None
        all_hits.extend(response.hits)
    grouped: dict[str, KnowledgeEvidenceCandidate] = {}
    for item in all_hits:
        current = grouped.get(item.provenance.evidence_key)
        signal = StageSignal(item.stage, item.rank, item.stage_score)
        if current is None:
            grouped[item.provenance.evidence_key] = KnowledgeEvidenceCandidate(
                item.provenance, item.content_text, (signal,)
            )
        elif current.provenance != item.provenance or current.content_text.reveal() != item.content_text.reveal():
            return None
        else:
            grouped[item.provenance.evidence_key] = KnowledgeEvidenceCandidate(
                current.provenance, current.content_text, current.stage_signals + (signal,)
            )
    return tuple(grouped[key] for key in sorted(grouped))


def _is_valid_search_response(
    request: EvidenceRetrievalKernelRequest, stage: EvidenceSearchStage, response: object
) -> bool:
    if not isinstance(response, EvidenceSearchSuccess):
        return False
    expected_config = request.lexical_config_ref if stage is EvidenceSearchStage.LEXICAL else request.dense_config_ref
    if (
        response.query_fingerprint != request.query_fingerprint
        or response.filter_snapshot_ref != request.filter_snapshot_ref
        or response.evidence_index_ref != request.evidence_index_ref
        or response.retrieval_config_ref != request.retrieval_config_ref
        or response.stage_config_ref != expected_config
        or response.stage is not stage
        or not _is_valid_artifact_ref(response.adapter_artifact_ref)
    ):
        return False
    limit = request.lexical_limit if stage is EvidenceSearchStage.LEXICAL else request.dense_limit
    if not isinstance(response, EvidenceSearchSuccess):
        return False
    return len(response.hits) <= limit and all(
        _is_valid_search_hit(item, stage, rank) for rank, item in enumerate(response.hits, start=1)
    )


def _is_valid_search_hit(item: object, stage: EvidenceSearchStage, expected_rank: int) -> bool:
    if not isinstance(item, KnowledgeEvidenceSearchHit):
        return False
    return (
        item.stage is stage
        and item.rank == expected_rank
        and _is_valid_score(item.stage_score)
        and _is_valid_provenance(item.provenance)
        and _is_valid_sensitive_text(item.content_text)
        and hashlib.sha256(item.content_text.reveal().encode("utf-8")).hexdigest()
        == item.provenance.content_sha256
    )


def _is_valid_score(value: object) -> bool:
    return isinstance(value, CanonicalScore) and bool(
        re.fullmatch(r"^(?:0|-?[1-9][0-9]*|-?(?:0|[1-9][0-9]*)\.[0-9]*[1-9])$", value.value)
    )


def _is_valid_provenance(value: object) -> bool:
    return isinstance(value, KnowledgeEvidenceProvenance) and all(
        _is_nonempty_nfc_string(item)
        for item in (
            value.evidence_key, value.knowledge_chunk_ref, value.source_version,
            value.locator, value.canonicalization_spec_version,
        )
    ) and _is_valid_artifact_ref(value.evidence_index_ref) and _is_valid_artifact_ref(value.source_snapshot_ref) and bool(_SHA256_RE.fullmatch(value.content_sha256))


def _is_valid_request(request: object) -> bool:
    if not isinstance(request, EvidenceRetrievalKernelRequest):
        return False

    if not _is_valid_sensitive_text(request.normalized_query):
        return False
    if not _is_valid_fingerprint(request.query_fingerprint):
        return False
    if not all(
        _is_valid_artifact_ref(reference)
        for reference in (
            request.filter_snapshot_ref,
            request.evidence_index_ref,
            request.retrieval_config_ref,
            request.lexical_config_ref,
            request.rerank_config_ref,
        )
    ):
        return False
    if request.dense_config_ref is not None and not _is_valid_artifact_ref(
        request.dense_config_ref
    ):
        return False
    if request.rerank_input_projection_version != RERANK_INPUT_PROJECTION_VERSION:
        return False
    if not all(_is_positive_int(limit) for limit in (request.lexical_limit, request.selection_limit)):
        return False
    if not _is_nonnegative_int(request.dense_limit):
        return False
    if (request.dense_limit == 0) is not (request.dense_config_ref is None):
        return False
    return request.selection_limit <= request.lexical_limit + request.dense_limit


def _is_valid_sensitive_text(value: object) -> bool:
    if not isinstance(value, SensitiveText):
        return False
    revealed = value.reveal()
    return isinstance(revealed, str) and bool(revealed.strip()) and _is_nfc(revealed)


def _is_valid_fingerprint(value: object) -> bool:
    return (
        isinstance(value, QueryFingerprint)
        and _is_nonempty_nfc_string(value.algorithm)
        and _is_nonempty_nfc_string(value.key_version)
        and isinstance(value.digest, str)
        and bool(_SHA256_RE.fullmatch(value.digest))
    )


def _is_valid_artifact_ref(value: object) -> bool:
    return (
        isinstance(value, ImmutableArtifactRef)
        and _is_nonempty_nfc_string(value.artifact_code)
        and _is_nonempty_nfc_string(value.version)
        and isinstance(value.content_sha256, str)
        and bool(_SHA256_RE.fullmatch(value.content_sha256))
    )


def _is_nonempty_nfc_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and _is_nfc(value)


def _is_nfc(value: str) -> bool:
    return value == unicodedata.normalize("NFC", value)


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
