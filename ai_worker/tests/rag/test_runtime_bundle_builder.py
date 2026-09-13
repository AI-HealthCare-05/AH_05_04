"""RAG-12A Runtime Bundle build kernel determinism and fail-closed rejection (Issue #175)."""

from dataclasses import MISSING, replace
from uuid import uuid4

from ai_worker.tasks.rag.catalog.types import CatalogFreshnessStatus, CatalogVerificationStatus
from ai_worker.tasks.rag.runtime_bundle_builder import (
    RUNTIME_BUNDLE_MANIFEST_PROJECTION_VERSION,
    WORKER_COMPATIBILITY_BLOCK_CODE,
    MedicationCatalogBinding,
    RuntimeBundleArtifactKind,
    RuntimeBundleArtifactMemberInput,
    RuntimeBundleBuildDecision,
    RuntimeBundleBuildExecutionStatus,
    RuntimeBundleBuildRequest,
    RuntimeBundleCanonicalConfiguration,
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
    runtime_bundle_configuration_from_request,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import (
    SnapshotUseFailureCode,
    SnapshotVerificationStatus,
)

_ENVIRONMENT = "local"
_CATALOG_VERSION = "catalog-1.0.0"


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


def _catalog(**overrides: object) -> MedicationCatalogBinding:
    catalog = MedicationCatalogBinding(
        catalog_version=_CATALOG_VERSION,
        catalog_manifest_hash=_hash("9"),
        verification_status=CatalogVerificationStatus.APPROVED,
        freshness_status=CatalogFreshnessStatus.CURRENT,
        is_complete=True,
        source_snapshot_ids=(),
    )
    return replace(catalog, **overrides)  # type: ignore[arg-type]


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
        verification_status=SnapshotVerificationStatus.CURRENT,
        rejected_record_count=0,
        publication_approval_passed=True,
        freshness_eligible=True,
        provenance_valid=True,
        approval_expired=False,
        revocation_unresolved=False,
        scope_allowed=True,
    )
    return replace(member, **overrides)  # type: ignore[arg-type]


def _artifact_member(
    kind: RuntimeBundleArtifactKind = RuntimeBundleArtifactKind.CANDIDATE_INDEX,
    **overrides: object,
) -> RuntimeBundleArtifactMemberInput:
    hashed = kind in {RuntimeBundleArtifactKind.CANDIDATE_INDEX, RuntimeBundleArtifactKind.KNOWLEDGE_INDEX}
    candidate = kind is RuntimeBundleArtifactKind.CANDIDATE_INDEX
    member = RuntimeBundleArtifactMemberInput(
        artifact_kind=kind,
        artifact_ref=f"artifact:{kind.value.lower()}",
        artifact_version="1.0.0",
        observed_environment=_ENVIRONMENT,
        approval_effective=True,
        approval_expired=False,
        revocation_unresolved=False,
        manifest_hash=_hash("e") if hashed else None,
        catalog_version=_CATALOG_VERSION if candidate else None,
        catalog_manifest_hash=_hash("9") if candidate else None,
    )
    return replace(member, **overrides)  # type: ignore[arg-type]


def _request(
    *,
    source_members: tuple[RuntimeBundleSourceMemberInput, ...] | None = None,
    catalog: MedicationCatalogBinding | None = None,
    **overrides: object,
) -> RuntimeBundleBuildRequest:
    """Build a coherent request: the catalog binding must cover the pinned CATALOG members.

    The default catalog is derived from whichever CATALOG members end up in the request, so a test
    that swaps members does not have to restate the binding.
    """
    # `is None` on purpose: an explicitly empty tuple is a test case, not an unset argument.
    members = (
        source_members
        if source_members is not None
        else (
            _source_member(RuntimeBundleMemberPurpose.CATALOG),
            _source_member(RuntimeBundleMemberPurpose.KNOWLEDGE),
        )
    )
    # Tolerates deliberately malformed members: shape rejection is the kernel's job, not the
    # fixture's, so the fixture must still be able to build the request.
    catalog_snapshot_ids = tuple(
        member.source_snapshot_id
        for member in members
        if isinstance(member, RuntimeBundleSourceMemberInput)
        and member.source_purpose is RuntimeBundleMemberPurpose.CATALOG
    )
    request = RuntimeBundleBuildRequest(
        bundle_key="local-rag-runtime",
        bundle_version="2026.09.10-001",
        environment_code=_ENVIRONMENT,
        execution_manifest=_manifest(),
        catalog=catalog or _catalog(source_snapshot_ids=catalog_snapshot_ids or (str(uuid4()),)),
        source_members=members,
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


def test_environment_code_is_part_of_content_identity() -> None:
    request = _request()
    other_environment = replace(
        request,
        environment_code="test",
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
    baseline = canonical_runtime_bundle_manifest_hash(
        runtime_bundle_configuration_from_request(request, execution_manifest_hash=manifest_hash)
    )
    observed_stale = replace(
        request,
        source_members=(
            replace(request.source_members[0], freshness_eligible=False),
            request.source_members[1],
        ),
    )

    assert (
        canonical_runtime_bundle_manifest_hash(
            runtime_bundle_configuration_from_request(observed_stale, execution_manifest_hash=manifest_hash)
        )
        == baseline
    )
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
    # Deliberately the wrong type: the kernel must answer, not raise.
    outcome = evaluate_runtime_bundle_build(_request(source_members=("not-a-member",)))  # type: ignore[arg-type]

    assert outcome.validation_codes == (RuntimeBundleValidationCode.REQUEST_SHAPE_INVALID,)
    assert outcome.decision is RuntimeBundleBuildDecision.REJECTED


def test_catalog_must_be_approved_current_and_complete() -> None:
    """A CATALOG-purpose source member is not evidence that the catalog itself was approved."""
    cases = (
        (
            {"verification_status": CatalogVerificationStatus.NOT_APPROVED},
            RuntimeBundleRejectionReason.CATALOG_NOT_APPROVED,
        ),
        ({"freshness_status": CatalogFreshnessStatus.STALE}, RuntimeBundleRejectionReason.CATALOG_STALE),
        ({"is_complete": False}, RuntimeBundleRejectionReason.CATALOG_PARTIAL),
    )
    for overrides, expected in cases:
        members = (
            _source_member(RuntimeBundleMemberPurpose.CATALOG),
            _source_member(RuntimeBundleMemberPurpose.KNOWLEDGE),
        )
        outcome = evaluate_runtime_bundle_build(
            _request(
                source_members=members,
                catalog=_catalog(source_snapshot_ids=(members[0].source_snapshot_id,), **overrides),
            )
        )

        assert outcome.decision is RuntimeBundleBuildDecision.REJECTED, overrides
        assert expected in outcome.rejection_reasons, overrides
        assert outcome.bundle_manifest_hash is None, overrides


def test_catalog_member_must_derive_from_the_pinned_catalog() -> None:
    """The pinned CATALOG snapshot must be one the catalog was actually built from."""
    members = (
        _source_member(RuntimeBundleMemberPurpose.CATALOG),
        _source_member(RuntimeBundleMemberPurpose.KNOWLEDGE),
    )
    outcome = evaluate_runtime_bundle_build(
        _request(
            source_members=members,
            # A different catalog's source refs.
            catalog=_catalog(source_snapshot_ids=(str(uuid4()),)),
        )
    )

    assert outcome.decision is RuntimeBundleBuildDecision.REJECTED
    assert RuntimeBundleRejectionReason.CATALOG_SOURCE_BINDING_INVALID in outcome.rejection_reasons


def test_candidate_index_built_from_another_catalog_is_rejected() -> None:
    for overrides in (
        {"catalog_version": "catalog-9.9.9"},
        {"catalog_manifest_hash": _hash("f")},
    ):
        outcome = evaluate_runtime_bundle_build(
            _request(artifact_members=(_artifact_member(RuntimeBundleArtifactKind.CANDIDATE_INDEX, **overrides),))
        )

        assert outcome.decision is RuntimeBundleBuildDecision.REJECTED, overrides
        assert RuntimeBundleRejectionReason.CANDIDATE_INDEX_CATALOG_MISMATCH in outcome.rejection_reasons, overrides


def test_candidate_index_must_declare_its_catalog_binding() -> None:
    outcome = evaluate_runtime_bundle_build(
        _request(
            artifact_members=(
                _artifact_member(
                    RuntimeBundleArtifactKind.CANDIDATE_INDEX,
                    catalog_version=None,
                    catalog_manifest_hash=None,
                ),
            )
        )
    )

    assert RuntimeBundleValidationCode.ARTIFACT_CATALOG_BINDING_REQUIRED in outcome.validation_codes


def test_non_candidate_artifact_must_not_declare_a_catalog_binding() -> None:
    outcome = evaluate_runtime_bundle_build(
        _request(
            artifact_members=(
                _artifact_member(RuntimeBundleArtifactKind.CANDIDATE_INDEX),
                _artifact_member(RuntimeBundleArtifactKind.RULE_SET, catalog_version=_CATALOG_VERSION),
            )
        )
    )

    assert RuntimeBundleValidationCode.ARTIFACT_CATALOG_BINDING_FORBIDDEN in outcome.validation_codes


def test_catalog_without_source_refs_is_a_validation_error() -> None:
    outcome = evaluate_runtime_bundle_build(_request(catalog=_catalog(source_snapshot_ids=())))

    assert RuntimeBundleValidationCode.CATALOG_SOURCE_REF_REQUIRED in outcome.validation_codes


def test_omitted_eligibility_evidence_cannot_be_constructed_as_permissive() -> None:
    """Every eligibility field is required, so "unspecified" can never read as approved."""
    required = {
        "verification_status",
        "rejected_record_count",
        "publication_approval_passed",
        "freshness_eligible",
        "provenance_valid",
        "approval_expired",
        "revocation_unresolved",
        "scope_allowed",
    }
    defaulted = {
        name
        for name, field in RuntimeBundleSourceMemberInput.__dataclass_fields__.items()
        if field.default is not MISSING or field.default_factory is not MISSING  # type: ignore[misc]
    }

    assert required.isdisjoint(defaulted)
    assert defaulted == {"required", "selected_for_operation"}


def test_configuration_hash_depends_only_on_the_persisted_configuration() -> None:
    """The hash input is the configuration alone, which is what makes the storage round trip work."""
    request = _request()
    outcome = evaluate_runtime_bundle_build(request)
    assert outcome.configuration is not None

    rebuilt = RuntimeBundleCanonicalConfiguration(
        environment_code=outcome.configuration.environment_code,
        execution_manifest_hash=outcome.configuration.execution_manifest_hash,
        catalog_version=outcome.configuration.catalog_version,
        catalog_manifest_hash=outcome.configuration.catalog_manifest_hash,
        # Reversed on purpose: order must not matter.
        source_members=tuple(reversed(outcome.configuration.source_members)),
        artifact_members=outcome.configuration.artifact_members,
    )

    assert canonical_runtime_bundle_manifest_hash(rebuilt) == outcome.bundle_manifest_hash


def test_artifact_version_is_part_of_content_identity() -> None:
    """Regression for the review finding: artifact_version entered the hash but was never stored."""
    request = _request()
    baseline = evaluate_runtime_bundle_build(request).bundle_manifest_hash
    changed = evaluate_runtime_bundle_build(
        _request(
            artifact_members=(_artifact_member(RuntimeBundleArtifactKind.CANDIDATE_INDEX, artifact_version="2.0.0"),)
        )
    )

    assert changed.bundle_manifest_hash != baseline
