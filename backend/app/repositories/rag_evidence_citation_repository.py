from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_evidence import (
    RagCitation,
    RagCitationAuthorizationStatus,
    RagCitationClaimKind,
    RagCitationReleaseStatus,
    RagCitationSupportStatus,
    RagCitationTargetType,
    RagEvidence,
    RagEvidenceGuideline,
    RagEvidenceGuidelineType,
    RagEvidenceKnowledge,
    RagEvidenceKnowledgeType,
    RagEvidenceRule,
    RagEvidenceRuleType,
    RagEvidenceStatus,
    RagEvidenceType,
)
from app.models.rag_source import RagSourceSnapshot


@dataclass(frozen=True)
class RagEvidenceKnowledgeCreate:
    source_snapshot_id: UUID
    knowledge_key: str
    knowledge_type: RagEvidenceKnowledgeType
    title: str
    content_digest: str
    source_locator: str | None = None


@dataclass(frozen=True)
class RagEvidenceCreate:
    source_snapshot_id: UUID
    evidence_key: str
    evidence_type: RagEvidenceType
    evidence_digest: str
    evidence_status: RagEvidenceStatus
    knowledge_id: UUID | None = None
    product_id: UUID | None = None
    ingredient_id: UUID | None = None
    source_locator: str | None = None


@dataclass(frozen=True)
class RagEvidenceRuleCreate:
    evidence_id: UUID
    rule_key: str
    rule_type: RagEvidenceRuleType
    rule_digest: str


@dataclass(frozen=True)
class RagEvidenceGuidelineCreate:
    evidence_id: UUID
    guideline_key: str
    guideline_type: RagEvidenceGuidelineType
    guideline_digest: str


@dataclass(frozen=True)
class RagCitationCreate:
    evidence_id: UUID
    target_type: RagCitationTargetType
    target_id: str
    claim_key: str
    claim_kind: RagCitationClaimKind
    support_status: RagCitationSupportStatus
    authorization_status: RagCitationAuthorizationStatus
    display_order: int
    source_title: str
    source_locator: str


class RagEvidenceCitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_knowledge_by_key(
        self,
        *,
        source_snapshot_id: UUID,
        knowledge_key: str,
    ) -> RagEvidenceKnowledge | None:
        result = await self.session.execute(
            select(RagEvidenceKnowledge).where(
                RagEvidenceKnowledge.source_snapshot_id == source_snapshot_id,
                RagEvidenceKnowledge.knowledge_key == knowledge_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_evidence_by_key(
        self,
        *,
        source_snapshot_id: UUID,
        evidence_key: str,
    ) -> RagEvidence | None:
        result = await self.session.execute(
            select(RagEvidence).where(
                RagEvidence.source_snapshot_id == source_snapshot_id,
                RagEvidence.evidence_key == evidence_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_approved_evidence_for_product(
        self,
        *,
        source_snapshot_id: UUID,
        product_id: UUID,
    ) -> list[RagEvidence]:
        result = await self.session.execute(
            select(RagEvidence).where(
                RagEvidence.source_snapshot_id == source_snapshot_id,
                RagEvidence.product_id == product_id,
                RagEvidence.evidence_status == RagEvidenceStatus.APPROVED,
            )
        )
        return list(result.scalars().all())

    async def list_approved_evidence_for_ingredient(
        self,
        *,
        source_snapshot_id: UUID,
        ingredient_id: UUID,
    ) -> list[RagEvidence]:
        result = await self.session.execute(
            select(RagEvidence).where(
                RagEvidence.source_snapshot_id == source_snapshot_id,
                RagEvidence.ingredient_id == ingredient_id,
                RagEvidence.evidence_status == RagEvidenceStatus.APPROVED,
            )
        )
        return list(result.scalars().all())

    async def get_rule_by_key(self, *, evidence_id: UUID, rule_key: str) -> RagEvidenceRule | None:
        result = await self.session.execute(
            select(RagEvidenceRule).where(
                RagEvidenceRule.evidence_id == evidence_id,
                RagEvidenceRule.rule_key == rule_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_guideline_by_key(self, *, evidence_id: UUID, guideline_key: str) -> RagEvidenceGuideline | None:
        result = await self.session.execute(
            select(RagEvidenceGuideline).where(
                RagEvidenceGuideline.evidence_id == evidence_id,
                RagEvidenceGuideline.guideline_key == guideline_key,
            )
        )
        return result.scalar_one_or_none()

    async def get_citation_by_claim(
        self,
        *,
        target_type: RagCitationTargetType,
        target_id: str,
        claim_key: str,
    ) -> RagCitation | None:
        result = await self.session.execute(
            select(RagCitation).where(
                RagCitation.target_type == target_type,
                RagCitation.target_id == target_id,
                RagCitation.claim_key == claim_key,
            )
        )
        return result.scalar_one_or_none()

    async def list_public_citations(
        self,
        *,
        target_type: RagCitationTargetType,
        target_id: str,
    ) -> list[RagCitation]:
        raise NotImplementedError("public citation retrieval opens with #180 Citation Authorization Guard")

    async def create_knowledge(self, item: RagEvidenceKnowledgeCreate) -> RagEvidenceKnowledge:
        knowledge = RagEvidenceKnowledge(
            source_snapshot_id=item.source_snapshot_id,
            knowledge_key=item.knowledge_key,
            knowledge_type=item.knowledge_type,
            title=item.title,
            source_locator=item.source_locator,
            content_digest=item.content_digest,
        )
        self.session.add(knowledge)
        await self.session.flush()
        return knowledge

    async def create_evidence(self, item: RagEvidenceCreate) -> RagEvidence:
        evidence = RagEvidence(
            source_snapshot_id=item.source_snapshot_id,
            knowledge_id=item.knowledge_id,
            product_id=item.product_id,
            ingredient_id=item.ingredient_id,
            evidence_key=item.evidence_key,
            evidence_type=item.evidence_type,
            evidence_status=item.evidence_status,
            source_locator=item.source_locator,
            evidence_digest=item.evidence_digest,
        )
        self.session.add(evidence)
        await self.session.flush()
        return evidence

    async def create_rule(self, item: RagEvidenceRuleCreate) -> RagEvidenceRule:
        rule = RagEvidenceRule(
            evidence_id=item.evidence_id,
            rule_key=item.rule_key,
            rule_type=item.rule_type,
            rule_digest=item.rule_digest,
        )
        self.session.add(rule)
        await self.session.flush()
        return rule

    async def create_guideline(self, item: RagEvidenceGuidelineCreate) -> RagEvidenceGuideline:
        guideline = RagEvidenceGuideline(
            evidence_id=item.evidence_id,
            guideline_key=item.guideline_key,
            guideline_type=item.guideline_type,
            guideline_digest=item.guideline_digest,
        )
        self.session.add(guideline)
        await self.session.flush()
        return guideline

    async def create_citation(self, item: RagCitationCreate) -> RagCitation:
        evidence = await self.session.get(RagEvidence, item.evidence_id)
        if evidence is None:
            raise ValueError("Citation evidence does not exist")
        source_snapshot = await self.session.get(RagSourceSnapshot, evidence.source_snapshot_id)
        if source_snapshot is None:
            raise ValueError("Citation source snapshot does not exist")
        citation = RagCitation(
            evidence_id=item.evidence_id,
            source_snapshot_id=evidence.source_snapshot_id,
            evidence_status=evidence.evidence_status,
            target_type=item.target_type,
            target_id=item.target_id,
            claim_key=item.claim_key,
            claim_kind=item.claim_kind,
            support_status=item.support_status,
            authorization_status=item.authorization_status,
            release_status=RagCitationReleaseStatus.NOT_PUBLIC,
            display_order=item.display_order,
            source_title=item.source_title,
            source_version=source_snapshot.source_version,
            source_locator=item.source_locator,
            public_excerpt=None,
        )
        self.session.add(citation)
        await self.session.flush()
        return citation
