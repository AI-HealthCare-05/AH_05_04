from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.release_policy import (
    load_approved_release_policy,
    load_release_policy,
    validate_comparison_policy_approval,
    validate_release_review_provenance,
)
from ai_worker.tasks.evaluation.schemas.common import (
    ActorNamespace,
    ActorRef,
    ActorRole,
    ExternalMedicalReviewStatus,
    ReviewProvenance,
    TeamGoldStatus,
)
from ai_worker.tasks.evaluation.schemas.policy import ComparisonPolicy

EVALS_ROOT = Path(__file__).parents[3] / "evals"


def _copy_policy_graph(tmp_path: Path) -> tuple[Path, Path, Path]:
    paths = (
        EVALS_ROOT / "policies/dev-foundation-v1.evaluation-policy.json",
        EVALS_ROOT / "profiles/dev-foundation-v1.profile.json",
        EVALS_ROOT / "policies/dev-foundation-v1.comparison-policy.json",
    )
    copied: list[Path] = []
    for source in paths:
        destination = tmp_path / source.name
        destination.write_bytes(source.read_bytes())
        copied.append(destination)
    return copied[0], copied[1], copied[2]


def test_release_policy_loader_binds_existing_policy_profile_and_comparison(tmp_path: Path) -> None:
    policy_path, profile_path, comparison_path = _copy_policy_graph(tmp_path)

    loaded = load_release_policy(policy_path, profile_path, comparison_path)

    assert loaded.evaluation_policy_ref.id == "rag-dev-foundation-policy"
    assert loaded.evaluation_profile_ref.id == "rag-dev-foundation-profile"
    assert loaded.comparison_policy_ref.id == "rag-dev-foundation-comparison"
    assert [item.metric_id for item in loaded.required_metrics] == []


def test_release_policy_loader_accepts_frozen_v1_2_policy_graph() -> None:
    loaded = load_release_policy(
        EVALS_ROOT / "policies/rag-holdout-safety-v1.evaluation-policy.json",
        EVALS_ROOT / "profiles/rag-holdout-safety-v1.profile.json",
        EVALS_ROOT / "policies/rag-holdout-safety-v1.comparison-policy.json",
    )

    assert loaded.evaluation_policy_ref.id == "rag-holdout-safety-policy"
    assert [item.value for item in loaded.required_partitions] == ["HOLDOUT", "SAFETY_REGRESSION"]


def test_release_policy_loader_rejects_exact_reference_hash_mismatch(tmp_path: Path) -> None:
    policy_path, profile_path, comparison_path = _copy_policy_graph(tmp_path)
    policy = json.loads(policy_path.read_bytes())
    policy["evaluation_profile_ref"]["reference"]["hash"] = "f" * 64
    policy["member_manifest_hash"] = canonical_sha256(
        {
            "members": [
                policy["evaluation_profile_ref"],
                policy["comparison_policy_ref"],
                *policy["required_partition_refs"],
                *policy["required_gate_refs"],
                *policy["required_suite_refs"],
                policy["artifact_schema_set_ref"],
            ]
        }
    )
    policy["evaluation_policy_hash"] = canonical_sha256(
        policy,
        excluded_top_level_keys=frozenset({"evaluation_policy_hash"}),
    )
    policy_path.write_bytes(canonical_json_bytes(policy))

    with pytest.raises(EvaluationValidationError) as caught:
        load_release_policy(policy_path, profile_path, comparison_path)

    assert caught.value.code is EvaluationErrorCode.HASH_MISMATCH


def _make_provenance(
    *,
    status: TeamGoldStatus = TeamGoldStatus.APPROVED,
    approver_role: ActorRole = ActorRole.PRODUCT_SAFETY_REVIEWER,
    approver_namespace: ActorNamespace = ActorNamespace.GITHUB_LOGIN,
    has_approval: bool = True,
) -> ReviewProvenance:
    approver = (
        ActorRef(namespace=approver_namespace, actor_id="approver-user", role=approver_role)
        if has_approval
        else None
    )
    return ReviewProvenance(
        authored_by=ActorRef(
            namespace=ActorNamespace.GITHUB_LOGIN,
            actor_id="author-user",
            role=ActorRole.EVALUATION_IMPLEMENTER,
        ),
        reviewed_by=ActorRef(
            namespace=ActorNamespace.GITHUB_LOGIN,
            actor_id="reviewer-user",
            role=ActorRole.PRODUCT_SAFETY_REVIEWER,
        ),
        approved_by=approver,
        authored_at="2026-09-10T00:00:00.000000Z",
        reviewed_at="2026-09-11T00:00:00.000000Z",
        approved_at="2026-09-12T00:00:00.000000Z" if has_approval else None,
        team_gold_status=status,
        external_medical_review_status=ExternalMedicalReviewStatus.NOT_REQUESTED,
        external_medical_approval_receipt_ref=None,
        evidence_review_refs=(),
    )


def test_validate_release_review_provenance_accepted() -> None:
    prov = _make_provenance(status=TeamGoldStatus.APPROVED, approver_role=ActorRole.PRODUCT_SAFETY_REVIEWER)
    validate_release_review_provenance(prov)


def test_validate_release_review_provenance_rejects_draft_and_reviewed() -> None:
    for status in (TeamGoldStatus.DRAFT, TeamGoldStatus.REVIEWED):
        prov = _make_provenance(status=status, has_approval=False)
        with pytest.raises(EvaluationValidationError) as exc:
            validate_release_review_provenance(prov)
        assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID


def test_validate_release_review_provenance_rejects_missing_approval() -> None:
    prov = ReviewProvenance.model_construct(
        authored_by=ActorRef(namespace=ActorNamespace.GITHUB_LOGIN, actor_id="a", role=ActorRole.EVALUATION_IMPLEMENTER),
        reviewed_by=ActorRef(namespace=ActorNamespace.GITHUB_LOGIN, actor_id="r", role=ActorRole.PRODUCT_SAFETY_REVIEWER),
        approved_by=None,
        authored_at="2026-09-10T00:00:00.000000Z",
        reviewed_at="2026-09-11T00:00:00.000000Z",
        approved_at=None,
        team_gold_status=TeamGoldStatus.APPROVED,
        external_medical_review_status=ExternalMedicalReviewStatus.NOT_REQUESTED,
        external_medical_approval_receipt_ref=None,
        evidence_review_refs=(),
    )
    with pytest.raises(EvaluationValidationError) as exc:
        validate_release_review_provenance(prov)
    assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID


def test_validate_release_review_provenance_rejects_non_product_safety_reviewer() -> None:
    prov = _make_provenance(
        status=TeamGoldStatus.APPROVED,
        approver_role=ActorRole.DATASET_CUSTODIAN,
    )
    with pytest.raises(EvaluationValidationError) as exc:
        validate_release_review_provenance(prov)
    assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID


def test_validate_release_review_provenance_rejects_system_namespace() -> None:
    prov = ReviewProvenance.model_construct(
        authored_by=ActorRef(namespace=ActorNamespace.GITHUB_LOGIN, actor_id="a", role=ActorRole.EVALUATION_IMPLEMENTER),
        reviewed_by=ActorRef(namespace=ActorNamespace.GITHUB_LOGIN, actor_id="r", role=ActorRole.PRODUCT_SAFETY_REVIEWER),
        approved_by=ActorRef.model_construct(namespace=ActorNamespace.SYSTEM, actor_id="s", role=ActorRole.PRODUCT_SAFETY_REVIEWER),
        authored_at="2026-09-10T00:00:00.000000Z",
        reviewed_at="2026-09-11T00:00:00.000000Z",
        approved_at="2026-09-12T00:00:00.000000Z",
        team_gold_status=TeamGoldStatus.APPROVED,
        external_medical_review_status=ExternalMedicalReviewStatus.NOT_REQUESTED,
        external_medical_approval_receipt_ref=None,
        evidence_review_refs=(),
    )
    with pytest.raises(EvaluationValidationError) as exc:
        validate_release_review_provenance(prov)
    assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID


def test_validate_comparison_policy_approval_accepted_and_rejected() -> None:
    # dev-foundation-v1 is approved by PRODUCT_SAFETY_REVIEWER
    dev_comp_path = EVALS_ROOT / "policies/dev-foundation-v1.comparison-policy.json"
    dev_comp = ComparisonPolicy.model_validate_json(dev_comp_path.read_bytes())
    validate_comparison_policy_approval(dev_comp)

    # rag-holdout-safety-v1 has approved_by.role == SYSTEM_VALIDATOR / namespace == SYSTEM -> reject!
    holdout_comp_path = EVALS_ROOT / "policies/rag-holdout-safety-v1.comparison-policy.json"
    holdout_comp = ComparisonPolicy.model_validate_json(holdout_comp_path.read_bytes())
    with pytest.raises(EvaluationValidationError) as exc:
        validate_comparison_policy_approval(holdout_comp)
    assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID

    # Approved by EVALUATION_IMPLEMENTER -> reject!
    implementer_comp = dev_comp.model_copy(
        update={
            "approved_by": ActorRef(
                namespace=ActorNamespace.GITHUB_LOGIN,
                actor_id="impl-lead",
                role=ActorRole.EVALUATION_IMPLEMENTER,
            ),
        }
    )
    with pytest.raises(EvaluationValidationError) as exc:
        validate_comparison_policy_approval(implementer_comp)
    assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID


def test_load_approved_release_policy_rejects_unapproved_checked_in_artifacts() -> None:
    # Checked-in policies are DRAFT/SYSTEM -> must fail closed
    with pytest.raises(EvaluationValidationError) as exc:
        load_approved_release_policy(
            EVALS_ROOT / "policies/rag-holdout-safety-v1.evaluation-policy.json",
            EVALS_ROOT / "profiles/rag-holdout-safety-v1.profile.json",
            EVALS_ROOT / "policies/rag-holdout-safety-v1.comparison-policy.json",
        )
    assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID
