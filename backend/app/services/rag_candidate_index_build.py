"""RAG-07B(#168) Candidate Index build execution boundary.

이 모듈은 ``backend``에서 ``ai_worker``를 import하는 유일한 지점이다. RAG-07A(#167)의 순수
로직(``ai_worker.tasks.rag.candidate_index``)을 호출해 build 결과를 얻고, 성공했을 때만
:mod:`app.repositories.rag_candidate_index_repository`로 위임해 저장한다. 실패 판정은 아무
row도 남기지 않는다 (``rag_runtime_bundle_build.py``와 동일한 boundary; 그쪽 모듈 docstring의
근거를 그대로 따른다: ``ai_worker`` 의존은 I/O·clock·session이 없는 순수 kernel 모듈 하나뿐이고,
Router→Service→Repository 원칙을 지키기 위한 최소 Service다).
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.tasks.rag.candidate_index import (
    CandidateEmbeddingPort,
    CandidateIndexBuildConfig,
    CandidateIndexBuildFailure,
    CandidateIndexBuildSuccess,
    CandidateIndexManifest,
    CandidateIndexMember,
    build_candidate_index,
)
from ai_worker.tasks.rag.catalog.export import CatalogExportArtifacts
from app.models.rag_candidate_index import RagCandidateIndexBuildMode, RagCandidateIndexEntityType
from app.models.rag_catalog import RagMedicationSearchEntryType
from app.repositories.rag_candidate_index_repository import (
    RagCandidateIndexBuildResult,
    RagCandidateIndexMemberCreate,
    RagCandidateIndexRepository,
    RagCandidateIndexVersionCreate,
)


@dataclass(frozen=True, slots=True)
class CandidateIndexBuildExecution:
    """What the port did: RAG-07A의 판정, 그리고 BUILDABLE이었을 때만 채워지는 저장 결과."""

    outcome: CandidateIndexBuildSuccess | CandidateIndexBuildFailure
    persisted: RagCandidateIndexBuildResult | None

    @property
    def stored(self) -> bool:
        return self.persisted is not None


async def execute_candidate_index_build(
    session: AsyncSession,
    *,
    artifacts: CatalogExportArtifacts,
    config: CandidateIndexBuildConfig,
    catalog_set_id: UUID,
    embedding_port: CandidateEmbeddingPort | None = None,
) -> CandidateIndexBuildExecution:
    """RAG-07A로 build를 수행하고, 성공했을 때만 하나의 ``BUILDING`` Version으로 저장한다.

    호출자가 트랜잭션을 소유하므로, 저장 중 에러가 나면 Version과 모든 member가 함께 롤백된다.
    실패 판정(``CandidateIndexBuildFailure``)은 어떤 row도 만들지 않는다.
    """
    outcome = build_candidate_index(artifacts, config, embedding_port)
    if isinstance(outcome, CandidateIndexBuildFailure):
        return CandidateIndexBuildExecution(outcome=outcome, persisted=None)

    repository = RagCandidateIndexRepository(session)
    persisted = await repository.build_index_version(
        version=_version_create(outcome.manifest, catalog_set_id=catalog_set_id),
        members=tuple(_member_create(member) for member in outcome.members),
    )
    return CandidateIndexBuildExecution(outcome=outcome, persisted=persisted)


def _version_create(manifest: CandidateIndexManifest, *, catalog_set_id: UUID) -> RagCandidateIndexVersionCreate:
    return RagCandidateIndexVersionCreate(
        index_code=manifest.index_code,
        index_version=manifest.index_version,
        build_mode=RagCandidateIndexBuildMode(manifest.build_mode.value),
        catalog_set_id=catalog_set_id,
        catalog_version=manifest.catalog_version,
        catalog_manifest_hash=manifest.catalog_manifest_hash,
        schema_version=manifest.schema_version,
        normalization_version=manifest.normalization_version,
        lexical_config_version=manifest.lexical_config_version,
        search_order_version=manifest.search_order_version,
        candidate_limit=manifest.candidate_limit,
        display_limit=manifest.display_limit,
        embedding_provider=manifest.embedding_provider,
        embedding_model=manifest.embedding_model,
        embedding_model_version=manifest.embedding_model_version,
        embedding_dimension=manifest.embedding_dimension,
        distance_metric=manifest.distance_metric.value if manifest.distance_metric is not None else None,
        member_count=manifest.member_count,
        product_identity_count=manifest.product_identity_count,
        product_name_count=manifest.product_name_count,
        approved_alias_count=manifest.approved_alias_count,
        vector_count=manifest.vector_count,
        member_set_hash=manifest.member_set_hash,
        configuration_hash=manifest.configuration_hash,
        content_hash=manifest.content_hash,
    )


def _member_create(member: CandidateIndexMember) -> RagCandidateIndexMemberCreate:
    return RagCandidateIndexMemberCreate(
        entry_type=RagMedicationSearchEntryType(member.entry_type.value),
        identity_entity_type=RagCandidateIndexEntityType(member.identity.entity_type.value),
        identity_code_system=member.identity.code_system,
        identity_canonical_code=member.identity.canonical_code,
        product_ref=member.product_ref,
        entry_ref=member.entry_ref,
        display_text=member.display_text,
        normalized_text=member.normalized_text,
        product_name=member.product_name,
        product_source_snapshot_id=UUID(member.product_source_snapshot_id),
        entry_source_snapshot_id=UUID(member.entry_source_snapshot_id),
        catalog_version=member.catalog_version,
        catalog_manifest_hash=member.catalog_manifest_hash,
        normalization_version=member.normalization_version,
        member_key=member.member_key,
        member_content_hash=member.member_content_hash,
        alias_ref=member.alias_ref,
        strength_text=member.strength_text,
        dosage_form=member.dosage_form,
        manufacturer_name=member.manufacturer_name,
        alias_source_snapshot_id=UUID(member.alias_source_snapshot_id) if member.alias_source_snapshot_id else None,
        embedding=member.embedding,
    )
