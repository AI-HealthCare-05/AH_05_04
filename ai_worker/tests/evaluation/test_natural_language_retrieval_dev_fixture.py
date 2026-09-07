from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from ai_worker.tasks.evaluation.canonical import canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.natural_language_retrieval_dev_authoring import (
    BASE_INTENTS,
    RESERVED_PRODUCT_CODES,
    build_issue_273_dev_graph,
)
from ai_worker.tasks.evaluation.schemas.authoring_v1_2 import EvidenceMappingManifestV12

CASE_PREFIX = "retrieval/cases/rag-natural-language-retrieval-dev-v1/"
INDEX_PATH = "retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json"
MAPPING_PATH = "retrieval/evidence/rag-natural-language-retrieval-dev-v1.evidence-mapping.json"
NEGATIVE_TYPES = {
    "SAME_FAMILY_DIFFERENT_ATTRIBUTE",
    "SAME_TOPIC_DIFFERENT_FAMILY",
    "LEXICAL_OVERLAP_UNSUPPORTED",
    "CROSS_TOPIC_OVERLAP",
}
EVALS_ROOT = Path(__file__).parents[3] / "evals"


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
    assert Counter(case["topic"] for case in cases) == {
        "TOPIC_MEDICATION_INFORMATION": 12,
        "TOPIC_PRECAUTIONS": 12,
        "TOPIC_LIFESTYLE_MANAGEMENT": 12,
        "TOPIC_STORAGE": 12,
        "TOPIC_MISSED_DOSE": 12,
    }
    assert Counter(case["expression"] for case in cases) == {
        "EXPRESSION_CANONICAL": 10,
        "EXPRESSION_SYNONYM": 10,
        "EXPRESSION_WORD_ORDER_PARTICLE": 10,
        "EXPRESSION_COLLOQUIAL": 10,
        "EXPRESSION_FRAGMENT": 10,
        "EXPRESSION_LIMITED_TYPO": 10,
    }
    assert Counter(case["transform_origin"] for case in cases) == {code: 3 for code in RESERVED_PRODUCT_CODES}
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


def test_issue_273_corpus_statements_are_sentence_ready_natural_korean() -> None:
    records = json.loads(build_issue_273_dev_graph()[INDEX_PATH])["records"]
    statements_by_id = {record["evidence_ref_id"]: record["statement"] for record in records}

    assert not any(
        malformed in statement
        for statement in statements_by_id.values()
        for malformed in ("제품의 제품의", "주의사항는", "조건는")
    )
    assert statements_by_id["ev-nlr-nlr-mi01-gold"] == (
        "NLR-MI01 제품의 합성 성분 정보에 관한 평가용 가상 지식은 해당 질문을 뒷받침하는 정답 항목으로 구분됩니다."
    )
    assert statements_by_id["ev-nlr-nlr-pc01-gold"] == (
        "NLR-PC01 제품의 복용 전 확인할 합성 주의사항에 관한 평가용 가상 지식은 "
        "해당 질문을 뒷받침하는 정답 항목으로 구분됩니다."
    )
    assert statements_by_id["ev-nlr-nlr-pc04-gold"] == (
        "NLR-PC04 제품의 전문가 확인이 필요한 합성 조건에 관한 평가용 가상 지식은 "
        "해당 질문을 뒷받침하는 정답 항목으로 구분됩니다."
    )
    assert statements_by_id["ev-nlr-nlr-mi01-neg-02"] == (
        "NLR-MI02 제품의 합성 제형·외형 정보 항목은 같은 주제에 속하지만 NLR-MI01 질문의 근거가 아닙니다."
    )
    assert statements_by_id["ev-nlr-nlr-mi01-neg-03"] == (
        "NLR-MI01 관련 제품 정보 자료의 목차를 안내하지만, 질문에서 찾는 세부 속성은 제시하지 않습니다."
    )


def test_issue_273_cases_and_mapping_resolve_to_each_origins_single_gold() -> None:
    graph = build_issue_273_dev_graph()
    records = json.loads(graph[INDEX_PATH])["records"]
    mapping = json.loads(graph[MAPPING_PATH])
    validated_mapping = EvidenceMappingManifestV12.model_validate_json(graph[MAPPING_PATH])
    cases = [json.loads(content) for path, content in graph.items() if path.startswith(CASE_PREFIX)]
    gold_by_origin = {record["transform_origin"]: record for record in records if record["record_kind"] == "GOLD"}
    mapping_by_id = {entry["evidence_ref_id"]: entry for entry in mapping["entries"]}
    index_sha256 = sha256_hex(graph[INDEX_PATH])

    assert len(mapping_by_id) == 20
    assert validated_mapping.schema_id == "rag-eval.evidence-mapping-manifest"
    assert validated_mapping.schema_version == "1.2.0"
    assert validated_mapping.model_dump(mode="json") == mapping
    assert set(mapping_by_id) == {record["evidence_ref_id"] for record in gold_by_origin.values()}
    for case in cases:
        gold_id = gold_by_origin[case["transform_origin"]]["evidence_ref_id"]
        assert case["required_evidence_refs"] == [gold_id]
        assert case["relevant_evidence_refs"] == [gold_id]

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


def test_issue_273_committed_corpus_artifacts_match_fresh_build() -> None:
    graph = build_issue_273_dev_graph()

    assert (EVALS_ROOT / INDEX_PATH).read_bytes() == graph[INDEX_PATH]
    assert (EVALS_ROOT / MAPPING_PATH).read_bytes() == graph[MAPPING_PATH]
