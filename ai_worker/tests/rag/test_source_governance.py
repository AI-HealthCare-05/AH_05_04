from collections.abc import Mapping
from dataclasses import replace
from typing import Any, TypedDict, cast

import pytest

from ai_worker.tasks.rag.source_governance import (
    SyntheticEnvironment,
    SyntheticGovernanceReason,
    SyntheticGuardDecision,
    SyntheticGuardManifestEntry,
    SyntheticGuardManifestEntryKind,
    SyntheticGuardOperation,
    SyntheticImmutableReference,
    SyntheticOriginGuardBinding,
    SyntheticSourceGovernanceFacts,
    SyntheticUsePurpose,
    evaluate_synthetic_source_governance,
)


class ManifestEntryShared(TypedDict):
    source_code: str
    source_version: str
    canonicalization_spec_version: str
    purpose_code: SyntheticUsePurpose
    approval_version: str
    scope_policy_hash: str
    freshness_policy_hash: str
    bundle_build_source_verification_stable_key: str


def synthetic_manifest_entries() -> tuple[SyntheticGuardManifestEntry, SyntheticGuardManifestEntry]:
    shared: ManifestEntryShared = {
        "source_code": "SYNTHETIC_SOURCE",
        "source_version": "v1",
        "canonicalization_spec_version": "v1",
        "purpose_code": SyntheticUsePurpose.RETRIEVAL,
        "approval_version": "approval-v1",
        "scope_policy_hash": "3" * 64,
        "freshness_policy_hash": "4" * 64,
        "bundle_build_source_verification_stable_key": "verification-1",
    }
    return (
        SyntheticGuardManifestEntry(
            member_kind=SyntheticGuardManifestEntryKind.RELEASE_SOURCE,
            endpoint_code=None,
            operation_code=None,
            artifact_code=None,
            artifact_version=None,
            content_sha256="1" * 64,
            source_manifest_member_hash="7" * 64,
            canonical_checksum="2" * 64,
            **shared,
        ),
        SyntheticGuardManifestEntry(
            member_kind=SyntheticGuardManifestEntryKind.SNAPSHOT_MEMBER,
            endpoint_code="SYNTHETIC_ENDPOINT",
            operation_code="SYNTHETIC_OPERATION",
            artifact_code=None,
            artifact_version=None,
            content_sha256="5" * 64,
            source_manifest_member_hash=None,
            canonical_checksum="6" * 64,
            **shared,
        ),
    )


def second_source_manifest_entries() -> tuple[SyntheticGuardManifestEntry, SyntheticGuardManifestEntry]:
    source_entry, snapshot_entry = synthetic_manifest_entries()
    return (
        replace(
            source_entry,
            source_code="SYNTHETIC_SOURCE_B",
            content_sha256="a" * 64,
            source_manifest_member_hash="c" * 64,
            canonical_checksum="d" * 64,
            bundle_build_source_verification_stable_key="verification-b",
        ),
        replace(
            snapshot_entry,
            source_code="SYNTHETIC_SOURCE_B",
            endpoint_code="SYNTHETIC_ENDPOINT_B",
            operation_code="SYNTHETIC_OPERATION_B",
            content_sha256="e" * 64,
            canonical_checksum="f" * 64,
            bundle_build_source_verification_stable_key="verification-b",
        ),
    )


def second_snapshot_manifest_entries() -> tuple[SyntheticGuardManifestEntry, SyntheticGuardManifestEntry]:
    source_entry, snapshot_entry = synthetic_manifest_entries()
    return (
        replace(
            source_entry,
            source_version="v2",
            content_sha256="9" * 64,
            source_manifest_member_hash="8" * 64,
            canonical_checksum="a" * 64,
            bundle_build_source_verification_stable_key="verification-2",
        ),
        replace(
            snapshot_entry,
            source_version="v2",
            endpoint_code="SYNTHETIC_ENDPOINT_V2",
            operation_code="SYNTHETIC_OPERATION_V2",
            content_sha256="b" * 64,
            canonical_checksum="c" * 64,
            bundle_build_source_verification_stable_key="verification-2",
        ),
    )


def passing_facts() -> SyntheticSourceGovernanceFacts:
    expected_references = (
        SyntheticImmutableReference("source-policy", "1.18", "a" * 64),
        SyntheticImmutableReference("bundle-manifest", "bundle-v7", "b" * 64),
    )
    target_entries = synthetic_manifest_entries()
    return SyntheticSourceGovernanceFacts(
        source_active=True,
        endpoint_verified=True,
        endpoint_runtime_enabled=True,
        endpoint_acquisition_approved=True,
        operation_runtime_enabled=True,
        operation_acquisition_approved=True,
        approval_effective=True,
        approval_expired=False,
        license_approved=True,
        clinical_scope_allowed=True,
        snapshot_freshness_current=True,
        revocation_unresolved=False,
        snapshot_complete=True,
        schema_matches=True,
        bundle_member_matches=True,
        operation=SyntheticGuardOperation.REQUEST,
        expected_purpose=SyntheticUsePurpose.RETRIEVAL,
        observed_purpose=SyntheticUsePurpose.RETRIEVAL,
        expected_environment=SyntheticEnvironment.LOCAL,
        observed_environment=SyntheticEnvironment.LOCAL,
        expected_immutable_references=expected_references,
        observed_immutable_references=expected_references,
        guard_target_manifest_spec_version="guard-target-manifest-v1",
        target_manifest_entries=target_entries,
        selection_manifest_entries=target_entries,
        expected_target_release_source_count=1,
        expected_target_release_source_manifest_hash="6086d157e3ab4d5f8af7f4e44b3833d3612e247b851bbf07e1f6a6f4cfe6365c",
        expected_target_snapshot_member_count=1,
        expected_target_snapshot_member_manifest_hash="8414c65163e4bee9109bf22ab64ddf66f3f774e426092c2acd361477a9f1962c",
        expected_selection_release_source_count=1,
        expected_selection_release_source_manifest_hash="5f1a1135a90a811359b5a1e0532cd4e1cc73950b5e2db1bc37bf33d0e14b905c",
        expected_selection_snapshot_member_count=1,
        expected_selection_snapshot_member_manifest_hash="8f8443ea5486eb7fac54f38421a61ac1c673e1dd381b7994a7c8e3998a6dfccd",
        expected_governance_revision=12,
        observed_governance_revision=12,
        expected_safety_epoch=4,
        observed_safety_epoch=4,
        runtime_environment_active=True,
        target_bundle_id="bundle-7",
        target_bundle_manifest_hash="b" * 64,
        requested_bundle_id="bundle-7",
        requested_bundle_manifest_hash="b" * 64,
        active_bundle_id="bundle-7",
        active_bundle_manifest_hash="b" * 64,
        request_scope_codes=("MEDICATION_GUIDANCE",),
        request_scope_manifest_hash="e5020ba42eded49c764da50b9215bb57b23f5e2e3755d34889d4b0831afa27dd",
        origin_request_guard=None,
    )


def changed_facts(changes: Mapping[str, object]) -> SyntheticSourceGovernanceFacts:
    return replace(passing_facts(), **cast(Any, dict(changes)))


def citation_manifest_fields() -> dict[str, object]:
    entries = tuple(
        replace(
            entry,
            purpose_code=SyntheticUsePurpose.PATIENT_CITATION,
            approval_version="citation-approval-v1",
            content_sha256=(
                "8" * 64
                if entry.member_kind is SyntheticGuardManifestEntryKind.RELEASE_SOURCE
                else entry.content_sha256
            ),
        )
        for entry in synthetic_manifest_entries()
    )
    return {
        "target_manifest_entries": entries,
        "selection_manifest_entries": entries,
        "expected_target_release_source_manifest_hash": "f4f54cce13e26fbb680f68fcf4c69a198cc4a9271e1778177fb0beb95ea8bf72",
        "expected_target_snapshot_member_manifest_hash": "5b01b03e7155737a38221b386fa546f3392d40e6b356a14cb0ab5ad643c27720",
        "expected_selection_release_source_manifest_hash": "2e4f1ea0b3dd5c669fe98f38b6d70d149509f73dffc5e983392d6f88b0df6539",
        "expected_selection_snapshot_member_manifest_hash": "d7f22a6f49ca26a7d3ae348e8ebc011d4a4e63f261b6f14b1c2469d4e9d44396",
    }


def test_complete_exact_matching_synthetic_evidence_passes() -> None:
    result = evaluate_synthetic_source_governance(passing_facts())

    assert result.decision is SyntheticGuardDecision.PASS
    assert result.observation_reasons == ()


def test_target_rejects_snapshot_member_without_same_source_release_entry() -> None:
    source_a, _ = synthetic_manifest_entries()
    _, snapshot_b = second_source_manifest_entries()
    cross_source_entries = (source_a, snapshot_b)
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "target_manifest_entries": cross_source_entries,
                "selection_manifest_entries": cross_source_entries,
                "expected_target_snapshot_member_manifest_hash": (
                    "54207659c8cbdfea23b9062c567616b7f5436b2a5476e4b44b38b189ba00b421"
                ),
                "expected_selection_snapshot_member_manifest_hash": (
                    "91ac2fc38c55d2f7fff0cc696d351ead517ff64c722f52f07b8291a41df50fb3"
                ),
            }
        )
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (
        SyntheticGovernanceReason.TARGET_SOURCE_MEMBER_RELATION_INVALID,
        SyntheticGovernanceReason.SELECTION_SOURCE_MEMBER_RELATION_INVALID,
    )


def test_target_rejects_member_from_a_different_snapshot_of_the_same_source() -> None:
    source_v1, _ = synthetic_manifest_entries()
    _, snapshot_v2 = second_snapshot_manifest_entries()
    mismatched_entries = (source_v1, snapshot_v2)
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "target_manifest_entries": mismatched_entries,
                "selection_manifest_entries": mismatched_entries,
                "expected_target_snapshot_member_manifest_hash": (
                    "c3bd45d2878fde4e283c823980c76a5e018714bdb0c0f0778c445ae820ceecf3"
                ),
                "expected_selection_snapshot_member_manifest_hash": (
                    "270d7dba22d6d248d747aa7d64674f878f3863ad4fb2e7ae4f6bded50fbac9fc"
                ),
            }
        )
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (
        SyntheticGovernanceReason.TARGET_SOURCE_MEMBER_RELATION_INVALID,
        SyntheticGovernanceReason.SELECTION_SOURCE_MEMBER_RELATION_INVALID,
    )


def test_selection_rejects_cross_source_release_and_snapshot_combination() -> None:
    source_a, snapshot_a = synthetic_manifest_entries()
    source_b, snapshot_b = second_source_manifest_entries()
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "target_manifest_entries": (source_a, snapshot_a, source_b, snapshot_b),
                "selection_manifest_entries": (source_a, snapshot_b),
                "expected_target_release_source_count": 2,
                "expected_target_release_source_manifest_hash": (
                    "f68a451e5fe28fa389b7bfb43600a34794cee50384429734992bab245521a80e"
                ),
                "expected_target_snapshot_member_count": 2,
                "expected_target_snapshot_member_manifest_hash": (
                    "40a02d237121b086279fc4dfde5539337d82353482037a7f3c8d151fba6cd143"
                ),
                "expected_selection_snapshot_member_manifest_hash": (
                    "91ac2fc38c55d2f7fff0cc696d351ead517ff64c722f52f07b8291a41df50fb3"
                ),
            }
        )
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (SyntheticGovernanceReason.SELECTION_SOURCE_MEMBER_RELATION_INVALID,)


def test_selection_rejects_members_from_a_different_snapshot_of_the_same_source() -> None:
    source_v1, snapshot_v1 = synthetic_manifest_entries()
    source_v2, snapshot_v2 = second_snapshot_manifest_entries()
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "target_manifest_entries": (source_v1, snapshot_v1, source_v2, snapshot_v2),
                "selection_manifest_entries": (source_v1, snapshot_v2),
                "expected_target_release_source_count": 2,
                "expected_target_release_source_manifest_hash": (
                    "8f14e50235fdc39ce104c985bb59d481e086d09413bfb83686cdfc42af0c790a"
                ),
                "expected_target_snapshot_member_count": 2,
                "expected_target_snapshot_member_manifest_hash": (
                    "c8b7aa8cac5038fbd3cfd60866ee0eab2f302918c9431630bc3f924cf24a326a"
                ),
                "expected_selection_snapshot_member_manifest_hash": (
                    "270d7dba22d6d248d747aa7d64674f878f3863ad4fb2e7ae4f6bded50fbac9fc"
                ),
            }
        )
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (SyntheticGovernanceReason.SELECTION_SOURCE_MEMBER_RELATION_INVALID,)


def test_request_rejects_selection_approved_for_a_different_purpose() -> None:
    safety_entries = tuple(
        replace(
            entry,
            purpose_code=SyntheticUsePurpose.SAFETY_ROUTING,
            approval_version="safety-approval-v1",
        )
        for entry in synthetic_manifest_entries()
    )
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "target_manifest_entries": safety_entries,
                "selection_manifest_entries": safety_entries,
                "expected_target_release_source_manifest_hash": (
                    "0b03c46ffcae2453bac9e73aea09d5fd172509cfb3b48f0324cbb9478f11069f"
                ),
                "expected_target_snapshot_member_manifest_hash": (
                    "16ed2c3b53b29fe168d6fa7ddb3f290b10061103ca5b0873ec896f2bc2f02f6a"
                ),
                "expected_selection_release_source_manifest_hash": (
                    "e8b597d3a65692f21448000b74ea5545a6d405ea68bbbc8c96765f4649877bdf"
                ),
                "expected_selection_snapshot_member_manifest_hash": (
                    "8bf37a0e985c440c1379c5d0539e1524fac97cb4e31b68057a2b1c81c1c42c27"
                ),
            }
        )
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (SyntheticGovernanceReason.SELECTION_PURPOSE_MISMATCH,)


@pytest.mark.parametrize(
    ("changed_field", "reason"),
    [
        ({"source_active": False}, SyntheticGovernanceReason.SOURCE_NOT_ACTIVE),
        ({"endpoint_verified": False}, SyntheticGovernanceReason.ENDPOINT_NOT_ELIGIBLE),
        ({"endpoint_runtime_enabled": False}, SyntheticGovernanceReason.ENDPOINT_NOT_ELIGIBLE),
        ({"endpoint_acquisition_approved": False}, SyntheticGovernanceReason.ENDPOINT_NOT_ELIGIBLE),
        ({"operation_runtime_enabled": False}, SyntheticGovernanceReason.OPERATION_NOT_ELIGIBLE),
        ({"operation_acquisition_approved": False}, SyntheticGovernanceReason.OPERATION_NOT_ELIGIBLE),
        ({"approval_effective": False}, SyntheticGovernanceReason.APPROVAL_NOT_EFFECTIVE),
        ({"license_approved": False}, SyntheticGovernanceReason.LICENSE_NOT_APPROVED),
        ({"clinical_scope_allowed": False}, SyntheticGovernanceReason.CLINICAL_SCOPE_NOT_ALLOWED),
        ({"snapshot_freshness_current": False}, SyntheticGovernanceReason.SNAPSHOT_FRESHNESS_STALE),
        ({"revocation_unresolved": True}, SyntheticGovernanceReason.REVOCATION_UNRESOLVED),
        ({"snapshot_complete": False}, SyntheticGovernanceReason.SNAPSHOT_INCOMPLETE),
        ({"schema_matches": False}, SyntheticGovernanceReason.SCHEMA_DRIFT),
        ({"bundle_member_matches": False}, SyntheticGovernanceReason.BUNDLE_MEMBER_MISMATCH),
    ],
)
def test_observed_governance_evidence_fails_closed(
    changed_field: dict[str, bool],
    reason: SyntheticGovernanceReason,
) -> None:
    result = evaluate_synthetic_source_governance(changed_facts(changed_field))

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (reason,)


def test_expired_approval_has_a_distinct_fail_closed_reason() -> None:
    result = evaluate_synthetic_source_governance(changed_facts({"approval_expired": True}))

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (SyntheticGovernanceReason.APPROVAL_EXPIRED,)


@pytest.mark.parametrize(
    ("changed_field", "reason"),
    [
        (
            {"observed_purpose": SyntheticUsePurpose.PATIENT_CITATION},
            SyntheticGovernanceReason.PURPOSE_MISMATCH,
        ),
        (
            {"observed_environment": SyntheticEnvironment.TEST},
            SyntheticGovernanceReason.ENVIRONMENT_MISMATCH,
        ),
        (
            {
                "observed_immutable_references": (
                    SyntheticImmutableReference("source-policy", "1.19", "a" * 64),
                    SyntheticImmutableReference("bundle-manifest", "bundle-v7", "b" * 64),
                )
            },
            SyntheticGovernanceReason.IMMUTABLE_REFERENCE_MISMATCH,
        ),
        ({"observed_governance_revision": 13}, SyntheticGovernanceReason.GOVERNANCE_REVISION_MISMATCH),
        ({"observed_safety_epoch": 5}, SyntheticGovernanceReason.SAFETY_EPOCH_MISMATCH),
    ],
)
def test_exact_match_contract_fails_closed(
    changed_field: dict[str, object],
    reason: SyntheticGovernanceReason,
) -> None:
    result = evaluate_synthetic_source_governance(changed_facts(changed_field))

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (reason,)


@pytest.mark.parametrize(
    ("changed_field", "reasons"),
    [
        (
            {"expected_immutable_references": (), "observed_immutable_references": ()},
            (SyntheticGovernanceReason.IMMUTABLE_REFERENCE_INVALID,),
        ),
        (
            {"expected_target_release_source_count": 0},
            (SyntheticGovernanceReason.TARGET_RELEASE_SOURCE_MANIFEST_MISMATCH,),
        ),
        (
            {"expected_selection_snapshot_member_count": 0},
            (SyntheticGovernanceReason.SELECTION_SNAPSHOT_MEMBER_MANIFEST_MISMATCH,),
        ),
        (
            {"expected_target_snapshot_member_manifest_hash": ""},
            (SyntheticGovernanceReason.TARGET_SNAPSHOT_MEMBER_MANIFEST_MISMATCH,),
        ),
    ],
)
def test_equal_but_unproven_reference_or_manifest_facts_cannot_pass(
    changed_field: dict[str, object],
    reasons: tuple[SyntheticGovernanceReason, ...],
) -> None:
    result = evaluate_synthetic_source_governance(changed_facts(changed_field))

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == reasons


def test_guard_decision_has_only_non_runtime_pass_fail_values() -> None:
    assert [decision.value for decision in SyntheticGuardDecision] == ["PASS", "FAIL"]


@pytest.mark.parametrize(
    "operation",
    [
        SyntheticGuardOperation.REQUEST,
        SyntheticGuardOperation.CITATION_AUTHORIZATION,
    ],
)
def test_selection_operations_require_complete_nonempty_selection(operation: SyntheticGuardOperation) -> None:
    purpose = (
        SyntheticUsePurpose.PATIENT_CITATION
        if operation is SyntheticGuardOperation.CITATION_AUTHORIZATION
        else SyntheticUsePurpose.RETRIEVAL
    )
    facts = changed_facts(
        {
            "operation": operation,
            "expected_purpose": purpose,
            "observed_purpose": purpose,
            "origin_request_guard": (
                valid_origin_request_guard() if operation is SyntheticGuardOperation.CITATION_AUTHORIZATION else None
            ),
            **(citation_manifest_fields() if operation is SyntheticGuardOperation.CITATION_AUTHORIZATION else {}),
            "expected_selection_release_source_count": None,
            "expected_selection_release_source_manifest_hash": None,
            "expected_selection_snapshot_member_count": None,
            "expected_selection_snapshot_member_manifest_hash": None,
            "selection_manifest_entries": (),
        }
    )

    result = evaluate_synthetic_source_governance(facts)

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (SyntheticGovernanceReason.SELECTION_REQUIRED,)


@pytest.mark.parametrize(
    "operation",
    [
        SyntheticGuardOperation.EVALUATION_CANDIDATE,
        SyntheticGuardOperation.PLANNED_ACTIVATION,
        SyntheticGuardOperation.EMERGENCY_ROLLBACK,
        SyntheticGuardOperation.RESUME,
    ],
)
def test_operations_without_complete_context_are_not_claimed_by_source_eligibility_evaluator(
    operation: SyntheticGuardOperation,
) -> None:
    no_selection = {
        "operation": operation,
        "expected_selection_release_source_count": None,
        "expected_selection_release_source_manifest_hash": None,
        "expected_selection_snapshot_member_count": None,
        "expected_selection_snapshot_member_manifest_hash": None,
        "selection_manifest_entries": (),
    }

    passing_result = evaluate_synthetic_source_governance(changed_facts(no_selection))
    forbidden_result = evaluate_synthetic_source_governance(changed_facts({"operation": operation}))

    assert passing_result.decision is SyntheticGuardDecision.FAIL
    assert passing_result.observation_reasons == (SyntheticGovernanceReason.OPERATION_CONTEXT_NOT_MODELED,)
    assert forbidden_result.decision is SyntheticGuardDecision.FAIL
    assert forbidden_result.observation_reasons == (SyntheticGovernanceReason.OPERATION_CONTEXT_NOT_MODELED,)


@pytest.mark.parametrize(
    "operation",
    [SyntheticGuardOperation.REQUEST, SyntheticGuardOperation.CITATION_AUTHORIZATION],
)
@pytest.mark.parametrize(
    ("changed_field", "reason"),
    [
        ({"runtime_environment_active": False}, SyntheticGovernanceReason.RUNTIME_ENVIRONMENT_NOT_ACTIVE),
        ({"requested_bundle_id": "bundle-8"}, SyntheticGovernanceReason.ACTIVE_BUNDLE_ID_MISMATCH),
        (
            {"requested_bundle_manifest_hash": "e" * 64},
            SyntheticGovernanceReason.ACTIVE_BUNDLE_MANIFEST_MISMATCH,
        ),
    ],
)
def test_request_and_citation_require_active_environment_bundle_exact_match(
    operation: SyntheticGuardOperation,
    changed_field: dict[str, object],
    reason: SyntheticGovernanceReason,
) -> None:
    operation_fields: dict[str, object] = {"operation": operation}
    if operation is SyntheticGuardOperation.CITATION_AUTHORIZATION:
        operation_fields.update(
            expected_purpose=SyntheticUsePurpose.PATIENT_CITATION,
            observed_purpose=SyntheticUsePurpose.PATIENT_CITATION,
            origin_request_guard=valid_origin_request_guard(),
        )
        operation_fields.update(citation_manifest_fields())
    result = evaluate_synthetic_source_governance(changed_facts({**operation_fields, **changed_field}))

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (reason,)


def test_citation_authorization_requires_patient_citation_purpose() -> None:
    result = evaluate_synthetic_source_governance(
        changed_facts({"operation": SyntheticGuardOperation.CITATION_AUTHORIZATION})
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (SyntheticGovernanceReason.CITATION_PURPOSE_REQUIRED,)


def valid_origin_request_guard() -> SyntheticOriginGuardBinding:
    return SyntheticOriginGuardBinding(
        decision=SyntheticGuardDecision.PASS,
        operation=SyntheticGuardOperation.REQUEST,
        bundle_id="bundle-7",
        bundle_manifest_hash="b" * 64,
        environment=SyntheticEnvironment.LOCAL,
        request_scope_codes=("MEDICATION_GUIDANCE",),
        scope_manifest_hash="e5020ba42eded49c764da50b9215bb57b23f5e2e3755d34889d4b0831afa27dd",
    )


def test_citation_authorization_requires_exact_origin_request_guard_binding() -> None:
    citation_facts = {
        "operation": SyntheticGuardOperation.CITATION_AUTHORIZATION,
        "expected_purpose": SyntheticUsePurpose.PATIENT_CITATION,
        "observed_purpose": SyntheticUsePurpose.PATIENT_CITATION,
        "origin_request_guard": valid_origin_request_guard(),
        **citation_manifest_fields(),
    }

    passing_result = evaluate_synthetic_source_governance(changed_facts(citation_facts))
    mismatched_result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                **citation_facts,
                "origin_request_guard": replace(valid_origin_request_guard(), scope_manifest_hash="e" * 64),
            }
        )
    )

    assert passing_result.decision is SyntheticGuardDecision.PASS
    assert mismatched_result.decision is SyntheticGuardDecision.FAIL
    assert mismatched_result.observation_reasons == (SyntheticGovernanceReason.CITATION_ORIGIN_REQUEST_MISMATCH,)


def test_request_scope_codes_are_sorted_unique_and_bound_to_their_manifest_hash() -> None:
    unsorted_result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "request_scope_codes": ("SAFETY", "MEDICATION_GUIDANCE"),
                "request_scope_manifest_hash": "e" * 64,
            }
        )
    )
    mismatched_hash_result = evaluate_synthetic_source_governance(
        changed_facts({"request_scope_manifest_hash": "e" * 64})
    )

    assert SyntheticGovernanceReason.REQUEST_SCOPE_INVALID in unsorted_result.observation_reasons
    assert mismatched_hash_result.observation_reasons == (SyntheticGovernanceReason.REQUEST_SCOPE_INVALID,)


def test_citation_origin_scope_codes_and_hash_must_both_exact_match() -> None:
    citation_facts = {
        "operation": SyntheticGuardOperation.CITATION_AUTHORIZATION,
        "expected_purpose": SyntheticUsePurpose.PATIENT_CITATION,
        "observed_purpose": SyntheticUsePurpose.PATIENT_CITATION,
        "origin_request_guard": replace(
            valid_origin_request_guard(),
            request_scope_codes=("DIFFERENT_SCOPE",),
        ),
        **citation_manifest_fields(),
    }

    result = evaluate_synthetic_source_governance(changed_facts(citation_facts))

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (SyntheticGovernanceReason.CITATION_ORIGIN_REQUEST_MISMATCH,)


def test_citation_authorization_rejects_retrieval_manifest_entries() -> None:
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "operation": SyntheticGuardOperation.CITATION_AUTHORIZATION,
                "expected_purpose": SyntheticUsePurpose.PATIENT_CITATION,
                "observed_purpose": SyntheticUsePurpose.PATIENT_CITATION,
                "origin_request_guard": valid_origin_request_guard(),
            }
        )
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (SyntheticGovernanceReason.SELECTION_PURPOSE_MISMATCH,)


def test_request_and_citation_bundle_identity_must_match_trusted_target() -> None:
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "requested_bundle_id": "bundle-8",
                "active_bundle_id": "bundle-8",
            }
        )
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (SyntheticGovernanceReason.ACTIVE_BUNDLE_ID_MISMATCH,)


def test_request_and_citation_manifest_must_match_trusted_immutable_reference() -> None:
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "requested_bundle_manifest_hash": "e" * 64,
                "active_bundle_manifest_hash": "e" * 64,
            }
        )
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (SyntheticGovernanceReason.ACTIVE_BUNDLE_MANIFEST_MISMATCH,)


def test_guard_recomputes_target_manifest_instead_of_trusting_recorded_hash() -> None:
    changed_entry = replace(synthetic_manifest_entries()[1], content_sha256="7" * 64)
    result = evaluate_synthetic_source_governance(
        changed_facts({"target_manifest_entries": (synthetic_manifest_entries()[0], changed_entry)})
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (
        SyntheticGovernanceReason.SELECTION_NOT_TARGET_SUBSET,
        SyntheticGovernanceReason.TARGET_SNAPSHOT_MEMBER_MANIFEST_MISMATCH,
    )


def test_guard_uses_authority_envelope_for_each_release_source_and_snapshot_set() -> None:
    result = evaluate_synthetic_source_governance(
        changed_facts({"expected_target_release_source_manifest_hash": "e" * 64})
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert SyntheticGovernanceReason.TARGET_RELEASE_SOURCE_MANIFEST_MISMATCH in result.observation_reasons


@pytest.mark.parametrize(
    ("entry_set", "changes", "reason"),
    [
        (
            "target",
            {
                "expected_target_release_source_count": 0,
                "expected_target_release_source_manifest_hash": (
                    "1cfbac5b36454deae13b3bd4b70941d299f5d99dd720576148f7881b0a34a974"
                ),
            },
            SyntheticGovernanceReason.TARGET_RELEASE_SOURCE_MANIFEST_MISMATCH,
        ),
        (
            "target",
            {
                "expected_target_snapshot_member_count": 0,
                "expected_target_snapshot_member_manifest_hash": (
                    "a0cd17364e17b955340d2b1fd83e106bdbe94e4d481a472244ada32c19ed8773"
                ),
            },
            SyntheticGovernanceReason.TARGET_SNAPSHOT_MEMBER_MANIFEST_MISMATCH,
        ),
        (
            "selection",
            {
                "expected_selection_release_source_count": 0,
                "expected_selection_release_source_manifest_hash": (
                    "a69a9fc16c3c887b24b20f2a0db7647bbedef6cfda58b5085ba0622ee08abbbc"
                ),
            },
            SyntheticGovernanceReason.SELECTION_RELEASE_SOURCE_MANIFEST_MISMATCH,
        ),
        (
            "selection",
            {
                "expected_selection_snapshot_member_count": 0,
                "expected_selection_snapshot_member_manifest_hash": (
                    "fe2248e4ed433ef8306fe4cab43b64b0c25d3cabb8c5c9b357ffba476e3a57a3"
                ),
            },
            SyntheticGovernanceReason.SELECTION_SNAPSHOT_MEMBER_MANIFEST_MISMATCH,
        ),
    ],
)
def test_each_required_split_manifest_rejects_zero_count_even_with_matching_empty_hash(
    entry_set: str,
    changes: dict[str, object],
    reason: SyntheticGovernanceReason,
) -> None:
    source_entry, snapshot_entry = synthetic_manifest_entries()
    removed_kind = (
        SyntheticGuardManifestEntryKind.RELEASE_SOURCE
        if "release_source" in next(iter(changes))
        else SyntheticGuardManifestEntryKind.SNAPSHOT_MEMBER
    )
    remaining_entries = tuple(entry for entry in (source_entry, snapshot_entry) if entry.member_kind != removed_kind)
    manifest_change = {
        "target_manifest_entries" if entry_set == "target" else "selection_manifest_entries": remaining_entries
    }

    result = evaluate_synthetic_source_governance(changed_facts({**changes, **manifest_change}))

    assert reason in result.observation_reasons


def test_release_source_manifest_hash_binds_source_manifest_member_hash() -> None:
    source_entry, snapshot_entry = synthetic_manifest_entries()
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "target_manifest_entries": (
                    replace(source_entry, source_manifest_member_hash="9" * 64),
                    snapshot_entry,
                )
            }
        )
    )

    assert SyntheticGovernanceReason.TARGET_RELEASE_SOURCE_MANIFEST_MISMATCH in result.observation_reasons


def test_guard_rejects_duplicate_or_unknown_canonical_manifest_entries() -> None:
    source_entry, snapshot_entry = synthetic_manifest_entries()
    duplicate_result = evaluate_synthetic_source_governance(
        changed_facts({"target_manifest_entries": (source_entry, snapshot_entry, snapshot_entry)})
    )
    unknown_kind_result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "target_manifest_entries": (
                    source_entry,
                    replace(snapshot_entry, member_kind=cast(Any, "UNKNOWN")),
                )
            }
        )
    )

    assert SyntheticGovernanceReason.CANONICAL_MANIFEST_ENTRY_INVALID in duplicate_result.observation_reasons
    assert SyntheticGovernanceReason.CANONICAL_MANIFEST_ENTRY_INVALID in unknown_kind_result.observation_reasons


def test_distinct_release_source_member_hashes_are_not_treated_as_duplicate_entries() -> None:
    source_entry, snapshot_entry = synthetic_manifest_entries()
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "target_manifest_entries": (
                    source_entry,
                    replace(source_entry, source_manifest_member_hash="9" * 64),
                    snapshot_entry,
                )
            }
        )
    )

    assert SyntheticGovernanceReason.CANONICAL_MANIFEST_ENTRY_INVALID not in result.observation_reasons


def test_duplicate_release_source_projection_is_rejected_even_if_non_projection_fields_differ() -> None:
    source_entry, snapshot_entry = synthetic_manifest_entries()
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "target_manifest_entries": (
                    source_entry,
                    replace(source_entry, canonical_checksum="9" * 64),
                    snapshot_entry,
                )
            }
        )
    )

    assert SyntheticGovernanceReason.CANONICAL_MANIFEST_ENTRY_INVALID in result.observation_reasons


@pytest.mark.parametrize(
    "invalid_entry",
    [
        replace(synthetic_manifest_entries()[0], endpoint_code="UNEXPECTED_ENDPOINT"),
        replace(
            synthetic_manifest_entries()[1],
            artifact_code="UNEXPECTED_ARTIFACT",
            artifact_version="v1",
        ),
        replace(
            synthetic_manifest_entries()[1],
            endpoint_code=None,
            operation_code=None,
            artifact_code=None,
            artifact_version=None,
        ),
    ],
)
def test_guard_rejects_invalid_null_combinations_for_canonical_entry_kind(
    invalid_entry: SyntheticGuardManifestEntry,
) -> None:
    source_entry, snapshot_entry = synthetic_manifest_entries()
    target_entries = (
        (invalid_entry, snapshot_entry)
        if invalid_entry.member_kind is SyntheticGuardManifestEntryKind.RELEASE_SOURCE
        else (source_entry, invalid_entry)
    )

    result = evaluate_synthetic_source_governance(changed_facts({"target_manifest_entries": target_entries}))

    assert SyntheticGovernanceReason.CANONICAL_MANIFEST_ENTRY_INVALID in result.observation_reasons


def test_evaluation_request_is_fail_closed_until_candidate_and_runner_context_is_modeled() -> None:
    result = evaluate_synthetic_source_governance(
        changed_facts(
            {
                "operation": SyntheticGuardOperation.EVALUATION_REQUEST,
                "runtime_environment_active": False,
                "requested_bundle_id": None,
                "requested_bundle_manifest_hash": None,
                "active_bundle_id": None,
                "active_bundle_manifest_hash": None,
            }
        )
    )

    assert result.decision is SyntheticGuardDecision.FAIL
    assert result.observation_reasons == (SyntheticGovernanceReason.OPERATION_CONTEXT_NOT_MODELED,)


def test_combined_failures_preserve_all_observations_in_stable_order() -> None:
    facts = replace(
        passing_facts(),
        approval_effective=False,
        snapshot_freshness_current=False,
        observed_purpose=SyntheticUsePurpose.PATIENT_CITATION,
        observed_governance_revision=13,
    )

    first = evaluate_synthetic_source_governance(facts)
    second = evaluate_synthetic_source_governance(facts)

    assert first == second
    assert first.decision is SyntheticGuardDecision.FAIL
    assert first.observation_reasons == (
        SyntheticGovernanceReason.APPROVAL_NOT_EFFECTIVE,
        SyntheticGovernanceReason.SNAPSHOT_FRESHNESS_STALE,
        SyntheticGovernanceReason.PURPOSE_MISMATCH,
        SyntheticGovernanceReason.GOVERNANCE_REVISION_MISMATCH,
    )
