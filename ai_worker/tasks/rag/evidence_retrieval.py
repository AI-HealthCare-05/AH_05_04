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


class EvidenceRerankPort(Protocol):
    def rerank(self, request: EvidenceRerankRequest) -> EvidenceRerankSuccess | EvidenceRerankFailure: ...


@dataclass(frozen=True, slots=True)
class UntrustedKnowledgeEvidenceSelection:
    candidate: KnowledgeEvidenceCandidate
    rerank_rank: int
    rerank_score: CanonicalScore


@dataclass(frozen=True, slots=True)
class DiagnosticHitRecord:
    evidence_key: str
    knowledge_chunk_ref: str
    stage: EvidenceSearchStage
    rank: int
    stage_score: CanonicalScore
    content_sha256: str
    evidence_index_ref: ImmutableArtifactRef
    source_snapshot_ref: ImmutableArtifactRef
    source_version: str
    locator: str
    canonicalization_spec_version: str


@dataclass(frozen=True, slots=True)
class DiagnosticSelectionRecord:
    evidence_key: str
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
    query_verifier_artifact_ref: ImmutableArtifactRef | None = None
    lexical_adapter_artifact_ref: ImmutableArtifactRef | None = None
    dense_adapter_artifact_ref: ImmutableArtifactRef | None = None
    rerank_adapter_artifact_ref: ImmutableArtifactRef | None = None
    hits: tuple[DiagnosticHitRecord, ...] = ()
    selections: tuple[DiagnosticSelectionRecord, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceRetrievalKernelOutcome:
    execution_status: KernelExecutionStatus
    diagnostic_code: KernelDiagnosticCode
    untrusted_selections: tuple[UntrustedKnowledgeEvidenceSelection, ...] = ()
    failure_details: tuple[str, ...] = ()
    trace: EvidenceRetrievalDiagnosticTrace | None = None


@dataclass(frozen=True, slots=True)
class _SearchExecution:
    candidates: tuple[KnowledgeEvidenceCandidate, ...]
    adapter_artifacts: tuple[tuple[EvidenceSearchStage, ImmutableArtifactRef], ...]
    hits: tuple[DiagnosticHitRecord, ...]


@dataclass(frozen=True, slots=True)
class _SearchExecutionFailure:
    diagnostic_code: KernelDiagnosticCode
    partial_execution: _SearchExecution


def retrieve_knowledge_evidence(
    request: EvidenceRetrievalKernelRequest,
    *,
    query_verifier: QueryBindingVerifierPort,
    search_port: EvidenceSearchPort,
    rerank_port: EvidenceRerankPort,
) -> EvidenceRetrievalKernelOutcome:
    """Validate and bind a query before later retrieval stages are allowed."""
    if not _is_valid_request(request):
        return _outcome(
            KernelExecutionStatus.VALIDATION_ERROR,
            KernelDiagnosticCode.REQUEST_INVALID,
        )

    binding = _query_binding(request, query_verifier)
    if isinstance(binding, EvidenceRetrievalKernelOutcome):
        return binding

    search_result = _search_candidates(request, search_port)
    if isinstance(search_result, _SearchExecutionFailure):
        return _outcome(
            KernelExecutionStatus.DEPENDENCY_ERROR,
            search_result.diagnostic_code,
            request,
            binding,
            search_result.partial_execution,
        )
    candidates = search_result.candidates
    if not candidates:
        return _outcome(KernelExecutionStatus.SUCCEEDED, KernelDiagnosticCode.NO_HITS, request, binding, search_result)
    rerank_request = _rerank_request(request, candidates)
    try:
        response = rerank_port.rerank(rerank_request)
    except Exception:
        return _outcome(
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.RERANK_DEPENDENCY_ERROR,
            request,
            binding,
            search_result,
        )
    if isinstance(response, EvidenceRerankFailure):
        return _outcome(
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.RERANK_DEPENDENCY_ERROR,
            request,
            binding,
            search_result,
        )
    if not isinstance(response, EvidenceRerankSuccess):
        return _outcome(
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.RERANK_RESULT_INVALID,
            request,
            binding,
            search_result,
        )
    rerank_adapter = response.adapter_artifact_ref if _is_valid_artifact_ref(response.adapter_artifact_ref) else None
    observed_selections = _traceable_selection_records(
        response.selections,
        frozenset(item.provenance.evidence_key for item in candidates),
    )
    if not _rerank_receipt_matches(request, rerank_request, response):
        return _outcome(
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.RERANK_RECEIPT_MISMATCH,
            request,
            binding,
            search_result,
            rerank_adapter,
            observed_selections,
        )
    selections = _validated_selections(request, rerank_request, response)
    if selections is None:
        return _outcome(
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.RERANK_RESULT_INVALID,
            request,
            binding,
            search_result,
            rerank_adapter,
            observed_selections,
        )
    return EvidenceRetrievalKernelOutcome(
        KernelExecutionStatus.SUCCEEDED,
        KernelDiagnosticCode.CANDIDATES_RERANKED,
        selections,
        trace=_trace(
            request,
            KernelExecutionStatus.SUCCEEDED,
            KernelDiagnosticCode.CANDIDATES_RERANKED,
            binding,
            search_result,
            response.adapter_artifact_ref,
            selections,
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
        "adapter_artifacts": {
            "query_verifier": _artifact_dict(trace.query_verifier_artifact_ref),
            "lexical": _artifact_dict(trace.lexical_adapter_artifact_ref),
            "dense": _artifact_dict(trace.dense_adapter_artifact_ref),
            "rerank": _artifact_dict(trace.rerank_adapter_artifact_ref),
        },
        "hits": [_diagnostic_hit_dict(item) for item in trace.hits],
        "selections": [
            {
                "evidence_key": item.evidence_key,
                "rerank_rank": item.rerank_rank,
                "rerank_score": item.rerank_score.value,
            }
            for item in trace.selections
        ],
    }


def _trace(
    request: EvidenceRetrievalKernelRequest,
    status: KernelExecutionStatus,
    diagnostic: KernelDiagnosticCode,
    query_verifier_artifact_ref: ImmutableArtifactRef | None = None,
    search_result: _SearchExecution | None = None,
    rerank_adapter_artifact_ref: ImmutableArtifactRef | None = None,
    selections: tuple[UntrustedKnowledgeEvidenceSelection, ...] = (),
    diagnostic_selections: tuple[DiagnosticSelectionRecord, ...] = (),
) -> EvidenceRetrievalDiagnosticTrace:
    adapters = dict(search_result.adapter_artifacts) if search_result else {}
    return EvidenceRetrievalDiagnosticTrace(
        request.query_fingerprint,
        request.filter_snapshot_ref,
        request.evidence_index_ref,
        request.retrieval_config_ref,
        request.lexical_config_ref,
        request.dense_config_ref,
        request.rerank_config_ref,
        status,
        diagnostic,
        query_verifier_artifact_ref,
        adapters.get(EvidenceSearchStage.LEXICAL),
        adapters.get(EvidenceSearchStage.DENSE),
        rerank_adapter_artifact_ref,
        search_result.hits if search_result else (),
        diagnostic_selections
        or tuple(
            DiagnosticSelectionRecord(
                item.candidate.provenance.evidence_key,
                item.rerank_rank,
                item.rerank_score,
            )
            for item in selections
        ),
    )


def _diagnostic_hit_dict(item: DiagnosticHitRecord) -> dict[str, object]:
    return {
        "evidence_key": item.evidence_key,
        "knowledge_chunk_ref": item.knowledge_chunk_ref,
        "stage": item.stage.value,
        "rank": item.rank,
        "stage_score": item.stage_score.value,
        "content_sha256": item.content_sha256,
        "evidence_index_ref": _artifact_dict(item.evidence_index_ref),
        "source_snapshot_ref": _artifact_dict(item.source_snapshot_ref),
        "source_version": item.source_version,
        "locator": item.locator,
        "canonicalization_spec_version": item.canonicalization_spec_version,
    }


def _artifact_dict(reference: ImmutableArtifactRef | None) -> dict[str, str] | None:
    if reference is None:
        return None
    return {
        "artifact_code": reference.artifact_code,
        "version": reference.version,
        "content_sha256": reference.content_sha256,
    }


def canonical_rerank_input_hash(projection_version: str, candidates: tuple[KnowledgeEvidenceCandidate, ...]) -> str:
    payload = {
        "projection_version": projection_version,
        "candidates": [
            {
                "evidence_key": item.provenance.evidence_key,
                "knowledge_chunk_ref": item.provenance.knowledge_chunk_ref,
                "evidence_index_ref": _artifact_dict(item.provenance.evidence_index_ref),
                "source_snapshot_ref": _artifact_dict(item.provenance.source_snapshot_ref),
                "source_version": item.provenance.source_version,
                "locator": item.provenance.locator,
                "content_sha256": item.provenance.content_sha256,
                "canonicalization_spec_version": item.provenance.canonicalization_spec_version,
                "stage_signals": [
                    {"stage": signal.stage.value, "rank": signal.rank, "score": signal.score.value}
                    for signal in sorted(
                        item.stage_signals,
                        key=lambda value: 0 if value.stage is EvidenceSearchStage.LEXICAL else 1,
                    )
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
        request.query_fingerprint,
        request.filter_snapshot_ref,
        request.evidence_index_ref,
        request.retrieval_config_ref,
        request.rerank_config_ref,
        request.rerank_input_projection_version,
        canonical_rerank_input_hash(request.rerank_input_projection_version, candidates),
        candidates,
    )


def _validated_selections(
    request: EvidenceRetrievalKernelRequest,
    rerank_request: EvidenceRerankRequest,
    response: EvidenceRerankSuccess,
) -> tuple[UntrustedKnowledgeEvidenceSelection, ...] | None:
    if not isinstance(response.selections, tuple) or len(response.selections) > request.selection_limit:
        return None
    if not all(
        isinstance(item, EvidenceRerankSelection) and _is_nonempty_nfc_string(item.evidence_key)
        for item in response.selections
    ):
        return None
    candidates = {item.provenance.evidence_key: item for item in rerank_request.candidates}
    if len({item.evidence_key for item in response.selections}) != len(response.selections):
        return None
    resolved: list[UntrustedKnowledgeEvidenceSelection] = []
    for rank, item in enumerate(response.selections, start=1):
        candidate = candidates.get(item.evidence_key)
        if (
            not _is_positive_int(item.rerank_rank)
            or item.rerank_rank != rank
            or candidate is None
            or not _is_valid_score(item.rerank_score)
        ):
            return None
        resolved.append(UntrustedKnowledgeEvidenceSelection(candidate, item.rerank_rank, item.rerank_score))
    return tuple(resolved)


def _traceable_selection_records(
    value: object,
    allowed_evidence_keys: frozenset[str],
) -> tuple[DiagnosticSelectionRecord, ...]:
    if not isinstance(value, tuple):
        return ()
    records: list[DiagnosticSelectionRecord] = []
    for item in value:
        if (
            not isinstance(item, EvidenceRerankSelection)
            or not _is_nonempty_nfc_string(item.evidence_key)
            or item.evidence_key not in allowed_evidence_keys
            or not _is_positive_int(item.rerank_rank)
            or not _is_valid_score(item.rerank_score)
        ):
            return ()
        records.append(DiagnosticSelectionRecord(item.evidence_key, item.rerank_rank, item.rerank_score))
    return tuple(records)


def _rerank_receipt_matches(
    request: EvidenceRetrievalKernelRequest,
    rerank_request: EvidenceRerankRequest,
    response: EvidenceRerankSuccess,
) -> bool:
    return not (
        response.query_fingerprint != request.query_fingerprint
        or response.filter_snapshot_ref != request.filter_snapshot_ref
        or response.evidence_index_ref != request.evidence_index_ref
        or response.retrieval_config_ref != request.retrieval_config_ref
        or response.rerank_config_ref != request.rerank_config_ref
        or response.projection_version != rerank_request.projection_version
        or response.input_set_hash != rerank_request.input_set_hash
        or not _is_valid_artifact_ref(response.adapter_artifact_ref)
    )


def _outcome(
    execution_status: KernelExecutionStatus,
    diagnostic_code: KernelDiagnosticCode,
    request: EvidenceRetrievalKernelRequest | None = None,
    query_verifier_artifact_ref: ImmutableArtifactRef | None = None,
    search_result: _SearchExecution | None = None,
    rerank_adapter_artifact_ref: ImmutableArtifactRef | None = None,
    diagnostic_selections: tuple[DiagnosticSelectionRecord, ...] = (),
) -> EvidenceRetrievalKernelOutcome:
    trace = None
    if isinstance(request, EvidenceRetrievalKernelRequest):
        trace = _trace(
            request,
            execution_status,
            diagnostic_code,
            query_verifier_artifact_ref,
            search_result,
            rerank_adapter_artifact_ref,
            diagnostic_selections=diagnostic_selections,
        )
    return EvidenceRetrievalKernelOutcome(execution_status, diagnostic_code, trace=trace)


def _query_binding(
    request: EvidenceRetrievalKernelRequest,
    query_verifier: QueryBindingVerifierPort,
) -> ImmutableArtifactRef | EvidenceRetrievalKernelOutcome:
    try:
        verification = query_verifier.verify(request.normalized_query, request.query_fingerprint)
    except Exception:
        return _outcome(
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.QUERY_BINDING_DEPENDENCY_ERROR,
            request,
        )
    if isinstance(verification, QueryBindingVerificationFailure):
        if verification.reason is QueryBindingFailureReason.INVALID_BINDING:
            return _outcome(
                KernelExecutionStatus.VALIDATION_ERROR,
                KernelDiagnosticCode.QUERY_BINDING_INVALID,
                request,
            )
        if verification.reason is QueryBindingFailureReason.DEPENDENCY_ERROR:
            return _outcome(
                KernelExecutionStatus.DEPENDENCY_ERROR,
                KernelDiagnosticCode.QUERY_BINDING_DEPENDENCY_ERROR,
                request,
            )
        return _outcome(
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.QUERY_BINDING_RECEIPT_MISMATCH,
            request,
        )
    if not _is_matching_verification(verification, request.query_fingerprint):
        return _outcome(
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.QUERY_BINDING_RECEIPT_MISMATCH,
            request,
        )
    return verification.verifier_artifact_ref


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
) -> _SearchExecution | _SearchExecutionFailure:
    all_hits: list[KnowledgeEvidenceSearchHit] = []
    adapter_artifacts: dict[EvidenceSearchStage, ImmutableArtifactRef] = {}
    stages = [EvidenceSearchStage.LEXICAL]
    if request.dense_limit:
        stages.append(EvidenceSearchStage.DENSE)
    for stage in stages:
        try:
            response = search_port.search(request, stage)
        except Exception:
            return _search_failure(KernelDiagnosticCode.SEARCH_DEPENDENCY_ERROR, all_hits, adapter_artifacts)
        if isinstance(response, EvidenceSearchFailure):
            return _search_failure(KernelDiagnosticCode.SEARCH_DEPENDENCY_ERROR, all_hits, adapter_artifacts)
        if not isinstance(response, EvidenceSearchSuccess):
            return _search_failure(KernelDiagnosticCode.SEARCH_RESULT_INVALID, all_hits, adapter_artifacts)
        if _is_valid_artifact_ref(response.adapter_artifact_ref):
            adapter_artifacts[stage] = response.adapter_artifact_ref
        if not _search_receipt_matches(request, stage, response):
            return _search_failure(KernelDiagnosticCode.SEARCH_RECEIPT_MISMATCH, all_hits, adapter_artifacts)
        if not _search_hits_are_valid(request, stage, response):
            return _search_failure(KernelDiagnosticCode.SEARCH_RESULT_INVALID, all_hits, adapter_artifacts)
        all_hits.extend(response.hits)
    normalized = _normalize_candidates(all_hits)
    if isinstance(normalized, KernelDiagnosticCode):
        return _search_failure(normalized, all_hits, adapter_artifacts)
    candidates = normalized
    return _search_execution(candidates, all_hits, adapter_artifacts)


def _search_failure(
    diagnostic_code: KernelDiagnosticCode,
    hits: list[KnowledgeEvidenceSearchHit],
    adapter_artifacts: dict[EvidenceSearchStage, ImmutableArtifactRef],
) -> _SearchExecutionFailure:
    return _SearchExecutionFailure(
        diagnostic_code,
        _search_execution((), hits, adapter_artifacts),
    )


def _search_execution(
    candidates: tuple[KnowledgeEvidenceCandidate, ...],
    hits: list[KnowledgeEvidenceSearchHit],
    adapter_artifacts: dict[EvidenceSearchStage, ImmutableArtifactRef],
) -> _SearchExecution:
    diagnostic_hits = tuple(
        DiagnosticHitRecord(
            item.provenance.evidence_key,
            item.provenance.knowledge_chunk_ref,
            item.stage,
            item.rank,
            item.stage_score,
            item.provenance.content_sha256,
            item.provenance.evidence_index_ref,
            item.provenance.source_snapshot_ref,
            item.provenance.source_version,
            item.provenance.locator,
            item.provenance.canonicalization_spec_version,
        )
        for item in hits
    )
    ordered_adapters = tuple(
        (stage, adapter_artifacts[stage])
        for stage in (EvidenceSearchStage.LEXICAL, EvidenceSearchStage.DENSE)
        if stage in adapter_artifacts
    )
    return _SearchExecution(candidates, ordered_adapters, diagnostic_hits)


def _normalize_candidates(
    all_hits: list[KnowledgeEvidenceSearchHit],
) -> tuple[KnowledgeEvidenceCandidate, ...] | KernelDiagnosticCode:
    grouped: dict[str, KnowledgeEvidenceCandidate] = {}
    stage_keys: set[tuple[EvidenceSearchStage, str]] = set()
    chunk_keys: dict[str, str] = {}
    for item in all_hits:
        stage_key = (item.stage, item.provenance.evidence_key)
        bound_key = chunk_keys.setdefault(item.provenance.knowledge_chunk_ref, item.provenance.evidence_key)
        if stage_key in stage_keys or bound_key != item.provenance.evidence_key:
            return KernelDiagnosticCode.SEARCH_RESULT_INVALID
        stage_keys.add(stage_key)
        current = grouped.get(item.provenance.evidence_key)
        signal = StageSignal(item.stage, item.rank, item.stage_score)
        if current is None:
            grouped[item.provenance.evidence_key] = KnowledgeEvidenceCandidate(
                item.provenance, item.content_text, (signal,)
            )
        elif current.provenance != item.provenance or current.content_text.reveal() != item.content_text.reveal():
            return KernelDiagnosticCode.SEARCH_RESULT_INVALID
        else:
            grouped[item.provenance.evidence_key] = KnowledgeEvidenceCandidate(
                current.provenance, current.content_text, current.stage_signals + (signal,)
            )
    return tuple(grouped[key] for key in sorted(grouped))


def _search_receipt_matches(
    request: EvidenceRetrievalKernelRequest,
    stage: EvidenceSearchStage,
    response: EvidenceSearchSuccess,
) -> bool:
    expected_config = request.lexical_config_ref if stage is EvidenceSearchStage.LEXICAL else request.dense_config_ref
    return not (
        response.query_fingerprint != request.query_fingerprint
        or response.filter_snapshot_ref != request.filter_snapshot_ref
        or response.evidence_index_ref != request.evidence_index_ref
        or response.retrieval_config_ref != request.retrieval_config_ref
        or response.stage_config_ref != expected_config
        or response.stage is not stage
        or not _is_valid_artifact_ref(response.adapter_artifact_ref)
    )


def _search_hits_are_valid(
    request: EvidenceRetrievalKernelRequest,
    stage: EvidenceSearchStage,
    response: EvidenceSearchSuccess,
) -> bool:
    limit = request.lexical_limit if stage is EvidenceSearchStage.LEXICAL else request.dense_limit
    if not isinstance(response.hits, tuple):
        return False
    return len(response.hits) <= limit and all(
        _is_valid_search_hit(item, stage, rank, request.evidence_index_ref)
        for rank, item in enumerate(response.hits, start=1)
    )


def _is_valid_search_hit(
    item: object,
    stage: EvidenceSearchStage,
    expected_rank: int,
    expected_evidence_index_ref: ImmutableArtifactRef,
) -> bool:
    if not isinstance(item, KnowledgeEvidenceSearchHit):
        return False
    return (
        item.stage is stage
        and _is_positive_int(item.rank)
        and item.rank == expected_rank
        and _is_valid_score(item.stage_score)
        and _is_valid_provenance(item.provenance)
        and item.provenance.evidence_index_ref == expected_evidence_index_ref
        and _is_valid_sensitive_text(item.content_text)
        and hashlib.sha256(item.content_text.reveal().encode("utf-8")).hexdigest() == item.provenance.content_sha256
    )


def _is_valid_score(value: object) -> bool:
    return (
        isinstance(value, CanonicalScore)
        and isinstance(value.value, str)
        and bool(re.fullmatch(r"^(?:0|-?[1-9][0-9]*|-?(?:0|[1-9][0-9]*)\.[0-9]*[1-9])$", value.value))
    )


def _is_valid_provenance(value: object) -> bool:
    return (
        isinstance(value, KnowledgeEvidenceProvenance)
        and all(
            _is_nonempty_nfc_string(item)
            for item in (
                value.evidence_key,
                value.knowledge_chunk_ref,
                value.source_version,
                value.locator,
                value.canonicalization_spec_version,
            )
        )
        and _is_valid_artifact_ref(value.evidence_index_ref)
        and _is_valid_artifact_ref(value.source_snapshot_ref)
        and isinstance(value.content_sha256, str)
        and bool(_SHA256_RE.fullmatch(value.content_sha256))
    )


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
    if request.dense_config_ref is not None and not _is_valid_artifact_ref(request.dense_config_ref):
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
