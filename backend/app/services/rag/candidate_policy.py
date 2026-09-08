import math
import unicodedata
from dataclasses import dataclass
from enum import StrEnum


class CandidateStage(StrEnum):
    PRODUCT_NAME_EXACT = "PRODUCT_NAME_EXACT"
    APPROVED_ALIAS_EXACT = "APPROVED_ALIAS_EXACT"
    INGREDIENT_EXACT = "INGREDIENT_EXACT"
    TRIGRAM_EDIT_DISTANCE = "TRIGRAM_EDIT_DISTANCE"
    DENSE_VECTOR = "DENSE_VECTOR"


PRODUCT_STAGES = frozenset(
    {
        CandidateStage.PRODUCT_NAME_EXACT,
        CandidateStage.APPROVED_ALIAS_EXACT,
        CandidateStage.TRIGRAM_EDIT_DISTANCE,
        CandidateStage.DENSE_VECTOR,
    }
)
AUTO_SELECTABLE_STAGES = PRODUCT_STAGES - {CandidateStage.DENSE_VECTOR}


@dataclass(frozen=True, slots=True)
class ResolverPolicy:
    policy_version: str
    maximum_input_length: int
    retrieval_limit: int
    enable_dense: bool
    release_eligible: bool
    stage_weights: tuple[tuple[CandidateStage, float], ...]
    rrf_k: float
    minimum_relevance: float
    minimum_margin: float
    auto_select_stages: frozenset[CandidateStage]


def policy_is_valid(policy: object) -> bool:
    if not isinstance(policy, ResolverPolicy):
        return False
    return _scalar_fields_are_valid(policy) and _stage_fields_are_valid(policy)


def _scalar_fields_are_valid(policy: ResolverPolicy) -> bool:
    return (
        _is_canonical_nonblank_text(policy.policy_version)
        and _is_positive_int(policy.maximum_input_length)
        and _is_positive_int(policy.retrieval_limit)
        and type(policy.enable_dense) is bool
        and type(policy.release_eligible) is bool
        and not policy.release_eligible
        and _is_finite_positive_number(policy.rrf_k)
        and _is_unit_interval(policy.minimum_relevance)
        and _is_unit_interval(policy.minimum_margin)
    )


def _stage_fields_are_valid(policy: ResolverPolicy) -> bool:
    if not isinstance(policy.stage_weights, tuple):
        return False
    stages: list[CandidateStage] = []
    for item in policy.stage_weights:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], CandidateStage)
            or not _is_finite_positive_number(item[1])
        ):
            return False
        stages.append(item[0])
    if len(stages) != len(PRODUCT_STAGES) or set(stages) != PRODUCT_STAGES:
        return False

    return (
        isinstance(policy.auto_select_stages, frozenset)
        and bool(policy.auto_select_stages)
        and all(isinstance(stage, CandidateStage) for stage in policy.auto_select_stages)
        and policy.auto_select_stages <= AUTO_SELECTABLE_STAGES
    )


def _is_canonical_nonblank_text(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip() and unicodedata.is_normalized("NFC", value)


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _is_finite_positive_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _is_unit_interval(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1
