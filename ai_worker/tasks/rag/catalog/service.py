"""Catalog build 검증·저장·export를 하나의 작업 경계로 조립합니다."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ai_worker.tasks.rag.catalog.approval import CatalogApprovalVerifier
from ai_worker.tasks.rag.catalog.build import (
    CatalogAliasInput,
    CatalogComponentInput,
    CatalogIngredientInput,
    CatalogMappingError,
    CatalogMembers,
    CatalogProductInput,
    build_catalog_members,
)
from ai_worker.tasks.rag.catalog.export import CatalogExportArtifacts, create_catalog_export, verify_catalog_export
from ai_worker.tasks.rag.catalog.types import CandidateCatalogSourceRef
from ai_worker.tasks.rag.catalog.validate import (
    CatalogValidationFailure,
    CatalogValidationFailureReason,
    CatalogValidationReport,
    validate_catalog_members,
)


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
    ingredients: tuple[CatalogIngredientInput, ...] = ()


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


def _rejected(reason: CatalogValidationFailureReason, paths: tuple[str, ...]) -> CatalogBuildResult:
    orphan = reason is CatalogValidationFailureReason.REFERENTIAL_INTEGRITY_INVALID
    report = CatalogValidationReport(
        duplicate_identity_count=0,
        orphan_count=int(orphan),
        conflict_count=int(not orphan),
        failures=(CatalogValidationFailure(reason=reason, references=paths),),
    )
    return CatalogBuildResult(decision=CatalogBuildDecision.REJECTED, validation=report, export=None)


async def build_catalog_candidate(
    *,
    request: CatalogBuildRequest,
    repository: CatalogBuildRepository,
    approval_verifier: CatalogApprovalVerifier | None = None,
) -> CatalogBuildResult:
    try:
        members = build_catalog_members(
            products=request.products,
            ingredients=request.ingredients,
            components=request.components,
            aliases=request.aliases,
        )
    except CatalogMappingError as error:
        reason = (
            CatalogValidationFailureReason.REFERENTIAL_INTEGRITY_INVALID
            if error.code.endswith("_NOT_FOUND")
            else CatalogValidationFailureReason.MEMBER_CONFLICT
        )
        return _rejected(reason, error.paths)
    except ValueError:
        return _rejected(CatalogValidationFailureReason.MEMBER_CONFLICT, ("inputs",))
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
    if approval_verifier is not None:
        receipt = await approval_verifier.verify(
            catalog_version=request.catalog_version,
            export_checksum=artifacts.export_checksum,
            source_refs=artifacts.catalog.source_refs,
        )
        artifacts = create_catalog_export(
            catalog_version=request.catalog_version,
            source_refs=request.source_refs,
            members=members,
            validation=validation,
            approval_receipt=receipt,
        )
    verify_catalog_export(artifacts)
    await repository.save_build(members=members, artifacts=artifacts)
    return CatalogBuildResult(
        decision=CatalogBuildDecision.ACTIVATION_CANDIDATE,
        validation=validation,
        export=artifacts,
    )
