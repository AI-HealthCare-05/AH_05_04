"""Catalog build 검증·저장·export를 하나의 작업 경계로 조립합니다."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ai_worker.tasks.rag.catalog.build import (
    CatalogAliasInput,
    CatalogComponentInput,
    CatalogMembers,
    CatalogProductInput,
    build_catalog_members,
)
from ai_worker.tasks.rag.catalog.export import CatalogExportArtifacts, create_catalog_export
from ai_worker.tasks.rag.catalog.types import CandidateCatalogSourceRef
from ai_worker.tasks.rag.catalog.validate import CatalogValidationReport, validate_catalog_members


class CatalogBuildDecision(StrEnum):
    ACTIVATION_CANDIDATE = "ACTIVATION_CANDIDATE"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class CatalogBuildRequest:
    catalog_version: str
    source_refs: tuple[CandidateCatalogSourceRef, ...]
    products: tuple[CatalogProductInput, ...]
    components: tuple[CatalogComponentInput, ...]
    aliases: tuple[CatalogAliasInput, ...]


@dataclass(frozen=True, slots=True)
class CatalogBuildResult:
    decision: CatalogBuildDecision
    validation: CatalogValidationReport
    export: CatalogExportArtifacts | None


class CatalogBuildRepository(Protocol):
    """구성원과 manifest candidate를 현재 transaction에 원자적으로 저장합니다."""

    async def save_build(
        self,
        *,
        members: CatalogMembers,
        artifacts: CatalogExportArtifacts,
    ) -> None: ...


async def build_catalog_candidate(
    *,
    request: CatalogBuildRequest,
    repository: CatalogBuildRepository,
) -> CatalogBuildResult:
    members = build_catalog_members(
        products=request.products,
        components=request.components,
        aliases=request.aliases,
    )
    validation = validate_catalog_members(members)
    if not validation.is_valid:
        return CatalogBuildResult(
            decision=CatalogBuildDecision.REJECTED,
            validation=validation,
            export=None,
        )

    artifacts = create_catalog_export(
        catalog_version=request.catalog_version,
        source_refs=request.source_refs,
        members=members,
        validation=validation,
    )
    await repository.save_build(members=members, artifacts=artifacts)
    return CatalogBuildResult(
        decision=CatalogBuildDecision.ACTIVATION_CANDIDATE,
        validation=validation,
        export=artifacts,
    )
