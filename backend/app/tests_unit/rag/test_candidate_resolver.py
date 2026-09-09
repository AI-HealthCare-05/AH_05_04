import ast
import dataclasses
from collections.abc import Mapping
from pathlib import Path

import pytest

from app.services.rag.candidate_policy import CandidateStage, ResolverPolicy
from app.services.rag.candidate_resolver import (
    AttributeCompatibility,
    CandidateAttributeAssessment,
    CandidateAttributeMatcherError,
    CandidateHit,
    CandidateIndexMode,
    CandidateIndexPortError,
    CandidateProvenanceReceipt,
    CandidateRelevanceEvaluatorError,
    CandidateSearchRequest,
    CandidateSourceRef,
    HydratedCandidateEvidence,
    IngredientHit,
    MedicationResolver,
    OfficialEntityType,
    OfficialIdentity,
    ProductSnapshot,
    ProductStatus,
    ResolverFailure,
    ResolverFailureReason,
    ResolverInput,
    ResolverOutcome,
    ResolverResult,
    prepare_candidate_search,
)

_MEMBER_KEY = "a" * 64
_CATALOG_VERSION = "catalog-v1"
_CATALOG_MANIFEST_HASH = "b" * 64
_SOURCE_SNAPSHOT_ID = "source-snapshot-v1"
_SOURCE_VERSION = "source-version-v1"
_NORMALIZATION_VERSION = "normalization-v1"
_EMBEDDING_MODEL_VERSION = "embedding-v1"


def synthetic_policy(**changes: object) -> ResolverPolicy:
    values: dict[str, object] = {
        "policy_version": "resolver-local-synthetic-v1",
        "maximum_input_length": 100,
        "retrieval_limit": 10,
        "enable_dense": True,
        "release_eligible": False,
        "stage_weights": (
            (CandidateStage.PRODUCT_NAME_EXACT, 4.0),
            (CandidateStage.APPROVED_ALIAS_EXACT, 3.0),
            (CandidateStage.TRIGRAM_EDIT_DISTANCE, 2.0),
            (CandidateStage.DENSE_VECTOR, 1.0),
        ),
        "rrf_k": 10.0,
        "minimum_relevance": 0.75,
        "minimum_margin": 0.02,
        "auto_select_stages": frozenset(
            {
                CandidateStage.PRODUCT_NAME_EXACT,
                CandidateStage.APPROVED_ALIAS_EXACT,
                CandidateStage.TRIGRAM_EDIT_DISTANCE,
            }
        ),
    }
    values.update(changes)
    return ResolverPolicy(**values)  # type: ignore[arg-type]


def resolver_input(**changes: object) -> ResolverInput:
    values: dict[str, object] = {
        "medication_name": "합성제품정",
        "strength_text": "10mg",
        "index_version": "candidate-index-v1",
        "policy_version": "resolver-local-synthetic-v1",
    }
    values.update(changes)
    return ResolverInput(**values)  # type: ignore[arg-type]


def product_identity(code: str) -> OfficialIdentity:
    return OfficialIdentity(
        entity_type=OfficialEntityType.PRODUCT,
        code_system="MFDS_ITEM_SEQ",
        canonical_code=code,
    )


def product_snapshot(
    code: str,
    *,
    strength_text: str | None = "10mg",
    status: ProductStatus = ProductStatus.ACTIVE,
) -> ProductSnapshot:
    return ProductSnapshot(
        identity=product_identity(code),
        product_name=f"합성제품-{code}",
        strength_text=strength_text,
        dosage_form="정제",
        manufacturer_name="합성제약",
        status=status,
    )


def hit(
    code: str,
    stage: CandidateStage,
    *,
    rank: int = 1,
    score: float = 0.9,
    snapshot: ProductSnapshot | None = None,
    index_version: str = "candidate-index-v1",
) -> CandidateHit:
    return CandidateHit(
        identity=product_identity(code),
        product=snapshot or product_snapshot(code),
        stage=stage,
        rank=rank,
        stage_score=score,
        index_version=index_version,
        member_key=_MEMBER_KEY,
        catalog_version=_CATALOG_VERSION,
        source_snapshot_id=_SOURCE_SNAPSHOT_ID,
        normalization_version=_NORMALIZATION_VERSION,
        embedding_model_version=(_EMBEDDING_MODEL_VERSION if stage is CandidateStage.DENSE_VECTOR else None),
    )


def ingredient_hit(*, rank: int = 1) -> IngredientHit:
    return IngredientHit(
        identity=OfficialIdentity(
            entity_type=OfficialEntityType.INGREDIENT,
            code_system="MFDS_INGREDIENT_CODE",
            canonical_code="SYNTH-I-001",
        ),
        rank=rank,
        stage_score=1.0,
        index_version="candidate-index-v1",
        catalog_version=_CATALOG_VERSION,
        source_snapshot_id=_SOURCE_SNAPSHOT_ID,
        normalization_version=_NORMALIZATION_VERSION,
    )


class FakeIndexPort:
    def __init__(
        self,
        hits: Mapping[CandidateStage, tuple[CandidateHit, ...]] | None = None,
        *,
        ingredient_hits: tuple[IngredientHit, ...] = (),
        mode: CandidateIndexMode = CandidateIndexMode.HYBRID,
        fail_stage: CandidateStage | None = None,
        provenance: object | None = None,
    ) -> None:
        self.hits = dict(hits or {})
        self.ingredient_hits = ingredient_hits
        self.mode = mode
        self.fail_stage = fail_stage
        self.provenance = provenance
        self.calls: list[str] = []

    def hydrate(self, request: CandidateSearchRequest) -> HydratedCandidateEvidence:
        self.calls.append("HYDRATE")
        if self.fail_stage is not None:
            raise CandidateIndexPortError(stage=self.fail_stage)
        provenance = self.provenance or CandidateProvenanceReceipt(
            index_version=request.index_version,
            catalog_version=_CATALOG_VERSION,
            catalog_manifest_hash=_CATALOG_MANIFEST_HASH,
            source_refs=(
                CandidateSourceRef(
                    snapshot_id=_SOURCE_SNAPSHOT_ID,
                    source_version=_SOURCE_VERSION,
                ),
            ),
            normalization_version=_NORMALIZATION_VERSION,
            embedding_model_version=(_EMBEDDING_MODEL_VERSION if self.mode is CandidateIndexMode.HYBRID else None),
            index_mode=self.mode,
        )
        product_hits = tuple(
            hit
            for stage in (
                CandidateStage.PRODUCT_NAME_EXACT,
                CandidateStage.APPROVED_ALIAS_EXACT,
                CandidateStage.TRIGRAM_EDIT_DISTANCE,
                CandidateStage.DENSE_VECTOR,
            )
            for hit in self.hits.get(stage, ())
        )
        return HydratedCandidateEvidence(
            provenance=provenance,  # type: ignore[arg-type]
            product_hits=product_hits,
            ingredient_hits=self.ingredient_hits,
        )


class FakeAttributeMatcher:
    def __init__(
        self,
        assessments: Mapping[str, object] | None = None,
        *,
        raises: bool = False,
    ) -> None:
        self.assessments = dict(assessments or {})
        self.raises = raises
        self.calls: list[str] = []

    def assess(self, resolver_input: ResolverInput, product: ProductSnapshot) -> CandidateAttributeAssessment:
        code = product.identity.canonical_code
        self.calls.append(code)
        if self.raises:
            raise CandidateAttributeMatcherError("sensitive matcher detail")
        default_strength = (
            AttributeCompatibility.MATCH
            if resolver_input.strength_text is not None
            else AttributeCompatibility.NOT_APPLICABLE
        )
        return self.assessments.get(
            code,
            CandidateAttributeAssessment(
                strength=default_strength,
                dosage_form=AttributeCompatibility.NOT_APPLICABLE,
                manufacturer=AttributeCompatibility.NOT_APPLICABLE,
            ),
        )  # type: ignore[return-value]


class FakeRelevanceEvaluator:
    def __init__(
        self,
        scores: Mapping[str, object] | None = None,
        *,
        raises: bool = False,
    ) -> None:
        self.scores = dict(scores or {})
        self.raises = raises
        self.calls: list[str] = []

    def evaluate(self, resolver_input: ResolverInput, candidate: object) -> float:
        del resolver_input
        code = candidate.identity.canonical_code  # type: ignore[attr-defined]
        self.calls.append(code)
        if self.raises:
            raise CandidateRelevanceEvaluatorError("sensitive evaluator detail")
        return self.scores.get(code, 0.9)  # type: ignore[return-value]


def resolve(
    port: FakeIndexPort,
    *,
    input_value: ResolverInput | None = None,
    policy: ResolverPolicy | None = None,
    matcher: FakeAttributeMatcher | None = None,
    evaluator: FakeRelevanceEvaluator | None = None,
) -> ResolverResult | ResolverFailure:
    return MedicationResolver(
        index_port=port,
        attribute_matcher=matcher or FakeAttributeMatcher(),
        relevance_evaluator=evaluator or FakeRelevanceEvaluator(),
    ).resolve(input_value or resolver_input(), policy or synthetic_policy())


@pytest.mark.parametrize(
    ("changes", "expected_reason"),
    [
        ({"policy_version": " "}, ResolverFailureReason.POLICY_INVALID),
        ({"maximum_input_length": True}, ResolverFailureReason.POLICY_INVALID),
        ({"retrieval_limit": 0}, ResolverFailureReason.POLICY_INVALID),
        ({"rrf_k": float("nan")}, ResolverFailureReason.POLICY_INVALID),
        ({"rrf_k": 10**1000}, ResolverFailureReason.POLICY_INVALID),
        ({"minimum_relevance": 1.1}, ResolverFailureReason.POLICY_INVALID),
        ({"minimum_margin": -0.1}, ResolverFailureReason.POLICY_INVALID),
        ({"release_eligible": True}, ResolverFailureReason.POLICY_INVALID),
        (
            {"stage_weights": ((CandidateStage.PRODUCT_NAME_EXACT, 1.0),)},
            ResolverFailureReason.POLICY_INVALID,
        ),
        (
            {
                "stage_weights": (
                    (CandidateStage.PRODUCT_NAME_EXACT, 10**1000),
                    (CandidateStage.APPROVED_ALIAS_EXACT, 3.0),
                    (CandidateStage.TRIGRAM_EDIT_DISTANCE, 2.0),
                    (CandidateStage.DENSE_VECTOR, 1.0),
                )
            },
            ResolverFailureReason.POLICY_INVALID,
        ),
        (
            {"auto_select_stages": frozenset({CandidateStage.DENSE_VECTOR})},
            ResolverFailureReason.POLICY_INVALID,
        ),
    ],
)
def test_invalid_policy_fails_before_index_access(
    changes: dict[str, object], expected_reason: ResolverFailureReason
) -> None:
    port = FakeIndexPort()

    result = resolve(port, policy=synthetic_policy(**changes))

    assert result == ResolverFailure(reason=expected_reason)
    assert port.calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"medication_name": ""},
        {"medication_name": " 합성제품정"},
        {"medication_name": "합성  제품정"},
        {"medication_name": "가"},
        {"medication_name": "가" * 101},
        {"strength_text": ""},
        {"strength_text": " 10mg"},
    ],
)
def test_invalid_confirmed_input_returns_invalid_input_without_port_call(changes: dict[str, object]) -> None:
    port = FakeIndexPort()

    result = resolve(port, input_value=resolver_input(**changes))

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.INVALID_INPUT
    assert result.candidate is None
    assert result.raw_count == 0
    assert port.calls == []


def test_policy_version_mismatch_is_not_invalid_input() -> None:
    port = FakeIndexPort()

    result = resolve(port, input_value=resolver_input(policy_version="other-policy"))

    assert result == ResolverFailure(reason=ResolverFailureReason.POLICY_VERSION_MISMATCH)
    assert port.calls == []


def test_index_provenance_version_mismatch_is_typed_failure() -> None:
    port = FakeIndexPort(
        provenance=CandidateProvenanceReceipt(
            index_version="other-index",
            catalog_version=_CATALOG_VERSION,
            catalog_manifest_hash=_CATALOG_MANIFEST_HASH,
            source_refs=(CandidateSourceRef(_SOURCE_SNAPSHOT_ID, _SOURCE_VERSION),),
            normalization_version=_NORMALIZATION_VERSION,
            embedding_model_version=_EMBEDDING_MODEL_VERSION,
            index_mode=CandidateIndexMode.HYBRID,
        )
    )

    result = resolve(port)

    assert result == ResolverFailure(reason=ResolverFailureReason.EVIDENCE_INVALID)
    assert port.calls == ["HYDRATE"]


def test_catalog_manifest_receipt_must_be_sha256() -> None:
    port = FakeIndexPort(
        provenance=CandidateProvenanceReceipt(
            index_version="candidate-index-v1",
            catalog_version=_CATALOG_VERSION,
            catalog_manifest_hash="not-a-sha256",
            source_refs=(CandidateSourceRef(_SOURCE_SNAPSHOT_ID, _SOURCE_VERSION),),
            normalization_version=_NORMALIZATION_VERSION,
            embedding_model_version=_EMBEDDING_MODEL_VERSION,
            index_mode=CandidateIndexMode.HYBRID,
        )
    )

    assert resolve(port) == ResolverFailure(reason=ResolverFailureReason.EVIDENCE_INVALID)


@pytest.mark.parametrize(
    "provenance",
    [
        CandidateProvenanceReceipt(
            index_version="candidate-index-v1",
            catalog_version=_CATALOG_VERSION,
            catalog_manifest_hash=object(),  # type: ignore[arg-type]
            source_refs=(CandidateSourceRef(_SOURCE_SNAPSHOT_ID, _SOURCE_VERSION),),
            normalization_version=_NORMALIZATION_VERSION,
            embedding_model_version=_EMBEDDING_MODEL_VERSION,
            index_mode=CandidateIndexMode.HYBRID,
        ),
        CandidateProvenanceReceipt(
            index_version="candidate-index-v1",
            catalog_version=_CATALOG_VERSION,
            catalog_manifest_hash=_CATALOG_MANIFEST_HASH,
            source_refs=(CandidateSourceRef([], _SOURCE_VERSION),),  # type: ignore[arg-type]
            normalization_version=_NORMALIZATION_VERSION,
            embedding_model_version=_EMBEDDING_MODEL_VERSION,
            index_mode=CandidateIndexMode.HYBRID,
        ),
    ],
)
def test_malformed_receipt_types_fail_closed(provenance: CandidateProvenanceReceipt) -> None:
    assert resolve(FakeIndexPort(provenance=provenance)) == ResolverFailure(
        reason=ResolverFailureReason.EVIDENCE_INVALID
    )


def test_product_hit_cannot_use_ingredient_stage() -> None:
    invalid = dataclasses.replace(
        hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),
        stage=CandidateStage.INGREDIENT_EXACT,
    )

    assert resolve(FakeIndexPort({CandidateStage.PRODUCT_NAME_EXACT: (invalid,)})) == ResolverFailure(
        reason=ResolverFailureReason.EVIDENCE_INVALID,
        stage=CandidateStage.INGREDIENT_EXACT,
    )


def test_none_product_stage_fails_closed() -> None:
    invalid = dataclasses.replace(
        hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),
        stage=None,  # type: ignore[arg-type]
    )

    assert resolve(FakeIndexPort({CandidateStage.PRODUCT_NAME_EXACT: (invalid,)})) == ResolverFailure(
        reason=ResolverFailureReason.EVIDENCE_INVALID
    )


def test_non_string_member_key_fails_closed() -> None:
    invalid = dataclasses.replace(
        hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),
        member_key=object(),  # type: ignore[arg-type]
    )

    assert resolve(FakeIndexPort({CandidateStage.PRODUCT_NAME_EXACT: (invalid,)})) == ResolverFailure(
        reason=ResolverFailureReason.EVIDENCE_INVALID,
        stage=CandidateStage.PRODUCT_NAME_EXACT,
    )


def test_port_failure_cannot_copy_sensitive_detail_into_stage() -> None:
    class SensitiveFailurePort(FakeIndexPort):
        def hydrate(self, request: CandidateSearchRequest) -> HydratedCandidateEvidence:
            del request
            raise CandidateIndexPortError(stage="SENSITIVE_RAW_QUERY")

    result = resolve(SensitiveFailurePort())

    assert result == ResolverFailure(reason=ResolverFailureReason.PORT_FAILURE)
    assert "SENSITIVE" not in repr(result)


def test_hybrid_search_order_dedupes_identity_and_redacts_internal_scores() -> None:
    exact = hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT)
    alias = hit("SYNTH-P-001", CandidateStage.APPROVED_ALIAS_EXACT)
    port = FakeIndexPort(
        {
            CandidateStage.PRODUCT_NAME_EXACT: (exact,),
            CandidateStage.APPROVED_ALIAS_EXACT: (alias,),
        },
        ingredient_hits=(ingredient_hit(),),
    )
    matcher = FakeAttributeMatcher()
    evaluator = FakeRelevanceEvaluator()

    result = resolve(port, matcher=matcher, evaluator=evaluator)

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.SINGLE_CANDIDATE
    assert result.candidate is not None
    assert result.candidate.identity == product_identity("SYNTH-P-001")
    assert (result.raw_count, result.deduped_count, result.eligible_count) == (2, 1, 1)
    assert port.calls == ["HYDRATE"]
    assert matcher.calls == ["SYNTH-P-001"]
    assert evaluator.calls == ["SYNTH-P-001"]
    assert {field.name for field in dataclasses.fields(CandidateSearchRequest)} == {
        "medication_name",
        "index_version",
        "retrieval_limit",
    }
    visible = result.redacted()
    assert visible.outcome is ResolverOutcome.SINGLE_CANDIDATE
    assert {field.name for field in dataclasses.fields(visible)} == {"outcome", "candidate"}
    visible_payload = dataclasses.asdict(visible)
    assert set(visible_payload["candidate"]) == {
        "product_name",
        "strength_text",
        "dosage_form",
        "manufacturer_name",
        "product_status",
    }
    assert "identity" not in repr(visible_payload)


def test_catalog_display_text_preserves_nfd_and_original_whitespace() -> None:
    original_name = "  가상  제품정  "
    snapshot = dataclasses.replace(product_snapshot("SYNTH-P-001"), product_name=original_name)
    port = FakeIndexPort(
        {CandidateStage.PRODUCT_NAME_EXACT: (hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, snapshot=snapshot),)}
    )

    result = resolve(port)

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.SINGLE_CANDIDATE
    visible = result.redacted()
    assert visible.candidate is not None
    assert visible.candidate.product_name == original_name


@pytest.mark.parametrize(("enable_dense", "raw_count"), [(False, 0), (True, 1)])
def test_dense_evidence_consumption_requires_policy(enable_dense: bool, raw_count: int) -> None:
    port = FakeIndexPort({CandidateStage.DENSE_VECTOR: (hit("SYNTH-P-001", CandidateStage.DENSE_VECTOR),)})

    result = resolve(port, policy=synthetic_policy(enable_dense=enable_dense))

    assert isinstance(result, ResolverResult)
    assert result.raw_count == raw_count
    assert port.calls == ["HYDRATE"]


def test_async_hydrator_has_one_strength_free_typed_handoff() -> None:
    prepared = prepare_candidate_search(resolver_input(), synthetic_policy(enable_dense=False))

    assert isinstance(prepared, CandidateSearchRequest)
    assert prepared == CandidateSearchRequest(
        medication_name="합성제품정",
        index_version="candidate-index-v1",
        retrieval_limit=10,
    )
    port = FakeIndexPort(
        {
            CandidateStage.PRODUCT_NAME_EXACT: (hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),),
            CandidateStage.APPROVED_ALIAS_EXACT: (hit("SYNTH-P-002", CandidateStage.APPROVED_ALIAS_EXACT),),
            CandidateStage.TRIGRAM_EDIT_DISTANCE: (hit("SYNTH-P-003", CandidateStage.TRIGRAM_EDIT_DISTANCE),),
            CandidateStage.DENSE_VECTOR: (hit("SYNTH-P-004", CandidateStage.DENSE_VECTOR),),
        },
        ingredient_hits=(ingredient_hit(),),
    )
    hydrated = port.hydrate(prepared)

    assert port.calls == ["HYDRATE"]
    assert [hit.stage for hit in hydrated.product_hits] == [
        CandidateStage.PRODUCT_NAME_EXACT,
        CandidateStage.APPROVED_ALIAS_EXACT,
        CandidateStage.TRIGRAM_EDIT_DISTANCE,
        CandidateStage.DENSE_VECTOR,
    ]
    assert len(hydrated.ingredient_hits) == 1


def test_multiple_eligible_products_are_always_ambiguous() -> None:
    port = FakeIndexPort(
        {
            CandidateStage.PRODUCT_NAME_EXACT: (
                hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, rank=1),
                hit("SYNTH-P-002", CandidateStage.PRODUCT_NAME_EXACT, rank=2),
            )
        }
    )

    result = resolve(port)

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.AMBIGUOUS
    assert result.candidate is None
    assert result.eligible_count == 2
    assert result.redacted().candidate is None


@pytest.mark.parametrize(
    "outcome",
    [
        ResolverOutcome.AMBIGUOUS,
        ResolverOutcome.NO_CANDIDATE,
        ResolverOutcome.INGREDIENT_ONLY,
        ResolverOutcome.INVALID_INPUT,
    ],
)
def test_redaction_never_exposes_candidate_for_non_single_outcome(outcome: ResolverOutcome) -> None:
    resolved = resolve(
        FakeIndexPort({CandidateStage.PRODUCT_NAME_EXACT: (hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),)})
    )
    assert isinstance(resolved, ResolverResult)
    assert resolved.candidate is not None
    inconsistent_result = dataclasses.replace(resolved, outcome=outcome)

    assert inconsistent_result.redacted().candidate is None


def test_nullable_strength_with_multiple_variants_is_ambiguous() -> None:
    port = FakeIndexPort(
        {
            CandidateStage.PRODUCT_NAME_EXACT: (
                hit("SYNTH-P-010", CandidateStage.PRODUCT_NAME_EXACT, rank=1),
                hit(
                    "SYNTH-P-020",
                    CandidateStage.PRODUCT_NAME_EXACT,
                    rank=2,
                    snapshot=product_snapshot("SYNTH-P-020", strength_text="20mg"),
                ),
            )
        }
    )

    result = resolve(port, input_value=resolver_input(strength_text=None))

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.AMBIGUOUS


@pytest.mark.parametrize(
    "assessment",
    [
        CandidateAttributeAssessment(
            strength=AttributeCompatibility.CONFLICT,
            dosage_form=AttributeCompatibility.NOT_APPLICABLE,
            manufacturer=AttributeCompatibility.NOT_APPLICABLE,
        ),
        CandidateAttributeAssessment(
            strength=AttributeCompatibility.MATCH,
            dosage_form=AttributeCompatibility.UNKNOWN,
            manufacturer=AttributeCompatibility.NOT_APPLICABLE,
        ),
        CandidateAttributeAssessment(
            strength=AttributeCompatibility.MATCH,
            dosage_form=AttributeCompatibility.NOT_APPLICABLE,
            manufacturer=AttributeCompatibility.CONFLICT,
        ),
    ],
)
def test_attribute_conflict_or_unknown_fails_closed(assessment: CandidateAttributeAssessment) -> None:
    port = FakeIndexPort({CandidateStage.PRODUCT_NAME_EXACT: (hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),)})
    matcher = FakeAttributeMatcher({"SYNTH-P-001": assessment})

    result = resolve(port, matcher=matcher)

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.NO_CANDIDATE
    assert result.eligible_count == 0


def test_nullable_strength_requires_not_applicable_assessment() -> None:
    port = FakeIndexPort({CandidateStage.PRODUCT_NAME_EXACT: (hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),)})
    matcher = FakeAttributeMatcher(
        {
            "SYNTH-P-001": CandidateAttributeAssessment(
                strength=AttributeCompatibility.MATCH,
                dosage_form=AttributeCompatibility.NOT_APPLICABLE,
                manufacturer=AttributeCompatibility.NOT_APPLICABLE,
            )
        }
    )

    result = resolve(port, input_value=resolver_input(strength_text=None), matcher=matcher)

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.NO_CANDIDATE


@pytest.mark.parametrize(
    ("port", "evaluator"),
    [
        (
            FakeIndexPort(
                {
                    CandidateStage.PRODUCT_NAME_EXACT: (
                        hit(
                            "SYNTH-P-001",
                            CandidateStage.PRODUCT_NAME_EXACT,
                            snapshot=product_snapshot("SYNTH-P-001", status=ProductStatus.INACTIVE),
                        ),
                    )
                }
            ),
            FakeRelevanceEvaluator(),
        ),
        (
            FakeIndexPort(
                {CandidateStage.PRODUCT_NAME_EXACT: (hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),)}
            ),
            FakeRelevanceEvaluator({"SYNTH-P-001": 0.74}),
        ),
        (
            FakeIndexPort({CandidateStage.DENSE_VECTOR: (hit("SYNTH-P-001", CandidateStage.DENSE_VECTOR),)}),
            FakeRelevanceEvaluator(),
        ),
    ],
)
def test_inactive_low_relevance_and_dense_only_cannot_auto_select(
    port: FakeIndexPort, evaluator: FakeRelevanceEvaluator
) -> None:
    result = resolve(port, evaluator=evaluator)

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.NO_CANDIDATE
    assert result.candidate is None


def test_single_eligible_candidate_requires_margin_from_other_identity() -> None:
    port = FakeIndexPort(
        {
            CandidateStage.PRODUCT_NAME_EXACT: (
                hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, rank=1),
                hit("SYNTH-P-002", CandidateStage.PRODUCT_NAME_EXACT, rank=2),
            )
        }
    )
    matcher = FakeAttributeMatcher(
        {
            "SYNTH-P-002": CandidateAttributeAssessment(
                strength=AttributeCompatibility.CONFLICT,
                dosage_form=AttributeCompatibility.NOT_APPLICABLE,
                manufacturer=AttributeCompatibility.NOT_APPLICABLE,
            )
        }
    )

    result = resolve(port, matcher=matcher, policy=synthetic_policy(minimum_margin=0.04))

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.AMBIGUOUS
    assert result.candidate is None


def test_one_strength_compatible_candidate_can_be_single_when_margin_is_safe() -> None:
    port = FakeIndexPort(
        {
            CandidateStage.PRODUCT_NAME_EXACT: (
                hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, rank=1),
                hit("SYNTH-P-002", CandidateStage.PRODUCT_NAME_EXACT, rank=2),
            )
        }
    )
    matcher = FakeAttributeMatcher(
        {
            "SYNTH-P-002": CandidateAttributeAssessment(
                strength=AttributeCompatibility.CONFLICT,
                dosage_form=AttributeCompatibility.NOT_APPLICABLE,
                manufacturer=AttributeCompatibility.NOT_APPLICABLE,
            )
        }
    )

    result = resolve(port, matcher=matcher)

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.SINGLE_CANDIDATE
    assert result.candidate is not None
    assert result.candidate.identity == product_identity("SYNTH-P-001")


def test_ingredient_only_never_becomes_product_candidate() -> None:
    result = resolve(FakeIndexPort(ingredient_hits=(ingredient_hit(),)))

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.INGREDIENT_ONLY
    assert result.candidate is None
    assert (result.raw_count, result.deduped_count, result.eligible_count) == (0, 0, 0)
    assert result.ingredient_hit_count == 1


def test_no_hits_returns_no_candidate() -> None:
    result = resolve(FakeIndexPort())

    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.NO_CANDIDATE
    assert result.candidate is None


def test_malformed_ingredient_evidence_is_typed_failure() -> None:
    invalid = dataclasses.replace(ingredient_hit(), rank=2)

    result = resolve(FakeIndexPort(ingredient_hits=(invalid,)))

    assert result == ResolverFailure(
        reason=ResolverFailureReason.EVIDENCE_INVALID,
        stage=CandidateStage.INGREDIENT_EXACT,
    )


@pytest.mark.parametrize("ingredient", [False, True])
def test_stage_result_cannot_exceed_retrieval_limit(ingredient: bool) -> None:
    product_hits = (
        hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, rank=1),
        hit("SYNTH-P-002", CandidateStage.PRODUCT_NAME_EXACT, rank=2),
    )
    ingredient_hits = (ingredient_hit(rank=1), ingredient_hit(rank=2))
    port = (
        FakeIndexPort(ingredient_hits=ingredient_hits)
        if ingredient
        else FakeIndexPort({CandidateStage.PRODUCT_NAME_EXACT: product_hits})
    )

    result = resolve(port, policy=synthetic_policy(retrieval_limit=1))

    expected_stage = CandidateStage.INGREDIENT_EXACT if ingredient else CandidateStage.PRODUCT_NAME_EXACT
    assert result == ResolverFailure(
        reason=ResolverFailureReason.EVIDENCE_INVALID,
        stage=expected_stage,
    )


def test_partial_stage_failure_discards_evidence_and_exception_detail() -> None:
    port = FakeIndexPort(
        {CandidateStage.PRODUCT_NAME_EXACT: (hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),)},
        fail_stage=CandidateStage.APPROVED_ALIAS_EXACT,
    )

    result = resolve(port)

    assert result == ResolverFailure(
        reason=ResolverFailureReason.PORT_FAILURE,
        stage=CandidateStage.APPROVED_ALIAS_EXACT,
    )
    assert "sensitive" not in repr(result)


def test_unexpected_programming_error_is_not_misclassified_as_port_failure() -> None:
    class BrokenPort(FakeIndexPort):
        def hydrate(self, request: CandidateSearchRequest) -> HydratedCandidateEvidence:
            del request
            raise RuntimeError("programming defect")

    with pytest.raises(RuntimeError, match="programming defect"):
        resolve(BrokenPort())


@pytest.mark.parametrize(
    "bad_hit",
    [
        hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, rank=0),
        hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, rank=True),
        hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, score=float("nan")),
        hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, score=10**1000),
        hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, index_version="other-index"),
    ],
)
def test_malformed_product_evidence_is_typed_failure(bad_hit: CandidateHit) -> None:
    port = FakeIndexPort({CandidateStage.PRODUCT_NAME_EXACT: (bad_hit,)})

    result = resolve(port)

    assert result == ResolverFailure(
        reason=ResolverFailureReason.EVIDENCE_INVALID,
        stage=CandidateStage.PRODUCT_NAME_EXACT,
    )


@pytest.mark.parametrize(
    "bad_hit",
    [
        dataclasses.replace(
            hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),
            member_key="not-a-sha256",
        ),
        dataclasses.replace(
            hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),
            catalog_version="other-catalog",
        ),
        dataclasses.replace(
            hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),
            source_snapshot_id="other-source",
        ),
        dataclasses.replace(
            hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),
            source_snapshot_id=[],  # type: ignore[arg-type]
        ),
        dataclasses.replace(
            hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),
            normalization_version="other-normalization",
        ),
        dataclasses.replace(
            hit("SYNTH-P-001", CandidateStage.DENSE_VECTOR),
            embedding_model_version="other-embedding",
        ),
    ],
)
def test_hydrated_product_provenance_mismatch_fails_closed(bad_hit: CandidateHit) -> None:
    result = resolve(FakeIndexPort({bad_hit.stage: (bad_hit,)}))

    assert result == ResolverFailure(
        reason=ResolverFailureReason.EVIDENCE_INVALID,
        stage=bad_hit.stage,
    )


@pytest.mark.parametrize(
    "snapshot",
    [
        dataclasses.replace(product_snapshot("SYNTH-P-001"), product_name="가" * 256),
        dataclasses.replace(product_snapshot("SYNTH-P-001"), strength_text="1" * 101),
        dataclasses.replace(product_snapshot("SYNTH-P-001"), dosage_form="정" * 101),
        dataclasses.replace(product_snapshot("SYNTH-P-001"), manufacturer_name="가" * 256),
    ],
)
def test_product_snapshot_display_fields_cannot_exceed_persistence_contract(
    snapshot: ProductSnapshot,
) -> None:
    candidate = hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, snapshot=snapshot)

    result = resolve(FakeIndexPort({CandidateStage.PRODUCT_NAME_EXACT: (candidate,)}))

    assert result == ResolverFailure(
        reason=ResolverFailureReason.EVIDENCE_INVALID,
        stage=CandidateStage.PRODUCT_NAME_EXACT,
    )


def test_conflicting_snapshots_for_same_identity_fail_closed() -> None:
    port = FakeIndexPort(
        {
            CandidateStage.PRODUCT_NAME_EXACT: (hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),),
            CandidateStage.APPROVED_ALIAS_EXACT: (
                hit(
                    "SYNTH-P-001",
                    CandidateStage.APPROVED_ALIAS_EXACT,
                    snapshot=dataclasses.replace(product_snapshot("SYNTH-P-001"), product_name="충돌제품명"),
                ),
            ),
        }
    )

    result = resolve(port)

    assert result == ResolverFailure(reason=ResolverFailureReason.EVIDENCE_INVALID)


def test_fusion_score_overflow_fails_closed() -> None:
    overflowing_weights = tuple(
        (stage, 1e308) for stage in CandidateStage if stage is not CandidateStage.INGREDIENT_EXACT
    )
    port = FakeIndexPort(
        {
            CandidateStage.PRODUCT_NAME_EXACT: (hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),),
            CandidateStage.APPROVED_ALIAS_EXACT: (hit("SYNTH-P-001", CandidateStage.APPROVED_ALIAS_EXACT),),
        }
    )

    result = resolve(
        port,
        policy=synthetic_policy(stage_weights=overflowing_weights, rrf_k=5e-324),
    )

    assert result == ResolverFailure(reason=ResolverFailureReason.EVIDENCE_INVALID)


def test_rrf_same_stage_normalization_and_identity_tie_break_are_deterministic() -> None:
    duplicate_port = FakeIndexPort(
        {
            CandidateStage.PRODUCT_NAME_EXACT: (
                hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, rank=1, score=0.8),
                hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT, rank=2, score=0.99),
            ),
            CandidateStage.APPROVED_ALIAS_EXACT: (hit("SYNTH-P-001", CandidateStage.APPROVED_ALIAS_EXACT, rank=1),),
        }
    )

    duplicate_result = resolve(duplicate_port)

    assert isinstance(duplicate_result, ResolverResult)
    evidence = duplicate_result.internal_candidates[0].evidence
    assert [(signal.stage, signal.rank) for signal in evidence.signals] == [
        (CandidateStage.PRODUCT_NAME_EXACT, 1),
        (CandidateStage.APPROVED_ALIAS_EXACT, 1),
    ]
    assert evidence.fusion_score == pytest.approx(4.0 / 11.0 + 3.0 / 11.0)

    equal_weights = tuple(
        (stage, 1.0)
        for stage in (
            CandidateStage.DENSE_VECTOR,
            CandidateStage.TRIGRAM_EDIT_DISTANCE,
            CandidateStage.APPROVED_ALIAS_EXACT,
            CandidateStage.PRODUCT_NAME_EXACT,
        )
    )
    tie_port = FakeIndexPort(
        {
            CandidateStage.PRODUCT_NAME_EXACT: (hit("SYNTH-P-002", CandidateStage.PRODUCT_NAME_EXACT),),
            CandidateStage.APPROVED_ALIAS_EXACT: (hit("SYNTH-P-001", CandidateStage.APPROVED_ALIAS_EXACT),),
        }
    )

    tie_result = resolve(tie_port, policy=synthetic_policy(stage_weights=equal_weights))

    assert isinstance(tie_result, ResolverResult)
    assert [item.evidence.identity.canonical_code for item in tie_result.internal_candidates] == [
        "SYNTH-P-001",
        "SYNTH-P-002",
    ]


@pytest.mark.parametrize(
    ("matcher", "evaluator", "reason"),
    [
        (FakeAttributeMatcher(raises=True), FakeRelevanceEvaluator(), ResolverFailureReason.PORT_FAILURE),
        (
            FakeAttributeMatcher({"SYNTH-P-001": object()}),
            FakeRelevanceEvaluator(),
            ResolverFailureReason.EVIDENCE_INVALID,
        ),
        (FakeAttributeMatcher(), FakeRelevanceEvaluator(raises=True), ResolverFailureReason.PORT_FAILURE),
        (
            FakeAttributeMatcher(),
            FakeRelevanceEvaluator({"SYNTH-P-001": float("nan")}),
            ResolverFailureReason.EVIDENCE_INVALID,
        ),
        (
            FakeAttributeMatcher(),
            FakeRelevanceEvaluator({"SYNTH-P-001": 10**1000}),
            ResolverFailureReason.EVIDENCE_INVALID,
        ),
    ],
)
def test_matcher_and_relevance_failures_are_typed_and_redacted(
    matcher: FakeAttributeMatcher,
    evaluator: FakeRelevanceEvaluator,
    reason: ResolverFailureReason,
) -> None:
    port = FakeIndexPort({CandidateStage.PRODUCT_NAME_EXACT: (hit("SYNTH-P-001", CandidateStage.PRODUCT_NAME_EXACT),)})

    result = resolve(port, matcher=matcher, evaluator=evaluator)

    assert result == ResolverFailure(reason=reason)
    assert "sensitive" not in repr(result)


def test_backend_resolver_does_not_import_worker_or_infrastructure_packages() -> None:
    service_directory = Path(__file__).parents[2] / "services" / "rag"
    forbidden_prefixes = ("ai_worker", "sqlalchemy", "redis", "boto3", "openai")
    imported_modules: set[str] = set()
    for source_path in service_directory.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.add(node.module)

    assert not {
        module for module in imported_modules if module.startswith(forbidden_prefixes) or "outbox" in module.lower()
    }
