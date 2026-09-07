import hashlib
import json
from pathlib import Path

import pytest

from ai_worker.tasks.rag.catalog import (
    CandidateAliasReviewStatus,
    CandidateCatalogSourceRef,
    CandidateEntityType,
    CandidateRecordStatus,
    CatalogAliasInput,
    CatalogExportError,
    CatalogProductInput,
    build_catalog_members,
    create_catalog_export,
    write_catalog_export,
)


def _product(code: str) -> CatalogProductInput:
    return CatalogProductInput(
        source_snapshot_id="snapshot-001",
        source_record_key=f"record-{code}",
        code_system="MFDS_ITEM_SEQ",
        canonical_code=code,
        product_name=f"합성 제품 {code}",
        product_status=CandidateRecordStatus.ACTIVE,
    )


def _alias(code: str, alias_ref: str, text: str) -> CatalogAliasInput:
    return CatalogAliasInput(
        source_snapshot_id="snapshot-001",
        target_source_snapshot_id="snapshot-001",
        source_alias_ref=alias_ref,
        target_type=CandidateEntityType.PRODUCT,
        target_code_system="MFDS_ITEM_SEQ",
        target_canonical_code=code,
        alias_source="SYNTHETIC_REVIEWED",
        alias_text=text,
        review_status=CandidateAliasReviewStatus.APPROVED,
        status=CandidateRecordStatus.ACTIVE,
        is_effective=True,
    )


def _export(*, reversed_input: bool = False):
    products = (_product("P-001"), _product("P-002"))
    aliases = (
        _alias("P-001", "alias-001", "합성 첫 별칭"),
        _alias("P-002", "alias-002", "합성 둘 별칭"),
    )
    if reversed_input:
        products = tuple(reversed(products))
        aliases = tuple(reversed(aliases))
    members = build_catalog_members(products=products, components=(), aliases=aliases)
    return create_catalog_export(
        catalog_version="synthetic-catalog-v1",
        source_refs=(CandidateCatalogSourceRef("snapshot-001", "2026-09-08"),),
        members=members,
    )


def test_input_order_does_not_change_catalog_or_export_hash() -> None:
    first = _export()
    second = _export(reversed_input=True)

    assert first.catalog.catalog_manifest_hash == second.catalog.catalog_manifest_hash
    assert first.export_checksum == second.export_checksum
    assert first.catalog_jsonl == second.catalog_jsonl


def test_manifest_binds_counts_sources_and_export_checksum() -> None:
    artifacts = _export()
    manifest = json.loads(artifacts.manifest_json)

    assert manifest["validation_decision"] == "PASSED"
    assert manifest["declared_counts"] == {
        "alias_count": 2,
        "component_count": 0,
        "ingredient_count": 0,
        "product_count": 2,
        "search_entry_count": 4,
    }
    assert manifest["export_checksum"] == hashlib.sha256(artifacts.catalog_jsonl).hexdigest()
    assert manifest["catalog_manifest_hash"] == artifacts.catalog.catalog_manifest_hash
    assert artifacts.catalog.catalog_manifest_hash == (
        "d916588366a092479500bd4d68debfb58be6322b31cd07fcb29b0c04052b6b06"
    )
    assert artifacts.export_checksum == ("c23811e1cf735c3c2f82b270f1447beb417798aa9f5fd2e847e269e7be0f3cfe")


def test_invalid_catalog_does_not_create_export() -> None:
    members = build_catalog_members(
        products=(_product("P-001"), _product("P-002")),
        components=(),
        aliases=(
            _alias("P-001", "alias-001", "충돌 별칭"),
            _alias("P-002", "alias-002", "충돌 별칭"),
        ),
    )

    with pytest.raises(CatalogExportError) as captured:
        create_catalog_export(
            catalog_version="synthetic-catalog-v1",
            source_refs=(CandidateCatalogSourceRef("snapshot-001", "2026-09-08"),),
            members=members,
        )

    assert captured.value.code == "CATALOG_VALIDATION_FAILED"


def test_writes_only_manifest_and_canonical_catalog_jsonl(tmp_path) -> None:
    artifacts = _export()

    write_catalog_export(artifacts=artifacts, directory=tmp_path)

    assert (tmp_path / "manifest.json").read_bytes() == artifacts.manifest_json
    assert (tmp_path / "catalog.jsonl").read_bytes() == artifacts.catalog_jsonl
    assert sorted(path.name for path in tmp_path.iterdir()) == ["catalog.jsonl", "manifest.json"]


def test_checked_in_handoff_artifacts_match_golden_export() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    evidence_root = repository_root / "docs" / "validation" / "rag" / "catalog" / "synthetic-catalog-v1"
    artifacts = _export()

    assert (evidence_root / "manifest.json").read_bytes() == artifacts.manifest_json
    assert (evidence_root / "catalog.jsonl").read_bytes() == artifacts.catalog_jsonl
