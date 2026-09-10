from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import config
from app.models.rag_catalog import RagMedicationAliasTargetType, RagMedicationComponentRole
from app.models.rag_evidence import (
    RagCitationAuthorizationStatus,
    RagCitationClaimKind,
    RagCitationSupportStatus,
    RagCitationTargetType,
    RagEvidenceGuidelineType,
    RagEvidenceKnowledgeType,
    RagEvidenceRuleType,
    RagEvidenceStatus,
    RagEvidenceType,
)
from app.models.rag_source import RagSnapshotVerificationStatus
from app.repositories.rag_evidence_citation_repository import (
    RagCitationCreate,
    RagEvidenceCitationRepository,
    RagEvidenceCreate,
    RagEvidenceGuidelineCreate,
    RagEvidenceKnowledgeCreate,
    RagEvidenceRuleCreate,
)
from app.repositories.rag_source_catalog_repository import (
    RagMedicationAliasCreate,
    RagMedicationIngredientCreate,
    RagMedicationProductComponentCreate,
    RagMedicationProductCreate,
    RagSourceCatalogRepository,
    RagSourceCreate,
    RagSourceEndpointCreate,
    RagSourceOperationCreate,
    RagSourceSnapshotCreate,
)

_CHECKSUM = "a" * 64
_OTHER_CHECKSUM = "b" * 64


async def _create_source_catalog_graph(db_session: AsyncSession):
    repository = RagSourceCatalogRepository(db_session)
    suffix = uuid4().hex[:10]
    source = await repository.create_source(
        RagSourceCreate(source_code=f"MFDS_EVIDENCE_{suffix}", display_name="MFDS Evidence Source")
    )
    endpoint = await repository.create_endpoint(
        RagSourceEndpointCreate(
            source_id=source.id,
            endpoint_code="PRODUCT_LIST",
            display_name="Product List",
        )
    )
    operation = await repository.create_operation(
        RagSourceOperationCreate(
            endpoint_id=endpoint.id,
            operation_code="LIST_APPROVED_PRODUCTS",
            display_name="List Approved Products",
        )
    )
    now = datetime.now(config.TIMEZONE)
    snapshot = await repository.create_snapshot(
        RagSourceSnapshotCreate(
            operation_id=operation.id,
            source_version=f"api:2026-09-08:{suffix}",
            raw_manifest_checksum=_CHECKSUM,
            canonical_checksum=_OTHER_CHECKSUM,
            schema_version="schema-v1",
            parser_version="parser-v1",
            normalization_version="normalization-v1",
            canonicalization_spec_version="canonical-v1",
            record_count=1,
            rejected_record_count=0,
            verification_status=RagSnapshotVerificationStatus.CURRENT,
            collected_at=now,
            verified_at=now,
            effective_at=now,
        )
    )
    product = await repository.create_product(
        RagMedicationProductCreate(
            source_snapshot_id=snapshot.id,
            source_record_key="ITEM_SEQ:200012345",
            code_system="MFDS_ITEM_SEQ",
            canonical_code="200012345",
            product_name="테스트정",
            normalized_product_name="테스트정",
            product_status="ACTIVE",
            strength_text="500mg",
        )
    )
    ingredient = await repository.create_ingredient(
        RagMedicationIngredientCreate(
            source_snapshot_id=snapshot.id,
            source_record_key="INGREDIENT:ACETAMINOPHEN",
            ingredient_code_system="MFDS_INGREDIENT",
            ingredient_code="I0001",
            ingredient_name="아세트아미노펜",
            normalized_ingredient_name="아세트아미노펜",
        )
    )
    await repository.create_alias(
        RagMedicationAliasCreate(
            source_snapshot_id=snapshot.id,
            product_id=product.id,
            target_type=RagMedicationAliasTargetType.PRODUCT,
            alias_text="테스트 정",
            normalized_alias_text="테스트정",
            is_approved=True,
        )
    )
    await repository.create_component(
        RagMedicationProductComponentCreate(
            source_snapshot_id=snapshot.id,
            product_id=product.id,
            ingredient_id=ingredient.id,
            component_role=RagMedicationComponentRole.ACTIVE_INGREDIENT,
            display_order=1,
            amount_text="500mg",
        )
    )
    return snapshot, product, ingredient


async def test_rag_evidence_citation_repository_can_save_and_read_minimum_graph(
    db_session: AsyncSession,
) -> None:
    snapshot, product, ingredient = await _create_source_catalog_graph(db_session)
    repository = RagEvidenceCitationRepository(db_session)
    target_id = str(uuid4())

    knowledge = await repository.create_knowledge(
        RagEvidenceKnowledgeCreate(
            source_snapshot_id=snapshot.id,
            knowledge_key="mfds-product:200012345",
            knowledge_type=RagEvidenceKnowledgeType.SOURCE_RECORD,
            title="MFDS product record",
            source_locator="ITEM_SEQ=200012345",
            content_digest="c" * 64,
        )
    )
    evidence = await repository.create_evidence(
        RagEvidenceCreate(
            source_snapshot_id=snapshot.id,
            knowledge_id=knowledge.id,
            product_id=product.id,
            ingredient_id=ingredient.id,
            evidence_key="evidence:200012345:ingredient",
            evidence_type=RagEvidenceType.PRODUCT_FACT,
            evidence_status=RagEvidenceStatus.APPROVED,
            source_locator="ITEM_SEQ=200012345",
            evidence_digest="d" * 64,
        )
    )
    rule = await repository.create_rule(
        RagEvidenceRuleCreate(
            evidence_id=evidence.id,
            rule_key="rule:acetaminophen:max-dose",
            rule_type=RagEvidenceRuleType.SAFETY_FALLBACK,
            rule_digest="e" * 64,
        )
    )
    guideline = await repository.create_guideline(
        RagEvidenceGuidelineCreate(
            evidence_id=evidence.id,
            guideline_key="guideline:limited-response:no-evidence",
            guideline_type=RagEvidenceGuidelineType.LIMITED_RESPONSE,
            guideline_digest="f" * 64,
        )
    )
    citation = await repository.create_citation(
        RagCitationCreate(
            evidence_id=evidence.id,
            target_type=RagCitationTargetType.CHAT_MESSAGE,
            target_id=target_id,
            claim_key="claim:ingredient",
            claim_kind=RagCitationClaimKind.MEDICAL,
            support_status=RagCitationSupportStatus.SUPPORTED,
            authorization_status=RagCitationAuthorizationStatus.PENDING,
            display_order=1,
            source_title="MFDS product record",
            source_locator="ITEM_SEQ=200012345",
        )
    )

    assert (
        await repository.get_knowledge_by_key(source_snapshot_id=snapshot.id, knowledge_key=knowledge.knowledge_key)
        == knowledge
    )
    assert (
        await repository.get_evidence_by_key(source_snapshot_id=snapshot.id, evidence_key=evidence.evidence_key)
        == evidence
    )
    assert await repository.list_approved_evidence_for_product(
        source_snapshot_id=snapshot.id, product_id=product.id
    ) == [evidence]
    assert await repository.list_approved_evidence_for_ingredient(
        source_snapshot_id=snapshot.id, ingredient_id=ingredient.id
    ) == [evidence]
    assert await repository.get_rule_by_key(evidence_id=evidence.id, rule_key=rule.rule_key) == rule
    assert (
        await repository.get_guideline_by_key(evidence_id=evidence.id, guideline_key=guideline.guideline_key)
        == guideline
    )
    assert (
        await repository.get_citation_by_claim(
            target_type=RagCitationTargetType.CHAT_MESSAGE,
            target_id=target_id,
            claim_key="claim:ingredient",
        )
        == citation
    )
    assert citation.source_version == snapshot.source_version
    with pytest.raises(NotImplementedError, match="#180 Citation Authorization Guard"):
        await repository.list_public_citations(target_type=RagCitationTargetType.CHAT_MESSAGE, target_id=target_id)


async def test_rag_evidence_repository_rejects_duplicate_evidence_key(db_session: AsyncSession) -> None:
    snapshot, _, _ = await _create_source_catalog_graph(db_session)
    repository = RagEvidenceCitationRepository(db_session)
    item = RagEvidenceCreate(
        source_snapshot_id=snapshot.id,
        evidence_key="duplicate-evidence",
        evidence_type=RagEvidenceType.SAFETY_POLICY,
        evidence_status=RagEvidenceStatus.APPROVED,
        evidence_digest="a" * 64,
    )
    await repository.create_evidence(item)

    with pytest.raises(IntegrityError):
        await repository.create_evidence(item)


async def test_rag_citation_repository_uses_snapshot_source_version(db_session: AsyncSession) -> None:
    snapshot, _, _ = await _create_source_catalog_graph(db_session)
    repository = RagEvidenceCitationRepository(db_session)
    evidence = await repository.create_evidence(
        RagEvidenceCreate(
            source_snapshot_id=snapshot.id,
            evidence_key="citation-guard-evidence",
            evidence_type=RagEvidenceType.SAFETY_POLICY,
            evidence_status=RagEvidenceStatus.APPROVED,
            evidence_digest="b" * 64,
        )
    )

    citation = await repository.create_citation(
        RagCitationCreate(
            evidence_id=evidence.id,
            target_type=RagCitationTargetType.GUIDE,
            target_id=str(uuid4()),
            claim_key="claim:guard",
            claim_kind=RagCitationClaimKind.MEDICAL,
            support_status=RagCitationSupportStatus.SUPPORTED,
            authorization_status=RagCitationAuthorizationStatus.PENDING,
            display_order=1,
            source_title="MFDS product record",
            source_locator="ITEM_SEQ=200012345",
        )
    )

    assert citation.source_version == snapshot.source_version
