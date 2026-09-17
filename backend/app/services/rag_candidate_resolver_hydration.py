"""READY Candidate Index -> Resolver Hydration Adapter.

이 모듈은 Backend runtime 소유이며 ``ai_worker``를 일절 import하지 않는다.
PostgreSQL에 저장되어 READY 상태인 Candidate Index를 조회하고,
persisted 무결성과 Source 결속을 재검증한 뒤, #167 규격 물리 검색 결과를
기존 #170 타입(CandidateProvenanceReceipt, HydratedCandidateEvidence)으로 변환한다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.rag_candidate_index import RagCandidateIndexBuildMode
from app.models.rag_catalog import RagMedicationProduct
from app.repositories.rag_candidate_index_repository import (
    CandidateIndexIntegrityCompromisedError,
    CandidateIndexReadyVersionNotFoundError,
    CandidateIndexSourceBindingMismatchError,
    CandidateIndexVersionMismatchError,
    RagCandidateIndexRepository,
)
from app.services.rag.candidate_policy import CandidateStage
from app.services.rag.candidate_resolver import (
    CandidateHit,
    CandidateIndexMode,
    CandidateIndexPortError,
    CandidateProvenanceReceipt,
    CandidateSearchRequest,
    CandidateSourceRef,
    HydratedCandidateEvidence,
    OfficialEntityType,
    OfficialIdentity,
    ProductSnapshot,
    ProductStatus,
)
from app.services.rag_candidate_index_search import (
    CandidateIndexSearchError,
    CandidateQueryEmbeddingPort,
    CandidateSearchRawHit,
    execute_candidate_search,
)


class CandidateIndexHydrationError(CandidateIndexPortError):
    """Safe typed Candidate Index hydration failure without sensitive details."""

    def __init__(self, reason: str, *, stage: CandidateStage | None = None) -> None:
        super().__init__(stage=stage)
        self.reason = reason

    def __str__(self) -> str:
        return f"CandidateIndexHydrationError({self.reason})"


class CandidateResolverHydrationAdapter:
    """READY Candidate Index를 읽고, #167 물리 검색 후 #170 evidence로 변환하는 어댑터."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        index_code: str,
        embedding_port: CandidateQueryEmbeddingPort | None = None,
    ) -> None:
        self._session = session
        self._index_code = index_code
        self._embedding_port = embedding_port
        self._repository = RagCandidateIndexRepository(session)

    async def hydrate_evidence(
        self,
        request: CandidateSearchRequest,
    ) -> HydratedCandidateEvidence:
        """active READY Candidate Index에서 검색 및 hydration을 수행한다."""
        try:
            snapshot = await self._repository.get_verified_ready_index_snapshot(
                index_code=self._index_code,
                expected_index_version=request.index_version,
            )
        except CandidateIndexReadyVersionNotFoundError as exc:
            raise CandidateIndexHydrationError("NO_READY_INDEX") from exc
        except CandidateIndexVersionMismatchError as exc:
            raise CandidateIndexHydrationError("INDEX_VERSION_MISMATCH") from exc
        except CandidateIndexIntegrityCompromisedError as exc:
            raise CandidateIndexHydrationError("PERSISTENCE_INTEGRITY_COMPROMISED") from exc
        except CandidateIndexSourceBindingMismatchError as exc:
            raise CandidateIndexHydrationError("SOURCE_SNAPSHOT_MISMATCH") from exc

        version = snapshot.version

        try:
            raw_hits = await execute_candidate_search(
                self._session,
                version=version,
                request=request,
                embedding_port=self._embedding_port,
            )
        except CandidateIndexSearchError as exc:
            raise CandidateIndexHydrationError(
                exc.reason, stage=exc.stage if isinstance(exc.stage, CandidateStage) else None
            ) from exc

        products_map = await self._hydrate_products_map(raw_hits)

        product_hits: list[CandidateHit] = []
        for hit in raw_hits:
            product_key = (hit.product_source_snapshot_id, hit.identity_code_system, hit.identity_canonical_code)
            product_snapshot = products_map.get(product_key)
            if product_snapshot is None:
                raise CandidateIndexHydrationError("PRODUCT_ROW_MISSING_OR_AMBIGUOUS")

            product_hits.append(
                CandidateHit(
                    identity=product_snapshot.identity,
                    product=product_snapshot,
                    stage=hit.stage,
                    rank=hit.rank,
                    stage_score=hit.stage_score,
                    index_version=hit.index_version,
                    member_key=hit.member_key,
                    catalog_version=hit.catalog_version,
                    source_snapshot_id=hit.entry_source_snapshot_id,
                    normalization_version=hit.normalization_version,
                    embedding_model_version=hit.embedding_model_version,
                )
            )

        receipt = CandidateProvenanceReceipt(
            index_version=version.index_version,
            catalog_version=version.catalog_version,
            catalog_manifest_hash=version.catalog_manifest_hash,
            source_refs=tuple(
                CandidateSourceRef(
                    snapshot_id=ref.snapshot_id,
                    source_version=ref.source_version,
                )
                for ref in snapshot.source_refs
            ),
            normalization_version=version.normalization_version,
            embedding_model_version=version.embedding_model_version
            if version.build_mode is RagCandidateIndexBuildMode.HYBRID
            else None,
            index_mode=CandidateIndexMode.HYBRID
            if version.build_mode is RagCandidateIndexBuildMode.HYBRID
            else CandidateIndexMode.LEXICAL_ONLY,
        )

        return HydratedCandidateEvidence(
            provenance=receipt,
            product_hits=tuple(product_hits),
            ingredient_hits=(),
        )

    async def _hydrate_products_map(
        self,
        hits: tuple[CandidateSearchRawHit, ...],
    ) -> dict[tuple[str, str, str], ProductSnapshot]:
        if not hits:
            return {}

        distinct_keys = {
            (UUID(hit.product_source_snapshot_id), hit.identity_code_system, hit.identity_canonical_code)
            for hit in hits
        }

        products_map: dict[tuple[str, str, str], ProductSnapshot] = {}
        for source_snapshot_id, code_system, canonical_code in distinct_keys:
            stmt = select(RagMedicationProduct).where(
                RagMedicationProduct.source_snapshot_id == source_snapshot_id,
                RagMedicationProduct.code_system == code_system,
                RagMedicationProduct.canonical_code == canonical_code,
            )
            results = list((await self._session.execute(stmt)).scalars().all())
            if len(results) != 1:
                raise CandidateIndexHydrationError("PRODUCT_ROW_MISSING_OR_AMBIGUOUS")

            prod = results[0]
            matching_hits = [
                h
                for h in hits
                if h.product_source_snapshot_id == str(source_snapshot_id)
                and h.identity_code_system == code_system
                and h.identity_canonical_code == canonical_code
            ]
            for hit in matching_hits:
                if (
                    prod.product_name != hit.product_name
                    or prod.strength_text != hit.strength_text
                    or prod.dosage_form != hit.dosage_form
                    or prod.manufacturer_name != hit.manufacturer_name
                ):
                    raise CandidateIndexHydrationError("PRODUCT_FIELD_MISMATCH")

            try:
                status = ProductStatus(prod.product_status)
            except (ValueError, KeyError) as err:
                raise CandidateIndexHydrationError("PRODUCT_STATUS_UNKNOWN") from err

            products_map[(str(source_snapshot_id), code_system, canonical_code)] = ProductSnapshot(
                identity=OfficialIdentity(
                    entity_type=OfficialEntityType.PRODUCT,
                    code_system=code_system,
                    canonical_code=canonical_code,
                ),
                product_name=prod.product_name,
                strength_text=prod.strength_text,
                dosage_form=prod.dosage_form,
                manufacturer_name=prod.manufacturer_name,
                status=status,
            )

        return products_map
