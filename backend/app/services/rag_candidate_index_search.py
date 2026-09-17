"""PostgreSQL physical Candidate Search implementation.

이 모듈은 Backend runtime 소유이며 ``ai_worker``를 일절 import하지 않는다.
#167 검색 단계 순서(PRODUCT_NAME_EXACT → APPROVED_ALIAS_EXACT → TRIGRAM_EDIT_DISTANCE → DENSE_VECTOR)를
PostgreSQL physical query로 충실히 실행하고 raw hits를 생성한다.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_candidate_index import (
    RagCandidateIndexBuildMode,
    RagCandidateIndexMember,
    RagCandidateIndexVersion,
)
from app.models.rag_catalog import RagMedicationSearchEntryType
from app.services.rag.candidate_policy import CandidateStage
from app.services.rag.candidate_resolver import (
    CandidateIndexPortError,
    CandidateSearchRequest,
)


class CandidateIndexSearchError(CandidateIndexPortError):
    """Safe typed Candidate search error without sensitive details."""

    def __init__(self, reason: str, *, stage: CandidateStage | None = None) -> None:
        super().__init__(stage=stage)
        self.reason = reason

    def __str__(self) -> str:
        return f"CandidateIndexSearchError({self.reason})"


@dataclass(frozen=True, slots=True)
class CandidateSearchRawHit:
    identity_entity_type: str
    identity_code_system: str
    identity_canonical_code: str
    product_name: str
    strength_text: str | None
    dosage_form: str | None
    manufacturer_name: str | None
    product_source_snapshot_id: str
    entry_source_snapshot_id: str
    alias_source_snapshot_id: str | None
    member_key: str
    stage: CandidateStage
    rank: int
    stage_score: float
    index_version: str
    catalog_version: str
    normalization_version: str
    embedding_model_version: str | None


class CandidateQueryEmbeddingPort(Protocol):
    async def embed_query(
        self,
        text: str,
        *,
        expected_model_version: str,
        expected_dimension: int,
    ) -> tuple[float, ...]: ...


def validate_search_request(
    request: CandidateSearchRequest,
    version: RagCandidateIndexVersion,
) -> None:
    if not isinstance(request, CandidateSearchRequest):
        raise CandidateIndexSearchError("QUERY_INVALID")
    if not isinstance(request.medication_name, str) or not request.medication_name:
        raise CandidateIndexSearchError("QUERY_INVALID")
    if request.medication_name != request.medication_name.strip():
        raise CandidateIndexSearchError("QUERY_INVALID")
    if not unicodedata.is_normalized("NFC", request.medication_name):
        raise CandidateIndexSearchError("QUERY_INVALID")
    if not isinstance(request.index_version, str) or not request.index_version:
        raise CandidateIndexSearchError("INDEX_VERSION_INVALID")
    if not unicodedata.is_normalized("NFC", request.index_version):
        raise CandidateIndexSearchError("INDEX_VERSION_INVALID")
    if request.index_version != version.index_version:
        raise CandidateIndexSearchError("INDEX_VERSION_MISMATCH")
    if (
        not isinstance(request.retrieval_limit, int)
        or isinstance(request.retrieval_limit, bool)
        or request.retrieval_limit <= 0
        or request.retrieval_limit > version.candidate_limit
    ):
        raise CandidateIndexSearchError("RETRIEVAL_LIMIT_INVALID")


async def search_product_name_exact(
    session: AsyncSession,
    *,
    version: RagCandidateIndexVersion,
    query_text: str,
    limit: int,
) -> tuple[CandidateSearchRawHit, ...]:
    stmt = (
        select(RagCandidateIndexMember)
        .where(
            RagCandidateIndexMember.candidate_index_version_id == version.id,
            RagCandidateIndexMember.entry_type == RagMedicationSearchEntryType.PRODUCT_NAME,
            RagCandidateIndexMember.normalized_text == query_text,
        )
        .order_by(
            RagCandidateIndexMember.identity_entity_type.asc(),
            RagCandidateIndexMember.identity_code_system.asc(),
            RagCandidateIndexMember.identity_canonical_code.asc(),
            RagCandidateIndexMember.entry_type.asc(),
            RagCandidateIndexMember.entry_ref.asc(),
        )
        .limit(limit)
    )
    result = await session.execute(stmt)
    members = list(result.scalars().all())
    return tuple(
        CandidateSearchRawHit(
            identity_entity_type=m.identity_entity_type.value,
            identity_code_system=m.identity_code_system,
            identity_canonical_code=m.identity_canonical_code,
            product_name=m.product_name,
            strength_text=m.strength_text,
            dosage_form=m.dosage_form,
            manufacturer_name=m.manufacturer_name,
            product_source_snapshot_id=str(m.product_source_snapshot_id),
            entry_source_snapshot_id=str(m.entry_source_snapshot_id),
            alias_source_snapshot_id=str(m.alias_source_snapshot_id) if m.alias_source_snapshot_id else None,
            member_key=m.member_key,
            stage=CandidateStage.PRODUCT_NAME_EXACT,
            rank=rank,
            stage_score=1.0,
            index_version=version.index_version,
            catalog_version=m.catalog_version,
            normalization_version=m.normalization_version,
            embedding_model_version=None,
        )
        for rank, m in enumerate(members, start=1)
    )


async def search_approved_alias_exact(
    session: AsyncSession,
    *,
    version: RagCandidateIndexVersion,
    query_text: str,
    limit: int,
) -> tuple[CandidateSearchRawHit, ...]:
    stmt = (
        select(RagCandidateIndexMember)
        .where(
            RagCandidateIndexMember.candidate_index_version_id == version.id,
            RagCandidateIndexMember.entry_type == RagMedicationSearchEntryType.APPROVED_ALIAS,
            RagCandidateIndexMember.normalized_text == query_text,
        )
        .order_by(
            RagCandidateIndexMember.identity_entity_type.asc(),
            RagCandidateIndexMember.identity_code_system.asc(),
            RagCandidateIndexMember.identity_canonical_code.asc(),
            RagCandidateIndexMember.entry_type.asc(),
            RagCandidateIndexMember.entry_ref.asc(),
        )
        .limit(limit)
    )
    result = await session.execute(stmt)
    members = list(result.scalars().all())
    return tuple(
        CandidateSearchRawHit(
            identity_entity_type=m.identity_entity_type.value,
            identity_code_system=m.identity_code_system,
            identity_canonical_code=m.identity_canonical_code,
            product_name=m.product_name,
            strength_text=m.strength_text,
            dosage_form=m.dosage_form,
            manufacturer_name=m.manufacturer_name,
            product_source_snapshot_id=str(m.product_source_snapshot_id),
            entry_source_snapshot_id=str(m.entry_source_snapshot_id),
            alias_source_snapshot_id=str(m.alias_source_snapshot_id) if m.alias_source_snapshot_id else None,
            member_key=m.member_key,
            stage=CandidateStage.APPROVED_ALIAS_EXACT,
            rank=rank,
            stage_score=1.0,
            index_version=version.index_version,
            catalog_version=m.catalog_version,
            normalization_version=m.normalization_version,
            embedding_model_version=None,
        )
        for rank, m in enumerate(members, start=1)
    )


async def search_trigram_edit_distance(
    session: AsyncSession,
    *,
    version: RagCandidateIndexVersion,
    query_text: str,
    limit: int,
) -> tuple[CandidateSearchRawHit, ...]:
    similarity_expr = func.similarity(RagCandidateIndexMember.normalized_text, query_text).label("score")
    stmt = (
        select(RagCandidateIndexMember, similarity_expr)
        .where(
            RagCandidateIndexMember.candidate_index_version_id == version.id,
        )
        .order_by(
            similarity_expr.desc(),
            RagCandidateIndexMember.identity_entity_type.asc(),
            RagCandidateIndexMember.identity_code_system.asc(),
            RagCandidateIndexMember.identity_canonical_code.asc(),
            RagCandidateIndexMember.entry_type.asc(),
            RagCandidateIndexMember.entry_ref.asc(),
        )
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = list(result.all())
    hits: list[CandidateSearchRawHit] = []
    for rank, (m, score) in enumerate(rows, start=1):
        score_val = float(score) if score is not None else 0.0
        if not math.isfinite(score_val):
            raise CandidateIndexSearchError("STAGE_SCORE_NON_FINITE", stage=CandidateStage.TRIGRAM_EDIT_DISTANCE)
        hits.append(
            CandidateSearchRawHit(
                identity_entity_type=m.identity_entity_type.value,
                identity_code_system=m.identity_code_system,
                identity_canonical_code=m.identity_canonical_code,
                product_name=m.product_name,
                strength_text=m.strength_text,
                dosage_form=m.dosage_form,
                manufacturer_name=m.manufacturer_name,
                product_source_snapshot_id=str(m.product_source_snapshot_id),
                entry_source_snapshot_id=str(m.entry_source_snapshot_id),
                alias_source_snapshot_id=str(m.alias_source_snapshot_id) if m.alias_source_snapshot_id else None,
                member_key=m.member_key,
                stage=CandidateStage.TRIGRAM_EDIT_DISTANCE,
                rank=rank,
                stage_score=score_val,
                index_version=version.index_version,
                catalog_version=m.catalog_version,
                normalization_version=m.normalization_version,
                embedding_model_version=None,
            )
        )
    return tuple(hits)


async def search_dense_vector(
    session: AsyncSession,
    *,
    version: RagCandidateIndexVersion,
    query_text: str,
    limit: int,
    embedding_port: CandidateQueryEmbeddingPort | None,
) -> tuple[CandidateSearchRawHit, ...]:
    if version.build_mode is not RagCandidateIndexBuildMode.HYBRID:
        return ()
    if (
        version.embedding_provider is None
        or version.embedding_model is None
        or version.embedding_model_version is None
        or version.embedding_dimension is None
        or version.distance_metric != "COSINE"
    ):
        raise CandidateIndexSearchError("HYBRID_CONFIGURATION_INVALID", stage=CandidateStage.DENSE_VECTOR)
    if embedding_port is None:
        raise CandidateIndexSearchError("QUERY_EMBEDDING_PORT_REQUIRED", stage=CandidateStage.DENSE_VECTOR)

    query_vector = await embedding_port.embed_query(
        query_text,
        expected_model_version=version.embedding_model_version,
        expected_dimension=version.embedding_dimension,
    )
    if not isinstance(query_vector, tuple) or len(query_vector) != version.embedding_dimension:
        raise CandidateIndexSearchError("QUERY_EMBEDDING_DIMENSION_MISMATCH", stage=CandidateStage.DENSE_VECTOR)
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in query_vector):
        raise CandidateIndexSearchError("QUERY_EMBEDDING_NON_FINITE", stage=CandidateStage.DENSE_VECTOR)

    distance_expr = RagCandidateIndexMember.embedding.cosine_distance(list(query_vector)).label("distance")
    stmt = (
        select(RagCandidateIndexMember, distance_expr)
        .where(
            RagCandidateIndexMember.candidate_index_version_id == version.id,
            RagCandidateIndexMember.embedding.is_not(None),
        )
        .order_by(
            distance_expr.asc(),
            RagCandidateIndexMember.identity_entity_type.asc(),
            RagCandidateIndexMember.identity_code_system.asc(),
            RagCandidateIndexMember.identity_canonical_code.asc(),
            RagCandidateIndexMember.entry_type.asc(),
            RagCandidateIndexMember.entry_ref.asc(),
        )
        .limit(limit)
    )
    result = await session.execute(stmt)
    rows = list(result.all())
    hits: list[CandidateSearchRawHit] = []
    for rank, (m, dist) in enumerate(rows, start=1):
        dist_val = float(dist) if dist is not None else 0.0
        if not math.isfinite(dist_val):
            raise CandidateIndexSearchError("STAGE_SCORE_NON_FINITE", stage=CandidateStage.DENSE_VECTOR)
        score = 1.0 - dist_val
        if not math.isfinite(score):
            raise CandidateIndexSearchError("STAGE_SCORE_NON_FINITE", stage=CandidateStage.DENSE_VECTOR)
        hits.append(
            CandidateSearchRawHit(
                identity_entity_type=m.identity_entity_type.value,
                identity_code_system=m.identity_code_system,
                identity_canonical_code=m.identity_canonical_code,
                product_name=m.product_name,
                strength_text=m.strength_text,
                dosage_form=m.dosage_form,
                manufacturer_name=m.manufacturer_name,
                product_source_snapshot_id=str(m.product_source_snapshot_id),
                entry_source_snapshot_id=str(m.entry_source_snapshot_id),
                alias_source_snapshot_id=str(m.alias_source_snapshot_id) if m.alias_source_snapshot_id else None,
                member_key=m.member_key,
                stage=CandidateStage.DENSE_VECTOR,
                rank=rank,
                stage_score=score,
                index_version=version.index_version,
                catalog_version=m.catalog_version,
                normalization_version=m.normalization_version,
                embedding_model_version=version.embedding_model_version,
            )
        )
    return tuple(hits)


async def execute_candidate_search(
    session: AsyncSession,
    *,
    version: RagCandidateIndexVersion,
    request: CandidateSearchRequest,
    embedding_port: CandidateQueryEmbeddingPort | None = None,
) -> tuple[CandidateSearchRawHit, ...]:
    """#167 검색 단계 순서대로 물리 검색을 수행하고 raw hits를 반환한다."""
    validate_search_request(request, version)
    query_text = request.medication_name
    limit = request.retrieval_limit

    p_hits = await search_product_name_exact(session, version=version, query_text=query_text, limit=limit)
    a_hits = await search_approved_alias_exact(session, version=version, query_text=query_text, limit=limit)
    t_hits = await search_trigram_edit_distance(session, version=version, query_text=query_text, limit=limit)
    d_hits: tuple[CandidateSearchRawHit, ...] = ()
    if version.build_mode is RagCandidateIndexBuildMode.HYBRID:
        d_hits = await search_dense_vector(
            session, version=version, query_text=query_text, limit=limit, embedding_port=embedding_port
        )

    return p_hits + a_hits + t_hits + d_hits
