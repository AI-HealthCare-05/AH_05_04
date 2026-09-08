from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.natural_language_retrieval_dev_authoring import DATASET_MANIFEST_PATH
from ai_worker.tasks.evaluation.natural_language_retrieval_holdout_preparation import (
    HOLDOUT_PREPARATION_JSON_PATH,
    HOLDOUT_PREPARATION_MARKDOWN_PATH,
    build_holdout_freeze_preparation,
    render_holdout_freeze_preparation_markdown,
)

REPOSITORY_ROOT = Path(__file__).parents[3]
EVALS_ROOT = REPOSITORY_ROOT / "evals"


def _copy_manifest(tmp_path: Path) -> Path:
    destination = tmp_path / "evals"
    source = EVALS_ROOT / DATASET_MANIFEST_PATH
    target = destination / DATASET_MANIFEST_PATH
    target.parent.mkdir(parents=True)
    shutil.copyfile(source, target)
    return destination


def test_preparation_packet_defines_the_unstarted_protected_holdout_contract() -> None:
    packet = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))

    assert packet["format_id"] == "issue-273.holdout-freeze-preparation"
    assert packet["format_version"] == "1.0.0"
    assert packet["contract_status"] == "REPOSITORY_LOCAL_NON_RUNTIME_PROJECTION"
    assert packet["purpose"] == "PREPARATION_ONLY"
    assert packet["preparation_status"] == "PREPARATION_READY"
    assert packet["issue"] == "#273"
    assert packet["dataset"] == {
        "manifest_sha256": "b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2",
        "ref": "rag-natural-language-retrieval-dev@1.0.0",
        "status": "DRAFT",
    }
    assert packet["holdout_plan"] == {
        "authored_questions": 0,
        "planned_questions": 40,
        "topic_counts": [
            {"count": 8, "topic": "TOPIC_LIFESTYLE_MANAGEMENT"},
            {"count": 8, "topic": "TOPIC_MEDICATION_INFORMATION"},
            {"count": 8, "topic": "TOPIC_MISSED_DOSE"},
            {"count": 8, "topic": "TOPIC_PRECAUTIONS"},
            {"count": 8, "topic": "TOPIC_STORAGE"},
        ],
    }
    assert packet["leakage_axes"] == [
        "question_template",
        "source_segment",
        "medication_family",
        "transform_origin",
    ]
    assert packet["implementation_owner"]["actor_id"] == "ceohwj"
    assert packet["product_evaluation_reviewer"]["actor_id"] == "hazelnutflavoured"
    assert packet["requested_dataset_custodian"]["actor_id"] == "phina-io"
    assert packet["custodian_conflict_approver"]["actor_id"] == "Jye-rookie"
    assert packet["access_authorized"] is False
    assert packet["holdout_authored"] is False
    assert packet["freeze_recorded"] is False
    assert packet["actual_run_ref"] is None
    assert packet["release_eligible"] is False
    assert packet["preparation_sha256"] == canonical_sha256(
        packet,
        excluded_top_level_keys=frozenset({"preparation_sha256"}),
    )


def test_preparation_rejects_manifest_content_not_covered_by_its_self_hash(tmp_path: Path) -> None:
    evals_root = _copy_manifest(tmp_path)
    manifest_path = evals_root / DATASET_MANIFEST_PATH
    manifest = json.loads(manifest_path.read_bytes())
    manifest["partition_counts"]["AUTHORING"] = 1
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(RuntimeError, match="self hash mismatch"):
        build_holdout_freeze_preparation(evals_root)


def test_preparation_requires_the_recorded_dataset_custodian_approval(tmp_path: Path) -> None:
    evals_root = _copy_manifest(tmp_path)
    manifest_path = evals_root / DATASET_MANIFEST_PATH
    manifest = json.loads(manifest_path.read_bytes())
    manifest["review_provenance"]["team_gold_status"] = "REVIEWED"
    manifest["manifest_sha256"] = canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(RuntimeError, match="approved Dataset provenance"):
        build_holdout_freeze_preparation(evals_root)


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "query",
        "gold_body",
        "record_label",
        "fingerprint_value",
        "hmac_value",
        "key_material",
        "credential",
        "protected_path",
    ],
)
def test_public_preparation_projection_rejects_protected_values(forbidden_key: str) -> None:
    packet = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))
    packet["unexpected"] = {forbidden_key: "must-not-be-public"}

    with pytest.raises(RuntimeError, match="protected HOLDOUT field"):
        render_holdout_freeze_preparation_markdown(packet)


@pytest.mark.parametrize("field", ["access_authorized", "holdout_authored", "freeze_recorded"])
def test_public_preparation_projection_rejects_a_premature_state(field: str) -> None:
    packet = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))
    packet[field] = True

    with pytest.raises(RuntimeError, match="cannot record authorization, authoring, or Freeze"):
        render_holdout_freeze_preparation_markdown(packet)


def test_public_preparation_projection_rejects_role_or_axis_drift() -> None:
    packet = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))
    packet["requested_dataset_custodian"] = packet["implementation_owner"]

    with pytest.raises(RuntimeError, match="separate human roles"):
        render_holdout_freeze_preparation_markdown(packet)

    packet = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))
    packet["leakage_axes"] = [*packet["leakage_axes"], "transform_origin"]

    with pytest.raises(RuntimeError, match="four ordered leakage axes"):
        render_holdout_freeze_preparation_markdown(packet)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda packet: packet.update({"actual_run_ref": {"id": "forged-run"}}),
        lambda packet: packet.update({"release_eligible": True}),
        lambda packet: packet.update({"purpose": "FREEZE_COMPLETE"}),
        lambda packet: packet.update({"preparation_status": "FROZEN"}),
        lambda packet: packet.update({"contract_status": "SHARED_RUNTIME_CONTRACT"}),
        lambda packet: packet["dataset"].update({"status": "FROZEN"}),
    ],
)
def test_public_preparation_projection_rejects_rehashed_state_claims(mutation: Any) -> None:
    packet = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))
    mutation(packet)
    packet["preparation_sha256"] = canonical_sha256(
        packet,
        excluded_top_level_keys=frozenset({"preparation_sha256"}),
    )

    with pytest.raises(RuntimeError, match="exact public preparation state"):
        render_holdout_freeze_preparation_markdown(packet)


def test_public_preparation_projection_rejects_rehashed_extra_fields() -> None:
    packet = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))
    packet["notes"] = "arbitrary content"
    packet["preparation_sha256"] = canonical_sha256(
        packet,
        excluded_top_level_keys=frozenset({"preparation_sha256"}),
    )

    with pytest.raises(RuntimeError, match="exact public preparation fields"):
        render_holdout_freeze_preparation_markdown(packet)


def test_public_preparation_projection_rejects_rehashed_role_reassignment() -> None:
    packet = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))
    packet["requested_dataset_custodian"] = {
        "actor_id": "other-reviewer",
        "namespace": "GITHUB_LOGIN",
        "role": "DATASET_CUSTODIAN",
    }
    packet["preparation_sha256"] = canonical_sha256(
        packet,
        excluded_top_level_keys=frozenset({"preparation_sha256"}),
    )

    with pytest.raises(RuntimeError, match="fixed role assignment"):
        render_holdout_freeze_preparation_markdown(packet)


def test_each_preparation_build_owns_its_mutable_collections() -> None:
    first = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))
    expected_axes = [
        "question_template",
        "source_segment",
        "medication_family",
        "transform_origin",
    ]
    try:
        first["leakage_axes"].append("forged_axis")
        second = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))
        assert second["leakage_axes"] == expected_axes
    finally:
        first["leakage_axes"][:] = expected_axes


def test_preparation_markdown_states_when_each_follow_up_may_start() -> None:
    packet = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))
    markdown = render_holdout_freeze_preparation_markdown(packet)

    assert markdown.startswith("# Issue #273 HOLDOUT Freeze Preparation\n")
    assert "`PREPARATION_READY`는 접근 승인이나 Freeze 완료가 아닙니다" in markdown
    assert "접근 승인 event가 생성된 뒤에만 HOLDOUT 40개 작성을 시작" in markdown
    assert "준비 PR 병합 직후 전용 protected Retrieval Runner Issue를 생성" in markdown
    assert "네 leakage 축의 교집합이 모두 0" in markdown
    assert "#178의 실제 Retriever Adapter와 보호 Runner가 준비된 뒤에만" in markdown
    assert "OTC 범위는 Issue #278" in markdown


def test_committed_preparation_artifacts_are_exact_deterministic_projections() -> None:
    packet = cast(dict[str, Any], build_holdout_freeze_preparation(EVALS_ROOT))

    assert (REPOSITORY_ROOT / HOLDOUT_PREPARATION_JSON_PATH).read_bytes() == canonical_json_bytes(packet)
    assert (REPOSITORY_ROOT / HOLDOUT_PREPARATION_MARKDOWN_PATH).read_text(encoding="utf-8") == (
        render_holdout_freeze_preparation_markdown(packet)
    )
