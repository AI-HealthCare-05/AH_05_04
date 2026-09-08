import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.services.rag.candidate_policy import CandidateStage, ResolverPolicy, policy_is_valid

_PRODUCT_STAGE_ORDER = (
    CandidateStage.PRODUCT_NAME_EXACT,
    CandidateStage.APPROVED_ALIAS_EXACT,
    CandidateStage.TRIGRAM_EDIT_DISTANCE,
    CandidateStage.DENSE_VECTOR,
)
_ALLOWED_ATTRIBUTE_RESULTS = frozenset(
    {
        "MATCH",
        "NOT_APPLICABLE",
    }
)
_WHITESPACE_PATTERN = re.compile(r"\s+")


class ResolverOutcome(StrEnum):
    SINGLE_CANDIDATE = "SINGLE_CANDIDATE"
    AMBIGUOUS = "AMBIGUOUS"
    NO_CANDIDATE = "NO_CANDIDATE"
    INGREDIENT_ONLY = "INGREDIENT_ONLY"
    INVALID_INPUT = "INVALID_INPUT"


class ResolverFailureReason(StrEnum):
    POLICY_INVALID = "POLICY_INVALID"
    POLICY_VERSION_MISMATCH = "POLICY_VERSION_MISMATCH"
    INDEX_VERSION_MISMATCH = "INDEX_VERSION_MISMATCH"
    PORT_FAILURE = "PORT_FAILURE"
    EVIDENCE_INVALID = "EVIDENCE_INVALID"


class CandidateIndexMode(StrEnum):
    LEXICAL_ONLY = "LEXICAL_ONLY"
    HYBRID = "HYBRID"


class OfficialEntityType(StrEnum):
    PRODUCT = "PRODUCT"
    INGREDIENT = "INGREDIENT"


class ProductStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class AttributeCompatibility(StrEnum):
    MATCH = "MATCH"
    CONFLICT = "CONFLICT"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class CandidateIndexPortError(Exception):
    """Expected Candidate Index dependency failure without safe public detail."""


class CandidateAttributeMatcherError(Exception):
    """Expected attribute-matcher dependency failure without safe public detail."""


class CandidateRelevanceEvaluatorError(Exception):
    """Expected relevance-evaluator dependency failure without safe public detail."""


@dataclass(frozen=True, slots=True)
class ResolverInput:
    medication_name: str
    strength_text: str | None
    index_version: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class CandidateSearchRequest:
    medication_name: str
    strength_text: str | None
    index_version: str
    retrieval_limit: int


@dataclass(frozen=True, slots=True)
class CandidateIndexDescriptor:
    index_version: str
    mode: CandidateIndexMode


@dataclass(frozen=True, slots=True)
class OfficialIdentity:
    entity_type: OfficialEntityType
    code_system: str
    canonical_code: str


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    identity: OfficialIdentity
    product_name: str
    strength_text: str | None
    dosage_form: str | None
    manufacturer_name: str | None
    status: ProductStatus


@dataclass(frozen=True, slots=True)
class CandidateHit:
    identity: OfficialIdentity
    product: ProductSnapshot
    stage: CandidateStage
    rank: int
    stage_score: float
    index_version: str


@dataclass(frozen=True, slots=True)
class IngredientHit:
    identity: OfficialIdentity
    rank: int
    stage_score: float
    index_version: str


@dataclass(frozen=True, slots=True)
class CandidateSignal:
    stage: CandidateStage
    rank: int
    stage_score: float


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    identity: OfficialIdentity
    product: ProductSnapshot
    signals: tuple[CandidateSignal, ...]
    fusion_score: float


@dataclass(frozen=True, slots=True)
class CandidateAttributeAssessment:
    strength: AttributeCompatibility
    dosage_form: AttributeCompatibility
    manufacturer: AttributeCompatibility


@dataclass(frozen=True, slots=True)
class EvaluatedCandidate:
    evidence: CandidateEvidence
    attributes: CandidateAttributeAssessment
    relevance: float
    eligible: bool


@dataclass(frozen=True, slots=True)
class ResolverCandidate:
    identity: OfficialIdentity
    product: ProductSnapshot


@dataclass(frozen=True, slots=True)
class ResolverVisibleCandidate:
    product_name: str
    strength_text: str | None
    dosage_form: str | None
    manufacturer_name: str | None
    product_status: ProductStatus


@dataclass(frozen=True, slots=True)
class ResolverVisibleResult:
    outcome: ResolverOutcome
    candidate: ResolverVisibleCandidate | None


@dataclass(frozen=True, slots=True)
class ResolverResult:
    outcome: ResolverOutcome
    candidate: ResolverCandidate | None
    internal_candidates: tuple[EvaluatedCandidate, ...]
    raw_count: int
    deduped_count: int
    eligible_count: int
    ingredient_hit_count: int

    def redacted(self) -> ResolverVisibleResult:
        visible_candidate = None
        if self.candidate is not None:
            product = self.candidate.product
            visible_candidate = ResolverVisibleCandidate(
                product_name=product.product_name,
                strength_text=product.strength_text,
                dosage_form=product.dosage_form,
                manufacturer_name=product.manufacturer_name,
                product_status=product.status,
            )
        return ResolverVisibleResult(outcome=self.outcome, candidate=visible_candidate)


@dataclass(frozen=True, slots=True)
class ResolverFailure:
    reason: ResolverFailureReason
    stage: CandidateStage | None = None


class CandidateIndexPort(Protocol):
    def describe(self, index_version: str) -> CandidateIndexDescriptor: ...

    def search_product_name_exact(self, request: CandidateSearchRequest) -> tuple[CandidateHit, ...]: ...

    def search_approved_alias_exact(self, request: CandidateSearchRequest) -> tuple[CandidateHit, ...]: ...

    def search_ingredient_exact(self, request: CandidateSearchRequest) -> tuple[IngredientHit, ...]: ...

    def search_trigram_edit_distance(self, request: CandidateSearchRequest) -> tuple[CandidateHit, ...]: ...

    def search_dense_vector(self, request: CandidateSearchRequest) -> tuple[CandidateHit, ...]: ...


class CandidateAttributeMatcher(Protocol):
    def assess(
        self,
        resolver_input: ResolverInput,
        product: ProductSnapshot,
    ) -> CandidateAttributeAssessment: ...


class CandidateRelevanceEvaluator(Protocol):
    def evaluate(
        self,
        resolver_input: ResolverInput,
        candidate: CandidateEvidence,
    ) -> float: ...


class MedicationResolver:
    def __init__(
        self,
        *,
        index_port: CandidateIndexPort,
        attribute_matcher: CandidateAttributeMatcher,
        relevance_evaluator: CandidateRelevanceEvaluator,
    ) -> None:
        self._index_port = index_port
        self._attribute_matcher = attribute_matcher
        self._relevance_evaluator = relevance_evaluator

    def resolve(
        self,
        resolver_input: ResolverInput,
        policy: ResolverPolicy,
    ) -> ResolverResult | ResolverFailure:
        if not policy_is_valid(policy):
            return ResolverFailure(ResolverFailureReason.POLICY_INVALID)
        if not isinstance(resolver_input, ResolverInput):
            return self._empty_result(ResolverOutcome.INVALID_INPUT)
        if (
            not _canonical_text_is_valid(resolver_input.policy_version)
            or resolver_input.policy_version != policy.policy_version
        ):
            return ResolverFailure(ResolverFailureReason.POLICY_VERSION_MISMATCH)
        if not _canonical_text_is_valid(resolver_input.index_version):
            return ResolverFailure(ResolverFailureReason.INDEX_VERSION_MISMATCH)
        if not _input_is_valid(resolver_input, policy.maximum_input_length):
            return self._empty_result(ResolverOutcome.INVALID_INPUT)

        descriptor = self._describe_index(resolver_input.index_version)
        if isinstance(descriptor, ResolverFailure):
            return descriptor

        request = CandidateSearchRequest(
            medication_name=resolver_input.medication_name,
            strength_text=resolver_input.strength_text,
            index_version=resolver_input.index_version,
            retrieval_limit=policy.retrieval_limit,
        )
        searched = self._search(request, descriptor, policy)
        if isinstance(searched, ResolverFailure):
            return searched
        raw_hits, ingredient_hits = searched

        deduped = _dedupe_candidates(raw_hits, policy)
        if isinstance(deduped, ResolverFailure):
            return deduped

        evaluated = self._evaluate_candidates(resolver_input, deduped, policy)
        if isinstance(evaluated, ResolverFailure):
            return evaluated
        return _classify(
            raw_count=len(raw_hits),
            ingredient_hit_count=len(ingredient_hits),
            candidates=evaluated,
            minimum_margin=policy.minimum_margin,
        )

    def _describe_index(self, index_version: str) -> CandidateIndexDescriptor | ResolverFailure:
        try:
            descriptor = self._index_port.describe(index_version)
        except CandidateIndexPortError:
            return ResolverFailure(ResolverFailureReason.PORT_FAILURE)
        if not isinstance(descriptor, CandidateIndexDescriptor) or not isinstance(descriptor.mode, CandidateIndexMode):
            return ResolverFailure(ResolverFailureReason.EVIDENCE_INVALID)
        if descriptor.index_version != index_version:
            return ResolverFailure(ResolverFailureReason.INDEX_VERSION_MISMATCH)
        return descriptor

    def _search(
        self,
        request: CandidateSearchRequest,
        descriptor: CandidateIndexDescriptor,
        policy: ResolverPolicy,
    ) -> tuple[tuple[CandidateHit, ...], tuple[IngredientHit, ...]] | ResolverFailure:
        raw_hits: list[CandidateHit] = []
        stages = (
            (CandidateStage.PRODUCT_NAME_EXACT, self._index_port.search_product_name_exact),
            (CandidateStage.APPROVED_ALIAS_EXACT, self._index_port.search_approved_alias_exact),
        )
        for stage, search in stages:
            result = self._call_product_stage(request, stage, search)
            if isinstance(result, ResolverFailure):
                return result
            raw_hits.extend(result)

        ingredient_hits = self._call_ingredient_stage(request)
        if isinstance(ingredient_hits, ResolverFailure):
            return ingredient_hits

        trigram_hits = self._call_product_stage(
            request,
            CandidateStage.TRIGRAM_EDIT_DISTANCE,
            self._index_port.search_trigram_edit_distance,
        )
        if isinstance(trigram_hits, ResolverFailure):
            return trigram_hits
        raw_hits.extend(trigram_hits)

        if descriptor.mode is CandidateIndexMode.HYBRID and policy.enable_dense:
            dense_hits = self._call_product_stage(
                request,
                CandidateStage.DENSE_VECTOR,
                self._index_port.search_dense_vector,
            )
            if isinstance(dense_hits, ResolverFailure):
                return dense_hits
            raw_hits.extend(dense_hits)

        return tuple(raw_hits), ingredient_hits

    def _call_product_stage(
        self,
        request: CandidateSearchRequest,
        stage: CandidateStage,
        search,
    ) -> tuple[CandidateHit, ...] | ResolverFailure:
        try:
            hits = search(request)
        except CandidateIndexPortError:
            return ResolverFailure(ResolverFailureReason.PORT_FAILURE, stage)
        if not isinstance(hits, tuple) or len(hits) > request.retrieval_limit:
            return ResolverFailure(ResolverFailureReason.EVIDENCE_INVALID, stage)
        for expected_rank, hit in enumerate(hits, start=1):
            if not _product_hit_is_valid(hit, stage, expected_rank, request.index_version):
                return ResolverFailure(ResolverFailureReason.EVIDENCE_INVALID, stage)
        return hits

    def _call_ingredient_stage(
        self,
        request: CandidateSearchRequest,
    ) -> tuple[IngredientHit, ...] | ResolverFailure:
        stage = CandidateStage.INGREDIENT_EXACT
        try:
            hits = self._index_port.search_ingredient_exact(request)
        except CandidateIndexPortError:
            return ResolverFailure(ResolverFailureReason.PORT_FAILURE, stage)
        if not isinstance(hits, tuple) or len(hits) > request.retrieval_limit:
            return ResolverFailure(ResolverFailureReason.EVIDENCE_INVALID, stage)
        for expected_rank, hit in enumerate(hits, start=1):
            if not _ingredient_hit_is_valid(hit, expected_rank, request.index_version):
                return ResolverFailure(ResolverFailureReason.EVIDENCE_INVALID, stage)
        return hits

    def _evaluate_candidates(
        self,
        resolver_input: ResolverInput,
        candidates: tuple[CandidateEvidence, ...],
        policy: ResolverPolicy,
    ) -> tuple[EvaluatedCandidate, ...] | ResolverFailure:
        evaluated: list[EvaluatedCandidate] = []
        for candidate in candidates:
            try:
                attributes = self._attribute_matcher.assess(resolver_input, candidate.product)
            except CandidateAttributeMatcherError:
                return ResolverFailure(ResolverFailureReason.PORT_FAILURE)
            try:
                relevance = self._relevance_evaluator.evaluate(resolver_input, candidate)
            except CandidateRelevanceEvaluatorError:
                return ResolverFailure(ResolverFailureReason.PORT_FAILURE)
            if not _assessment_is_valid(attributes) or not _unit_interval_is_valid(relevance):
                return ResolverFailure(ResolverFailureReason.EVIDENCE_INVALID)
            eligible = (
                candidate.product.status is ProductStatus.ACTIVE
                and _attributes_are_eligible(resolver_input, attributes)
                and relevance >= policy.minimum_relevance
                and any(signal.stage in policy.auto_select_stages for signal in candidate.signals)
            )
            evaluated.append(
                EvaluatedCandidate(
                    evidence=candidate,
                    attributes=attributes,
                    relevance=float(relevance),
                    eligible=eligible,
                )
            )
        return tuple(evaluated)

    @staticmethod
    def _empty_result(outcome: ResolverOutcome) -> ResolverResult:
        return ResolverResult(
            outcome=outcome,
            candidate=None,
            internal_candidates=(),
            raw_count=0,
            deduped_count=0,
            eligible_count=0,
            ingredient_hit_count=0,
        )


def _input_is_valid(resolver_input: ResolverInput, maximum_length: int) -> bool:
    return _input_text_is_valid(resolver_input.medication_name, maximum_length) and (
        resolver_input.strength_text is None or _input_text_is_valid(resolver_input.strength_text, maximum_length)
    )


def _input_text_is_valid(value: object, maximum_length: int) -> bool:
    return (
        isinstance(value, str)
        and _canonical_text_is_valid(value)
        and len(value) <= maximum_length
        and _WHITESPACE_PATTERN.sub(" ", value.strip()) == value
    )


def _canonical_text_is_valid(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip() and unicodedata.is_normalized("NFC", value)


def _optional_canonical_text_is_valid(value: object) -> bool:
    return value is None or _display_text_is_valid(value)


def _display_text_is_valid(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _identity_is_valid(identity: object, entity_type: OfficialEntityType, code_system: str) -> bool:
    return (
        isinstance(identity, OfficialIdentity)
        and identity.entity_type is entity_type
        and identity.code_system == code_system
        and _canonical_text_is_valid(identity.canonical_code)
    )


def _product_snapshot_is_valid(product: object) -> bool:
    return (
        isinstance(product, ProductSnapshot)
        and _identity_is_valid(product.identity, OfficialEntityType.PRODUCT, "MFDS_ITEM_SEQ")
        and _display_text_is_valid(product.product_name)
        and _optional_canonical_text_is_valid(product.strength_text)
        and _optional_canonical_text_is_valid(product.dosage_form)
        and _optional_canonical_text_is_valid(product.manufacturer_name)
        and isinstance(product.status, ProductStatus)
    )


def _finite_number_is_valid(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _unit_interval_is_valid(value: object) -> bool:
    return isinstance(value, (int, float)) and _finite_number_is_valid(value) and 0 <= value <= 1


def _product_hit_is_valid(
    hit: object,
    expected_stage: CandidateStage,
    expected_rank: int,
    index_version: str,
) -> bool:
    return (
        isinstance(hit, CandidateHit)
        and _identity_is_valid(hit.identity, OfficialEntityType.PRODUCT, "MFDS_ITEM_SEQ")
        and _product_snapshot_is_valid(hit.product)
        and hit.product.identity == hit.identity
        and hit.stage is expected_stage
        and type(hit.rank) is int
        and hit.rank == expected_rank
        and _finite_number_is_valid(hit.stage_score)
        and hit.index_version == index_version
    )


def _ingredient_hit_is_valid(hit: object, expected_rank: int, index_version: str) -> bool:
    return (
        isinstance(hit, IngredientHit)
        and _identity_is_valid(hit.identity, OfficialEntityType.INGREDIENT, "MFDS_INGREDIENT_CODE")
        and type(hit.rank) is int
        and hit.rank == expected_rank
        and _finite_number_is_valid(hit.stage_score)
        and hit.index_version == index_version
    )


def _dedupe_candidates(
    hits: tuple[CandidateHit, ...],
    policy: ResolverPolicy,
) -> tuple[CandidateEvidence, ...] | ResolverFailure:
    snapshots: dict[tuple[str, str], ProductSnapshot] = {}
    signals: dict[tuple[str, str], dict[CandidateStage, CandidateSignal]] = {}
    identities: dict[tuple[str, str], OfficialIdentity] = {}
    for hit in hits:
        key = (hit.identity.code_system, hit.identity.canonical_code)
        previous_snapshot = snapshots.get(key)
        if previous_snapshot is not None and previous_snapshot != hit.product:
            return ResolverFailure(ResolverFailureReason.EVIDENCE_INVALID)
        snapshots[key] = hit.product
        identities[key] = hit.identity
        signal = CandidateSignal(stage=hit.stage, rank=hit.rank, stage_score=float(hit.stage_score))
        previous_signal = signals.setdefault(key, {}).get(hit.stage)
        if previous_signal is None or (signal.rank, -signal.stage_score) < (
            previous_signal.rank,
            -previous_signal.stage_score,
        ):
            signals[key][hit.stage] = signal

    weights = dict(policy.stage_weights)
    candidates: list[CandidateEvidence] = []
    for key, identity in identities.items():
        candidate_signals = tuple(
            sorted(signals[key].values(), key=lambda signal: _PRODUCT_STAGE_ORDER.index(signal.stage))
        )
        fusion_score = sum(weights[signal.stage] / (policy.rrf_k + signal.rank) for signal in candidate_signals)
        candidates.append(
            CandidateEvidence(
                identity=identity,
                product=snapshots[key],
                signals=candidate_signals,
                fusion_score=fusion_score,
            )
        )
    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                -candidate.fusion_score,
                candidate.identity.code_system,
                candidate.identity.canonical_code,
            ),
        )
    )


def _assessment_is_valid(value: object) -> bool:
    return (
        isinstance(value, CandidateAttributeAssessment)
        and isinstance(value.strength, AttributeCompatibility)
        and isinstance(value.dosage_form, AttributeCompatibility)
        and isinstance(value.manufacturer, AttributeCompatibility)
    )


def _attributes_are_eligible(
    resolver_input: ResolverInput,
    assessment: CandidateAttributeAssessment,
) -> bool:
    expected_strength = (
        AttributeCompatibility.MATCH
        if resolver_input.strength_text is not None
        else AttributeCompatibility.NOT_APPLICABLE
    )
    return (
        assessment.strength is expected_strength
        and assessment.dosage_form.value in _ALLOWED_ATTRIBUTE_RESULTS
        and assessment.manufacturer.value in _ALLOWED_ATTRIBUTE_RESULTS
    )


def _classify(
    *,
    raw_count: int,
    ingredient_hit_count: int,
    candidates: tuple[EvaluatedCandidate, ...],
    minimum_margin: float,
) -> ResolverResult:
    eligible = tuple(candidate for candidate in candidates if candidate.eligible)
    outcome: ResolverOutcome
    selected: ResolverCandidate | None = None
    if not candidates:
        outcome = ResolverOutcome.INGREDIENT_ONLY if ingredient_hit_count else ResolverOutcome.NO_CANDIDATE
    elif len(eligible) > 1:
        outcome = ResolverOutcome.AMBIGUOUS
    elif len(eligible) == 1:
        top = eligible[0]
        other_scores = [
            candidate.evidence.fusion_score
            for candidate in candidates
            if candidate.evidence.identity != top.evidence.identity
        ]
        margin_is_safe = not other_scores or top.evidence.fusion_score - max(other_scores) >= minimum_margin
        if margin_is_safe:
            outcome = ResolverOutcome.SINGLE_CANDIDATE
            selected = ResolverCandidate(identity=top.evidence.identity, product=top.evidence.product)
        else:
            outcome = ResolverOutcome.AMBIGUOUS
    else:
        outcome = ResolverOutcome.NO_CANDIDATE
    return ResolverResult(
        outcome=outcome,
        candidate=selected,
        internal_candidates=candidates,
        raw_count=raw_count,
        deduped_count=len(candidates),
        eligible_count=len(eligible),
        ingredient_hit_count=ingredient_hit_count,
    )
