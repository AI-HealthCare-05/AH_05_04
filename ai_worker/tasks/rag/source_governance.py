"""Synthetic-only RAG source-governance evaluation with no runtime authority."""

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class SyntheticGuardDecision(StrEnum):
    """Local synthetic verdict; this is not a Runtime Guard wire contract."""

    PASS = "PASS"
    FAIL = "FAIL"


class SyntheticGuardOperation(StrEnum):
    REQUEST = "REQUEST"
    CITATION_AUTHORIZATION = "CITATION_AUTHORIZATION"
    EVALUATION_CANDIDATE = "EVALUATION_CANDIDATE"
    EVALUATION_REQUEST = "EVALUATION_REQUEST"
    PLANNED_ACTIVATION = "PLANNED_ACTIVATION"
    EMERGENCY_ROLLBACK = "EMERGENCY_ROLLBACK"
    RESUME = "RESUME"


class SyntheticUsePurpose(StrEnum):
    PRODUCT_IDENTIFICATION = "PRODUCT_IDENTIFICATION"
    SAFETY_ROUTING = "SAFETY_ROUTING"
    RULE_DERIVATION = "RULE_DERIVATION"
    RETRIEVAL = "RETRIEVAL"
    PATIENT_CITATION = "PATIENT_CITATION"


class SyntheticGuardManifestEntryKind(StrEnum):
    RELEASE_SOURCE = "RELEASE_SOURCE"
    SNAPSHOT_MEMBER = "SNAPSHOT_MEMBER"


class SyntheticEnvironment(StrEnum):
    LOCAL = "LOCAL"
    TEST = "TEST"
    CLOSED_DEMO = "CLOSED_DEMO"
    PRODUCTION = "PRODUCTION"


class SyntheticGovernanceReason(StrEnum):
    """Observed synthetic evidence failures, separate from the guard verdict."""

    SOURCE_NOT_ACTIVE = "SOURCE_NOT_ACTIVE"
    ENDPOINT_NOT_ELIGIBLE = "ENDPOINT_NOT_ELIGIBLE"
    OPERATION_NOT_ELIGIBLE = "OPERATION_NOT_ELIGIBLE"
    APPROVAL_NOT_EFFECTIVE = "APPROVAL_NOT_EFFECTIVE"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    LICENSE_NOT_APPROVED = "LICENSE_NOT_APPROVED"
    CLINICAL_SCOPE_NOT_ALLOWED = "CLINICAL_SCOPE_NOT_ALLOWED"
    SNAPSHOT_FRESHNESS_STALE = "SNAPSHOT_FRESHNESS_STALE"
    REVOCATION_UNRESOLVED = "REVOCATION_UNRESOLVED"
    SNAPSHOT_INCOMPLETE = "SNAPSHOT_INCOMPLETE"
    SCHEMA_DRIFT = "SCHEMA_DRIFT"
    BUNDLE_MEMBER_MISMATCH = "BUNDLE_MEMBER_MISMATCH"
    PURPOSE_MISMATCH = "PURPOSE_MISMATCH"
    ENVIRONMENT_MISMATCH = "ENVIRONMENT_MISMATCH"
    IMMUTABLE_REFERENCE_INVALID = "IMMUTABLE_REFERENCE_INVALID"
    IMMUTABLE_REFERENCE_MISMATCH = "IMMUTABLE_REFERENCE_MISMATCH"
    SELECTION_NOT_TARGET_SUBSET = "SELECTION_NOT_TARGET_SUBSET"
    GOVERNANCE_REVISION_MISMATCH = "GOVERNANCE_REVISION_MISMATCH"
    SAFETY_EPOCH_MISMATCH = "SAFETY_EPOCH_MISMATCH"
    SELECTION_REQUIRED = "SELECTION_REQUIRED"
    SELECTION_FORBIDDEN = "SELECTION_FORBIDDEN"
    RUNTIME_ENVIRONMENT_NOT_ACTIVE = "RUNTIME_ENVIRONMENT_NOT_ACTIVE"
    ACTIVE_BUNDLE_ID_MISMATCH = "ACTIVE_BUNDLE_ID_MISMATCH"
    ACTIVE_BUNDLE_MANIFEST_MISMATCH = "ACTIVE_BUNDLE_MANIFEST_MISMATCH"
    REQUEST_SCOPE_INVALID = "REQUEST_SCOPE_INVALID"
    CITATION_PURPOSE_REQUIRED = "CITATION_PURPOSE_REQUIRED"
    CITATION_ORIGIN_REQUEST_MISMATCH = "CITATION_ORIGIN_REQUEST_MISMATCH"
    SELECTION_PURPOSE_MISMATCH = "SELECTION_PURPOSE_MISMATCH"
    TARGET_SOURCE_MEMBER_RELATION_INVALID = "TARGET_SOURCE_MEMBER_RELATION_INVALID"
    SELECTION_SOURCE_MEMBER_RELATION_INVALID = "SELECTION_SOURCE_MEMBER_RELATION_INVALID"
    OPERATION_CONTEXT_NOT_MODELED = "OPERATION_CONTEXT_NOT_MODELED"
    CANONICAL_MANIFEST_ENTRY_INVALID = "CANONICAL_MANIFEST_ENTRY_INVALID"
    TARGET_RELEASE_SOURCE_MANIFEST_MISMATCH = "TARGET_RELEASE_SOURCE_MANIFEST_MISMATCH"
    TARGET_SNAPSHOT_MEMBER_MANIFEST_MISMATCH = "TARGET_SNAPSHOT_MEMBER_MANIFEST_MISMATCH"
    SELECTION_RELEASE_SOURCE_MANIFEST_MISMATCH = "SELECTION_RELEASE_SOURCE_MANIFEST_MISMATCH"
    SELECTION_SNAPSHOT_MEMBER_MANIFEST_MISMATCH = "SELECTION_SNAPSHOT_MEMBER_MANIFEST_MISMATCH"


@dataclass(frozen=True, slots=True)
class SyntheticImmutableReference:
    """Local immutable version/hash evidence used only by synthetic tests."""

    reference_code: str
    version: str
    sha256: str


@dataclass(frozen=True, slots=True)
class SyntheticGuardManifestEntry:
    """Stable Source Manifest union key used to recompute Guard manifests."""

    member_kind: SyntheticGuardManifestEntryKind
    source_code: str
    endpoint_code: str | None
    operation_code: str | None
    artifact_code: str | None
    artifact_version: str | None
    content_sha256: str
    source_manifest_member_hash: str | None
    source_version: str
    canonicalization_spec_version: str
    canonical_checksum: str
    purpose_code: SyntheticUsePurpose
    approval_version: str
    scope_policy_hash: str
    freshness_policy_hash: str
    bundle_build_source_verification_stable_key: str


@dataclass(frozen=True, slots=True)
class SyntheticOriginGuardBinding:
    """Immutable REQUEST guard evidence required by citation authorization."""

    decision: SyntheticGuardDecision
    operation: SyntheticGuardOperation
    bundle_id: str
    bundle_manifest_hash: str
    environment: SyntheticEnvironment
    request_scope_codes: tuple[str, ...]
    scope_manifest_hash: str


@dataclass(frozen=True, slots=True)
class SyntheticSourceGovernanceFacts:
    """Complete local synthetic observations; not an external or runtime DTO."""

    source_active: bool
    endpoint_verified: bool
    endpoint_runtime_enabled: bool
    endpoint_acquisition_approved: bool
    operation_runtime_enabled: bool
    operation_acquisition_approved: bool
    approval_effective: bool
    approval_expired: bool
    license_approved: bool
    clinical_scope_allowed: bool
    snapshot_freshness_current: bool
    revocation_unresolved: bool
    snapshot_complete: bool
    schema_matches: bool
    bundle_member_matches: bool
    operation: SyntheticGuardOperation
    expected_purpose: SyntheticUsePurpose
    observed_purpose: SyntheticUsePurpose
    expected_environment: SyntheticEnvironment
    observed_environment: SyntheticEnvironment
    expected_immutable_references: tuple[SyntheticImmutableReference, ...]
    observed_immutable_references: tuple[SyntheticImmutableReference, ...]
    guard_target_manifest_spec_version: str
    target_manifest_entries: tuple[SyntheticGuardManifestEntry, ...]
    selection_manifest_entries: tuple[SyntheticGuardManifestEntry, ...]
    expected_target_release_source_count: int
    expected_target_release_source_manifest_hash: str
    expected_target_snapshot_member_count: int
    expected_target_snapshot_member_manifest_hash: str
    expected_selection_release_source_count: int | None
    expected_selection_release_source_manifest_hash: str | None
    expected_selection_snapshot_member_count: int | None
    expected_selection_snapshot_member_manifest_hash: str | None
    expected_governance_revision: int
    observed_governance_revision: int
    expected_safety_epoch: int
    observed_safety_epoch: int
    runtime_environment_active: bool
    target_bundle_id: str
    target_bundle_manifest_hash: str
    requested_bundle_id: str | None
    requested_bundle_manifest_hash: str | None
    active_bundle_id: str | None
    active_bundle_manifest_hash: str | None
    request_scope_codes: tuple[str, ...] | None
    request_scope_manifest_hash: str | None
    origin_request_guard: SyntheticOriginGuardBinding | None


@dataclass(frozen=True, slots=True)
class SyntheticSourceGovernanceEvaluation:
    """Synthetic verdict plus independently observable failure reasons."""

    decision: SyntheticGuardDecision
    observation_reasons: tuple[SyntheticGovernanceReason, ...]


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _references_are_valid(references: tuple[SyntheticImmutableReference, ...]) -> bool:
    reference_codes = [reference.reference_code for reference in references]
    return (
        bool(references)
        and len(reference_codes) == len(set(reference_codes))
        and all(
            reference.reference_code and reference.version and _is_sha256(reference.sha256) for reference in references
        )
    )


def _manifest_entry_payload(entry: SyntheticGuardManifestEntry) -> dict[str, str | None]:
    return {
        "member_kind": str(entry.member_kind),
        "source_code": entry.source_code,
        "endpoint_code": entry.endpoint_code,
        "operation_code": entry.operation_code,
        "artifact_code": entry.artifact_code,
        "artifact_version": entry.artifact_version,
        "content_sha256": entry.content_sha256,
        "source_version": entry.source_version,
        "canonicalization_spec_version": entry.canonicalization_spec_version,
        "canonical_checksum": entry.canonical_checksum,
        "purpose_code": entry.purpose_code.value,
        "approval_version": entry.approval_version,
        "scope_policy_hash": entry.scope_policy_hash,
        "freshness_policy_hash": entry.freshness_policy_hash,
        "bundle_build_source_verification_stable_key": entry.bundle_build_source_verification_stable_key,
    }


def _canonical_json_bytes(value: object) -> bytes:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return unicodedata.normalize("NFC", serialized).encode("utf-8")


def _release_source_payload(entry: SyntheticGuardManifestEntry) -> dict[str, str | None]:
    return {
        "source_code": entry.source_code,
        "source_version": entry.source_version,
        "purpose_code": entry.purpose_code.value,
        "approval_version": entry.approval_version,
        "scope_policy_hash": entry.scope_policy_hash,
        "freshness_policy_hash": entry.freshness_policy_hash,
        "bundle_build_source_verification_stable_key": entry.bundle_build_source_verification_stable_key,
        "source_manifest_member_hash": entry.source_manifest_member_hash,
    }


def _canonical_guard_manifest_hash(
    facts: SyntheticSourceGovernanceFacts,
    *,
    set_role: str,
    entry_kind: str,
    entries: tuple[SyntheticGuardManifestEntry, ...],
) -> str:
    if entry_kind == "RELEASE_SOURCE":
        payloads: list[dict[str, str | None]] = [_release_source_payload(entry) for entry in entries]
    else:
        payloads = [_manifest_entry_payload(entry) for entry in entries]
    payloads.sort(key=_canonical_json_bytes)
    envelope = {
        "guard_target_manifest_spec_version": facts.guard_target_manifest_spec_version,
        "target_bundle_manifest_hash": facts.target_bundle_manifest_hash,
        "set_role": set_role,
        "entry_kind": entry_kind,
        "entries": payloads,
    }
    return hashlib.sha256(_canonical_json_bytes(envelope)).hexdigest()


def _manifest_entry_null_combination_is_valid(entry: SyntheticGuardManifestEntry) -> bool:
    if entry.member_kind is SyntheticGuardManifestEntryKind.RELEASE_SOURCE:
        return _is_sha256(entry.source_manifest_member_hash or "") and all(
            value is None
            for value in (entry.endpoint_code, entry.operation_code, entry.artifact_code, entry.artifact_version)
        )
    if entry.member_kind is not SyntheticGuardManifestEntryKind.SNAPSHOT_MEMBER:
        return False
    if entry.source_manifest_member_hash is not None:
        return False
    endpoint_member = (
        bool(entry.endpoint_code)
        and bool(entry.operation_code)
        and entry.artifact_code is None
        and entry.artifact_version is None
    )
    artifact_member = (
        entry.endpoint_code is None
        and entry.operation_code is None
        and bool(entry.artifact_code)
        and bool(entry.artifact_version)
    )
    return endpoint_member or artifact_member


def _canonical_manifest_entries_are_valid(facts: SyntheticSourceGovernanceFacts) -> bool:
    def canonical_payload(entry: SyntheticGuardManifestEntry) -> dict[str, str | None]:
        return (
            _release_source_payload(entry)
            if entry.member_kind is SyntheticGuardManifestEntryKind.RELEASE_SOURCE
            else _manifest_entry_payload(entry)
        )

    target_payloads = [canonical_payload(entry) for entry in facts.target_manifest_entries]
    selection_payloads = [canonical_payload(entry) for entry in facts.selection_manifest_entries]
    all_entries = (*facts.target_manifest_entries, *facts.selection_manifest_entries)
    return (
        bool(facts.guard_target_manifest_spec_version)
        and len(target_payloads) == len({_canonical_json_bytes(payload) for payload in target_payloads})
        and len(selection_payloads) == len({_canonical_json_bytes(payload) for payload in selection_payloads})
        and all(
            _manifest_entry_null_combination_is_valid(entry)
            and entry.source_code
            and entry.source_version
            and entry.approval_version
            and _is_sha256(entry.content_sha256)
            and _is_sha256(entry.canonical_checksum)
            and _is_sha256(entry.scope_policy_hash)
            and _is_sha256(entry.freshness_policy_hash)
            for entry in all_entries
        )
    )


_SELECTION_REQUIRED_OPERATIONS = frozenset(
    {
        SyntheticGuardOperation.REQUEST,
        SyntheticGuardOperation.CITATION_AUTHORIZATION,
    }
)
_ACTIVE_BUNDLE_REQUIRED_OPERATIONS = frozenset(
    {
        SyntheticGuardOperation.REQUEST,
        SyntheticGuardOperation.CITATION_AUTHORIZATION,
    }
)
_MODELED_OPERATIONS = frozenset(
    {
        SyntheticGuardOperation.REQUEST,
        SyntheticGuardOperation.CITATION_AUTHORIZATION,
    }
)


def _selection_is_not_target_subset(facts: SyntheticSourceGovernanceFacts) -> bool:
    if facts.operation not in _SELECTION_REQUIRED_OPERATIONS or not facts.selection_manifest_entries:
        return False
    target_release_sources = {
        _canonical_json_bytes(_release_source_payload(entry))
        for entry in facts.target_manifest_entries
        if entry.member_kind is SyntheticGuardManifestEntryKind.RELEASE_SOURCE
    }
    target_snapshot_members = {
        _canonical_json_bytes(_manifest_entry_payload(entry))
        for entry in facts.target_manifest_entries
        if entry.member_kind is SyntheticGuardManifestEntryKind.SNAPSHOT_MEMBER
    }
    selection_release_sources = {
        _canonical_json_bytes(_release_source_payload(entry))
        for entry in facts.selection_manifest_entries
        if entry.member_kind is SyntheticGuardManifestEntryKind.RELEASE_SOURCE
    }
    selection_snapshot_members = {
        _canonical_json_bytes(_manifest_entry_payload(entry))
        for entry in facts.selection_manifest_entries
        if entry.member_kind is SyntheticGuardManifestEntryKind.SNAPSHOT_MEMBER
    }
    return not (
        selection_release_sources.issubset(target_release_sources)
        and selection_snapshot_members.issubset(target_snapshot_members)
    )


def _snapshot_members_have_release_sources(
    release_sources: tuple[SyntheticGuardManifestEntry, ...],
    snapshot_members: tuple[SyntheticGuardManifestEntry, ...],
) -> bool:
    def binding(entry: SyntheticGuardManifestEntry) -> bytes:
        return _canonical_json_bytes(
            {
                "source_code": entry.source_code,
                "source_version": entry.source_version,
                "purpose_code": entry.purpose_code.value,
                "approval_version": entry.approval_version,
                "scope_policy_hash": entry.scope_policy_hash,
                "freshness_policy_hash": entry.freshness_policy_hash,
                "bundle_build_source_verification_stable_key": (entry.bundle_build_source_verification_stable_key),
            }
        )

    release_source_bindings = {binding(entry) for entry in release_sources}
    return all(binding(entry) in release_source_bindings for entry in snapshot_members)


def _scope_codes_are_canonical(scope_codes: tuple[str, ...] | None) -> bool:
    return (
        scope_codes is not None
        and bool(scope_codes)
        and scope_codes
        == tuple(sorted(scope_codes, key=lambda code: unicodedata.normalize("NFC", code).encode("utf-8")))
        and len(scope_codes) == len(set(scope_codes))
        and all(code and code == unicodedata.normalize("NFC", code) for code in scope_codes)
    )


def _calculate_scope_manifest_hash(scope_codes: tuple[str, ...]) -> str:
    return hashlib.sha256(_canonical_json_bytes(list(scope_codes))).hexdigest()


def _evidence_checks(
    facts: SyntheticSourceGovernanceFacts,
) -> tuple[tuple[bool, SyntheticGovernanceReason], ...]:
    endpoint_eligible = (
        facts.endpoint_verified and facts.endpoint_runtime_enabled and facts.endpoint_acquisition_approved
    )
    operation_eligible = facts.operation_runtime_enabled and facts.operation_acquisition_approved
    references_valid = _references_are_valid(facts.expected_immutable_references) and _references_are_valid(
        facts.observed_immutable_references
    )
    selection_values = (
        facts.expected_selection_release_source_count,
        facts.expected_selection_release_source_manifest_hash,
        facts.expected_selection_snapshot_member_count,
        facts.expected_selection_snapshot_member_manifest_hash,
    )
    operation_modeled = facts.operation in _MODELED_OPERATIONS
    selection_required = operation_modeled and facts.operation in _SELECTION_REQUIRED_OPERATIONS
    selection_present = bool(facts.selection_manifest_entries) and all(value is not None for value in selection_values)
    selection_absent = not facts.selection_manifest_entries and all(value is None for value in selection_values)
    selection_is_not_subset = _selection_is_not_target_subset(facts)
    target_release_sources = tuple(
        entry
        for entry in facts.target_manifest_entries
        if entry.member_kind is SyntheticGuardManifestEntryKind.RELEASE_SOURCE
    )
    target_snapshot_members = tuple(
        entry
        for entry in facts.target_manifest_entries
        if entry.member_kind is SyntheticGuardManifestEntryKind.SNAPSHOT_MEMBER
    )
    selection_release_sources = tuple(
        entry
        for entry in facts.selection_manifest_entries
        if entry.member_kind is SyntheticGuardManifestEntryKind.RELEASE_SOURCE
    )
    selection_snapshot_members = tuple(
        entry
        for entry in facts.selection_manifest_entries
        if entry.member_kind is SyntheticGuardManifestEntryKind.SNAPSHOT_MEMBER
    )
    target_source_member_relation_valid = _snapshot_members_have_release_sources(
        target_release_sources,
        target_snapshot_members,
    )
    selection_source_member_relation_valid = _snapshot_members_have_release_sources(
        selection_release_sources,
        selection_snapshot_members,
    )
    selection_purpose_matches = not selection_present or all(
        entry.purpose_code is facts.expected_purpose for entry in facts.selection_manifest_entries
    )
    canonical_entries_valid = _canonical_manifest_entries_are_valid(facts)
    target_release_source_manifest_matches = (
        facts.expected_target_release_source_count > 0
        and len(target_release_sources) == facts.expected_target_release_source_count
        and _is_sha256(facts.expected_target_release_source_manifest_hash)
        and _canonical_guard_manifest_hash(
            facts,
            set_role="ELIGIBILITY_TARGET",
            entry_kind="RELEASE_SOURCE",
            entries=target_release_sources,
        )
        == facts.expected_target_release_source_manifest_hash
    )
    target_snapshot_member_manifest_matches = (
        facts.expected_target_snapshot_member_count > 0
        and len(target_snapshot_members) == facts.expected_target_snapshot_member_count
        and _is_sha256(facts.expected_target_snapshot_member_manifest_hash)
        and _canonical_guard_manifest_hash(
            facts,
            set_role="ELIGIBILITY_TARGET",
            entry_kind="SNAPSHOT_MEMBER",
            entries=target_snapshot_members,
        )
        == facts.expected_target_snapshot_member_manifest_hash
    )
    selection_release_source_manifest_matches = (
        facts.expected_selection_release_source_count is not None
        and facts.expected_selection_release_source_manifest_hash is not None
        and facts.expected_selection_release_source_count > 0
        and len(selection_release_sources) == facts.expected_selection_release_source_count
        and _is_sha256(facts.expected_selection_release_source_manifest_hash)
        and _canonical_guard_manifest_hash(
            facts,
            set_role="OPERATION_SELECTION",
            entry_kind="RELEASE_SOURCE",
            entries=selection_release_sources,
        )
        == facts.expected_selection_release_source_manifest_hash
    )
    selection_snapshot_member_manifest_matches = (
        facts.expected_selection_snapshot_member_count is not None
        and facts.expected_selection_snapshot_member_manifest_hash is not None
        and facts.expected_selection_snapshot_member_count > 0
        and len(selection_snapshot_members) == facts.expected_selection_snapshot_member_count
        and _is_sha256(facts.expected_selection_snapshot_member_manifest_hash)
        and _canonical_guard_manifest_hash(
            facts,
            set_role="OPERATION_SELECTION",
            entry_kind="SNAPSHOT_MEMBER",
            entries=selection_snapshot_members,
        )
        == facts.expected_selection_snapshot_member_manifest_hash
    )
    active_bundle_required = facts.operation in _ACTIVE_BUNDLE_REQUIRED_OPERATIONS
    active_bundle_id_matches = (
        bool(facts.target_bundle_id)
        and facts.requested_bundle_id == facts.target_bundle_id
        and facts.active_bundle_id == facts.target_bundle_id
    )
    active_bundle_manifest_matches = (
        references_valid
        and _is_sha256(facts.target_bundle_manifest_hash)
        and facts.requested_bundle_manifest_hash == facts.target_bundle_manifest_hash
        and facts.active_bundle_manifest_hash == facts.target_bundle_manifest_hash
        and any(
            reference.reference_code.endswith("bundle-manifest")
            and facts.target_bundle_manifest_hash == reference.sha256
            for reference in facts.expected_immutable_references
        )
    )
    citation_purpose_allowed = facts.operation is not SyntheticGuardOperation.CITATION_AUTHORIZATION or (
        facts.expected_purpose is SyntheticUsePurpose.PATIENT_CITATION
        and facts.observed_purpose is SyntheticUsePurpose.PATIENT_CITATION
    )
    request_scope_valid = facts.operation not in _MODELED_OPERATIONS or (
        _scope_codes_are_canonical(facts.request_scope_codes)
        and facts.request_scope_codes is not None
        and facts.request_scope_manifest_hash is not None
        and _is_sha256(facts.request_scope_manifest_hash)
        and _calculate_scope_manifest_hash(facts.request_scope_codes) == facts.request_scope_manifest_hash
    )
    origin = facts.origin_request_guard
    citation_origin_matches = facts.operation is not SyntheticGuardOperation.CITATION_AUTHORIZATION or (
        origin is not None
        and origin.decision is SyntheticGuardDecision.PASS
        and origin.operation is SyntheticGuardOperation.REQUEST
        and origin.bundle_id == facts.target_bundle_id
        and origin.bundle_manifest_hash == facts.target_bundle_manifest_hash
        and origin.environment is facts.observed_environment
        and _scope_codes_are_canonical(origin.request_scope_codes)
        and origin.request_scope_codes == facts.request_scope_codes
        and _is_sha256(origin.scope_manifest_hash)
        and origin.scope_manifest_hash == facts.request_scope_manifest_hash
        and _calculate_scope_manifest_hash(origin.request_scope_codes) == origin.scope_manifest_hash
    )
    return (
        (not facts.source_active, SyntheticGovernanceReason.SOURCE_NOT_ACTIVE),
        (not endpoint_eligible, SyntheticGovernanceReason.ENDPOINT_NOT_ELIGIBLE),
        (not operation_eligible, SyntheticGovernanceReason.OPERATION_NOT_ELIGIBLE),
        (not facts.approval_effective, SyntheticGovernanceReason.APPROVAL_NOT_EFFECTIVE),
        (facts.approval_expired, SyntheticGovernanceReason.APPROVAL_EXPIRED),
        (not facts.license_approved, SyntheticGovernanceReason.LICENSE_NOT_APPROVED),
        (not facts.clinical_scope_allowed, SyntheticGovernanceReason.CLINICAL_SCOPE_NOT_ALLOWED),
        (not facts.snapshot_freshness_current, SyntheticGovernanceReason.SNAPSHOT_FRESHNESS_STALE),
        (facts.revocation_unresolved, SyntheticGovernanceReason.REVOCATION_UNRESOLVED),
        (not facts.snapshot_complete, SyntheticGovernanceReason.SNAPSHOT_INCOMPLETE),
        (not facts.schema_matches, SyntheticGovernanceReason.SCHEMA_DRIFT),
        (not facts.bundle_member_matches, SyntheticGovernanceReason.BUNDLE_MEMBER_MISMATCH),
        (facts.observed_purpose is not facts.expected_purpose, SyntheticGovernanceReason.PURPOSE_MISMATCH),
        (facts.observed_environment is not facts.expected_environment, SyntheticGovernanceReason.ENVIRONMENT_MISMATCH),
        (not references_valid, SyntheticGovernanceReason.IMMUTABLE_REFERENCE_INVALID),
        (
            facts.observed_immutable_references != facts.expected_immutable_references,
            SyntheticGovernanceReason.IMMUTABLE_REFERENCE_MISMATCH,
        ),
        (
            selection_required and not selection_present,
            SyntheticGovernanceReason.SELECTION_REQUIRED,
        ),
        (
            operation_modeled and not selection_required and not selection_absent,
            SyntheticGovernanceReason.SELECTION_FORBIDDEN,
        ),
        (selection_is_not_subset, SyntheticGovernanceReason.SELECTION_NOT_TARGET_SUBSET),
        (
            not target_source_member_relation_valid,
            SyntheticGovernanceReason.TARGET_SOURCE_MEMBER_RELATION_INVALID,
        ),
        (
            operation_modeled and selection_present and not selection_source_member_relation_valid,
            SyntheticGovernanceReason.SELECTION_SOURCE_MEMBER_RELATION_INVALID,
        ),
        (
            operation_modeled and selection_present and not selection_purpose_matches,
            SyntheticGovernanceReason.SELECTION_PURPOSE_MISMATCH,
        ),
        (
            facts.observed_governance_revision != facts.expected_governance_revision,
            SyntheticGovernanceReason.GOVERNANCE_REVISION_MISMATCH,
        ),
        (
            facts.observed_safety_epoch != facts.expected_safety_epoch,
            SyntheticGovernanceReason.SAFETY_EPOCH_MISMATCH,
        ),
        (
            active_bundle_required and not facts.runtime_environment_active,
            SyntheticGovernanceReason.RUNTIME_ENVIRONMENT_NOT_ACTIVE,
        ),
        (
            active_bundle_required and not active_bundle_id_matches,
            SyntheticGovernanceReason.ACTIVE_BUNDLE_ID_MISMATCH,
        ),
        (
            active_bundle_required and references_valid and not active_bundle_manifest_matches,
            SyntheticGovernanceReason.ACTIVE_BUNDLE_MANIFEST_MISMATCH,
        ),
        (
            not request_scope_valid,
            SyntheticGovernanceReason.REQUEST_SCOPE_INVALID,
        ),
        (
            not citation_purpose_allowed,
            SyntheticGovernanceReason.CITATION_PURPOSE_REQUIRED,
        ),
        (
            citation_purpose_allowed and not citation_origin_matches,
            SyntheticGovernanceReason.CITATION_ORIGIN_REQUEST_MISMATCH,
        ),
        (
            facts.operation not in _MODELED_OPERATIONS,
            SyntheticGovernanceReason.OPERATION_CONTEXT_NOT_MODELED,
        ),
        (
            not canonical_entries_valid,
            SyntheticGovernanceReason.CANONICAL_MANIFEST_ENTRY_INVALID,
        ),
        (
            canonical_entries_valid and not target_release_source_manifest_matches,
            SyntheticGovernanceReason.TARGET_RELEASE_SOURCE_MANIFEST_MISMATCH,
        ),
        (
            canonical_entries_valid and not target_snapshot_member_manifest_matches,
            SyntheticGovernanceReason.TARGET_SNAPSHOT_MEMBER_MANIFEST_MISMATCH,
        ),
        (
            canonical_entries_valid
            and operation_modeled
            and selection_present
            and not selection_release_source_manifest_matches,
            SyntheticGovernanceReason.SELECTION_RELEASE_SOURCE_MANIFEST_MISMATCH,
        ),
        (
            canonical_entries_valid
            and operation_modeled
            and selection_present
            and not selection_snapshot_member_manifest_matches,
            SyntheticGovernanceReason.SELECTION_SNAPSHOT_MEMBER_MANIFEST_MISMATCH,
        ),
    )


def evaluate_synthetic_source_governance(
    facts: SyntheticSourceGovernanceFacts,
) -> SyntheticSourceGovernanceEvaluation:
    """Evaluate complete synthetic evidence without performing or authorizing runtime work."""

    reasons = tuple(reason for failed, reason in _evidence_checks(facts) if failed)
    decision = SyntheticGuardDecision.FAIL if reasons else SyntheticGuardDecision.PASS
    return SyntheticSourceGovernanceEvaluation(decision=decision, observation_reasons=reasons)
