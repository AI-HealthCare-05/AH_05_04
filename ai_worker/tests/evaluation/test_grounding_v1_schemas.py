from __future__ import annotations

from copy import deepcopy
from enum import StrEnum
from typing import Any

import pytest
from pydantic import BaseModel

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.schemas import (
    AuthorizationDecision,
    AuthorizationReason,
    CandidateValidationDecision,
    CandidateValidationExecutionStatus,
    CandidateValidationReason,
    CitationSourceType,
    ClaimCitationObservation,
    ClaimCriticality,
    ClaimKind,
    ClaimSupportStatus,
    CriticalitySource,
    GroundingSignal,
    GroundingSignalStatus,
    parse_claim_citation_observation_bytes,
    parse_grounding_signal_bytes,
)
from ai_worker.tasks.evaluation.schemas.common import TaskType

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
RUN_ID = "12345678-1234-4234-8234-123456789abc"
Payload = dict[str, Any]


def _ref(identifier: str, hash_value: str = SHA_A) -> Payload:
    return {"id": identifier, "version": "1.0.0", "hash": hash_value}


def _with_hash(payload: Payload, field: str) -> Payload:
    result = deepcopy(payload)
    result[field] = canonical_sha256(result, excluded_top_level_keys=frozenset({field}))
    return result


def _citation() -> Payload:
    return {
        "citation_key": "citation-001",
        "claim_key": "claim-001",
        "source_type": "KNOWLEDGE_CHUNK",
        "evidence_ref_id": "evidence-001",
        "source_version": "1.0.0",
        "locator": "section 1 paragraph 2",
        "content_sha256": SHA_B,
        "accepted": True,
        "validation_reason_code": None,
        "authorized": True,
        "authorization_reason_code": None,
        "authorization_selection_sha256": SHA_C,
        "gold_source_matched": True,
    }


def _observation_payload() -> Payload:
    return _with_hash(
        {
            "schema_id": "rag-eval.claim-citation-observation",
            "schema_version": "1.0.0",
            "observation_sha256": SHA_A,
            "run_id": RUN_ID,
            "case_id": "case-001",
            "task_type": "ANSWER_GROUNDING",
            "dataset_code": "dev-foundation-v1",
            "dataset_version": "1.0.0",
            "input_sha256": SHA_A,
            "answer_sha256": SHA_B,
            "answer_variant_manifest_hash": SHA_C,
            "validation_execution_status": "EVALUATED",
            "validation_decision": "VALIDATED",
            "validation_reason_codes": [],
            "validated_selection_sha256": SHA_A,
            "authorization_decision": "AUTHORIZED",
            "authorization_reason_codes": [],
            "authorization_receipt_ref": _ref("citation-authorization-receipt", SHA_B),
            "authorization_receipt_sha256": SHA_C,
            "claims": [
                {
                    "claim_key": "claim-001",
                    "claim_kind": "MEDICAL",
                    "criticality": "CRITICAL",
                    "criticality_source": "GOLD_EXACT_MATCH",
                    "criticality_review_ref": None,
                    "support_status": "SUPPORTED",
                    "support_receipt_sha256": SHA_A,
                    "citations": [_citation()],
                }
            ],
        },
        "observation_sha256",
    )


def _signal_payload(*, no_claims: bool = False) -> Payload:
    payload: Payload = {
        "schema_id": "rag-eval.grounding-signal",
        "schema_version": "1.0.0",
        "signal_sha256": SHA_A,
        "run_id": RUN_ID,
        "case_id": "case-001",
        "task_type": "SAFETY",
        "dataset_code": "dev-foundation-v1",
        "dataset_version": "1.0.0",
        "input_sha256": SHA_A,
        "answer_sha256": None if no_claims else SHA_B,
        "status": "NOT_APPLICABLE_NO_CLAIMS" if no_claims else "EVALUATED",
        "observation_ref": None if no_claims else _ref("claim-citation-observation", SHA_C),
        "observation_sha256": None if no_claims else SHA_C,
        "critical_unsupported_claim": False,
        "uncited_medical_claim": False,
        "source_binding_misuse": False,
    }
    return _with_hash(payload, "signal_sha256")


def _parse_observation(payload: Payload):
    return parse_claim_citation_observation_bytes(canonical_json_bytes(payload))


def _parse_signal(payload: Payload):
    return parse_grounding_signal_bytes(canonical_json_bytes(payload))


def _rehash(payload: Payload, field: str) -> Payload:
    return _with_hash(payload, field)


def _assert_observation_schema_invalid(payload: Payload) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        _parse_observation(_rehash(payload, "observation_sha256"))
    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


def _assert_signal_schema_invalid(payload: Payload) -> None:
    with pytest.raises(EvaluationValidationError) as caught:
        _parse_signal(_rehash(payload, "signal_sha256"))
    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


def test_parses_canonical_claim_citation_observation() -> None:
    observation = _parse_observation(_observation_payload())

    assert observation.task_type is TaskType.ANSWER_GROUNDING
    assert observation.claims[0].claim_kind is ClaimKind.MEDICAL
    assert observation.claims[0].citations[0].source_type is CitationSourceType.KNOWLEDGE_CHUNK


@pytest.mark.parametrize("no_claims", [False, True])
def test_parses_both_grounding_signal_states(no_claims: bool) -> None:
    signal = _parse_signal(_signal_payload(no_claims=no_claims))

    expected = GroundingSignalStatus.NOT_APPLICABLE_NO_CLAIMS if no_claims else GroundingSignalStatus.EVALUATED
    assert signal.status is expected
    assert signal.task_type is TaskType.SAFETY


def test_no_claims_signal_allows_approved_fallback_answer_hash() -> None:
    payload = _signal_payload(no_claims=True)
    payload["answer_sha256"] = SHA_B

    signal = _parse_signal(_rehash(payload, "signal_sha256"))

    assert signal.status is GroundingSignalStatus.NOT_APPLICABLE_NO_CLAIMS
    assert signal.answer_sha256 == SHA_B


@pytest.mark.parametrize(
    ("enum_type", "values"),
    [
        (
            CitationSourceType,
            {"PRESCRIPTION", "KNOWLEDGE_CHUNK", "INTERACTION_RULE", "LIFESTYLE_GUIDELINE", "SAFETY_POLICY"},
        ),
        (ClaimKind, {"MEDICAL", "AUXILIARY", "SAFETY_FALLBACK"}),
        (ClaimSupportStatus, {"SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED", "NOT_SUPPORTED"}),
        (ClaimCriticality, {"CRITICAL", "NON_CRITICAL"}),
        (CriticalitySource, {"GOLD_EXACT_MATCH", "APPROVED_REVIEW"}),
        (CandidateValidationExecutionStatus, {"EVALUATED", "VALIDATION_ERROR", "DEPENDENCY_ERROR"}),
        (CandidateValidationDecision, {"VALIDATED", "REJECTED"}),
        (
            CandidateValidationReason,
            {
                "REQUEST_INVALID",
                "CLAIM_IDENTITY_INVALID",
                "CITATION_IDENTITY_INVALID",
                "EVIDENCE_TYPE_MISMATCH",
                "EVIDENCE_PROVENANCE_INVALID",
                "MEDICAL_CLAIM_CITATION_REQUIRED",
                "MEDICAL_CLAIM_NOT_SUPPORTED",
                "CLAIM_NOT_SUPPORTED",
                "SUPPORT_RECEIPT_REQUIRED",
                "SUPPORT_RECEIPT_MISMATCH",
            },
        ),
        (AuthorizationDecision, {"AUTHORIZED", "REJECTED"}),
        (
            AuthorizationReason,
            {
                "VALIDATED_SELECTION_INVALID",
                "RUNTIME_BINDING_INVALID",
                "ORIGIN_REQUEST_MISMATCH",
                "AUTHORIZATION_SELECTION_INVALID",
                "AUTHORIZATION_SELECTION_REQUIRED",
                "AUTHORIZATION_REQUEST_INVALID",
                "RECEIPT_INVALID",
                "RECEIPT_BINDING_MISMATCH",
                "SELECTION_RECEIPT_MISMATCH",
                "SELECTION_NOT_AUTHORIZED",
            },
        ),
        (GroundingSignalStatus, {"EVALUATED", "NOT_APPLICABLE_NO_CLAIMS"}),
    ],
)
def test_bounded_enums_exactly_match_approved_wire_values(enum_type: type[StrEnum], values: set[str]) -> None:
    assert {member.value for member in enum_type} == values
    with pytest.raises(ValueError):
        enum_type("UNSUPPORTED_VALUE")


@pytest.mark.parametrize("task_type", ["ANSWER_QUALITY", "RETRIEVAL", "UNKNOWN"])
def test_observation_rejects_unsupported_task_type(task_type: str) -> None:
    payload = _observation_payload()
    payload["task_type"] = task_type
    payload = _with_hash(payload, "observation_sha256")

    with pytest.raises(EvaluationValidationError) as caught:
        _parse_observation(payload)

    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


def test_observation_accepts_runtime_opaque_source_version() -> None:
    payload = _observation_payload()
    payload["claims"][0]["citations"][0]["source_version"] = "2026-09-01"
    payload = _with_hash(payload, "observation_sha256")

    observation = _parse_observation(payload)

    assert observation.claims[0].citations[0].source_version == "2026-09-01"


@pytest.mark.parametrize("task_type", ["ANSWER_GROUNDING", "ANSWER_QUALITY", "RETRIEVAL", "UNKNOWN"])
def test_signal_rejects_non_safety_task_type(task_type: str) -> None:
    payload = _signal_payload()
    payload["task_type"] = task_type
    payload = _with_hash(payload, "signal_sha256")

    with pytest.raises(EvaluationValidationError) as caught:
        _parse_signal(payload)

    assert caught.value.code is EvaluationErrorCode.SCHEMA_INVALID


@pytest.mark.parametrize(
    "mutation",
    [
        "empty_claims",
        "duplicate_claim",
        "unsorted_claims",
        "duplicate_citation",
        "unsorted_citations",
        "orphan_citation",
        "duplicate_validation_reason",
        "unsorted_validation_reasons",
        "duplicate_authorization_reason",
        "unsorted_authorization_reasons",
    ],
)
def test_observation_rejects_noncanonical_collections(mutation: str) -> None:
    payload = _observation_payload()
    claims = payload["claims"]
    assert isinstance(claims, list)
    first_claim = claims[0]
    assert isinstance(first_claim, dict)
    citations = first_claim["citations"]
    assert isinstance(citations, list)

    if mutation == "empty_claims":
        payload["claims"] = []
    elif mutation == "duplicate_claim":
        claims.append(deepcopy(first_claim))
    elif mutation == "unsorted_claims":
        second = deepcopy(first_claim)
        second["claim_key"] = "claim-000"
        second["citations"] = []
        claims.append(second)
    elif mutation == "duplicate_citation":
        second = deepcopy(first_claim)
        second["claim_key"] = "claim-002"
        second_citation = deepcopy(citations[0])
        second_citation["claim_key"] = "claim-002"
        second["citations"] = [second_citation]
        claims.append(second)
    elif mutation == "unsorted_citations":
        second_citation = deepcopy(citations[0])
        second_citation["citation_key"] = "citation-000"
        citations.append(second_citation)
    elif mutation == "orphan_citation":
        citations[0]["claim_key"] = "claim-other"
    elif mutation == "duplicate_validation_reason":
        payload["validation_decision"] = "REJECTED"
        payload["validated_selection_sha256"] = None
        payload["validation_reason_codes"] = ["REQUEST_INVALID", "REQUEST_INVALID"]
    elif mutation == "unsorted_validation_reasons":
        payload["validation_decision"] = "REJECTED"
        payload["validated_selection_sha256"] = None
        payload["validation_reason_codes"] = ["SUPPORT_RECEIPT_MISMATCH", "REQUEST_INVALID"]
    elif mutation == "duplicate_authorization_reason":
        payload["authorization_decision"] = "REJECTED"
        payload["authorization_receipt_ref"] = None
        payload["authorization_receipt_sha256"] = None
        payload["authorization_reason_codes"] = ["RECEIPT_INVALID", "RECEIPT_INVALID"]
    else:
        payload["authorization_decision"] = "REJECTED"
        payload["authorization_receipt_ref"] = None
        payload["authorization_receipt_sha256"] = None
        payload["authorization_reason_codes"] = ["SELECTION_NOT_AUTHORIZED", "RECEIPT_INVALID"]

    _assert_observation_schema_invalid(payload)


def test_observation_rejects_citation_keys_not_globally_sorted() -> None:
    payload = _observation_payload()
    first_claim = payload["claims"][0]
    first_claim["citations"][0]["citation_key"] = "citation-z"
    second_claim = deepcopy(first_claim)
    second_claim["claim_key"] = "claim-002"
    second_claim["citations"][0]["claim_key"] = "claim-002"
    second_claim["citations"][0]["citation_key"] = "citation-a"
    payload["claims"].append(second_claim)

    _assert_observation_schema_invalid(payload)


@pytest.mark.parametrize(
    ("criticality_source", "review_ref"),
    [
        ("GOLD_EXACT_MATCH", _ref("unexpected-review")),
        ("APPROVED_REVIEW", None),
    ],
)
def test_observation_rejects_criticality_review_binding_mismatch(
    criticality_source: str,
    review_ref: Payload | None,
) -> None:
    payload = _observation_payload()
    payload["claims"][0]["criticality_source"] = criticality_source
    payload["claims"][0]["criticality_review_ref"] = review_ref

    _assert_observation_schema_invalid(payload)


@pytest.mark.parametrize(
    ("accepted", "reason"),
    [(True, "CITATION_IDENTITY_INVALID"), (False, None)],
)
def test_observation_rejects_edge_validation_tuple_mismatch(accepted: bool, reason: str | None) -> None:
    payload = _observation_payload()
    citation = payload["claims"][0]["citations"][0]
    citation["accepted"] = accepted
    citation["validation_reason_code"] = reason

    _assert_observation_schema_invalid(payload)


@pytest.mark.parametrize(
    ("authorized", "reason", "selection_hash"),
    [
        (True, "SELECTION_NOT_AUTHORIZED", SHA_C),
        (True, None, None),
        (False, None, None),
        (False, "SELECTION_NOT_AUTHORIZED", SHA_C),
    ],
)
def test_observation_rejects_edge_authorization_tuple_mismatch(
    authorized: bool,
    reason: str | None,
    selection_hash: str | None,
) -> None:
    payload = _observation_payload()
    citation = payload["claims"][0]["citations"][0]
    citation["authorized"] = authorized
    citation["authorization_reason_code"] = reason
    citation["authorization_selection_sha256"] = selection_hash

    _assert_observation_schema_invalid(payload)


def test_rejected_validation_with_emitted_citation_forbids_authorization() -> None:
    payload = _observation_payload()
    payload["validation_decision"] = "REJECTED"
    payload["validation_reason_codes"] = ["CLAIM_NOT_SUPPORTED"]
    payload["validated_selection_sha256"] = None
    payload["authorization_decision"] = None
    payload["authorization_reason_codes"] = []
    payload["authorization_receipt_ref"] = None
    payload["authorization_receipt_sha256"] = None
    citation = payload["claims"][0]["citations"][0]
    citation["authorized"] = False
    citation["authorization_reason_code"] = None
    citation["authorization_selection_sha256"] = None
    payload = _with_hash(payload, "observation_sha256")

    observation = _parse_observation(payload)

    assert observation.authorization_decision is None
    assert observation.claims[0].citations[0].authorization_reason_code is None


def test_rejected_validation_cannot_claim_authorized_receipt() -> None:
    payload = _observation_payload()
    payload["validation_decision"] = "REJECTED"
    payload["validation_reason_codes"] = ["CLAIM_NOT_SUPPORTED"]
    payload["validated_selection_sha256"] = None

    _assert_observation_schema_invalid(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        "validated_without_selection",
        "validated_with_reason",
        "rejected_with_selection",
        "validation_rejected_without_reason",
        "non_evaluated_validated",
        "authorized_without_receipt",
        "authorized_with_reason",
        "rejected_with_receipt",
        "authorization_rejected_without_reason",
        "missing_authorization_with_citation",
    ],
)
def test_observation_rejects_outcome_tuple_mismatch(mutation: str) -> None:
    payload = _observation_payload()
    if mutation == "validated_without_selection":
        payload["validated_selection_sha256"] = None
    elif mutation == "validated_with_reason":
        payload["validation_reason_codes"] = ["REQUEST_INVALID"]
    elif mutation == "rejected_with_selection":
        payload["validation_decision"] = "REJECTED"
        payload["validation_reason_codes"] = ["REQUEST_INVALID"]
    elif mutation == "validation_rejected_without_reason":
        payload["validation_decision"] = "REJECTED"
        payload["validated_selection_sha256"] = None
    elif mutation == "non_evaluated_validated":
        payload["validation_execution_status"] = "VALIDATION_ERROR"
    elif mutation == "authorized_without_receipt":
        payload["authorization_receipt_ref"] = None
    elif mutation == "authorized_with_reason":
        payload["authorization_reason_codes"] = ["RECEIPT_INVALID"]
    elif mutation == "rejected_with_receipt":
        payload["authorization_decision"] = "REJECTED"
        payload["authorization_reason_codes"] = ["RECEIPT_INVALID"]
    elif mutation == "authorization_rejected_without_reason":
        payload["authorization_decision"] = "REJECTED"
        payload["authorization_receipt_ref"] = None
        payload["authorization_receipt_sha256"] = None
    else:
        payload["authorization_decision"] = None
        payload["authorization_receipt_ref"] = None
        payload["authorization_receipt_sha256"] = None

    _assert_observation_schema_invalid(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("observation_ref", _ref("unexpected-observation")),
        ("observation_sha256", SHA_C),
        ("critical_unsupported_claim", True),
        ("uncited_medical_claim", True),
        ("source_binding_misuse", True),
    ],
)
def test_no_claims_signal_rejects_nonempty_binding_or_failure(field: str, value: object) -> None:
    payload = _signal_payload(no_claims=True)
    payload[field] = value

    _assert_signal_schema_invalid(payload)


@pytest.mark.parametrize("field", ["answer_sha256", "observation_ref", "observation_sha256"])
def test_evaluated_signal_requires_every_binding(field: str) -> None:
    payload = _signal_payload()
    payload[field] = None

    _assert_signal_schema_invalid(payload)


@pytest.mark.parametrize(
    ("payload_factory", "hash_field"),
    [(_observation_payload, "observation_sha256"), (_signal_payload, "signal_sha256")],
)
def test_parsers_reject_self_hash_mismatch(payload_factory, hash_field: str) -> None:
    payload = payload_factory()
    payload[hash_field] = "f" * 64

    with pytest.raises(EvaluationValidationError) as caught:
        canonical = canonical_json_bytes(payload)
        if hash_field == "observation_sha256":
            parse_claim_citation_observation_bytes(canonical)
        else:
            parse_grounding_signal_bytes(canonical)

    assert caught.value.code is EvaluationErrorCode.HASH_MISMATCH


@pytest.mark.parametrize("model", [ClaimCitationObservation, GroundingSignal])
def test_projection_models_expose_no_body_or_sensitive_identity_fields(model: type[BaseModel]) -> None:
    forbidden = {
        "query",
        "question",
        "answer_text",
        "claim_text",
        "source_body",
        "provider_payload",
        "credential",
        "patient",
    }

    def property_names(value: object) -> set[str]:
        if isinstance(value, dict):
            names = set(value.get("properties", {})) if isinstance(value.get("properties"), dict) else set()
            return names | set().union(*(property_names(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(property_names(item) for item in value))
        return set()

    assert property_names(model.model_json_schema()).isdisjoint(forbidden)
