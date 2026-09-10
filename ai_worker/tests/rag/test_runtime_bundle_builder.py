"""RAG-12A Runtime Bundle build kernel determinism and fail-closed rejection (Issue #175)."""

from dataclasses import replace
from uuid import uuid4

from ai_worker.tasks.rag.runtime_bundle_builder import (
    RUNTIME_BUNDLE_MANIFEST_PROJECTION_VERSION,
    WORKER_COMPATIBILITY_BLOCK_CODE,
    RuntimeBundleArtifactKind,
    RuntimeBundleArtifactMemberInput,
    RuntimeBundleBuildDecision,
    RuntimeBundleBuildExecutionStatus,
    RuntimeBundleBuildRequest,
    RuntimeBundleDeferredCheck,
    RuntimeBundleMemberPurpose,
    RuntimeBundleReadinessBlocker,
    RuntimeBundleRejectionReason,
    RuntimeBundleSourceMemberInput,
    RuntimeBundleValidationCode,
    RuntimeExecutionManifestInput,
    canonical_execution_manifest_hash,
    canonical_runtime_bundle_manifest_hash,
    evaluate_runtime_bundle_build,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotUseFailureCode,
    SnapshotVerificationStatus,
)

_ENVIRONMENT = "local"


def _hash(char: str) -> str:
    return char * 64


def _manifest() -> RuntimeExecutionManifestInput:
    return RuntimeExecutionManifestInput(
        manifest_key="rag-runtime",
        manifest_version="2026.09.10-001",
        schema_version="runtime-manifest-v1",
        git_commit_sha="abcdef1",
        worker_artifact_ref="worker:local:2026.09.10",
        model_ref="model:local:v1",
        prompt_ref="prompt:v1",
        parser_ref="parser:v1",
        resolver_ref="resolver:v1",
        guard_policy_ref="guard:v1",
    )


def _source_member(
    purpose: RuntimeBundleMemberPurpose = RuntimeBundleMemberPurpose.CATALOG,
    **overrides: object,
) -> RuntimeBundleSourceMemberInput:
    member = RuntimeBundleSourceMemberInput(
        source_snapshot_id=str(uuid4()),
        source_purpose=purpose,
        source_version="api:2026-09-10:0001",
        canonical_checksum=_hash("b"),
        approval_version="approval-v1",
        scope_policy_hash=_hash("c"),
        freshness_policy_hash=_hash("d"),
        observed_environment=_ENVIRONMENT,
    )
    return replace(member, **overrides)  # type: ignore[arg-type]


def _artifact_member(
    kind: RuntimeBundleArtifactKind = RuntimeBundleArtifactKind.CANDIDATE_INDEX,
    **overrides: object,
) -> RuntimeBundleArtifactMemberInput:
    member = RuntimeBundleArtifactMemberInput(
        artifact_kind=kind,
        artifact_ref=f"artifact:{kind.value.lower()}",
        artifact_version="1.0.0",
        observed_environment=_ENVIRONMENT,
        manifest_hash=_hash("e")
        if kind
        in {
            RuntimeBundleArtifactKind.CANDIDATE_INDEX,
            RuntimeBundleArtifactKind.KNOWLEDGE_INDEX,
        }
        else None,
    )
    return replace(member, **overrides)  # type: ignore[arg-type]


def _request(**overrides: object) -> RuntimeBundleBuildRequest:
    request = RuntimeBundleBuildRequest(
        bundle_key="local-rag-runtime",
        bundle_version="2026.09.10-001",
        target_environment=_ENVIRONMENT,
        execution_manifest=_manifest(),
        source_members=(
            _source_member(RuntimeBundleMemberPurpose.CATALOG),
            _source_member(RuntimeBundleMemberPurpose.KNOWLEDGE),
        ),
        artifact_members=(_artifact_member(RuntimeBundleArtifactKind.CANDIDATE_INDEX),),
        created_by="ai-worker-test",
    )
    return replace(request, **overrides)  # type: ignore[arg-type]


def test_minimum_member_set_is_buildable_and_never_ready() -> None:
    outcome = evaluate_runtime_bundle_build(_request())

    assert outcome.execution_status is RuntimeBundleBuildExecutionStatus.EVALUATED
    assert outcome.decision is RuntimeBundleBuildDecision.BUILDABLE
    assert outcome.rejection_reasons == ()
    assert outcome.validation_codes == ()
    assert outcome.manifest_projection_version == RUNTIME_BUNDLE_MANIFEST_PROJECTION_VERSION
    assert outcome.manifest_hash is not None
    assert outcome.bundle_manifest_hash is not None
    assert len(outcome.manifest_hash) == 64
    assert len(outcome.bundle_manifest_hash) == 64
    assert outcome.source_member_count == 2
    assert outcome.artifact_member_count == 1
    # The kernel has no vocabulary for READY or an active pointer; that stays with RAG-17.
    assert {member.value for member in RuntimeBundleBuildDecision} == {"BUILDABLE", "REJECTED"}


def test_same_components_in_any_order_produce_the_same_hashes() -> None:
    request = _request()
    reordered = replace(
        request,
        source_members=tuple(reversed(request.source_members)),
    )

    first = evaluate_runtime_bundle_build(request)
    second = evaluate_runtime_bundle_build(reordered)

    assert first.manifest_hash == second.manifest_hash
    assert first.bundle_manifest_hash == second.bundle_manifest_hash


def test_bundle_hash_is_stable_across_repeated_evaluation() -> None:
    request = _request()

    assert evaluate_runtime_bundle_build(request).bundle_manifest_hash == (
        evaluate_runtime_bundle_build(request).bundle_manifest_hash
    )


def test_changing_a_member_version_changes_the_bundle_hash() -> None:
    request = _request()
    baseline = evaluate_runtime_bundle_build(request).bundle_manifest_hash
    changed = replace(
        request,
        source_members=(
            replace(request.source_members[0], source_version="api:2026-09-11:0002"),
            request.source_members[1],
        ),
    )

    assert evaluate_runtime_bundle_build(changed).bundle_manifest_hash != baseline


def test_changing_the_worker_artifact_changes_both_hashes() -> None:
    request = _request()
    baseline = evaluate_runtime_bundle_build(request)
    changed = evaluate_runtime_bundle_build(
        replace(request, execution_manifest=replace(request.execution_manifest, worker_artifact_ref="worker:other"))
    )

    assert changed.manifest_hash != baseline.manifest_hash
    assert changed.bundle_manifest_hash != baseline.bundle_manifest_hash


def test_bundle_naming_and_provenance_are_not_part_of_content_identity() -> None:
    request = _request()
    renamed = replace(request, bundle_key="other-key", bundle_version="9.9.9", created_by="someone-else")

    assert (
        evaluate_runtime_bundle_build(renamed).bundle_manifest_hash
        == evaluate_runtime_bundle_build(request).bundle_manifest_hash
    )


def test_target_environment_is_part_of_content_identity() -> None:
    request = _request()
    other_environment = replace(
        request,
        target_environment="test",
        source_members=tuple(replace(member, observed_environment="test") for member in request.source_members),
        artifact_members=tuple(replace(member, observed_environment="test") for member in request.artifact_members),
    )

    assert (
        evaluate_runtime_bundle_build(other_environment).bundle_manifest_hash
        != evaluate_runtime_bundle_build(request).bundle_manifest_hash
    )


def test_observation_fields_do_not_change_the_bundle_hash() -> None:
    request = _request()
    manifest_hash = canonical_execution_manifest_hash(request.execution_manifest)
    baseline = canonical_runtime_bundle_manifest_hash(request, manifest_hash=manifest_hash)
    observed_stale = replace(
        request,
        source_members=(
            replace(request.source_members[0], freshness_eligible=False),
            request.source_members[1],
        ),
    )

    assert canonical_runtime_bundle_manifest_hash(observed_stale, manifest_hash=manifest_hash) == baseline
    # The pinned identity is unchanged, but the member is no longer eligible.
    assert evaluate_runtime_bundle_build(observed_stale).decision is RuntimeBundleBuildDecision.REJECTED


def test_medication_catalog_member_is_required() -> None:
    outcome = evaluate_runtime_bundle_build(
        _request(source_members=(_source_member(RuntimeBundleMemberPurpose.KNOWLEDGE),))
    )

    assert outcome.decision is RuntimeBundleBuildDecision.REJECTED
    assert RuntimeBundleRejectionReason.REQUIRED_SOURCE_MEMBER_MISSING in outcome.rejection_reasons
    assert outcome.bundle_manifest_hash is None


def test_candidate_index_member_is_required() -> None:
    outcome = evaluate_runtime_bundle_build(_request(artifact_members=()))

    assert outcome.decision is RuntimeBundleBuildDecision.REJECTED
    assert RuntimeBundleRejectionReason.REQUIRED_ARTIFACT_MEMBER_MISSING in outcome.rejection_reasons


def test_unapproved_expired_revoked_stale_and_mismatched_members_are_blocked() -> None:
    cases = (
        ({"approval_expired": True}, RuntimeBundleRejectionReason.MEMBER_APPROVAL_EXPIRED),
        ({"revocation_unresolved": True}, RuntimeBundleRejectionReason.MEMBER_REVOCATION_UNRESOLVED),
        ({"scope_allowed": False}, RuntimeBundleRejectionReason.MEMBER_SCOPE_NOT_ALLOWED),
        ({"observed_environment": "production"}, RuntimeBundleRejectionReason.MEMBER_ENVIRONMENT_MISMATCH),
    )
    for overrides, expected in cases:
        outcome = evaluate_runtime_bundle_build(
            _request(
                source_members=(
                    _source_member(RuntimeBundleMemberPurpose.CATALOG, **overrides),
                    _source_member(RuntimeBundleMemberPurpose.KNOWLEDGE),
                )
            )
        )

        assert outcome.decision is RuntimeBundleBuildDecision.REJECTED, overrides
        assert expected in outcome.rejection_reasons, overrides
        assert outcome.bundle_manifest_hash is None, overrides


def test_snapshot_eligibility_is_delegated_to_the_shared_362_policy() -> None:
    """Every snapshot verdict must come from evaluate_snapshot_use_eligibility, not a local copy."""
    cases = (
        ({"provenance_valid": False}, SnapshotUseFailureCode.SNAPSHOT_PROVENANCE_INVALID),
        (
            {"verification_status": SnapshotVerificationStatus.FAILED},
            SnapshotUseFailureCode.SNAPSHOT_VALIDATION_FAILED,
        ),
        (
            {"verification_status": SnapshotVerificationStatus.STALE},
            SnapshotUseFailureCode.SNAPSHOT_SUPERSEDED,
        ),
        (
            {"verification_status": SnapshotVerificationStatus.PENDING},
            SnapshotUseFailureCode.SNAPSHOT_NOT_APPROVED,
        ),
        (
            {"rejected_record_count": 1, "publication_approval_passed": False},
            SnapshotUseFailureCode.SNAPSHOT_NOT_APPROVED,
        ),
        ({"freshness_eligible": False}, SnapshotUseFailureCode.SNAPSHOT_FRESHNESS_STALE),
    )
    for overrides, expected_code in cases:
        outcome = evaluate_runtime_bundle_build(
            _request(
                source_members=(
                    _source_member(RuntimeBundleMemberPurpose.CATALOG, **overrides),
                    _source_member(RuntimeBundleMemberPurpose.KNOWLEDGE),
                )
            )
        )

        assert outcome.decision is RuntimeBundleBuildDecision.REJECTED, overrides
        assert RuntimeBundleRejectionReason.MEMBER_SNAPSHOT_NOT_USABLE in outcome.rejection_reasons, overrides
        assert expected_code in outcome.snapshot_use_failure_codes, overrides
        assert outcome.bundle_manifest_hash is None, overrides


def test_rejected_records_with_publication_approval_stay_usable() -> None:
    outcome = evaluate_runtime_bundle_build(
        _request(
            source_members=(
                _source_member(
                    RuntimeBundleMemberPurpose.CATALOG,
                    rejected_record_count=1,
                    publication_approval_passed=True,
                ),
            )
        )
    )

    assert outcome.decision is RuntimeBundleBuildDecision.BUILDABLE
    assert outcome.snapshot_use_failure_codes == ()


def test_negative_rejected_record_count_is_a_validation_error_not_an_exception() -> None:
    """evaluate_snapshot_use_eligibility raises on a negative count; the kernel must not."""
    outcome = evaluate_runtime_bundle_build(
        _request(source_members=(_source_member(RuntimeBundleMemberPurpose.CATALOG, rejected_record_count=-1),))
    )

    assert outcome.validation_codes == (RuntimeBundleValidationCode.REQUEST_SHAPE_INVALID,)
    assert outcome.decision is RuntimeBundleBuildDecision.REJECTED


def test_optional_member_is_gated_exactly_like_a_required_one() -> None:
    outcome = evaluate_runtime_bundle_build(
        _request(
            source_members=(
                _source_member(RuntimeBundleMemberPurpose.CATALOG),
                _source_member(RuntimeBundleMemberPurpose.KNOWLEDGE, required=False, revocation_unresolved=True),
            )
        )
    )

    assert outcome.decision is RuntimeBundleBuildDecision.REJECTED
    assert RuntimeBundleRejectionReason.MEMBER_REVOCATION_UNRESOLVED in outcome.rejection_reasons


def test_artifact_members_are_gated_on_their_own_axes() -> None:
    """Artifact members are not source snapshots, so #362's policy does not cover them."""
    cases = (
        ({"observed_environment": "production"}, RuntimeBundleRejectionReason.MEMBER_ENVIRONMENT_MISMATCH),
        ({"approval_effective": False}, RuntimeBundleRejectionReason.MEMBER_APPROVAL_NOT_EFFECTIVE),
        ({"approval_expired": True}, RuntimeBundleRejectionReason.MEMBER_APPROVAL_EXPIRED),
        ({"revocation_unresolved": True}, RuntimeBundleRejectionReason.MEMBER_REVOCATION_UNRESOLVED),
    )
    for overrides, expected in cases:
        outcome = evaluate_runtime_bundle_build(
            _request(artifact_members=(_artifact_member(RuntimeBundleArtifactKind.CANDIDATE_INDEX, **overrides),))
        )

        assert outcome.decision is RuntimeBundleBuildDecision.REJECTED, overrides
        assert expected in outcome.rejection_reasons, overrides
        # The shared snapshot policy must not be consulted for an artifact member.
        assert outcome.snapshot_use_failure_codes == (), overrides


def test_missing_rule_guideline_and_safety_members_block_readiness_but_allow_building() -> None:
    outcome = evaluate_runtime_bundle_build(_request())

    assert outcome.decision is RuntimeBundleBuildDecision.BUILDABLE
    assert outcome.readiness_blockers == (
        RuntimeBundleReadinessBlocker.KNOWLEDGE_INDEX_MEMBER_ABSENT,
        RuntimeBundleReadinessBlocker.RULE_SET_MEMBER_ABSENT,
        RuntimeBundleReadinessBlocker.GUIDELINE_SET_MEMBER_ABSENT,
        RuntimeBundleReadinessBlocker.SAFETY_POLICY_MEMBER_ABSENT,
    )


def test_complete_member_set_reports_no_readiness_blocker() -> None:
    outcome = evaluate_runtime_bundle_build(
        _request(
            artifact_members=tuple(_artifact_member(kind) for kind in RuntimeBundleArtifactKind),
        )
    )

    assert outcome.decision is RuntimeBundleBuildDecision.BUILDABLE
    assert outcome.readiness_blockers == ()


def test_worker_compatibility_is_reported_as_deferred_with_its_block_code() -> None:
    for outcome in (
        evaluate_runtime_bundle_build(_request()),
        evaluate_runtime_bundle_build(_request(artifact_members=())),
    ):
        assert outcome.deferred_checks == (RuntimeBundleDeferredCheck.WORKER_BUNDLE_COMPATIBILITY,)
        assert outcome.deferred_check_block_codes == (WORKER_COMPATIBILITY_BLOCK_CODE,)


def test_structural_failures_end_as_typed_validation_errors() -> None:
    cases = (
        (
            _request(source_members=()),
            RuntimeBundleValidationCode.AT_LEAST_ONE_SOURCE_MEMBER_REQUIRED,
        ),
        (
            _request(
                source_members=(_source_member(RuntimeBundleMemberPurpose.CATALOG, source_snapshot_id="not-a-uuid"),)
            ),
            RuntimeBundleValidationCode.IDENTIFIER_NOT_CANONICAL_UUID,
        ),
        (
            _request(source_members=(_source_member(RuntimeBundleMemberPurpose.CATALOG, canonical_checksum="short"),)),
            RuntimeBundleValidationCode.SHA256_NOT_CANONICAL,
        ),
        (
            _request(execution_manifest=replace(_manifest(), git_commit_sha="ZZZ")),
            RuntimeBundleValidationCode.GIT_COMMIT_SHA_NOT_CANONICAL,
        ),
        (
            _request(bundle_key="   "),
            RuntimeBundleValidationCode.TEXT_BLANK,
        ),
        (
            _request(
                artifact_members=(_artifact_member(RuntimeBundleArtifactKind.CANDIDATE_INDEX, manifest_hash=None),)
            ),
            RuntimeBundleValidationCode.ARTIFACT_MANIFEST_HASH_REQUIRED,
        ),
        (
            _request(
                artifact_members=(
                    _artifact_member(RuntimeBundleArtifactKind.CANDIDATE_INDEX),
                    _artifact_member(RuntimeBundleArtifactKind.RULE_SET, manifest_hash=_hash("f")),
                )
            ),
            RuntimeBundleValidationCode.ARTIFACT_MANIFEST_HASH_FORBIDDEN,
        ),
        (
            _request(
                artifact_members=(
                    _artifact_member(RuntimeBundleArtifactKind.CANDIDATE_INDEX),
                    _artifact_member(RuntimeBundleArtifactKind.CANDIDATE_INDEX),
                )
            ),
            RuntimeBundleValidationCode.DUPLICATE_ARTIFACT_MEMBER,
        ),
    )
    for request, expected in cases:
        outcome = evaluate_runtime_bundle_build(request)

        assert outcome.execution_status is RuntimeBundleBuildExecutionStatus.VALIDATION_ERROR, expected
        assert outcome.decision is RuntimeBundleBuildDecision.REJECTED, expected
        assert expected in outcome.validation_codes, expected
        assert outcome.bundle_manifest_hash is None, expected


def test_duplicate_source_member_is_rejected() -> None:
    member = _source_member(RuntimeBundleMemberPurpose.CATALOG)
    outcome = evaluate_runtime_bundle_build(_request(source_members=(member, member)))

    assert RuntimeBundleValidationCode.DUPLICATE_SOURCE_MEMBER in outcome.validation_codes


def test_same_snapshot_under_two_purposes_is_allowed() -> None:
    snapshot_id = str(uuid4())
    outcome = evaluate_runtime_bundle_build(
        _request(
            source_members=(
                _source_member(RuntimeBundleMemberPurpose.CATALOG, source_snapshot_id=snapshot_id),
                _source_member(RuntimeBundleMemberPurpose.CANDIDATE_INDEX_INPUT, source_snapshot_id=snapshot_id),
            )
        )
    )

    assert outcome.decision is RuntimeBundleBuildDecision.BUILDABLE


def test_unshaped_request_never_raises() -> None:
    outcome = evaluate_runtime_bundle_build(_request(source_members=("not-a-member",)))

    assert outcome.validation_codes == (RuntimeBundleValidationCode.REQUEST_SHAPE_INVALID,)
    assert outcome.decision is RuntimeBundleBuildDecision.REJECTED
