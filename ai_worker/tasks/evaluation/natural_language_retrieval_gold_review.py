from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.natural_language_retrieval_dev_authoring import (
    AUTHORING_IDENTITY_PATH,
    DATASET_MANIFEST_PATH,
    EVALUATION_LABEL_PATH,
    EVIDENCE_MAPPING_PATH,
    INDEX_PATH,
    NEGATIVE_TYPES,
)

GOLD_REVIEW_JSON_PATH = "docs/validation/rag/issue-273/gold-review-packet.json"
GOLD_REVIEW_MARKDOWN_PATH = "docs/validation/rag/issue-273/gold-review-packet.md"

_EXPECTED_COUNTS = {
    "case_count": 60,
    "gold_count": 20,
    "hard_negative_count": 80,
    "transform_origin_count": 20,
}


def _read_json(evals_root: Path, relative_path: str) -> tuple[bytes, dict[str, Any]]:
    content = (evals_root / relative_path).read_bytes()
    value = json.loads(content)
    if not isinstance(value, dict):
        raise RuntimeError(f"Issue 273 Gold review source must be a JSON object: {relative_path}")
    return content, value


def _require_draft_provenance(value: dict[str, Any], *, source: str) -> None:
    provenance = value.get("review_provenance")
    if not isinstance(provenance, dict) or provenance.get("team_gold_status") != "DRAFT":
        raise RuntimeError(f"Issue 273 Gold review preparation requires DRAFT provenance: {source}")
    for field in ("reviewed_by", "reviewed_at", "approved_by", "approved_at"):
        if provenance.get(field) is not None:
            raise RuntimeError(f"Issue 273 Gold review preparation cannot record {field}: {source}")
    if provenance.get("evidence_review_refs") != []:
        raise RuntimeError(f"Issue 273 Gold review preparation requires empty evidence review refs: {source}")


def _source_ref(relative_path: str, content: bytes) -> dict[str, JsonValue]:
    return {"path": relative_path, "sha256": sha256_hex(content)}


def _require_self_hash(value: dict[str, Any], *, field: str, source: str) -> None:
    expected = canonical_sha256(value, excluded_top_level_keys=frozenset({field}))
    if value.get(field) != expected:
        raise RuntimeError(f"Issue 273 {source} self hash mismatch")


def _case_resources(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    resources = manifest.get("case_resources")
    if not isinstance(resources, list) or len(resources) != _EXPECTED_COUNTS["case_count"]:
        raise RuntimeError("Issue 273 Gold review preparation requires exactly 60 Case resources")
    if any(not isinstance(resource, dict) or resource.get("partition") != "DEV" for resource in resources):
        raise RuntimeError("Issue 273 Gold review preparation accepts DEV Case resources only")
    return cast(list[dict[str, Any]], resources)


def _load_cases(evals_root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for resource in _case_resources(manifest):
        path = cast(str, resource["path"])
        content, case = _read_json(evals_root, path)
        if sha256_hex(content) != resource.get("sha256"):
            raise RuntimeError(f"Issue 273 Case hash mismatch: {path}")
        if case.get("partition") != "DEV":
            raise RuntimeError(f"Issue 273 Gold review preparation accepts DEV Cases only: {path}")
        _require_draft_provenance(case, source=path)
        cases.append(case)
    return sorted(cases, key=lambda case: cast(str, case["case_id"]))


def _records_by_id(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = index.get("records")
    if not isinstance(records, list) or len(records) != 100:
        raise RuntimeError("Issue 273 Gold review preparation requires exactly 100 corpus records")
    result: dict[str, dict[str, Any]] = {}
    for value in records:
        if not isinstance(value, dict):
            raise RuntimeError("Issue 273 corpus records must be JSON objects")
        evidence_id = value.get("evidence_ref_id")
        if not isinstance(evidence_id, str) or evidence_id in result:
            raise RuntimeError("Issue 273 corpus evidence_ref_id values must be unique strings")
        result[evidence_id] = value
    return result


def _labels_by_origin(labels: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    values = labels.get("labels")
    if not isinstance(values, list) or len(values) != 100:
        raise RuntimeError("Issue 273 Gold review preparation requires exactly 100 evaluation labels")
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("transform_origin"), str):
            raise RuntimeError("Issue 273 evaluation labels require transform_origin")
        grouped[value["transform_origin"]].append(value)
    return dict(grouped)


def _gold_mapping_by_id(
    mapping: dict[str, Any],
    *,
    index_bytes: bytes,
    index: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    entries = mapping.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError("Issue 273 Evidence Mapping entries are required")
    result: dict[str, dict[str, Any]] = {}
    for value in entries:
        if not isinstance(value, dict):
            raise RuntimeError("Issue 273 Evidence Mapping entries must be JSON objects")
        locator = value.get("locator")
        if (
            value.get("evidence_type") == "KNOWLEDGE_CHUNK"
            and isinstance(locator, str)
            and locator.startswith("$.records[")
        ):
            evidence_id = cast(str, value["evidence_ref_id"])
            fixture_ref = value.get("fixture_record_ref")
            if fixture_ref != {"path": INDEX_PATH, "sha256": sha256_hex(index_bytes)}:
                raise RuntimeError(f"Issue 273 Gold mapping is not bound to the retrieval index: {evidence_id}")
            match = re.fullmatch(r"\$\.records\[(\d+)]", locator)
            records = index.get("records")
            if match is None or not isinstance(records, list):
                raise RuntimeError(f"Issue 273 Gold locator is invalid: {evidence_id}")
            record_index = int(match.group(1))
            if record_index >= len(records) or records[record_index].get("evidence_ref_id") != evidence_id:
                raise RuntimeError(f"Issue 273 Gold locator does not resolve to its Gold record: {evidence_id}")
            result[evidence_id] = value
    if len(result) != _EXPECTED_COUNTS["gold_count"]:
        raise RuntimeError("Issue 273 Gold review preparation requires exactly 20 Gold mappings")
    return result


def _topic(case: dict[str, Any]) -> str:
    topics = [value for value in case["slice_ids"] if isinstance(value, str) and value.startswith("TOPIC_")]
    if len(topics) != 1:
        raise RuntimeError(f"Issue 273 Case must carry exactly one topic: {case['case_id']}")
    return topics[0]


def _expression(case: dict[str, Any]) -> str:
    expressions = [value for value in case["slice_ids"] if isinstance(value, str) and value.startswith("EXPRESSION_")]
    if len(expressions) != 1:
        raise RuntimeError(f"Issue 273 Case must carry exactly one expression: {case['case_id']}")
    return expressions[0]


def _required_gold_ids(origin_cases: list[dict[str, Any]]) -> set[str]:
    return {
        cast(str, case["expected"]["required_evidence_refs"][0])
        for case in origin_cases
        if case["expected"]["required_evidence_refs"] == case["expected"]["relevant_evidence_refs"]
        and len(case["expected"]["required_evidence_refs"]) == 1
    }


def _build_review_item(
    *,
    origin: str,
    origin_cases: list[dict[str, Any]],
    origin_labels: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    mappings: dict[str, dict[str, Any]],
) -> tuple[JsonValue, set[str]]:
    if len(origin_cases) != 3:
        raise RuntimeError(f"Issue 273 Gold review origin must have exactly three Cases: {origin}")
    gold_labels = [label for label in origin_labels if label.get("record_kind") == "GOLD"]
    negative_labels = [label for label in origin_labels if label.get("record_kind") == "HARD_NEGATIVE"]
    if len(gold_labels) != 1 or len(negative_labels) != 4:
        raise RuntimeError(f"Issue 273 Gold review origin requires one Gold and four negatives: {origin}")
    if {label.get("negative_type") for label in negative_labels} != set(NEGATIVE_TYPES):
        raise RuntimeError(f"Issue 273 Gold review origin has an invalid negative type set: {origin}")
    if (
        gold_labels[0].get("negative_type") is not None
        or gold_labels[0].get("adversarial_for_transform_origin") is not None
    ):
        raise RuntimeError(f"Issue 273 Gold label carries negative-only metadata: {origin}")
    if any(label.get("adversarial_for_transform_origin") != origin for label in negative_labels):
        raise RuntimeError(f"Issue 273 hard negative adversarial origin mismatch: {origin}")

    gold_id = cast(str, gold_labels[0]["evidence_ref_id"])
    mapping = mappings.get(gold_id)
    if mapping is None:
        raise RuntimeError(f"Issue 273 Gold Evidence Mapping is missing: {gold_id}")
    if _required_gold_ids(origin_cases) != {gold_id}:
        raise RuntimeError(f"Issue 273 Cases do not agree on one Gold Evidence: {origin}")

    gold_record = records[gold_id]
    if gold_record.get("transform_origin") is not None:
        raise RuntimeError("Issue 273 retrieval record unexpectedly exposes transform_origin")
    negatives: list[JsonValue] = []
    evidence_ids = {gold_id}
    for negative_type in NEGATIVE_TYPES:
        label = next(label for label in negative_labels if label["negative_type"] == negative_type)
        evidence_id = cast(str, label["evidence_ref_id"])
        record = records[evidence_id]
        negatives.append(
            {
                "content_sha256": record["content_sha256"],
                "evidence_ref_id": evidence_id,
                "negative_type": negative_type,
                "statement": record["statement"],
            }
        )
        evidence_ids.add(evidence_id)

    item: dict[str, JsonValue] = {
        "gold": {
            "content_sha256": gold_record["content_sha256"],
            "evidence_ref_id": gold_id,
            "locator": mapping["locator"],
            "stable_key": mapping["stable_key"],
            "statement": gold_record["statement"],
        },
        "hard_negatives": negatives,
        "product_code": gold_record["product_code"],
        "queries": [
            {
                "case_id": case["case_id"],
                "expression": _expression(case),
                "query": case["query"],
            }
            for case in origin_cases
        ],
        "topic": _topic(origin_cases[0]),
        "transform_origin": origin,
    }
    return item, evidence_ids


def _review_items(
    cases: list[dict[str, Any]],
    records: dict[str, dict[str, Any]],
    labels_by_origin: dict[str, list[dict[str, Any]]],
    mappings: dict[str, dict[str, Any]],
) -> list[JsonValue]:
    cases_by_origin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        origin = case["leakage_group_ids"]["transform_origin"]
        if not isinstance(origin, str):
            raise RuntimeError(f"Issue 273 Case transform_origin must be a string: {case['case_id']}")
        cases_by_origin[origin].append(case)

    items: list[JsonValue] = []
    seen_label_ids: set[str] = set()
    ordered_origins = sorted(cases_by_origin, key=lambda origin: cases_by_origin[origin][0]["case_id"])
    if len(ordered_origins) != _EXPECTED_COUNTS["transform_origin_count"]:
        raise RuntimeError("Issue 273 Gold review preparation requires exactly 20 transform origins")

    for origin in ordered_origins:
        item, evidence_ids = _build_review_item(
            origin=origin,
            origin_cases=cases_by_origin[origin],
            origin_labels=labels_by_origin.get(origin, []),
            records=records,
            mappings=mappings,
        )
        items.append(item)
        seen_label_ids.update(evidence_ids)

    if seen_label_ids != set(records):
        raise RuntimeError("Issue 273 Gold review packet does not cover the complete labelled corpus")
    return items


def build_gold_review_packet(evals_root: Path) -> dict[str, JsonValue]:
    manifest_bytes, manifest = _read_json(evals_root, DATASET_MANIFEST_PATH)
    mapping_bytes, mapping = _read_json(evals_root, EVIDENCE_MAPPING_PATH)
    index_bytes, index = _read_json(evals_root, INDEX_PATH)
    label_bytes, labels = _read_json(evals_root, EVALUATION_LABEL_PATH)
    authoring_ref = manifest.get("authoring_identity_manifest_ref")
    if not isinstance(authoring_ref, dict) or authoring_ref.get("path") != AUTHORING_IDENTITY_PATH:
        raise RuntimeError("Issue 273 Dataset manifest authoring identity reference is invalid")
    authoring_bytes, authoring = _read_json(evals_root, AUTHORING_IDENTITY_PATH)

    if manifest.get("status") != "DRAFT" or manifest.get("frozen_at") is not None:
        raise RuntimeError("Issue 273 Gold review preparation requires an unfrozen DRAFT Dataset")
    if manifest.get("partition_counts", {}).get("HOLDOUT") != 0:
        raise RuntimeError("Issue 273 Gold review preparation cannot include HOLDOUT")
    _require_draft_provenance(manifest, source=DATASET_MANIFEST_PATH)
    _require_draft_provenance(mapping, source=EVIDENCE_MAPPING_PATH)
    _require_self_hash(manifest, field="manifest_sha256", source="Dataset manifest")
    _require_self_hash(mapping, field="manifest_sha256", source="Evidence Mapping")
    _require_self_hash(authoring, field="manifest_sha256", source="authoring identity manifest")
    if authoring_ref.get("sha256") != sha256_hex(authoring_bytes):
        raise RuntimeError("Issue 273 Dataset manifest is not bound to the authoring identity manifest")

    if index.get("evaluation_label_ref") != {
        "path": EVALUATION_LABEL_PATH,
        "sha256": sha256_hex(label_bytes),
    }:
        raise RuntimeError("Issue 273 retrieval index is not bound to the evaluation label sidecar")
    if manifest.get("evidence_mapping_manifest_sha256") != mapping.get("manifest_sha256"):
        raise RuntimeError("Issue 273 Dataset manifest is not bound to the Evidence Mapping")

    cases = _load_cases(evals_root, manifest)
    records = _records_by_id(index)
    grouped_labels = _labels_by_origin(labels)
    mappings = _gold_mapping_by_id(mapping, index_bytes=index_bytes, index=index)
    items = _review_items(cases, records, grouped_labels, mappings)
    counts: dict[str, JsonValue] = {
        "case_count": sum(len(cast(dict[str, Any], item)["queries"]) for item in items),
        "gold_count": len(items),
        "hard_negative_count": sum(len(cast(dict[str, Any], item)["hard_negatives"]) for item in items),
        "transform_origin_count": len(items),
    }
    if counts != _EXPECTED_COUNTS:
        raise RuntimeError("Issue 273 Gold review packet has unexpected counts")

    payload: dict[str, JsonValue] = {
        "contract_status": "REPOSITORY_LOCAL_NON_RUNTIME_PROJECTION",
        "counts": counts,
        "dataset_ref": f"{manifest['dataset_code']}@{manifest['dataset_version']}",
        "format_id": "issue-273.gold-review-packet",
        "format_version": "1.0.0",
        "issue": "#273",
        "items": items,
        "packet_sha256": "0" * 64,
        "purpose": "PREPARATION_ONLY",
        "source_artifacts": {
            "authoring_identity": _source_ref(AUTHORING_IDENTITY_PATH, authoring_bytes),
            "dataset_manifest": _source_ref(DATASET_MANIFEST_PATH, manifest_bytes),
            "evaluation_labels": _source_ref(EVALUATION_LABEL_PATH, label_bytes),
            "evidence_mapping": _source_ref(EVIDENCE_MAPPING_PATH, mapping_bytes),
            "retrieval_index": _source_ref(INDEX_PATH, index_bytes),
        },
        "source_dataset_manifest_sha256": manifest["manifest_sha256"],
        "source_dataset_status": "DRAFT",
    }
    payload["packet_sha256"] = canonical_sha256(
        payload,
        excluded_top_level_keys=frozenset({"packet_sha256"}),
    )
    return payload


def render_gold_review_markdown(packet: dict[str, JsonValue]) -> str:
    counts = cast(dict[str, JsonValue], packet["counts"])
    sources = cast(dict[str, JsonValue], packet["source_artifacts"])
    lines = [
        "# Issue #273 DEV Gold Review Packet",
        "",
        "> 이 문서는 준비 전용(PREPARATION_ONLY) 검토 입력입니다. 사람의 검토 결과, 승인, Freeze를 기록하지 않습니다.",
        "> 이 JSON/Markdown은 저장소 로컬 비런타임 projection이며 공유 schema 또는 runtime contract가 아닙니다.",
        "",
        "## 검토 범위",
        "",
        f"- Dataset: `{packet['dataset_ref']}` (`{packet['source_dataset_status']}`)",
        f"- 대상: {counts['transform_origin_count']} origins / {counts['case_count']} 한국어 DEV 질문 / "
        f"{counts['gold_count']} Gold / {counts['hard_negative_count']} hard negatives",
        "- 공개 가능한 합성 DEV만 포함하며 HOLDOUT은 포함하지 않습니다.",
        "- OTC 추천·상호작용 질문은 포함하지 않으며 해당 범위는 Issue #278에서 다룹니다.",
        "",
        "## 원본 결속",
        "",
        f"- Dataset manifest self hash: `{packet['source_dataset_manifest_sha256']}`",
        f"- Packet self hash: `{packet['packet_sha256']}`",
    ]
    for name in sorted(sources):
        source = cast(dict[str, JsonValue], sources[name])
        lines.append(f"- `{name}`: `{source['path']}` / `{source['sha256']}`")

    lines.extend(
        [
            "",
            "## 담당 리뷰어 확인 기준",
            "",
            "각 origin을 직접 확인하고 다음 기준을 모두 판단합니다.",
            "",
            "- Gold Evidence가 세 질문의 의도를 모두 충족하는지 확인합니다.",
            "- Gold가 필요한 내용을 담은 최소 단일 Evidence인지 확인합니다.",
            "- 네 hard negative 중 실제 정답인 false negative가 없는지 확인합니다.",
            "- 실제 환자·제품·Provider 데이터가 아닌 합성 데이터인지 확인합니다.",
            "- 세 표현 변형이 같은 검색 의도를 보존하는지 확인합니다.",
            "- OTC 추천·상호작용 질문이 포함되지 않음을 확인합니다.",
            "",
            "## 검토 결과 기록 방법",
            "",
            "검토 결과는 이 파일에 미리 쓰지 않습니다. 담당 리뷰어가 모든 20개 origin을 확인한 뒤 "
            "GitHub Pull Request review event에 검토 범위와 결과를 명시합니다. 변경이 필요하면 origin ID를 "
            "지정합니다. PR #316의 승인은 Gold review 증빙으로 재사용하지 않습니다. 실제 event가 생성된 뒤 "
            "별도 기록 PR에서 immutable event reference를 결속하고 provenance를 전이합니다.",
            "",
            "- REVIEWED event actor는 `EVALUATION_REVIEWER` 역할이어야 합니다.",
            "- APPROVED event actor는 `DATASET_CUSTODIAN` 역할이어야 합니다.",
            "- 작성자·REVIEWED actor·APPROVED actor는 서로 다른 실제 사람이어야 합니다.",
            "- APPROVED event를 요청하기 전에 Issue와 PR에 별도 승인 담당자를 명시하고 실제 계정·역할 매핑을 "
            "확인합니다. 확인되지 않은 계정을 추정해 기록하지 않습니다.",
            "",
            "첫 실제 내용 검토 event에는 아래 REVIEWED 문구를 사용합니다.",
            "",
            "```text",
            "Gold review result: REVIEWED",
            f"packet_sha256: {packet['packet_sha256']}",
            f"dataset_manifest_sha256: {packet['source_dataset_manifest_sha256']}",
            "reviewed_origins: 20/20",
            "review_commit_oid: <reviewed commit OID>",
            "```",
            "",
            "수정 요구가 모두 해소된 뒤 별도의 승인 event에는 아래 APPROVED 문구를 사용합니다.",
            "",
            "```text",
            "Gold review result: APPROVED",
            f"packet_sha256: {packet['packet_sha256']}",
            f"dataset_manifest_sha256: {packet['source_dataset_manifest_sha256']}",
            "approved_origins: 20/20",
            "review_commit_oid: <approved commit OID>",
            "```",
            "",
            "각 event 제출 후 기록 PR이 GitHub API에서 실제 review ID, actor, submitted timestamp를 수집하고 "
            "event body와 commit OID에 함께 결속합니다. 템플릿 placeholder를 provenance 값으로 사용하지 "
            "않습니다. APPROVED event 전에는 Dataset을 Freeze하지 않습니다.",
            "",
        ]
    )

    for value in cast(list[JsonValue], packet["items"]):
        item = cast(dict[str, Any], value)
        lines.extend(
            [
                f"## {item['transform_origin']}",
                "",
                f"- Topic: `{item['topic']}`",
                f"- Product code: `{item['product_code']}`",
                "- 질문:",
            ]
        )
        for query in item["queries"]:
            lines.append(f"  - `{query['case_id']}` · `{query['expression']}`: {query['query']}")
        gold = item["gold"]
        lines.extend(
            [
                f"- Gold: `{gold['evidence_ref_id']}` · `{gold['stable_key']}` · `{gold['locator']}`",
                f"  - {gold['statement']}",
                "- Hard negatives:",
            ]
        )
        for negative in item["hard_negatives"]:
            lines.append(
                f"  - `{negative['negative_type']}` · `{negative['evidence_ref_id']}`: {negative['statement']}"
            )
        lines.append("")
    return "\n".join(lines)


def write_gold_review_packet(repository_root: Path) -> None:
    packet = build_gold_review_packet(repository_root / "evals")
    json_path = repository_root / GOLD_REVIEW_JSON_PATH
    markdown_path = repository_root / GOLD_REVIEW_MARKDOWN_PATH
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_bytes(canonical_json_bytes(packet))
    markdown_path.write_text(render_gold_review_markdown(packet), encoding="utf-8")
