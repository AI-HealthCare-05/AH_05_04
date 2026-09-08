from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, cast

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.natural_language_retrieval_gold_review import (
    GOLD_REVIEW_JSON_PATH,
    GOLD_REVIEW_MARKDOWN_PATH,
    build_gold_review_packet,
    render_gold_review_markdown,
)

REPOSITORY_ROOT = Path(__file__).parents[3]
EVALS_ROOT = REPOSITORY_ROOT / "evals"
NEGATIVE_TYPES = {
    "SAME_FAMILY_DIFFERENT_ATTRIBUTE",
    "SAME_TOPIC_DIFFERENT_FAMILY",
    "LEXICAL_OVERLAP_UNSUPPORTED",
    "CROSS_TOPIC_OVERLAP",
}
FORBIDDEN_REVIEW_RESULT_KEYS = {
    "approved_at",
    "approved_by",
    "decision",
    "reviewed_at",
    "reviewed_by",
    "reviewer",
    "team_gold_status",
}


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for child in value.values() for key in _all_keys(child)}
    if isinstance(value, list):
        return {key for child in value for key in _all_keys(child)}
    return set()


def _packet() -> dict[str, Any]:
    return cast(dict[str, Any], build_gold_review_packet(EVALS_ROOT))


def test_gold_review_packet_joins_the_exact_public_dev_review_surface() -> None:
    packet = _packet()
    items = packet["items"]

    assert packet["format_id"] == "issue-273.gold-review-packet"
    assert packet["format_version"] == "1.0.0"
    assert packet["contract_status"] == "REPOSITORY_LOCAL_NON_RUNTIME_PROJECTION"
    assert packet["purpose"] == "PREPARATION_ONLY"
    assert packet["issue"] == "#273"
    assert packet["dataset_ref"] == "rag-natural-language-retrieval-dev@1.0.0"
    assert packet["source_dataset_status"] == "DRAFT"
    assert packet["counts"] == {
        "case_count": 60,
        "gold_count": 20,
        "hard_negative_count": 80,
        "transform_origin_count": 20,
    }
    assert len(items) == 20
    assert sum(len(item["queries"]) for item in items) == 60
    assert sum(len(item["hard_negatives"]) for item in items) == 80
    assert [item["queries"][0]["case_id"] for item in items] == [
        f"rag-nlr-dev-{number:03d}" for number in range(1, 61, 3)
    ]

    seen_evidence_ids: set[str] = set()
    for item in items:
        assert item["product_code"] == item["transform_origin"]
        assert len(item["queries"]) == 3
        assert [query["case_id"] for query in item["queries"]] == sorted(query["case_id"] for query in item["queries"])
        assert all(re.search(r"[가-힣]", query["query"]) for query in item["queries"])
        assert item["gold"]["locator"].startswith("$.records[")
        assert item["gold"]["stable_key"].startswith("SYNTHETIC_NLR_CHUNK_")
        assert {negative["negative_type"] for negative in item["hard_negatives"]} == NEGATIVE_TYPES
        evidence_ids = {
            item["gold"]["evidence_ref_id"],
            *(negative["evidence_ref_id"] for negative in item["hard_negatives"]),
        }
        assert len(evidence_ids) == 5
        assert not seen_evidence_ids.intersection(evidence_ids)
        seen_evidence_ids.update(evidence_ids)

    assert len(seen_evidence_ids) == 100
    assert not FORBIDDEN_REVIEW_RESULT_KEYS.intersection(_all_keys(packet))
    assert packet["packet_sha256"] == canonical_sha256(
        packet,
        excluded_top_level_keys=frozenset({"packet_sha256"}),
    )


def test_gold_review_packet_binds_every_consumed_source_to_committed_bytes() -> None:
    packet = _packet()
    source_artifacts = packet["source_artifacts"]

    assert set(source_artifacts) == {
        "authoring_identity",
        "dataset_manifest",
        "evaluation_labels",
        "evidence_mapping",
        "retrieval_index",
    }
    for source in source_artifacts.values():
        source_path = EVALS_ROOT / source["path"]
        assert source_path.is_file()
        assert source["sha256"] == sha256_hex(source_path.read_bytes())

    manifest = json.loads((EVALS_ROOT / source_artifacts["dataset_manifest"]["path"]).read_bytes())
    assert packet["source_dataset_manifest_sha256"] == manifest["manifest_sha256"]
    assert manifest["status"] == "DRAFT"
    assert manifest["frozen_at"] is None
    assert manifest["partition_counts"]["HOLDOUT"] == 0


def test_gold_review_packet_rejects_an_invalid_dataset_manifest_self_hash(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    shutil.copytree(EVALS_ROOT, evals_root)
    manifest_path = evals_root / "retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["manifest_sha256"] = "0" * 64
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(RuntimeError, match="Dataset manifest self hash mismatch"):
        build_gold_review_packet(evals_root)


def test_gold_review_packet_rejects_a_negative_bound_to_another_origin(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    shutil.copytree(EVALS_ROOT, evals_root)
    labels_path = (
        evals_root / "retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/evaluation-labels.json"
    )
    index_path = (
        evals_root / "retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json"
    )
    mapping_path = evals_root / "retrieval/evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json"
    manifest_path = evals_root / "retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json"

    labels = json.loads(labels_path.read_bytes())
    negative = next(label for label in labels["labels"] if label["record_kind"] == "HARD_NEGATIVE")
    negative["adversarial_for_transform_origin"] = "NLR-NOT-THE-TARGET"
    labels_path.write_bytes(canonical_json_bytes(labels))

    index = json.loads(index_path.read_bytes())
    index["evaluation_label_ref"]["sha256"] = sha256_hex(labels_path.read_bytes())
    index_path.write_bytes(canonical_json_bytes(index))
    index_sha256 = sha256_hex(index_path.read_bytes())

    mapping = json.loads(mapping_path.read_bytes())
    for entry in mapping["entries"]:
        entry["content_sha256"] = index_sha256
        entry["fixture_record_ref"]["sha256"] = index_sha256
    mapping["manifest_sha256"] = canonical_sha256(
        mapping,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    mapping_path.write_bytes(canonical_json_bytes(mapping))

    manifest = json.loads(manifest_path.read_bytes())
    manifest["evidence_mapping_manifest_sha256"] = mapping["manifest_sha256"]
    manifest["manifest_sha256"] = canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(RuntimeError, match="adversarial origin mismatch"):
        build_gold_review_packet(evals_root)


def test_gold_review_packet_rejects_a_gold_locator_that_points_to_another_record(tmp_path: Path) -> None:
    evals_root = tmp_path / "evals"
    shutil.copytree(EVALS_ROOT, evals_root)
    mapping_path = evals_root / "retrieval/evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json"
    manifest_path = evals_root / "retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json"
    mapping = json.loads(mapping_path.read_bytes())
    gold_entry = next(entry for entry in mapping["entries"] if entry["locator"].startswith("$.records["))
    gold_entry["locator"] = "$.records[0]"
    mapping["manifest_sha256"] = canonical_sha256(
        mapping,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    mapping_path.write_bytes(canonical_json_bytes(mapping))

    manifest = json.loads(manifest_path.read_bytes())
    manifest["evidence_mapping_manifest_sha256"] = mapping["manifest_sha256"]
    manifest["manifest_sha256"] = canonical_sha256(
        manifest,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(RuntimeError, match="locator does not resolve to its Gold record"):
        build_gold_review_packet(evals_root)


def test_gold_review_markdown_is_a_complete_korean_human_review_projection() -> None:
    packet = _packet()
    markdown = render_gold_review_markdown(packet)

    assert markdown.startswith("# Issue #273 DEV Gold Review Packet\n")
    assert "준비 전용(PREPARATION_ONLY)" in markdown
    assert "GitHub Pull Request review event" in markdown
    assert "PR #316의 승인은 Gold review 증빙으로 재사용하지 않습니다." in markdown
    assert "Gold review result: REVIEWED" in markdown
    assert "Gold review result: APPROVED" in markdown
    assert "pull_request_review_id" not in markdown
    assert "GitHub API에서 실제 review ID, actor, submitted timestamp를 수집" in markdown
    assert "review_commit_oid" in markdown
    assert "20/20" in markdown
    assert "REVIEWED event actor는 `EVALUATION_REVIEWER`" in markdown
    assert "APPROVED event actor는 `DATASET_CUSTODIAN`" in markdown
    assert "작성자·REVIEWED actor·APPROVED actor는 서로 다른 실제 사람" in markdown
    assert "APPROVED event를 요청하기 전에 Issue와 PR에 별도 승인 담당자를 명시" in markdown
    assert "Issue #278" in markdown
    for criterion in (
        "세 질문의 의도를 모두 충족",
        "최소 단일 Evidence",
        "false negative",
        "합성 데이터",
        "표현 변형",
        "OTC 추천·상호작용 질문이 포함되지 않음",
    ):
        assert criterion in markdown
    for item in packet["items"]:
        assert f"## {item['transform_origin']}" in markdown
        assert item["gold"]["statement"] in markdown
        for query in item["queries"]:
            assert query["query"] in markdown
        for negative in item["hard_negatives"]:
            assert negative["negative_type"] in markdown
            assert negative["statement"] in markdown


def test_committed_gold_review_artifacts_are_exact_deterministic_projections() -> None:
    packet = _packet()

    assert (REPOSITORY_ROOT / GOLD_REVIEW_JSON_PATH).read_bytes() == canonical_json_bytes(packet)
    assert (REPOSITORY_ROOT / GOLD_REVIEW_MARKDOWN_PATH).read_text(encoding="utf-8") == (
        render_gold_review_markdown(packet)
    )
    assert Counter(item["topic"] for item in packet["items"]) == {
        "TOPIC_MEDICATION_INFORMATION": 4,
        "TOPIC_PRECAUTIONS": 4,
        "TOPIC_LIFESTYLE_MANAGEMENT": 4,
        "TOPIC_STORAGE": 4,
        "TOPIC_MISSED_DOSE": 4,
    }
