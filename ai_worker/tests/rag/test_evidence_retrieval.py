import dataclasses
import hashlib
import json
from dataclasses import replace

import pytest

from ai_worker.tasks.rag.evidence_retrieval import (
    CanonicalScore,
    EvidenceRerankFailure,
    EvidenceRerankRequest,
    EvidenceRerankSelection,
    EvidenceRerankSuccess,
    EvidenceRetrievalKernelRequest,
    EvidenceSearchStage,
    EvidenceSearchSuccess,
    ImmutableArtifactRef,
    KernelDiagnosticCode,
    KernelExecutionStatus,
    KnowledgeEvidenceProvenance,
    KnowledgeEvidenceSearchHit,
    QueryBindingFailureReason,
    QueryBindingVerificationFailure,
    QueryBindingVerificationSuccess,
    QueryFingerprint,
    SensitiveText,
    StageSignal,
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
    def rerank(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("rerank must not be called")


class SearchPort:
    def __init__(self, responses: dict[EvidenceSearchStage, EvidenceSearchSuccess]) -> None:
        self.responses = responses

    def search(
        self, request: EvidenceRetrievalKernelRequest, stage: EvidenceSearchStage
    ) -> EvidenceSearchSuccess:
        return self.responses[stage]


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


def test_search_results_normalize_same_evidence_into_one_candidate() -> None:
    request = replace(
        lexical_request(), dense_config_ref=artifact("dense-config"), dense_limit=5
    )
    search = SearchPort(
        {
            EvidenceSearchStage.LEXICAL: EvidenceSearchSuccess.from_request(
                request, EvidenceSearchStage.LEXICAL, (hit(EvidenceSearchStage.LEXICAL, "0.9"),)
            ),
            EvidenceSearchStage.DENSE: EvidenceSearchSuccess.from_request(
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
            EvidenceSearchStage.LEXICAL: EvidenceSearchSuccess.from_request(
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
    trace = to_sanitized_trace_dict(outcome.trace)
    assert "합성 복약 정보" not in json.dumps(trace, ensure_ascii=False)
    assert "normalized_query" not in trace
