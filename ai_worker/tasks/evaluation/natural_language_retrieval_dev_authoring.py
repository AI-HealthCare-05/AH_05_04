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
type NegativeType = Literal[
    "SAME_FAMILY_DIFFERENT_ATTRIBUTE",
    "SAME_TOPIC_DIFFERENT_FAMILY",
    "LEXICAL_OVERLAP_UNSUPPORTED",
    "CROSS_TOPIC_OVERLAP",
]

DATASET_CODE = "rag-natural-language-retrieval-dev"
DATASET_VERSION = "1.0.0"
FILE_PREFIX = "rag-natural-language-retrieval-dev-v1"
CASE_DIRECTORY = f"retrieval/cases/{FILE_PREFIX}"
INDEX_PATH = f"retrieval/evidence/resources/{FILE_PREFIX}/synthetic-knowledge-index.json"
EVIDENCE_MAPPING_PATH = f"retrieval/evidence/{FILE_PREFIX}.evidence-mapping.json"

NEGATIVE_TYPES: tuple[NegativeType, ...] = (
    "SAME_FAMILY_DIFFERENT_ATTRIBUTE",
    "SAME_TOPIC_DIFFERENT_FAMILY",
    "LEXICAL_OVERLAP_UNSUPPORTED",
    "CROSS_TOPIC_OVERLAP",
)

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
    product_code: str
    topic: Topic
    record_kind: Literal["GOLD", "HARD_NEGATIVE"]
    statement: str
    content_sha256: str
    negative_type: NegativeType | None
    adversarial_for_transform_origin: str | None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "adversarial_for_transform_origin": self.adversarial_for_transform_origin,
            "content_sha256": self.content_sha256,
            "evidence_ref_id": self.evidence_ref_id,
            "negative_type": self.negative_type,
            "product_code": self.product_code,
            "record_kind": self.record_kind,
            "statement": self.statement,
            "topic": self.topic,
            "transform_origin": self.transform_origin,
        }


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
    INDEX_PATH,
    EVIDENCE_MAPPING_PATH,
    f"retrieval/manifests/{FILE_PREFIX}.critical-claim-rubric.json",
    f"retrieval/manifests/{FILE_PREFIX}.authoring-identities.json",
    f"retrieval/manifests/{FILE_PREFIX}.dataset.json",
    f"profiles/{FILE_PREFIX}.profile.json",
    f"policies/{FILE_PREFIX}.comparison-policy.json",
    f"policies/{FILE_PREFIX}.evaluation-policy.json",
    f"suites/{FILE_PREFIX}.suite.json",
    f"provenance/{FILE_PREFIX}.protected-artifact-receipt.json",
)
_IMPLEMENTED_GRAPH_PATHS = (INDEX_PATH, EVIDENCE_MAPPING_PATH)

_TOPIC_OVERLAP_TERMS: dict[Topic, str] = {
    "TOPIC_MEDICATION_INFORMATION": "제품 정보",
    "TOPIC_PRECAUTIONS": "주의 안내",
    "TOPIC_LIFESTYLE_MANAGEMENT": "생활 관리",
    "TOPIC_STORAGE": "보관 안내",
    "TOPIC_MISSED_DOSE": "복용 누락 안내",
}

_DRAFT_REVIEW_PROVENANCE: JsonValue = {
    "approved_at": None,
    "approved_by": None,
    "authored_at": "2026-09-05T00:00:00.000000Z",
    "authored_by": {
        "actor_id": "ceohwj",
        "namespace": "GITHUB_LOGIN",
        "role": "EVALUATION_IMPLEMENTER",
    },
    "evidence_review_refs": [],
    "external_medical_approval_receipt_ref": None,
    "external_medical_review_status": "NOT_REQUESTED",
    "reviewed_at": None,
    "reviewed_by": None,
    "team_gold_status": "DRAFT",
}


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


def _record(
    *,
    evidence_ref_id: str,
    transform_origin: str,
    product_code: str,
    topic: Topic,
    record_kind: Literal["GOLD", "HARD_NEGATIVE"],
    statement: str,
    negative_type: NegativeType | None = None,
    adversarial_for_transform_origin: str | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_ref_id=evidence_ref_id,
        transform_origin=transform_origin,
        product_code=product_code,
        topic=topic,
        record_kind=record_kind,
        statement=statement,
        content_sha256=sha256_hex(statement.encode("utf-8")),
        negative_type=negative_type,
        adversarial_for_transform_origin=adversarial_for_transform_origin,
    )


def _build_evidence_records() -> tuple[EvidenceRecord, ...]:
    records: list[EvidenceRecord] = []
    for index, intent in enumerate(BASE_INTENTS):
        origin_lower = intent.transform_origin.lower()
        records.append(
            _record(
                evidence_ref_id=f"ev-nlr-{origin_lower}-gold",
                transform_origin=intent.transform_origin,
                product_code=intent.product_code,
                topic=intent.topic,
                record_kind="GOLD",
                statement=(
                    f"{intent.product_code} 제품의 {intent.gold_intent}는 이 평가용 가상 지식에서 "
                    "해당 질문을 뒷받침하는 정답 항목으로 구분됩니다."
                ),
            )
        )

        topic_start = (index // 4) * 4
        same_topic_intent = BASE_INTENTS[topic_start + ((index + 1) % 4)]
        cross_topic_intent = BASE_INTENTS[(index + 4) % len(BASE_INTENTS)]
        statements = (
            (
                intent.product_code,
                f"{intent.product_code} 제품의 대조용 포장 순번은 이 합성 평가 자료에서 별도 항목으로 관리됩니다.",
            ),
            (
                same_topic_intent.product_code,
                f"{same_topic_intent.product_code} 제품의 {same_topic_intent.gold_intent}는 같은 주제에 "
                f"속하지만 {intent.product_code} 질문의 근거가 아닙니다.",
            ),
            (
                intent.product_code,
                f"{intent.product_code} 제품 관련 {_TOPIC_OVERLAP_TERMS[intent.topic]} 자료의 목차를 "
                "안내하지만, 질문에서 찾는 세부 속성은 제시하지 않습니다.",
            ),
            (
                cross_topic_intent.product_code,
                f"{cross_topic_intent.product_code} 제품의 {cross_topic_intent.gold_intent}는 "
                f"{intent.product_code} 질문과 일부 표현만 겹치는 다른 주제의 합성 자료입니다.",
            ),
        )
        for negative_number, (negative_type, (product_code, statement)) in enumerate(
            zip(NEGATIVE_TYPES, statements, strict=True),
            start=1,
        ):
            records.append(
                _record(
                    evidence_ref_id=f"ev-nlr-{origin_lower}-neg-{negative_number:02d}",
                    transform_origin=intent.transform_origin,
                    product_code=product_code,
                    topic=intent.topic,
                    record_kind="HARD_NEGATIVE",
                    statement=statement,
                    negative_type=negative_type,
                    adversarial_for_transform_origin=intent.transform_origin,
                )
            )
    return tuple(records)


def _build_evidence_mapping(index_bytes: bytes, records: tuple[EvidenceRecord, ...]) -> bytes:
    index_sha256 = sha256_hex(index_bytes)
    entries: list[JsonValue] = []
    for gold_number, record_index in enumerate(range(0, len(records), 5), start=1):
        record = records[record_index]
        entries.append(
            {
                "content_sha256": index_sha256,
                "evidence_ref_id": record.evidence_ref_id,
                "evidence_type": "KNOWLEDGE_CHUNK",
                "fixture_record_ref": {"path": INDEX_PATH, "sha256": index_sha256},
                "locator": f"$.records[{record_index}]",
                "runtime_typed_ref": None,
                "source_version": DATASET_VERSION,
                "stable_key": f"SYNTHETIC_NLR_GOLD_{gold_number:03d}",
                "target_kind": "FIXTURE_RECORD",
            }
        )
    payload: dict[str, JsonValue] = {
        "entries": entries,
        "manifest_sha256": "0" * 64,
        "mapping_id": f"{DATASET_CODE}-evidence",
        "mapping_version": DATASET_VERSION,
        "review_provenance": _DRAFT_REVIEW_PROVENANCE,
        "schema_id": "rag-eval.evidence-mapping-manifest",
        "schema_version": "1.2.0",
    }
    payload["manifest_sha256"] = canonical_sha256(
        payload,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    return canonical_json_bytes(payload)


def build_issue_273_dev_graph() -> dict[str, bytes]:
    _validate_catalog()
    graph = {path: canonical_json_bytes({}) for path in _GRAPH_MEMBER_PATHS}
    evidence_records = _build_evidence_records()
    index_payload: JsonValue = {
        "data_classification": "SYNTHETIC",
        "index_id": f"{DATASET_CODE}-synthetic-index",
        "index_version": DATASET_VERSION,
        "records": [record.to_json() for record in evidence_records],
    }
    graph[INDEX_PATH] = canonical_json_bytes(index_payload)
    graph[EVIDENCE_MAPPING_PATH] = _build_evidence_mapping(graph[INDEX_PATH], evidence_records)
    case_number = 1
    for intent in BASE_INTENTS:
        gold_id = f"ev-nlr-{intent.transform_origin.lower()}-gold"
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
                "relevant_evidence_refs": [gold_id],
                "required_evidence_refs": [gold_id],
                "topic": intent.topic,
                "transform_origin": intent.transform_origin,
            }
            graph[f"{CASE_DIRECTORY}/{case_id}.json"] = canonical_json_bytes(payload)
            case_number += 1
    return graph


def write_issue_273_dev_graph(evals_root: Path) -> None:
    graph = build_issue_273_dev_graph()
    for relative_path in _IMPLEMENTED_GRAPH_PATHS:
        content = graph[relative_path]
        destination = evals_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
