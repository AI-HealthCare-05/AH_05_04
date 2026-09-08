from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.natural_language_retrieval_dataset_approval import (
    DATASET_APPROVAL_JSON_PATH,
    DATASET_APPROVAL_MARKDOWN_PATH,
    build_dataset_approval_request,
    render_dataset_approval_markdown,
)
from ai_worker.tasks.evaluation.natural_language_retrieval_dev_authoring import (
    DATASET_MANIFEST_PATH,
    REVIEW_EVIDENCE_PATH,
)

REPOSITORY_ROOT = Path(__file__).parents[3]
EVALS_ROOT = REPOSITORY_ROOT / "evals"


def _packet() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((REPOSITORY_ROOT / DATASET_APPROVAL_JSON_PATH).read_bytes()))


def _copy_evals(tmp_path: Path) -> Path:
    destination = tmp_path / "evals"
    shutil.copytree(EVALS_ROOT, destination)
    return destination


def test_approval_request_binds_the_exact_reviewed_dev_dataset() -> None:
    packet = _packet()

    assert packet["format_id"] == "issue-273.dataset-approval-request"
    assert packet["format_version"] == "1.0.0"
    assert packet["contract_status"] == "REPOSITORY_LOCAL_NON_RUNTIME_PROJECTION"
    assert packet["purpose"] == "PREPARATION_ONLY"
    assert packet["issue"] == "#273"
    assert packet["dataset_ref"] == "rag-natural-language-retrieval-dev@1.0.0"
    assert packet["dataset_status"] == "DRAFT"
    assert packet["team_gold_status"] == "REVIEWED"
    assert packet["counts"] == {
        "case_count": 60,
        "gold_count": 20,
        "hard_negative_count": 80,
        "holdout_count": 0,
        "transform_origin_count": 20,
    }
    assert packet["implementation_owner"] == {
        "actor_id": "ceohwj",
        "namespace": "GITHUB_LOGIN",
        "role": "EVALUATION_IMPLEMENTER",
    }
    assert packet["gold_reviewer"] == {
        "actor_id": "hazelnutflavoured",
        "namespace": "GITHUB_LOGIN",
        "role": "EVALUATION_REVIEWER",
    }
    assert packet["requested_dataset_custodian"] == {
        "actor_id": "phina-io",
        "namespace": "GITHUB_LOGIN",
        "role": "DATASET_CUSTODIAN",
    }
    assert packet["approval_recorded"] is False
    assert packet["freeze_recorded"] is False
    assert packet["approval_event"] is None
    assert len(packet["case_resources"]) == 60
    assert {resource["partition"] for resource in packet["case_resources"]} == {"DEV"}
    assert packet["request_sha256"] == canonical_sha256(
        packet,
        excluded_top_level_keys=frozenset({"request_sha256"}),
    )


def test_approval_request_binds_review_evidence_and_every_case_byte() -> None:
    packet = cast(dict[str, Any], build_dataset_approval_request(EVALS_ROOT))
    source_artifacts = packet["source_artifacts"]

    for source in source_artifacts.values():
        source_path = EVALS_ROOT / source["path"]
        assert source_path.is_file()
        assert source["sha256"] == sha256_hex(source_path.read_bytes())
    for resource in packet["case_resources"]:
        case_path = EVALS_ROOT / resource["path"]
        assert resource["sha256"] == sha256_hex(case_path.read_bytes())

    review_evidence = json.loads((EVALS_ROOT / REVIEW_EVIDENCE_PATH).read_bytes())
    assert packet["gold_review_event"] == {
        "commit_sha": review_evidence["commit_sha"],
        "evidence_id": review_evidence["evidence_id"],
        "evidence_sha256": sha256_hex((EVALS_ROOT / REVIEW_EVIDENCE_PATH).read_bytes()),
        "packet_sha256": review_evidence["packet_sha256"],
        "review_id": review_evidence["review_id"],
        "review_state": review_evidence["review_state"],
        "review_submitted_at": review_evidence["review_submitted_at"],
        "review_url": review_evidence["review_url"],
        "reviewed_dataset_manifest_sha256": review_evidence["reviewed_dataset_manifest_sha256"],
        "reviewed_origins": "20/20",
    }


def test_approval_request_rejects_prefilled_approval(tmp_path: Path) -> None:
    evals_root = _copy_evals(tmp_path)
    manifest_path = evals_root / DATASET_MANIFEST_PATH
    manifest = json.loads(manifest_path.read_bytes())
    manifest["review_provenance"]["approved_by"] = {
        "actor_id": "phina-io",
        "namespace": "GITHUB_LOGIN",
        "role": "DATASET_CUSTODIAN",
    }
    manifest["review_provenance"]["approved_at"] = "2026-09-08T08:00:00.000000Z"
    manifest["review_provenance"]["team_gold_status"] = "APPROVED"
    manifest["manifest_sha256"] = canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(RuntimeError, match="requires REVIEWED provenance without approval"):
        build_dataset_approval_request(evals_root)


def test_approval_request_rejects_holdout_content(tmp_path: Path) -> None:
    evals_root = _copy_evals(tmp_path)
    manifest_path = evals_root / DATASET_MANIFEST_PATH
    manifest = json.loads(manifest_path.read_bytes())
    manifest["partition_counts"]["HOLDOUT"] = 1
    manifest["manifest_sha256"] = canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(RuntimeError, match="cannot include HOLDOUT"):
        build_dataset_approval_request(evals_root)


def test_approval_request_rejects_unbound_gold_review_evidence(tmp_path: Path) -> None:
    evals_root = _copy_evals(tmp_path)
    evidence_path = evals_root / REVIEW_EVIDENCE_PATH
    evidence = json.loads(evidence_path.read_bytes())
    evidence["review_url"] += "#tampered"
    evidence_path.write_bytes(canonical_json_bytes(evidence))

    with pytest.raises(RuntimeError, match="not bound to the Gold review evidence"):
        build_dataset_approval_request(evals_root)


def test_approval_request_rejects_a_case_not_bound_to_the_same_review(tmp_path: Path) -> None:
    evals_root = _copy_evals(tmp_path)
    manifest_path = evals_root / DATASET_MANIFEST_PATH
    manifest = json.loads(manifest_path.read_bytes())
    resource = manifest["case_resources"][0]
    case_path = evals_root / resource["path"]
    case = json.loads(case_path.read_bytes())
    case["review_provenance"]["evidence_review_refs"][0]["hash"] = "0" * 64
    case_path.write_bytes(canonical_json_bytes(case))
    resource["sha256"] = sha256_hex(case_path.read_bytes())
    manifest["resource_set_hash"] = canonical_sha256(manifest["case_resources"])
    manifest["manifest_sha256"] = canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(RuntimeError, match="not bound to the Gold review evidence"):
        build_dataset_approval_request(evals_root)


def test_approval_markdown_defines_the_real_approval_and_follow_up_boundary() -> None:
    packet = _packet()
    markdown = render_dataset_approval_markdown(packet)

    assert markdown.startswith("# Issue #273 Dataset Approval Request\n")
    assert "준비 전용(PREPARATION_ONLY)" in markdown
    assert "송은영" in markdown
    assert "`@phina-io`" in markdown
    assert "권가빈 (`@hazelnutflavoured`, `EVALUATION_REVIEWER`)" in markdown
    assert "GitHub의 Approve 기능" in markdown
    assert "Gold review result: APPROVED" in markdown
    assert f"approval_request_sha256: {packet['request_sha256']}" in markdown
    assert f"dataset_manifest_sha256: {packet['source_dataset_manifest_sha256']}" in markdown
    assert f"gold_review_evidence_sha256: {packet['gold_review_event']['evidence_sha256']}" in markdown
    assert "review_commit_oid: <approved commit OID>" in markdown
    assert "GitHub가 실제로 검토한 PR HEAD의 정확한 40자리 commit OID" in markdown
    assert "pull_request_review_id" not in markdown
    assert "별도 provenance 기록 PR" in markdown
    assert "현재 Dataset은 계속 `DRAFT`" in markdown
    assert "HOLDOUT 40개를 만들거나 Freeze하지 않습니다" in markdown
    assert "Baseline을 실행하지 않습니다" in markdown


def test_committed_approval_request_artifacts_are_exact_deterministic_projections() -> None:
    packet = cast(dict[str, Any], build_dataset_approval_request(EVALS_ROOT))

    assert (REPOSITORY_ROOT / DATASET_APPROVAL_JSON_PATH).read_bytes() == canonical_json_bytes(packet)
    assert (REPOSITORY_ROOT / DATASET_APPROVAL_MARKDOWN_PATH).read_text(encoding="utf-8") == (
        render_dataset_approval_markdown(packet)
    )
