from __future__ import annotations

import json
import re
from collections import Counter

from ai_worker.tasks.evaluation.natural_language_retrieval_dev_authoring import (
    RESERVED_PRODUCT_CODES,
    build_issue_273_dev_graph,
)

CASE_PREFIX = "retrieval/cases/rag-natural-language-retrieval-dev-v1/"


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
