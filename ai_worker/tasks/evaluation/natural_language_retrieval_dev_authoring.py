from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256, sha256_hex

type Topic = Literal[
    "TOPIC_MEDICATION_INFORMATION",
    "TOPIC_PRECAUTIONS",
    "TOPIC_LIFESTYLE_MANAGEMENT",
    "TOPIC_STORAGE",
    "TOPIC_MISSED_DOSE",
]
type Expression = Literal[
    "EXPRESSION_CANONICAL",
    "EXPRESSION_SYNONYM",
    "EXPRESSION_WORD_ORDER_PARTICLE",
    "EXPRESSION_COLLOQUIAL",
    "EXPRESSION_FRAGMENT",
    "EXPRESSION_LIMITED_TYPO",
]

DATASET_CODE = "rag-natural-language-retrieval-dev"
DATASET_VERSION = "1.0.0"
FILE_PREFIX = "rag-natural-language-retrieval-dev-v1"
CASE_DIRECTORY = f"retrieval/cases/{FILE_PREFIX}"

EXPRESSION_PLAN: tuple[tuple[Expression, Expression, Expression], ...] = (
    ("EXPRESSION_CANONICAL", "EXPRESSION_SYNONYM", "EXPRESSION_COLLOQUIAL"),
    ("EXPRESSION_WORD_ORDER_PARTICLE", "EXPRESSION_FRAGMENT", "EXPRESSION_LIMITED_TYPO"),
    ("EXPRESSION_CANONICAL", "EXPRESSION_WORD_ORDER_PARTICLE", "EXPRESSION_COLLOQUIAL"),
    ("EXPRESSION_SYNONYM", "EXPRESSION_FRAGMENT", "EXPRESSION_LIMITED_TYPO"),
)

RESERVED_PRODUCT_CODES = (
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


@dataclass(frozen=True, slots=True)
class QuestionVariant:
    expression: Expression
    query: str


@dataclass(frozen=True, slots=True)
class BaseIntent:
    topic: Topic
    transform_origin: str
    product_code: str
    gold_intent: str
    query_subject: str
    variants: tuple[QuestionVariant, QuestionVariant, QuestionVariant]


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_ref_id: str
    transform_origin: str
    record_kind: Literal["GOLD", "HARD_NEGATIVE"]
    statement: str
    content_sha256: str


def _question(expression: Expression, product_code: str, subject: str) -> str:
    if expression == "EXPRESSION_CANONICAL":
        return f"{product_code} 제품의 {subject}에 대해 알려 주세요."
    if expression == "EXPRESSION_SYNONYM":
        return f"{product_code} 제품에서 {subject}를 어떻게 확인할 수 있나요?"
    if expression == "EXPRESSION_WORD_ORDER_PARTICLE":
        return f"{product_code} {subject}, 어디서 확인하나요?"
    if expression == "EXPRESSION_COLLOQUIAL":
        return f"{product_code} 제품 {subject}가 궁금해요."
    if expression == "EXPRESSION_FRAGMENT":
        return f"{product_code} 제품 {subject} 궁금합니다."
    return f"{product_code} 제품의 {subject}에 대해 알려 주새요."


def _intent(
    topic: Topic,
    origin: str,
    gold_intent: str,
    query_subject: str,
    expressions: tuple[Expression, Expression, Expression],
) -> BaseIntent:
    variants = tuple(
        QuestionVariant(expression=expression, query=_question(expression, origin, query_subject))
        for expression in expressions
    )
    return BaseIntent(
        topic=topic,
        transform_origin=origin,
        product_code=origin,
        gold_intent=gold_intent,
        query_subject=query_subject,
        variants=cast(tuple[QuestionVariant, QuestionVariant, QuestionVariant], variants),
    )


BASE_INTENTS = (
    _intent(
        "TOPIC_MEDICATION_INFORMATION",
        "NLR-MI01",
        "제품의 합성 성분 정보",
        "성분 정보",
        EXPRESSION_PLAN[0],
    ),
    _intent(
        "TOPIC_MEDICATION_INFORMATION",
        "NLR-MI02",
        "제품의 합성 제형·외형 정보",
        "제형과 외형 정보",
        EXPRESSION_PLAN[1],
    ),
    _intent(
        "TOPIC_MEDICATION_INFORMATION",
        "NLR-MI03",
        "제품의 합성 사용 목적 정보",
        "사용 목적 정보",
        EXPRESSION_PLAN[2],
    ),
    _intent(
        "TOPIC_MEDICATION_INFORMATION",
        "NLR-MI04",
        "제품 라벨의 합성 식별 정보",
        "라벨 식별 정보",
        EXPRESSION_PLAN[3],
    ),
    _intent(
        "TOPIC_PRECAUTIONS",
        "NLR-PC01",
        "복용 전 확인할 합성 주의사항",
        "복용 전 주의사항",
        EXPRESSION_PLAN[0],
    ),
    _intent(
        "TOPIC_PRECAUTIONS",
        "NLR-PC02",
        "합성 알레르기 경고 정보",
        "알레르기 경고 정보",
        EXPRESSION_PLAN[1],
    ),
    _intent(
        "TOPIC_PRECAUTIONS",
        "NLR-PC03",
        "합성 이상 반응 관찰 정보",
        "이상 반응 관찰 정보",
        EXPRESSION_PLAN[2],
    ),
    _intent(
        "TOPIC_PRECAUTIONS",
        "NLR-PC04",
        "전문가 확인이 필요한 합성 조건",
        "전문가 확인이 필요한 조건",
        EXPRESSION_PLAN[3],
    ),
    _intent(
        "TOPIC_LIFESTYLE_MANAGEMENT",
        "NLR-LM01",
        "합성 수분 섭취 안내",
        "수분 섭취 안내",
        EXPRESSION_PLAN[0],
    ),
    _intent(
        "TOPIC_LIFESTYLE_MANAGEMENT",
        "NLR-LM02",
        "합성 식사 습관 안내",
        "식사 습관 안내",
        EXPRESSION_PLAN[1],
    ),
    _intent(
        "TOPIC_LIFESTYLE_MANAGEMENT",
        "NLR-LM03",
        "합성 활동 안내",
        "활동 안내",
        EXPRESSION_PLAN[2],
    ),
    _intent(
        "TOPIC_LIFESTYLE_MANAGEMENT",
        "NLR-LM04",
        "합성 상태 기록 안내",
        "상태 기록 안내",
        EXPRESSION_PLAN[3],
    ),
    _intent(
        "TOPIC_STORAGE",
        "NLR-ST01",
        "합성 보관 온도 정보",
        "보관 온도 정보",
        EXPRESSION_PLAN[0],
    ),
    _intent(
        "TOPIC_STORAGE",
        "NLR-ST02",
        "합성 빛·습기 차단 정보",
        "빛과 습기 차단 정보",
        EXPRESSION_PLAN[1],
    ),
    _intent(
        "TOPIC_STORAGE",
        "NLR-ST03",
        "합성 안전 보관 위치 정보",
        "안전한 보관 위치 정보",
        EXPRESSION_PLAN[2],
    ),
    _intent(
        "TOPIC_STORAGE",
        "NLR-ST04",
        "합성 원래 용기 보관 정보",
        "원래 용기 보관 정보",
        EXPRESSION_PLAN[3],
    ),
    _intent(
        "TOPIC_MISSED_DOSE",
        "NLR-MD01",
        "누락을 일찍 알았을 때의 합성 안내",
        "복용 누락을 일찍 알았을 때의 안내",
        EXPRESSION_PLAN[0],
    ),
    _intent(
        "TOPIC_MISSED_DOSE",
        "NLR-MD02",
        "다음 시각이 가까울 때의 합성 안내",
        "다음 복용 시각이 가까울 때의 안내",
        EXPRESSION_PLAN[1],
    ),
    _intent(
        "TOPIC_MISSED_DOSE",
        "NLR-MD03",
        "합성 중복 복용 금지 안내",
        "중복 복용 금지 안내",
        EXPRESSION_PLAN[2],
    ),
    _intent(
        "TOPIC_MISSED_DOSE",
        "NLR-MD04",
        "반복 누락 시 전문가 상담 안내",
        "반복해서 복용을 놓쳤을 때의 전문가 상담 안내",
        EXPRESSION_PLAN[3],
    ),
)

_GRAPH_MEMBER_PATHS = (
    f"retrieval/evidence/resources/{FILE_PREFIX}/synthetic-knowledge-index.json",
    f"retrieval/evidence/{FILE_PREFIX}.evidence-mapping.json",
    f"retrieval/manifests/{FILE_PREFIX}.critical-claim-rubric.json",
    f"retrieval/manifests/{FILE_PREFIX}.authoring-identities.json",
    f"retrieval/manifests/{FILE_PREFIX}.dataset.json",
    f"profiles/{FILE_PREFIX}.profile.json",
    f"policies/{FILE_PREFIX}.comparison-policy.json",
    f"policies/{FILE_PREFIX}.evaluation-policy.json",
    f"suites/{FILE_PREFIX}.suite.json",
    f"provenance/{FILE_PREFIX}.protected-artifact-receipt.json",
)


def _validate_catalog() -> None:
    if len(BASE_INTENTS) != 20:
        raise RuntimeError("Issue 273 authoring catalog must contain exactly 20 base intents")
    if tuple(intent.product_code for intent in BASE_INTENTS) != RESERVED_PRODUCT_CODES:
        raise RuntimeError("Issue 273 base intents must use the reserved product allowlist in order")
    if any(intent.transform_origin != intent.product_code for intent in BASE_INTENTS):
        raise RuntimeError("Issue 273 transform origins must match reserved product codes")
    if any(
        tuple(variant.expression for variant in intent.variants) != EXPRESSION_PLAN[index % len(EXPRESSION_PLAN)]
        for index, intent in enumerate(BASE_INTENTS)
    ):
        raise RuntimeError("Issue 273 base intents must follow the fixed expression plan")
    expression_counts = Counter(variant.expression for intent in BASE_INTENTS for variant in intent.variants)
    if set(expression_counts.values()) != {10}:
        raise RuntimeError("Issue 273 expression plan must produce ten cases per expression")


def build_issue_273_dev_graph() -> dict[str, bytes]:
    _validate_catalog()
    graph = {path: canonical_json_bytes({}) for path in _GRAPH_MEMBER_PATHS}
    case_number = 1
    for intent in BASE_INTENTS:
        for variant in intent.variants:
            case_id = f"rag-nlr-dev-{case_number:03d}"
            input_value: JsonValue = {
                "product_code": intent.product_code,
                "query": variant.query,
                "transform_origin": intent.transform_origin,
            }
            payload: JsonValue = {
                "case_id": case_id,
                "dataset_code": DATASET_CODE,
                "dataset_version": DATASET_VERSION,
                "expression": variant.expression,
                "input_sha256": canonical_sha256(input_value),
                "product_code": intent.product_code,
                "query": variant.query,
                "query_sha256": sha256_hex(variant.query.encode("utf-8")),
                "topic": intent.topic,
                "transform_origin": intent.transform_origin,
            }
            graph[f"{CASE_DIRECTORY}/{case_id}.json"] = canonical_json_bytes(payload)
            case_number += 1
    return graph


def write_issue_273_dev_graph(evals_root: Path) -> None:
    for relative_path, content in build_issue_273_dev_graph().items():
        destination = evals_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
