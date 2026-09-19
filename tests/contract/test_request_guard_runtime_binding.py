from uuid import UUID, uuid4

import pytest

from ai_worker.tasks.rag.citation_authorization import (
    canonical_scope_manifest_hash as citation_scope_manifest_hash,
)
from rag_runtime.request_authority import (
    RequestAuthorityArtifactRef,
    RequestAuthorityDecisionOutcome,
    RequestAuthorityDecisionStage,
)
from rag_runtime.request_guard_runtime_binding import (
    RequestGuardRuntimeBindingObservation,
    RequestGuardRuntimeBindingValidationError,
    canonical_scope_manifest_hash,
    compute_request_guard_runtime_binding_ref,
)
from rag_runtime.runtime_environment import RuntimeEnvironmentCode

USER_ID = UUID("80600000-0000-4000-8000-000000000001")
REQUEST_ID = UUID("80600000-0000-4000-8000-000000000002")
LEGACY_REF = RequestAuthorityArtifactRef(
    artifact_code="request_guard_authority",
    version="1.0",
    content_sha256="a" * 64,
)


def _observation(**overrides: object) -> RequestGuardRuntimeBindingObservation:
    values: dict[str, object] = {
        "request_guard_decision_id": REQUEST_ID,
        "actual_decision_outcome": RequestAuthorityDecisionOutcome.PASS,
        "user_id": USER_ID,
        "request_operation_code": "GUIDE_SYNC_ANSWER",
        "decision_stage": RequestAuthorityDecisionStage.REQUEST,
        "environment": RuntimeEnvironmentCode.PRODUCTION,
        "bundle_id": UUID("80600000-0000-4000-8000-000000000003"),
        "bundle_manifest_hash": "b" * 64,
        "request_scope_codes": ("GUIDE", "PATIENT_CITATION"),
        "scope_manifest_hash": canonical_scope_manifest_hash(("GUIDE", "PATIENT_CITATION")),
        "legacy_request_authority_ref": LEGACY_REF,
    }
    values.update(overrides)
    return RequestGuardRuntimeBindingObservation(**values)


def test_same_request_instance_is_content_addressed_deterministically() -> None:
    first = compute_request_guard_runtime_binding_ref(_observation())
    second = compute_request_guard_runtime_binding_ref(_observation())

    assert first == second
    assert first.artifact_code == "request_guard_runtime_binding"
    assert first.version == "1.0"


def test_different_request_instances_have_different_authority_refs() -> None:
    first = compute_request_guard_runtime_binding_ref(_observation())
    second = compute_request_guard_runtime_binding_ref(_observation(request_guard_decision_id=uuid4()))

    assert first.content_sha256 != second.content_sha256


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("request_scope_codes", ("PATIENT_CITATION", "GUIDE")),
        ("request_scope_codes", ("GUIDE", "GUIDE")),
        ("request_scope_codes", ()),
        ("request_scope_codes", ("e\u0301",)),
        ("actual_decision_outcome", "PASS"),
        ("environment", "production"),
    ),
)
def test_noncanonical_runtime_facts_fail_closed(field: str, value: object) -> None:
    with pytest.raises(RequestGuardRuntimeBindingValidationError):
        _observation(**{field: value})


def test_scope_hash_must_match_the_persisted_scope_tuple() -> None:
    with pytest.raises(RequestGuardRuntimeBindingValidationError):
        _observation(scope_manifest_hash="c" * 64)


@pytest.mark.parametrize(
    "scopes",
    (("GUIDE", "PATIENT_CITATION"), ("가이드", "환자"), ("A", "B", "C")),
)
def test_scope_hash_is_exactly_compatible_with_existing_citation_kernel(
    scopes: tuple[str, ...],
) -> None:
    assert canonical_scope_manifest_hash(scopes) == citation_scope_manifest_hash(scopes)


def test_fail_is_persisted_as_fail_and_changes_identity() -> None:
    passed = compute_request_guard_runtime_binding_ref(_observation())
    failed = compute_request_guard_runtime_binding_ref(
        _observation(actual_decision_outcome=RequestAuthorityDecisionOutcome.FAIL)
    )

    assert passed.content_sha256 != failed.content_sha256
    assert (
        _observation(actual_decision_outcome=RequestAuthorityDecisionOutcome.FAIL).actual_decision_outcome
        is RequestAuthorityDecisionOutcome.FAIL
    )
