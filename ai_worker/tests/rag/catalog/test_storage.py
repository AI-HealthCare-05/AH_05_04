import json
from dataclasses import fields, replace

import pytest

from ai_worker.tasks.rag.catalog import (
    CandidateAliasReviewStatus,
    CandidateCatalogSourceRef,
    CandidateEntityType,
    CandidateEntryType,
    CandidateRecordStatus,
    CatalogIngredientInput,
    build_catalog_members,
    create_catalog_export,
)
from ai_worker.tasks.rag.catalog.build import CatalogMembers
from ai_worker.tasks.rag.catalog.storage import CatalogStoragePreparationError, prepare_catalog_storage
from ai_worker.tests.rag.catalog.test_build import _component_inputs, _ingredient_inputs, _product_inputs
from ai_worker.tests.rag.catalog.test_export import _alias, _product
from ai_worker.tests.rag.catalog.test_hash_contract_v2 import FIXTURE, approved_export


def members_from(artifacts):
    c = artifacts.catalog
    return CatalogMembers(c.products, c.ingredients, c.components, c.aliases, c.search_entries)


def prepare(members, refs=None):
    if refs is None:
        refs = (CandidateCatalogSourceRef("snapshot-001", "v1"),)
    artifacts = create_catalog_export(catalog_version="synthetic-storage-v2", members=members, source_refs=refs)
    return prepare_catalog_storage(members=members, artifacts=artifacts)


def record(row):
    return json.loads(row.canonical_record)


def test_golden_bytes_and_source_order_are_preserved():
    artifacts = approved_export()
    forward = prepare_catalog_storage(members=members_from(artifacts), artifacts=artifacts)
    reverse = approved_export(reverse=True)
    backward = prepare_catalog_storage(members=members_from(reverse), artifacts=reverse)
    assert forward == backward
    assert b"".join(row.canonical_record + b"\n" for row in forward.rows) == (FIXTURE / "catalog.jsonl").read_bytes()
    assert forward.manifest_json == (FIXTURE / "manifest.json").read_bytes()
    expected = json.loads((FIXTURE / "expected.json").read_bytes())
    assert {h.kind: h.digest for h in forward.hashes} == {
        "EXPORT_CHECKSUM": expected["export_checksum"],
        "CATALOG_ENVELOPE": expected["catalog_manifest_hash"],
    }
    assert forward.hashes[1].canonical_bytes == (FIXTURE / "envelope-payload.json").read_bytes()
    assert all(h.schema_version == "medication-catalog-v2" for h in forward.hashes)
    assert all(h.contract_spec_version == "catalog-manifest-envelope-v2" for h in forward.hashes)
    assert forward.hashes[0].target == "catalog_jsonl"
    assert forward.hashes[1].target == "envelope_payload"


def test_identity_stays_stable_across_snapshots_without_inventing_a_run():
    def build(snapshot):
        return prepare(
            build_catalog_members(
                products=(replace(_product("P-001"), source_snapshot_id=snapshot),), components=(), aliases=()
            ),
            (CandidateCatalogSourceRef(snapshot, "v1"),),
        )

    first, second = build("snapshot-001"), build("snapshot-002")
    assert first.identities == second.identities
    assert first.rows[0].member_ref != second.rows[0].member_ref
    for plan in (first, second):
        assert not any("run_id" in f.name for f in fields(plan))
        for row in plan.rows:
            assert "normalization_run_id" not in record(row)
            assert "ingestion_run_id" not in record(row)


def test_same_snapshot_changed_content_is_preserved_not_overwritten_or_used_as_execution_id():
    first = build_catalog_members(products=(_product("P-001"),), components=(), aliases=())
    second = build_catalog_members(
        products=(replace(_product("P-001"), product_name="다른 합성 표시값"),), components=(), aliases=()
    )
    old, new = prepare(first), prepare(second)
    assert old.identities == new.identities
    assert old.rows[0].member_ref == new.rows[0].member_ref
    assert old.rows[0].canonical_record != new.rows[0].canonical_record
    assert old.hashes != new.hashes


def test_same_name_distinct_ingredient_identities_are_preserved():
    ingredients = tuple(
        CatalogIngredientInput("snapshot-001", f"row-{code}", "MFDS_INGREDIENT_CODE", code, "같은 성분명")
        for code in ("I-001", "I-002")
    )
    plan = prepare(build_catalog_members(products=(), ingredients=ingredients, components=(), aliases=()))
    assert len(plan.identities) == 2
    assert len(plan.rows) == 2
    assert {record(row)["ingredient_name"] for row in plan.rows} == {"같은 성분명"}
    assert {row.identity.canonical_code for row in plan.rows} == {"I-001", "I-002"}


def test_cross_snapshot_alias_uses_stable_identity_and_entry_keeps_own_source():
    artifacts = approved_export()
    plan = prepare_catalog_storage(members=members_from(artifacts), artifacts=artifacts)
    alias = next(row for row in plan.rows if row.kind == "ALIAS")
    entry = next(row for row in plan.rows if row.kind == "SEARCH_ENTRY" and record(row)["alias_ref"])
    product = next(row for row in plan.rows if row.kind == "PRODUCT" and row.identity == alias.identity)
    assert alias.identity == entry.identity == product.identity
    assert alias.links == ()  # Alias의 대상은 Snapshot별 Product row가 아닌 Identity다.
    assert alias.source_ref.snapshot_id == entry.source_ref.snapshot_id == "snapshot-002"
    assert product.source_ref.snapshot_id == "snapshot-001"
    assert {(link.kind, link.member_ref) for link in entry.links} == {
        ("PRODUCT", product.member_ref),
        ("ALIAS", alias.member_ref),
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"review_status": CandidateAliasReviewStatus.PENDING},
        {"review_status": CandidateAliasReviewStatus.REJECTED},
        {"status": CandidateRecordStatus.INACTIVE},
        {"is_effective": False},
    ],
)
def test_ineligible_alias_is_preserved_without_a_search_entry(changes):
    alias = replace(_alias("P-001", "alias-1", "비적격 합성 별칭"), **changes)
    plan = prepare(build_catalog_members(products=(_product("P-001"),), aliases=(alias,), components=()))
    stored = next(row for row in plan.rows if row.kind == "ALIAS")
    assert record(stored)["review_status"] == alias.review_status
    assert record(stored)["status"] == alias.status
    assert record(stored)["is_effective"] == alias.is_effective
    assert len([row for row in plan.rows if row.kind == "SEARCH_ENTRY"]) == 1
    assert all(record(row)["alias_ref"] is None for row in plan.rows if row.kind == "SEARCH_ENTRY")


def test_repeated_alias_preserves_all_sources_but_one_search_entry():
    a = _alias("P-001", "alias-1", "반복 합성 별칭")
    b = replace(a, source_alias_ref="alias-2", source_snapshot_id="snapshot-002")
    plan = prepare(
        build_catalog_members(products=(_product("P-001"),), components=(), aliases=(b, a)),
        (CandidateCatalogSourceRef("snapshot-001", "v1"), CandidateCatalogSourceRef("snapshot-002", "v2")),
    )
    aliases = [row for row in plan.rows if row.kind == "ALIAS"]
    entries = [row for row in plan.rows if row.kind == "SEARCH_ENTRY" and record(row)["alias_ref"]]
    assert len(aliases) == 2 and len(entries) == 1
    assert record(entries[0])["alias_ref"] == min(row.member_ref for row in aliases)


def test_component_strength_strings_and_role_are_not_converted_to_db_numeric():
    members = build_catalog_members(
        products=_product_inputs(), ingredients=_ingredient_inputs(), components=_component_inputs(), aliases=()
    )
    plan = prepare(members, (CandidateCatalogSourceRef("synthetic-snapshot-001", "v1"),))
    components = [row for row in plan.rows if row.kind == "COMPONENT"]
    assert len(components) == 3
    assert {record(row)["strength_value"] for row in components} == {"010.00", "5.0", "2.50"}
    assert all(len(row.links) == 2 for row in components)
    assert all(row.identity is None for row in components)
    assert len([row for row in plan.rows if row.kind == "INGREDIENT"]) == 2


def test_members_and_artifacts_from_different_builds_are_rejected_without_raw_error():
    artifacts = approved_export()
    members = members_from(artifacts)
    changed = replace(
        members,
        products=(replace(members.products[0], product_name="원문을 오류에 남기지 않음"), *members.products[1:]),
    )
    with pytest.raises(CatalogStoragePreparationError) as error:
        prepare_catalog_storage(members=changed, artifacts=artifacts)
    assert str(error.value) == "Catalog storage preparation failed"
    assert error.value.__cause__ is None
    assert "원문" not in repr(error.value)


@pytest.mark.parametrize(
    "mutation", ["wrong-source", "wrong-product", "wrong-alias", "wrong-display", "missing", "duplicate"]
)
def test_self_consistent_export_cannot_hide_invalid_search_entry_links(mutation):
    artifacts = approved_export()
    members = members_from(artifacts)
    entry = next(e for e in members.search_entries if e.entry_type is CandidateEntryType.APPROVED_ALIAS)
    changed = {
        "wrong-source": replace(entry, source_snapshot_id="snapshot-001"),
        "wrong-product": replace(entry, product_ref=members.products[1].product_ref),
        "wrong-alias": replace(entry, alias_ref=None),
        "wrong-display": replace(entry, display_text="상충 표시값"),
    }.get(mutation, entry)
    entries = tuple(e for e in members.search_entries if e != entry)
    if mutation != "missing":
        entries += (changed,)
    if mutation == "duplicate":
        entries += (changed,)
    with pytest.raises(CatalogStoragePreparationError):
        prepare(replace(members, search_entries=entries), artifacts.catalog.source_refs)


def test_component_cannot_borrow_alias_cross_snapshot_rule():
    members = build_catalog_members(
        products=_product_inputs(), ingredients=_ingredient_inputs(), components=_component_inputs(), aliases=()
    )
    changed = replace(
        members, components=(replace(members.components[0], source_snapshot_id="snapshot-002"), *members.components[1:])
    )
    refs = (CandidateCatalogSourceRef("synthetic-snapshot-001", "v1"), CandidateCatalogSourceRef("snapshot-002", "v2"))
    with pytest.raises(CatalogStoragePreparationError):
        prepare(changed, refs)


def test_ingredient_alias_has_no_product_search_entry():
    ingredient = CatalogIngredientInput("snapshot-001", "row-1", "MFDS_INGREDIENT_CODE", "I-001", "성분")
    alias = replace(
        _alias("I-001", "alias-1", "성분 별칭"),
        target_type=CandidateEntityType.INGREDIENT,
        target_code_system="MFDS_INGREDIENT_CODE",
    )
    plan = prepare(build_catalog_members(products=(), ingredients=(ingredient,), aliases=(alias,), components=()))
    assert len(plan.identities) == 1
    assert len(plan.rows) == 2
    assert all(row.kind != "SEARCH_ENTRY" for row in plan.rows)


def test_unused_source_refs_and_empty_v2_export_are_preserved():
    refs = (CandidateCatalogSourceRef("snapshot-001", "v1"),)
    plan = prepare(build_catalog_members(products=(), components=(), aliases=()), refs)
    assert plan.source_refs == refs
    assert plan.rows == ()
    assert plan.hashes[0].canonical_bytes == b""
    assert json.loads(plan.manifest_json)["verification_status"] == "NOT_APPROVED"


def test_storage_plan_repr_does_not_dump_source_display_text():
    artifacts = approved_export()
    plan = prepare_catalog_storage(members=members_from(artifacts), artifacts=artifacts)
    assert "합성 제품" not in repr(plan)
    assert "합성 별칭" not in repr(plan.rows)
    assert "합성 제품" not in repr(plan.hashes)


@pytest.mark.parametrize("mutation", ["count", "bool-count", "schema", "normalization"])
def test_self_rehashed_metadata_cannot_replace_current_v2_or_actual_counts(mutation):
    import hashlib

    artifacts = approved_export()
    manifest = json.loads(artifacts.manifest_json)
    catalog = artifacts.catalog
    if mutation in {"count", "bool-count"}:
        count = 99 if mutation == "count" else True
        manifest["declared_counts"]["alias_count"] = count
        catalog = replace(catalog, declared_counts=replace(catalog.declared_counts, alias_count=count))
    else:
        key = "schema_version" if mutation == "schema" else "normalization_version"
        manifest[key] = "unconfirmed-v3"
        catalog = replace(catalog, **{key: "unconfirmed-v3"})
    del manifest["catalog_manifest_hash"]
    digest = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    manifest["catalog_manifest_hash"] = digest
    altered = replace(
        artifacts, catalog=replace(catalog, catalog_manifest_hash=digest), manifest_json=json.dumps(manifest).encode()
    )
    with pytest.raises(CatalogStoragePreparationError):
        prepare_catalog_storage(members=members_from(altered), artifacts=altered)
