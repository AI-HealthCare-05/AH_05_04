"""Unit tests for the Issue #173 Medication Identification Preflight decision kernel.

Synthetic identifiers only.  No patient data, no real product identity, no insurance code.
"""

from __future__ import annotations

import inspect
from dataclasses import replace
from typing import cast

import pytest

from rag_runtime.identification_preflight import (
    MANIFEST_PROJECTION_VERSION,
    IdentificationSnapshotRef,
    MedicationIdentificationPreflightRequest,
    MedicationPreflightState,
    MedicationSnapshotRef,
    PreflightCurrentnessToken,
    PreflightDecision,
    PreflightExecutionStatus,
    PreflightReason,
    PreflightStaleProjection,
    PreflightStaleSignal,
    PreflightValidationCode,
    canonical_preflight_manifest_hash,
    evaluate_medication_identification_preflight,
    medication_ids_in_manifest_order,
    preflight_state_from_mapping,
    project_preflight_stale_signal,
    project_preflight_stale_signals,
)

PRESCRIPTION_ID = "11111111-1111-4111-8111-111111111111"
VERSION_ID = "22222222-2222-4222-8222-222222222222"
OTHER_VERSION_ID = "22222222-2222-4222-8222-2222222222ff"
BUNDLE_ID = "33333333-3333-4333-8333-333333333333"
OTHER_BUNDLE_ID = "33333333-3333-4333-8333-3333333333ff"


def medication_id(index: int) -> str:
    return f"44444444-4444-4444-8444-4444444444{index:02d}"


def identification_id(index: int) -> str:
    return f"55555555-5555-4555-8555-5555555555{index:02d}"


def currentness(
    *,
    observed_version_id: str | None = None,
    observed_bundle_id: str | None = None,
    ownership_verified: bool = True,
) -> PreflightCurrentnessToken:
    return PreflightCurrentnessToken(
        prescription_id=PRESCRIPTION_ID,
        pinned_prescription_version_id=VERSION_ID,
        observed_active_prescription_version_id=observed_version_id or VERSION_ID,
        pinned_runtime_release_bundle_id=BUNDLE_ID,
        observed_active_runtime_release_bundle_id=observed_bundle_id or BUNDLE_ID,
        ownership_verified=ownership_verified,
    )


def medication(index: int, *, version_id: str | None = None) -> MedicationSnapshotRef:
    return MedicationSnapshotRef(
        prescription_version_medication_id=medication_id(index),
        prescription_version_id=version_id or VERSION_ID,
        display_order=index,
    )


def identification(
    index: int,
    state: MedicationPreflightState = MedicationPreflightState.MATCHED,
    *,
    version_id: str | None = None,
    bundle_id: str | None = None,
) -> IdentificationSnapshotRef:
    if state is MedicationPreflightState.MATCHED:
        return IdentificationSnapshotRef(
            prescription_version_medication_id=medication_id(index),
            state=state,
            prescription_version_id=version_id or VERSION_ID,
            identification_id=identification_id(index),
            code_system="SYNTHETIC-CODE-SYSTEM",
            canonical_code=f"SYNTHETIC-{index:04d}",
            runtime_release_bundle_id=bundle_id or BUNDLE_ID,
        )
    return IdentificationSnapshotRef(
        prescription_version_medication_id=medication_id(index),
        state=state,
        prescription_version_id=version_id or VERSION_ID,
    )


def request(
    states: tuple[MedicationPreflightState, ...] = (MedicationPreflightState.MATCHED,),
    *,
    token: PreflightCurrentnessToken | None = None,
) -> MedicationIdentificationPreflightRequest:
    indexes = tuple(range(1, len(states) + 1))
    return MedicationIdentificationPreflightRequest(
        currentness=token or currentness(),
        medications=tuple(medication(index) for index in indexes),
        identifications=tuple(identification(index, state) for index, state in zip(indexes, states, strict=True)),
    )


def test_all_matched_returns_pass() -> None:
    outcome = evaluate_medication_identification_preflight(request((MedicationPreflightState.MATCHED,) * 3))

    assert outcome.execution_status is PreflightExecutionStatus.EVALUATED
    assert outcome.decision is PreflightDecision.PASS
    assert outcome.reason is PreflightReason.MATCHED
    assert outcome.medication_count == 3
    assert outcome.matched_count == 3
    assert outcome.identification_reasons == ()
    assert outcome.blocking_medication_ids == ()
    assert outcome.stale_signals == ()
    assert outcome.validation_codes == ()
    assert outcome.manifest_projection_version == MANIFEST_PROJECTION_VERSION
    assert outcome.manifest_hash is not None


@pytest.mark.parametrize(
    "state",
    [
        MedicationPreflightState.REVIEW_REQUIRED,
        MedicationPreflightState.AMBIGUOUS,
        MedicationPreflightState.UNAVAILABLE,
        MedicationPreflightState.NOT_FOUND,
        MedicationPreflightState.INVALID_INPUT,
        MedicationPreflightState.UNRESOLVED,
    ],
)
def test_single_non_matched_blocks_execution(state: MedicationPreflightState) -> None:
    outcome = evaluate_medication_identification_preflight(
        request((MedicationPreflightState.MATCHED, state, MedicationPreflightState.MATCHED))
    )

    assert outcome.decision is PreflightDecision.IDENTIFICATION_FALLBACK
    assert outcome.reason is PreflightReason(state.value)
    assert outcome.identification_reasons == (PreflightReason(state.value),)
    assert outcome.blocking_medication_ids == (medication_id(2),)
    assert outcome.matched_count == 2
    assert outcome.stale_signals == ()


def test_identification_fallback_reason_follows_contract_order() -> None:
    outcome = evaluate_medication_identification_preflight(
        request(
            (
                MedicationPreflightState.UNRESOLVED,
                MedicationPreflightState.AMBIGUOUS,
                MedicationPreflightState.REVIEW_REQUIRED,
            )
        )
    )

    assert outcome.reason is PreflightReason.REVIEW_REQUIRED
    assert outcome.identification_reasons == (
        PreflightReason.REVIEW_REQUIRED,
        PreflightReason.AMBIGUOUS,
        PreflightReason.UNRESOLVED,
    )
    assert outcome.blocking_medication_ids == (medication_id(1), medication_id(2), medication_id(3))


def test_active_version_change_returns_stale_fallback() -> None:
    outcome = evaluate_medication_identification_preflight(
        request(
            (MedicationPreflightState.MATCHED,),
            token=currentness(observed_version_id=OTHER_VERSION_ID),
        )
    )

    assert outcome.decision is PreflightDecision.STALE_FALLBACK
    assert outcome.reason is PreflightReason.EXECUTION_CONTEXT_STALE
    assert outcome.stale_signals == (PreflightStaleSignal.PRESCRIPTION_STALE,)
    assert outcome.identification_reasons == ()
    assert outcome.blocking_medication_ids == ()


def test_active_bundle_change_returns_stale_fallback() -> None:
    outcome = evaluate_medication_identification_preflight(
        request(
            (MedicationPreflightState.MATCHED,),
            token=currentness(observed_bundle_id=OTHER_BUNDLE_ID),
        )
    )

    assert outcome.decision is PreflightDecision.STALE_FALLBACK
    assert outcome.stale_signals == (PreflightStaleSignal.RUNTIME_RELEASE_STALE,)


def test_identification_pinned_to_other_version_returns_stale_fallback() -> None:
    base = request((MedicationPreflightState.MATCHED,))
    stale = replace(
        base,
        identifications=(identification(1, version_id=OTHER_VERSION_ID),),
    )

    outcome = evaluate_medication_identification_preflight(stale)

    assert outcome.decision is PreflightDecision.STALE_FALLBACK
    assert outcome.stale_signals == (PreflightStaleSignal.IDENTIFICATION_STALE,)


def test_matched_identification_from_other_bundle_returns_stale_fallback() -> None:
    base = request((MedicationPreflightState.MATCHED,))
    stale = replace(base, identifications=(identification(1, bundle_id=OTHER_BUNDLE_ID),))

    outcome = evaluate_medication_identification_preflight(stale)

    assert outcome.decision is PreflightDecision.STALE_FALLBACK
    assert outcome.stale_signals == (PreflightStaleSignal.RUNTIME_RELEASE_STALE,)


def test_stale_context_precedes_identification_fallback() -> None:
    outcome = evaluate_medication_identification_preflight(
        request(
            (MedicationPreflightState.AMBIGUOUS,),
            token=currentness(observed_version_id=OTHER_VERSION_ID),
        )
    )

    assert outcome.decision is PreflightDecision.STALE_FALLBACK
    assert outcome.reason is PreflightReason.EXECUTION_CONTEXT_STALE
    assert outcome.identification_reasons == ()
    assert outcome.primary_stale_projection == PreflightStaleProjection(
        fallback_code="PRESCRIPTION_STALE",
        stale_reason=None,
    )


def test_compound_stale_active_version_and_bundle_change() -> None:
    outcome = evaluate_medication_identification_preflight(
        request(
            (MedicationPreflightState.MATCHED,),
            token=currentness(
                observed_version_id=OTHER_VERSION_ID,
                observed_bundle_id=OTHER_BUNDLE_ID,
            ),
        )
    )

    assert outcome.decision is PreflightDecision.STALE_FALLBACK
    assert outcome.reason is PreflightReason.EXECUTION_CONTEXT_STALE
    assert outcome.stale_signals == (
        PreflightStaleSignal.PRESCRIPTION_STALE,
        PreflightStaleSignal.RUNTIME_RELEASE_STALE,
    )
    assert outcome.primary_stale_projection == PreflightStaleProjection(
        fallback_code="PRESCRIPTION_STALE",
        stale_reason=None,
    )


def test_compound_stale_version_and_identification_mismatch() -> None:
    base = request((MedicationPreflightState.MATCHED,), token=currentness(observed_version_id=OTHER_VERSION_ID))
    stale = replace(base, identifications=(identification(1, version_id=OTHER_VERSION_ID),))
    outcome = evaluate_medication_identification_preflight(stale)

    assert outcome.decision is PreflightDecision.STALE_FALLBACK
    assert outcome.stale_signals == (
        PreflightStaleSignal.PRESCRIPTION_STALE,
        PreflightStaleSignal.IDENTIFICATION_STALE,
    )
    assert outcome.primary_stale_projection == PreflightStaleProjection(
        fallback_code="PRESCRIPTION_STALE",
        stale_reason=None,
    )


def test_compound_stale_identification_and_bundle_mismatch() -> None:
    base = request((MedicationPreflightState.MATCHED,), token=currentness(observed_bundle_id=OTHER_BUNDLE_ID))
    stale = replace(base, identifications=(identification(1, version_id=OTHER_VERSION_ID),))
    outcome = evaluate_medication_identification_preflight(stale)

    assert outcome.decision is PreflightDecision.STALE_FALLBACK
    assert outcome.stale_signals == (
        PreflightStaleSignal.IDENTIFICATION_STALE,
        PreflightStaleSignal.RUNTIME_RELEASE_STALE,
    )
    assert outcome.primary_stale_projection == PreflightStaleProjection(
        fallback_code="EXECUTION_CONTEXT_STALE",
        stale_reason="IDENTIFICATION_STALE",
    )


def test_compound_stale_all_signals_present() -> None:
    base = request(
        (MedicationPreflightState.MATCHED,),
        token=currentness(
            observed_version_id=OTHER_VERSION_ID,
            observed_bundle_id=OTHER_BUNDLE_ID,
        ),
    )
    stale = replace(base, identifications=(identification(1, version_id=OTHER_VERSION_ID),))
    outcome = evaluate_medication_identification_preflight(stale)

    assert outcome.decision is PreflightDecision.STALE_FALLBACK
    assert outcome.stale_signals == (
        PreflightStaleSignal.PRESCRIPTION_STALE,
        PreflightStaleSignal.IDENTIFICATION_STALE,
        PreflightStaleSignal.RUNTIME_RELEASE_STALE,
    )
    assert outcome.primary_stale_projection == PreflightStaleProjection(
        fallback_code="PRESCRIPTION_STALE",
        stale_reason=None,
    )


def assert_fails_closed(
    broken: MedicationIdentificationPreflightRequest,
    expected: PreflightValidationCode,
) -> None:
    outcome = evaluate_medication_identification_preflight(broken)

    assert outcome.execution_status is PreflightExecutionStatus.VALIDATION_ERROR
    assert outcome.decision is PreflightDecision.IDENTIFICATION_FALLBACK
    assert outcome.reason is PreflightReason.REVIEW_REQUIRED
    assert outcome.manifest_hash is None
    assert outcome.matched_count == 0
    assert expected in outcome.validation_codes


def test_empty_medications_fails_closed() -> None:
    assert_fails_closed(
        MedicationIdentificationPreflightRequest(currentness(), (), ()),
        PreflightValidationCode.AT_LEAST_ONE_MEDICATION_REQUIRED,
    )


def test_duplicate_medication_id_fails_closed() -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(),
        (medication(1), replace(medication(1), display_order=2)),
        (identification(1), identification(1)),
    )

    assert_fails_closed(broken, PreflightValidationCode.DUPLICATE_MEDICATION_ID)


def test_duplicate_display_order_fails_closed() -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(),
        (medication(1), replace(medication(2), display_order=1)),
        (identification(1), identification(2)),
    )

    assert_fails_closed(broken, PreflightValidationCode.DUPLICATE_DISPLAY_ORDER)


def test_non_positive_display_order_fails_closed() -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(),
        (replace(medication(1), display_order=0),),
        (identification(1),),
    )

    assert_fails_closed(broken, PreflightValidationCode.DISPLAY_ORDER_NOT_POSITIVE)


def test_unknown_state_fails_closed() -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(),
        (medication(1),),
        (
            IdentificationSnapshotRef(
                prescription_version_medication_id=medication_id(1),
                state=cast(MedicationPreflightState, "PROBABLY_MATCHED"),
                prescription_version_id=VERSION_ID,
            ),
        ),
    )

    assert_fails_closed(broken, PreflightValidationCode.UNKNOWN_PREFLIGHT_STATE)


def test_missing_identification_snapshot_fails_closed() -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(),
        (medication(1), medication(2)),
        (identification(1),),
    )

    assert_fails_closed(broken, PreflightValidationCode.IDENTIFICATION_MEDICATION_SET_MISMATCH)


def test_ownership_not_verified_fails_closed() -> None:
    assert_fails_closed(
        request((MedicationPreflightState.MATCHED,), token=currentness(ownership_verified=False)),
        PreflightValidationCode.OWNERSHIP_NOT_VERIFIED,
    )


@pytest.mark.parametrize(
    "raw_id",
    [
        "{44444444-4444-4444-8444-444444444401}",
        "44444444444444448444444444444401",
        " 44444444-4444-4444-8444-444444444401",
        "44444444-4444-4444-8444-4444444444AB",
        "",
    ],
)
def test_non_canonical_uuid_fails_closed(raw_id: str) -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(),
        (replace(medication(1), prescription_version_medication_id=raw_id),),
        (replace(identification(1), prescription_version_medication_id=raw_id),),
    )

    assert_fails_closed(broken, PreflightValidationCode.IDENTIFIER_NOT_CANONICAL_UUID)


def test_matched_without_product_identity_fails_closed() -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(),
        (medication(1),),
        (replace(identification(1), canonical_code=None),),
    )

    assert_fails_closed(broken, PreflightValidationCode.MATCHED_PRODUCT_IDENTITY_REQUIRED)


def test_non_matched_with_product_identity_fails_closed() -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(),
        (medication(1),),
        (
            replace(
                identification(1, MedicationPreflightState.UNRESOLVED),
                code_system="SYNTHETIC-CODE-SYSTEM",
                canonical_code="SYNTHETIC-0001",
            ),
        ),
    )

    assert_fails_closed(broken, PreflightValidationCode.NON_MATCHED_PRODUCT_IDENTITY_FORBIDDEN)


def test_duplicate_identification_id_fails_closed() -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(),
        (medication(1), medication(2)),
        (identification(1), replace(identification(2), identification_id=identification_id(1))),
    )

    assert_fails_closed(broken, PreflightValidationCode.DUPLICATE_IDENTIFICATION_ID)


def test_medication_pinned_to_other_version_fails_closed() -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(),
        (medication(1, version_id=OTHER_VERSION_ID),),
        (identification(1),),
    )

    assert_fails_closed(broken, PreflightValidationCode.MEDICATION_VERSION_MISMATCH)


def test_list_inputs_fail_closed_instead_of_raising() -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(),
        cast(tuple[MedicationSnapshotRef, ...], [medication(1)]),
        cast(tuple[IdentificationSnapshotRef, ...], [identification(1)]),
    )

    assert_fails_closed(broken, PreflightValidationCode.REQUEST_SHAPE_INVALID)


def test_validation_codes_are_reported_in_declaration_order() -> None:
    broken = MedicationIdentificationPreflightRequest(
        currentness(ownership_verified=False),
        (),
        (),
    )

    outcome = evaluate_medication_identification_preflight(broken)

    assert outcome.validation_codes == (
        PreflightValidationCode.OWNERSHIP_NOT_VERIFIED,
        PreflightValidationCode.AT_LEAST_ONE_MEDICATION_REQUIRED,
    )


def test_input_order_does_not_change_manifest_or_decision() -> None:
    forward = request(
        (
            MedicationPreflightState.MATCHED,
            MedicationPreflightState.AMBIGUOUS,
            MedicationPreflightState.MATCHED,
        )
    )
    reversed_request = MedicationIdentificationPreflightRequest(
        currentness=forward.currentness,
        medications=tuple(reversed(forward.medications)),
        identifications=tuple(reversed(forward.identifications)),
    )

    first = evaluate_medication_identification_preflight(forward)
    second = evaluate_medication_identification_preflight(reversed_request)

    assert first == second
    assert canonical_preflight_manifest_hash(forward) == canonical_preflight_manifest_hash(reversed_request)


def test_manifest_hash_is_stable_across_currentness_observation() -> None:
    pinned = request((MedicationPreflightState.MATCHED,))
    stale = request(
        (MedicationPreflightState.MATCHED,),
        token=currentness(observed_version_id=OTHER_VERSION_ID),
    )

    assert canonical_preflight_manifest_hash(pinned) == canonical_preflight_manifest_hash(stale)
    assert evaluate_medication_identification_preflight(pinned).decision is PreflightDecision.PASS
    assert evaluate_medication_identification_preflight(stale).decision is PreflightDecision.STALE_FALLBACK


def test_manifest_hash_changes_with_state() -> None:
    matched = request((MedicationPreflightState.MATCHED,))
    unresolved = request((MedicationPreflightState.UNRESOLVED,))

    assert canonical_preflight_manifest_hash(matched) != canonical_preflight_manifest_hash(unresolved)


def test_manifest_hash_changes_with_identification_prescription_version_id() -> None:
    pinned = request((MedicationPreflightState.MATCHED,))
    different_version_identifications = (replace(pinned.identifications[0], prescription_version_id=OTHER_VERSION_ID),)
    different_version_request = replace(pinned, identifications=different_version_identifications)

    assert canonical_preflight_manifest_hash(pinned) != canonical_preflight_manifest_hash(different_version_request)
    assert evaluate_medication_identification_preflight(pinned).decision is PreflightDecision.PASS
    assert (
        evaluate_medication_identification_preflight(different_version_request).decision
        is PreflightDecision.STALE_FALLBACK
    )


def test_manifest_hash_changes_with_identification_runtime_release_bundle_id() -> None:
    pinned = request((MedicationPreflightState.MATCHED,))
    different_bundle_identifications = (replace(pinned.identifications[0], runtime_release_bundle_id=OTHER_BUNDLE_ID),)
    different_bundle_request = replace(pinned, identifications=different_bundle_identifications)

    assert canonical_preflight_manifest_hash(pinned) != canonical_preflight_manifest_hash(different_bundle_request)
    assert evaluate_medication_identification_preflight(pinned).decision is PreflightDecision.PASS
    assert (
        evaluate_medication_identification_preflight(different_bundle_request).decision
        is PreflightDecision.STALE_FALLBACK
    )


def test_repeated_evaluation_is_idempotent() -> None:
    subject = request((MedicationPreflightState.MATCHED, MedicationPreflightState.NOT_FOUND))

    outcomes = [evaluate_medication_identification_preflight(subject) for _ in range(5)]

    assert len({outcome for outcome in outcomes}) == 1
    assert subject == request((MedicationPreflightState.MATCHED, MedicationPreflightState.NOT_FOUND))


def test_kernel_has_no_execution_ports() -> None:
    parameters = inspect.signature(evaluate_medication_identification_preflight).parameters

    assert list(parameters) == ["request"]


def test_outcome_is_immutable() -> None:
    outcome = evaluate_medication_identification_preflight(request())

    with pytest.raises(AttributeError):
        outcome.decision = PreflightDecision.PASS  # type: ignore[misc]


def test_preflight_state_from_mapping_does_not_raise_on_unknown_value() -> None:
    assert preflight_state_from_mapping({"state": "MATCHED"}) is MedicationPreflightState.MATCHED
    assert preflight_state_from_mapping({"state": "PROBABLY_MATCHED"}) == "PROBABLY_MATCHED"
    assert preflight_state_from_mapping({}) == ""


def test_medication_ids_in_manifest_order() -> None:
    medications = (medication(3), medication(1), medication(2))

    assert medication_ids_in_manifest_order(medications) == (
        medication_id(1),
        medication_id(2),
        medication_id(3),
    )


def test_project_preflight_stale_signal() -> None:
    rx_proj = project_preflight_stale_signal(PreflightStaleSignal.PRESCRIPTION_STALE)
    assert rx_proj == PreflightStaleProjection(fallback_code="PRESCRIPTION_STALE", stale_reason=None)

    id_proj = project_preflight_stale_signal(PreflightStaleSignal.IDENTIFICATION_STALE)
    assert id_proj == PreflightStaleProjection(
        fallback_code="EXECUTION_CONTEXT_STALE", stale_reason="IDENTIFICATION_STALE"
    )

    bundle_proj = project_preflight_stale_signal(PreflightStaleSignal.RUNTIME_RELEASE_STALE)
    assert bundle_proj == PreflightStaleProjection(
        fallback_code="EXECUTION_CONTEXT_STALE", stale_reason="RUNTIME_RELEASE_STALE"
    )


def test_project_preflight_stale_signals_aggregation() -> None:
    assert project_preflight_stale_signals(()) is None

    assert project_preflight_stale_signals((PreflightStaleSignal.PRESCRIPTION_STALE,)) == PreflightStaleProjection(
        fallback_code="PRESCRIPTION_STALE", stale_reason=None
    )
    assert project_preflight_stale_signals((PreflightStaleSignal.IDENTIFICATION_STALE,)) == PreflightStaleProjection(
        fallback_code="EXECUTION_CONTEXT_STALE", stale_reason="IDENTIFICATION_STALE"
    )
    assert project_preflight_stale_signals((PreflightStaleSignal.RUNTIME_RELEASE_STALE,)) == PreflightStaleProjection(
        fallback_code="EXECUTION_CONTEXT_STALE", stale_reason="RUNTIME_RELEASE_STALE"
    )

    # Order-independent precedence: PRESCRIPTION_STALE wins
    assert project_preflight_stale_signals(
        (PreflightStaleSignal.PRESCRIPTION_STALE, PreflightStaleSignal.RUNTIME_RELEASE_STALE)
    ) == PreflightStaleProjection(fallback_code="PRESCRIPTION_STALE", stale_reason=None)
    assert project_preflight_stale_signals(
        (PreflightStaleSignal.RUNTIME_RELEASE_STALE, PreflightStaleSignal.PRESCRIPTION_STALE)
    ) == PreflightStaleProjection(fallback_code="PRESCRIPTION_STALE", stale_reason=None)

    # Order-independent precedence: IDENTIFICATION_STALE wins over RUNTIME_RELEASE_STALE
    assert project_preflight_stale_signals(
        (PreflightStaleSignal.IDENTIFICATION_STALE, PreflightStaleSignal.RUNTIME_RELEASE_STALE)
    ) == PreflightStaleProjection(fallback_code="EXECUTION_CONTEXT_STALE", stale_reason="IDENTIFICATION_STALE")
    assert project_preflight_stale_signals(
        (PreflightStaleSignal.RUNTIME_RELEASE_STALE, PreflightStaleSignal.IDENTIFICATION_STALE)
    ) == PreflightStaleProjection(fallback_code="EXECUTION_CONTEXT_STALE", stale_reason="IDENTIFICATION_STALE")
