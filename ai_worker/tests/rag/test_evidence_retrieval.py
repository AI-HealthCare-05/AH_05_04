import dataclasses
import hashlib
import json
from dataclasses import replace
from typing import Any, cast

import pytest

from ai_worker.tasks.rag.evidence_retrieval import (
    CanonicalScore,
    EvidenceRerankFailure,
    EvidenceRerankPort,
    EvidenceRerankRequest,
    EvidenceRerankSelection,
    EvidenceRerankSuccess,
    EvidenceRetrievalKernelRequest,
    EvidenceSearchStage,
    EvidenceSearchSuccess,
    ImmutableArtifactRef,
    KernelDiagnosticCode,
    KernelExecutionStatus,
    KnowledgeEvidenceCandidate,
    KnowledgeEvidenceProvenance,
    KnowledgeEvidenceSearchHit,
    QueryBindingFailureReason,
    QueryBindingVerificationFailure,
    QueryBindingVerificationSuccess,
    QueryFingerprint,
    SensitiveText,
    StageSignal,
    canonical_rerank_input_hash,
    retrieve_knowledge_evidence,
    to_sanitized_trace_dict,
)


def artifact(code: str) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(
        artifact_code=code,
        version=f"{code}@1",
        content_sha256="a" * 64,
    )


def fingerprint() -> QueryFingerprint:
    return QueryFingerprint(
        algorithm="HMAC-SHA-256",
        key_version="query-hmac@1",
        digest="b" * 64,
    )


def lexical_request() -> EvidenceRetrievalKernelRequest:
    return EvidenceRetrievalKernelRequest(
        normalized_query=SensitiveText("합성 복약 정보"),
        query_fingerprint=fingerprint(),
        filter_snapshot_ref=artifact("filter-snapshot"),
        evidence_index_ref=artifact("knowledge-index"),
        retrieval_config_ref=artifact("retrieval-config"),
        lexical_config_ref=artifact("lexical-config"),
        dense_config_ref=None,
        rerank_config_ref=artifact("rerank-config"),
        rerank_input_projection_version="knowledge-rerank-input-v1",
        lexical_limit=5,
        dense_limit=0,
        selection_limit=3,
    )


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def provenance() -> KnowledgeEvidenceProvenance:
    return KnowledgeEvidenceProvenance(
        evidence_key="knowledge:chunk-1",
        knowledge_chunk_ref="chunk-1",
        evidence_index_ref=artifact("knowledge-index"),
        source_snapshot_ref=artifact("source-snapshot"),
        source_version="mfds-synthetic@1",
        locator="$.records.synthetic-1",
        content_sha256=content_hash("합성 복약 근거"),
        canonicalization_spec_version="knowledge-text@1",
    )


def hit(stage: EvidenceSearchStage, score: str) -> KnowledgeEvidenceSearchHit:
    return KnowledgeEvidenceSearchHit(
        provenance=provenance(),
        stage=stage,
        rank=1,
        stage_score=CanonicalScore(score),
        content_text=SensitiveText("합성 복약 근거"),
    )


def search_success(
    request: EvidenceRetrievalKernelRequest,
    stage: EvidenceSearchStage,
    hits: tuple[KnowledgeEvidenceSearchHit, ...],
) -> EvidenceSearchSuccess:
    stage_config_ref = (
        request.lexical_config_ref
        if stage is EvidenceSearchStage.LEXICAL
        else request.dense_config_ref
    )
    assert stage_config_ref is not None
    return EvidenceSearchSuccess(
        request.query_fingerprint,
        request.filter_snapshot_ref,
        request.evidence_index_ref,
        request.retrieval_config_ref,
        stage_config_ref,
        stage,
        artifact("search-adapter"),
        hits,
    )


class QueryVerifier:
    def __init__(
        self,
        result: QueryBindingVerificationSuccess | QueryBindingVerificationFailure,
    ) -> None:
        self.result = result
        self.calls = 0

    def verify(
        self,
        query: SensitiveText,
        query_fingerprint: QueryFingerprint,
    ) -> QueryBindingVerificationSuccess | QueryBindingVerificationFailure:
        self.calls += 1
        return self.result


class NeverSearch:
    def __init__(self) -> None:
        self.calls = 0

    def search(
        self, request: EvidenceRetrievalKernelRequest, stage: EvidenceSearchStage
    ) -> EvidenceSearchSuccess:
        self.calls += 1
        raise AssertionError("search must not be called")


class NeverRerank:
    def rerank(
        self, request: EvidenceRerankRequest
    ) -> EvidenceRerankSuccess | EvidenceRerankFailure:
        raise AssertionError("rerank must not be called")


class SearchPort:
    def __init__(self, responses: dict[EvidenceSearchStage, EvidenceSearchSuccess]) -> None:
        self.responses = responses

    def search(
        self, request: EvidenceRetrievalKernelRequest, stage: EvidenceSearchStage
    ) -> EvidenceSearchSuccess:
        return self.responses[stage]


class RaisingSearchPort:
    def search(
        self, request: EvidenceRetrievalKernelRequest, stage: EvidenceSearchStage
    ) -> EvidenceSearchSuccess:
        raise RuntimeError("provider-secret")


class CapturingRerankPort:
    def __init__(self) -> None:
        self.request: EvidenceRerankRequest | None = None

    def rerank(self, request: EvidenceRerankRequest) -> EvidenceRerankFailure:
        self.request = request
        return EvidenceRerankFailure()


class SuccessfulRerankPort:
    def rerank(self, request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
        return EvidenceRerankSuccess(
            request.query_fingerprint,
            request.filter_snapshot_ref,
            request.evidence_index_ref,
            request.retrieval_config_ref,
            request.rerank_config_ref,
            request.projection_version,
            request.input_set_hash,
            artifact("rerank-adapter"),
            (EvidenceRerankSelection("knowledge:chunk-1", 1, CanonicalScore("0.95")),),
        )


class ReceiptMismatchRerankPort(SuccessfulRerankPort):
    def rerank(self, request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
        return replace(
            super().rerank(request),
            input_set_hash="f" * 64,
        )


class MalformedRerankPort(SuccessfulRerankPort):
    def rerank(self, request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
        return replace(super().rerank(request), selections=(object(),))  # type: ignore[arg-type]


def test_sensitive_text_redacts_representation_and_is_not_json_serializable() -> None:
    secret = "이 민감한 질의는 로그에 남으면 안 됩니다"
    value = SensitiveText(secret)

    assert str(value) == repr(value) == "<redacted>"
    assert value.reveal() == secret

    request = replace(lexical_request(), normalized_query=value)
    assert secret not in repr(request)
    with pytest.raises(TypeError):
        json.dumps(dataclasses.asdict(request))


@pytest.mark.parametrize(
    ("verification", "status", "diagnostic"),
    [
        (
            QueryBindingVerificationFailure(QueryBindingFailureReason.INVALID_BINDING),
            KernelExecutionStatus.VALIDATION_ERROR,
            KernelDiagnosticCode.QUERY_BINDING_INVALID,
        ),
        (
            QueryBindingVerificationFailure(QueryBindingFailureReason.DEPENDENCY_ERROR),
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.QUERY_BINDING_DEPENDENCY_ERROR,
        ),
        (
            QueryBindingVerificationSuccess(
                QueryFingerprint("HMAC-SHA-256", "query-hmac@1", "c" * 64),
                artifact("query-verifier"),
            ),
            KernelExecutionStatus.DEPENDENCY_ERROR,
            KernelDiagnosticCode.QUERY_BINDING_RECEIPT_MISMATCH,
        ),
    ],
)
def test_query_binding_failures_stop_before_search(
    verification: QueryBindingVerificationSuccess | QueryBindingVerificationFailure,
    status: KernelExecutionStatus,
    diagnostic: KernelDiagnosticCode,
) -> None:
    verifier = QueryVerifier(verification)
    search = NeverSearch()

    outcome = retrieve_knowledge_evidence(
        lexical_request(),
        query_verifier=verifier,
        search_port=search,
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is status
    assert outcome.diagnostic_code is diagnostic
    assert verifier.calls == 1
    assert search.calls == 0
    assert outcome.trace is not None
    assert to_sanitized_trace_dict(outcome.trace)["diagnostic_code"] == diagnostic.value


def test_search_results_normalize_same_evidence_into_one_candidate() -> None:
    request = replace(
        lexical_request(), dense_config_ref=artifact("dense-config"), dense_limit=5
    )
    search = SearchPort(
        {
            EvidenceSearchStage.LEXICAL: search_success(
                request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),)
            ),
            EvidenceSearchStage.DENSE: search_success(
                request, EvidenceSearchStage.DENSE, (hit(EvidenceSearchStage.DENSE, "0.8"),)
            ),
        }
    )
    rerank = CapturingRerankPort()

    retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(
            QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))
        ),
        search_port=search,
        rerank_port=rerank,
    )

    assert rerank.request is not None
    assert rerank.request.candidates[0].stage_signals == (
        StageSignal(EvidenceSearchStage.LEXICAL, 1, CanonicalScore("0.9")),
        StageSignal(EvidenceSearchStage.DENSE, 1, CanonicalScore("0.8")),
    )


def test_valid_rerank_rebinds_selection_to_canonical_candidate() -> None:
    request = lexical_request()
    search = SearchPort(
        {
            EvidenceSearchStage.LEXICAL: search_success(
                request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),)
            )
        }
    )
    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(
            QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))
        ),
        search_port=search,
        rerank_port=SuccessfulRerankPort(),
    )

    assert outcome.execution_status is KernelExecutionStatus.SUCCEEDED
    assert outcome.diagnostic_code is KernelDiagnosticCode.CANDIDATES_RERANKED
    assert outcome.untrusted_selections[0].candidate.provenance.evidence_key == "knowledge:chunk-1"
    assert outcome.trace is not None
    trace = cast(dict[str, Any], to_sanitized_trace_dict(outcome.trace))
    assert "합성 복약 정보" not in json.dumps(trace, ensure_ascii=False)
    assert "normalized_query" not in trace
    assert trace["adapter_artifacts"]["query_verifier"]["artifact_code"] == "query-verifier"
    assert trace["adapter_artifacts"]["lexical"]["artifact_code"] == "search-adapter"
    assert trace["adapter_artifacts"]["rerank"]["artifact_code"] == "rerank-adapter"
    assert trace["hits"][0]["content_sha256"] == content_hash("합성 복약 근거")
    assert trace["hits"][0]["evidence_index_ref"]["artifact_code"] == "knowledge-index"
    assert trace["hits"][0]["canonicalization_spec_version"] == "knowledge-text@1"
    assert "content_text" not in trace["hits"][0]
    assert trace["selections"] == [
        {"evidence_key": "knowledge:chunk-1", "rerank_rank": 1, "rerank_score": "0.95"}
    ]


def test_hit_from_different_evidence_index_fails_closed() -> None:
    request = lexical_request()
    wrong = replace(
        hit(EvidenceSearchStage.LEXICAL, "0.9"),
        provenance=replace(provenance(), evidence_index_ref=artifact("other-index")),
    )
    search = SearchPort(
        {EvidenceSearchStage.LEXICAL: search_success(request, EvidenceSearchStage.LEXICAL, (wrong,))}
    )

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=search,
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


def test_rerank_hash_binds_complete_provenance() -> None:
    candidate = KnowledgeEvidenceCandidate(
        provenance(),
        SensitiveText("합성 복약 근거"),
        (StageSignal(EvidenceSearchStage.LEXICAL, 1, CanonicalScore("0.9")),),
    )
    changed = replace(candidate, provenance=replace(candidate.provenance, locator="$.changed"))

    assert canonical_rerank_input_hash("knowledge-rerank-input-v1", (candidate,)) != canonical_rerank_input_hash(
        "knowledge-rerank-input-v1", (changed,)
    )


def test_rerank_projection_has_approved_golden_hash() -> None:
    candidate = KnowledgeEvidenceCandidate(
        provenance(),
        SensitiveText("합성 복약 근거"),
        (
            StageSignal(EvidenceSearchStage.LEXICAL, 1, CanonicalScore("0.9")),
            StageSignal(EvidenceSearchStage.DENSE, 2, CanonicalScore("-0.25")),
        ),
    )

    assert canonical_rerank_input_hash(
        "knowledge-rerank-input-v1", (candidate,)
    ) == "e01b174ebf70c08b24db48efafd908219d77fa242502fe54443fe753fcce894c"


def test_invalid_request_does_not_create_trace_from_untrusted_metadata() -> None:
    request = replace(
        lexical_request(),
        query_fingerprint=replace(
            fingerprint(), algorithm="PATIENT-SECRET", digest="invalid"
        ),
    )

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=NeverSearch(),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.REQUEST_INVALID
    assert outcome.trace is None


def test_search_exception_has_dependency_diagnostic() -> None:
    outcome = retrieve_knowledge_evidence(
        lexical_request(),
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=RaisingSearchPort(),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_DEPENDENCY_ERROR


def test_search_receipt_mismatch_has_receipt_diagnostic() -> None:
    request = lexical_request()
    response = replace(
        search_success(request, EvidenceSearchStage.LEXICAL, ()),
        filter_snapshot_ref=artifact("other-filter"),
    )
    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RECEIPT_MISMATCH
    assert outcome.trace is not None
    assert outcome.trace.lexical_adapter_artifact_ref == artifact("search-adapter")


def test_duplicate_evidence_key_in_one_stage_fails_closed() -> None:
    request = lexical_request()
    first = hit(EvidenceSearchStage.LEXICAL, "0.9")
    second = replace(first, rank=2, stage_score=CanonicalScore("0.8"))
    response = search_success(
        request, EvidenceSearchStage.LEXICAL, (first, second)
    )
    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


@pytest.mark.parametrize(
    ("rerank_port", "diagnostic"),
    [
        (ReceiptMismatchRerankPort(), KernelDiagnosticCode.RERANK_RECEIPT_MISMATCH),
        (MalformedRerankPort(), KernelDiagnosticCode.RERANK_RESULT_INVALID),
    ],
)
def test_rerank_failures_are_classified_and_do_not_escape(
    rerank_port: EvidenceRerankPort, diagnostic: KernelDiagnosticCode
) -> None:
    request = lexical_request()
    response = search_success(
        request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),)
    )

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=rerank_port,
    )

    assert outcome.diagnostic_code is diagnostic
    assert outcome.trace is not None
    assert outcome.trace.rerank_adapter_artifact_ref == artifact("rerank-adapter")


def test_malformed_score_fails_closed_without_exception() -> None:
    request = lexical_request()
    malformed = replace(
        hit(EvidenceSearchStage.LEXICAL, "0.9"),
        stage_score=CanonicalScore(None),  # type: ignore[arg-type]
    )
    response = search_success(
        request, EvidenceSearchStage.LEXICAL, (malformed,)
    )

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


def test_malformed_nested_provenance_fails_closed_without_exception() -> None:
    request = lexical_request()
    malformed = replace(
        hit(EvidenceSearchStage.LEXICAL, "0.9"),
        provenance=object(),  # type: ignore[arg-type]
    )
    response = search_success(request, EvidenceSearchStage.LEXICAL, (malformed,))

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


def test_unhashable_rerank_key_fails_closed_without_exception() -> None:
    request = lexical_request()
    response = search_success(
        request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),)
    )

    class UnhashableKeyRerank(SuccessfulRerankPort):
        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            selection = EvidenceRerankSelection([], 1, CanonicalScore("0.95"))  # type: ignore[arg-type]
            return replace(super().rerank(rerank_request), selections=(selection,))

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=UnhashableKeyRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.RERANK_RESULT_INVALID


def test_malformed_query_failure_reason_is_receipt_mismatch() -> None:
    malformed = QueryBindingVerificationFailure("not-an-enum")  # type: ignore[arg-type]
    outcome = retrieve_knowledge_evidence(
        lexical_request(),
        query_verifier=QueryVerifier(malformed),
        search_port=NeverSearch(),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.QUERY_BINDING_RECEIPT_MISMATCH
