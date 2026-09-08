from __future__ import annotations

import json
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
EVALUATION_LABEL_PATH = f"retrieval/evidence/resources/{FILE_PREFIX}/evaluation-labels.json"
EVIDENCE_MAPPING_PATH = f"retrieval/evidence/{FILE_PREFIX}.evidence-mapping.json"
RUBRIC_PATH = f"retrieval/manifests/{FILE_PREFIX}.critical-claim-rubric.json"
AUTHORING_IDENTITY_PATH = f"retrieval/manifests/{FILE_PREFIX}.authoring-identities.json"
DATASET_MANIFEST_PATH = f"retrieval/manifests/{FILE_PREFIX}.dataset.json"
PROFILE_PATH = f"profiles/{FILE_PREFIX}.profile.json"
COMPARISON_POLICY_PATH = f"policies/{FILE_PREFIX}.comparison-policy.json"
EVALUATION_POLICY_PATH = f"policies/{FILE_PREFIX}.evaluation-policy.json"
SUITE_PATH = f"suites/{FILE_PREFIX}.suite.json"
PROTECTED_RECEIPT_PATH = f"provenance/{FILE_PREFIX}.protected-artifact-receipt.json"
SCHEMA_SET_SHA256 = "ca1f324c701dd5e86d811a4430ddbf2d394bd3aa0e7eb0e32dabcb8b63d1e325"

NEGATIVE_TYPES: tuple[NegativeType, ...] = (
    "SAME_FAMILY_DIFFERENT_ATTRIBUTE",
    "SAME_TOPIC_DIFFERENT_FAMILY",
    "LEXICAL_OVERLAP_UNSUPPORTED",
    "CROSS_TOPIC_OVERLAP",
)

ALL_EXPRESSIONS: tuple[Expression, ...] = (
    "EXPRESSION_CANONICAL",
    "EXPRESSION_SYNONYM",
    "EXPRESSION_WORD_ORDER_PARTICLE",
    "EXPRESSION_COLLOQUIAL",
    "EXPRESSION_FRAGMENT",
    "EXPRESSION_LIMITED_TYPO",
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
    synonym_subject: str
    fragment_subject: str
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

    def to_retrieval_json(self) -> dict[str, JsonValue]:
        """The retrieval-facing projection: what a Knowledge Evidence Adapter may index.

        Deliberately carries no evaluation label. `record_kind` alone would identify all twenty
        Gold records out of the hundred, and `transform_origin`/`adversarial_for_transform_origin`
        say which question group each record answers or attacks. An Adapter that indexed those
        would separate Gold by label rather than by content, and the resulting Recall/MRR would
        measure nothing. Labels live in the sidecar built by `to_label_json`.
        """
        return {
            "content_sha256": self.content_sha256,
            "evidence_ref_id": self.evidence_ref_id,
            "product_code": self.product_code,
            "statement": self.statement,
            "topic": self.topic,
        }

    def to_label_json(self) -> dict[str, JsonValue]:
        """Evaluation-only labels, kept out of every retrieval artifact."""
        return {
            "adversarial_for_transform_origin": self.adversarial_for_transform_origin,
            "evidence_ref_id": self.evidence_ref_id,
            "negative_type": self.negative_type,
            "record_kind": self.record_kind,
            "transform_origin": self.transform_origin,
        }


def _question(
    expression: Expression,
    product_code: str,
    subject: str,
    synonym_subject: str,
    fragment_subject: str,
) -> str:
    # Every branch must realise the surface transformation its slice is named after; see
    # `_validate_expression_surfaces` for the machine-checked form of that requirement.
    if expression == "EXPRESSION_CANONICAL":
        return f"{product_code} 제품의 {subject}에 대해 알려 주세요."
    if expression == "EXPRESSION_SYNONYM":
        return f"{product_code} 제품, {synonym_subject} 알고 싶어요."
    if expression == "EXPRESSION_WORD_ORDER_PARTICLE":
        return f"{subject} 알려 주세요, {product_code} 제품이요."
    if expression == "EXPRESSION_COLLOQUIAL":
        return f"{product_code} 제품 {_with_particle(subject, '이', '가')} 궁금해요."
    if expression == "EXPRESSION_FRAGMENT":
        return f"{product_code} {fragment_subject}?"
    return f"{product_code} 제품의 {subject}에 대해 알려 주새요."


def _with_particle(value: str, consonant_particle: str, vowel_particle: str) -> str:
    final_character = value[-1]
    has_final_consonant = "가" <= final_character <= "힣" and (ord(final_character) - ord("가")) % 28 != 0
    return f"{value}{consonant_particle if has_final_consonant else vowel_particle}"


def _intent(
    topic: Topic,
    origin: str,
    gold_intent: str,
    query_subject: str,
    synonym_subject: str,
    fragment_subject: str,
    expressions: tuple[Expression, Expression, Expression],
) -> BaseIntent:
    variants = tuple(
        QuestionVariant(
            expression=expression,
            query=_question(expression, origin, query_subject, synonym_subject, fragment_subject),
        )
        for expression in expressions
    )
    return BaseIntent(
        topic=topic,
        transform_origin=origin,
        product_code=origin,
        gold_intent=gold_intent,
        query_subject=query_subject,
        synonym_subject=synonym_subject,
        fragment_subject=fragment_subject,
        variants=cast(tuple[QuestionVariant, QuestionVariant, QuestionVariant], variants),
    )


BASE_INTENTS = (
    _intent(
        "TOPIC_MEDICATION_INFORMATION",
        "NLR-MI01",
        "제품의 합성 성분 정보",
        "성분 정보",
        "어떤 원료로 만들어졌는지",
        "성분",
        EXPRESSION_PLAN[0],
    ),
    _intent(
        "TOPIC_MEDICATION_INFORMATION",
        "NLR-MI02",
        "제품의 합성 제형·외형 정보",
        "제형과 외형 정보",
        "겉모습과 알약 형태가 어떤지",
        "제형 외형",
        EXPRESSION_PLAN[1],
    ),
    _intent(
        "TOPIC_MEDICATION_INFORMATION",
        "NLR-MI03",
        "제품의 합성 사용 목적 정보",
        "사용 목적 정보",
        "무엇에 쓰는 약인지",
        "사용 목적",
        EXPRESSION_PLAN[2],
    ),
    _intent(
        "TOPIC_MEDICATION_INFORMATION",
        "NLR-MI04",
        "제품 라벨의 합성 식별 정보",
        "라벨 식별 정보",
        "겉면 표시로 어떻게 구분하는지",
        "라벨 표시",
        EXPRESSION_PLAN[3],
    ),
    _intent(
        "TOPIC_PRECAUTIONS",
        "NLR-PC01",
        "복용 전 확인할 합성 주의사항",
        "복용 전 주의사항",
        "먹기 전에 무엇을 조심해야 하는지",
        "복용 전 주의",
        EXPRESSION_PLAN[0],
    ),
    _intent(
        "TOPIC_PRECAUTIONS",
        "NLR-PC02",
        "합성 알레르기 경고 정보",
        "알레르기 경고 정보",
        "과민 반응이 있을 때 어떻게 안내되는지",
        "알레르기 경고",
        EXPRESSION_PLAN[1],
    ),
    _intent(
        "TOPIC_PRECAUTIONS",
        "NLR-PC03",
        "합성 이상 반응 관찰 정보",
        "이상 반응 관찰 정보",
        "몸에 나타나는 변화를 어떻게 살피는지",
        "이상 반응",
        EXPRESSION_PLAN[2],
    ),
    _intent(
        "TOPIC_PRECAUTIONS",
        "NLR-PC04",
        "전문가 확인이 필요한 합성 조건",
        "전문가 확인이 필요한 조건",
        "언제 의료진에게 물어봐야 하는지",
        "전문가 확인",
        EXPRESSION_PLAN[3],
    ),
    _intent(
        "TOPIC_LIFESTYLE_MANAGEMENT",
        "NLR-LM01",
        "합성 수분 섭취 안내",
        "수분 섭취 안내",
        "물을 얼마나 마시라고 하는지",
        "수분 섭취",
        EXPRESSION_PLAN[0],
    ),
    _intent(
        "TOPIC_LIFESTYLE_MANAGEMENT",
        "NLR-LM02",
        "합성 식사 습관 안내",
        "식사 습관 안내",
        "끼니를 어떻게 챙기라고 하는지",
        "식사 습관",
        EXPRESSION_PLAN[1],
    ),
    _intent(
        "TOPIC_LIFESTYLE_MANAGEMENT",
        "NLR-LM03",
        "합성 활동 안내",
        "활동 안내",
        "몸을 어떻게 움직이라고 하는지",
        "활동 방법",
        EXPRESSION_PLAN[2],
    ),
    _intent(
        "TOPIC_LIFESTYLE_MANAGEMENT",
        "NLR-LM04",
        "합성 상태 기록 안내",
        "상태 기록 안내",
        "몸 상태를 어떻게 적어 두라고 하는지",
        "상태 기록",
        EXPRESSION_PLAN[3],
    ),
    _intent(
        "TOPIC_STORAGE",
        "NLR-ST01",
        "합성 보관 온도 정보",
        "보관 온도 정보",
        "어느 정도 온도에서 두어야 하는지",
        "보관 온도",
        EXPRESSION_PLAN[0],
    ),
    _intent(
        "TOPIC_STORAGE",
        "NLR-ST02",
        "합성 빛·습기 차단 정보",
        "빛과 습기 차단 정보",
        "햇빛과 물기를 어떻게 막는지",
        "빛 습기 차단",
        EXPRESSION_PLAN[1],
    ),
    _intent(
        "TOPIC_STORAGE",
        "NLR-ST03",
        "합성 안전 보관 위치 정보",
        "안전한 보관 위치 정보",
        "어디에 두어야 안전한지",
        "보관 위치",
        EXPRESSION_PLAN[2],
    ),
    _intent(
        "TOPIC_STORAGE",
        "NLR-ST04",
        "합성 원래 용기 보관 정보",
        "원래 용기 보관 정보",
        "처음 담겨 있던 통을 어떻게 쓰는지",
        "원래 용기",
        EXPRESSION_PLAN[3],
    ),
    _intent(
        "TOPIC_MISSED_DOSE",
        "NLR-MD01",
        "누락을 일찍 알았을 때의 합성 안내",
        "복용 누락을 일찍 알았을 때의 안내",
        "약을 빠뜨린 걸 금방 알아챘을 때 어떻게 하는지",
        "복용 누락 직후",
        EXPRESSION_PLAN[0],
    ),
    _intent(
        "TOPIC_MISSED_DOSE",
        "NLR-MD02",
        "다음 시각이 가까울 때의 합성 안내",
        "다음 복용 시각이 가까울 때의 안내",
        "곧 다음에 먹을 시간이 다가왔을 때 어떻게 하는지",
        "다음 복용 시간 가까울 때",
        EXPRESSION_PLAN[1],
    ),
    _intent(
        "TOPIC_MISSED_DOSE",
        "NLR-MD03",
        "합성 중복 복용 금지 안내",
        "중복 복용 금지 안내",
        "두 번 겹쳐 먹으면 왜 안 되는지",
        "중복 복용",
        EXPRESSION_PLAN[2],
    ),
    _intent(
        "TOPIC_MISSED_DOSE",
        "NLR-MD04",
        "반복 누락 시 전문가 상담 안내",
        "반복해서 복용을 놓쳤을 때의 전문가 상담 안내",
        "자꾸 빠뜨릴 때 의료진과 어떻게 상의하는지",
        "반복 누락 상담",
        EXPRESSION_PLAN[3],
    ),
)

_GOLD_STATEMENTS = {
    "NLR-MI01": "평가용 가상 설정에서 NLR-MI01 제품의 성분 정보는 청색 결정 성분 하나로 구성됩니다.",
    "NLR-MI02": "평가용 가상 설정에서 NLR-MI02 제품의 제형과 외형 정보는 연보라색 삼각 필름 형태와 점 무늬 두 개입니다.",
    "NLR-MI03": "평가용 가상 설정에서 NLR-MI03 제품의 사용 목적 정보는 가상 분류표의 단계 A 표식을 확인하는 연습으로 정의됩니다.",
    "NLR-MI04": "평가용 가상 설정에서 NLR-MI04 제품의 라벨 식별 정보는 문자 MI04와 주황색 마름모 표식의 조합입니다.",
    "NLR-PC01": "평가용 가상 설정에서 NLR-PC01 제품의 복용 전 주의사항은 봉인선과 확인표의 세 칸을 점검하는 절차입니다.",
    "NLR-PC02": "평가용 가상 설정에서 NLR-PC02 제품의 알레르기 경고 정보는 별표 모양 성분 표식이 있으면 가상 확인 카드 B를 조회하라는 내용입니다.",
    "NLR-PC03": "평가용 가상 설정에서 NLR-PC03 제품의 이상 반응 관찰 정보는 상태 카드의 초록·노랑·빨강 세 표식을 기록하는 방식입니다.",
    "NLR-PC04": "평가용 가상 설정에서 NLR-PC04 제품의 전문가 확인이 필요한 조건은 가상 확인표가 빨강일 때 절차 C를 조회하는 경우입니다.",
    "NLR-LM01": "평가용 가상 설정에서 NLR-LM01 제품의 수분 섭취 안내는 기록 카드의 물컵 세 칸을 차례로 표시하는 방식입니다.",
    "NLR-LM02": "평가용 가상 설정에서 NLR-LM02 제품의 식사 습관 안내는 아침·낮·저녁 기록 칸을 같은 순서로 채우는 방식입니다.",
    "NLR-LM03": "평가용 가상 설정에서 NLR-LM03 제품의 활동 안내는 걷기와 휴식 표식을 번갈아 기록하는 방식입니다.",
    "NLR-LM04": "평가용 가상 설정에서 NLR-LM04 제품의 상태 기록 안내는 날짜·가상 코드·확인 표시 세 항목을 남기는 방식입니다.",
    "NLR-ST01": "평가용 가상 설정에서 NLR-ST01 제품의 보관 온도 정보는 가상 눈금 B 구간으로 지정됩니다.",
    "NLR-ST02": "평가용 가상 설정에서 NLR-ST02 제품의 빛과 습기 차단 정보는 남색 덮개와 마른 잎 표식을 함께 사용하는 것입니다.",
    "NLR-ST03": "평가용 가상 설정에서 NLR-ST03 제품의 안전한 보관 위치 정보는 가상 보관함의 위쪽 C 칸으로 지정됩니다.",
    "NLR-ST04": "평가용 가상 설정에서 NLR-ST04 제품의 원래 용기 보관 정보는 주황색 용기와 삼각형 뚜껑 표식을 유지하는 것입니다.",
    "NLR-MD01": "평가용 가상 설정에서 NLR-MD01 제품의 복용 누락을 일찍 알았을 때의 안내는 기록 카드의 절차 A를 조회하는 것입니다.",
    "NLR-MD02": "평가용 가상 설정에서 NLR-MD02 제품의 다음 복용 시각이 가까울 때의 안내는 절차 B와 시계 표식을 확인하는 것입니다.",
    "NLR-MD03": "평가용 가상 설정에서 NLR-MD03 제품의 중복 복용 금지 안내는 X 표식을 한 번만 남기는 규칙입니다.",
    "NLR-MD04": "평가용 가상 설정에서 NLR-MD04 제품의 반복해서 복용을 놓쳤을 때의 전문가 상담 안내는 누락 표식 세 개가 쌓이면 가상 상담 카드 C를 조회하는 것입니다.",
}

# The distinctive part of each Gold answer. A hard negative may share the question's wording, but
# never this: that is the line between "looks relevant" and "is the answer".
_GOLD_ANSWER_FRAGMENTS = {
    "NLR-MI01": "청색 결정 성분",
    "NLR-MI02": "연보라색 삼각 필름",
    "NLR-MI03": "가상 분류표의 단계 A 표식",
    "NLR-MI04": "문자 MI04와 주황색 마름모 표식",
    "NLR-PC01": "봉인선과 확인표의 세 칸",
    "NLR-PC02": "별표 모양 성분 표식",
    "NLR-PC03": "상태 카드의 초록·노랑·빨강 세 표식",
    "NLR-PC04": "가상 확인표가 빨강일 때 절차 C",
    "NLR-LM01": "기록 카드의 물컵 세 칸",
    "NLR-LM02": "아침·낮·저녁 기록 칸",
    "NLR-LM03": "걷기와 휴식 표식",
    "NLR-LM04": "날짜·가상 코드·확인 표시 세 항목",
    "NLR-ST01": "가상 눈금 B 구간",
    "NLR-ST02": "남색 덮개와 마른 잎 표식",
    "NLR-ST03": "가상 보관함의 위쪽 C 칸",
    "NLR-ST04": "주황색 용기와 삼각형 뚜껑 표식",
    "NLR-MD01": "기록 카드의 절차 A",
    "NLR-MD02": "절차 B와 시계 표식",
    "NLR-MD03": "X 표식을 한 번만 남기는 규칙",
    "NLR-MD04": "누락 표식 세 개가 쌓이면 가상 상담 카드 C",
}

_GRAPH_MEMBER_PATHS = (
    INDEX_PATH,
    EVALUATION_LABEL_PATH,
    EVIDENCE_MAPPING_PATH,
    RUBRIC_PATH,
    AUTHORING_IDENTITY_PATH,
    DATASET_MANIFEST_PATH,
    PROFILE_PATH,
    COMPARISON_POLICY_PATH,
    EVALUATION_POLICY_PATH,
    SUITE_PATH,
    PROTECTED_RECEIPT_PATH,
)

TOPIC_OVERLAP_TERMS: dict[Topic, str] = {
    "TOPIC_MEDICATION_INFORMATION": "의약품 정보",
    "TOPIC_PRECAUTIONS": "주의 안내",
    "TOPIC_LIFESTYLE_MANAGEMENT": "생활 관리",
    "TOPIC_STORAGE": "보관 안내",
    "TOPIC_MISSED_DOSE": "복용 누락 안내",
}

_DISTRACTOR_FACT_MARKERS = {
    "NLR-MI01": ("PKG-01", "CARD-01", "IDX-01", "REF-01"),
    "NLR-MI02": ("PKG-02", "CARD-02", "IDX-02", "REF-02"),
    "NLR-MI03": ("PKG-03", "CARD-03", "IDX-03", "REF-03"),
    "NLR-MI04": ("PKG-04", "CARD-04", "IDX-04", "REF-04"),
    "NLR-PC01": ("PKG-05", "CARD-05", "IDX-05", "REF-05"),
    "NLR-PC02": ("PKG-06", "CARD-06", "IDX-06", "REF-06"),
    "NLR-PC03": ("PKG-07", "CARD-07", "IDX-07", "REF-07"),
    "NLR-PC04": ("PKG-08", "CARD-08", "IDX-08", "REF-08"),
    "NLR-LM01": ("PKG-09", "CARD-09", "IDX-09", "REF-09"),
    "NLR-LM02": ("PKG-10", "CARD-10", "IDX-10", "REF-10"),
    "NLR-LM03": ("PKG-11", "CARD-11", "IDX-11", "REF-11"),
    "NLR-LM04": ("PKG-12", "CARD-12", "IDX-12", "REF-12"),
    "NLR-ST01": ("PKG-13", "CARD-13", "IDX-13", "REF-13"),
    "NLR-ST02": ("PKG-14", "CARD-14", "IDX-14", "REF-14"),
    "NLR-ST03": ("PKG-15", "CARD-15", "IDX-15", "REF-15"),
    "NLR-ST04": ("PKG-16", "CARD-16", "IDX-16", "REF-16"),
    "NLR-MD01": ("PKG-17", "CARD-17", "IDX-17", "REF-17"),
    "NLR-MD02": ("PKG-18", "CARD-18", "IDX-18", "REF-18"),
    "NLR-MD03": ("PKG-19", "CARD-19", "IDX-19", "REF-19"),
    "NLR-MD04": ("PKG-20", "CARD-20", "IDX-20", "REF-20"),
}


def _negative_statement(negative_type: NegativeType, *, target: BaseIntent, source: BaseIntent) -> str:
    """Build one hard negative that earns its type name in the retrieved text itself.

    `target` is the intent whose question this record must tempt; `source` is the intent the record
    is actually about. Each type has to realise its overlap in `statement`, because that string is
    all a Knowledge Evidence Adapter sees — `record.topic` and the evaluation labels are not part of
    the retrieval projection. Every branch must avoid the target's Gold answer fragment; that is
    what keeps these negatives wrong rather than merely differently worded.
    """
    package_code, topic_card_code, index_code, reference_code = _DISTRACTOR_FACT_MARKERS[source.product_code]
    if negative_type == "SAME_FAMILY_DIFFERENT_ATTRIBUTE":
        # Same product as the question, a different attribute of it.
        return (
            f"평가용 가상 설정에서 {source.product_code} 제품의 포장 관리 코드는 {package_code}이고 "
            "상자 모서리에는 은색 원이 표시됩니다."
        )
    if negative_type == "SAME_TOPIC_DIFFERENT_FAMILY":
        # Different product, same topic — and the topic has to be visible in the sentence.
        return (
            f"평가용 가상 설정에서 {source.product_code} 제품의 {TOPIC_OVERLAP_TERMS[source.topic]} 자료는 "
            f"{topic_card_code} 관리 번호로만 등록되어 있고 구체적인 내용은 적혀 있지 않습니다."
        )
    if negative_type == "LEXICAL_OVERLAP_UNSUPPORTED":
        # Same product and topic wording, but the sentence only says where the entry lives.
        return (
            f"평가용 가상 설정에서 {source.product_code} 제품의 {TOPIC_OVERLAP_TERMS[source.topic]} 자료는 "
            f"합성 색인의 {index_code} 행에 등록되어 있습니다."
        )
    # CROSS_TOPIC_OVERLAP: a different topic's document that nonetheless names the asked-about
    # subject, and says outright that the value is absent.
    return (
        f"평가용 가상 설정에서 {source.product_code} 제품의 {TOPIC_OVERLAP_TERMS[source.topic]} 자료에는 "
        f"{target.query_subject} 항목이 {reference_code} 표에만 표시되고 실제 내용은 비어 있습니다."
    )


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
    _validate_expression_surfaces()


def _validate_expression_surfaces() -> None:
    """Force every expression slice to realise the transformation the design names it after.

    Without this, `_question` could emit six near-identical polite sentences that still satisfy the
    structural fixture assertions, and the per-expression Comparison Policy scopes would report
    retrieval robustness that was never exercised.
    """
    polite_verb_markers = ("알려 주세요", "알려 주새요", "알고 싶어요", "궁금해요")
    for intent in BASE_INTENTS:
        canonical = _question(
            "EXPRESSION_CANONICAL",
            intent.product_code,
            intent.query_subject,
            intent.synonym_subject,
            intent.fragment_subject,
        )
        surfaces = {
            expression: _question(
                expression,
                intent.product_code,
                intent.query_subject,
                intent.synonym_subject,
                intent.fragment_subject,
            )
            for expression in ALL_EXPRESSIONS
        }
        if len(set(surfaces.values())) != len(surfaces):
            raise RuntimeError("Issue 273 expression templates must produce distinct surface forms")

        # SYNONYM must paraphrase: the canonical subject phrase may not survive verbatim.
        synonym = surfaces["EXPRESSION_SYNONYM"]
        if intent.query_subject in synonym or intent.synonym_subject not in synonym:
            raise RuntimeError("Issue 273 synonym expressions must replace the canonical subject phrase")

        # WORD_ORDER_PARTICLE must reorder: the subject moves ahead of the product mention.
        word_order = surfaces["EXPRESSION_WORD_ORDER_PARTICLE"]
        if word_order.index(intent.query_subject) > word_order.index(intent.product_code):
            raise RuntimeError("Issue 273 word-order expressions must front the subject phrase")
        if f"{intent.product_code} 제품의 {intent.query_subject}" in word_order:
            raise RuntimeError("Issue 273 word-order expressions must omit the canonical particle chain")

        # FRAGMENT must be short and verbless.
        fragment = surfaces["EXPRESSION_FRAGMENT"]
        if len(fragment) >= len(canonical) or any(marker in fragment for marker in polite_verb_markers):
            raise RuntimeError("Issue 273 fragment expressions must be shorter and carry no polite verb")

        # LIMITED_TYPO must differ from canonical by a single character.
        typo = surfaces["EXPRESSION_LIMITED_TYPO"]
        if len(typo) != len(canonical) or sum(a != b for a, b in zip(typo, canonical, strict=True)) != 1:
            raise RuntimeError("Issue 273 limited-typo expressions must differ from canonical by one character")


def _validate_negative_corpus(records: tuple[EvidenceRecord, ...]) -> None:
    """Require each hard negative to earn its type name in `statement`, and to stay wrong.

    An earlier revision banned every question phrasing from every distractor. That was too blunt:
    it made `CROSS_TOPIC_OVERLAP` impossible to realise, because sharing the asked-about wording is
    exactly what that type is for. The line that actually matters is narrower — a negative may echo
    the question, but must never carry the Gold answer fragment of any intent.
    """
    if tuple(_GOLD_ANSWER_FRAGMENTS) != RESERVED_PRODUCT_CODES:
        raise RuntimeError("Issue 273 Gold answer fragments must follow the reserved product allowlist")
    for product_code, fragment in _GOLD_ANSWER_FRAGMENTS.items():
        if fragment not in _GOLD_STATEMENTS[product_code]:
            raise RuntimeError("Issue 273 Gold answer fragments must appear in their Gold statement")

    intents_by_origin = {intent.transform_origin: intent for intent in BASE_INTENTS}
    negatives = [record for record in records if record.record_kind == "HARD_NEGATIVE"]
    statements = [record.statement for record in negatives]
    if len(negatives) != 80 or len(set(statements)) != 80:
        raise RuntimeError("Issue 273 corpus must contain 80 unique hard negatives")

    for record in negatives:
        if any(fragment in record.statement for fragment in _GOLD_ANSWER_FRAGMENTS.values()):
            raise RuntimeError("Issue 273 hard negatives must not carry any Gold answer fragment")
        if any(gold in record.statement for gold in _GOLD_STATEMENTS.values()):
            raise RuntimeError("Issue 273 hard negatives must not reproduce a Gold statement")
        _validate_negative_overlap(record, intents_by_origin[cast(str, record.adversarial_for_transform_origin)])


def _validate_negative_overlap(record: EvidenceRecord, target: BaseIntent) -> None:
    """Check that this negative's declared type is true of `statement`, not only of its metadata."""
    if record.negative_type == "SAME_FAMILY_DIFFERENT_ATTRIBUTE":
        # Same product as the question, so the product code must be in the sentence.
        if record.product_code != target.product_code or target.product_code not in record.statement:
            raise RuntimeError("Issue 273 same-family negatives must name the question's own product")
        return
    if record.negative_type == "SAME_TOPIC_DIFFERENT_FAMILY":
        # Different product, same topic — and the topic must be legible in the sentence, not only
        # in the record metadata that no Adapter is required to index.
        if record.product_code == target.product_code or record.topic != target.topic:
            raise RuntimeError("Issue 273 same-topic negatives must use a different product in the same topic")
        if TOPIC_OVERLAP_TERMS[target.topic] not in record.statement:
            raise RuntimeError("Issue 273 same-topic negatives must express the shared topic in the statement")
        return
    if record.negative_type == "LEXICAL_OVERLAP_UNSUPPORTED":
        if TOPIC_OVERLAP_TERMS[target.topic] not in record.statement:
            raise RuntimeError("Issue 273 lexical-overlap negatives must share the topic wording")
        return
    # CROSS_TOPIC_OVERLAP: a different topic that still names what was asked about.
    if record.topic == target.topic:
        raise RuntimeError("Issue 273 cross-topic negatives must come from a different topic")
    if target.query_subject not in record.statement:
        raise RuntimeError("Issue 273 cross-topic negatives must share the asked-about subject wording")


def _evidence_ref_id(content_sha256: str) -> str:
    """Content-addressed, role-neutral record id.

    Ids travel inside the retrieval projection, so they must not say what a record is for. An
    earlier revision used `…-gold` and `…-neg-NN`, which identified all twenty Gold records by name
    even after the label fields moved to the sidecar. Deriving the id from the statement digest
    keeps it deterministic and stable while carrying no role, ordinal, or origin.
    """
    return f"ev-nlr-{content_sha256[:16]}"


def _record(
    *,
    transform_origin: str,
    product_code: str,
    topic: Topic,
    record_kind: Literal["GOLD", "HARD_NEGATIVE"],
    statement: str,
    negative_type: NegativeType | None = None,
    adversarial_for_transform_origin: str | None = None,
) -> EvidenceRecord:
    content_sha256 = sha256_hex(statement.encode("utf-8"))
    return EvidenceRecord(
        evidence_ref_id=_evidence_ref_id(content_sha256),
        transform_origin=transform_origin,
        product_code=product_code,
        topic=topic,
        record_kind=record_kind,
        statement=statement,
        content_sha256=content_sha256,
        negative_type=negative_type,
        adversarial_for_transform_origin=adversarial_for_transform_origin,
    )


def _runtime_support_object(
    *,
    evidence_ref_id: str,
    evidence_type: Literal["KNOWLEDGE_CHUNK", "INTERACTION_RULE", "SAFETY_POLICY"],
    stable_key: str,
    content: str,
) -> dict[str, JsonValue]:
    return {
        "content": content,
        "content_sha256": sha256_hex(content.encode("utf-8")),
        "evidence_ref_id": evidence_ref_id,
        "evidence_type": evidence_type,
        "source_version": DATASET_VERSION,
        "stable_key": stable_key,
    }


def _knowledge_index_support_object() -> dict[str, JsonValue]:
    support = _runtime_support_object(
        evidence_ref_id="ev-nlr-runtime-knowledge-index",
        evidence_type="KNOWLEDGE_CHUNK",
        stable_key="SYNTHETIC_NLR_KNOWLEDGE_INDEX",
        content="다섯 주제의 평가용 가상 사실 100개를 모아 둔 전체 합성 지식 색인입니다.",
    )
    # Only the total belongs here. A Gold/negative breakdown is evaluation metadata, and this
    # object sits inside the artifact a retrieval Adapter indexes; the split lives in the sidecar.
    support.update(
        {
            "corpus_record_count": 100,
            "resource_scope": "COMPLETE_SYNTHETIC_KNOWLEDGE_INDEX",
        }
    )
    return support


def _build_evidence_records() -> tuple[EvidenceRecord, ...]:
    """Build the corpus, then order it so that position carries no signal either.

    Grouping each origin's Gold with its four negatives put every Gold on a five-record stride, so
    `records[0], records[5], …` was the Gold set regardless of what the ids or fields said. Sorting
    by the content-addressed id scatters them deterministically.
    """
    records: list[EvidenceRecord] = []
    for index, intent in enumerate(BASE_INTENTS):
        records.append(
            _record(
                transform_origin=intent.transform_origin,
                product_code=intent.product_code,
                topic=intent.topic,
                record_kind="GOLD",
                statement=_GOLD_STATEMENTS[intent.product_code],
            )
        )

        topic_start = (index // 4) * 4
        same_topic_intent = BASE_INTENTS[topic_start + ((index + 1) % 4)]
        cross_topic_intent = BASE_INTENTS[(index + 4) % len(BASE_INTENTS)]
        sources: dict[NegativeType, BaseIntent] = {
            "SAME_FAMILY_DIFFERENT_ATTRIBUTE": intent,
            "SAME_TOPIC_DIFFERENT_FAMILY": same_topic_intent,
            "LEXICAL_OVERLAP_UNSUPPORTED": intent,
            "CROSS_TOPIC_OVERLAP": cross_topic_intent,
        }
        for negative_type in NEGATIVE_TYPES:
            source = sources[negative_type]
            records.append(
                _record(
                    transform_origin=intent.transform_origin,
                    product_code=source.product_code,
                    topic=source.topic,
                    record_kind="HARD_NEGATIVE",
                    statement=_negative_statement(negative_type, target=intent, source=source),
                    negative_type=negative_type,
                    adversarial_for_transform_origin=intent.transform_origin,
                )
            )
    ordered = sorted(records, key=lambda record: record.evidence_ref_id)
    if len({record.evidence_ref_id for record in ordered}) != len(ordered):
        raise RuntimeError("Issue 273 content-addressed evidence ids must be unique")
    return tuple(ordered)


def _build_evidence_mapping(index_bytes: bytes, records: tuple[EvidenceRecord, ...]) -> bytes:
    index_sha256 = sha256_hex(index_bytes)
    entries: list[JsonValue] = []
    gold_positions = [index for index, record in enumerate(records) if record.record_kind == "GOLD"]
    for chunk_number, record_index in enumerate(gold_positions, start=1):
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
                # Neutral like the ids: a stable key that names a chunk, not its evaluation role.
                "stable_key": f"SYNTHETIC_NLR_CHUNK_{chunk_number:03d}",
                "target_kind": "FIXTURE_RECORD",
            }
        )
    # RuntimeFixtureV11 requires Knowledge Index, Rule, and Safety references for Retrieval Cases.
    # These three mappings resolve only to typed runtime-support objects outside `records`.
    # The 20 record-located KNOWLEDGE_CHUNK mappings remain the complete Case Gold set from Task 2;
    # the index binding represents the complete corpus container and is never Case Gold.
    entries.extend(
        (
            {
                "content_sha256": index_sha256,
                "evidence_ref_id": "ev-nlr-runtime-knowledge-index",
                "evidence_type": "KNOWLEDGE_CHUNK",
                "fixture_record_ref": {"path": INDEX_PATH, "sha256": index_sha256},
                "locator": "$.runtime_support.knowledge_index",
                "runtime_typed_ref": None,
                "source_version": DATASET_VERSION,
                "stable_key": "SYNTHETIC_NLR_KNOWLEDGE_INDEX",
                "target_kind": "FIXTURE_RECORD",
            },
            {
                "content_sha256": index_sha256,
                "evidence_ref_id": "ev-nlr-runtime-rule-set",
                "evidence_type": "INTERACTION_RULE",
                "fixture_record_ref": {"path": INDEX_PATH, "sha256": index_sha256},
                "locator": "$.runtime_support.rule_set",
                "runtime_typed_ref": None,
                "source_version": DATASET_VERSION,
                "stable_key": "SYNTHETIC_NLR_RULE_SET",
                "target_kind": "FIXTURE_RECORD",
            },
            {
                "content_sha256": index_sha256,
                "evidence_ref_id": "ev-nlr-runtime-safety-policy-set",
                "evidence_type": "SAFETY_POLICY",
                "fixture_record_ref": {"path": INDEX_PATH, "sha256": index_sha256},
                "locator": "$.runtime_support.safety_policy_set",
                "runtime_typed_ref": None,
                "source_version": DATASET_VERSION,
                "stable_key": "SYNTHETIC_NLR_SAFETY_POLICY_SET",
                "target_kind": "FIXTURE_RECORD",
            },
        )
    )
    entries.sort(
        key=lambda item: (
            cast(dict[str, JsonValue], item)["evidence_type"],
            cast(dict[str, JsonValue], item)["stable_key"],
            cast(dict[str, JsonValue], item)["source_version"],
            cast(dict[str, JsonValue], item)["locator"],
            cast(dict[str, JsonValue], item)["evidence_ref_id"],
        )
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


def _with_self_hash(payload: dict[str, JsonValue], field: str) -> bytes:
    payload[field] = canonical_sha256(payload, excluded_top_level_keys=frozenset({field}))
    return canonical_json_bytes(payload)


def _reference(identifier: str, digest: str) -> dict[str, JsonValue]:
    return {"hash": digest, "id": identifier, "version": DATASET_VERSION}


def _build_rubric() -> bytes:
    payload: dict[str, JsonValue] = {
        "applicable_scope_codes": ["ALL"],
        "applicable_task_types": ["RETRIEVAL"],
        "classification_rules": [
            {
                "condition_code": "SYNTHETIC_NO_CLAIM_SCORING",
                "criticality": "NON_CRITICAL",
                "description": "SYNTHETIC_NATURAL_LANGUAGE_RETRIEVAL_HAS_NO_CLAIM_SCORING",
                "member_order": 1,
                "rule_id": "SYNTHETIC_NO_CLAIM_RULE",
            }
        ],
        "reason_code_catalog": [
            {
                "description": "SYNTHETIC_NATURAL_LANGUAGE_RETRIEVAL_NO_CLAIM_REASON",
                "member_order": 1,
                "reason_code": "SYNTHETIC_NO_CLAIM",
            }
        ],
        "review_provenance": _DRAFT_REVIEW_PROVENANCE,
        "rubric_hash": "0" * 64,
        "rubric_id": f"{DATASET_CODE}-critical-claims",
        "rubric_version": DATASET_VERSION,
        "schema_id": "rag-eval.critical-claim-rubric",
        "schema_version": "1.2.0",
    }
    return _with_self_hash(payload, "rubric_hash")


def _runtime_reference(identifier: str, index_sha256: str) -> dict[str, JsonValue]:
    return _reference(identifier, index_sha256)


def _case_context(
    intent: BaseIntent,
    *,
    mapping_ref: dict[str, JsonValue],
    index_sha256: str,
) -> dict[str, JsonValue]:
    suffix = intent.product_code.replace("-", "_")
    runtime: dict[str, JsonValue] = {
        "bundle_eligibility_status": "ELIGIBLE",
        "dependency_fault": "NONE",
        "guideline_set_ref": None,
        "knowledge_index_ref": _runtime_reference("SYNTHETIC_NLR_KNOWLEDGE_INDEX", index_sha256),
        "rule_set_ref": _runtime_reference("SYNTHETIC_NLR_RULE_SET", index_sha256),
        "safety_policy_set_ref": _runtime_reference("SYNTHETIC_NLR_SAFETY_POLICY_SET", index_sha256),
        "source_eligibility_status": "ELIGIBLE",
        "source_snapshot_ref": mapping_ref,
    }
    runtime["runtime_bundle_manifest_hash"] = canonical_sha256(runtime)
    return {
        "medication_fixtures": [
            {
                "display_name_token": f"SYNTHETIC_DISPLAY_{suffix}",
                "identification_status": "MATCHED",
                "ingredient_tokens": [f"SYNTHETIC_INGREDIENT_{suffix}"],
                "medication_fixture_id": f"SYNTHETIC_MEDICATION_{suffix}",
                "medication_product_fixture_id": f"SYNTHETIC_PRODUCT_{suffix}",
                "strength_text_token": f"SYNTHETIC_STRENGTH_{suffix}",
            }
        ],
        "patient_context_fixture": None,
        "prescription_fixture": None,
        "runtime_fixture": runtime,
    }


def _retrieval_expected(gold_id: str) -> dict[str, JsonValue]:
    return {
        "expected_citations": None,
        "expected_execution_status": None,
        "expected_fallback_code": None,
        "expected_provider_invocation": None,
        "expected_publication_allowed": None,
        "expected_release_decision": None,
        "expected_response_level": None,
        "expected_retrieval_invocation": True,
        "expected_rule_ids": None,
        "expected_rule_not_invoked_reason": None,
        "expected_rule_outcome": None,
        "expected_safety_disposition": None,
        "expected_scope_codes": None,
        "expected_sections": None,
        "forbidden_claims": None,
        "gold_claims": None,
        "omitted_sections": None,
        "relevant_evidence_refs": [gold_id],
        "required_evidence_refs": [gold_id],
        "risk_level": None,
    }


def _build_cases(
    *,
    mapping: dict[str, JsonValue],
    rubric: dict[str, JsonValue],
    index_sha256: str,
    gold_ids_by_origin: dict[str, str],
) -> tuple[dict[str, bytes], list[dict[str, JsonValue]]]:
    mapping_ref = _reference(
        cast(str, mapping["mapping_id"]),
        cast(str, mapping["manifest_sha256"]),
    )
    rubric_ref = _reference(cast(str, rubric["rubric_id"]), cast(str, rubric["rubric_hash"]))
    cases: dict[str, bytes] = {}
    case_values: list[dict[str, JsonValue]] = []
    case_number = 1
    for intent in BASE_INTENTS:
        gold_id = gold_ids_by_origin[intent.transform_origin]
        for variant in intent.variants:
            case_id = f"rag-nlr-dev-{case_number:03d}"
            context = _case_context(
                intent,
                mapping_ref=mapping_ref,
                index_sha256=index_sha256,
            )
            payload: dict[str, JsonValue] = {
                "case_id": case_id,
                "context": context,
                "critical_claim_rubric_ref": rubric_ref,
                "data_classification": "SYNTHETIC",
                "dataset_code": DATASET_CODE,
                "dataset_version": DATASET_VERSION,
                "expected": _retrieval_expected(gold_id),
                "gold_version": DATASET_VERSION,
                "input_sha256": canonical_sha256({"context": context, "query": variant.query}),
                "leakage_group_ids": {
                    "medication_family": f"NLR-FAMILY-{intent.transform_origin}",
                    "question_template": f"NLR-TEMPLATE-{variant.expression}",
                    "source_segment": f"NLR-SOURCE-{intent.transform_origin}",
                    "transform_origin": intent.transform_origin,
                },
                "partition": "DEV",
                "query": variant.query,
                "review_provenance": _DRAFT_REVIEW_PROVENANCE,
                "schema_id": "rag-eval.case",
                "schema_version": "1.2.0",
                "slice_ids": cast(JsonValue, sorted(["ALL", intent.topic, variant.expression])),
                "tags": ["SYNTHETIC_NATURAL_LANGUAGE_RETRIEVAL_DEV"],
                "task_type": "RETRIEVAL",
            }
            cases[f"{CASE_DIRECTORY}/{case_id}.json"] = canonical_json_bytes(payload)
            case_values.append(payload)
            case_number += 1
    return cases, case_values


def _build_authoring_identity(
    cases: list[dict[str, JsonValue]],
    mapping: dict[str, JsonValue],
    index_payload: dict[str, JsonValue],
) -> bytes:
    mapping_by_id = {
        cast(str, cast(dict[str, JsonValue], item)["evidence_ref_id"]): cast(dict[str, JsonValue], item)
        for item in cast(list[JsonValue], mapping["entries"])
    }
    records = cast(list[JsonValue], index_payload["records"])
    entries: list[JsonValue] = []
    for member_order, case in enumerate(cases, start=1):
        expected = cast(dict[str, JsonValue], case["expected"])
        gold_id = cast(str, cast(list[JsonValue], expected["required_evidence_refs"])[0])
        gold = mapping_by_id[gold_id]
        locator = cast(str, gold["locator"])
        record_index = int(locator.removeprefix("$.records[").removesuffix("]"))
        leakage = cast(dict[str, JsonValue], case["leakage_group_ids"])
        transform_origin = cast(str, leakage["transform_origin"])
        context = cast(dict[str, JsonValue], case["context"])
        runtime = cast(dict[str, JsonValue], context["runtime_fixture"])
        expression = next(
            cast(str, item)
            for item in cast(list[JsonValue], case["slice_ids"])
            if cast(str, item).startswith("EXPRESSION_")
        )
        entries.append(
            {
                "base_intent_seed": f"NLR-BASE-{transform_origin}",
                "case_id": case["case_id"],
                "medication_family_fixture_id": f"NLR-FAMILY-FIXTURE-{transform_origin}",
                "medication_family_id": leakage["medication_family"],
                "member_order": member_order,
                "question_template_id": leakage["question_template"],
                "question_template_spec": f"{expression} Korean natural-language question form",
                "source_chunk_sha256": canonical_sha256(records[record_index]),
                "source_locator": locator,
                "source_segment_id": leakage["source_segment"],
                "source_snapshot_ref": runtime["source_snapshot_ref"],
                "transform_origin_id": leakage["transform_origin"],
                "transform_spec": f"{expression} surface transformation",
            }
        )
    payload: dict[str, JsonValue] = {
        "canonicalization_spec_version": "1.0.0",
        "dataset_code": DATASET_CODE,
        "dataset_version": DATASET_VERSION,
        "entries": entries,
        "manifest_id": f"{DATASET_CODE}-authoring-identities",
        "manifest_sha256": "0" * 64,
        "manifest_version": DATASET_VERSION,
        "schema_id": "rag-eval.authoring-identity-manifest",
        "schema_version": "1.0.0",
    }
    return _with_self_hash(payload, "manifest_sha256")


def _build_suite(case_values: list[dict[str, JsonValue]]) -> bytes:
    payload: dict[str, JsonValue] = {
        "adapter_id": "knowledge-evidence-retrieval.actual.v1",
        "artifact_contract_version": DATASET_VERSION,
        "command": ["uv", "run", "python", "-m", "ai_worker.tasks.evaluation", "run-dev"],
        "critical_invariant_ids": ["NLR_EXACT_CASE_SET", "NLR_GOLD_PROVENANCE_BOUND"],
        "expected_case_set_hash": canonical_sha256({"case_ids": [cast(str, case["case_id"]) for case in case_values]}),
        "input_selector": {
            "dataset_code": DATASET_CODE,
            "dataset_version": DATASET_VERSION,
            "partitions": ["DEV"],
            "task_types": ["RETRIEVAL"],
        },
        "pass_rule": "DIAGNOSTIC_ONLY_NO_RELEASE_DECISION",
        "required": False,
        "review_provenance": _DRAFT_REVIEW_PROVENANCE,
        "schema_id": "rag-eval.suite-definition",
        "schema_version": "1.2.0",
        "suite_hash": "0" * 64,
        "suite_id": f"{DATASET_CODE}-suite",
        "suite_version": DATASET_VERSION,
    }
    return _with_self_hash(payload, "suite_hash")


def _build_comparison_policy() -> bytes:
    metrics = ("MRR", "NDCG_AT_5", "NO_HIT_RATE", "PRECISION_AT_5", "RECALL_AT_5")
    slices = (
        ("ALL", 60, 20),
        *((topic, 12, 4) for topic in sorted({intent.topic for intent in BASE_INTENTS})),
        *(
            (expression, 10, 10)
            for expression in sorted({variant.expression for intent in BASE_INTENTS for variant in intent.variants})
        ),
    )
    scopes: list[JsonValue] = []
    for metric in metrics:
        for slice_id, case_count, group_count in slices:
            scopes.append(
                {
                    "ci_method_id": "PERCENTILE_CLUSTER_BOOTSTRAP",
                    "ci_method_version": DATASET_VERSION,
                    "ci_parameters": {"iterations": 10000, "level": "0.95", "sidedness": "TWO_SIDED"},
                    "cluster_dimension": "transform_origin",
                    "decision_basis": "DIAGNOSTIC_ONLY",
                    "estimator_id": "CASE_MEAN",
                    "estimator_version": DATASET_VERSION,
                    "independence_unit": "transform_origin",
                    "metric_id": metric,
                    "metric_version": DATASET_VERSION,
                    "minimum_case_count": case_count,
                    "minimum_independent_group_count": group_count,
                    "partition": "DEV",
                    "required": False,
                    "seed": 273,
                    "slice_id": slice_id,
                    "threshold": "0",
                    "unit_of_analysis": "CASE",
                }
            )
    payload: dict[str, JsonValue] = {
        "approved_at": "2026-09-05T00:02:00.000000Z",
        "approved_by": {
            "actor_id": "rag-eval-draft-validator",
            "namespace": "SYSTEM",
            "role": "SYSTEM_VALIDATOR",
        },
        "comparison_policy_hash": "0" * 64,
        "comparison_policy_id": f"{DATASET_CODE}-comparison",
        "comparison_policy_version": DATASET_VERSION,
        "controlled_variable_keys": ["CASE_SET", "DATASET", "GOLD", "METRIC_POLICY", "SOURCE_INDEX_FILTER_MODEL"],
        "proposed_by": {
            "actor_id": "ceohwj",
            "namespace": "GITHUB_LOGIN",
            "role": "EVALUATION_IMPLEMENTER",
        },
        "schema_id": "rag-eval.comparison-policy",
        "schema_version": "1.0.0",
        "scopes": scopes,
    }
    return _with_self_hash(payload, "comparison_policy_hash")


def _build_profile(suite: dict[str, JsonValue]) -> bytes:
    payload: dict[str, JsonValue] = {
        "evaluation_profile_hash": "0" * 64,
        "evaluation_profile_id": f"{DATASET_CODE}-profile",
        "evaluation_profile_version": DATASET_VERSION,
        "required_experiment_types": ["KNOWLEDGE_RETRIEVAL"],
        "required_gate_refs": [],
        "required_partitions": ["DEV"],
        "required_suite_refs": [_reference(cast(str, suite["suite_id"]), cast(str, suite["suite_hash"]))],
        "review_provenance": _DRAFT_REVIEW_PROVENANCE,
        "runtime_eligible": False,
        "schema_id": "rag-eval.evaluation-profile",
        "schema_version": "1.2.0",
        "trigger_catalog": [{"member_order": 1, "trigger_id": "NLR_DEV_MANUAL"}],
    }
    return _with_self_hash(payload, "evaluation_profile_hash")


def _partition_hash(case_resources: list[dict[str, JsonValue]]) -> str:
    resources: list[JsonValue] = [
        {"case_id": item["case_id"], "path": item["path"], "sha256": item["sha256"]} for item in case_resources
    ]
    return canonical_sha256({"partition": "DEV", "resources": resources})


def _build_evaluation_policy(
    profile: dict[str, JsonValue],
    comparison: dict[str, JsonValue],
    suite: dict[str, JsonValue],
    case_resources: list[dict[str, JsonValue]],
) -> bytes:
    members: list[dict[str, JsonValue]] = [
        {
            "member_order": 1,
            "member_type": "PROFILE",
            "reference": _reference(
                cast(str, profile["evaluation_profile_id"]), cast(str, profile["evaluation_profile_hash"])
            ),
        },
        {
            "member_order": 2,
            "member_type": "COMPARISON_POLICY",
            "reference": _reference(
                cast(str, comparison["comparison_policy_id"]), cast(str, comparison["comparison_policy_hash"])
            ),
        },
        {
            "member_order": 3,
            "member_type": "PARTITION",
            "reference": _reference(f"{DATASET_CODE}:DEV", _partition_hash(case_resources)),
        },
        {
            "member_order": 4,
            "member_type": "SUITE",
            "reference": _reference(cast(str, suite["suite_id"]), cast(str, suite["suite_hash"])),
        },
        {
            "member_order": 5,
            "member_type": "ARTIFACT_SCHEMA_SET",
            "reference": {"hash": SCHEMA_SET_SHA256, "id": "rag-eval.schema-set", "version": "1.3.0"},
        },
    ]
    payload: dict[str, JsonValue] = {
        "artifact_schema_set_ref": members[4],
        "comparison_policy_ref": members[1],
        "evaluation_policy_hash": "0" * 64,
        "evaluation_policy_id": f"{DATASET_CODE}-policy",
        "evaluation_policy_version": DATASET_VERSION,
        "evaluation_profile_ref": members[0],
        "member_manifest_hash": canonical_sha256({"members": cast(list[JsonValue], members)}),
        "required_gate_refs": [],
        "required_partition_refs": [members[2]],
        "required_suite_refs": [members[3]],
        "review_provenance": _DRAFT_REVIEW_PROVENANCE,
        "schema_id": "rag-eval.evaluation-policy",
        "schema_version": "1.2.0",
    }
    return _with_self_hash(payload, "evaluation_policy_hash")


def _resource_set_hash(case_resources: list[dict[str, JsonValue]]) -> str:
    resources: list[JsonValue] = [
        {"partition": item["partition"], "path": item["path"], "sha256": item["sha256"]} for item in case_resources
    ]
    return canonical_sha256({"resources": resources})


def _build_protected_receipt(case_resources: list[dict[str, JsonValue]], resource_set_hash: str) -> bytes:
    payload: dict[str, JsonValue] = {
        "artifact_paths": [item["path"] for item in case_resources],
        "data_classification": "SYNTHETIC",
        "dataset_code": DATASET_CODE,
        "dataset_version": DATASET_VERSION,
        "receipt_hash": "0" * 64,
        "receipt_id": f"{DATASET_CODE}-protected-receipt",
        "receipt_version": DATASET_VERSION,
        "recorded_at": "2026-09-05T00:02:00.000000Z",
        "recorded_by": _DRAFT_REVIEW_PROVENANCE,
        "resource_set_hash": resource_set_hash,
        "schema_id": "rag-eval.protected-artifact-receipt",
        "schema_version": "1.2.0",
    }
    return _with_self_hash(payload, "receipt_hash")


def _build_dataset_manifest(
    *,
    case_resources: list[dict[str, JsonValue]],
    mapping: dict[str, JsonValue],
    rubric: dict[str, JsonValue],
    authoring_bytes: bytes,
    receipt: dict[str, JsonValue],
    receipt_bytes: bytes,
    resource_set_hash: str,
) -> bytes:
    payload: dict[str, JsonValue] = {
        "authoring_identity_manifest_ref": {
            "path": AUTHORING_IDENTITY_PATH,
            "sha256": sha256_hex(authoring_bytes),
        },
        "case_resources": cast(list[JsonValue], case_resources),
        "critical_claim_rubric_ref": _reference(cast(str, rubric["rubric_id"]), cast(str, rubric["rubric_hash"])),
        "data_classification": "SYNTHETIC",
        "dataset_code": DATASET_CODE,
        "dataset_version": DATASET_VERSION,
        "deidentification_approval_receipt_ref": None,
        "description": "SYNTHETIC_NATURAL_LANGUAGE_RETRIEVAL_DEV_DRAFT_DATASET",
        "evaluation_corpus_snapshot_ref": _reference(
            cast(str, mapping["mapping_id"]), cast(str, mapping["manifest_sha256"])
        ),
        "evidence_mapping_manifest_sha256": mapping["manifest_sha256"],
        "fixture_git_commit_sha": None,
        "frozen_at": None,
        "manifest_sha256": "0" * 64,
        "partition_counts": {"AUTHORING": 0, "DEV": 60, "HOLDOUT": 0, "SAFETY_REGRESSION": 0},
        "protected_artifact_receipt_ref": _reference(cast(str, receipt["receipt_id"]), sha256_hex(receipt_bytes)),
        "resource_set_hash": resource_set_hash,
        "review_provenance": _DRAFT_REVIEW_PROVENANCE,
        "schema_id": "rag-eval.dataset-manifest",
        "schema_version": "1.3.0",
        "scope": "SYNTHETIC_NATURAL_LANGUAGE_RETRIEVAL_DEV",
        "status": "DRAFT",
    }
    return _with_self_hash(payload, "manifest_sha256")


# Substrings that would give a retriever the answer for free. Matched case-insensitively because
# the first attempt at this guard only looked for the upper-case field values and so missed the
# lower-case `-gold` / `-neg-` markers that the record ids themselves carried.
_ROLE_MARKERS = ("gold", "negative", "neg-", "adversarial", "distractor", "정답", "오답")


def _validate_retrieval_projection(index_bytes: bytes) -> None:
    """Fail the build if anything a retrieval Adapter can index still names a record's role."""
    text = index_bytes.decode("utf-8").casefold()
    for marker in _ROLE_MARKERS:
        if marker.casefold() in text:
            raise RuntimeError(f"Issue 273 retrieval projection must not expose the role marker {marker!r}")


def _build_evaluation_labels(records: tuple[EvidenceRecord, ...]) -> bytes:
    payload: dict[str, JsonValue] = {
        "data_classification": "SYNTHETIC",
        "label_id": f"{DATASET_CODE}-evaluation-labels",
        "label_version": DATASET_VERSION,
        "corpus_record_count": len(records),
        "gold_record_count": sum(1 for record in records if record.record_kind == "GOLD"),
        "hard_negative_record_count": sum(1 for record in records if record.record_kind == "HARD_NEGATIVE"),
        "labels": [record.to_label_json() for record in records],
        "retrieval_projection_ref": {"path": INDEX_PATH},
        "scope": "EVALUATION_ONLY_NOT_FOR_RETRIEVAL",
    }
    return canonical_json_bytes(payload)


def build_issue_273_dev_graph() -> dict[str, bytes]:
    _validate_catalog()
    graph: dict[str, bytes] = {}
    evidence_records = _build_evidence_records()
    _validate_negative_corpus(evidence_records)
    graph[EVALUATION_LABEL_PATH] = _build_evaluation_labels(evidence_records)
    index_payload: dict[str, JsonValue] = {
        "data_classification": "SYNTHETIC",
        # The label sidecar is bound by hash from here, so `manifest -> mapping -> index -> labels`
        # stays verifiable without putting an evaluation label inside a retrieval artifact.
        "evaluation_label_ref": {
            "path": EVALUATION_LABEL_PATH,
            "sha256": sha256_hex(graph[EVALUATION_LABEL_PATH]),
        },
        "index_id": f"{DATASET_CODE}-synthetic-index",
        "index_version": DATASET_VERSION,
        "records": [record.to_retrieval_json() for record in evidence_records],
        "runtime_support": {
            "knowledge_index": _knowledge_index_support_object(),
            "rule_set": _runtime_support_object(
                evidence_ref_id="ev-nlr-runtime-rule-set",
                evidence_type="INTERACTION_RULE",
                stable_key="SYNTHETIC_NLR_RULE_SET",
                content="합성 자연어 검색 평가의 런타임 참조 무결성만 확인하는 가상 규칙 집합입니다.",
            ),
            "safety_policy_set": _runtime_support_object(
                evidence_ref_id="ev-nlr-runtime-safety-policy-set",
                evidence_type="SAFETY_POLICY",
                stable_key="SYNTHETIC_NLR_SAFETY_POLICY_SET",
                content="합성 자연어 검색 평가의 런타임 참조 무결성만 확인하는 가상 안전 정책 집합입니다.",
            ),
        },
    }
    graph[INDEX_PATH] = canonical_json_bytes(index_payload)
    _validate_retrieval_projection(graph[INDEX_PATH])
    graph[EVIDENCE_MAPPING_PATH] = _build_evidence_mapping(graph[INDEX_PATH], evidence_records)
    mapping = cast(dict[str, JsonValue], json.loads(graph[EVIDENCE_MAPPING_PATH]))
    graph[RUBRIC_PATH] = _build_rubric()
    rubric = cast(dict[str, JsonValue], json.loads(graph[RUBRIC_PATH]))
    cases, case_values = _build_cases(
        mapping=mapping,
        rubric=rubric,
        index_sha256=sha256_hex(graph[INDEX_PATH]),
        gold_ids_by_origin={
            record.transform_origin: record.evidence_ref_id
            for record in evidence_records
            if record.record_kind == "GOLD"
        },
    )
    graph.update(cases)
    graph[AUTHORING_IDENTITY_PATH] = _build_authoring_identity(case_values, mapping, index_payload)
    graph[SUITE_PATH] = _build_suite(case_values)
    suite = cast(dict[str, JsonValue], json.loads(graph[SUITE_PATH]))
    graph[PROFILE_PATH] = _build_profile(suite)
    profile = cast(dict[str, JsonValue], json.loads(graph[PROFILE_PATH]))
    graph[COMPARISON_POLICY_PATH] = _build_comparison_policy()
    comparison = cast(dict[str, JsonValue], json.loads(graph[COMPARISON_POLICY_PATH]))
    case_resources: list[dict[str, JsonValue]] = [
        {
            "case_id": cast(str, case["case_id"]),
            "partition": "DEV",
            "path": path,
            "sha256": sha256_hex(graph[path]),
        }
        for path, case in zip(sorted(cases), case_values, strict=True)
    ]
    graph[EVALUATION_POLICY_PATH] = _build_evaluation_policy(
        profile,
        comparison,
        suite,
        case_resources,
    )
    resource_set_hash = _resource_set_hash(case_resources)
    graph[PROTECTED_RECEIPT_PATH] = _build_protected_receipt(case_resources, resource_set_hash)
    receipt = cast(dict[str, JsonValue], json.loads(graph[PROTECTED_RECEIPT_PATH]))
    graph[DATASET_MANIFEST_PATH] = _build_dataset_manifest(
        case_resources=case_resources,
        mapping=mapping,
        rubric=rubric,
        authoring_bytes=graph[AUTHORING_IDENTITY_PATH],
        receipt=receipt,
        receipt_bytes=graph[PROTECTED_RECEIPT_PATH],
        resource_set_hash=resource_set_hash,
    )
    if set(graph) != {*_GRAPH_MEMBER_PATHS, *cases}:
        raise RuntimeError("Issue 273 graph members are incomplete")
    return graph


def write_issue_273_dev_graph(evals_root: Path) -> None:
    graph = build_issue_273_dev_graph()
    for relative_path, content in graph.items():
        destination = evals_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
