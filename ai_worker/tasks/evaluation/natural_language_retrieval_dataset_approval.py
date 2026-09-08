from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.natural_language_retrieval_dev_authoring import (
    DATASET_MANIFEST_PATH,
    EVALUATION_LABEL_PATH,
    EVIDENCE_MAPPING_PATH,
    INDEX_PATH,
    REVIEW_EVIDENCE_PATH,
)

DATASET_APPROVAL_JSON_PATH = "docs/validation/rag/issue-273/dataset-approval-request.json"
DATASET_APPROVAL_MARKDOWN_PATH = "docs/validation/rag/issue-273/dataset-approval-request.md"

_EXPECTED_IMPLEMENTER = {
    "actor_id": "ceohwj",
    "namespace": "GITHUB_LOGIN",
    "role": "EVALUATION_IMPLEMENTER",
}
_EXPECTED_GOLD_REVIEWER = {
    "actor_id": "hazelnutflavoured",
    "namespace": "GITHUB_LOGIN",
    "role": "EVALUATION_REVIEWER",
}
_REQUESTED_CUSTODIAN: dict[str, JsonValue] = {
    "actor_id": "phina-io",
    "namespace": "GITHUB_LOGIN",
    "role": "DATASET_CUSTODIAN",
}


def _read_json(evals_root: Path, relative_path: str) -> tuple[bytes, dict[str, Any]]:
    content = (evals_root / relative_path).read_bytes()
    value = json.loads(content)
    if not isinstance(value, dict):
        raise RuntimeError(f"Issue 273 Dataset approval source must be a JSON object: {relative_path}")
    return content, value


def _source_ref(relative_path: str, content: bytes) -> dict[str, JsonValue]:
    return {"path": relative_path, "sha256": sha256_hex(content)}


def _require_self_hash(value: dict[str, Any], *, source: str) -> None:
    expected = canonical_sha256(value, excluded_top_level_keys=frozenset({"manifest_sha256"}))
    if value.get("manifest_sha256") != expected:
        raise RuntimeError(f"Issue 273 {source} self hash mismatch")


def _require_reviewed_provenance(
    value: dict[str, Any],
    *,
    source: str,
    expected_review_ref: dict[str, JsonValue],
) -> None:
    provenance = value.get("review_provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("team_gold_status") != "REVIEWED"
        or provenance.get("approved_by") is not None
        or provenance.get("approved_at") is not None
    ):
        raise RuntimeError(
            f"Issue 273 Dataset approval preparation requires REVIEWED provenance without approval: {source}"
        )
    if provenance.get("authored_by") != _EXPECTED_IMPLEMENTER:
        raise RuntimeError(f"Issue 273 Dataset approval preparation has an unexpected implementer: {source}")
    if provenance.get("reviewed_by") != _EXPECTED_GOLD_REVIEWER:
        raise RuntimeError(f"Issue 273 Dataset approval preparation has an unexpected Gold reviewer: {source}")
    if provenance.get("evidence_review_refs") != [expected_review_ref]:
        raise RuntimeError(f"Issue 273 {source} is not bound to the Gold review evidence")


def _review_event(review_bytes: bytes, review: dict[str, Any]) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    evidence_sha256 = sha256_hex(review_bytes)
    required = {
        "gold_review_result": "REVIEWED",
        "review_state": "COMMENTED",
        "reviewed_origins": "20/20",
        "reviewer": "hazelnutflavoured",
    }
    if any(review.get(field) != expected for field, expected in required.items()):
        raise RuntimeError("Issue 273 Gold review evidence does not describe the accepted REVIEWED event")
    review_body = review.get("review_body")
    if not isinstance(review_body, str) or not all(
        marker in review_body
        for marker in (
            "Gold review result: REVIEWED",
            f"packet_sha256: {review.get('packet_sha256')}",
            f"dataset_manifest_sha256: {review.get('reviewed_dataset_manifest_sha256')}",
            "reviewed_origins: 20/20",
            f"review_commit_oid: {review.get('commit_sha')}",
        )
    ):
        raise RuntimeError("Issue 273 Gold review evidence body is not bound to its structured fields")
    expected_ref: dict[str, JsonValue] = {
        "hash": evidence_sha256,
        "id": cast(str, review["evidence_id"]),
        "version": cast(str, review["evidence_version"]),
    }
    event: dict[str, JsonValue] = {
        "commit_sha": cast(str, review["commit_sha"]),
        "evidence_id": cast(str, review["evidence_id"]),
        "evidence_sha256": evidence_sha256,
        "packet_sha256": cast(str, review["packet_sha256"]),
        "review_id": cast(int, review["review_id"]),
        "review_state": cast(str, review["review_state"]),
        "review_submitted_at": cast(str, review["review_submitted_at"]),
        "review_url": cast(str, review["review_url"]),
        "reviewed_dataset_manifest_sha256": cast(str, review["reviewed_dataset_manifest_sha256"]),
        "reviewed_origins": cast(str, review["reviewed_origins"]),
    }
    return event, expected_ref


def _case_resources(
    evals_root: Path,
    manifest: dict[str, Any],
    expected_review_ref: dict[str, JsonValue],
) -> list[JsonValue]:
    resources = manifest.get("case_resources")
    if not isinstance(resources, list) or len(resources) != 60:
        raise RuntimeError("Issue 273 Dataset approval preparation requires exactly 60 Case resources")

    result: list[JsonValue] = []
    resource_hash_inputs: list[JsonValue] = []
    seen_case_ids: set[str] = set()
    for resource in resources:
        if not isinstance(resource, dict) or resource.get("partition") != "DEV":
            raise RuntimeError("Issue 273 Dataset approval preparation accepts DEV Cases only")
        case_id = resource.get("case_id")
        relative_path = resource.get("path")
        if not isinstance(case_id, str) or case_id in seen_case_ids or not isinstance(relative_path, str):
            raise RuntimeError("Issue 273 Dataset approval Case references must be unique and complete")
        case_bytes, case = _read_json(evals_root, relative_path)
        if sha256_hex(case_bytes) != resource.get("sha256"):
            raise RuntimeError(f"Issue 273 Case hash mismatch: {relative_path}")
        if case.get("case_id") != case_id or case.get("partition") != "DEV":
            raise RuntimeError(f"Issue 273 Case reference mismatch: {relative_path}")
        _require_reviewed_provenance(case, source=relative_path, expected_review_ref=expected_review_ref)
        result.append(cast(JsonValue, dict(resource)))
        resource_hash_inputs.append(
            {"partition": resource["partition"], "path": relative_path, "sha256": resource["sha256"]}
        )
        seen_case_ids.add(case_id)

    if manifest.get("resource_set_hash") != canonical_sha256({"resources": resource_hash_inputs}):
        raise RuntimeError("Issue 273 Dataset manifest resource set hash mismatch")
    return result


def _corpus_counts(labels: dict[str, Any], manifest: dict[str, Any]) -> dict[str, JsonValue]:
    values = labels.get("labels")
    if not isinstance(values, list) or len(values) != 100:
        raise RuntimeError("Issue 273 Dataset approval preparation requires exactly 100 evaluation labels")
    gold_count = sum(isinstance(value, dict) and value.get("record_kind") == "GOLD" for value in values)
    negative_count = sum(isinstance(value, dict) and value.get("record_kind") == "HARD_NEGATIVE" for value in values)
    origins = {
        value.get("transform_origin")
        for value in values
        if isinstance(value, dict) and isinstance(value.get("transform_origin"), str)
    }
    counts: dict[str, JsonValue] = {
        "case_count": manifest.get("partition_counts", {}).get("DEV"),
        "gold_count": gold_count,
        "hard_negative_count": negative_count,
        "holdout_count": manifest.get("partition_counts", {}).get("HOLDOUT"),
        "transform_origin_count": len(origins),
    }
    expected: dict[str, JsonValue] = {
        "case_count": 60,
        "gold_count": 20,
        "hard_negative_count": 80,
        "holdout_count": 0,
        "transform_origin_count": 20,
    }
    if counts != expected:
        raise RuntimeError("Issue 273 Dataset approval preparation has unexpected dataset counts")
    return counts


def build_dataset_approval_request(evals_root: Path) -> dict[str, JsonValue]:
    manifest_bytes, manifest = _read_json(evals_root, DATASET_MANIFEST_PATH)
    mapping_bytes, mapping = _read_json(evals_root, EVIDENCE_MAPPING_PATH)
    labels_bytes, labels = _read_json(evals_root, EVALUATION_LABEL_PATH)
    index_bytes, index = _read_json(evals_root, INDEX_PATH)
    review_bytes, review = _read_json(evals_root, REVIEW_EVIDENCE_PATH)
    gold_review_event, expected_review_ref = _review_event(review_bytes, review)

    if manifest.get("status") != "DRAFT" or manifest.get("frozen_at") is not None:
        raise RuntimeError("Issue 273 Dataset approval preparation requires an unfrozen DRAFT Dataset")
    if manifest.get("partition_counts", {}).get("HOLDOUT") != 0:
        raise RuntimeError("Issue 273 Dataset approval preparation cannot include HOLDOUT")
    _require_self_hash(manifest, source="Dataset manifest")
    _require_self_hash(mapping, source="Evidence Mapping")
    _require_reviewed_provenance(
        manifest,
        source=DATASET_MANIFEST_PATH,
        expected_review_ref=expected_review_ref,
    )
    _require_reviewed_provenance(
        mapping,
        source=EVIDENCE_MAPPING_PATH,
        expected_review_ref=expected_review_ref,
    )
    if manifest.get("evidence_mapping_manifest_sha256") != mapping.get("manifest_sha256"):
        raise RuntimeError("Issue 273 Dataset manifest is not bound to the Evidence Mapping")
    if index.get("evaluation_label_ref") != {
        "path": EVALUATION_LABEL_PATH,
        "sha256": sha256_hex(labels_bytes),
    }:
        raise RuntimeError("Issue 273 retrieval index is not bound to the evaluation labels")

    case_resources = _case_resources(evals_root, manifest, expected_review_ref)
    counts = _corpus_counts(labels, manifest)
    payload: dict[str, JsonValue] = {
        "approval_event": None,
        "approval_recorded": False,
        "case_resources": case_resources,
        "contract_status": "REPOSITORY_LOCAL_NON_RUNTIME_PROJECTION",
        "counts": counts,
        "dataset_ref": f"{manifest['dataset_code']}@{manifest['dataset_version']}",
        "dataset_status": "DRAFT",
        "format_id": "issue-273.dataset-approval-request",
        "format_version": "1.0.0",
        "freeze_recorded": False,
        "gold_review_event": gold_review_event,
        "gold_reviewer": cast(JsonValue, _EXPECTED_GOLD_REVIEWER),
        "implementation_owner": cast(JsonValue, _EXPECTED_IMPLEMENTER),
        "issue": "#273",
        "purpose": "PREPARATION_ONLY",
        "request_sha256": "0" * 64,
        "requested_dataset_custodian": _REQUESTED_CUSTODIAN,
        "source_artifacts": {
            "dataset_manifest": _source_ref(DATASET_MANIFEST_PATH, manifest_bytes),
            "evaluation_labels": _source_ref(EVALUATION_LABEL_PATH, labels_bytes),
            "evidence_mapping": _source_ref(EVIDENCE_MAPPING_PATH, mapping_bytes),
            "gold_review_evidence": _source_ref(REVIEW_EVIDENCE_PATH, review_bytes),
            "retrieval_index": _source_ref(INDEX_PATH, index_bytes),
        },
        "source_dataset_manifest_sha256": cast(str, manifest["manifest_sha256"]),
        "team_gold_status": "REVIEWED",
    }
    payload["request_sha256"] = canonical_sha256(
        payload,
        excluded_top_level_keys=frozenset({"request_sha256"}),
    )
    return payload


def render_dataset_approval_markdown(packet: dict[str, JsonValue]) -> str:
    counts = cast(dict[str, Any], packet["counts"])
    event = cast(dict[str, Any], packet["gold_review_event"])
    sources = cast(dict[str, Any], packet["source_artifacts"])
    lines = [
        "# Issue #273 Dataset Approval Request",
        "",
        "> 이 문서는 준비 전용(PREPARATION_ONLY) 승인 요청 입력입니다. 승인 결과나 Freeze를 선기록하지 않습니다.",
        "> 저장소 로컬 비런타임 projection이며 공유 schema 또는 runtime contract가 아닙니다.",
        "",
        "## 담당자와 현재 상태",
        "",
        "- 구현 담당자: 정현우 (`@ceohwj`, `EVALUATION_IMPLEMENTER`)",
        "- Gold 내용 검토자: 권가빈 (`@hazelnutflavoured`, `EVALUATION_REVIEWER`)",
        "- 요청 Dataset Custodian: 송은영 (`@phina-io`, `DATASET_CUSTODIAN`)",
        "- 현재 Dataset은 계속 `DRAFT`, Gold provenance는 `REVIEWED`, HOLDOUT은 `0`입니다.",
        "- 이 준비 PR에서는 HOLDOUT 40개를 만들거나 Freeze하지 않습니다.",
        "- 실제 Retriever Adapter가 없으므로 Baseline을 실행하지 않습니다.",
        "",
        "## 승인 대상",
        "",
        f"- Dataset: `{packet['dataset_ref']}`",
        f"- 범위: {counts['transform_origin_count']} origins / {counts['case_count']} 한국어 DEV 질문 / "
        f"{counts['gold_count']} Gold / {counts['hard_negative_count']} hard negatives",
        f"- 현재 Dataset manifest self hash: `{packet['source_dataset_manifest_sha256']}`",
        f"- Gold review evidence: `{event['evidence_id']}` / `{event['evidence_sha256']}`",
        f"- Approval request self hash: `{packet['request_sha256']}`",
        "",
        "## 원본 결속",
        "",
    ]
    for name in sorted(sources):
        source = cast(dict[str, Any], sources[name])
        lines.append(f"- `{name}`: `{source['path']}` / `{source['sha256']}`")
    lines.extend(
        [
            f"- Case resources: `{len(cast(list[Any], packet['case_resources']))}`개; 각 path와 raw SHA-256은 JSON에 기록",
            "",
            "## Dataset Custodian 확인 기준",
            "",
            "- 위 해시가 PR의 현재 파일과 일치하고 60개 Case가 모두 동일한 Gold review evidence에 결속되는지 확인합니다.",
            "- 구현 담당자, Gold 검토자, Dataset Custodian이 서로 다른 실제 사람인지 확인합니다.",
            "- 공개 가능한 합성 DEV 데이터만 포함되고 실제 환자·제품·Provider 데이터와 HOLDOUT이 없는지 확인합니다.",
            "- Dataset과 Evidence Mapping에 승인자·승인 시각·Freeze 시각이 미리 기록되지 않았는지 확인합니다.",
            "",
            "## 실제 승인 방법",
            "",
            "송은영님은 위 기준을 확인한 뒤 이 준비 PR에서 GitHub의 Approve 기능으로 review를 제출하고, "
            "본문에 아래 문구를 사용합니다. placeholder는 실제 provenance 값이 아닙니다.",
            "`<approved commit OID>`는 제출 전에 GitHub가 실제로 검토한 PR HEAD의 정확한 40자리 commit OID로 "
            "반드시 치환합니다.",
            "",
            "```text",
            "Gold review result: APPROVED",
            f"approval_request_sha256: {packet['request_sha256']}",
            f"dataset_manifest_sha256: {packet['source_dataset_manifest_sha256']}",
            f"gold_review_evidence_sha256: {event['evidence_sha256']}",
            "approved_origins: 20/20",
            "review_commit_oid: <approved commit OID>",
            "```",
            "",
            "## 승인 후 후속 작업",
            "",
            "실제 승인 event가 생성된 뒤에만 별도 provenance 기록 PR을 만듭니다. 그 PR에서 GitHub API로 "
            "review ID, actor, state, submitted timestamp, body, commit OID를 수집해 immutable evidence로 결속하고 "
            "60개 Case·Evidence Mapping·Dataset Manifest를 `APPROVED`로 전이합니다. 그 전에는 승인값을 기록하지 않습니다.",
            "",
            "Dataset 승인과 HOLDOUT Freeze는 별개입니다. 접근 통제와 Freeze Receipt 계약이 확정된 뒤 별도 작업으로 "
            "HOLDOUT 40개를 준비하며, Retriever Adapter가 준비된 이후에만 100개 실제 baseline을 실행합니다.",
            "",
        ]
    )
    return "\n".join(lines)


def write_dataset_approval_request(repository_root: Path) -> None:
    packet = build_dataset_approval_request(repository_root / "evals")
    json_path = repository_root / DATASET_APPROVAL_JSON_PATH
    markdown_path = repository_root / DATASET_APPROVAL_MARKDOWN_PATH
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_bytes(canonical_json_bytes(packet))
    markdown_path.write_text(render_dataset_approval_markdown(packet), encoding="utf-8")
