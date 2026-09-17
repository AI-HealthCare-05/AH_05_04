"""Unit tests for CandidateResolverHydrationAdapter and PrehydratedCandidateIndexPort.

Covers:
1. Error mapping and safe typing (no sensitive details).
2. PrehydratedCandidateIndexPort protocol conformance and pure MedicationResolver consumption.
3. Hydration adapter validation, missing product, field mismatch, and status validation.
4. Parity verification with #167 CandidateIndexSearchPort and search_candidate_index.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from ai_worker.tasks.rag.candidate_index import (
    CandidateEntityType,
    CandidateIndexBuildSuccess,
    CandidateIndexManifest,
    CandidateIndexSearchSuccess,
    CandidateRawHit,
    CandidateSearchQuery,
    CandidateSearchStage,
    ProductIdentity,
    search_candidate_index,
)
from ai_worker.tasks.rag.candidate_index import (
    _build_candidate_index_members as build_candidate_index,
)
from ai_worker.tests.rag.test_candidate_index import (
    FixedEmbeddingPort,
    hybrid_config,
    valid_catalog,
)
from app.models.rag_candidate_index import (
    RagCandidateIndexBuildMode,
    RagCandidateIndexStatus,
    RagCandidateIndexVersion,
)
from app.models.rag_catalog import RagMedicationProduct
from app.repositories.rag_candidate_index_repository import (
    CandidateIndexIntegrityCompromisedError,
    CandidateIndexReadyVersionNotFoundError,
    CandidateIndexSourceBindingMismatchError,
    CandidateIndexVersionMismatchError,
    ReadyCandidateIndexSourceRef,
    VerifiedReadyCandidateIndexSnapshot,
)
from app.services.rag.candidate_policy import CandidateStage, ResolverPolicy
from app.services.rag.candidate_resolver import (
    AttributeCompatibility,
    CandidateAttributeAssessment,
    CandidateEvidence,
    CandidateHit,
    CandidateIndexMode,
    CandidateIndexPortError,
    CandidateProvenanceReceipt,
    CandidateSearchRequest,
    CandidateSourceRef,
    HydratedCandidateEvidence,
    MedicationResolver,
    OfficialEntityType,
    OfficialIdentity,
    ProductSnapshot,
    ProductStatus,
    ResolverInput,
    ResolverOutcome,
    ResolverResult,
)
from app.services.rag_candidate_index_search import (
    CandidateIndexSearchError,
    CandidateSearchRawHit,
)
from app.services.rag_candidate_resolver_hydration import (
    CandidateIndexHydrationError,
    CandidateResolverHydrationAdapter,
)


@dataclass(frozen=True, slots=True)
class PrehydratedCandidateIndexPort:
    """In-memory CandidateIndexPort protocol test bridge for pure MedicationResolver consumption."""

    evidence: HydratedCandidateEvidence

    def hydrate(self, request: CandidateSearchRequest) -> HydratedCandidateEvidence:
        return self.evidence


def _make_version(
    *,
    index_version: str = "index-v1",
    build_mode: RagCandidateIndexBuildMode = RagCandidateIndexBuildMode.HYBRID,
) -> RagCandidateIndexVersion:
    version = MagicMock(spec=RagCandidateIndexVersion)
    version.id = uuid4()
    version.index_code = "MFDS_CANDIDATE_INDEX"
    version.index_version = index_version
    version.catalog_version = "catalog-v1"
    version.catalog_manifest_hash = "a" * 64
    version.normalization_version = "norm-v1"
    version.lexical_config_version = "lex-v1"
    version.search_order_version = "ord-v1"
    version.candidate_limit = 50
    version.display_limit = 1
    version.build_mode = build_mode
    version.status = RagCandidateIndexStatus.READY
    version.embedding_provider = "openai" if build_mode is RagCandidateIndexBuildMode.HYBRID else None
    version.embedding_model = "text-embedding-3-small" if build_mode is RagCandidateIndexBuildMode.HYBRID else None
    version.embedding_model_version = "v1" if build_mode is RagCandidateIndexBuildMode.HYBRID else None
    version.embedding_dimension = 1536 if build_mode is RagCandidateIndexBuildMode.HYBRID else None
    version.distance_metric = "COSINE" if build_mode is RagCandidateIndexBuildMode.HYBRID else None
    version.ann_config = {"method": "hnsw"} if build_mode is RagCandidateIndexBuildMode.HYBRID else None
    return version


def _synthetic_policy(**overrides: object) -> ResolverPolicy:
    defaults: dict[str, object] = {
        "policy_version": "resolver-policy-v1",
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
        "minimum_relevance": 0.5,
        "minimum_margin": 0.01,
        "auto_select_stages": frozenset(
            {
                CandidateStage.PRODUCT_NAME_EXACT,
                CandidateStage.APPROVED_ALIAS_EXACT,
                CandidateStage.TRIGRAM_EDIT_DISTANCE,
            }
        ),
    }
    defaults.update(overrides)
    return ResolverPolicy(**defaults)  # type: ignore[arg-type]


class _SyntheticMatcher:
    def assess(self, resolver_input: ResolverInput, product: ProductSnapshot) -> CandidateAttributeAssessment:
        return CandidateAttributeAssessment(
            strength=AttributeCompatibility.MATCH,
            dosage_form=AttributeCompatibility.NOT_APPLICABLE,
            manufacturer=AttributeCompatibility.NOT_APPLICABLE,
        )


class _SyntheticEvaluator:
    def evaluate(self, search_request: CandidateSearchRequest, candidate: CandidateEvidence) -> float:
        return 0.95


def test_error_typing_and_safe_representation():
    hydration_err = CandidateIndexHydrationError("NO_READY_INDEX")
    assert isinstance(hydration_err, CandidateIndexPortError)
    assert "NO_READY_INDEX" in str(hydration_err)
    assert "password" not in str(hydration_err).lower()
    assert "patient" not in str(hydration_err).lower()

    search_err = CandidateIndexSearchError("STAGE_SCORE_NON_FINITE", stage=CandidateStage.DENSE_VECTOR)
    assert isinstance(search_err, CandidateIndexPortError)
    assert search_err.stage is CandidateStage.DENSE_VECTOR


def test_prehydrated_candidate_index_port_satisfies_protocol():
    snapshot_id = str(uuid4())
    provenance = CandidateProvenanceReceipt(
        index_version="index-v1",
        catalog_version="catalog-v1",
        catalog_manifest_hash="a" * 64,
        source_refs=(CandidateSourceRef(snapshot_id=snapshot_id, source_version="v1"),),
        normalization_version="norm-v1",
        embedding_model_version="v1",
        index_mode=CandidateIndexMode.HYBRID,
    )
    product = ProductSnapshot(
        identity=OfficialIdentity(
            entity_type=OfficialEntityType.PRODUCT,
            code_system="MFDS_ITEM_SEQ",
            canonical_code="P-100",
        ),
        product_name="테스트약정",
        strength_text="10mg",
        dosage_form="정제",
        manufacturer_name="테스트제약",
        status=ProductStatus.ACTIVE,
    )
    hit = CandidateHit(
        identity=product.identity,
        product=product,
        stage=CandidateStage.PRODUCT_NAME_EXACT,
        rank=1,
        stage_score=1.0,
        index_version="index-v1",
        member_key="a" * 64,
        catalog_version="catalog-v1",
        source_snapshot_id=snapshot_id,
        normalization_version="norm-v1",
        embedding_model_version=None,
    )
    evidence = HydratedCandidateEvidence(
        provenance=provenance,
        product_hits=(hit,),
        ingredient_hits=(),
    )
    port = PrehydratedCandidateIndexPort(evidence=evidence)

    assert hasattr(port, "hydrate")
    assert callable(port.hydrate)

    req = CandidateSearchRequest(
        medication_name="테스트약정",
        index_version="index-v1",
        retrieval_limit=10,
    )
    hydrated = port.hydrate(req)
    assert hydrated is evidence
    assert hydrated.ingredient_hits == ()


def test_pure_medication_resolver_consumes_prehydrated_evidence():
    snapshot_id = str(uuid4())
    product = ProductSnapshot(
        identity=OfficialIdentity(
            entity_type=OfficialEntityType.PRODUCT,
            code_system="MFDS_ITEM_SEQ",
            canonical_code="P-100",
        ),
        product_name="테스트약정",
        strength_text="10mg",
        dosage_form="정제",
        manufacturer_name="테스트제약",
        status=ProductStatus.ACTIVE,
    )
    hit = CandidateHit(
        identity=product.identity,
        product=product,
        stage=CandidateStage.PRODUCT_NAME_EXACT,
        rank=1,
        stage_score=1.0,
        index_version="index-v1",
        member_key="a" * 64,
        catalog_version="catalog-v1",
        source_snapshot_id=snapshot_id,
        normalization_version="norm-v1",
        embedding_model_version=None,
    )
    provenance = CandidateProvenanceReceipt(
        index_version="index-v1",
        catalog_version="catalog-v1",
        catalog_manifest_hash="a" * 64,
        source_refs=(CandidateSourceRef(snapshot_id=snapshot_id, source_version="v1"),),
        normalization_version="norm-v1",
        embedding_model_version=None,
        index_mode=CandidateIndexMode.LEXICAL_ONLY,
    )
    evidence = HydratedCandidateEvidence(
        provenance=provenance,
        product_hits=(hit,),
        ingredient_hits=(),
    )
    port = PrehydratedCandidateIndexPort(evidence=evidence)

    matcher = _SyntheticMatcher()
    evaluator = _SyntheticEvaluator()
    resolver = MedicationResolver(
        index_port=port,
        attribute_matcher=matcher,
        relevance_evaluator=evaluator,
    )
    r_input = ResolverInput(
        medication_name="테스트약정",
        strength_text="10mg",
        index_version="index-v1",
        policy_version="resolver-policy-v1",
    )
    policy = _synthetic_policy()

    result = resolver.resolve(r_input, policy)
    assert isinstance(result, ResolverResult)
    assert result.outcome is ResolverOutcome.SINGLE_CANDIDATE
    assert result.candidate is not None
    assert result.candidate.identity.canonical_code == "P-100"
    assert result.candidate.product.product_name == "테스트약정"
    assert result.raw_count == 1
    assert result.ingredient_hit_count == 0


@pytest.mark.asyncio
async def test_hydration_adapter_maps_repository_exceptions():
    session = AsyncMock()
    adapter = CandidateResolverHydrationAdapter(session, index_code="MFDS_CANDIDATE_INDEX")
    req = CandidateSearchRequest(
        medication_name="테스트약정",
        index_version="index-v1",
        retrieval_limit=10,
    )

    adapter._repository.get_verified_ready_index_snapshot = AsyncMock(  # type: ignore[method-assign]
        side_effect=CandidateIndexReadyVersionNotFoundError("not found")
    )
    with pytest.raises(CandidateIndexHydrationError) as exc_info:
        await adapter.hydrate_evidence(req)
    assert exc_info.value.reason == "NO_READY_INDEX"

    adapter._repository.get_verified_ready_index_snapshot = AsyncMock(  # type: ignore[method-assign]
        side_effect=CandidateIndexVersionMismatchError("mismatch")
    )
    with pytest.raises(CandidateIndexHydrationError) as exc_info:
        await adapter.hydrate_evidence(req)
    assert exc_info.value.reason == "INDEX_VERSION_MISMATCH"

    adapter._repository.get_verified_ready_index_snapshot = AsyncMock(  # type: ignore[method-assign]
        side_effect=CandidateIndexIntegrityCompromisedError("compromised")
    )
    with pytest.raises(CandidateIndexHydrationError) as exc_info:
        await adapter.hydrate_evidence(req)
    assert exc_info.value.reason == "PERSISTENCE_INTEGRITY_COMPROMISED"

    adapter._repository.get_verified_ready_index_snapshot = AsyncMock(  # type: ignore[method-assign]
        side_effect=CandidateIndexSourceBindingMismatchError("binding mismatch")
    )
    with pytest.raises(CandidateIndexHydrationError) as exc_info:
        await adapter.hydrate_evidence(req)
    assert exc_info.value.reason == "SOURCE_SNAPSHOT_MISMATCH"


@pytest.mark.asyncio
async def test_hydration_adapter_missing_product_row_raises():
    session = AsyncMock()
    version = _make_version(index_version="index-v1")
    snapshot = VerifiedReadyCandidateIndexSnapshot(
        version=version,
        members=(),
        source_refs=(ReadyCandidateIndexSourceRef(snapshot_id=str(uuid4()), source_version="v1"),),
    )
    adapter = CandidateResolverHydrationAdapter(session, index_code="MFDS_CANDIDATE_INDEX")
    adapter._repository.get_verified_ready_index_snapshot = AsyncMock(return_value=snapshot)  # type: ignore[method-assign]

    source_snapshot_id = str(uuid4())
    raw_hit = CandidateSearchRawHit(
        identity_entity_type="PRODUCT",
        identity_code_system="MFDS_ITEM_SEQ",
        identity_canonical_code="P-100",
        product_name="약물정",
        strength_text="10mg",
        dosage_form="정제",
        manufacturer_name="제약사",
        product_source_snapshot_id=source_snapshot_id,
        entry_source_snapshot_id=source_snapshot_id,
        alias_source_snapshot_id=None,
        member_key="a" * 64,
        stage=CandidateStage.PRODUCT_NAME_EXACT,
        rank=1,
        stage_score=1.0,
        index_version="index-v1",
        catalog_version="catalog-v1",
        normalization_version="norm-v1",
        embedding_model_version=None,
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=mock_result)

    req = CandidateSearchRequest(
        medication_name="약물정",
        index_version="index-v1",
        retrieval_limit=10,
    )

    with patch("app.services.rag_candidate_resolver_hydration.execute_candidate_search", return_value=(raw_hit,)):
        with pytest.raises(CandidateIndexHydrationError) as exc_info:
            await adapter.hydrate_evidence(req)
        assert exc_info.value.reason == "PRODUCT_ROW_MISSING_OR_AMBIGUOUS"


@pytest.mark.asyncio
async def test_hydration_adapter_product_field_mismatch_raises():
    session = AsyncMock()
    version = _make_version(index_version="index-v1")
    snapshot = VerifiedReadyCandidateIndexSnapshot(
        version=version,
        members=(),
        source_refs=(ReadyCandidateIndexSourceRef(snapshot_id=str(uuid4()), source_version="v1"),),
    )
    adapter = CandidateResolverHydrationAdapter(session, index_code="MFDS_CANDIDATE_INDEX")
    adapter._repository.get_verified_ready_index_snapshot = AsyncMock(return_value=snapshot)  # type: ignore[method-assign]

    source_snapshot_id = str(uuid4())
    raw_hit = CandidateSearchRawHit(
        identity_entity_type="PRODUCT",
        identity_code_system="MFDS_ITEM_SEQ",
        identity_canonical_code="P-100",
        product_name="인덱스약물정",
        strength_text="10mg",
        dosage_form="정제",
        manufacturer_name="제약사",
        product_source_snapshot_id=source_snapshot_id,
        entry_source_snapshot_id=source_snapshot_id,
        alias_source_snapshot_id=None,
        member_key="a" * 64,
        stage=CandidateStage.PRODUCT_NAME_EXACT,
        rank=1,
        stage_score=1.0,
        index_version="index-v1",
        catalog_version="catalog-v1",
        normalization_version="norm-v1",
        embedding_model_version=None,
    )

    db_product = MagicMock(spec=RagMedicationProduct)
    db_product.source_snapshot_id = uuid4()
    db_product.code_system = "MFDS_ITEM_SEQ"
    db_product.canonical_code = "P-100"
    db_product.product_name = "다른약물정"  # Mismatch
    db_product.strength_text = "10mg"
    db_product.dosage_form = "정제"
    db_product.manufacturer_name = "제약사"
    db_product.product_status = "ACTIVE"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [db_product]
    session.execute = AsyncMock(return_value=mock_result)

    req = CandidateSearchRequest(
        medication_name="인덱스약물정",
        index_version="index-v1",
        retrieval_limit=10,
    )

    with patch("app.services.rag_candidate_resolver_hydration.execute_candidate_search", return_value=(raw_hit,)):
        with pytest.raises(CandidateIndexHydrationError) as exc_info:
            await adapter.hydrate_evidence(req)
        assert exc_info.value.reason == "PRODUCT_FIELD_MISMATCH"


@pytest.mark.asyncio
async def test_hydration_adapter_invalid_product_status_raises():
    session = AsyncMock()
    version = _make_version(index_version="index-v1")
    snapshot = VerifiedReadyCandidateIndexSnapshot(
        version=version,
        members=(),
        source_refs=(ReadyCandidateIndexSourceRef(snapshot_id=str(uuid4()), source_version="v1"),),
    )
    adapter = CandidateResolverHydrationAdapter(session, index_code="MFDS_CANDIDATE_INDEX")
    adapter._repository.get_verified_ready_index_snapshot = AsyncMock(return_value=snapshot)  # type: ignore[method-assign]

    source_snapshot_id = str(uuid4())
    raw_hit = CandidateSearchRawHit(
        identity_entity_type="PRODUCT",
        identity_code_system="MFDS_ITEM_SEQ",
        identity_canonical_code="P-100",
        product_name="약물정",
        strength_text="10mg",
        dosage_form="정제",
        manufacturer_name="제약사",
        product_source_snapshot_id=source_snapshot_id,
        entry_source_snapshot_id=source_snapshot_id,
        alias_source_snapshot_id=None,
        member_key="a" * 64,
        stage=CandidateStage.PRODUCT_NAME_EXACT,
        rank=1,
        stage_score=1.0,
        index_version="index-v1",
        catalog_version="catalog-v1",
        normalization_version="norm-v1",
        embedding_model_version=None,
    )

    db_product = MagicMock(spec=RagMedicationProduct)
    db_product.source_snapshot_id = uuid4()
    db_product.code_system = "MFDS_ITEM_SEQ"
    db_product.canonical_code = "P-100"
    db_product.product_name = "약물정"
    db_product.strength_text = "10mg"
    db_product.dosage_form = "정제"
    db_product.manufacturer_name = "제약사"
    db_product.product_status = "UNKNOWN_STATUS"  # Invalid

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [db_product]
    session.execute = AsyncMock(return_value=mock_result)

    req = CandidateSearchRequest(
        medication_name="약물정",
        index_version="index-v1",
        retrieval_limit=10,
    )

    with patch("app.services.rag_candidate_resolver_hydration.execute_candidate_search", return_value=(raw_hit,)):
        with pytest.raises(CandidateIndexHydrationError) as exc_info:
            await adapter.hydrate_evidence(req)
        assert exc_info.value.reason == "PRODUCT_STATUS_UNKNOWN"


def test_parity_oracle_search_candidate_index():
    catalog = valid_catalog()
    config = hybrid_config(dimension=2)
    build_result = build_candidate_index(
        catalog,
        config,
        FixedEmbeddingPort(),
    )
    assert isinstance(build_result, CandidateIndexBuildSuccess)
    manifest = build_result.manifest

    def _make_raw_hit(
        stage: CandidateSearchStage,
        code: str,
        score: float,
        rank: int,
    ) -> CandidateRawHit:
        return CandidateRawHit(
            identity=ProductIdentity(
                entity_type=CandidateEntityType.PRODUCT,
                code_system="MFDS_ITEM_SEQ",
                canonical_code=code,
            ),
            member_key="a" * 64,
            stage=stage,
            rank=rank,
            stage_score=score,
            index_version=manifest.index_version,
            catalog_version=manifest.catalog_version,
            source_snapshot_id="snapshot-1",
            normalization_version=manifest.normalization_version,
            embedding_model_version=manifest.embedding_model_version
            if stage is CandidateSearchStage.DENSE_VECTOR
            else None,
        )

    class SyntheticSearchPort:
        def search_product_name_exact(
            self, query: CandidateSearchQuery, manifest: CandidateIndexManifest
        ) -> tuple[CandidateRawHit, ...]:
            return (_make_raw_hit(CandidateSearchStage.PRODUCT_NAME_EXACT, "P-001", 1.0, 1),)

        def search_approved_alias_exact(
            self, query: CandidateSearchQuery, manifest: CandidateIndexManifest
        ) -> tuple[CandidateRawHit, ...]:
            return (_make_raw_hit(CandidateSearchStage.APPROVED_ALIAS_EXACT, "P-001", 1.0, 1),)

        def search_trigram_edit_distance(
            self, query: CandidateSearchQuery, manifest: CandidateIndexManifest
        ) -> tuple[CandidateRawHit, ...]:
            return (_make_raw_hit(CandidateSearchStage.TRIGRAM_EDIT_DISTANCE, "P-001", 0.85, 1),)

        def search_dense_vector(
            self, query: CandidateSearchQuery, manifest: CandidateIndexManifest
        ) -> tuple[CandidateRawHit, ...]:
            return (_make_raw_hit(CandidateSearchStage.DENSE_VECTOR, "P-001", 0.92, 1),)

    port = SyntheticSearchPort()
    query = CandidateSearchQuery(
        index_version=manifest.index_version,
        normalized_query="가나다정",
        retrieval_limit=10,
    )

    outcome = search_candidate_index(query, manifest, port)
    assert isinstance(outcome, CandidateIndexSearchSuccess)
    assert len(outcome.raw_hits) == 4
    stages = [h.stage for h in outcome.raw_hits]
    assert stages == [
        CandidateSearchStage.PRODUCT_NAME_EXACT,
        CandidateSearchStage.APPROVED_ALIAS_EXACT,
        CandidateSearchStage.TRIGRAM_EDIT_DISTANCE,
        CandidateSearchStage.DENSE_VECTOR,
    ]


@pytest.mark.asyncio
async def test_exact_search_multi_hit_ranks_and_resolver_ambiguity():
    """Unit test for multi-hit exact stage results (ranks 1, 2) and Resolver consumption."""
    session = AsyncMock()
    adapter = CandidateResolverHydrationAdapter(session, index_code="MFDS_CANDIDATE_INDEX")

    source_snapshot_id = "00000000-0000-0000-0000-000000000001"
    version = _make_version()
    snapshot = VerifiedReadyCandidateIndexSnapshot(
        version=version,
        members=(),
        source_refs=(ReadyCandidateIndexSourceRef(snapshot_id=source_snapshot_id, source_version="v1"),),
    )
    adapter._repository.get_verified_ready_index_snapshot = AsyncMock(return_value=snapshot)  # type: ignore[method-assign]

    hit1 = CandidateSearchRawHit(
        identity_entity_type="PRODUCT",
        identity_code_system="MFDS_ITEM_SEQ",
        identity_canonical_code="P-001",
        product_name="공통정",
        strength_text="10mg",
        dosage_form="정제",
        manufacturer_name="제약사A",
        product_source_snapshot_id="00000000-0000-0000-0000-000000000001",
        entry_source_snapshot_id="00000000-0000-0000-0000-000000000001",
        alias_source_snapshot_id=None,
        member_key="a" * 64,
        stage=CandidateStage.PRODUCT_NAME_EXACT,
        rank=1,
        stage_score=1.0,
        index_version="index-v1",
        catalog_version="catalog-v1",
        normalization_version="norm-v1",
        embedding_model_version=None,
    )
    hit2 = CandidateSearchRawHit(
        identity_entity_type="PRODUCT",
        identity_code_system="MFDS_ITEM_SEQ",
        identity_canonical_code="P-002",
        product_name="공통정",
        strength_text="20mg",
        dosage_form="정제",
        manufacturer_name="제약사B",
        product_source_snapshot_id="00000000-0000-0000-0000-000000000001",
        entry_source_snapshot_id="00000000-0000-0000-0000-000000000001",
        alias_source_snapshot_id=None,
        member_key="b" * 64,
        stage=CandidateStage.PRODUCT_NAME_EXACT,
        rank=2,
        stage_score=1.0,
        index_version="index-v1",
        catalog_version="catalog-v1",
        normalization_version="norm-v1",
        embedding_model_version=None,
    )

    prod1 = MagicMock(spec=RagMedicationProduct)
    prod1.source_snapshot_id = UUID("00000000-0000-0000-0000-000000000001")
    prod1.code_system = "MFDS_ITEM_SEQ"
    prod1.canonical_code = "P-001"
    prod1.product_name = "공통정"
    prod1.strength_text = "10mg"
    prod1.dosage_form = "정제"
    prod1.manufacturer_name = "제약사A"
    prod1.product_status = "ACTIVE"

    prod2 = MagicMock(spec=RagMedicationProduct)
    prod2.source_snapshot_id = UUID("00000000-0000-0000-0000-000000000001")
    prod2.code_system = "MFDS_ITEM_SEQ"
    prod2.canonical_code = "P-002"
    prod2.product_name = "공통정"
    prod2.strength_text = "20mg"
    prod2.dosage_form = "정제"
    prod2.manufacturer_name = "제약사B"
    prod2.product_status = "ACTIVE"

    def mock_execute(stmt):
        params = stmt.compile().params
        code = next((v for k, v in params.items() if "canonical_code" in k), None)
        res = MagicMock()
        if code == "P-001":
            res.scalars.return_value.all.return_value = [prod1]
        elif code == "P-002":
            res.scalars.return_value.all.return_value = [prod2]
        else:
            res.scalars.return_value.all.return_value = []
        return res

    session.execute = AsyncMock(side_effect=mock_execute)

    req = CandidateSearchRequest(
        medication_name="공통정",
        index_version="index-v1",
        retrieval_limit=10,
    )

    with patch(
        "app.services.rag_candidate_resolver_hydration.execute_candidate_search",
        new=AsyncMock(return_value=(hit1, hit2)),
    ):
        evidence = await adapter.hydrate_evidence(req)

    assert isinstance(evidence, HydratedCandidateEvidence)
    assert len(evidence.product_hits) == 2
    assert [h.rank for h in evidence.product_hits] == [1, 2]
    assert [h.stage_score for h in evidence.product_hits] == [1.0, 1.0]
    assert evidence.product_hits[0].identity.canonical_code == "P-001"
    assert evidence.product_hits[1].identity.canonical_code == "P-002"

    # Consume via pure MedicationResolver
    port = PrehydratedCandidateIndexPort(evidence=evidence)

    class MultiHitMatcher:
        def assess(self, resolver_input: ResolverInput, product: ProductSnapshot) -> CandidateAttributeAssessment:
            return CandidateAttributeAssessment(
                strength=AttributeCompatibility.NOT_APPLICABLE,
                dosage_form=AttributeCompatibility.NOT_APPLICABLE,
                manufacturer=AttributeCompatibility.NOT_APPLICABLE,
            )

    matcher = MultiHitMatcher()
    evaluator = _SyntheticEvaluator()
    resolver = MedicationResolver(
        index_port=port,
        attribute_matcher=matcher,
        relevance_evaluator=evaluator,
    )
    policy = _synthetic_policy()
    r_input = ResolverInput(
        medication_name="공통정",
        strength_text=None,
        index_version="index-v1",
        policy_version="resolver-policy-v1",
    )
    res = resolver.resolve(r_input, policy)
    assert isinstance(res, ResolverResult)
    assert res.outcome is ResolverOutcome.AMBIGUOUS
    assert res.candidate is None
    assert len(res.internal_candidates) == 2
