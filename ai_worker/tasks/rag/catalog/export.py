"""검증된 Catalog를 Candidate Index용 결정적 산출물로 변환합니다."""

import dataclasses
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ai_worker.tasks.rag.catalog.build import CatalogMembers
from ai_worker.tasks.rag.catalog.normalize import CATALOG_NORMALIZATION_VERSION
from ai_worker.tasks.rag.catalog.types import (
    CandidateAliasReviewStatus,
    CandidateCatalogCounts,
    CandidateCatalogExport,
    CandidateCatalogSourceRef,
    CandidateEntityType,
    CandidateRecordStatus,
    CatalogFreshnessStatus,
    CatalogVerificationStatus,
)
from ai_worker.tasks.rag.catalog.validate import CatalogValidationReport, validate_catalog_members

CATALOG_SCHEMA_VERSION = "medication-catalog-v1"


class CatalogExportError(ValueError):
    def __init__(self, code: str, paths: tuple[str, ...]) -> None:
        self.code = code
        self.paths = paths
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class CatalogExportArtifacts:
    catalog: CandidateCatalogExport
    manifest_json: bytes
    catalog_jsonl: bytes
    export_checksum: str


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _text_sort_key(value: str) -> bytes:
    return value.encode("utf-8")


def _sorted_source_refs(
    source_refs: tuple[CandidateCatalogSourceRef, ...],
) -> tuple[CandidateCatalogSourceRef, ...]:
    return tuple(
        sorted(
            source_refs,
            key=lambda item: (
                _text_sort_key(item.snapshot_id),
                _text_sort_key(item.source_version),
            ),
        )
    )


def _validate_source_refs(
    members: CatalogMembers,
    source_refs: tuple[CandidateCatalogSourceRef, ...],
) -> tuple[CandidateCatalogSourceRef, ...]:
    if not source_refs:
        raise CatalogExportError("CATALOG_SOURCE_REQUIRED", ("source_refs",))

    ordered = _sorted_source_refs(source_refs)
    pairs = {(item.snapshot_id, item.source_version) for item in ordered}
    snapshot_versions = {item.snapshot_id: item.source_version for item in ordered}
    if len(pairs) != len(ordered) or len(snapshot_versions) != len(ordered):
        raise CatalogExportError("CATALOG_SOURCE_BINDING_INVALID", ("source_refs",))
    if any(not item.snapshot_id.strip() or not item.source_version.strip() for item in ordered):
        raise CatalogExportError("CATALOG_SOURCE_BINDING_INVALID", ("source_refs",))

    member_snapshot_ids = {
        *(item.source_snapshot_id for item in members.products),
        *(item.source_snapshot_id for item in members.ingredients),
        *(item.source_snapshot_id for item in members.components),
        *(item.source_snapshot_id for item in members.aliases),
        *(item.source_snapshot_id for item in members.search_entries),
    }
    if not member_snapshot_ids.issubset(snapshot_versions):
        raise CatalogExportError("CATALOG_SOURCE_BINDING_INVALID", ("source_refs",))
    return ordered


def _record_lines(members: CatalogMembers) -> tuple[dict[str, object], ...]:
    records: list[tuple[tuple[bytes, ...], dict[str, object]]] = []

    for product in members.products:
        records.append(
            (
                (b"PRODUCT", _text_sort_key(product.product_ref)),
                {"record_type": "PRODUCT", **dataclasses.asdict(product)},
            )
        )
    for ingredient in members.ingredients:
        records.append(
            (
                (b"INGREDIENT", _text_sort_key(ingredient.ingredient_ref)),
                {"record_type": "INGREDIENT", **dataclasses.asdict(ingredient)},
            )
        )
    for component in members.components:
        records.append(
            (
                (
                    b"COMPONENT",
                    _text_sort_key(component.product_ref),
                    f"{component.component_order:020d}".encode("ascii"),
                    _text_sort_key(component.component_ref),
                ),
                {"record_type": "COMPONENT", **dataclasses.asdict(component)},
            )
        )
    for alias in members.aliases:
        records.append(
            (
                (b"ALIAS", _text_sort_key(alias.alias_ref)),
                {"record_type": "ALIAS", **dataclasses.asdict(alias)},
            )
        )
    for entry in members.search_entries:
        records.append(
            (
                (b"SEARCH_ENTRY", _text_sort_key(entry.entry_ref)),
                {"record_type": "SEARCH_ENTRY", **dataclasses.asdict(entry)},
            )
        )

    records.sort(key=lambda item: item[0])
    return tuple(record for _, record in records)


def _catalog_jsonl(members: CatalogMembers) -> bytes:
    records = _record_lines(members)
    if not records:
        return b""
    return b"\n".join(_canonical_json_bytes(record) for record in records) + b"\n"


def _excluded_aliases(members: CatalogMembers) -> tuple[dict[str, object], ...]:
    active_products = {
        product.identity for product in members.products if product.status is CandidateRecordStatus.ACTIVE
    }
    counts: dict[tuple[str, str], int] = {}
    for alias in members.aliases:
        if alias.identity.entity_type is CandidateEntityType.INGREDIENT:
            reason = "INGREDIENT_ALIAS_NOT_PRODUCT_CANDIDATE"
        elif alias.review_status is not CandidateAliasReviewStatus.APPROVED:
            reason = "ALIAS_NOT_APPROVED"
        elif alias.status is not CandidateRecordStatus.ACTIVE:
            reason = "ALIAS_INACTIVE"
        elif not alias.is_effective:
            reason = "ALIAS_NOT_EFFECTIVE"
        elif alias.identity not in active_products:
            reason = "TARGET_PRODUCT_INACTIVE"
        else:
            continue
        key = (alias.alias_source, reason)
        counts[key] = counts.get(key, 0) + 1
    return tuple(
        {
            "alias_source": alias_source,
            "reason_code": reason,
            "count": count,
        }
        for (alias_source, reason), count in sorted(counts.items())
    )


def create_catalog_export(
    *,
    catalog_version: str,
    source_refs: tuple[CandidateCatalogSourceRef, ...],
    members: CatalogMembers,
    validation: CatalogValidationReport | None = None,
) -> CatalogExportArtifacts:
    if not catalog_version.strip():
        raise CatalogExportError("CATALOG_VERSION_INVALID", ("catalog_version",))

    report = validation or validate_catalog_members(members)
    if not report.is_valid:
        raise CatalogExportError(
            "CATALOG_VALIDATION_FAILED",
            tuple(failure.reason.value for failure in report.failures),
        )

    ordered_source_refs = _validate_source_refs(members, source_refs)
    catalog_jsonl = _catalog_jsonl(members)
    export_checksum = _sha256(catalog_jsonl)
    counts = CandidateCatalogCounts(
        product_count=len(members.products),
        ingredient_count=len(members.ingredients),
        component_count=len(members.components),
        alias_count=len(members.aliases),
        search_entry_count=len(members.search_entries),
    )
    code_systems = tuple(
        sorted(
            {
                *(item.identity.code_system for item in members.products),
                *(item.identity.code_system for item in members.ingredients),
            },
            key=_text_sort_key,
        )
    )
    manifest_payload = {
        "catalog_version": catalog_version,
        "source_refs": [dataclasses.asdict(item) for item in ordered_source_refs],
        "normalization_version": CATALOG_NORMALIZATION_VERSION,
        "schema_version": CATALOG_SCHEMA_VERSION,
        "declared_counts": dataclasses.asdict(counts),
        "export_checksum": export_checksum,
        "duplicate_identity_count": report.duplicate_identity_count,
        "orphan_count": report.orphan_count,
        "conflict_count": report.conflict_count,
        "validation_decision": "PASSED",
        "official_identity_code_systems": code_systems,
        "excluded_aliases": _excluded_aliases(members),
    }
    manifest_hash = _sha256(_canonical_json_bytes(manifest_payload))
    manifest = {**manifest_payload, "catalog_manifest_hash": manifest_hash}
    catalog = CandidateCatalogExport(
        catalog_version=catalog_version,
        catalog_manifest_hash=manifest_hash,
        source_refs=ordered_source_refs,
        schema_version=CATALOG_SCHEMA_VERSION,
        normalization_version=CATALOG_NORMALIZATION_VERSION,
        verification_status=CatalogVerificationStatus.APPROVED,
        freshness_status=CatalogFreshnessStatus.CURRENT,
        is_complete=True,
        products=tuple(sorted(members.products, key=lambda item: _text_sort_key(item.product_ref))),
        ingredients=tuple(sorted(members.ingredients, key=lambda item: _text_sort_key(item.ingredient_ref))),
        components=tuple(
            sorted(
                members.components,
                key=lambda item: (
                    _text_sort_key(item.product_ref),
                    item.component_order,
                    _text_sort_key(item.component_ref),
                ),
            )
        ),
        aliases=tuple(sorted(members.aliases, key=lambda item: _text_sort_key(item.alias_ref))),
        search_entries=tuple(sorted(members.search_entries, key=lambda item: _text_sort_key(item.entry_ref))),
        declared_counts=counts,
        duplicate_identity_count=0,
        orphan_count=0,
        conflict_count=0,
    )
    return CatalogExportArtifacts(
        catalog=catalog,
        manifest_json=_canonical_json_bytes(manifest) + b"\n",
        catalog_jsonl=catalog_jsonl,
        export_checksum=export_checksum,
    )


def write_catalog_export(*, artifacts: CatalogExportArtifacts, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_bytes(artifacts.manifest_json)
    (directory / "catalog.jsonl").write_bytes(artifacts.catalog_jsonl)
