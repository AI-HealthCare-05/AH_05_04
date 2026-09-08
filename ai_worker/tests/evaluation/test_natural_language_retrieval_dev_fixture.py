from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.loaders import _resolve_json_locator, load_dataset
from ai_worker.tasks.evaluation.natural_language_retrieval_dev_authoring import (
    BASE_INTENTS,
    FILE_PREFIX,
    RESERVED_PRODUCT_CODES,
    TOPIC_OVERLAP_TERMS,
    build_issue_273_dev_graph,
)
from ai_worker.tasks.evaluation.schemas.authoring_v1_2 import EvidenceMappingManifestV12

CASE_PREFIX = "retrieval/cases/rag-natural-language-retrieval-dev-v1/"
INDEX_PATH = "retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json"
LABEL_PATH = "retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/evaluation-labels.json"
EVALUATION_LABEL_KEYS = frozenset(
    {"record_kind", "negative_type", "adversarial_for_transform_origin", "transform_origin"}
)
MAPPING_PATH = "retrieval/evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json"
MANIFEST_PATH = "retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json"
AUTHORING_PATH = "retrieval/manifests/rag-natural-language-retrieval-dev-v1.authoring-identities.json"
RUBRIC_PATH = "retrieval/manifests/rag-natural-language-retrieval-dev-v1.critical-claim-rubric.json"
PROFILE_PATH = "profiles/rag-natural-language-retrieval-dev-v1.profile.json"
COMPARISON_PATH = "policies/rag-natural-language-retrieval-dev-v1.comparison-policy.json"
POLICY_PATH = "policies/rag-natural-language-retrieval-dev-v1.evaluation-policy.json"
SUITE_PATH = "suites/rag-natural-language-retrieval-dev-v1.suite.json"
RECEIPT_PATH = "provenance/rag-natural-language-retrieval-dev-v1.protected-artifact-receipt.json"
REVIEW_EVIDENCE_PATH = "provenance/rag-natural-language-retrieval-dev-v1.review-evidence.json"
APPROVAL_EVIDENCE_PATH = "provenance/rag-natural-language-retrieval-dev-v1.approval-evidence.json"
NEGATIVE_TYPES = {
    "SAME_FAMILY_DIFFERENT_ATTRIBUTE",
    "SAME_TOPIC_DIFFERENT_FAMILY",
    "LEXICAL_OVERLAP_UNSUPPORTED",
    "CROSS_TOPIC_OVERLAP",
}
EVALS_ROOT = Path(__file__).parents[3] / "evals"


def _labelled_records(graph: dict[str, bytes]) -> list[dict[str, Any]]:
    """Join the retrieval projection back onto its evaluation labels, for assertions only.

    The committed index carries no label — that is the point of the sidecar — so tests that reason
    about Gold/negative structure have to re-attach them here rather than read them from the
    artifact a retrieval Adapter would index.
    """
    labels = {item["evidence_ref_id"]: item for item in json.loads(graph[LABEL_PATH])["labels"]}
    return [record | labels[record["evidence_ref_id"]] for record in json.loads(graph[INDEX_PATH])["records"]]


def _materialize_graph(tmp_path: Path, graph: dict[str, bytes]) -> Path:
    root = tmp_path / "evals"
    shutil.copytree(EVALS_ROOT / "schemas", root / "schemas")
    for relative_path, content in graph.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return root


def _non_digest_strings(value: JsonValue) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _non_digest_strings(item)]
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            if key not in {"hash", "sha256"}
            and not key.endswith("_sha")
            and not key.endswith("_sha256")
            and not key.endswith("_hash")
            for text in _non_digest_strings(item)
        ]
    return []


def test_issue_273_complete_graph_loads_with_schema_set_1_3(tmp_path: Path) -> None:
    root = _materialize_graph(tmp_path, build_issue_273_dev_graph())

    loaded = load_dataset(root / MANIFEST_PATH, evals_root=root)

    assert loaded.manifest.schema_version == "1.3.0"
    assert loaded.manifest.status.value == "DRAFT"
    assert loaded.manifest.fixture_git_commit_sha is None
    assert loaded.manifest.frozen_at is None
    assert len(loaded.cases) == 60
    assert loaded.authoring_identity_manifest is not None
    assert loaded.profile.runtime_eligible is False


def test_issue_273_cases_are_natural_korean_reviewed_retrieval_cases() -> None:
    graph = build_issue_273_dev_graph()
    cases = [json.loads(content) for path, content in graph.items() if path.startswith(CASE_PREFIX)]

    assert len(cases) == 60
    assert len({case["case_id"] for case in cases}) == 60
    assert len({case["query"] for case in cases}) == 60
    for case in cases:
        topic = next(item for item in case["slice_ids"] if item.startswith("TOPIC_"))
        expression = next(item for item in case["slice_ids"] if item.startswith("EXPRESSION_"))
        assert case["task_type"] == "RETRIEVAL"
        assert case["data_classification"] == "SYNTHETIC"
        assert case["partition"] == "DEV"
        assert case["review_provenance"]["team_gold_status"] == "APPROVED"
        assert case["expected"]["required_evidence_refs"] == case["expected"]["relevant_evidence_refs"]
        assert len(case["expected"]["required_evidence_refs"]) == 1
        assert re.search(r"[가-힣]", case["query"])
        assert not case["query"].startswith("SYNTHETIC_QUERY_")
        assert case["query"].endswith((".", "?"))
        assert case["slice_ids"] == sorted(["ALL", topic, expression])


def _queries_by_expression(graph: dict[str, bytes]) -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for path, content in graph.items():
        if not path.startswith(CASE_PREFIX):
            continue
        case = json.loads(content)
        expression = next(item for item in case["slice_ids"] if item.startswith("EXPRESSION_"))
        grouped.setdefault(expression, []).append((case["leakage_group_ids"]["transform_origin"], case["query"]))
    return grouped


def test_issue_273_paraphrase_slices_never_reuse_the_gold_subject_phrase() -> None:
    """Guard the property the per-expression Comparison Policy scopes claim to measure.

    Collapsing every `_question` branch onto the canonical template still yields 60 distinct
    queries, so the structural assertions above cannot catch it. These assertions can.
    """
    grouped = _queries_by_expression(build_issue_273_dev_graph())
    intents = {intent.transform_origin: intent for intent in BASE_INTENTS}

    assert set(grouped) == {
        "EXPRESSION_CANONICAL",
        "EXPRESSION_SYNONYM",
        "EXPRESSION_WORD_ORDER_PARTICLE",
        "EXPRESSION_COLLOQUIAL",
        "EXPRESSION_FRAGMENT",
        "EXPRESSION_LIMITED_TYPO",
    }

    for origin, query in grouped["EXPRESSION_SYNONYM"]:
        assert intents[origin].query_subject not in query, query
        assert intents[origin].synonym_subject in query, query
    for origin, query in grouped["EXPRESSION_FRAGMENT"]:
        assert intents[origin].query_subject not in query, query
        assert intents[origin].fragment_subject in query, query
        assert not any(marker in query for marker in ("알려 주", "궁금", "싶어요")), query
        assert query.endswith("?")


def test_issue_273_surface_form_slices_keep_the_subject_but_move_the_sentence_frame() -> None:
    grouped = _queries_by_expression(build_issue_273_dev_graph())
    intents = {intent.transform_origin: intent for intent in BASE_INTENTS}

    for expression in ("EXPRESSION_CANONICAL", "EXPRESSION_COLLOQUIAL", "EXPRESSION_LIMITED_TYPO"):
        for origin, query in grouped[expression]:
            assert intents[origin].query_subject in query, (expression, query)

    for origin, query in grouped["EXPRESSION_WORD_ORDER_PARTICLE"]:
        subject = intents[origin].query_subject
        assert subject in query
        assert query.index(subject) < query.index(origin), query
        assert f"{origin} 제품의 {subject}" not in query, query

    # No intent carries both CANONICAL and LIMITED_TYPO, so state the canonical form independently.
    for origin, query in grouped["EXPRESSION_LIMITED_TYPO"]:
        canonical = f"{origin} 제품의 {intents[origin].query_subject}에 대해 알려 주세요."
        assert len(query) == len(canonical)
        assert sum(left != right for left, right in zip(query, canonical, strict=True)) == 1


def test_issue_273_authoring_provenance_resolves_to_each_cases_gold_resource() -> None:
    graph = build_issue_273_dev_graph()
    cases = {
        json.loads(content)["case_id"]: json.loads(content)
        for path, content in graph.items()
        if path.startswith(CASE_PREFIX)
    }
    mapping = json.loads(graph[MAPPING_PATH])
    mapping_by_id = {entry["evidence_ref_id"]: entry for entry in mapping["entries"]}
    authoring = json.loads(graph[AUTHORING_PATH])

    assert {entry["case_id"] for entry in authoring["entries"]} == set(cases)
    for entry in authoring["entries"]:
        case = cases[entry["case_id"]]
        gold_id = case["expected"]["required_evidence_refs"][0]
        gold = mapping_by_id[gold_id]
        source = json.loads(graph[gold["fixture_record_ref"]["path"]])
        assert entry["source_snapshot_ref"] == case["context"]["runtime_fixture"]["source_snapshot_ref"]
        assert entry["source_locator"] == gold["locator"]
        assert entry["source_chunk_sha256"] == canonical_sha256(_resolve_json_locator(source, gold["locator"]))


def test_issue_273_graph_excludes_sensitive_actual_and_holdout_content() -> None:
    graph = build_issue_273_dev_graph()
    serialized = "\n".join(text for content in graph.values() for text in _non_digest_strings(json.loads(content)))
    actual_product_denylist = ("타이레놀", "게보린", "판콜", "아스피린")

    assert not re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", serialized)
    assert not re.search(r"01[016789]-?\d{3,4}-?\d{4}", serialized)
    assert not re.search(r"(?:sk|pk|api)[-_][A-Za-z0-9]{16,}", serialized, flags=re.IGNORECASE)
    assert not any(product in serialized for product in actual_product_denylist)
    assert not any("HOLDOUT" in path.upper() for path in graph)
    assert not any(
        FILE_PREFIX in path.as_posix() and "HOLDOUT" in path.as_posix().upper() for path in EVALS_ROOT.rglob("*")
    )


def test_issue_273_gold_never_resolves_to_a_hard_negative() -> None:
    graph = build_issue_273_dev_graph()
    records = _labelled_records(graph)
    records_by_id = {record["evidence_ref_id"]: record for record in records}

    cases = [json.loads(content) for path, content in graph.items() if path.startswith(CASE_PREFIX)]
    referenced = {case["expected"]["required_evidence_refs"][0] for case in cases}

    assert all(records_by_id[evidence_id]["record_kind"] == "GOLD" for evidence_id in referenced)


def test_issue_273_graph_build_is_byte_deterministic() -> None:
    first = build_issue_273_dev_graph()
    second = build_issue_273_dev_graph()

    assert first.keys() == second.keys()
    assert first == second
    assert all(content == canonical_json_bytes(json.loads(content)) for content in first.values())


def test_issue_273_comparison_scopes_are_diagnostic_and_schema_approved_only() -> None:
    graph = build_issue_273_dev_graph()
    comparison = json.loads(graph[COMPARISON_PATH])
    scopes = comparison["scopes"]
    expected_slice_counts: dict[str, tuple[int, int]] = {"ALL": (60, 20)}
    expected_slice_counts.update({intent.topic: (12, 4) for intent in BASE_INTENTS})
    expected_slice_counts.update(
        {variant.expression: (10, 10) for intent in BASE_INTENTS for variant in intent.variants}
    )

    assert comparison["approved_by"] == {
        "actor_id": "rag-eval-draft-validator",
        "namespace": "SYSTEM",
        "role": "SYSTEM_VALIDATOR",
    }
    assert len(scopes) == 5 * len(expected_slice_counts)
    for scope in scopes:
        expected_case_count, expected_group_count = expected_slice_counts[scope["slice_id"]]
        assert scope["minimum_case_count"] == expected_case_count
        assert scope["minimum_independent_group_count"] == expected_group_count
        assert scope["cluster_dimension"] == "transform_origin"
        assert scope["independence_unit"] == "transform_origin"
        assert scope["required"] is False
        assert scope["decision_basis"] == "DIAGNOSTIC_ONLY"
        assert scope["threshold"] == "0"


def test_issue_273_graph_records_only_the_actual_gold_review_event() -> None:
    graph = build_issue_273_dev_graph(dataset_approved=False)
    evidence = json.loads(graph[REVIEW_EVIDENCE_PATH])
    reviewed_values = [json.loads(graph[path])["review_provenance"] for path in (MAPPING_PATH, MANIFEST_PATH)]
    reviewed_values.extend(
        json.loads(content)["review_provenance"] for path, content in graph.items() if path.startswith(CASE_PREFIX)
    )
    draft_values = [
        json.loads(graph[path])["review_provenance"] for path in (RUBRIC_PATH, PROFILE_PATH, POLICY_PATH, SUITE_PATH)
    ]
    draft_values.append(json.loads(graph[RECEIPT_PATH])["recorded_by"])

    assert evidence == {
        "commit_sha": "4d5e23100cbf5f64abaaa4b3fb48985b51eac920",
        "evidence_id": "github-pr-341-review-5137833200",
        "evidence_version": "1.0.0",
        "gold_review_result": "REVIEWED",
        "packet_sha256": "fd98d6b3b88f80f858f32275ab8769f77dca152d55ea31e7583a2f88af180f1b",
        "pull_number": 341,
        "repository": "AI-HealthCare-05/AH_05_04",
        "review_body": (
            "Gold review result: REVIEWED\n"
            "packet_sha256: fd98d6b3b88f80f858f32275ab8769f77dca152d55ea31e7583a2f88af180f1b\n"
            "dataset_manifest_sha256: a6461ca49c6021b242bd5b13f3d9b1b52bf564bea186a47894cd254a40600291\n"
            "reviewed_origins: 20/20\n"
            "review_commit_oid: 4d5e23100cbf5f64abaaa4b3fb48985b51eac920\n\n"
            "20개 origin의 질문 60개, Gold 20개, hard negative 80개를 전수 대조했습니다. "
            "세 표현 변형은 origin별로 같은 검색 의도를 유지하고, 각 Gold는 세 질문을 충족하는 최소 "
            "단일 Evidence이며 hard negative에서 실제 정답인 false negative는 발견하지 못했습니다. "
            "실제 환자·제품·Provider 데이터나 OTC 추천·상호작용 질문도 포함되지 않았습니다.\n\n"
            "독립 검증: 관련 pytest 7 passed, Ruff 통과, 대상 모듈 Mypy 통과. GitHub CI의 "
            "lint/test/checks도 모두 통과한 상태를 확인했습니다.\n"
        ),
        "review_id": 5137833200,
        "review_node_id": "PRR_kwDOT3EWNs8AAAABMj0c8A",
        "review_state": "COMMENTED",
        "review_submitted_at": "2026-09-08T05:47:16.000000Z",
        "review_url": "https://github.com/AI-HealthCare-05/AH_05_04/pull/341#pullrequestreview-5137833200",
        "reviewed_dataset_manifest_sha256": "a6461ca49c6021b242bd5b13f3d9b1b52bf564bea186a47894cd254a40600291",
        "reviewed_origins": "20/20",
        "reviewer": "hazelnutflavoured",
    }
    expected_ref = [
        {
            "hash": canonical_sha256(evidence),
            "id": "github-pr-341-review-5137833200",
            "version": "1.0.0",
        }
    ]
    assert len(reviewed_values) == 62
    for provenance in reviewed_values:
        assert provenance["team_gold_status"] == "REVIEWED"
        assert provenance["reviewed_by"] == {
            "actor_id": "hazelnutflavoured",
            "namespace": "GITHUB_LOGIN",
            "role": "EVALUATION_REVIEWER",
        }
        assert provenance["reviewed_at"] == "2026-09-08T05:47:16.000000Z"
        assert provenance["evidence_review_refs"] == expected_ref
        assert provenance["approved_by"] is None
        assert provenance["approved_at"] is None

    assert all(provenance["team_gold_status"] == "DRAFT" for provenance in draft_values)
    assert all(provenance["reviewed_by"] is None for provenance in draft_values)
    assert all(provenance["approved_by"] is None for provenance in draft_values)
    assert all(provenance["evidence_review_refs"] == [] for provenance in draft_values)


def test_issue_273_graph_records_the_actual_dataset_custodian_approval_event() -> None:
    graph = build_issue_273_dev_graph()
    evidence = json.loads(graph[APPROVAL_EVIDENCE_PATH])
    approved_values = [json.loads(graph[path])["review_provenance"] for path in (MAPPING_PATH, MANIFEST_PATH)]
    approved_values.extend(
        json.loads(content)["review_provenance"] for path, content in graph.items() if path.startswith(CASE_PREFIX)
    )

    assert evidence == {
        "approval_request_artifact_sha256": "eab78ae599e5d60682dc0c6d1fac1c9e3474cc9a61839fbaa0c9c25d10f3cdd1",
        "approval_request_path": "docs/validation/rag/issue-273/dataset-approval-request.json",
        "approval_request_sha256": "032d6c8ce6110c500a8c5fe570381cd377ad73498b856f11c647b13197a15a8f",
        "approval_result": "APPROVED",
        "approved_dataset_manifest_sha256": "c4d54f4b17f84845ff3cec10f84958a9742357500db10b194535665735fbecff",
        "approver": "phina-io",
        "commit_sha": "c71ebf1a4e644ce3604f57f436d31a86f5e60536",
        "evidence_id": "github-pr-354-review-5139907268",
        "evidence_version": "1.0.0",
        "pull_number": 354,
        "repository": "AI-HealthCare-05/AH_05_04",
        "review_body": (
            "리뷰 (담당 범위: Dataset Custodian으로서 60개 DEV Case·Gold review evidence 해시 결속, 역할 독립성, "
            "승인값·Freeze·HOLDOUT 미기록 상태 확인)\n\n"
            "기술적으로 아래 4가지 확인 기준 전부 검증 완료했습니다.\n\n"
            "1. 해시 결속 — 직접 재실행해서 확인\n\n"
            "ai_worker/tests/evaluation/test_natural_language_retrieval_dataset_approval.py 8개 테스트를 직접 "
            "실행했습니다 (본문 claim과 일치, 8 passed). 특히 "
            "test_committed_approval_request_artifacts_are_exact_deterministic_projections가 커밋된 JSON/MD가 지금 이 "
            "순간 소스 파일들로 다시 만들면 나오는 결과와 100% 동일한지 검증하는데, 이게 통과한다는 건 문서에 "
            "적힌 해시·개수가 실제 파일 상태와 어긋날 여지가 없다는 뜻입니다.\n\n"
            "2. Gold review evidence 진위 — GitHub에서 직접 조회\n\n"
            "인용된 github-pr-341-review-5137833200을 API로 직접 조회했고, 실제로 존재하며 가빈님"
            "(hazelnutflavoured)이 작성한 리뷰 본문이 문서에 인용된 해시·문구와 정확히 일치합니다.\n\n"
            "3. 역할 독립성\n\n"
            "구현(정현우) / Gold 검토(가빈) / Custodian(은영님) — 세 개 GitHub 계정 모두 다른 실제 사람으로 "
            "확인. 자기검증 구조 아닙니다.\n\n"
            "4. 승인값·Freeze·HOLDOUT 미기록\n\n"
            "approval_event: null, approval_recorded: false, freeze_recorded: false, holdout_count: 0 전부 확인. 사전 "
            "기록 없음을 거부하는 회귀 테스트(test_approval_request_rejects_prefilled_approval, "
            "test_approval_request_rejects_holdout_content)도 직접 실행해서 통과 확인했습니다.\n\n"
            "참고: develop 대비 뒤처짐 확인\n\n"
            "이 브랜치가 develop보다 2커밋 뒤처져 있는데(#329 RAG Catalog, #350 OCR 검수 필드), 둘 다 "
            "evals/·ai_worker/tasks/evaluation/·docs/validation/rag/issue-273/를 전혀 건드리지 않아 이번 승인 대상과 "
            "무관함을 확인했습니다.\n\n\n"
            "Gold review result: APPROVED\n"
            "approval_request_sha256: 032d6c8ce6110c500a8c5fe570381cd377ad73498b856f11c647b13197a15a8f\n"
            "dataset_manifest_sha256: c4d54f4b17f84845ff3cec10f84958a9742357500db10b194535665735fbecff\n"
            "gold_review_evidence_sha256: "
            "6dd83d9c258499fb0d543870e5a99a913abb0b2dcb3c11e4b72855e43c235776\n"
            "approved_origins: 20/20\n"
            "review_commit_oid: c71ebf1a4e644ce3604f57f436d31a86f5e60536"
        ),
        "review_id": 5139907268,
        "review_node_id": "PRR_kwDOT3EWNs8AAAABMlzCxA",
        "review_state": "APPROVED",
        "review_submitted_at": "2026-09-08T09:30:09.000000Z",
        "review_url": "https://github.com/AI-HealthCare-05/AH_05_04/pull/354#pullrequestreview-5139907268",
    }
    approval_request_path = EVALS_ROOT.parent / evidence["approval_request_path"]
    approval_request = json.loads(approval_request_path.read_bytes())
    assert sha256_hex(approval_request_path.read_bytes()) == evidence["approval_request_artifact_sha256"]
    assert approval_request["request_sha256"] == evidence["approval_request_sha256"]
    assert approval_request["source_dataset_manifest_sha256"] == evidence["approved_dataset_manifest_sha256"]
    assert "\r" not in evidence["review_body"]
    assert evidence["review_body"].endswith(
        "Gold review result: APPROVED\n"
        f"approval_request_sha256: {evidence['approval_request_sha256']}\n"
        f"dataset_manifest_sha256: {evidence['approved_dataset_manifest_sha256']}\n"
        "gold_review_evidence_sha256: "
        "6dd83d9c258499fb0d543870e5a99a913abb0b2dcb3c11e4b72855e43c235776\n"
        "approved_origins: 20/20\n"
        f"review_commit_oid: {evidence['commit_sha']}"
    )
    expected_refs = [
        {
            "hash": "6dd83d9c258499fb0d543870e5a99a913abb0b2dcb3c11e4b72855e43c235776",
            "id": "github-pr-341-review-5137833200",
            "version": "1.0.0",
        },
        {
            "hash": canonical_sha256(evidence),
            "id": "github-pr-354-review-5139907268",
            "version": "1.0.0",
        },
    ]
    assert len(approved_values) == 62
    for provenance in approved_values:
        assert provenance["team_gold_status"] == "APPROVED"
        assert provenance["reviewed_by"]["actor_id"] == "hazelnutflavoured"
        assert provenance["approved_by"] == {
            "actor_id": "phina-io",
            "namespace": "GITHUB_LOGIN",
            "role": "DATASET_CUSTODIAN",
        }
        assert provenance["approved_at"] == "2026-09-08T09:30:09.000000Z"
        assert provenance["evidence_review_refs"] == expected_refs


def test_issue_273_dev_graph_has_fixed_identity_and_distribution() -> None:
    graph = build_issue_273_dev_graph()
    case_paths = sorted(path for path in graph if path.startswith(CASE_PREFIX))
    cases = [json.loads(graph[path]) for path in case_paths]

    assert set(graph) == {
        *(f"{CASE_PREFIX}rag-nlr-dev-{index:03d}.json" for index in range(1, 61)),
        "retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json",
        "retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/evaluation-labels.json",
        "retrieval/evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json",
        "retrieval/manifests/rag-natural-language-retrieval-dev-v1.critical-claim-rubric.json",
        "retrieval/manifests/rag-natural-language-retrieval-dev-v1.authoring-identities.json",
        "retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json",
        "profiles/rag-natural-language-retrieval-dev-v1.profile.json",
        "policies/rag-natural-language-retrieval-dev-v1.comparison-policy.json",
        "policies/rag-natural-language-retrieval-dev-v1.evaluation-policy.json",
        "suites/rag-natural-language-retrieval-dev-v1.suite.json",
        "provenance/rag-natural-language-retrieval-dev-v1.protected-artifact-receipt.json",
        "provenance/rag-natural-language-retrieval-dev-v1.review-evidence.json",
        "provenance/rag-natural-language-retrieval-dev-v1.approval-evidence.json",
    }
    assert [case["case_id"] for case in cases] == [f"rag-nlr-dev-{index:03d}" for index in range(1, 61)]
    assert Counter(next(item for item in case["slice_ids"] if item.startswith("TOPIC_")) for case in cases) == {
        "TOPIC_MEDICATION_INFORMATION": 12,
        "TOPIC_PRECAUTIONS": 12,
        "TOPIC_LIFESTYLE_MANAGEMENT": 12,
        "TOPIC_STORAGE": 12,
        "TOPIC_MISSED_DOSE": 12,
    }
    assert Counter(next(item for item in case["slice_ids"] if item.startswith("EXPRESSION_")) for case in cases) == {
        "EXPRESSION_CANONICAL": 10,
        "EXPRESSION_SYNONYM": 10,
        "EXPRESSION_WORD_ORDER_PARTICLE": 10,
        "EXPRESSION_COLLOQUIAL": 10,
        "EXPRESSION_FRAGMENT": 10,
        "EXPRESSION_LIMITED_TYPO": 10,
    }
    assert Counter(case["leakage_group_ids"]["transform_origin"] for case in cases) == {
        code: 3 for code in RESERVED_PRODUCT_CODES
    }
    assert RESERVED_PRODUCT_CODES == (
        "NLR-MI01",
        "NLR-MI02",
        "NLR-MI03",
        "NLR-MI04",
        "NLR-PC01",
        "NLR-PC02",
        "NLR-PC03",
        "NLR-PC04",
        "NLR-LM01",
        "NLR-LM02",
        "NLR-LM03",
        "NLR-LM04",
        "NLR-ST01",
        "NLR-ST02",
        "NLR-ST03",
        "NLR-ST04",
        "NLR-MD01",
        "NLR-MD02",
        "NLR-MD03",
        "NLR-MD04",
    )
    assert all(re.search(r"[가-힣]", case["query"]) for case in cases)
    assert all("SYNTHETIC_QUERY" not in case["query"] for case in cases)


def test_issue_273_corpus_has_one_gold_and_four_hard_negatives_per_origin() -> None:
    graph = build_issue_273_dev_graph()
    records = _labelled_records(graph)
    gold_records = [record for record in records if record["record_kind"] == "GOLD"]
    negative_records = [record for record in records if record["record_kind"] == "HARD_NEGATIVE"]

    assert len(records) == 100
    assert len(gold_records) == 20
    assert len(negative_records) == 80
    assert Counter(record["transform_origin"] for record in gold_records) == {
        code: 1 for code in RESERVED_PRODUCT_CODES
    }
    assert Counter(record["adversarial_for_transform_origin"] for record in negative_records) == {
        code: 4 for code in RESERVED_PRODUCT_CODES
    }
    for origin in RESERVED_PRODUCT_CODES:
        origin_negatives = [
            record for record in negative_records if record["adversarial_for_transform_origin"] == origin
        ]
        assert {record["negative_type"] for record in origin_negatives} == NEGATIVE_TYPES

    record_ids = [record["evidence_ref_id"] for record in records]
    content_hashes = [record["content_sha256"] for record in records]
    # Ids are content-addressed and the corpus is ordered by them, so neither the id nor the
    # position tells a retriever what a record is for.
    assert record_ids == sorted(record_ids)
    assert all(
        evidence_id == f"ev-nlr-{record['content_sha256'][:16]}"
        for evidence_id, record in zip(record_ids, records, strict=True)
    )
    assert len(record_ids) == len(set(record_ids))
    assert len(content_hashes) == len(set(content_hashes))
    assert {record["evidence_ref_id"] for record in gold_records}.isdisjoint(
        record["evidence_ref_id"] for record in negative_records
    )
    assert {record["content_sha256"] for record in gold_records}.isdisjoint(
        record["content_sha256"] for record in negative_records
    )
    assert all(re.search(r"[가-힣]", record["statement"]) for record in records)
    assert all(record["content_sha256"] == sha256_hex(record["statement"].encode("utf-8")) for record in records)

    intents_by_origin = {intent.transform_origin: intent for intent in BASE_INTENTS}
    for record in negative_records:
        intent = intents_by_origin[record["adversarial_for_transform_origin"]]
        if record["negative_type"] in {
            "SAME_FAMILY_DIFFERENT_ATTRIBUTE",
            "LEXICAL_OVERLAP_UNSUPPORTED",
        }:
            assert record["product_code"] == intent.product_code
            assert intent.query_subject not in record["statement"]


def test_issue_273_runtime_support_mappings_resolve_to_typed_non_corpus_objects() -> None:
    graph = build_issue_273_dev_graph()
    index = json.loads(graph[INDEX_PATH])
    mapping = json.loads(graph[MAPPING_PATH])
    support_entries = [entry for entry in mapping["entries"] if entry["evidence_ref_id"].startswith("ev-nlr-runtime-")]
    corpus_ids = {record["evidence_ref_id"] for record in index["records"]}

    assert len(index["records"]) == 100
    assert len(support_entries) == 3
    assert corpus_ids.isdisjoint(entry["evidence_ref_id"] for entry in support_entries)
    for entry in support_entries:
        assert not entry["locator"].startswith("$.records[")
        selected = _resolve_json_locator(index, entry["locator"])
        assert isinstance(selected, dict)
        selected_object: dict[str, JsonValue] = selected
        selected_content = selected_object["content"]
        assert isinstance(selected_content, str)
        assert selected_object["evidence_ref_id"] == entry["evidence_ref_id"]
        assert selected_object["evidence_type"] == entry["evidence_type"]
        assert selected_object["stable_key"] == entry["stable_key"]
        assert selected_object["source_version"] == entry["source_version"]
        assert selected_object["content_sha256"] == sha256_hex(selected_content.encode("utf-8"))
        assert "record_kind" not in selected_object


def test_issue_273_cases_share_one_complete_non_gold_knowledge_index_reference() -> None:
    graph = build_issue_273_dev_graph()
    index = json.loads(graph[INDEX_PATH])
    mapping = json.loads(graph[MAPPING_PATH])
    cases = [json.loads(content) for path, content in graph.items() if path.startswith(CASE_PREFIX)]
    references = {tuple(sorted(case["context"]["runtime_fixture"]["knowledge_index_ref"].items())) for case in cases}
    corpus_ids = {record["evidence_ref_id"] for record in index["records"]}
    gold_stable_keys = {entry["stable_key"] for entry in mapping["entries"] if entry["evidence_ref_id"] in corpus_ids}

    assert len(references) == 1
    reference = dict(references.pop())
    assert reference["id"] == "SYNTHETIC_NLR_KNOWLEDGE_INDEX"
    assert reference["id"] not in gold_stable_keys

    entry = next(item for item in mapping["entries"] if item["stable_key"] == reference["id"])
    assert entry["evidence_type"] == "KNOWLEDGE_CHUNK"
    assert entry["locator"] == "$.runtime_support.knowledge_index"
    selected = _resolve_json_locator(index, entry["locator"])
    assert isinstance(selected, dict)
    selected_index: dict[str, JsonValue] = selected
    assert selected_index["stable_key"] == reference["id"]
    assert selected_index["evidence_type"] == "KNOWLEDGE_CHUNK"
    assert selected_index["resource_scope"] == "COMPLETE_SYNTHETIC_KNOWLEDGE_INDEX"
    assert selected_index["corpus_record_count"] == 100
    # The Gold/negative split is evaluation metadata and lives in the sidecar, not in the index.
    assert "gold_record_count" not in selected_index
    assert "hard_negative_record_count" not in selected_index
    sidecar = json.loads(graph[LABEL_PATH])
    assert sidecar["gold_record_count"] == 20
    assert sidecar["hard_negative_record_count"] == 80


def test_issue_273_corpus_statements_are_sentence_ready_natural_korean() -> None:
    records = _labelled_records(build_issue_273_dev_graph())
    statements_by_id = {record["evidence_ref_id"]: record["statement"] for record in records}

    assert not any(
        malformed in statement
        for statement in statements_by_id.values()
        for malformed in ("제품의 제품의", "주의사항는", "조건는")
    )
    gold_statement_by_origin = {
        record["transform_origin"]: record["statement"] for record in records if record["record_kind"] == "GOLD"
    }
    assert gold_statement_by_origin["NLR-MI01"] == (
        "평가용 가상 설정에서 NLR-MI01 제품의 성분 정보는 청색 결정 성분 하나로 구성됩니다."
    )
    assert gold_statement_by_origin["NLR-PC01"] == (
        "평가용 가상 설정에서 NLR-PC01 제품의 복용 전 주의사항은 봉인선과 확인표의 세 칸을 점검하는 절차입니다."
    )
    assert gold_statement_by_origin["NLR-LM01"] == (
        "평가용 가상 설정에서 NLR-LM01 제품의 수분 섭취 안내는 기록 카드의 물컵 세 칸을 차례로 표시하는 방식입니다."
    )
    assert gold_statement_by_origin["NLR-ST01"] == (
        "평가용 가상 설정에서 NLR-ST01 제품의 보관 온도 정보는 가상 눈금 B 구간으로 지정됩니다."
    )
    assert gold_statement_by_origin["NLR-MD01"] == (
        "평가용 가상 설정에서 NLR-MD01 제품의 복용 누락을 일찍 알았을 때의 안내는 기록 카드의 절차 A를 조회하는 것입니다."
    )


def test_issue_273_corpus_contains_substantive_facts_without_class_label_leakage() -> None:
    graph = build_issue_273_dev_graph()
    index = json.loads(graph[INDEX_PATH])
    records = _labelled_records(graph)
    gold_records = [record for record in records if record["record_kind"] == "GOLD"]
    class_label_phrases = (
        "정답",
        "오답",
        "근거가 아닙니다",
        "질문의 근거",
        "뒷받침하는",
        "세부 속성은 제시하지",
        "일부 표현만 겹치는",
        "방해 자료",
    )
    retrieval_content = [
        *(record["statement"] for record in records),
        index["runtime_support"]["knowledge_index"]["content"],
    ]
    intents_by_origin = {intent.transform_origin: intent for intent in BASE_INTENTS}

    assert all(record["statement"].startswith("평가용 가상 설정에서 ") for record in records)
    assert all(
        intents_by_origin[record["transform_origin"]].query_subject in record["statement"] for record in gold_records
    )
    assert all(record["product_code"] in record["statement"] for record in records)
    assert not any(phrase in content for content in retrieval_content for phrase in class_label_phrases)


def test_issue_273_no_hard_negative_reproduces_or_answers_any_gold_intent() -> None:
    records = _labelled_records(build_issue_273_dev_graph())
    gold_statements = [record["statement"] for record in records if record["record_kind"] == "GOLD"]
    negative_records = [record for record in records if record["record_kind"] == "HARD_NEGATIVE"]
    normalized_gold = {re.sub(r"[^0-9A-Za-z가-힣]", "", statement).casefold() for statement in gold_statements}
    intents_by_product = {intent.product_code: intent for intent in BASE_INTENTS}

    assert len(gold_statements) == 20
    assert len(negative_records) == 80
    for record in negative_records:
        statement = record["statement"]
        source_intent = intents_by_product[record["product_code"]]
        normalized_statement = re.sub(r"[^0-9A-Za-z가-힣]", "", statement).casefold()
        assert not any(gold_statement in statement for gold_statement in gold_statements)
        assert normalized_statement not in normalized_gold
        assert source_intent.query_subject not in statement
        assert [code for code in RESERVED_PRODUCT_CODES if code in statement] == [record["product_code"]]


def test_issue_273_retrieval_projection_carries_no_evaluation_label() -> None:
    """Whatever a Knowledge Evidence Adapter indexes must not reveal which record is the answer.

    `record_kind` alone would separate all twenty Gold records from the eighty negatives, and
    `transform_origin`/`adversarial_for_transform_origin` say which question each record serves or
    attacks. Recall/MRR computed over an index carrying those measures label lookup, not retrieval.
    """
    graph = build_issue_273_dev_graph()
    index = json.loads(graph[INDEX_PATH])
    labels = json.loads(graph[LABEL_PATH])

    for record in index["records"]:
        assert EVALUATION_LABEL_KEYS.isdisjoint(record), record
    # Not just absent from `records` — absent from the whole artifact, so that indexing the file
    # wholesale cannot leak them either.
    index_text = graph[INDEX_PATH].decode("utf-8")
    for label_token in ("GOLD", "HARD_NEGATIVE", *NEGATIVE_TYPES, "adversarial_for_transform_origin"):
        assert label_token not in index_text, label_token

    assert len(labels["labels"]) == 100
    assert {item["evidence_ref_id"] for item in labels["labels"]} == {
        record["evidence_ref_id"] for record in index["records"]
    }
    # The sidecar is bound by hash from the retrieval artifact, so the split stays verifiable.
    assert index["evaluation_label_ref"] == {"path": LABEL_PATH, "sha256": sha256_hex(graph[LABEL_PATH])}


def test_issue_273_retrieval_projection_hides_the_role_case_insensitively() -> None:
    """Case-insensitive because the first version of this guard was case-sensitive and missed it.

    After the label fields moved to the sidecar, the record ids still read `…-gold` and `…-neg-NN`,
    so all twenty Gold records were still identifiable by name. Upper-case-only checks passed. So
    did a Gold set sitting at every fifth position. Both are covered here.
    """
    graph = build_issue_273_dev_graph()
    index_text = graph[INDEX_PATH].decode("utf-8").casefold()
    for marker in ("gold", "negative", "neg-", "adversarial", "distractor", "정답", "오답"):
        assert marker.casefold() not in index_text, marker

    records = _labelled_records(graph)
    identifiers = [record["evidence_ref_id"] for record in records]
    assert len(set(identifiers)) == 100
    assert identifiers == sorted(identifiers), "corpus order must follow the neutral id, not authoring order"
    for record in records:
        assert record["evidence_ref_id"] == f"ev-nlr-{record['content_sha256'][:16]}"

    # Position must not identify Gold either: reject any constant stride.
    gold_positions = [index for index, record in enumerate(records) if record["record_kind"] == "GOLD"]
    assert len(gold_positions) == 20
    strides = {second - first for first, second in zip(gold_positions, gold_positions[1:], strict=False)}
    assert len(strides) > 1, gold_positions


def test_issue_273_each_negative_type_realises_its_overlap_in_the_statement() -> None:
    """A negative type has to be true of the retrieved sentence, not only of its metadata."""
    graph = build_issue_273_dev_graph()
    records = _labelled_records(graph)
    intents_by_origin = {intent.transform_origin: intent for intent in BASE_INTENTS}
    negatives = [record for record in records if record["record_kind"] == "HARD_NEGATIVE"]
    gold_answer_fragments = {
        record["statement"].split("는 ", maxsplit=1)[-1] for record in records if record["record_kind"] == "GOLD"
    }

    assert len(negatives) == 80
    for record in negatives:
        target = intents_by_origin[record["adversarial_for_transform_origin"]]
        statement = record["statement"]
        negative_type = record["negative_type"]

        if negative_type == "SAME_FAMILY_DIFFERENT_ATTRIBUTE":
            assert target.product_code in statement, statement
        elif negative_type == "SAME_TOPIC_DIFFERENT_FAMILY":
            assert TOPIC_OVERLAP_TERMS[target.topic] in statement, statement
            assert record["product_code"] != target.product_code
        elif negative_type == "LEXICAL_OVERLAP_UNSUPPORTED":
            assert TOPIC_OVERLAP_TERMS[target.topic] in statement, statement
        else:
            assert target.query_subject in statement, statement
            assert record["topic"] != target.topic

        # Sharing the question's wording is the point; carrying the answer is not.
        assert not any(fragment in statement for fragment in gold_answer_fragments), statement


def test_issue_273_hard_negative_types_use_fixed_unqueried_fact_categories() -> None:
    records = _labelled_records(build_issue_273_dev_graph())
    intents_by_origin = {intent.transform_origin: intent for intent in BASE_INTENTS}
    intents_by_product = {intent.product_code: intent for intent in BASE_INTENTS}
    intent_indexes = {intent.transform_origin: index for index, intent in enumerate(BASE_INTENTS)}
    required_fact_category = {
        "SAME_FAMILY_DIFFERENT_ATTRIBUTE": "포장 관리 코드",
        "SAME_TOPIC_DIFFERENT_FAMILY": "관리 번호로만 등록",
        "LEXICAL_OVERLAP_UNSUPPORTED": "합성 색인의",
        "CROSS_TOPIC_OVERLAP": "표에만 표시되고 실제 내용은 비어",
    }
    negative_statements = {record["statement"] for record in records if record["record_kind"] == "HARD_NEGATIVE"}

    assert len(negative_statements) == 80
    assert {record["product_code"] for record in records} == set(RESERVED_PRODUCT_CODES)

    for record in records:
        if record["record_kind"] != "HARD_NEGATIVE":
            continue
        negative_type = record["negative_type"]
        target_intent = intents_by_origin[record["adversarial_for_transform_origin"]]
        source_intent = intents_by_product[record["product_code"]]
        target_index = intent_indexes[target_intent.transform_origin]
        topic_start = (target_index // 4) * 4
        expected_source_by_type = {
            "SAME_FAMILY_DIFFERENT_ATTRIBUTE": target_intent.product_code,
            "SAME_TOPIC_DIFFERENT_FAMILY": BASE_INTENTS[topic_start + ((target_index + 1) % 4)].product_code,
            "LEXICAL_OVERLAP_UNSUPPORTED": target_intent.product_code,
            "CROSS_TOPIC_OVERLAP": BASE_INTENTS[(target_index + 4) % len(BASE_INTENTS)].product_code,
        }
        assert required_fact_category[negative_type] in record["statement"]
        assert record["product_code"] == expected_source_by_type[negative_type]
        assert record["topic"] == source_intent.topic
        if negative_type in {"SAME_FAMILY_DIFFERENT_ATTRIBUTE", "LEXICAL_OVERLAP_UNSUPPORTED"}:
            assert source_intent == target_intent
        elif negative_type == "SAME_TOPIC_DIFFERENT_FAMILY":
            assert source_intent != target_intent
            assert source_intent.topic == target_intent.topic
        else:
            assert source_intent.topic != target_intent.topic


def test_issue_273_cross_topic_distractors_use_their_source_topic_and_product() -> None:
    records = _labelled_records(build_issue_273_dev_graph())
    intents_by_product = {intent.product_code: intent for intent in BASE_INTENTS}
    intents_by_origin = {intent.transform_origin: intent for intent in BASE_INTENTS}
    cross_topic_records = [record for record in records if record["negative_type"] == "CROSS_TOPIC_OVERLAP"]

    assert len(cross_topic_records) == 20
    assert all(record["topic"] == intents_by_product[record["product_code"]].topic for record in records)
    for record in cross_topic_records:
        source_intent = intents_by_product[record["product_code"]]
        target_intent = intents_by_origin[record["adversarial_for_transform_origin"]]
        assert record["topic"] == source_intent.topic
        assert record["topic"] != target_intent.topic
        assert record["product_code"] in record["statement"]


def test_issue_273_reviewed_query_particles_are_natural_korean() -> None:
    graph = build_issue_273_dev_graph()
    cases = {
        json.loads(content)["case_id"]: json.loads(content)
        for path, content in graph.items()
        if path.startswith(CASE_PREFIX)
    }
    queries = [case["query"] for case in cases.values()]
    limited_typo_queries = [case["query"] for case in cases.values() if "EXPRESSION_LIMITED_TYPO" in case["slice_ids"]]

    assert cases["rag-nlr-dev-014"]["query"] == "NLR-PC01 제품, 먹기 전에 무엇을 조심해야 하는지 알고 싶어요."
    assert cases["rag-nlr-dev-015"]["query"] == "NLR-PC01 제품 복용 전 주의사항이 궁금해요."
    assert cases["rag-nlr-dev-022"]["query"] == "NLR-PC04 제품, 언제 의료진에게 물어봐야 하는지 알고 싶어요."
    content_request_expressions = {"EXPRESSION_SYNONYM", "EXPRESSION_WORD_ORDER_PARTICLE"}
    content_requests = [
        case["query"] for case in cases.values() if content_request_expressions.intersection(case["slice_ids"])
    ]
    assert all("어떻게 확인" not in query and "어디서 확인" not in query for query in content_requests)
    assert all(query.endswith(("알고 싶어요.", "제품이요.")) for query in content_requests)
    assert not any(malformed in query for query in queries for malformed in ("주의사항를", "주의사항가", "조건를"))
    assert len(limited_typo_queries) == 10
    assert all("알려 주새요." in query for query in limited_typo_queries)
    assert [query for query in queries if "알려 주새요." in query] == limited_typo_queries


def test_issue_273_cases_and_mapping_resolve_to_each_origins_single_gold() -> None:
    graph = build_issue_273_dev_graph()
    records = _labelled_records(graph)
    mapping = json.loads(graph[MAPPING_PATH])
    validated_mapping = EvidenceMappingManifestV12.model_validate_json(graph[MAPPING_PATH])
    cases = [json.loads(content) for path, content in graph.items() if path.startswith(CASE_PREFIX)]
    gold_by_origin = {record["transform_origin"]: record for record in records if record["record_kind"] == "GOLD"}
    mapping_by_id = {entry["evidence_ref_id"]: entry for entry in mapping["entries"]}
    index_sha256 = sha256_hex(graph[INDEX_PATH])

    gold_mapping_by_id = {
        evidence_id: mapping_by_id[evidence_id]
        for evidence_id in {record["evidence_ref_id"] for record in gold_by_origin.values()}
    }
    assert len(mapping_by_id) == 23
    assert len(gold_mapping_by_id) == 20
    assert validated_mapping.schema_id == "rag-eval.evidence-mapping-manifest"
    assert validated_mapping.schema_version == "1.2.0"
    assert validated_mapping.model_dump(mode="json") == mapping
    assert set(gold_mapping_by_id) == {record["evidence_ref_id"] for record in gold_by_origin.values()}
    for case in cases:
        origin = case["leakage_group_ids"]["transform_origin"]
        gold_id = gold_by_origin[origin]["evidence_ref_id"]
        assert case["expected"]["required_evidence_refs"] == [gold_id]
        assert case["expected"]["relevant_evidence_refs"] == [gold_id]

    # Chunk numbering follows the corpus order, which is the content-addressed id order rather than
    # the authoring order — so derive the expected locator and key from the committed positions.
    gold_positions = [index for index, record in enumerate(records) if record["record_kind"] == "GOLD"]
    for gold_index, record_index in enumerate(gold_positions):
        gold_id = records[record_index]["evidence_ref_id"]
        entry = mapping_by_id[gold_id]
        assert entry == {
            "content_sha256": index_sha256,
            "evidence_ref_id": gold_id,
            "evidence_type": "KNOWLEDGE_CHUNK",
            "fixture_record_ref": {"path": INDEX_PATH, "sha256": index_sha256},
            "locator": f"$.records[{record_index}]",
            "runtime_typed_ref": None,
            "source_version": "1.0.0",
            "stable_key": f"SYNTHETIC_NLR_CHUNK_{gold_index + 1:03d}",
            "target_kind": "FIXTURE_RECORD",
        }
    assert mapping["review_provenance"]["team_gold_status"] == "APPROVED"
    assert mapping["review_provenance"]["evidence_review_refs"]
    assert mapping["review_provenance"]["reviewed_by"]["actor_id"] == "hazelnutflavoured"
    assert mapping["review_provenance"]["approved_by"]["actor_id"] == "phina-io"
    assert mapping["manifest_sha256"] == canonical_sha256(
        mapping,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )


def test_issue_273_committed_graph_artifacts_match_fresh_build() -> None:
    graph = build_issue_273_dev_graph()

    assert all((EVALS_ROOT / path).read_bytes() == content for path, content in graph.items())
