from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.loaders import _resolve_json_locator, load_dataset
from ai_worker.tasks.evaluation.natural_language_retrieval_dev_authoring import (
    BASE_INTENTS,
    FILE_PREFIX,
    RESERVED_PRODUCT_CODES,
    build_issue_273_dev_graph,
)
from ai_worker.tasks.evaluation.schemas.authoring_v1_2 import EvidenceMappingManifestV12

CASE_PREFIX = "retrieval/cases/rag-natural-language-retrieval-dev-v1/"
INDEX_PATH = "retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json"
MAPPING_PATH = "retrieval/evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json"
MANIFEST_PATH = "retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json"
AUTHORING_PATH = "retrieval/manifests/rag-natural-language-retrieval-dev-v1.authoring-identities.json"
RUBRIC_PATH = "retrieval/manifests/rag-natural-language-retrieval-dev-v1.critical-claim-rubric.json"
PROFILE_PATH = "profiles/rag-natural-language-retrieval-dev-v1.profile.json"
COMPARISON_PATH = "policies/rag-natural-language-retrieval-dev-v1.comparison-policy.json"
POLICY_PATH = "policies/rag-natural-language-retrieval-dev-v1.evaluation-policy.json"
SUITE_PATH = "suites/rag-natural-language-retrieval-dev-v1.suite.json"
RECEIPT_PATH = "provenance/rag-natural-language-retrieval-dev-v1.protected-artifact-receipt.json"
NEGATIVE_TYPES = {
    "SAME_FAMILY_DIFFERENT_ATTRIBUTE",
    "SAME_TOPIC_DIFFERENT_FAMILY",
    "LEXICAL_OVERLAP_UNSUPPORTED",
    "CROSS_TOPIC_OVERLAP",
}
EVALS_ROOT = Path(__file__).parents[3] / "evals"


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
            if key != "hash" and not key.endswith("_sha256") and not key.endswith("_hash")
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


def test_issue_273_cases_are_natural_korean_draft_retrieval_cases() -> None:
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
        assert case["review_provenance"]["team_gold_status"] == "DRAFT"
        assert case["expected"]["required_evidence_refs"] == case["expected"]["relevant_evidence_refs"]
        assert len(case["expected"]["required_evidence_refs"]) == 1
        assert re.search(r"[가-힣]", case["query"])
        assert not case["query"].startswith("SYNTHETIC_QUERY_")
        assert case["query"].endswith((".", "?"))
        assert case["slice_ids"] == sorted(["ALL", topic, expression])


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
    records = json.loads(graph[INDEX_PATH])["records"]
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


def test_issue_273_graph_has_only_draft_non_human_review_state() -> None:
    graph = build_issue_273_dev_graph()
    review_paths = (MAPPING_PATH, RUBRIC_PATH, PROFILE_PATH, POLICY_PATH, SUITE_PATH)
    provenance_values = [json.loads(graph[path])["review_provenance"] for path in review_paths]
    provenance_values.append(json.loads(graph[RECEIPT_PATH])["recorded_by"])
    provenance_values.append(json.loads(graph[MANIFEST_PATH])["review_provenance"])
    provenance_values.extend(
        json.loads(content)["review_provenance"] for path, content in graph.items() if path.startswith(CASE_PREFIX)
    )

    assert all(provenance["team_gold_status"] == "DRAFT" for provenance in provenance_values)
    assert all(provenance["reviewed_by"] is None for provenance in provenance_values)
    assert all(provenance["approved_by"] is None for provenance in provenance_values)
    assert all(provenance["evidence_review_refs"] == [] for provenance in provenance_values)


def test_issue_273_dev_graph_has_fixed_identity_and_distribution() -> None:
    graph = build_issue_273_dev_graph()
    case_paths = sorted(path for path in graph if path.startswith(CASE_PREFIX))
    cases = [json.loads(graph[path]) for path in case_paths]

    assert set(graph) == {
        *(f"{CASE_PREFIX}rag-nlr-dev-{index:03d}.json" for index in range(1, 61)),
        "retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json",
        "retrieval/evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json",
        "retrieval/manifests/rag-natural-language-retrieval-dev-v1.critical-claim-rubric.json",
        "retrieval/manifests/rag-natural-language-retrieval-dev-v1.authoring-identities.json",
        "retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json",
        "profiles/rag-natural-language-retrieval-dev-v1.profile.json",
        "policies/rag-natural-language-retrieval-dev-v1.comparison-policy.json",
        "policies/rag-natural-language-retrieval-dev-v1.evaluation-policy.json",
        "suites/rag-natural-language-retrieval-dev-v1.suite.json",
        "provenance/rag-natural-language-retrieval-dev-v1.protected-artifact-receipt.json",
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
    records = json.loads(graph[INDEX_PATH])["records"]
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
    expected_record_ids = [
        evidence_id
        for origin in RESERVED_PRODUCT_CODES
        for evidence_id in (
            f"ev-nlr-{origin.lower()}-gold",
            *(f"ev-nlr-{origin.lower()}-neg-{number:02d}" for number in range(1, 5)),
        )
    ]
    assert record_ids == expected_record_ids
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
    gold_stable_keys = {
        entry["stable_key"] for entry in mapping["entries"] if entry["evidence_ref_id"].endswith("-gold")
    }

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
    assert selected_index["gold_record_count"] == 20
    assert selected_index["hard_negative_record_count"] == 80


def test_issue_273_corpus_statements_are_sentence_ready_natural_korean() -> None:
    records = json.loads(build_issue_273_dev_graph()[INDEX_PATH])["records"]
    statements_by_id = {record["evidence_ref_id"]: record["statement"] for record in records}

    assert not any(
        malformed in statement
        for statement in statements_by_id.values()
        for malformed in ("제품의 제품의", "주의사항는", "조건는")
    )
    assert statements_by_id["ev-nlr-nlr-mi01-gold"] == (
        "평가용 가상 설정에서 NLR-MI01 제품의 성분 정보는 청색 결정 성분 하나로 구성됩니다."
    )
    assert statements_by_id["ev-nlr-nlr-pc01-gold"] == (
        "평가용 가상 설정에서 NLR-PC01 제품의 복용 전 주의사항은 봉인선과 확인표의 세 칸을 점검하는 절차입니다."
    )
    assert statements_by_id["ev-nlr-nlr-lm01-gold"] == (
        "평가용 가상 설정에서 NLR-LM01 제품의 수분 섭취 안내는 기록 카드의 물컵 세 칸을 차례로 표시하는 방식입니다."
    )
    assert statements_by_id["ev-nlr-nlr-st01-gold"] == (
        "평가용 가상 설정에서 NLR-ST01 제품의 보관 온도 정보는 가상 눈금 B 구간으로 지정됩니다."
    )
    assert statements_by_id["ev-nlr-nlr-md01-gold"] == (
        "평가용 가상 설정에서 NLR-MD01 제품의 복용 누락을 일찍 알았을 때의 안내는 기록 카드의 절차 A를 조회하는 것입니다."
    )


def test_issue_273_corpus_contains_substantive_facts_without_class_label_leakage() -> None:
    index = json.loads(build_issue_273_dev_graph()[INDEX_PATH])
    records = index["records"]
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


def test_issue_273_cross_topic_distractors_use_their_source_topic_and_product() -> None:
    records = json.loads(build_issue_273_dev_graph()[INDEX_PATH])["records"]
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

    assert cases["rag-nlr-dev-014"]["query"] == "NLR-PC01 제품의 복용 전 주의사항은 무엇인가요?"
    assert cases["rag-nlr-dev-015"]["query"] == "NLR-PC01 제품 복용 전 주의사항이 궁금해요."
    assert cases["rag-nlr-dev-022"]["query"] == "NLR-PC04 제품의 전문가 확인이 필요한 조건은 무엇인가요?"
    content_request_expressions = {"EXPRESSION_SYNONYM", "EXPRESSION_WORD_ORDER_PARTICLE"}
    content_requests = [
        case["query"] for case in cases.values() if content_request_expressions.intersection(case["slice_ids"])
    ]
    assert all("어떻게 확인" not in query and "어디서 확인" not in query for query in content_requests)
    assert all("무엇인가요?" in query or "알려 주세요." in query for query in content_requests)
    assert not any(malformed in query for query in queries for malformed in ("주의사항를", "주의사항가", "조건를"))
    assert len(limited_typo_queries) == 10
    assert all("알려 주새요." in query for query in limited_typo_queries)
    assert [query for query in queries if "알려 주새요." in query] == limited_typo_queries


def test_issue_273_cases_and_mapping_resolve_to_each_origins_single_gold() -> None:
    graph = build_issue_273_dev_graph()
    records = json.loads(graph[INDEX_PATH])["records"]
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

    for gold_index, intent in enumerate(BASE_INTENTS):
        gold_id = gold_by_origin[intent.transform_origin]["evidence_ref_id"]
        entry = mapping_by_id[gold_id]
        assert entry == {
            "content_sha256": index_sha256,
            "evidence_ref_id": gold_id,
            "evidence_type": "KNOWLEDGE_CHUNK",
            "fixture_record_ref": {"path": INDEX_PATH, "sha256": index_sha256},
            "locator": f"$.records[{gold_index * 5}]",
            "runtime_typed_ref": None,
            "source_version": "1.0.0",
            "stable_key": f"SYNTHETIC_NLR_GOLD_{gold_index + 1:03d}",
            "target_kind": "FIXTURE_RECORD",
        }
    assert mapping["review_provenance"]["team_gold_status"] == "DRAFT"
    assert mapping["review_provenance"]["evidence_review_refs"] == []
    assert mapping["review_provenance"]["reviewed_by"] is None
    assert mapping["review_provenance"]["approved_by"] is None
    assert mapping["manifest_sha256"] == canonical_sha256(
        mapping,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )


def test_issue_273_committed_graph_artifacts_match_fresh_build() -> None:
    graph = build_issue_273_dev_graph()

    assert all((EVALS_ROOT / path).read_bytes() == content for path, content in graph.items())
