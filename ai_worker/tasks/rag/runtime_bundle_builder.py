"""Side-effect-free RAG-12A Runtime Bundle build decision and canonical hashes (Issue #175).

Reading Source, Catalog, Index, Rule, Guideline and Safety versions as "whatever is latest"
makes the evaluated target and the executed target drift apart.  This module fixes one member
set once and derives its deterministic identity, so an evaluation approval and a later runtime
execution can prove they refer to the same content.

Like ``rag_runtime/identification_preflight.py`` (#173) this is a kernel: no I/O, no lock, no
clock, no port.  A passing unit test here proves determinism only -- never approval, publication
readiness, or ``PUBLIC_TRACK_F_ENABLED``.  Rejected input never raises; every failure ends as a
typed fail-closed outcome so a caller cannot mistake an exception path for permission to build.

Scope boundary.  This kernel owns ``BUILDING`` and build-failure judgment only.  It cannot
return ``READY``: :class:`RuntimeBundleBuildDecision` has no such value, and the environment
pointer, ``RETIRED`` and rollback execution stay with RAG-17 (#180).  Worker-Bundle
compatibility is *not* judged here -- see :data:`WORKER_COMPATIBILITY_BLOCK_CODE`.

See ``docs/designs/ceohwj/issue-175-runtime-bundle-build-design.md``.
"""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotUseFailureCode,
    SnapshotVerificationStatus,
    evaluate_snapshot_use_eligibility,
)

RUNTIME_BUNDLE_MANIFEST_PROJECTION_VERSION = "rag-runtime-bundle-manifest-v1"

WORKER_COMPATIBILITY_BLOCK_CODE = "BLOCKED_BY_RUNTIME_BUNDLE_WORKER_DEPLOYMENT_DECISION"
"""Why Worker-Bundle compatibility is recorded as deferred instead of judged.

``docs/governance/post-mvp-1-document-authority.md`` still lists "Runtime Bundle과 Worker 배포"
as an unresolved pre-implementation conflict: ``RETRY_WAIT`` handling, the Worker-Bundle
compatibility check and the drain/rolling deployment method must be decided together, and until
then the section blocks the related implementation and ``current/`` promotion.  ``AGENTS.md``
forbids inventing a rule where no approved document fixes the boundary, so this kernel pins
``worker_artifact_ref`` as an immutable manifest value and reports the compatibility judgment as
:attr:`RuntimeBundleDeferredCheck.WORKER_BUNDLE_COMPATIBILITY` carrying this block code.
"""

_CANONICAL_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


class RuntimeBundleMemberPurpose(StrEnum):
    """Source-backed member vocabulary.

    Value-for-value the merged ``app.models.rag_runtime.RagRuntimeSourcePurpose``.  ``ai_worker``
    never imports ``app``, so the kernel restates it and
    ``backend/app/tests/rag/test_runtime_bundle_repository.py`` pins the two enums as equal.
    """

    CATALOG = "CATALOG"
    KNOWLEDGE = "KNOWLEDGE"
    CANDIDATE_INDEX_INPUT = "CANDIDATE_INDEX_INPUT"
    RULE = "RULE"
    GUIDELINE = "GUIDELINE"
    SAFETY_POLICY = "SAFETY_POLICY"


class RuntimeBundleArtifactKind(StrEnum):
    """Artifact members carried on the bundle row rather than as a source snapshot."""

    CANDIDATE_INDEX = "CANDIDATE_INDEX"
    KNOWLEDGE_INDEX = "KNOWLEDGE_INDEX"
    RULE_SET = "RULE_SET"
    GUIDELINE_SET = "GUIDELINE_SET"
    SAFETY_POLICY = "SAFETY_POLICY"


_HASHED_ARTIFACT_KINDS = frozenset(
    {
        RuntimeBundleArtifactKind.CANDIDATE_INDEX,
        RuntimeBundleArtifactKind.KNOWLEDGE_INDEX,
    }
)
"""Kinds with a ``*_manifest_hash`` column on ``rag_runtime_release_bundle``.

The remaining kinds are stored as a bare ``*_ref``, so a hash supplied for them would have
nowhere to be persisted and is rejected instead of silently dropped.
"""

_REQUIRED_SOURCE_PURPOSES = (RuntimeBundleMemberPurpose.CATALOG,)
_REQUIRED_ARTIFACT_KINDS = (RuntimeBundleArtifactKind.CANDIDATE_INDEX,)


class RuntimeBundleBuildDecision(StrEnum):
    BUILDABLE = "BUILDABLE"
    REJECTED = "REJECTED"


class RuntimeBundleBuildExecutionStatus(StrEnum):
    EVALUATED = "EVALUATED"
    VALIDATION_ERROR = "VALIDATION_ERROR"


class RuntimeBundleRejectionReason(StrEnum):
    """Member eligibility failures.  Internal diagnostics; never projected onto a public DTO.

    Naming follows the already-merged ``SyntheticGovernanceReason`` vocabulary in
    ``ai_worker/tasks/rag/source_governance.py`` so the two read as one policy language.

    Snapshot approval and freshness are deliberately absent: those verdicts belong to
    :func:`evaluate_snapshot_use_eligibility` and are reported through
    :attr:`RuntimeBundleBuildOutcome.snapshot_use_failure_codes`, with
    :attr:`MEMBER_SNAPSHOT_NOT_USABLE` as the single umbrella reason here.
    """

    REQUIRED_SOURCE_MEMBER_MISSING = "REQUIRED_SOURCE_MEMBER_MISSING"
    REQUIRED_ARTIFACT_MEMBER_MISSING = "REQUIRED_ARTIFACT_MEMBER_MISSING"
    MEMBER_SNAPSHOT_NOT_USABLE = "MEMBER_SNAPSHOT_NOT_USABLE"
    MEMBER_APPROVAL_NOT_EFFECTIVE = "MEMBER_APPROVAL_NOT_EFFECTIVE"
    MEMBER_APPROVAL_EXPIRED = "MEMBER_APPROVAL_EXPIRED"
    MEMBER_REVOCATION_UNRESOLVED = "MEMBER_REVOCATION_UNRESOLVED"
    MEMBER_SCOPE_NOT_ALLOWED = "MEMBER_SCOPE_NOT_ALLOWED"
    MEMBER_ENVIRONMENT_MISMATCH = "MEMBER_ENVIRONMENT_MISMATCH"


class RuntimeBundleValidationCode(StrEnum):
    """Structural request failures.  Internal diagnostics; never projected onto a public DTO."""

    REQUEST_SHAPE_INVALID = "REQUEST_SHAPE_INVALID"
    IDENTIFIER_NOT_CANONICAL_UUID = "IDENTIFIER_NOT_CANONICAL_UUID"
    SHA256_NOT_CANONICAL = "SHA256_NOT_CANONICAL"
    GIT_COMMIT_SHA_NOT_CANONICAL = "GIT_COMMIT_SHA_NOT_CANONICAL"
    TEXT_NOT_NFC = "TEXT_NOT_NFC"
    TEXT_BLANK = "TEXT_BLANK"
    AT_LEAST_ONE_SOURCE_MEMBER_REQUIRED = "AT_LEAST_ONE_SOURCE_MEMBER_REQUIRED"
    DUPLICATE_SOURCE_MEMBER = "DUPLICATE_SOURCE_MEMBER"
    DUPLICATE_ARTIFACT_MEMBER = "DUPLICATE_ARTIFACT_MEMBER"
    ARTIFACT_MANIFEST_HASH_REQUIRED = "ARTIFACT_MANIFEST_HASH_REQUIRED"
    ARTIFACT_MANIFEST_HASH_FORBIDDEN = "ARTIFACT_MANIFEST_HASH_FORBIDDEN"


class RuntimeBundleReadinessBlocker(StrEnum):
    """Members whose absence keeps RAG-17 from promoting the bundle past ``BUILDING``.

    ``#175`` owns only "block ``READY`` while a component is incomplete"; the ``READY`` judgment
    itself is RAG-17's.  This axis is the input RAG-17 reads, not a judgment made here.
    """

    KNOWLEDGE_INDEX_MEMBER_ABSENT = "KNOWLEDGE_INDEX_MEMBER_ABSENT"
    RULE_SET_MEMBER_ABSENT = "RULE_SET_MEMBER_ABSENT"
    GUIDELINE_SET_MEMBER_ABSENT = "GUIDELINE_SET_MEMBER_ABSENT"
    SAFETY_POLICY_MEMBER_ABSENT = "SAFETY_POLICY_MEMBER_ABSENT"


class RuntimeBundleDeferredCheck(StrEnum):
    WORKER_BUNDLE_COMPATIBILITY = "WORKER_BUNDLE_COMPATIBILITY"


_READINESS_BLOCKER_BY_ARTIFACT_KIND = {
    RuntimeBundleArtifactKind.KNOWLEDGE_INDEX: RuntimeBundleReadinessBlocker.KNOWLEDGE_INDEX_MEMBER_ABSENT,
    RuntimeBundleArtifactKind.RULE_SET: RuntimeBundleReadinessBlocker.RULE_SET_MEMBER_ABSENT,
    RuntimeBundleArtifactKind.GUIDELINE_SET: RuntimeBundleReadinessBlocker.GUIDELINE_SET_MEMBER_ABSENT,
    RuntimeBundleArtifactKind.SAFETY_POLICY: RuntimeBundleReadinessBlocker.SAFETY_POLICY_MEMBER_ABSENT,
}


@dataclass(frozen=True, slots=True)
class RuntimeExecutionManifestInput:
    """The execution-environment axis pinned into ``rag_runtime_execution_manifest``.

    ``worker_artifact_ref`` is pinned, never compatibility-checked; see
    :data:`WORKER_COMPATIBILITY_BLOCK_CODE`.
    """

    manifest_key: str
    manifest_version: str
    schema_version: str
    git_commit_sha: str
    worker_artifact_ref: str | None = None
    model_ref: str | None = None
    prompt_ref: str | None = None
    parser_ref: str | None = None
    resolver_ref: str | None = None
    guard_policy_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeBundleSourceMemberInput:
    """One ``rag_runtime_bundle_source`` member plus the eligibility facts observed for it.

    The pinned fields identify the member and enter the bundle hash.  The observation fields
    (``verification_status`` onwards) are eligibility evidence gathered by the caller under its
    own lock and are deliberately excluded from the hash; see
    :func:`canonical_runtime_bundle_manifest_hash`.

    ``verification_status``, ``rejected_record_count``, ``publication_approval_passed``,
    ``freshness_eligible`` and ``provenance_valid`` are the exact inputs of
    :func:`evaluate_snapshot_use_eligibility` -- the merged "Catalog·Runtime 공통 Snapshot 사용
    가능 판정" from #362.  Snapshot approval and freshness are decided by that shared function,
    not restated here, so bundle build and catalog build cannot drift apart.  ``approval_expired``,
    ``revocation_unresolved`` and ``scope_allowed`` remain local because that function models
    none of them.

    ``required`` and ``selected_for_operation`` are pinned member configuration -- they change
    what the bundle means -- so they are hashed and stored, not treated as observations.  Every
    member is gated identically regardless of ``required``: pinning a revoked or unapproved
    member into an immutable bundle is fail-closed either way.
    """

    source_snapshot_id: str
    source_purpose: RuntimeBundleMemberPurpose
    source_version: str
    canonical_checksum: str
    approval_version: str
    scope_policy_hash: str
    freshness_policy_hash: str
    observed_environment: str
    required: bool = True
    selected_for_operation: bool = True
    verification_status: SnapshotVerificationStatus = SnapshotVerificationStatus.CURRENT
    rejected_record_count: int = 0
    publication_approval_passed: bool = False
    freshness_eligible: bool = True
    provenance_valid: bool = True
    approval_expired: bool = False
    revocation_unresolved: bool = False
    scope_allowed: bool = True


@dataclass(frozen=True, slots=True)
class RuntimeBundleArtifactMemberInput:
    """One artifact member carried on the ``rag_runtime_release_bundle`` row.

    For :attr:`RuntimeBundleArtifactKind.CANDIDATE_INDEX` the ``manifest_hash`` is the
    ``CandidateIndexManifest.content_hash`` produced by RAG-07A (#167).  No new hash scheme is
    introduced here.
    """

    artifact_kind: RuntimeBundleArtifactKind
    artifact_ref: str
    artifact_version: str
    observed_environment: str
    manifest_hash: str | None = None
    approval_effective: bool = True
    approval_expired: bool = False
    revocation_unresolved: bool = False


@dataclass(frozen=True, slots=True)
class RuntimeBundleBuildRequest:
    bundle_key: str
    bundle_version: str
    target_environment: str
    execution_manifest: RuntimeExecutionManifestInput
    source_members: tuple[RuntimeBundleSourceMemberInput, ...]
    artifact_members: tuple[RuntimeBundleArtifactMemberInput, ...] = ()
    governance_revision_ref: str | None = None
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeBundleBuildOutcome:
    execution_status: RuntimeBundleBuildExecutionStatus
    decision: RuntimeBundleBuildDecision
    manifest_projection_version: str = RUNTIME_BUNDLE_MANIFEST_PROJECTION_VERSION
    manifest_hash: str | None = None
    bundle_manifest_hash: str | None = None
    source_member_count: int = 0
    artifact_member_count: int = 0
    rejection_reasons: tuple[RuntimeBundleRejectionReason, ...] = ()
    snapshot_use_failure_codes: tuple[SnapshotUseFailureCode, ...] = ()
    validation_codes: tuple[RuntimeBundleValidationCode, ...] = ()
    readiness_blockers: tuple[RuntimeBundleReadinessBlocker, ...] = ()
    deferred_checks: tuple[RuntimeBundleDeferredCheck, ...] = ()
    deferred_check_block_codes: tuple[str, ...] = ()


def evaluate_runtime_bundle_build(request: RuntimeBundleBuildRequest) -> RuntimeBundleBuildOutcome:
    """Decide whether a ``BUILDING`` bundle may be persisted for this member set.

    ``BUILDABLE`` authorises exactly one thing: writing the manifest, the bundle row at
    ``BUILDING`` and its members in a single transaction.  It is not a ``READY`` judgment, an
    evaluation approval, or permission to touch an environment pointer.
    """
    validation_codes = _validate_request(request)
    if validation_codes:
        return RuntimeBundleBuildOutcome(
            execution_status=RuntimeBundleBuildExecutionStatus.VALIDATION_ERROR,
            decision=RuntimeBundleBuildDecision.REJECTED,
            validation_codes=validation_codes,
        )

    rejection_reasons, snapshot_use_failure_codes = _rejection_reasons(request)
    counted = RuntimeBundleBuildOutcome(
        execution_status=RuntimeBundleBuildExecutionStatus.EVALUATED,
        decision=RuntimeBundleBuildDecision.REJECTED,
        source_member_count=len(request.source_members),
        artifact_member_count=len(request.artifact_members),
        rejection_reasons=rejection_reasons,
        snapshot_use_failure_codes=snapshot_use_failure_codes,
        readiness_blockers=_readiness_blockers(request),
        deferred_checks=(RuntimeBundleDeferredCheck.WORKER_BUNDLE_COMPATIBILITY,),
        deferred_check_block_codes=(WORKER_COMPATIBILITY_BLOCK_CODE,),
    )
    if rejection_reasons:
        return counted

    manifest_hash = canonical_execution_manifest_hash(request.execution_manifest)
    return RuntimeBundleBuildOutcome(
        execution_status=counted.execution_status,
        decision=RuntimeBundleBuildDecision.BUILDABLE,
        manifest_hash=manifest_hash,
        bundle_manifest_hash=canonical_runtime_bundle_manifest_hash(request, manifest_hash=manifest_hash),
        source_member_count=counted.source_member_count,
        artifact_member_count=counted.artifact_member_count,
        readiness_blockers=counted.readiness_blockers,
        deferred_checks=counted.deferred_checks,
        deferred_check_block_codes=counted.deferred_check_block_codes,
    )


def canonical_execution_manifest_hash(manifest: RuntimeExecutionManifestInput) -> str:
    """Return the SHA-256 identity of the pinned execution-environment axis.

    ``manifest_key`` and ``manifest_version`` are included, which keeps this value consistent
    with the ``uq_rag_runtime_manifest_hash`` and ``uq_rag_runtime_manifest_key_version``
    constraints holding at the same time.
    """
    payload = {
        "projection_version": RUNTIME_BUNDLE_MANIFEST_PROJECTION_VERSION,
        "manifest_key": manifest.manifest_key,
        "manifest_version": manifest.manifest_version,
        "schema_version": manifest.schema_version,
        "git_commit_sha": manifest.git_commit_sha,
        "worker_artifact_ref": manifest.worker_artifact_ref,
        "model_ref": manifest.model_ref,
        "prompt_ref": manifest.prompt_ref,
        "parser_ref": manifest.parser_ref,
        "resolver_ref": manifest.resolver_ref,
        "guard_policy_ref": manifest.guard_policy_ref,
    }
    return _sha256_of(payload)


def canonical_runtime_bundle_manifest_hash(
    request: RuntimeBundleBuildRequest,
    *,
    manifest_hash: str,
) -> str:
    """Return the order-independent SHA-256 identity of the pinned bundle content.

    What is hashed, and why:

    - The execution manifest hash, the full source member set and the full artifact member set:
      this value must change whenever the executed content changes.
    - ``target_environment``: ``rag_runtime_release_bundle`` has no environment column, so this
      is the only place the environment binding required by ``rag-runtime-v1.md`` can live
      without adding one.

    What is excluded, and why:

    - ``bundle_key`` / ``bundle_version`` / ``created_by``: naming and provenance, not content.
      Excluding them makes ``uq_rag_runtime_bundle_manifest_hash`` mean "one bundle row per
      distinct execution content", which is what ``rag-runtime-v1.md`` relies on when it requires
      an evaluation of "동일 Manifest" to carry over.  Rebuilding identical content therefore
      collides on that constraint by design; the caller reuses the existing bundle.
    - ``governance_revision_ref``: checked separately as its own guard reason
      (``GOVERNANCE_REVISION_MISMATCH`` in ``source_governance.py``).  Folding a revision bump
      into content identity would invalidate evaluation reuse for unchanged content.
    - Every observation field: the manifest identifies the *pinned* member set, and the same
      member set must hash identically whether or not an observation later turns it ineligible.
      Same reason ``canonical_preflight_manifest_hash`` (#173) excludes observed pointers.
    """
    source_members = sorted(
        (
            {
                "source_snapshot_id": member.source_snapshot_id,
                "source_purpose": member.source_purpose.value,
                "source_version": member.source_version,
                "canonical_checksum": member.canonical_checksum,
                "approval_version": member.approval_version,
                "scope_policy_hash": member.scope_policy_hash,
                "freshness_policy_hash": member.freshness_policy_hash,
                "required": member.required,
                "selected_for_operation": member.selected_for_operation,
            }
            for member in request.source_members
        ),
        key=_canonical_bytes,
    )
    artifact_members = sorted(
        (
            {
                "artifact_kind": member.artifact_kind.value,
                "artifact_ref": member.artifact_ref,
                "artifact_version": member.artifact_version,
                "manifest_hash": member.manifest_hash,
            }
            for member in request.artifact_members
        ),
        key=_canonical_bytes,
    )
    payload = {
        "projection_version": RUNTIME_BUNDLE_MANIFEST_PROJECTION_VERSION,
        "target_environment": request.target_environment,
        "execution_manifest_hash": manifest_hash,
        "source_members": source_members,
        "artifact_members": artifact_members,
    }
    return _sha256_of(payload)


def _rejection_reasons(
    request: RuntimeBundleBuildRequest,
) -> tuple[tuple[RuntimeBundleRejectionReason, ...], tuple[SnapshotUseFailureCode, ...]]:
    reasons: set[RuntimeBundleRejectionReason] = set()
    snapshot_failures: set[SnapshotUseFailureCode] = set()

    present_purposes = {member.source_purpose for member in request.source_members}
    if any(purpose not in present_purposes for purpose in _REQUIRED_SOURCE_PURPOSES):
        reasons.add(RuntimeBundleRejectionReason.REQUIRED_SOURCE_MEMBER_MISSING)
    present_kinds = {member.artifact_kind for member in request.artifact_members}
    if any(kind not in present_kinds for kind in _REQUIRED_ARTIFACT_KINDS):
        reasons.add(RuntimeBundleRejectionReason.REQUIRED_ARTIFACT_MEMBER_MISSING)

    for source_member in request.source_members:
        # #362's shared Catalog/Runtime policy owns snapshot approval and freshness.
        eligibility = evaluate_snapshot_use_eligibility(
            verification_status=source_member.verification_status,
            rejected_record_count=source_member.rejected_record_count,
            publication_approval_passed=source_member.publication_approval_passed,
            freshness_eligible=source_member.freshness_eligible,
            provenance_valid=source_member.provenance_valid,
        )
        if not eligibility.usable:
            reasons.add(RuntimeBundleRejectionReason.MEMBER_SNAPSHOT_NOT_USABLE)
            if eligibility.failure_code is not None:
                snapshot_failures.add(eligibility.failure_code)
        if not source_member.scope_allowed:
            reasons.add(RuntimeBundleRejectionReason.MEMBER_SCOPE_NOT_ALLOWED)
        reasons.update(
            _common_member_reasons(
                approval_expired=source_member.approval_expired,
                revocation_unresolved=source_member.revocation_unresolved,
                observed_environment=source_member.observed_environment,
                target_environment=request.target_environment,
            )
        )

    for artifact_member in request.artifact_members:
        # Artifact members are index/rule/guideline/safety refs, not source snapshots, so the
        # snapshot policy does not apply to them and their own approval axis is checked here.
        if not artifact_member.approval_effective:
            reasons.add(RuntimeBundleRejectionReason.MEMBER_APPROVAL_NOT_EFFECTIVE)
        reasons.update(
            _common_member_reasons(
                approval_expired=artifact_member.approval_expired,
                revocation_unresolved=artifact_member.revocation_unresolved,
                observed_environment=artifact_member.observed_environment,
                target_environment=request.target_environment,
            )
        )

    return (
        tuple(reason for reason in RuntimeBundleRejectionReason if reason in reasons),
        tuple(code for code in SnapshotUseFailureCode if code in snapshot_failures),
    )


def _common_member_reasons(
    *,
    approval_expired: bool,
    revocation_unresolved: bool,
    observed_environment: str,
    target_environment: str,
) -> set[RuntimeBundleRejectionReason]:
    reasons: set[RuntimeBundleRejectionReason] = set()
    if approval_expired:
        reasons.add(RuntimeBundleRejectionReason.MEMBER_APPROVAL_EXPIRED)
    if revocation_unresolved:
        reasons.add(RuntimeBundleRejectionReason.MEMBER_REVOCATION_UNRESOLVED)
    if observed_environment != target_environment:
        reasons.add(RuntimeBundleRejectionReason.MEMBER_ENVIRONMENT_MISMATCH)
    return reasons


def _readiness_blockers(request: RuntimeBundleBuildRequest) -> tuple[RuntimeBundleReadinessBlocker, ...]:
    present_kinds = {member.artifact_kind for member in request.artifact_members}
    blockers = {blocker for kind, blocker in _READINESS_BLOCKER_BY_ARTIFACT_KIND.items() if kind not in present_kinds}
    return tuple(blocker for blocker in RuntimeBundleReadinessBlocker if blocker in blockers)


def _validate_request(request: RuntimeBundleBuildRequest) -> tuple[RuntimeBundleValidationCode, ...]:
    if not _is_request_shaped(request):
        return (RuntimeBundleValidationCode.REQUEST_SHAPE_INVALID,)

    codes: set[RuntimeBundleValidationCode] = set()
    codes.update(_validate_bundle_identity(request))
    codes.update(_validate_execution_manifest(request.execution_manifest))
    codes.update(_validate_source_members(request))
    codes.update(_validate_artifact_members(request))
    return tuple(code for code in RuntimeBundleValidationCode if code in codes)


def _validate_bundle_identity(request: RuntimeBundleBuildRequest) -> set[RuntimeBundleValidationCode]:
    optional_text = (request.governance_revision_ref, request.created_by)
    return _text_codes(
        required=(request.bundle_key, request.bundle_version, request.target_environment),
        optional=optional_text,
    )


def _validate_execution_manifest(manifest: RuntimeExecutionManifestInput) -> set[RuntimeBundleValidationCode]:
    codes = _text_codes(
        required=(manifest.manifest_key, manifest.manifest_version, manifest.schema_version),
        optional=(
            manifest.worker_artifact_ref,
            manifest.model_ref,
            manifest.prompt_ref,
            manifest.parser_ref,
            manifest.resolver_ref,
            manifest.guard_policy_ref,
        ),
    )
    if _GIT_COMMIT_SHA_RE.fullmatch(manifest.git_commit_sha) is None:
        codes.add(RuntimeBundleValidationCode.GIT_COMMIT_SHA_NOT_CANONICAL)
    return codes


def _validate_source_members(request: RuntimeBundleBuildRequest) -> set[RuntimeBundleValidationCode]:
    members = request.source_members
    codes: set[RuntimeBundleValidationCode] = set()
    if not members:
        codes.add(RuntimeBundleValidationCode.AT_LEAST_ONE_SOURCE_MEMBER_REQUIRED)
        return codes

    # Mirrors uq_rag_runtime_bundle_source_member: one row per (snapshot, purpose).
    member_keys = [(member.source_snapshot_id, member.source_purpose) for member in members]
    if len(member_keys) != len(set(member_keys)):
        codes.add(RuntimeBundleValidationCode.DUPLICATE_SOURCE_MEMBER)

    for member in members:
        if _CANONICAL_UUID_RE.fullmatch(member.source_snapshot_id) is None:
            codes.add(RuntimeBundleValidationCode.IDENTIFIER_NOT_CANONICAL_UUID)
        if any(
            _SHA256_RE.fullmatch(value) is None
            for value in (member.canonical_checksum, member.scope_policy_hash, member.freshness_policy_hash)
        ):
            codes.add(RuntimeBundleValidationCode.SHA256_NOT_CANONICAL)
        codes.update(
            _text_codes(
                required=(member.source_version, member.approval_version, member.observed_environment),
                optional=(),
            )
        )
    return codes


def _validate_artifact_members(request: RuntimeBundleBuildRequest) -> set[RuntimeBundleValidationCode]:
    members = request.artifact_members
    codes: set[RuntimeBundleValidationCode] = set()
    kinds = [member.artifact_kind for member in members]
    if len(kinds) != len(set(kinds)):
        codes.add(RuntimeBundleValidationCode.DUPLICATE_ARTIFACT_MEMBER)

    for member in members:
        codes.update(
            _text_codes(
                required=(member.artifact_ref, member.artifact_version, member.observed_environment),
                optional=(),
            )
        )
        if member.artifact_kind in _HASHED_ARTIFACT_KINDS:
            if member.manifest_hash is None:
                codes.add(RuntimeBundleValidationCode.ARTIFACT_MANIFEST_HASH_REQUIRED)
            elif _SHA256_RE.fullmatch(member.manifest_hash) is None:
                codes.add(RuntimeBundleValidationCode.SHA256_NOT_CANONICAL)
        elif member.manifest_hash is not None:
            codes.add(RuntimeBundleValidationCode.ARTIFACT_MANIFEST_HASH_FORBIDDEN)
    return codes


def _text_codes(
    *,
    required: tuple[str, ...],
    optional: tuple[str | None, ...],
) -> set[RuntimeBundleValidationCode]:
    codes: set[RuntimeBundleValidationCode] = set()
    if any(value.strip() == "" for value in required):
        codes.add(RuntimeBundleValidationCode.TEXT_BLANK)
    present = required + tuple(value for value in optional if value is not None)
    if any(not unicodedata.is_normalized("NFC", value) for value in present):
        codes.add(RuntimeBundleValidationCode.TEXT_NOT_NFC)
    return codes


def _is_source_member_shaped(member: object) -> bool:
    return (
        isinstance(member, RuntimeBundleSourceMemberInput)
        and isinstance(member.source_purpose, RuntimeBundleMemberPurpose)
        and all(
            isinstance(value, str)
            for value in (
                member.source_snapshot_id,
                member.source_version,
                member.canonical_checksum,
                member.approval_version,
                member.scope_policy_hash,
                member.freshness_policy_hash,
                member.observed_environment,
            )
        )
        and isinstance(member.verification_status, SnapshotVerificationStatus)
        # evaluate_snapshot_use_eligibility raises on a negative count, so reject it here
        # instead: this kernel must never raise for bad input.
        and isinstance(member.rejected_record_count, int)
        and not isinstance(member.rejected_record_count, bool)
        and member.rejected_record_count >= 0
        and all(
            isinstance(value, bool)
            for value in (
                member.required,
                member.selected_for_operation,
                member.publication_approval_passed,
                member.freshness_eligible,
                member.provenance_valid,
                member.approval_expired,
                member.revocation_unresolved,
                member.scope_allowed,
            )
        )
    )


def _is_artifact_member_shaped(member: object) -> bool:
    return (
        isinstance(member, RuntimeBundleArtifactMemberInput)
        and isinstance(member.artifact_kind, RuntimeBundleArtifactKind)
        and all(
            isinstance(value, str)
            for value in (member.artifact_ref, member.artifact_version, member.observed_environment)
        )
        and (member.manifest_hash is None or isinstance(member.manifest_hash, str))
        and all(
            isinstance(value, bool)
            for value in (member.approval_effective, member.approval_expired, member.revocation_unresolved)
        )
    )


def _is_execution_manifest_shaped(manifest: object) -> bool:
    return (
        isinstance(manifest, RuntimeExecutionManifestInput)
        and all(
            isinstance(value, str)
            for value in (
                manifest.manifest_key,
                manifest.manifest_version,
                manifest.schema_version,
                manifest.git_commit_sha,
            )
        )
        and all(
            value is None or isinstance(value, str)
            for value in (
                manifest.worker_artifact_ref,
                manifest.model_ref,
                manifest.prompt_ref,
                manifest.parser_ref,
                manifest.resolver_ref,
                manifest.guard_policy_ref,
            )
        )
    )


def _is_request_shaped(request: object) -> bool:
    if not isinstance(request, RuntimeBundleBuildRequest):
        return False
    if not all(
        isinstance(value, str) for value in (request.bundle_key, request.bundle_version, request.target_environment)
    ):
        return False
    if not all(
        value is None or isinstance(value, str) for value in (request.governance_revision_ref, request.created_by)
    ):
        return False
    if not _is_execution_manifest_shaped(request.execution_manifest):
        return False
    if not isinstance(request.source_members, tuple) or not isinstance(request.artifact_members, tuple):
        return False
    return all(_is_source_member_shaped(member) for member in request.source_members) and all(
        _is_artifact_member_shaped(member) for member in request.artifact_members
    )


def _canonical_bytes(value: object) -> bytes:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return unicodedata.normalize("NFC", serialized).encode("utf-8")


def _sha256_of(payload: object) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()
