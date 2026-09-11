"""후속 DB adapter에 넘길 v2 저장 자료를 검증합니다. SQL·실행 FK는 만들지 않습니다."""

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Literal

from ai_worker.tasks.rag.catalog.build import CatalogMembers, _alias_entry, _product_entry
from ai_worker.tasks.rag.catalog.export import (
    CATALOG_MANIFEST_SPEC_VERSION,
    CATALOG_SCHEMA_VERSION,
    CatalogExportArtifacts,
    _canonical_json_bytes,
    _record_lines,
    verify_catalog_export,
)
from ai_worker.tasks.rag.catalog.normalize import CATALOG_NORMALIZATION_VERSION, require_official_identity_text
from ai_worker.tasks.rag.catalog.types import (
    CandidateAliasReviewStatus,
    CandidateCatalogSourceRef,
    CandidateEntityType,
    CandidateRecordStatus,
    CatalogSearchEntry,
    ProductIdentity,
    is_p0_code_system,
)
from ai_worker.tasks.rag.catalog.validate import validate_catalog_members

MemberKind = Literal["PRODUCT", "INGREDIENT", "COMPONENT", "ALIAS", "SEARCH_ENTRY"]
HashKind = Literal["EXPORT_CHECKSUM", "CATALOG_ENVELOPE"]


class CatalogStoragePreparationError(ValueError):
    """입력 원문을 포함하지 않는 내부 저장 준비 오류입니다."""

    def __init__(self) -> None:
        super().__init__("Catalog storage preparation failed")


@dataclass(frozen=True, slots=True)
class CatalogMemberLink:
    kind: MemberKind
    member_ref: str


@dataclass(frozen=True, slots=True)
class CatalogStorageRow:
    """member_ref는 v2 참조이며 아직 Publication PK나 실행 식별자가 아닙니다."""

    kind: MemberKind
    member_ref: str
    source_ref: CandidateCatalogSourceRef
    identity: ProductIdentity | None
    links: tuple[CatalogMemberLink, ...]
    canonical_record: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class CatalogHashMaterial:
    kind: HashKind
    schema_version: str
    contract_spec_version: str
    digest: str
    target: Literal["catalog_jsonl", "envelope_payload"]
    canonical_bytes: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class CatalogStoragePlan:
    """DB 비의존 저장 자료. 생성 성공은 commit·승인·READY를 의미하지 않습니다."""

    catalog_version: str
    identities: tuple[ProductIdentity, ...]
    source_refs: tuple[CandidateCatalogSourceRef, ...]
    rows: tuple[CatalogStorageRow, ...] = field(repr=False)
    hashes: tuple[CatalogHashMaterial, ...] = field(repr=False)
    manifest_json: bytes = field(repr=False)


def _require(condition: bool) -> None:
    if not condition:
        raise CatalogStoragePreparationError()


def _identity_key(identity: ProductIdentity) -> tuple[str, str, str]:
    return identity.entity_type.value, identity.code_system, identity.canonical_code


def _validate_identity(identity: ProductIdentity, expected: CandidateEntityType) -> None:
    _require(identity.entity_type is expected and is_p0_code_system(expected, identity.code_system))
    for value in (identity.code_system, identity.canonical_code):
        _require(require_official_identity_text(value, field_name="identity") == value)


def _member_links(members: CatalogMembers) -> dict[tuple[MemberKind, str], tuple[CatalogMemberLink, ...]]:
    products = {p.product_ref: p for p in members.products}
    ingredients = {i.ingredient_ref: i for i in members.ingredients}
    aliases = {a.alias_ref: a for a in members.aliases}
    _require(len(products) == len(members.products))
    _require(len(ingredients) == len(members.ingredients))
    _require(len(aliases) == len(members.aliases))
    product_by_identity = {p.identity: p for p in members.products}
    ingredient_identities = {i.identity for i in members.ingredients}
    links: dict[tuple[MemberKind, str], tuple[CatalogMemberLink, ...]] = {}
    expected_entries = {_product_entry(p) for p in members.products if p.status is CandidateRecordStatus.ACTIVE}
    alias_entries: dict[tuple[str, str], CatalogSearchEntry] = {}
    for p in members.products:
        _validate_identity(p.identity, CandidateEntityType.PRODUCT)
    for i in members.ingredients:
        _validate_identity(i.identity, CandidateEntityType.INGREDIENT)
    for a in members.aliases:
        _validate_identity(a.identity, a.identity.entity_type)
        target = product_by_identity.get(a.identity)
        _require(target is not None or a.identity in ingredient_identities)
        _require(type(a.is_effective) is bool)
        if (
            target is not None
            and target.status is CandidateRecordStatus.ACTIVE
            and a.review_status is CandidateAliasReviewStatus.APPROVED
            and a.status is CandidateRecordStatus.ACTIVE
            and a.is_effective
        ):
            entry = _alias_entry(a, product=target)
            key = (entry.product_ref, entry.normalized_text)
            alias_entries[key] = min(entry, alias_entries.get(key, entry), key=lambda x: x.alias_ref or "")
    expected_entries.update(alias_entries.values())
    _require(len(members.search_entries) == len(expected_entries))
    _require(set(members.search_entries) == expected_entries)
    for c in members.components:
        p, i = products[c.product_ref], ingredients[c.ingredient_ref]
        _require(c.source_snapshot_id == p.source_snapshot_id == i.source_snapshot_id)
        _require(type(c.component_order) is int and c.component_order > 0)
        links[("COMPONENT", c.component_ref)] = (
            CatalogMemberLink("PRODUCT", c.product_ref),
            CatalogMemberLink("INGREDIENT", c.ingredient_ref),
        )
    for entry in members.search_entries:
        entry_links = [CatalogMemberLink("PRODUCT", entry.product_ref)]
        if entry.alias_ref is not None:
            entry_links.append(CatalogMemberLink("ALIAS", entry.alias_ref))
        links[("SEARCH_ENTRY", entry.entry_ref)] = tuple(entry_links)
    return links


def prepare_catalog_storage(*, members: CatalogMembers, artifacts: CatalogExportArtifacts) -> CatalogStoragePlan:
    """서로 다른 구성원/export의 혼합과 출처 손실을 SQL 실행 전에 차단합니다.

    승인 verifier의 권한 검증·DB FK·동시성 제어는 대체하지 않습니다.
    미확정 D-02 실행 ID나 Set 상태를 생성하지 않습니다.
    """
    try:
        return _prepare(members=members, artifacts=artifacts)
    except (ValueError, KeyError, TypeError, AttributeError):
        raise CatalogStoragePreparationError() from None


def _prepare(*, members: CatalogMembers, artifacts: CatalogExportArtifacts) -> CatalogStoragePlan:
    verify_catalog_export(artifacts)
    # save_build의 두 인자가 동일 구성원을 표현하는지 기존 v2 직렬화로 확인합니다.
    verify_catalog_export(
        replace(
            artifacts,
            catalog=replace(
                artifacts.catalog,
                products=members.products,
                ingredients=members.ingredients,
                components=members.components,
                aliases=members.aliases,
                search_entries=members.search_entries,
            ),
        )
    )
    _require(validate_catalog_members(members).is_valid)
    links = _member_links(members)
    catalog = artifacts.catalog
    _require(catalog.schema_version == CATALOG_SCHEMA_VERSION)
    _require(catalog.normalization_version == CATALOG_NORMALIZATION_VERSION)
    counts = catalog.declared_counts
    actual_counts = (
        (counts.product_count, len(members.products)),
        (counts.ingredient_count, len(members.ingredients)),
        (counts.component_count, len(members.components)),
        (counts.alias_count, len(members.aliases)),
        (counts.search_entry_count, len(members.search_entries)),
    )
    _require(all(type(declared) is int and declared == actual for declared, actual in actual_counts))
    _require(
        all(
            type(count) is int and count == 0
            for count in (
                catalog.duplicate_identity_count,
                catalog.orphan_count,
                catalog.conflict_count,
            )
        )
    )
    source_refs = {ref.snapshot_id: ref for ref in catalog.source_refs}
    _require(bool(source_refs) and len(source_refs) == len(catalog.source_refs))
    for ref in source_refs.values():
        for value in (ref.snapshot_id, ref.source_version):
            _require(require_official_identity_text(value, field_name="source_ref") == value)
    identities = tuple(
        sorted({p.identity for p in members.products} | {i.identity for i in members.ingredients}, key=_identity_key)
    )
    identity_by_member: dict[tuple[MemberKind, str], ProductIdentity] = {}
    identity_by_member.update({("PRODUCT", x.product_ref): x.identity for x in members.products})
    identity_by_member.update({("INGREDIENT", x.ingredient_ref): x.identity for x in members.ingredients})
    identity_by_member.update({("ALIAS", x.alias_ref): x.identity for x in members.aliases})
    identity_by_member.update({("SEARCH_ENTRY", x.entry_ref): x.identity for x in members.search_entries})
    ref_fields: dict[str, tuple[MemberKind, str]] = {
        "PRODUCT": ("PRODUCT", "product_ref"),
        "INGREDIENT": ("INGREDIENT", "ingredient_ref"),
        "COMPONENT": ("COMPONENT", "component_ref"),
        "ALIAS": ("ALIAS", "alias_ref"),
        "SEARCH_ENTRY": ("SEARCH_ENTRY", "entry_ref"),
    }
    rows: list[CatalogStorageRow] = []
    seen: set[tuple[MemberKind, str]] = set()
    for record in _record_lines(members):
        kind, ref_field = ref_fields[str(record["record_type"])]
        member_ref, snapshot = str(record[ref_field]), str(record["source_snapshot_id"])
        key = (kind, member_ref)
        _require(key not in seen)
        seen.add(key)
        rows.append(
            CatalogStorageRow(
                kind,
                member_ref,
                source_refs[snapshot],
                identity_by_member.get(key),
                links.get(key, ()),
                _canonical_json_bytes(record),
            )
        )
    payload = json.loads(artifacts.manifest_json)
    del payload["catalog_manifest_hash"]
    envelope_bytes = _canonical_json_bytes(payload)
    hashes = (
        CatalogHashMaterial(
            "EXPORT_CHECKSUM",
            catalog.schema_version,
            CATALOG_MANIFEST_SPEC_VERSION,
            artifacts.export_checksum,
            "catalog_jsonl",
            artifacts.catalog_jsonl,
        ),
        CatalogHashMaterial(
            "CATALOG_ENVELOPE",
            catalog.schema_version,
            CATALOG_MANIFEST_SPEC_VERSION,
            catalog.catalog_manifest_hash,
            "envelope_payload",
            envelope_bytes,
        ),
    )
    _require(all(hashlib.sha256(h.canonical_bytes).hexdigest() == h.digest for h in hashes))
    return CatalogStoragePlan(
        catalog.catalog_version, identities, catalog.source_refs, tuple(rows), hashes, artifacts.manifest_json
    )
