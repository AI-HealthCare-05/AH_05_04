from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import AfterValidator, ValidationError

from ai_worker.tasks.evaluation.schemas.common import (
    ActorRef,
    CanonicalDecimal,
    CanonicalUuid,
    DecisionStatus,
    ExecutionDecisionMixin,
    ExecutionStatus,
    ImmutableReference,
    ResourcePath,
    SafeInteger,
    Sha256Hex,
    StrictContractModel,
    UtcTimestamp,
    ensure_unique_resource_paths,
)


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
        "recorded_at": "2026-09-01T03:04:05Z",
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
        ("recorded_at", "2026-09-01 03:04:05+00:00"),
        ("recorded_at", "2026-09-01T03:04:05+00:00"),
        ("recorded_at", "2026-02-30T03:04:05Z"),
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
    proposer = ActorRef(namespace="github", actor_id="reviewer-1")
    same_identity = ActorRef(namespace="github", actor_id="reviewer-1", display_name="Different label")

    from ai_worker.tasks.evaluation.schemas.common import ReviewProvenance

    with pytest.raises(ValidationError):
        ReviewProvenance(
            proposed_by=proposer,
            approved_by=same_identity,
            reviewed_at="2026-09-01T03:04:05Z",
        )


def test_immutable_reference_uses_canonical_identity_version_and_hash() -> None:
    reference = ImmutableReference(
        resource_id="rag-eval.dataset.synthetic-dev",
        resource_version="1.0.0",
        resource_hash="a" * 64,
    )

    assert reference.resource_hash == "a" * 64
