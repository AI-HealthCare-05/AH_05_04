import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import (
    APIConnectionError,
    APIResponseValidationError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)

from ai_worker.adapters.openai_guideline_generator import OpenAIGuidelineGeneratorAdapter
from ai_worker.tasks.rag.evidence_gate import (
    EvidenceGateExecutionStatus,
    EvidenceGateOutcome,
    EvidenceGateReason,
    EvidenceGateTrace,
    EvidenceStatus,
    GatePassedKnowledgeEvidenceSelection,
)
from ai_worker.tasks.rag.evidence_retrieval import (
    CanonicalScore,
    EvidenceSearchStage,
    ImmutableArtifactRef,
    KnowledgeEvidenceCandidate,
    KnowledgeEvidenceProvenance,
    SensitiveText,
    StageSignal,
    UntrustedKnowledgeEvidenceSelection,
)
from ai_worker.tasks.rag.guideline_card import (
    GuidelineActionClass,
    GuidelineCardDraft,
    GuidelineGenerationFailure,
    GuidelineScope,
    MedicationIdentityRef,
    VersionedGuidelinePolicy,
)
from ai_worker.tasks.rag.guideline_generator import (
    GuidelineGenerationRequest,
    GuidelineGeneratorPort,
)
from ai_worker.tasks.rag.guideline_generator_prompt import (
    GUIDELINE_GENERATOR_PROMPT_VERSION,
    GuidelineClaimSelection,
    GuidelineStructuredSelection,
    build_candidate_provenance,
)
from provider_contracts.observability import (
    DeploymentEnvironment,
    ProviderCallContext,
)
from provider_runtime.observability import ProviderCallLogger

EVALUATED_AT = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
FOOD_AVOIDANCE_TEXT = (
    "이 약을 복용하는 동안 과도한 음주는 피하고 임의로 복용을 중단하지 마세요. 궁금한 점은 약사와 상담하세요."
)


def artifact(code: str, digest: str = "a" * 64) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(code, f"{code}@synthetic-1", digest)


def make_medication(
    *,
    item_id: str = "11111111-1111-4111-8111-111111111111",
    canonical_code: str = "SYNTHETIC-ITEM-001",
) -> MedicationIdentityRef:
    return MedicationIdentityRef(
        prescription_version_medication_id=item_id,
        code_system="MFDS_ITEM_SEQ",
        canonical_code=canonical_code,
    )


def make_gate_passed_selection(
    *,
    evidence_key: str = "knowledge:guideline-1",
    content_text: str = FOOD_AVOIDANCE_TEXT,
) -> GatePassedKnowledgeEvidenceSelection:
    provenance = KnowledgeEvidenceProvenance(
        evidence_key=evidence_key,
        knowledge_chunk_ref=f"chunk-{evidence_key}",
        evidence_index_ref=artifact("knowledge-index"),
        source_snapshot_ref=artifact("source-snapshot"),
        source_version="api:ver-1",
        locator="$.items[0]",
        content_sha256=hashlib.sha256(content_text.encode()).hexdigest(),
        canonicalization_spec_version="knowledge-text@1",
    )
    selection = UntrustedKnowledgeEvidenceSelection(
        candidate=KnowledgeEvidenceCandidate(
            provenance=provenance,
            content_text=SensitiveText(content_text),
            stage_signals=(StageSignal(EvidenceSearchStage.LEXICAL, 1, CanonicalScore("0.9")),),
        ),
        rerank_rank=1,
        rerank_score=CanonicalScore("0.9"),
    )
    return GatePassedKnowledgeEvidenceSelection(
        selection=selection,
        assessment_artifact_ref=artifact("assessment"),
        eligibility_receipt_ref=artifact("eligibility-receipt"),
        retrieval_receipt_ref=artifact("retrieval-receipt"),
        verifier_artifact_ref=artifact("eligibility-verifier"),
    )


def make_policy(*, maximum_claims: int = 4) -> VersionedGuidelinePolicy:
    return VersionedGuidelinePolicy.create(
        "guideline-policy",
        "guideline-policy@synthetic-1",
        maximum_claims=maximum_claims,
        uncertainty_text_sha256=hashlib.sha256("승인된 근거 범위 밖의 내용은 확인할 수 없습니다.".encode()).hexdigest(),
        consultation_text_sha256=hashlib.sha256(
            "불편하거나 궁금한 점은 의사 또는 약사와 상담하세요.".encode()
        ).hexdigest(),
    )


def make_valid_request() -> GuidelineGenerationRequest:
    med = make_medication()
    sel = make_gate_passed_selection()
    gate_outcome = EvidenceGateOutcome(
        execution_status=EvidenceGateExecutionStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.SUFFICIENT,
        reason=EvidenceGateReason.EVIDENCE_SUFFICIENT,
        gate_passed_selections=(sel,),
        trace=EvidenceGateTrace(
            policy_ref=artifact("evidence-gate-policy"),
            retrieval_receipt_ref=sel.retrieval_receipt_ref,
            evaluated_at=EVALUATED_AT,
            assessment_artifact_refs=(sel.assessment_artifact_ref,),
            selected_evidence_keys=(sel.selection.candidate.provenance.evidence_key,),
        ),
    )
    return GuidelineGenerationRequest(
        medication_identities=(med,),
        evidence_gate_outcome=gate_outcome,
        policy=make_policy(),
    )


def make_context() -> ProviderCallContext:
    return ProviderCallContext(
        trace_id="1" * 32,
        validation_run_id=None,
        environment=DeploymentEnvironment.LOCAL,
        validation_enabled=False,
    )


class MockProviderResponse:
    def __init__(self, structured_selection: GuidelineStructuredSelection, model: str = "gpt-4o-2024-08-06"):
        self.status = "completed"
        self.model = model
        self.id = "resp-123"
        self.output_parsed = structured_selection
        self.output = [
            MagicMock(
                content=[
                    MagicMock(
                        type="output_text",
                        parsed=structured_selection,
                    )
                ]
            )
        ]


def build_mock_client(
    response: Any = None,
    side_effect: Any = None,
    max_retries: int = 0,
) -> MagicMock:
    client = MagicMock()
    client.max_retries = max_retries

    async_parse = AsyncMock(return_value=response, side_effect=side_effect)
    client.responses.parse = async_parse

    # Support with_options returning itself with options updated
    def with_options(**kwargs):
        client._options = kwargs
        return client

    client.with_options = MagicMock(side_effect=with_options)
    return client


class CapturingLogger:
    def __init__(self) -> None:
        self.records: list[str] = []

    def info(self, msg: str) -> None:
        self.records.append(msg)

    def warning(self, msg: str) -> None:
        self.records.append(msg)

    def error(self, msg: str) -> None:
        self.records.append(msg)


def test_protocol_structural_typing() -> None:
    client = build_mock_client()
    adapter = OpenAIGuidelineGeneratorAdapter(
        client=client,
        model="gpt-4o-synthetic",
        timeout_seconds=5.0,
        context=make_context(),
    )
    typed_port: GuidelineGeneratorPort = adapter
    assert hasattr(typed_port, "generate")
    assert callable(typed_port.generate)


def test_rejects_client_with_max_retries_greater_than_zero() -> None:
    client = build_mock_client(max_retries=2)
    with pytest.raises(ValueError, match="OpenAI client must have max_retries=0"):
        OpenAIGuidelineGeneratorAdapter(
            client=client,
            model="gpt-4o-synthetic",
            timeout_seconds=5.0,
            context=make_context(),
        )


def test_rejects_client_without_max_retries_attribute() -> None:
    class FakeClientWithoutMaxRetries:
        pass

    with pytest.raises(ValueError, match="OpenAI client must have max_retries=0"):
        OpenAIGuidelineGeneratorAdapter(
            client=FakeClientWithoutMaxRetries(),  # type: ignore[arg-type]
            model="gpt-4o-synthetic",
            timeout_seconds=5.0,
            context=make_context(),
        )


def test_rejects_client_without_with_options() -> None:
    class FakeClientWithoutWithOptions:
        max_retries = 0

    with pytest.raises(ValueError, match="OpenAI client must support with_options"):
        OpenAIGuidelineGeneratorAdapter(
            client=FakeClientWithoutWithOptions(),  # type: ignore[arg-type]
            model="gpt-4o-synthetic",
            timeout_seconds=5.0,
            context=make_context(),
        )


def test_rejects_client_with_non_callable_with_options() -> None:
    class FakeClientWithNonCallableWithOptions:
        max_retries = 0
        with_options = "not-callable"

    with pytest.raises(ValueError, match="OpenAI client must support with_options"):
        OpenAIGuidelineGeneratorAdapter(
            client=FakeClientWithNonCallableWithOptions(),  # type: ignore[arg-type]
            model="gpt-4o-synthetic",
            timeout_seconds=5.0,
            context=make_context(),
        )


def test_adapter_computes_exact_runtime_provenance() -> None:
    client = build_mock_client()
    adapter = OpenAIGuidelineGeneratorAdapter(
        client=client,
        model="gpt-4o-synthetic",
        timeout_seconds=5.0,
        context=make_context(),
    )
    expected_prov = build_candidate_provenance(model="gpt-4o-synthetic")
    assert adapter.provenance == expected_prov
    assert adapter.provenance.prompt_ref.artifact_code == "guideline-prompt"
    assert adapter.provenance.prompt_ref.version == GUIDELINE_GENERATOR_PROMPT_VERSION
    assert adapter.provenance.model_ref.artifact_code == "guideline-model"
    assert adapter.provenance.model_ref.version == "openai:gpt-4o-synthetic"
    assert adapter.provenance.parser_ref.artifact_code == "guideline-parser"
    assert adapter.provenance.validator_ref.artifact_code == "guideline-validator"


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_status", tuple(EvidenceGateExecutionStatus))
async def test_gate_precondition_execution_status_axis(
    execution_status: EvidenceGateExecutionStatus,
) -> None:
    structured_resp = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            )
        ]
    )
    client = build_mock_client(response=MockProviderResponse(structured_resp))
    adapter = OpenAIGuidelineGeneratorAdapter(
        client=client,
        model="gpt-4o-synthetic",
        timeout_seconds=5.0,
        context=make_context(),
    )
    base_req = make_valid_request()
    gate_outcome = EvidenceGateOutcome(
        execution_status=execution_status,
        evidence_status=EvidenceStatus.SUFFICIENT,
        reason=EvidenceGateReason.EVIDENCE_SUFFICIENT,
        gate_passed_selections=base_req.evidence_gate_outcome.gate_passed_selections,
        trace=base_req.evidence_gate_outcome.trace,
    )
    req = GuidelineGenerationRequest(
        medication_identities=base_req.medication_identities,
        evidence_gate_outcome=gate_outcome,
        policy=base_req.policy,
    )
    res = await adapter.generate(req)
    if execution_status is EvidenceGateExecutionStatus.SUCCEEDED:
        assert isinstance(res, GuidelineCardDraft)
        assert client.responses.parse.call_count == 1
    else:
        assert res is GuidelineGenerationFailure.VALIDATION_FAILED
        assert client.responses.parse.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_status", tuple(EvidenceStatus) + (None,))
async def test_gate_precondition_evidence_status_axis(
    evidence_status: EvidenceStatus | None,
) -> None:
    structured_resp = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            )
        ]
    )
    client = build_mock_client(response=MockProviderResponse(structured_resp))
    adapter = OpenAIGuidelineGeneratorAdapter(
        client=client,
        model="gpt-4o-synthetic",
        timeout_seconds=5.0,
        context=make_context(),
    )
    base_req = make_valid_request()
    gate_outcome = EvidenceGateOutcome(
        execution_status=EvidenceGateExecutionStatus.SUCCEEDED,
        evidence_status=evidence_status,  # type: ignore[arg-type]
        reason=EvidenceGateReason.EVIDENCE_SUFFICIENT,
        gate_passed_selections=base_req.evidence_gate_outcome.gate_passed_selections,
        trace=base_req.evidence_gate_outcome.trace,
    )
    req = GuidelineGenerationRequest(
        medication_identities=base_req.medication_identities,
        evidence_gate_outcome=gate_outcome,
        policy=base_req.policy,
    )
    res = await adapter.generate(req)
    if evidence_status is EvidenceStatus.SUFFICIENT:
        assert isinstance(res, GuidelineCardDraft)
        assert client.responses.parse.call_count == 1
    else:
        assert res is GuidelineGenerationFailure.VALIDATION_FAILED
        assert client.responses.parse.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", tuple(EvidenceGateReason))
async def test_gate_precondition_reason_axis(
    reason: EvidenceGateReason,
) -> None:
    structured_resp = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            )
        ]
    )
    client = build_mock_client(response=MockProviderResponse(structured_resp))
    adapter = OpenAIGuidelineGeneratorAdapter(
        client=client,
        model="gpt-4o-synthetic",
        timeout_seconds=5.0,
        context=make_context(),
    )
    base_req = make_valid_request()
    gate_outcome = EvidenceGateOutcome(
        execution_status=EvidenceGateExecutionStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.SUFFICIENT,
        reason=reason,
        gate_passed_selections=base_req.evidence_gate_outcome.gate_passed_selections,
        trace=base_req.evidence_gate_outcome.trace,
    )
    req = GuidelineGenerationRequest(
        medication_identities=base_req.medication_identities,
        evidence_gate_outcome=gate_outcome,
        policy=base_req.policy,
    )
    res = await adapter.generate(req)
    if reason is EvidenceGateReason.EVIDENCE_SUFFICIENT:
        assert isinstance(res, GuidelineCardDraft)
        assert client.responses.parse.call_count == 1
    else:
        assert res is GuidelineGenerationFailure.VALIDATION_FAILED
        assert client.responses.parse.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "axis,sentinel",
    [
        ("execution_status", "UNSUPPORTED_EXECUTION_STATUS"),
        ("evidence_status", "UNSUPPORTED_EVIDENCE_STATUS"),
        ("reason", "UNSUPPORTED_REASON"),
    ],
)
async def test_gate_precondition_unsupported_sentinel_values(
    axis: str,
    sentinel: Any,
) -> None:
    client = build_mock_client()
    adapter = OpenAIGuidelineGeneratorAdapter(
        client=client,
        model="gpt-4o-synthetic",
        timeout_seconds=5.0,
        context=make_context(),
    )
    base_req = make_valid_request()
    kwargs: dict[str, Any] = {
        "execution_status": EvidenceGateExecutionStatus.SUCCEEDED,
        "evidence_status": EvidenceStatus.SUFFICIENT,
        "reason": EvidenceGateReason.EVIDENCE_SUFFICIENT,
        "gate_passed_selections": base_req.evidence_gate_outcome.gate_passed_selections,
        "trace": base_req.evidence_gate_outcome.trace,
    }
    kwargs[axis] = sentinel
    gate_outcome = EvidenceGateOutcome(**kwargs)
    req = GuidelineGenerationRequest(
        medication_identities=base_req.medication_identities,
        evidence_gate_outcome=gate_outcome,
        policy=base_req.policy,
    )
    res = await adapter.generate(req)
    assert res is GuidelineGenerationFailure.VALIDATION_FAILED
    assert client.responses.parse.call_count == 0


@pytest.mark.asyncio
async def test_gate_precondition_empty_selections_or_medications_fail_closed() -> None:
    client = build_mock_client()
    adapter = OpenAIGuidelineGeneratorAdapter(
        client=client,
        model="gpt-4o-synthetic",
        timeout_seconds=5.0,
        context=make_context(),
    )
    base_req = make_valid_request()
    # Empty selections
    gate_outcome_empty_sels = EvidenceGateOutcome(
        execution_status=EvidenceGateExecutionStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.SUFFICIENT,
        reason=EvidenceGateReason.EVIDENCE_SUFFICIENT,
        gate_passed_selections=(),
        trace=base_req.evidence_gate_outcome.trace,
    )
    req_empty_sels = GuidelineGenerationRequest(
        medication_identities=base_req.medication_identities,
        evidence_gate_outcome=gate_outcome_empty_sels,
        policy=base_req.policy,
    )
    assert await adapter.generate(req_empty_sels) is GuidelineGenerationFailure.VALIDATION_FAILED
    assert client.responses.parse.call_count == 0

    # Empty medications
    req_empty_meds = GuidelineGenerationRequest(
        medication_identities=(),
        evidence_gate_outcome=base_req.evidence_gate_outcome,
        policy=base_req.policy,
    )
    assert await adapter.generate(req_empty_meds) is GuidelineGenerationFailure.VALIDATION_FAILED
    assert client.responses.parse.call_count == 0


@pytest.mark.asyncio
async def test_success_path_single_call_and_observability() -> None:
    structured_selection = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            )
        ]
    )
    client = build_mock_client(response=MockProviderResponse(structured_selection, model="gpt-4o-synthetic-provider"))

    capturing_logger = CapturingLogger()
    call_logger = ProviderCallLogger(capturing_logger)  # type: ignore[arg-type]

    adapter = OpenAIGuidelineGeneratorAdapter(
        client=client,
        model="gpt-4o-synthetic",
        timeout_seconds=2.5,
        context=make_context(),
        call_logger=call_logger,
    )

    req = make_valid_request()
    result = await adapter.generate(req)

    assert isinstance(result, GuidelineCardDraft)
    assert len(result.claims) == 1
    assert result.claims[0].claim_key == "claim:001"
    assert result.claims[0].action_class is GuidelineActionClass.FOOD_AVOIDANCE

    # Provider call count exactly 1
    assert client.responses.parse.call_count == 1
    call_kwargs = client.responses.parse.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-synthetic"
    assert call_kwargs["store"] is False
    assert call_kwargs["temperature"] == 0
    assert call_kwargs["timeout"] == 2.5

    # Observability log checks
    assert len(capturing_logger.records) == 2
    start_event = json.loads(capturing_logger.records[0])
    success_event = json.loads(capturing_logger.records[1])

    assert start_event["event"] == "provider.call.started"
    assert start_event["operation"] == "GUIDE_GENERATION"
    assert start_event["prompt_version"] == GUIDELINE_GENERATOR_PROMPT_VERSION

    assert success_event["event"] == "provider.call.succeeded"
    assert success_event["outcome"] == "SUCCESS"
    assert success_event["model_name"] == "gpt-4o-synthetic-provider"


@pytest.mark.asyncio
async def test_timeout_mapping_both_api_and_asyncio_timeout() -> None:
    # 1. APITimeoutError
    client_api_timeout = build_mock_client(side_effect=APITimeoutError(request=MagicMock()))
    adapter_api = OpenAIGuidelineGeneratorAdapter(
        client=client_api_timeout,
        model="gpt-4o",
        timeout_seconds=1.0,
        context=make_context(),
    )
    result_api = await adapter_api.generate(make_valid_request())
    assert result_api is GuidelineGenerationFailure.PROVIDER_TIMEOUT

    # 2. asyncio.TimeoutError
    client_async_timeout = build_mock_client(side_effect=TimeoutError())
    adapter_async = OpenAIGuidelineGeneratorAdapter(
        client=client_async_timeout,
        model="gpt-4o",
        timeout_seconds=1.0,
        context=make_context(),
    )
    result_async = await adapter_async.generate(make_valid_request())
    assert result_async is GuidelineGenerationFailure.PROVIDER_TIMEOUT


@pytest.mark.asyncio
async def test_dependency_unavailable_mappings() -> None:
    # 1. APIConnectionError
    c1 = build_mock_client(side_effect=APIConnectionError(request=MagicMock()))
    a1 = OpenAIGuidelineGeneratorAdapter(client=c1, model="gpt-4o", timeout_seconds=1.0, context=make_context())
    assert await a1.generate(make_valid_request()) is GuidelineGenerationFailure.DEPENDENCY_UNAVAILABLE

    # 2. RateLimitError (429)
    resp429 = MagicMock(status_code=429, headers={})
    c2 = build_mock_client(side_effect=RateLimitError(message="rate limit", response=resp429, body=None))
    a2 = OpenAIGuidelineGeneratorAdapter(client=c2, model="gpt-4o", timeout_seconds=1.0, context=make_context())
    assert await a2.generate(make_valid_request()) is GuidelineGenerationFailure.DEPENDENCY_UNAVAILABLE

    # 3. 500 Server Error
    resp500 = MagicMock(status_code=500, headers={})
    c3 = build_mock_client(side_effect=APIStatusError(message="server error", response=resp500, body=None))
    a3 = OpenAIGuidelineGeneratorAdapter(client=c3, model="gpt-4o", timeout_seconds=1.0, context=make_context())
    assert await a3.generate(make_valid_request()) is GuidelineGenerationFailure.DEPENDENCY_UNAVAILABLE


@pytest.mark.asyncio
async def test_validation_failed_mappings() -> None:
    # 1. 400 Bad Request
    resp400 = MagicMock(status_code=400, headers={})
    c1 = build_mock_client(side_effect=APIStatusError(message="bad request", response=resp400, body=None))
    a1 = OpenAIGuidelineGeneratorAdapter(client=c1, model="gpt-4o", timeout_seconds=1.0, context=make_context())
    assert await a1.generate(make_valid_request()) is GuidelineGenerationFailure.VALIDATION_FAILED

    # 2. APIResponseValidationError
    c2 = build_mock_client(side_effect=APIResponseValidationError(response=resp400, body=None, message="invalid"))
    a2 = OpenAIGuidelineGeneratorAdapter(client=c2, model="gpt-4o", timeout_seconds=1.0, context=make_context())
    assert await a2.generate(make_valid_request()) is GuidelineGenerationFailure.VALIDATION_FAILED

    # 3. Provider returns forged medication_slot
    selection_forged = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m999",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            )
        ]
    )
    c3 = build_mock_client(response=MockProviderResponse(selection_forged))
    a3 = OpenAIGuidelineGeneratorAdapter(client=c3, model="gpt-4o", timeout_seconds=1.0, context=make_context())
    assert await a3.generate(make_valid_request()) is GuidelineGenerationFailure.VALIDATION_FAILED


@pytest.mark.asyncio
async def test_refusal_and_safety_filter_mappings() -> None:
    # 1. Refusal in response
    refusal_response = MagicMock()
    refusal_response.status = "completed"
    refusal_response.model = "gpt-4o"
    refusal_item = MagicMock(refusal="I cannot provide medical guidelines.")
    refusal_response.output = [MagicMock(content=[refusal_item])]
    refusal_response.output_parsed = None

    c1 = build_mock_client(response=refusal_response)
    a1 = OpenAIGuidelineGeneratorAdapter(client=c1, model="gpt-4o", timeout_seconds=1.0, context=make_context())
    assert await a1.generate(make_valid_request()) is GuidelineGenerationFailure.VALIDATION_FAILED

    # 2. Content filter in incomplete_details
    safety_response = MagicMock()
    safety_response.status = "incomplete"
    safety_response.incomplete_details = MagicMock(reason="content_filter")
    safety_response.output = []
    safety_response.output_parsed = None

    c2 = build_mock_client(response=safety_response)
    a2 = OpenAIGuidelineGeneratorAdapter(client=c2, model="gpt-4o", timeout_seconds=1.0, context=make_context())
    assert await a2.generate(make_valid_request()) is GuidelineGenerationFailure.VALIDATION_FAILED


@pytest.mark.asyncio
async def test_cancellation_propagates_and_records_span_abort() -> None:
    client = build_mock_client(side_effect=asyncio.CancelledError())
    capturing_logger = CapturingLogger()
    call_logger = ProviderCallLogger(capturing_logger)  # type: ignore[arg-type]

    adapter = OpenAIGuidelineGeneratorAdapter(
        client=client,
        model="gpt-4o",
        timeout_seconds=1.0,
        context=make_context(),
        call_logger=call_logger,
    )

    with pytest.raises(asyncio.CancelledError):
        await adapter.generate(make_valid_request())

    # Verify aborted event in logs
    events = [json.loads(r) for r in capturing_logger.records]
    assert len(events) == 2
    assert events[1]["event"] == "provider.call.failed"
    assert events[1]["error_code"] == "PROVIDER_CALL_ABORTED"
    assert events[1]["failure_phase"] == "APPLICATION_DEADLINE"


@pytest.mark.asyncio
async def test_privacy_and_logging_no_leakage() -> None:
    """Verifies that API keys, raw response bodies, patient IDs, and full evidence text do not leak into logs."""
    structured_selection = GuidelineStructuredSelection(
        claims=[
            GuidelineClaimSelection(
                medication_slot="m0",
                scope=GuidelineScope.FOOD_CAUTION,
                evidence_slots=["e0"],
            )
        ]
    )
    client = build_mock_client(response=MockProviderResponse(structured_selection))
    capturing_logger = CapturingLogger()
    call_logger = ProviderCallLogger(capturing_logger)  # type: ignore[arg-type]

    adapter = OpenAIGuidelineGeneratorAdapter(
        client=client,
        model="gpt-4o",
        timeout_seconds=2.0,
        context=make_context(),
        call_logger=call_logger,
    )

    req = make_valid_request()
    await adapter.generate(req)

    all_logs = " ".join(capturing_logger.records)
    patient_id = req.medication_identities[0].prescription_version_medication_id
    evidence_text = req.evidence_gate_outcome.gate_passed_selections[0].selection.candidate.content_text.reveal()

    assert patient_id not in all_logs
    assert evidence_text not in all_logs
    assert "sk-" not in all_logs
