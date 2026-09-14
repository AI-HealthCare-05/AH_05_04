"""Pure Citation Authorization request construction and receipt verification.

External approval and persistence are deliberately outside this module. A pass
here proves only that an observed receipt exactly binds the detached request;
it is not patient-visible release authority.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from ai_worker.tasks.rag.claim_citation_validator import (
    InteractionRuleEvidenceRef,
    KnowledgeChunkEvidenceRef,
    LifestyleGuidelineEvidenceRef,
    SafetyPolicyEvidenceRef,
    SourceExecutionProvenance,
    SourceMemberKind,
    ValidatedCitationSelection,
    validate_claim_citations,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
AUTHORIZATION_SELECTION_PROJECTION_VERSION = "rag-citation-authorization-selection-v1"
AUTHORIZATION_REQUEST_PROJECTION_VERSION = "rag-citation-authorization-request-v1"


class GuardDecision(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class GuardOperation(StrEnum):
    REQUEST = "REQUEST"
    CITATION_AUTHORIZATION = "CITATION_AUTHORIZATION"


class RuntimeEnvironment(StrEnum):
    LOCAL = "LOCAL"
    TEST = "TEST"
    CLOSED_DEMO = "CLOSED_DEMO"
    PRODUCTION = "PRODUCTION"


class UsePurpose(StrEnum):
    PATIENT_CITATION = "PATIENT_CITATION"
    RETRIEVAL = "RETRIEVAL"


class AuthorizationBuildDecision(StrEnum):
    BUILT = "BUILT"
    REJECTED = "REJECTED"


class AuthorizationVerificationDecision(StrEnum):
    AUTHORIZED = "AUTHORIZED"
    REJECTED = "REJECTED"


class AuthorizationReason(StrEnum):
    VALIDATED_SELECTION_INVALID = "VALIDATED_SELECTION_INVALID"
    RUNTIME_BINDING_INVALID = "RUNTIME_BINDING_INVALID"
    ORIGIN_REQUEST_MISMATCH = "ORIGIN_REQUEST_MISMATCH"
    AUTHORIZATION_SELECTION_INVALID = "AUTHORIZATION_SELECTION_INVALID"
    AUTHORIZATION_SELECTION_REQUIRED = "AUTHORIZATION_SELECTION_REQUIRED"
    AUTHORIZATION_REQUEST_INVALID = "AUTHORIZATION_REQUEST_INVALID"
    RECEIPT_INVALID = "RECEIPT_INVALID"
    RECEIPT_BINDING_MISMATCH = "RECEIPT_BINDING_MISMATCH"
    SELECTION_RECEIPT_MISMATCH = "SELECTION_RECEIPT_MISMATCH"
    SELECTION_NOT_AUTHORIZED = "SELECTION_NOT_AUTHORIZED"


@dataclass(frozen=True, slots=True)
class RuntimeAuthorizationBinding:
    environment: RuntimeEnvironment
    bundle_id: str
    bundle_manifest_hash: str
    request_scope_codes: tuple[str, ...]
    scope_manifest_hash: str


@dataclass(frozen=True, slots=True)
class OriginRequestGuardBinding:
    guard_ref: ImmutableArtifactRef
    decision: GuardDecision
    operation: GuardOperation
    environment: RuntimeEnvironment
    bundle_id: str
    bundle_manifest_hash: str
    request_scope_codes: tuple[str, ...]
    scope_manifest_hash: str


@dataclass(frozen=True, slots=True)
class CitationAuthorizationSelectionEntry:
    source_code: str
    source_version: str
    member_kind: SourceMemberKind
    endpoint_code: str | None
    operation_code: str | None
    artifact_code: str | None
    artifact_version: str | None


@dataclass(frozen=True, slots=True)
class CitationAuthorizationRequest:
    origin_request_guard: OriginRequestGuardBinding
    runtime_binding: RuntimeAuthorizationBinding
    validated_selection_sha256: str
    selection_manifest: tuple[CitationAuthorizationSelectionEntry, ...]
    selection_manifest_sha256: str
    request_sha256: str


@dataclass(frozen=True, slots=True)
class CitationAuthorizationBuildOutcome:
    decision: AuthorizationBuildDecision
    reasons: tuple[AuthorizationReason, ...]
    request: CitationAuthorizationRequest | None


@dataclass(frozen=True, slots=True)
class CitationAuthorizationSelectionReceipt:
    selection: CitationAuthorizationSelectionEntry
    source_decision_ref: ImmutableArtifactRef
    member_decision_ref: ImmutableArtifactRef
    selected_for_operation: bool
    purpose: UsePurpose
    source_decision: GuardDecision
    member_decision: GuardDecision


@dataclass(frozen=True, slots=True)
class CitationAuthorizationReceipt:
    receipt_ref: ImmutableArtifactRef
    request_sha256: str
    origin_guard_ref: ImmutableArtifactRef
    origin_decision: GuardDecision
    operation: GuardOperation
    environment: RuntimeEnvironment
    bundle_id: str
    bundle_manifest_hash: str
    request_scope_codes: tuple[str, ...]
    scope_manifest_hash: str
    validated_selection_sha256: str
    selection_manifest_sha256: str
    selections: tuple[CitationAuthorizationSelectionReceipt, ...]


@dataclass(frozen=True, slots=True)
class CitationAuthorizationVerificationOutcome:
    decision: AuthorizationVerificationDecision
    reasons: tuple[AuthorizationReason, ...]
    receipt: CitationAuthorizationReceipt | None


def _canonical_json_bytes(value: object) -> bytes:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return unicodedata.normalize("NFC", serialized).encode("utf-8")


def _is_nfc_text(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and unicodedata.is_normalized("NFC", value)


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _artifact_is_valid(value: object) -> bool:
    return (
        type(value) is ImmutableArtifactRef
        and _is_nfc_text(value.artifact_code)
        and _is_nfc_text(value.version)
        and _is_sha256(value.content_sha256)
    )


def _artifact_payload(value: ImmutableArtifactRef) -> dict[str, str]:
    return {
        "artifact_code": value.artifact_code,
        "version": value.version,
        "content_sha256": value.content_sha256,
    }


def canonical_scope_manifest_hash(scope_codes: tuple[str, ...]) -> str:
    return hashlib.sha256(_canonical_json_bytes(list(scope_codes))).hexdigest()


def _scopes_are_canonical(scope_codes: object) -> bool:
    return (
        type(scope_codes) is tuple
        and bool(scope_codes)
        and all(_is_nfc_text(code) for code in scope_codes)
        and len(scope_codes) == len(set(scope_codes))
        and scope_codes == tuple(sorted(scope_codes, key=lambda code: code.encode("utf-8")))
    )


def _runtime_binding_is_valid(value: object) -> bool:
    return (
        type(value) is RuntimeAuthorizationBinding
        and type(value.environment) is RuntimeEnvironment
        and _is_nfc_text(value.bundle_id)
        and _is_sha256(value.bundle_manifest_hash)
        and _scopes_are_canonical(value.request_scope_codes)
        and _is_sha256(value.scope_manifest_hash)
        and value.scope_manifest_hash == canonical_scope_manifest_hash(value.request_scope_codes)
    )


def _origin_matches_runtime(value: object, runtime: RuntimeAuthorizationBinding) -> bool:
    return (
        type(value) is OriginRequestGuardBinding
        and _artifact_is_valid(value.guard_ref)
        and value.decision is GuardDecision.PASS
        and value.operation is GuardOperation.REQUEST
        and value.environment is runtime.environment
        and value.bundle_id == runtime.bundle_id
        and value.bundle_manifest_hash == runtime.bundle_manifest_hash
        and value.request_scope_codes == runtime.request_scope_codes
        and value.scope_manifest_hash == runtime.scope_manifest_hash
    )


def _selection_payload(value: CitationAuthorizationSelectionEntry) -> dict[str, object]:
    return {
        "source_code": value.source_code,
        "source_version": value.source_version,
        "member_kind": value.member_kind.value,
        "endpoint_code": value.endpoint_code,
        "operation_code": value.operation_code,
        "artifact_code": value.artifact_code,
        "artifact_version": value.artifact_version,
    }


def _selection_is_valid(value: object) -> bool:
    if type(value) is not CitationAuthorizationSelectionEntry:
        return False
    endpoint_member = (
        value.member_kind is SourceMemberKind.ENDPOINT_OPERATION
        and _is_nfc_text(value.endpoint_code)
        and _is_nfc_text(value.operation_code)
        and value.artifact_code is None
        and value.artifact_version is None
    )
    artifact_member = (
        value.member_kind is SourceMemberKind.ARTIFACT_MEMBER
        and value.endpoint_code is None
        and value.operation_code is None
        and _is_nfc_text(value.artifact_code)
        and _is_nfc_text(value.artifact_version)
    )
    return (
        _is_nfc_text(value.source_code) and _is_nfc_text(value.source_version) and (endpoint_member or artifact_member)
    )


def _entry_from_binding(binding: SourceExecutionProvenance) -> CitationAuthorizationSelectionEntry:
    return CitationAuthorizationSelectionEntry(
        source_code=binding.source_code,
        source_version=binding.source_version,
        member_kind=binding.member_kind,
        endpoint_code=binding.endpoint_code,
        operation_code=binding.operation_code,
        artifact_code=binding.artifact_code,
        artifact_version=binding.artifact_version,
    )


def _authorization_entries(selection: ValidatedCitationSelection) -> tuple[CitationAuthorizationSelectionEntry, ...]:
    entries: dict[bytes, CitationAuthorizationSelectionEntry] = {}
    source_backed_types = (
        KnowledgeChunkEvidenceRef,
        InteractionRuleEvidenceRef,
        LifestyleGuidelineEvidenceRef,
        SafetyPolicyEvidenceRef,
    )
    for citation in selection.candidate_set.citations:
        evidence = citation.evidence_ref
        if isinstance(evidence, source_backed_types):
            entry = _entry_from_binding(evidence.execution_provenance)
            entries[_canonical_json_bytes(_selection_payload(entry))] = entry
    return tuple(entries[key] for key in sorted(entries))


def _selection_manifest_hash(entries: tuple[CitationAuthorizationSelectionEntry, ...]) -> str:
    payload = {
        "projection_version": AUTHORIZATION_SELECTION_PROJECTION_VERSION,
        "entries": [_selection_payload(entry) for entry in entries],
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _runtime_payload(value: RuntimeAuthorizationBinding) -> dict[str, object]:
    return {
        "environment": value.environment.value,
        "bundle_id": value.bundle_id,
        "bundle_manifest_hash": value.bundle_manifest_hash,
        "request_scope_codes": list(value.request_scope_codes),
        "scope_manifest_hash": value.scope_manifest_hash,
    }


def _origin_payload(value: OriginRequestGuardBinding) -> dict[str, object]:
    return {
        "guard_ref": _artifact_payload(value.guard_ref),
        "decision": value.decision.value,
        "operation": value.operation.value,
        **_runtime_payload(
            RuntimeAuthorizationBinding(
                environment=value.environment,
                bundle_id=value.bundle_id,
                bundle_manifest_hash=value.bundle_manifest_hash,
                request_scope_codes=value.request_scope_codes,
                scope_manifest_hash=value.scope_manifest_hash,
            )
        ),
    }


def _request_hash(
    origin: OriginRequestGuardBinding,
    runtime: RuntimeAuthorizationBinding,
    validated_selection_sha256: str,
    entries: tuple[CitationAuthorizationSelectionEntry, ...],
    selection_manifest_sha256: str,
) -> str:
    payload = {
        "projection_version": AUTHORIZATION_REQUEST_PROJECTION_VERSION,
        "origin_request_guard": _origin_payload(origin),
        "runtime_binding": _runtime_payload(runtime),
        "validated_selection_sha256": validated_selection_sha256,
        "selection_manifest": [_selection_payload(entry) for entry in entries],
        "selection_manifest_sha256": selection_manifest_sha256,
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def build_citation_authorization_request(
    validated_selection: ValidatedCitationSelection,
    runtime_binding: RuntimeAuthorizationBinding,
    origin_request_guard: OriginRequestGuardBinding,
) -> CitationAuthorizationBuildOutcome:
    """Build an immutable request for a later Worker-owned I/O boundary."""

    try:
        replayed_validation = validate_claim_citations(
            validated_selection.candidate_set,
            validated_selection.support_receipts,
        )
        selection_valid = (
            type(validated_selection) is ValidatedCitationSelection
            and _is_sha256(validated_selection.selection_sha256)
            and replayed_validation.validated_selection == validated_selection
        )
    except (AttributeError, StopIteration, TypeError, ValueError):
        selection_valid = False
    if not selection_valid:
        return CitationAuthorizationBuildOutcome(
            AuthorizationBuildDecision.REJECTED,
            (AuthorizationReason.VALIDATED_SELECTION_INVALID,),
            None,
        )
    if not _runtime_binding_is_valid(runtime_binding):
        return CitationAuthorizationBuildOutcome(
            AuthorizationBuildDecision.REJECTED,
            (AuthorizationReason.RUNTIME_BINDING_INVALID,),
            None,
        )
    if not _origin_matches_runtime(origin_request_guard, runtime_binding):
        return CitationAuthorizationBuildOutcome(
            AuthorizationBuildDecision.REJECTED,
            (AuthorizationReason.ORIGIN_REQUEST_MISMATCH,),
            None,
        )
    entries = _authorization_entries(validated_selection)
    if not entries:
        return CitationAuthorizationBuildOutcome(
            AuthorizationBuildDecision.REJECTED,
            (AuthorizationReason.AUTHORIZATION_SELECTION_REQUIRED,),
            None,
        )
    if not all(_selection_is_valid(entry) for entry in entries):
        return CitationAuthorizationBuildOutcome(
            AuthorizationBuildDecision.REJECTED,
            (AuthorizationReason.AUTHORIZATION_SELECTION_INVALID,),
            None,
        )
    selection_hash = _selection_manifest_hash(entries)
    request_hash = _request_hash(
        origin_request_guard,
        runtime_binding,
        validated_selection.selection_sha256,
        entries,
        selection_hash,
    )
    return CitationAuthorizationBuildOutcome(
        AuthorizationBuildDecision.BUILT,
        (),
        CitationAuthorizationRequest(
            origin_request_guard=origin_request_guard,
            runtime_binding=runtime_binding,
            validated_selection_sha256=validated_selection.selection_sha256,
            selection_manifest=entries,
            selection_manifest_sha256=selection_hash,
            request_sha256=request_hash,
        ),
    )


def _receipt_shape_is_valid(value: object) -> bool:
    return (
        type(value) is CitationAuthorizationReceipt
        and _artifact_is_valid(value.receipt_ref)
        and _is_sha256(value.request_sha256)
        and _artifact_is_valid(value.origin_guard_ref)
        and type(value.origin_decision) is GuardDecision
        and type(value.operation) is GuardOperation
        and type(value.environment) is RuntimeEnvironment
        and _is_nfc_text(value.bundle_id)
        and _is_sha256(value.bundle_manifest_hash)
        and _scopes_are_canonical(value.request_scope_codes)
        and _is_sha256(value.scope_manifest_hash)
        and _is_sha256(value.validated_selection_sha256)
        and _is_sha256(value.selection_manifest_sha256)
        and type(value.selections) is tuple
        and all(
            type(item) is CitationAuthorizationSelectionReceipt
            and _selection_is_valid(item.selection)
            and _artifact_is_valid(item.source_decision_ref)
            and _artifact_is_valid(item.member_decision_ref)
            and type(item.selected_for_operation) is bool
            and type(item.purpose) is UsePurpose
            and type(item.source_decision) is GuardDecision
            and type(item.member_decision) is GuardDecision
            for item in value.selections
        )
    )


def _request_is_valid(value: object) -> bool:
    if (
        type(value) is not CitationAuthorizationRequest
        or not _runtime_binding_is_valid(value.runtime_binding)
        or not _origin_matches_runtime(value.origin_request_guard, value.runtime_binding)
        or not _is_sha256(value.validated_selection_sha256)
        or type(value.selection_manifest) is not tuple
        or not value.selection_manifest
        or not all(_selection_is_valid(entry) for entry in value.selection_manifest)
        or len(value.selection_manifest) != len(set(value.selection_manifest))
        or tuple(sorted(value.selection_manifest, key=lambda entry: _canonical_json_bytes(_selection_payload(entry))))
        != value.selection_manifest
        or not _is_sha256(value.selection_manifest_sha256)
        or value.selection_manifest_sha256 != _selection_manifest_hash(value.selection_manifest)
        or not _is_sha256(value.request_sha256)
    ):
        return False
    return value.request_sha256 == _request_hash(
        value.origin_request_guard,
        value.runtime_binding,
        value.validated_selection_sha256,
        value.selection_manifest,
        value.selection_manifest_sha256,
    )


def verify_citation_authorization_receipt(
    request: CitationAuthorizationRequest,
    receipt: object,
) -> CitationAuthorizationVerificationOutcome:
    """Fail closed unless the complete observed receipt exact-matches the request."""

    if not _request_is_valid(request):
        return CitationAuthorizationVerificationOutcome(
            AuthorizationVerificationDecision.REJECTED,
            (AuthorizationReason.AUTHORIZATION_REQUEST_INVALID,),
            None,
        )
    if not _receipt_shape_is_valid(receipt):
        return CitationAuthorizationVerificationOutcome(
            AuthorizationVerificationDecision.REJECTED,
            (AuthorizationReason.RECEIPT_INVALID,),
            None,
        )
    receipt = cast(CitationAuthorizationReceipt, receipt)
    binding_matches = (
        receipt.request_sha256 == request.request_sha256
        and receipt.origin_guard_ref == request.origin_request_guard.guard_ref
        and receipt.origin_decision is GuardDecision.PASS
        and receipt.operation is GuardOperation.CITATION_AUTHORIZATION
        and receipt.environment is request.runtime_binding.environment
        and receipt.bundle_id == request.runtime_binding.bundle_id
        and receipt.bundle_manifest_hash == request.runtime_binding.bundle_manifest_hash
        and receipt.request_scope_codes == request.runtime_binding.request_scope_codes
        and receipt.scope_manifest_hash == request.runtime_binding.scope_manifest_hash
        and receipt.validated_selection_sha256 == request.validated_selection_sha256
        and receipt.selection_manifest_sha256 == request.selection_manifest_sha256
    )
    if not binding_matches:
        return CitationAuthorizationVerificationOutcome(
            AuthorizationVerificationDecision.REJECTED,
            (AuthorizationReason.RECEIPT_BINDING_MISMATCH,),
            None,
        )
    if tuple(item.selection for item in receipt.selections) != request.selection_manifest:
        return CitationAuthorizationVerificationOutcome(
            AuthorizationVerificationDecision.REJECTED,
            (AuthorizationReason.SELECTION_RECEIPT_MISMATCH,),
            None,
        )
    if any(
        not item.selected_for_operation
        or item.purpose is not UsePurpose.PATIENT_CITATION
        or item.source_decision is not GuardDecision.PASS
        or item.member_decision is not GuardDecision.PASS
        for item in receipt.selections
    ):
        return CitationAuthorizationVerificationOutcome(
            AuthorizationVerificationDecision.REJECTED,
            (AuthorizationReason.SELECTION_NOT_AUTHORIZED,),
            None,
        )
    return CitationAuthorizationVerificationOutcome(
        AuthorizationVerificationDecision.AUTHORIZED,
        (),
        receipt,
    )
