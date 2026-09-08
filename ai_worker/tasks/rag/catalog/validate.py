"""Catalog 저장과 export 전에 구성원 무결성을 검증합니다."""

from dataclasses import dataclass
from enum import StrEnum

from ai_worker.tasks.rag.catalog.build import CatalogMembers
from ai_worker.tasks.rag.catalog.types import (
    CandidateAliasReviewStatus,
    CandidateEntityType,
    CandidateRecordStatus,
    ProductIdentity,
)


class CatalogValidationFailureReason(StrEnum):
    DUPLICATE_PRODUCT_IDENTITY = "DUPLICATE_PRODUCT_IDENTITY"
    DUPLICATE_INGREDIENT_IDENTITY = "DUPLICATE_INGREDIENT_IDENTITY"
    REFERENTIAL_INTEGRITY_INVALID = "REFERENTIAL_INTEGRITY_INVALID"
    ALIAS_CONFLICT = "ALIAS_CONFLICT"
    MEMBER_CONFLICT = "MEMBER_CONFLICT"


@dataclass(frozen=True, slots=True)
class CatalogValidationFailure:
    reason: CatalogValidationFailureReason
    references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CatalogValidationReport:
    duplicate_identity_count: int
    orphan_count: int
    conflict_count: int
    failures: tuple[CatalogValidationFailure, ...]

    @property
    def is_valid(self) -> bool:
        return not self.failures


def _identity_sort_key(identity: ProductIdentity) -> tuple[str, str, str]:
    return (
        identity.entity_type.value,
        identity.code_system,
        identity.canonical_code,
    )


def _duplicate_product_failures(
    members: CatalogMembers,
) -> tuple[CatalogValidationFailure, ...]:
    references_by_identity: dict[ProductIdentity, list[str]] = {}

    for product in members.products:
        references_by_identity.setdefault(product.identity, []).append(
            product.product_ref,
        )

    return tuple(
        CatalogValidationFailure(
            reason=CatalogValidationFailureReason.DUPLICATE_PRODUCT_IDENTITY,
            references=tuple(sorted(references)),
        )
        for identity, references in sorted(
            references_by_identity.items(),
            key=lambda item: _identity_sort_key(item[0]),
        )
        if len(references) > 1
    )


def _duplicate_ingredient_failures(
    members: CatalogMembers,
) -> tuple[CatalogValidationFailure, ...]:
    references_by_identity: dict[ProductIdentity, list[str]] = {}

    for ingredient in members.ingredients:
        references_by_identity.setdefault(ingredient.identity, []).append(
            ingredient.ingredient_ref,
        )

    return tuple(
        CatalogValidationFailure(
            reason=CatalogValidationFailureReason.DUPLICATE_INGREDIENT_IDENTITY,
            references=tuple(sorted(references)),
        )
        for identity, references in sorted(
            references_by_identity.items(),
            key=lambda item: _identity_sort_key(item[0]),
        )
        if len(references) > 1
    )


def _orphan_component_failures(
    members: CatalogMembers,
) -> tuple[CatalogValidationFailure, ...]:
    product_refs = {product.product_ref for product in members.products}
    ingredient_refs = {ingredient.ingredient_ref for ingredient in members.ingredients}

    return tuple(
        CatalogValidationFailure(
            reason=CatalogValidationFailureReason.REFERENTIAL_INTEGRITY_INVALID,
            references=(component.component_ref,),
        )
        for component in members.components
        if (component.product_ref not in product_refs or component.ingredient_ref not in ingredient_refs)
    )


def _component_conflict_failures(
    members: CatalogMembers,
) -> tuple[CatalogValidationFailure, ...]:
    references_by_key: dict[tuple[str, str, str], list[str]] = {}
    for component in members.components:
        key = (
            component.product_ref,
            component.ingredient_ref,
            component.component_role.value,
        )
        references_by_key.setdefault(key, []).append(component.component_ref)

    return tuple(
        CatalogValidationFailure(
            reason=CatalogValidationFailureReason.MEMBER_CONFLICT,
            references=tuple(sorted(references)),
        )
        for key, references in sorted(references_by_key.items())
        if len(references) > 1
    )


def _duplicate_alias_failures(
    members: CatalogMembers,
) -> tuple[CatalogValidationFailure, ...]:
    references_by_key: dict[tuple[str, str], list[str]] = {}
    for alias in members.aliases:
        key = (alias.alias_ref, alias.normalized_alias)
        references_by_key.setdefault(key, []).append(alias.alias_ref)

    return tuple(
        CatalogValidationFailure(
            reason=CatalogValidationFailureReason.MEMBER_CONFLICT,
            references=tuple(sorted(references)),
        )
        for key, references in sorted(references_by_key.items())
        if len(references) > 1
    )


def _alias_conflict_failures(
    members: CatalogMembers,
) -> tuple[CatalogValidationFailure, ...]:
    active_product_identities = {
        product.identity for product in members.products if product.status is CandidateRecordStatus.ACTIVE
    }
    aliases_by_text: dict[
        str,
        dict[ProductIdentity, list[str]],
    ] = {}

    for alias in members.aliases:
        if (
            alias.identity.entity_type is CandidateEntityType.PRODUCT
            and alias.identity in active_product_identities
            and alias.review_status is CandidateAliasReviewStatus.APPROVED
            and alias.status is CandidateRecordStatus.ACTIVE
            and alias.is_effective
        ):
            targets = aliases_by_text.setdefault(alias.normalized_alias, {})
            targets.setdefault(alias.identity, []).append(alias.alias_ref)

    failures: list[CatalogValidationFailure] = []
    for normalized_alias in sorted(aliases_by_text, key=lambda value: value.encode("utf-8")):
        targets = aliases_by_text[normalized_alias]
        if len(targets) <= 1:
            continue

        references = tuple(sorted(alias_ref for alias_refs in targets.values() for alias_ref in alias_refs))
        failures.append(
            CatalogValidationFailure(
                reason=CatalogValidationFailureReason.ALIAS_CONFLICT,
                references=references,
            )
        )

    return tuple(failures)


def validate_catalog_members(
    members: CatalogMembers,
) -> CatalogValidationReport:
    product_failures = _duplicate_product_failures(members)
    ingredient_failures = _duplicate_ingredient_failures(members)
    orphan_failures = _orphan_component_failures(members)
    component_failures = _component_conflict_failures(members)
    duplicate_alias_failures = _duplicate_alias_failures(members)
    alias_failures = _alias_conflict_failures(members)

    return CatalogValidationReport(
        duplicate_identity_count=(len(product_failures) + len(ingredient_failures)),
        orphan_count=len(orphan_failures),
        conflict_count=(len(component_failures) + len(duplicate_alias_failures) + len(alias_failures)),
        failures=(
            *product_failures,
            *ingredient_failures,
            *orphan_failures,
            *component_failures,
            *duplicate_alias_failures,
            *alias_failures,
        ),
    )
