from __future__ import annotations

import re
from typing import Annotated, Any

import pytest
from pydantic import AfterValidator, ValidationError

from ai_worker.tasks.evaluation.schemas.common import (
    ActorNamespace,
    ActorRef,
    ActorRole,
    CanonicalDecimal,
    CanonicalUuid,
    DecisionStatus,
    ExecutionDecisionMixin,
    ExecutionStatus,
    ImmutableReference,
    ResourcePath,
    ReviewProvenance,
    SafeInteger,
    Sha256Hex,
    StrictContractModel,
    UtcTimestamp,
    ensure_unique_resource_paths,
)
from ai_worker.tasks.evaluation.schemas.common_v1_2 import ReviewProvenanceV12


class ExecutionDecision(ExecutionDecisionMixin):
    pass


class RequiredExecutionDecision(ExecutionDecisionMixin):
    required: bool


class ScalarContract(StrictContractModel):
    count: SafeInteger
    digest: Sha256Hex
    identifier: CanonicalUuid
    ratio: CanonicalDecimal
    recorded_at: UtcTimestamp
    resource_path: ResourcePath


UniqueResourcePaths = Annotated[list[ResourcePath], AfterValidator(ensure_unique_resource_paths)]


class ResourceList(StrictContractModel):
    paths: UniqueResourcePaths


def valid_scalar_payload() -> dict[str, object]:
    return {
        "count": 1,
        "digest": "a" * 64,
        "identifier": "123e4567-e89b-42d3-a456-426614174000",
        "ratio": "0.125",
        "recorded_at": "2026-09-01T03:04:05.000000Z",
        "resource_path": "fixtures/case.json",
    }


def test_strict_contract_rejects_extra_fields_and_is_frozen() -> None:
    payload = valid_scalar_payload()
    payload["extra"] = "forbidden"
    with pytest.raises(ValidationError):
        ScalarContract.model_validate(payload)

    model = ScalarContract.model_validate(valid_scalar_payload())
    with pytest.raises(ValidationError):
        model.count = 2


@pytest.mark.parametrize("count", [True, -(2**53), 2**53])
def test_safe_integer_rejects_boolean_and_out_of_range_values(count: object) -> None:
    payload = valid_scalar_payload()
    payload["count"] = count

    with pytest.raises(ValidationError):
        ScalarContract.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("digest", "A" * 64),
        ("identifier", "123E4567-E89B-42D3-A456-426614174000"),
        ("identifier", "{123e4567-e89b-42d3-a456-426614174000}"),
        ("ratio", "01.25"),
        ("ratio", "1.250"),
        ("ratio", "-0"),
        ("ratio", "-00.5"),
        ("ratio", "-01.5"),
        ("resource_path", "C:/absolute.json"),
        ("recorded_at", "2026-09-01 03:04:05+00:00"),
        ("recorded_at", "2026-09-01T03:04:05+00:00"),
        ("recorded_at", "2026-02-30T03:04:05.000000Z"),
    ],
)
def test_scalar_contract_rejects_noncanonical_wire_values(field: str, value: str) -> None:
    payload = valid_scalar_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        ScalarContract.model_validate(payload)


def test_resource_paths_are_normalized_and_duplicate_normalized_paths_are_rejected() -> None:
    model = ResourceList(paths=["fixtures/café.json"])
    assert model.paths == ["fixtures/café.json"]

    with pytest.raises(ValidationError):
        ResourceList(paths=["fixtures/café.json", "fixtures/cafe\u0301.json"])


def test_execution_decision_rejects_pass_before_completion() -> None:
    with pytest.raises(ValidationError):
        ExecutionDecision.model_validate({"execution_status": "NOT_EVALUATED", "decision_status": "PASS"})


def test_execution_decision_requires_a_decision_only_after_completion() -> None:
    pending = ExecutionDecision.model_validate({"execution_status": "NOT_EVALUATED", "decision_status": None})
    completed = ExecutionDecision.model_validate({"execution_status": "COMPLETED", "decision_status": "PASS"})

    assert pending.execution_status is ExecutionStatus.NOT_EVALUATED
    assert completed.decision_status is DecisionStatus.PASS

    with pytest.raises(ValidationError):
        ExecutionDecision.model_validate({"execution_status": "COMPLETED", "decision_status": None})


def test_required_execution_decision_rejects_na() -> None:
    with pytest.raises(ValidationError):
        RequiredExecutionDecision.model_validate(
            {"execution_status": "COMPLETED", "decision_status": "N/A", "required": True}
        )


def test_review_provenance_rejects_self_approval_by_actor_identity() -> None:
    proposer = ActorRef(
        namespace=ActorNamespace.GITHUB_LOGIN,
        actor_id="reviewer-1",
        role=ActorRole.EVALUATION_IMPLEMENTER,
    )
    same_identity = ActorRef(
        namespace=ActorNamespace.GITHUB_LOGIN,
        actor_id="reviewer-1",
        role=ActorRole.PRODUCT_SAFETY_REVIEWER,
    )

    from ai_worker.tasks.evaluation.schemas.common import ReviewProvenance

    with pytest.raises(ValidationError):
        ReviewProvenance.model_validate(
            {
                "authored_by": proposer.model_dump(mode="json"),
                "reviewed_by": same_identity.model_dump(mode="json"),
                "approved_by": None,
                "authored_at": "2026-09-01T03:04:05.000000Z",
                "reviewed_at": "2026-09-01T03:04:05.000000Z",
                "approved_at": None,
                "team_gold_status": "REVIEWED",
                "external_medical_review_status": "NOT_REQUESTED",
                "external_medical_approval_receipt_ref": None,
                "evidence_review_refs": [],
            }
        )


def test_immutable_reference_uses_canonical_identity_version_and_hash() -> None:
    reference = ImmutableReference(
        id="rag-eval.dataset.synthetic-dev",
        version="1.0.0",
        hash="a" * 64,
    )

    assert reference.hash == "a" * 64


def test_section_16_actor_reference_requires_exact_namespace_actor_id_and_role_shape() -> None:
    actor = ActorRef.model_validate(
        {
            "namespace": "GITHUB_LOGIN",
            "actor_id": "rag-owner",
            "role": "EVALUATION_IMPLEMENTER",
        }
    )
    assert actor.identity == ("GITHUB_LOGIN", "rag-owner")

    with pytest.raises(ValidationError):
        ActorRef.model_validate(
            {
                "namespace": "GITHUB_LOGIN",
                "actor_id": "rag-owner",
                "role": "EVALUATION_IMPLEMENTER",
                "display_name": "must not be stored",
            }
        )


def test_section_16_immutable_reference_requires_exact_id_version_hash_shape() -> None:
    reference = ImmutableReference.model_validate(
        {"id": "rag-eval.dataset.synthetic-dev", "version": "1.0.0", "hash": "a" * 64}
    )
    assert reference.id == "rag-eval.dataset.synthetic-dev"

    with pytest.raises(ValidationError):
        ImmutableReference.model_validate(
            {
                "resource_id": "rag-eval.dataset.synthetic-dev",
                "resource_version": "1.0.0",
                "resource_hash": "a" * 64,
            }
        )


def test_section_16_timestamp_requires_exactly_six_fractional_utc_digits() -> None:
    payload = valid_scalar_payload()
    payload["recorded_at"] = "2026-09-01T03:04:05.123456Z"
    ScalarContract.model_validate(payload)

    for invalid in (
        "2026-09-01T03:04:05Z",
        "2026-09-01T03:04:05.1Z",
        "2026-09-01T03:04:05.1234567Z",
    ):
        payload["recorded_at"] = invalid
        with pytest.raises(ValidationError):
            ScalarContract.model_validate(payload)


def test_scalar_json_schema_preserves_runtime_patterns_and_formats() -> None:
    properties = ScalarContract.model_json_schema()["properties"]

    assert properties["digest"]["pattern"] == "^[0-9a-f]{64}$"
    assert properties["identifier"]["pattern"].startswith("^[0-9a-f]{8}")
    assert properties["identifier"]["format"] == "uuid"
    assert properties["ratio"]["pattern"].startswith("^(?:0|")
    assert properties["recorded_at"]["pattern"].endswith("Z$")
    assert properties["recorded_at"]["format"] == "date-time"
    assert "pattern" in properties["resource_path"]


def test_scalar_schema_patterns_accept_and_reject_the_same_concrete_values_as_runtime() -> None:
    properties = ScalarContract.model_json_schema()["properties"]
    decimal_pattern = properties["ratio"]["pattern"]
    path_pattern = properties["resource_path"]["pattern"]

    assert re.fullmatch(decimal_pattern, "-0.5")
    assert re.fullmatch(decimal_pattern, "-12")
    assert re.fullmatch(decimal_pattern, "-0") is None
    assert re.fullmatch(decimal_pattern, "-00.5") is None
    assert re.fullmatch(decimal_pattern, "-01.5") is None
    for invalid_path in (
        "/absolute.json",
        "C:/absolute.json",
        "cases//item.json",
        "cases/./item.json",
        "cases/../item.json",
        "a\\b",
        "cases/\x00item.json",
    ):
        assert re.fullmatch(path_pattern, invalid_path) is None


def _review_actor(actor_id: str, role: str) -> dict[str, str]:
    return {"namespace": "GITHUB_LOGIN", "actor_id": actor_id, "role": role}


def test_review_provenance_uses_exact_section_16_3_approved_shape_and_sorted_evidence_refs() -> None:
    payload: dict[str, Any] = {
        "authored_by": _review_actor("author-1", "EVALUATION_IMPLEMENTER"),
        "reviewed_by": _review_actor("reviewer-1", "DATASET_CUSTODIAN"),
        "approved_by": _review_actor("approver-1", "PRODUCT_SAFETY_REVIEWER"),
        "authored_at": "2026-09-01T00:00:00.000000Z",
        "reviewed_at": "2026-09-01T00:01:00.000000Z",
        "approved_at": "2026-09-01T00:02:00.000000Z",
        "team_gold_status": "APPROVED",
        "external_medical_review_status": "NOT_REQUESTED",
        "external_medical_approval_receipt_ref": None,
        "evidence_review_refs": [
            {"id": "evidence-review-1", "version": "1.0.0", "hash": "a" * 64},
            {"id": "evidence-review-2", "version": "1.0.0", "hash": "b" * 64},
        ],
    }

    provenance = ReviewProvenance.model_validate(payload)
    assert provenance.team_gold_status.value == "APPROVED"

    evidence_review_refs = payload["evidence_review_refs"]
    assert isinstance(evidence_review_refs, list)
    payload["evidence_review_refs"] = list(reversed(evidence_review_refs))
    with pytest.raises(ValidationError):
        ReviewProvenance.model_validate(payload)


def test_review_provenance_rejects_legacy_shape_and_invalid_status_role_combinations() -> None:
    with pytest.raises(ValidationError):
        ReviewProvenance.model_validate(
            {
                "proposed_by": _review_actor("author-1", "EVALUATION_IMPLEMENTER"),
                "approved_by": _review_actor("approver-1", "PRODUCT_SAFETY_REVIEWER"),
                "reviewed_at": "2026-09-01T00:01:00.000000Z",
            }
        )

    payload: dict[str, Any] = {
        "authored_by": _review_actor("same-actor", "EVALUATION_IMPLEMENTER"),
        "reviewed_by": _review_actor("same-actor", "DATASET_CUSTODIAN"),
        "approved_by": None,
        "authored_at": "2026-09-01T00:00:00.000000Z",
        "reviewed_at": "2026-09-01T00:01:00.000000Z",
        "approved_at": None,
        "team_gold_status": "REVIEWED",
        "external_medical_review_status": "NOT_REQUESTED",
        "external_medical_approval_receipt_ref": None,
        "evidence_review_refs": [],
    }
    with pytest.raises(ValidationError):
        ReviewProvenance.model_validate(payload)

    payload["reviewed_by"] = _review_actor("reviewer-1", "SYSTEM_VALIDATOR")
    with pytest.raises(ValidationError):
        ReviewProvenance.model_validate(payload)


def _v12_review_payload(status: str) -> dict[str, Any]:
    return {
        "authored_by": _review_actor("author-1", "EVALUATION_IMPLEMENTER"),
        "reviewed_by": None,
        "approved_by": None,
        "authored_at": "2026-09-03T00:00:00.000000Z",
        "reviewed_at": None,
        "approved_at": None,
        "team_gold_status": status,
        "external_medical_review_status": "NOT_REQUESTED",
        "external_medical_approval_receipt_ref": None,
        "evidence_review_refs": [],
    }


def _v12_review_ref() -> dict[str, str]:
    return {"id": "review-evidence-1", "version": "1.0.0", "hash": "a" * 64}


def test_review_provenance_v12_accepts_draft_without_reviewer() -> None:
    provenance = ReviewProvenanceV12.model_validate(_v12_review_payload("DRAFT"))

    assert provenance.reviewed_by is None
    assert provenance.reviewed_at is None
    assert provenance.evidence_review_refs == ()


@pytest.mark.parametrize(
    ("status", "reviewed_by", "reviewed_at", "evidence_review_refs"),
    [
        ("DRAFT", _review_actor("reviewer-1", "EVALUATION_REVIEWER"), None, []),
        ("DRAFT", None, "2026-09-03T00:01:00.000000Z", []),
        ("DRAFT", None, None, [_v12_review_ref()]),
        ("REVIEWED", None, "2026-09-03T00:01:00.000000Z", [_v12_review_ref()]),
        ("REVIEWED", _review_actor("reviewer-1", "EVALUATION_REVIEWER"), None, [_v12_review_ref()]),
        ("REVIEWED", _review_actor("reviewer-1", "EVALUATION_REVIEWER"), "2026-09-03T00:01:00.000000Z", []),
    ],
)
def test_review_provenance_v12_rejects_inconsistent_review_state(
    status: str,
    reviewed_by: dict[str, str] | None,
    reviewed_at: str | None,
    evidence_review_refs: list[dict[str, str]],
) -> None:
    payload = _v12_review_payload(status)
    payload.update(
        reviewed_by=reviewed_by,
        reviewed_at=reviewed_at,
        evidence_review_refs=evidence_review_refs,
    )

    with pytest.raises(ValidationError):
        ReviewProvenanceV12.model_validate(payload)


def test_review_provenance_v12_accepts_internal_evaluation_reviewer_after_actual_review() -> None:
    payload = _v12_review_payload("REVIEWED")
    payload.update(
        reviewed_by=_review_actor("gold-fixture-reviewer", "EVALUATION_REVIEWER"),
        reviewed_at="2026-09-03T00:01:00.000000Z",
        evidence_review_refs=[_v12_review_ref()],
    )

    provenance = ReviewProvenanceV12.model_validate(payload)

    assert provenance.reviewed_by is not None
    assert provenance.reviewed_by.role.value == "EVALUATION_REVIEWER"
