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
    EvidenceRetrievalKernelOutcome,
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
    stage_config_ref = request.lexical_config_ref if stage is EvidenceSearchStage.LEXICAL else request.dense_config_ref
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

    def search(self, request: EvidenceRetrievalKernelRequest, stage: EvidenceSearchStage) -> EvidenceSearchSuccess:
        self.calls += 1
        raise AssertionError("search must not be called")


class NeverRerank:
    def rerank(self, request: EvidenceRerankRequest) -> EvidenceRerankSuccess | EvidenceRerankFailure:
        raise AssertionError("rerank must not be called")


class SearchPort:
    def __init__(self, responses: dict[EvidenceSearchStage, EvidenceSearchSuccess]) -> None:
        self.responses = responses

    def search(self, request: EvidenceRetrievalKernelRequest, stage: EvidenceSearchStage) -> EvidenceSearchSuccess:
        return self.responses[stage]


class RaisingSearchPort:
    def search(self, request: EvidenceRetrievalKernelRequest, stage: EvidenceSearchStage) -> EvidenceSearchSuccess:
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
    request = replace(lexical_request(), dense_config_ref=artifact("dense-config"), dense_limit=5)
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
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
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
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
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
    assert trace["dense_config_ref"] is None
    assert trace["adapter_artifacts"]["dense"] is None
    assert trace["adapter_artifacts"]["rerank"]["artifact_code"] == "rerank-adapter"
    assert trace["hits"][0]["content_sha256"] == content_hash("합성 복약 근거")
    assert trace["hits"][0]["evidence_index_ref"]["artifact_code"] == "knowledge-index"
    assert trace["hits"][0]["canonicalization_spec_version"] == "knowledge-text@1"
    assert "content_text" not in trace["hits"][0]
    assert trace["selections"] == [{"evidence_key": "knowledge:chunk-1", "rerank_rank": 1, "rerank_score": "0.95"}]


def test_hit_from_different_evidence_index_fails_closed() -> None:
    request = lexical_request()
    wrong = replace(
        hit(EvidenceSearchStage.LEXICAL, "0.9"),
        provenance=replace(provenance(), evidence_index_ref=artifact("other-index")),
    )
    search = SearchPort({EvidenceSearchStage.LEXICAL: search_success(request, EvidenceSearchStage.LEXICAL, (wrong,))})

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

    assert (
        canonical_rerank_input_hash("knowledge-rerank-input-v1", (candidate,))
        == "e01b174ebf70c08b24db48efafd908219d77fa242502fe54443fe753fcce894c"
    )


def test_lexical_only_rerank_projection_has_approved_golden_hash() -> None:
    candidate = KnowledgeEvidenceCandidate(
        provenance(),
        SensitiveText("합성 복약 근거"),
        (StageSignal(EvidenceSearchStage.LEXICAL, 1, CanonicalScore("0.9")),),
    )

    assert (
        canonical_rerank_input_hash("knowledge-rerank-input-v1", (candidate,))
        == "85c14f49fa90742170a548ddc7baae0c79e683aee0e4efe8956748c2058365d1"
    )


def test_rerank_projection_sorts_multiple_candidates_by_utf8_key() -> None:
    first = KnowledgeEvidenceCandidate(
        provenance(),
        SensitiveText("합성 복약 근거"),
        (StageSignal(EvidenceSearchStage.LEXICAL, 1, CanonicalScore("0.9")),),
    )
    second_text = "합성 두 번째 근거"
    second = KnowledgeEvidenceCandidate(
        replace(
            provenance(),
            evidence_key="knowledge:chunk-2",
            knowledge_chunk_ref="chunk-2",
            locator="$.records.synthetic-2",
            content_sha256=content_hash(second_text),
        ),
        SensitiveText(second_text),
        (StageSignal(EvidenceSearchStage.LEXICAL, 2, CanonicalScore("0.8")),),
    )

    expected = "3fd43e8bb5eeac9018347e74bdd0c9f8b6497b790f68186a7ac992039f3fd2ee"
    assert canonical_rerank_input_hash("knowledge-rerank-input-v1", (second, first)) == expected
    assert canonical_rerank_input_hash("knowledge-rerank-input-v1", (first, second)) == expected


def test_invalid_request_does_not_create_trace_from_untrusted_metadata() -> None:
    request = replace(
        lexical_request(),
        query_fingerprint=replace(fingerprint(), algorithm="PATIENT-SECRET", digest="invalid"),
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
    assert "provider-secret" not in repr(outcome)
    assert outcome.trace is not None
    assert "provider-secret" not in json.dumps(to_sanitized_trace_dict(outcome.trace), ensure_ascii=False)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("query_fingerprint", QueryFingerprint("HMAC-SHA-256", "query-hmac@1", "c" * 64)),
        ("filter_snapshot_ref", artifact("other-filter")),
        ("evidence_index_ref", artifact("other-index")),
        ("retrieval_config_ref", artifact("other-retrieval")),
        ("stage_config_ref", artifact("other-stage")),
        ("stage", EvidenceSearchStage.DENSE),
        ("adapter_artifact_ref", replace(artifact("search-adapter"), content_sha256="invalid")),
    ],
)
def test_search_receipt_mismatch_has_receipt_diagnostic(field: str, value: object) -> None:
    request = lexical_request()
    response = replace(
        search_success(request, EvidenceSearchStage.LEXICAL, ()),
        **{field: value},  # type: ignore[arg-type]
    )
    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RECEIPT_MISMATCH
    assert outcome.trace is not None
    expected_adapter = None if field == "adapter_artifact_ref" else artifact("search-adapter")
    assert outcome.trace.lexical_adapter_artifact_ref == expected_adapter


def test_duplicate_evidence_key_in_one_stage_fails_closed() -> None:
    request = lexical_request()
    first = hit(EvidenceSearchStage.LEXICAL, "0.9")
    second = replace(first, rank=2, stage_score=CanonicalScore("0.8"))
    response = search_success(request, EvidenceSearchStage.LEXICAL, (first, second))
    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


@pytest.mark.parametrize("invalid_shape", ["rank-gap", "limit-exceeded"])
def test_invalid_search_hit_sequence_fails_closed(invalid_shape: str) -> None:
    request = lexical_request()
    hit_count = 2 if invalid_shape == "rank-gap" else 6
    hits: list[KnowledgeEvidenceSearchHit] = []
    for position in range(1, hit_count + 1):
        rank = 3 if invalid_shape == "rank-gap" and position == 2 else position
        text = f"합성 검색 근거 {position}"
        hits.append(
            KnowledgeEvidenceSearchHit(
                replace(
                    provenance(),
                    evidence_key=f"knowledge:search-{position}",
                    knowledge_chunk_ref=f"search-{position}",
                    content_sha256=content_hash(text),
                ),
                EvidenceSearchStage.LEXICAL,
                rank,
                CanonicalScore(f"0.{10 - position}"),
                SensitiveText(text),
            )
        )

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort(
            {EvidenceSearchStage.LEXICAL: search_success(request, EvidenceSearchStage.LEXICAL, tuple(hits))}
        ),
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
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

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
    response = search_success(request, EvidenceSearchStage.LEXICAL, (malformed,))

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


def test_diagnostic_enum_returned_by_search_adapter_is_malformed_output() -> None:
    class MalformedSearchPort:
        def search(
            self,
            request: EvidenceRetrievalKernelRequest,
            stage: EvidenceSearchStage,
        ) -> object:
            return KernelDiagnosticCode.NO_HITS

    outcome = retrieve_knowledge_evidence(
        lexical_request(),
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=MalformedSearchPort(),  # type: ignore[arg-type]
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


def test_diagnostic_enum_returned_by_rerank_adapter_is_malformed_output() -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

    class MalformedRerankPort:
        def rerank(self, rerank_request: EvidenceRerankRequest) -> object:
            return KernelDiagnosticCode.CANDIDATES_RERANKED

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=MalformedRerankPort(),  # type: ignore[arg-type]
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.RERANK_RESULT_INVALID


def test_unhashable_rerank_key_fails_closed_without_exception() -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

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


def test_unknown_rerank_key_is_not_exposed_in_sanitized_trace() -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

    class UnknownKeyRerank(SuccessfulRerankPort):
        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            selection = EvidenceRerankSelection("PATIENT-SECRET-IDENTIFIER", 1, CanonicalScore("0.95"))
            return replace(super().rerank(rerank_request), selections=(selection,))

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=UnknownKeyRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.RERANK_RESULT_INVALID
    assert outcome.trace is not None
    serialized = json.dumps(to_sanitized_trace_dict(outcome.trace), ensure_ascii=False)
    assert "PATIENT-SECRET-IDENTIFIER" not in serialized


def test_no_hits_succeeds_without_reranking() -> None:
    request = lexical_request()
    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: search_success(request, EvidenceSearchStage.LEXICAL, ())}),
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.SUCCEEDED
    assert outcome.diagnostic_code is KernelDiagnosticCode.NO_HITS
    assert outcome.untrusted_selections == ()


@pytest.mark.parametrize(
    "invalid_request",
    [
        replace(lexical_request(), normalized_query=SensitiveText("")),
        replace(lexical_request(), normalized_query=SensitiveText("합성 질의")),
        replace(lexical_request(), query_fingerprint=replace(fingerprint(), digest="bad")),
        replace(lexical_request(), query_fingerprint=replace(fingerprint(), algorithm="HMAC-SHA-256-가")),
        replace(lexical_request(), filter_snapshot_ref=replace(artifact("filter-snapshot"), content_sha256="BAD")),
        replace(lexical_request(), rerank_input_projection_version="unknown"),
        replace(lexical_request(), lexical_limit=True),
        replace(lexical_request(), dense_limit=1, dense_config_ref=None),
        replace(lexical_request(), selection_limit=6),
    ],
)
def test_invalid_request_matrix_fails_before_dependencies(
    invalid_request: EvidenceRetrievalKernelRequest,
) -> None:
    outcome = retrieve_knowledge_evidence(
        invalid_request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=NeverSearch(),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.REQUEST_INVALID
    assert outcome.trace is None


@pytest.mark.parametrize("score", ["-0", "1e-3", "1.0", "01", "+1", "NaN", "Infinity"])
def test_noncanonical_search_scores_fail_closed(score: str) -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, score),))
    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


@pytest.mark.parametrize("mutation", ["rank", "stage", "content_hash"])
def test_invalid_search_hit_matrix_fails_closed(mutation: str) -> None:
    request = lexical_request()
    value = hit(EvidenceSearchStage.LEXICAL, "0.9")
    if mutation == "rank":
        value = replace(value, rank=0)
    elif mutation == "stage":
        value = replace(value, stage=EvidenceSearchStage.DENSE)
    else:
        value = replace(value, provenance=replace(value.provenance, content_sha256="c" * 64))
    response = search_success(request, EvidenceSearchStage.LEXICAL, (value,))

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


def test_cross_stage_provenance_mismatch_fails_closed() -> None:
    request = replace(lexical_request(), dense_limit=5, dense_config_ref=artifact("dense-config"))
    dense = replace(
        hit(EvidenceSearchStage.DENSE, "0.8"),
        provenance=replace(provenance(), locator="$.different"),
    )
    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort(
            {
                EvidenceSearchStage.LEXICAL: search_success(
                    request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),)
                ),
                EvidenceSearchStage.DENSE: search_success(request, EvidenceSearchStage.DENSE, (dense,)),
            }
        ),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("query_fingerprint", QueryFingerprint("HMAC-SHA-256", "query-hmac@1", "c" * 64)),
        ("filter_snapshot_ref", artifact("other-filter")),
        ("evidence_index_ref", artifact("other-index")),
        ("retrieval_config_ref", artifact("other-retrieval")),
        ("rerank_config_ref", artifact("other-rerank")),
        ("projection_version", "other-version"),
        ("input_set_hash", "f" * 64),
        ("adapter_artifact_ref", replace(artifact("rerank-adapter"), content_sha256="invalid")),
    ],
)
def test_rerank_receipt_mutation_matrix(field: str, value: object) -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

    class MutatedRerank(SuccessfulRerankPort):
        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            return replace(super().rerank(rerank_request), **{field: value})  # type: ignore[arg-type]

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=MutatedRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.RERANK_RECEIPT_MISMATCH


def test_trace_is_deterministic_private_and_outcome_is_not_default_serializable() -> None:
    def run() -> EvidenceRetrievalKernelOutcome:
        request = lexical_request()
        return retrieve_knowledge_evidence(
            request,
            query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
            search_port=SearchPort(
                {
                    EvidenceSearchStage.LEXICAL: search_success(
                        request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),)
                    )
                }
            ),
            rerank_port=SuccessfulRerankPort(),
        )

    first = run()
    second = run()
    assert first.trace is not None and second.trace is not None
    assert to_sanitized_trace_dict(first.trace) == to_sanitized_trace_dict(second.trace)
    rendered = json.dumps(to_sanitized_trace_dict(first.trace), ensure_ascii=False)
    assert "합성 복약 정보" not in rendered
    assert "합성 복약 근거" not in rendered
    with pytest.raises(TypeError):
        json.dumps(dataclasses.asdict(first))


def test_boolean_search_rank_fails_closed() -> None:
    request = lexical_request()
    malformed = replace(hit(EvidenceSearchStage.LEXICAL, "0.9"), rank=True)
    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort(
            {EvidenceSearchStage.LEXICAL: search_success(request, EvidenceSearchStage.LEXICAL, (malformed,))}
        ),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


def test_boolean_rerank_rank_fails_closed() -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

    class BooleanRankRerank(SuccessfulRerankPort):
        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            selection = EvidenceRerankSelection("knowledge:chunk-1", True, CanonicalScore("0.95"))
            return replace(super().rerank(rerank_request), selections=(selection,))

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=BooleanRankRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.RERANK_RESULT_INVALID


def test_query_verifier_cannot_mutate_query_used_by_search() -> None:
    request = lexical_request()

    class MutatingQueryVerifier:
        def verify(
            self,
            query: SensitiveText,
            query_fingerprint: QueryFingerprint,
        ) -> QueryBindingVerificationSuccess:
            object.__setattr__(query, "_SensitiveText__value", "변조된 질의")
            return QueryBindingVerificationSuccess(query_fingerprint, artifact("query-verifier"))

    search = NeverSearch()
    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=MutatingQueryVerifier(),
        search_port=search,
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.QUERY_BINDING_RECEIPT_MISMATCH
    assert outcome.untrusted_selections == ()
    assert search.calls == 0


def test_search_port_cannot_mutate_query_and_return_success() -> None:
    request = lexical_request()

    class MutatingSearchPort:
        def search(
            self,
            search_request: EvidenceRetrievalKernelRequest,
            stage: EvidenceSearchStage,
        ) -> EvidenceSearchSuccess:
            object.__setattr__(search_request.normalized_query, "_SensitiveText__value", "변조된 질의")
            return search_success(search_request, stage, ())

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=MutatingSearchPort(),
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RECEIPT_MISMATCH
    assert outcome.untrusted_selections == ()


def test_rerank_port_cannot_mutate_canonical_candidate_content() -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

    class MutatingRerankPort(SuccessfulRerankPort):
        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            object.__setattr__(
                rerank_request.candidates[0].content_text,
                "_SensitiveText__value",
                "변조된 근거 내용",
            )
            return super().rerank(rerank_request)

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=MutatingRerankPort(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.RERANK_RESULT_INVALID
    assert outcome.untrusted_selections == ()


def test_successful_rerank_response_is_snapshotted_before_return() -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

    class RetainingRerankPort(SuccessfulRerankPort):
        returned_response: EvidenceRerankSuccess | None = None

        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            self.returned_response = super().rerank(rerank_request)
            return self.returned_response

    reranker = RetainingRerankPort()
    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=reranker,
    )
    assert reranker.returned_response is not None

    object.__setattr__(reranker.returned_response.selections[0].rerank_score, "value", "0.1")
    object.__setattr__(reranker.returned_response.adapter_artifact_ref, "artifact_code", "mutated-adapter")

    assert outcome.untrusted_selections[0].rerank_score == CanonicalScore("0.95")
    assert outcome.trace is not None
    assert outcome.trace.rerank_adapter_artifact_ref == artifact("rerank-adapter")


def test_query_verifier_deleting_query_state_fails_closed() -> None:
    class DestructiveQueryVerifier:
        def verify(
            self,
            query: SensitiveText,
            query_fingerprint: QueryFingerprint,
        ) -> QueryBindingVerificationSuccess:
            object.__delattr__(query, "_SensitiveText__value")
            return QueryBindingVerificationSuccess(query_fingerprint, artifact("query-verifier"))

    outcome = retrieve_knowledge_evidence(
        lexical_request(),
        query_verifier=DestructiveQueryVerifier(),
        search_port=NeverSearch(),
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.QUERY_BINDING_RECEIPT_MISMATCH


@pytest.mark.parametrize("mutation", ["fingerprint-type", "fingerprint-field-deleted"])
def test_query_verifier_fingerprint_mutation_fails_closed(mutation: str) -> None:
    class MutatingQueryVerifier:
        def verify(
            self,
            query: SensitiveText,
            query_fingerprint: QueryFingerprint,
        ) -> QueryBindingVerificationSuccess:
            if mutation == "fingerprint-type":
                object.__setattr__(query_fingerprint, "digest", object())
            else:
                object.__delattr__(query_fingerprint, "key_version")
            return QueryBindingVerificationSuccess(query_fingerprint, artifact("query-verifier"))

    search = NeverSearch()
    outcome = retrieve_knowledge_evidence(
        lexical_request(),
        query_verifier=MutatingQueryVerifier(),
        search_port=search,
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.QUERY_BINDING_RECEIPT_MISMATCH
    assert search.calls == 0


def test_search_port_deleting_request_state_fails_closed() -> None:
    class DestructiveSearchPort:
        def search(
            self,
            request: EvidenceRetrievalKernelRequest,
            stage: EvidenceSearchStage,
        ) -> EvidenceSearchSuccess:
            response = search_success(request, stage, ())
            object.__delattr__(request.normalized_query, "_SensitiveText__value")
            return response

    outcome = retrieve_knowledge_evidence(
        lexical_request(),
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=DestructiveSearchPort(),
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RECEIPT_MISMATCH


def test_rerank_port_deleting_candidate_state_fails_closed() -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

    class DestructiveRerankPort(SuccessfulRerankPort):
        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            object.__delattr__(rerank_request.candidates[0].content_text, "_SensitiveText__value")
            return super().rerank(rerank_request)

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=DestructiveRerankPort(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.RERANK_RESULT_INVALID


@pytest.mark.parametrize(
    "mutation",
    ["query-type", "fingerprint", "artifact", "limit-type"],
)
def test_search_request_nested_mutation_matrix_fails_closed(mutation: str) -> None:
    class MutatingSearchPort:
        def search(
            self,
            request: EvidenceRetrievalKernelRequest,
            stage: EvidenceSearchStage,
        ) -> EvidenceSearchSuccess:
            response = search_success(request, stage, ())
            if mutation == "query-type":
                object.__setattr__(request, "normalized_query", object())
            elif mutation == "fingerprint":
                object.__setattr__(request.query_fingerprint, "digest", object())
            elif mutation == "artifact":
                object.__delattr__(request.filter_snapshot_ref, "version")
            else:
                object.__setattr__(request, "lexical_limit", "5")
            return response

    outcome = retrieve_knowledge_evidence(
        lexical_request(),
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=MutatingSearchPort(),
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RECEIPT_MISMATCH


@pytest.mark.parametrize(
    "mutation",
    ["projection", "input-hash", "candidate-provenance", "stage-signal", "candidates-type"],
)
def test_rerank_request_nested_mutation_matrix_fails_closed(mutation: str) -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

    class MutatingRerankPort(SuccessfulRerankPort):
        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            success = super().rerank(rerank_request)
            if mutation == "projection":
                object.__setattr__(rerank_request, "projection_version", object())
            elif mutation == "input-hash":
                object.__setattr__(rerank_request, "input_set_hash", object())
            elif mutation == "candidate-provenance":
                object.__setattr__(rerank_request.candidates[0].provenance, "locator", object())
            elif mutation == "stage-signal":
                object.__setattr__(rerank_request.candidates[0].stage_signals[0], "score", object())
            else:
                object.__setattr__(rerank_request, "candidates", [rerank_request.candidates[0]])
            return success

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=MutatingRerankPort(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.RERANK_RESULT_INVALID
    assert outcome.untrusted_selections == ()


def test_query_verifier_malformed_success_receipt_fails_closed() -> None:
    malformed_fingerprint = fingerprint()
    object.__delattr__(malformed_fingerprint, "key_version")

    outcome = retrieve_knowledge_evidence(
        lexical_request(),
        query_verifier=QueryVerifier(
            QueryBindingVerificationSuccess(malformed_fingerprint, artifact("query-verifier"))
        ),
        search_port=NeverSearch(),
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.QUERY_BINDING_RECEIPT_MISMATCH


def test_search_malformed_success_receipt_fails_closed() -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, ())
    object.__delattr__(response.adapter_artifact_ref, "version")

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RECEIPT_MISMATCH


def test_rerank_malformed_success_selection_fails_closed() -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

    class MalformedSelectionRerank(SuccessfulRerankPort):
        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            success = super().rerank(rerank_request)
            object.__delattr__(success.selections[0].rerank_score, "value")
            return success

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=MalformedSelectionRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.RERANK_RESULT_INVALID


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("receipt-fingerprint", KernelDiagnosticCode.SEARCH_RECEIPT_MISMATCH),
        ("hit-content", KernelDiagnosticCode.SEARCH_RESULT_INVALID),
        ("hit-provenance", KernelDiagnosticCode.SEARCH_RESULT_INVALID),
    ],
)
def test_search_malformed_nested_response_matrix_fails_closed(
    mutation: str,
    expected: KernelDiagnosticCode,
) -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))
    if mutation == "receipt-fingerprint":
        response = replace(response, query_fingerprint=fingerprint())
        object.__delattr__(response.query_fingerprint, "digest")
    elif mutation == "hit-content":
        object.__delattr__(response.hits[0].content_text, "_SensitiveText__value")
    else:
        object.__delattr__(response.hits[0].provenance, "locator")

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is expected


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("receipt-artifact", KernelDiagnosticCode.RERANK_RECEIPT_MISMATCH),
        ("selection-key", KernelDiagnosticCode.RERANK_RESULT_INVALID),
        ("selection-container", KernelDiagnosticCode.RERANK_RESULT_INVALID),
    ],
)
def test_rerank_malformed_nested_response_matrix_fails_closed(
    mutation: str,
    expected: KernelDiagnosticCode,
) -> None:
    request = lexical_request()
    response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))

    class MalformedResponseRerank(SuccessfulRerankPort):
        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            success = super().rerank(rerank_request)
            if mutation == "receipt-artifact":
                detached_config = artifact("rerank-config")
                object.__delattr__(detached_config, "version")
                success = replace(success, rerank_config_ref=detached_config)
            elif mutation == "selection-key":
                object.__delattr__(success.selections[0], "evidence_key")
            else:
                object.__setattr__(success, "selections", object())
            return success

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=MalformedResponseRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is expected
    assert outcome.untrusted_selections == ()


def test_malformed_request_with_deleted_field_is_validation_error() -> None:
    request = lexical_request()
    object.__delattr__(request.query_fingerprint, "algorithm")

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=NeverSearch(),
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.VALIDATION_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.REQUEST_INVALID
    assert outcome.trace is None


def test_trace_distinguishes_effective_execution_limits() -> None:
    def run(request: EvidenceRetrievalKernelRequest) -> dict[str, object]:
        outcome = retrieve_knowledge_evidence(
            request,
            query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
            search_port=SearchPort(
                {EvidenceSearchStage.LEXICAL: search_success(request, EvidenceSearchStage.LEXICAL, ())}
            ),
            rerank_port=NeverRerank(),
        )
        assert outcome.trace is not None
        return to_sanitized_trace_dict(outcome.trace)

    first = run(lexical_request())
    second = run(replace(lexical_request(), lexical_limit=6))

    assert first["execution_limits"] == {"lexical": 5, "dense": 0, "selection": 3}
    assert second["execution_limits"] == {"lexical": 6, "dense": 0, "selection": 3}
    assert first != second


def test_query_verifier_missing_adapter_reference_fails_closed() -> None:
    invalid_adapter = replace(artifact("query-verifier"), content_sha256="invalid")
    outcome = retrieve_knowledge_evidence(
        lexical_request(),
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), invalid_adapter)),
        search_port=NeverSearch(),
        rerank_port=NeverRerank(),
    )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert outcome.diagnostic_code is KernelDiagnosticCode.QUERY_BINDING_RECEIPT_MISMATCH


@pytest.mark.parametrize(
    "foreign_hit",
    [
        object(),
        {"evidence_kind": "RULE_EVIDENCE", "evidence_key": "rule:synthetic-1"},
    ],
    ids=["candidate-index-hit", "rule-evidence-hit"],
)
def test_foreign_evidence_kinds_are_rejected(foreign_hit: object) -> None:
    request = lexical_request()
    response = search_success(
        request,
        EvidenceSearchStage.LEXICAL,
        (foreign_hit,),  # type: ignore[arg-type]
    )

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


def test_same_chunk_bound_to_multiple_evidence_keys_fails_closed() -> None:
    request = lexical_request()
    first = hit(EvidenceSearchStage.LEXICAL, "0.9")
    second = replace(
        first,
        provenance=replace(first.provenance, evidence_key="knowledge:chunk-2"),
        rank=2,
        stage_score=CanonicalScore("0.8"),
    )
    response = search_success(request, EvidenceSearchStage.LEXICAL, (first, second))

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


def test_same_evidence_key_bound_to_multiple_chunks_fails_closed() -> None:
    request = replace(lexical_request(), dense_limit=5, dense_config_ref=artifact("dense-config"))
    dense = replace(
        hit(EvidenceSearchStage.DENSE, "0.8"),
        provenance=replace(provenance(), knowledge_chunk_ref="chunk-2"),
    )

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort(
            {
                EvidenceSearchStage.LEXICAL: search_success(
                    request,
                    EvidenceSearchStage.LEXICAL,
                    (hit(EvidenceSearchStage.LEXICAL, "0.9"),),
                ),
                EvidenceSearchStage.DENSE: search_success(request, EvidenceSearchStage.DENSE, (dense,)),
            }
        ),
        rerank_port=NeverRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.SEARCH_RESULT_INVALID


@pytest.mark.parametrize("selection_shape", ["duplicate", "rank-gap", "limit-exceeded"])
def test_invalid_rerank_selection_shape_fails_closed(selection_shape: str) -> None:
    request = lexical_request()
    hits: list[KnowledgeEvidenceSearchHit] = []
    for rank in range(1, 5):
        text = f"합성 복약 근거 {rank}"
        item_provenance = replace(
            provenance(),
            evidence_key=f"knowledge:chunk-{rank}",
            knowledge_chunk_ref=f"chunk-{rank}",
            content_sha256=content_hash(text),
        )
        hits.append(
            KnowledgeEvidenceSearchHit(
                item_provenance,
                EvidenceSearchStage.LEXICAL,
                rank,
                CanonicalScore(f"0.{10 - rank}"),
                SensitiveText(text),
            )
        )
    response = search_success(request, EvidenceSearchStage.LEXICAL, tuple(hits))

    class InvalidSelectionRerank(SuccessfulRerankPort):
        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            selections: tuple[EvidenceRerankSelection, ...]
            if selection_shape == "duplicate":
                selections = (
                    EvidenceRerankSelection("knowledge:chunk-1", 1, CanonicalScore("0.9")),
                    EvidenceRerankSelection("knowledge:chunk-1", 2, CanonicalScore("0.8")),
                )
            elif selection_shape == "rank-gap":
                selections = (EvidenceRerankSelection("knowledge:chunk-1", 2, CanonicalScore("0.9")),)
            else:
                selections = tuple(
                    EvidenceRerankSelection(f"knowledge:chunk-{rank}", rank, CanonicalScore(f"0.{10 - rank}"))
                    for rank in range(1, 5)
                )
            return replace(super().rerank(rerank_request), selections=selections)

    outcome = retrieve_knowledge_evidence(
        request,
        query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
        search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
        rerank_port=InvalidSelectionRerank(),
    )

    assert outcome.diagnostic_code is KernelDiagnosticCode.RERANK_RESULT_INVALID


@pytest.mark.parametrize("stage", ["query-verifier", "reranker"])
def test_port_exception_messages_are_not_exposed(stage: str) -> None:
    sentinel = "PATIENT-SECRET-EXCEPTION"
    request = lexical_request()

    class RaisingQueryVerifier:
        def verify(
            self,
            query: SensitiveText,
            query_fingerprint: QueryFingerprint,
        ) -> QueryBindingVerificationSuccess:
            raise RuntimeError(sentinel)

    class RaisingRerankPort:
        def rerank(self, rerank_request: EvidenceRerankRequest) -> EvidenceRerankSuccess:
            raise RuntimeError(sentinel)

    if stage == "query-verifier":
        outcome = retrieve_knowledge_evidence(
            request,
            query_verifier=RaisingQueryVerifier(),
            search_port=NeverSearch(),
            rerank_port=NeverRerank(),
        )
    else:
        response = search_success(request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),))
        outcome = retrieve_knowledge_evidence(
            request,
            query_verifier=QueryVerifier(QueryBindingVerificationSuccess(fingerprint(), artifact("query-verifier"))),
            search_port=SearchPort({EvidenceSearchStage.LEXICAL: response}),
            rerank_port=RaisingRerankPort(),
        )

    assert outcome.execution_status is KernelExecutionStatus.DEPENDENCY_ERROR
    assert sentinel not in repr(outcome)
    assert outcome.trace is not None
    assert sentinel not in json.dumps(to_sanitized_trace_dict(outcome.trace), ensure_ascii=False)
