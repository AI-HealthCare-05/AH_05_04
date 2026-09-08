from dataclasses import dataclass, field

import pytest

from ai_worker.tasks.rag.catalog import (
    CandidateAliasReviewStatus,
    CandidateCatalogSourceRef,
    CandidateEntityType,
    CandidateRecordStatus,
    CatalogAliasInput,
    CatalogBuildDecision,
    CatalogBuildRequest,
    CatalogExportArtifacts,
    CatalogMembers,
    CatalogProductInput,
    build_catalog_candidate,
)


@dataclass
class RecordingRepository:
    saved: list[tuple[CatalogMembers, CatalogExportArtifacts]] = field(default_factory=list)
    fail: bool = False

    async def save_build(
        self,
        *,
        members: CatalogMembers,
        artifacts: CatalogExportArtifacts,
    ) -> None:
        if self.fail:
            raise RuntimeError("synthetic commit failure")
        self.saved.append((members, artifacts))


def _request(*, conflict: bool = False) -> CatalogBuildRequest:
    products = tuple(
        CatalogProductInput(
            source_snapshot_id="snapshot-001",
            source_record_key=f"record-{code}",
            code_system="MFDS_ITEM_SEQ",
            canonical_code=code,
            product_name=f"합성 제품 {code}",
            product_status=CandidateRecordStatus.ACTIVE,
        )
        for code in ("P-001", "P-002")
    )
    aliases = tuple(
        CatalogAliasInput(
            source_snapshot_id="snapshot-001",
            target_source_snapshot_id="snapshot-001",
            source_alias_ref=f"alias-{index}",
            target_type=CandidateEntityType.PRODUCT,
            target_code_system="MFDS_ITEM_SEQ",
            target_canonical_code=code,
            alias_source="SYNTHETIC_REVIEWED",
            alias_text="충돌 별칭" if conflict else f"합성 별칭 {index}",
            review_status=CandidateAliasReviewStatus.APPROVED,
            status=CandidateRecordStatus.ACTIVE,
            is_effective=True,
        )
        for index, code in enumerate(("P-001", "P-002"), start=1)
    )
    return CatalogBuildRequest(
        catalog_version="synthetic-catalog-v1",
        source_refs=(CandidateCatalogSourceRef("snapshot-001", "2026-09-08"),),
        products=products,
        components=(),
        aliases=aliases,
    )


@pytest.mark.asyncio
async def test_valid_build_is_saved_once_as_activation_candidate() -> None:
    repository = RecordingRepository()

    result = await build_catalog_candidate(request=_request(), repository=repository)

    assert result.decision is CatalogBuildDecision.ACTIVATION_CANDIDATE
    assert result.export is not None
    assert len(repository.saved) == 1


@pytest.mark.asyncio
async def test_invalid_build_is_inactive_and_never_saved_or_exported() -> None:
    repository = RecordingRepository()

    result = await build_catalog_candidate(request=_request(conflict=True), repository=repository)

    assert result.decision is CatalogBuildDecision.REJECTED
    assert result.export is None
    assert repository.saved == []


@pytest.mark.asyncio
async def test_repository_failure_propagates_without_success_result() -> None:
    repository = RecordingRepository(fail=True)

    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        await build_catalog_candidate(request=_request(), repository=repository)

    assert repository.saved == []
