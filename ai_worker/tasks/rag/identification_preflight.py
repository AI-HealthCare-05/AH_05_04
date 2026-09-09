"""Side-effect-free Medication Identification Preflight decision (RAG-12, Issue #173).

Chat ``ROUTINE`` and the automatic Guide must answer one question the same way: may this
prescription version proceed to the general Rule/Retrieval/Composer path?  This module is the
shared answer.  It performs no I/O, holds no lock, reads no clock, and takes no port, so a
passing unit test here proves determinism only -- never approval, publication readiness, or
``PUBLIC_TRACK_F_ENABLED``.

Scope boundary.  ``rag-runtime-v1.md`` fixes the preflight branches, but no approved document
fixes the projection from ``(candidate_search.status, medication_identification.status)`` onto
those branches.  The persisted status axis has only ``MATCHED | UNRESOLVED``.  This kernel
therefore accepts the per-medication :class:`MedicationPreflightState` as *input* and validates
it against an allowlist.  Deriving that state from repository rows belongs to RAG-12-API
(Issue #174) together with its own Decision.

See ``docs/designs/ceohwj/issue-173-medication-identification-preflight-design.md``.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

MANIFEST_PROJECTION_VERSION = "medication-identification-preflight-manifest-v1"

_CANONICAL_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class MedicationPreflightState(StrEnum):
    """Per-medication preflight state supplied by the caller.

    Declaration order is the enumeration order written in the ``rag-runtime-v1.md`` fixed
    execution graph, and :func:`evaluate_medication_identification_preflight` uses that order as
    the aggregate reason precedence.  Do not reorder without a Decision.
    """

    MATCHED = "MATCHED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_FOUND = "NOT_FOUND"
    INVALID_INPUT = "INVALID_INPUT"
    UNRESOLVED = "UNRESOLVED"


class PreflightDecision(StrEnum):
    PASS = "PASS"
    IDENTIFICATION_FALLBACK = "IDENTIFICATION_FALLBACK"
    STALE_FALLBACK = "STALE_FALLBACK"


class PreflightReason(StrEnum):
    MATCHED = "MATCHED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    AMBIGUOUS = "AMBIGUOUS"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_FOUND = "NOT_FOUND"
    INVALID_INPUT = "INVALID_INPUT"
    UNRESOLVED = "UNRESOLVED"
    EXECUTION_CONTEXT_STALE = "EXECUTION_CONTEXT_STALE"


class PreflightExecutionStatus(StrEnum):
    EVALUATED = "EVALUATED"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class PreflightStaleSignal(StrEnum):
    """Which staleness the caller must report downstream.  No new vocabulary is coined here.

    ``safety-result-v2.md`` "STALE과 공개 오류" separates the two public codes: a prescription
    version mismatch is published as ``PRESCRIPTION_STALE``, while Identification and Runtime
    Bundle mismatches are published as ``EXECUTION_CONTEXT_STALE`` with internal ``stale_reason``
    ``IDENTIFICATION_STALE`` / ``RUNTIME_RELEASE_STALE``.  The ``rag-runtime-v1.md`` graph labels
    the whole preflight branch ``EXECUTION_CONTEXT_STALE``, so :attr:`PreflightReason` keeps that
    label and this axis carries the distinction RAG-12-API (#174) needs to pick the right public
    ``fallback_code``.  Mapping ``reason`` straight onto ``fallback_code`` would publish
    ``EXECUTION_CONTEXT_STALE`` for a version change, which ``safety-result-v2.md`` forbids.

    ``PATIENT_CONTEXT_STALE``, ``RUNTIME_ENVIRONMENT_SUSPENDED`` and ``RESOLVER_MEMBER_REVOKED``
    are the remaining ``stale_reason`` values; none of them is derivable from this kernel's inputs,
    so they stay with #174 and #180.
    """

    PRESCRIPTION_STALE = "PRESCRIPTION_STALE"
    IDENTIFICATION_STALE = "IDENTIFICATION_STALE"
    RUNTIME_RELEASE_STALE = "RUNTIME_RELEASE_STALE"


class PreflightValidationCode(StrEnum):
    """Internal diagnostics.  Never projected onto a patient-facing DTO or message."""

    OWNERSHIP_NOT_VERIFIED = "OWNERSHIP_NOT_VERIFIED"
    IDENTIFIER_NOT_CANONICAL_UUID = "IDENTIFIER_NOT_CANONICAL_UUID"
    TEXT_NOT_NFC = "TEXT_NOT_NFC"
    AT_LEAST_ONE_MEDICATION_REQUIRED = "AT_LEAST_ONE_MEDICATION_REQUIRED"
    DUPLICATE_MEDICATION_ID = "DUPLICATE_MEDICATION_ID"
    DUPLICATE_DISPLAY_ORDER = "DUPLICATE_DISPLAY_ORDER"
    DISPLAY_ORDER_NOT_POSITIVE = "DISPLAY_ORDER_NOT_POSITIVE"
    MEDICATION_VERSION_MISMATCH = "MEDICATION_VERSION_MISMATCH"
    DUPLICATE_IDENTIFICATION_MEDICATION_ID = "DUPLICATE_IDENTIFICATION_MEDICATION_ID"
    DUPLICATE_IDENTIFICATION_ID = "DUPLICATE_IDENTIFICATION_ID"
    IDENTIFICATION_MEDICATION_SET_MISMATCH = "IDENTIFICATION_MEDICATION_SET_MISMATCH"
    UNKNOWN_PREFLIGHT_STATE = "UNKNOWN_PREFLIGHT_STATE"
    MATCHED_PRODUCT_IDENTITY_REQUIRED = "MATCHED_PRODUCT_IDENTITY_REQUIRED"
    NON_MATCHED_PRODUCT_IDENTITY_FORBIDDEN = "NON_MATCHED_PRODUCT_IDENTITY_FORBIDDEN"
    REQUEST_SHAPE_INVALID = "REQUEST_SHAPE_INVALID"


@dataclass(frozen=True, slots=True)
class PreflightCurrentnessToken:
    """Pinned context plus the currentness facts observed under the caller's lock.

    ``prescription-version-v1.md`` fixes currentness as equality with
    ``prescription.active_version_id``; there is no separate ``is_current`` column to read.
    ``ownership_verified`` records that the caller already completed the
    ``prescription_version_medication -> prescription_version -> prescription -> profile_id``
    check.  This kernel never decides ownership itself.
    """

    prescription_id: str
    pinned_prescription_version_id: str
    observed_active_prescription_version_id: str
    pinned_runtime_release_bundle_id: str
    observed_active_runtime_release_bundle_id: str
    ownership_verified: bool


@dataclass(frozen=True, slots=True)
class MedicationSnapshotRef:
    prescription_version_medication_id: str
    prescription_version_id: str
    display_order: int


@dataclass(frozen=True, slots=True)
class IdentificationSnapshotRef:
    """One medication's identification state.

    Product identity is carried as ``(code_system, canonical_code)`` and never as ``product_id``
    alone, per the #260 Product Identity principle recorded in ``rag_candidate.py``.
    """

    prescription_version_medication_id: str
    state: MedicationPreflightState
    prescription_version_id: str
    identification_id: str | None = None
    code_system: str | None = None
    canonical_code: str | None = None
    runtime_release_bundle_id: str | None = None


@dataclass(frozen=True, slots=True)
class MedicationIdentificationPreflightRequest:
    currentness: PreflightCurrentnessToken
    medications: tuple[MedicationSnapshotRef, ...]
    identifications: tuple[IdentificationSnapshotRef, ...]


@dataclass(frozen=True, slots=True)
class MedicationIdentificationPreflightOutcome:
    execution_status: PreflightExecutionStatus
    decision: PreflightDecision
    reason: PreflightReason
    manifest_projection_version: str = MANIFEST_PROJECTION_VERSION
    manifest_hash: str | None = None
    medication_count: int = 0
    matched_count: int = 0
    identification_reasons: tuple[PreflightReason, ...] = ()
    blocking_medication_ids: tuple[str, ...] = ()
    stale_signals: tuple[PreflightStaleSignal, ...] = ()
    validation_codes: tuple[PreflightValidationCode, ...] = ()


def evaluate_medication_identification_preflight(
    request: MedicationIdentificationPreflightRequest,
) -> MedicationIdentificationPreflightOutcome:
    """Decide whether the general Rule/Retrieval/Composer path may run.

    Never raises for rejected input: every failure ends as a typed fail-closed decision so a
    caller cannot mistake an exception path for permission to execute.
    """
    validation_codes = _validate_request(request)
    if validation_codes:
        return MedicationIdentificationPreflightOutcome(
            execution_status=PreflightExecutionStatus.VALIDATION_ERROR,
            decision=PreflightDecision.IDENTIFICATION_FALLBACK,
            reason=PreflightReason.REVIEW_REQUIRED,
            validation_codes=validation_codes,
        )

    manifest_hash = canonical_preflight_manifest_hash(request)
    states = {item.prescription_version_medication_id: item.state for item in request.identifications}
    matched_count = sum(1 for state in states.values() if state is MedicationPreflightState.MATCHED)
    counted = MedicationIdentificationPreflightOutcome(
        execution_status=PreflightExecutionStatus.EVALUATED,
        decision=PreflightDecision.PASS,
        reason=PreflightReason.MATCHED,
        manifest_hash=manifest_hash,
        medication_count=len(request.medications),
        matched_count=matched_count,
    )

    stale_signals = _stale_signals(request)
    if stale_signals:
        # A pinned context that is no longer current makes every per-medication state computed
        # on top of it untrustworthy, so staleness is reported ahead of identification gaps.
        # Both branches block general RAG equally; see the design doc for the open review item.
        return _replace_decision(
            counted,
            decision=PreflightDecision.STALE_FALLBACK,
            reason=PreflightReason.EXECUTION_CONTEXT_STALE,
            stale_signals=stale_signals,
        )

    blocking = tuple(
        sorted(
            medication_id for medication_id, state in states.items() if state is not MedicationPreflightState.MATCHED
        )
    )
    if not blocking:
        return counted

    present = {states[medication_id] for medication_id in blocking}
    reasons = tuple(PreflightReason(state.value) for state in MedicationPreflightState if state in present)
    return _replace_decision(
        counted,
        decision=PreflightDecision.IDENTIFICATION_FALLBACK,
        reason=reasons[0],
        identification_reasons=reasons,
        blocking_medication_ids=blocking,
    )


def canonical_preflight_manifest_hash(request: MedicationIdentificationPreflightRequest) -> str:
    """Return the order-independent SHA-256 identity of the pinned preflight input set.

    Observed active pointers and ``ownership_verified`` are excluded: the manifest identifies the
    pinned input set, and the same input set must hash identically whether or not it is stale.

    Precondition: the request already passed :func:`evaluate_medication_identification_preflight`
    structural validation, so every medication has exactly one identification snapshot.
    """
    states = {item.prescription_version_medication_id: item for item in request.identifications}
    medications = [
        {
            "prescription_version_medication_id": medication.prescription_version_medication_id,
            "display_order": medication.display_order,
            "state": states[medication.prescription_version_medication_id].state.value,
            "identification_id": states[medication.prescription_version_medication_id].identification_id,
            "code_system": states[medication.prescription_version_medication_id].code_system,
            "canonical_code": states[medication.prescription_version_medication_id].canonical_code,
        }
        for medication in sorted(
            request.medications,
            key=lambda item: (item.display_order, item.prescription_version_medication_id),
        )
    ]
    payload = {
        "projection_version": MANIFEST_PROJECTION_VERSION,
        "prescription_id": request.currentness.prescription_id,
        "prescription_version_id": request.currentness.pinned_prescription_version_id,
        "runtime_release_bundle_id": request.currentness.pinned_runtime_release_bundle_id,
        "medications": medications,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _replace_decision(
    outcome: MedicationIdentificationPreflightOutcome,
    *,
    decision: PreflightDecision,
    reason: PreflightReason,
    identification_reasons: tuple[PreflightReason, ...] = (),
    blocking_medication_ids: tuple[str, ...] = (),
    stale_signals: tuple[PreflightStaleSignal, ...] = (),
) -> MedicationIdentificationPreflightOutcome:
    return MedicationIdentificationPreflightOutcome(
        execution_status=outcome.execution_status,
        decision=decision,
        reason=reason,
        manifest_hash=outcome.manifest_hash,
        medication_count=outcome.medication_count,
        matched_count=outcome.matched_count,
        identification_reasons=identification_reasons,
        blocking_medication_ids=blocking_medication_ids,
        stale_signals=stale_signals,
    )


def _stale_signals(request: MedicationIdentificationPreflightRequest) -> tuple[PreflightStaleSignal, ...]:
    currentness = request.currentness
    signals: set[PreflightStaleSignal] = set()
    if currentness.observed_active_prescription_version_id != currentness.pinned_prescription_version_id:
        signals.add(PreflightStaleSignal.PRESCRIPTION_STALE)
    if any(
        item.prescription_version_id != currentness.pinned_prescription_version_id for item in request.identifications
    ):
        signals.add(PreflightStaleSignal.IDENTIFICATION_STALE)
    if currentness.observed_active_runtime_release_bundle_id != currentness.pinned_runtime_release_bundle_id or any(
        item.state is MedicationPreflightState.MATCHED
        and item.runtime_release_bundle_id != currentness.pinned_runtime_release_bundle_id
        for item in request.identifications
    ):
        signals.add(PreflightStaleSignal.RUNTIME_RELEASE_STALE)
    return tuple(signal for signal in PreflightStaleSignal if signal in signals)


def _validate_request(
    request: MedicationIdentificationPreflightRequest,
) -> tuple[PreflightValidationCode, ...]:
    if not _is_request_shaped(request):
        return (PreflightValidationCode.REQUEST_SHAPE_INVALID,)

    codes: set[PreflightValidationCode] = set()
    codes.update(_validate_currentness(request.currentness))
    codes.update(_validate_medications(request))
    codes.update(_validate_identifications(request))
    return tuple(code for code in PreflightValidationCode if code in codes)


def _is_request_shaped(request: MedicationIdentificationPreflightRequest) -> bool:
    return (
        isinstance(request, MedicationIdentificationPreflightRequest)
        and isinstance(request.currentness, PreflightCurrentnessToken)
        and isinstance(request.medications, tuple)
        and isinstance(request.identifications, tuple)
        and all(isinstance(item, MedicationSnapshotRef) for item in request.medications)
        and all(isinstance(item, IdentificationSnapshotRef) for item in request.identifications)
    )


def _validate_currentness(currentness: PreflightCurrentnessToken) -> set[PreflightValidationCode]:
    codes: set[PreflightValidationCode] = set()
    if currentness.ownership_verified is not True:
        codes.add(PreflightValidationCode.OWNERSHIP_NOT_VERIFIED)
    identifiers = (
        currentness.prescription_id,
        currentness.pinned_prescription_version_id,
        currentness.observed_active_prescription_version_id,
        currentness.pinned_runtime_release_bundle_id,
        currentness.observed_active_runtime_release_bundle_id,
    )
    if not all(_is_canonical_uuid(value) for value in identifiers):
        codes.add(PreflightValidationCode.IDENTIFIER_NOT_CANONICAL_UUID)
    return codes


def _validate_medications(
    request: MedicationIdentificationPreflightRequest,
) -> set[PreflightValidationCode]:
    medications = request.medications
    codes: set[PreflightValidationCode] = set()
    if not medications:
        codes.add(PreflightValidationCode.AT_LEAST_ONE_MEDICATION_REQUIRED)
        return codes

    medication_ids = [item.prescription_version_medication_id for item in medications]
    if len(set(medication_ids)) != len(medication_ids):
        codes.add(PreflightValidationCode.DUPLICATE_MEDICATION_ID)
    display_orders = [item.display_order for item in medications]
    if len(set(display_orders)) != len(display_orders):
        codes.add(PreflightValidationCode.DUPLICATE_DISPLAY_ORDER)
    if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in display_orders):
        codes.add(PreflightValidationCode.DISPLAY_ORDER_NOT_POSITIVE)
    if not all(_is_canonical_uuid(value) for value in medication_ids):
        codes.add(PreflightValidationCode.IDENTIFIER_NOT_CANONICAL_UUID)
    if any(item.prescription_version_id != request.currentness.pinned_prescription_version_id for item in medications):
        codes.add(PreflightValidationCode.MEDICATION_VERSION_MISMATCH)
    return codes


def _validate_identifications(
    request: MedicationIdentificationPreflightRequest,
) -> set[PreflightValidationCode]:
    identifications = request.identifications
    codes: set[PreflightValidationCode] = set()
    identification_medication_ids = [item.prescription_version_medication_id for item in identifications]
    if len(set(identification_medication_ids)) != len(identification_medication_ids):
        codes.add(PreflightValidationCode.DUPLICATE_IDENTIFICATION_MEDICATION_ID)
    identification_ids = [item.identification_id for item in identifications if item.identification_id is not None]
    if len(set(identification_ids)) != len(identification_ids):
        codes.add(PreflightValidationCode.DUPLICATE_IDENTIFICATION_ID)
    if set(identification_medication_ids) != {
        item.prescription_version_medication_id for item in request.medications
    } or len(identification_medication_ids) != len(request.medications):
        codes.add(PreflightValidationCode.IDENTIFICATION_MEDICATION_SET_MISMATCH)
    for item in identifications:
        codes.update(_validate_identification_item(item))
    return codes


def _validate_identification_item(item: IdentificationSnapshotRef) -> set[PreflightValidationCode]:
    codes: set[PreflightValidationCode] = set()
    if not isinstance(item.state, MedicationPreflightState):
        codes.add(PreflightValidationCode.UNKNOWN_PREFLIGHT_STATE)
        return codes

    required = (item.prescription_version_medication_id, item.prescription_version_id)
    optional_ids = tuple(
        value for value in (item.identification_id, item.runtime_release_bundle_id) if value is not None
    )
    if not all(_is_canonical_uuid(value) for value in required + optional_ids):
        codes.add(PreflightValidationCode.IDENTIFIER_NOT_CANONICAL_UUID)

    identity = (item.code_system, item.canonical_code)
    if item.state is MedicationPreflightState.MATCHED:
        if (
            item.identification_id is None
            or item.runtime_release_bundle_id is None
            or not all(_is_nonblank_nfc(value) for value in identity)
        ):
            codes.add(PreflightValidationCode.MATCHED_PRODUCT_IDENTITY_REQUIRED)
    elif item.identification_id is not None or any(value is not None for value in identity):
        codes.add(PreflightValidationCode.NON_MATCHED_PRODUCT_IDENTITY_FORBIDDEN)

    if any(isinstance(value, str) and not _is_nfc(value) for value in identity):
        codes.add(PreflightValidationCode.TEXT_NOT_NFC)
    return codes


def _is_canonical_uuid(value: object) -> bool:
    return isinstance(value, str) and _CANONICAL_UUID_RE.fullmatch(value) is not None


def _is_nfc(value: str) -> bool:
    return unicodedata.is_normalized("NFC", value)


def _is_nonblank_nfc(value: object) -> bool:
    return isinstance(value, str) and value.strip() != "" and _is_nfc(value)


def preflight_state_from_mapping(payload: Mapping[str, object]) -> MedicationPreflightState | str:
    """Read a raw ``state`` value without raising, so an unknown enum ends as a typed decision.

    Fixture and RAG-12-API loaders use this instead of ``MedicationPreflightState(value)``: an
    unrecognised string is returned as-is and rejected by the kernel as
    ``UNKNOWN_PREFLIGHT_STATE``.
    """
    raw = payload.get("state")
    if isinstance(raw, str):
        try:
            return MedicationPreflightState(raw)
        except ValueError:
            return raw
    return ""


def medication_ids_in_manifest_order(medications: Sequence[MedicationSnapshotRef]) -> tuple[str, ...]:
    """Return medication ids in the canonical manifest order, for trace and evidence records."""
    return tuple(
        item.prescription_version_medication_id
        for item in sorted(medications, key=lambda item: (item.display_order, item.prescription_version_medication_id))
    )
