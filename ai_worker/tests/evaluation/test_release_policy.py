from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.release_policy import (
    load_approved_release_policy,
    load_release_policy,
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
        ActorRef(namespace=approver_namespace, actor_id="approver-user", role=approver_role) if has_approval else None
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
    prov = _make_provenance(status=TeamGoldStatus.APPROVED)
    validate_release_review_provenance(prov)


def test_validate_release_review_provenance_rejects_draft_and_reviewed() -> None:
    for status in (TeamGoldStatus.DRAFT, TeamGoldStatus.REVIEWED):
        prov = _make_provenance(status=status, has_approval=False)
        with pytest.raises(EvaluationValidationError) as exc:
            validate_release_review_provenance(prov)
        assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID


def test_comparison_policy_schema_approval_invariants() -> None:
    dev_comp_path = EVALS_ROOT / "policies/dev-foundation-v1.comparison-policy.json"
    raw_comp = json.loads(dev_comp_path.read_bytes())

    # Proposer == Approver raises ValidationError by schema model validator
    invalid_comp_data = dict(raw_comp)
    invalid_comp_data["approved_by"] = invalid_comp_data["proposed_by"]
    with pytest.raises(ValidationError):
        ComparisonPolicy.model_validate(invalid_comp_data)


def test_load_approved_release_policy_rejects_unapproved_checked_in_artifacts() -> None:
    # Checked-in policies are DRAFT -> must fail closed
    with pytest.raises(EvaluationValidationError) as exc:
        load_approved_release_policy(
            EVALS_ROOT / "policies/rag-holdout-safety-v1.evaluation-policy.json",
            EVALS_ROOT / "profiles/rag-holdout-safety-v1.profile.json",
            EVALS_ROOT / "policies/rag-holdout-safety-v1.comparison-policy.json",
        )
    assert exc.value.code == EvaluationErrorCode.REVIEW_PROVENANCE_INVALID


def test_load_approved_release_policy_accepts_approved_policy_graph(tmp_path: Path) -> None:
    pol_path, prof_path, comp_path = _copy_policy_graph(tmp_path)
    prov = _make_provenance(status=TeamGoldStatus.APPROVED).model_dump(mode="json")

    prof = json.loads(prof_path.read_bytes())
    prof["review_provenance"] = prov
    prof["evaluation_profile_hash"] = canonical_sha256(
        prof, excluded_top_level_keys=frozenset({"evaluation_profile_hash"})
    )
    prof_path.write_bytes(canonical_json_bytes(prof))

    pol = json.loads(pol_path.read_bytes())
    pol["review_provenance"] = prov
    pol["evaluation_profile_ref"]["reference"]["hash"] = prof["evaluation_profile_hash"]
    pol["member_manifest_hash"] = canonical_sha256(
        {
            "members": [
                pol["evaluation_profile_ref"],
                pol["comparison_policy_ref"],
                *pol["required_partition_refs"],
                *pol["required_gate_refs"],
                pol["required_suite_refs"][0],
                pol["artifact_schema_set_ref"],
            ]
        }
    )
    pol["evaluation_policy_hash"] = canonical_sha256(pol, excluded_top_level_keys=frozenset({"evaluation_policy_hash"}))
    pol_path.write_bytes(canonical_json_bytes(pol))

    loaded_policy = load_approved_release_policy(pol_path, prof_path, comp_path)
    assert loaded_policy.evaluation_policy_ref.id == pol["evaluation_policy_id"]
