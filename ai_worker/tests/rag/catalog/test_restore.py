import json
from dataclasses import replace

import pytest

from ai_worker.tasks.rag.candidate_index import CandidateIndexBuildFailure, CandidateIndexBuildSuccess
from ai_worker.tasks.rag.catalog import CandidateCatalogSourceRef, build_catalog_members, create_catalog_export
from ai_worker.tasks.rag.catalog.restore import CatalogStorageRestoreError, restore_catalog_storage
from ai_worker.tasks.rag.catalog.storage import prepare_catalog_storage
from ai_worker.tests.rag.catalog.test_build import _component_inputs, _ingredient_inputs, _product_inputs
from ai_worker.tests.rag.catalog.test_export import _export
from ai_worker.tests.rag.catalog.test_hash_contract_v2 import FIXTURE, approved_export, candidate
from ai_worker.tests.rag.catalog.test_storage import members_from


def storage_plan():
    artifacts = approved_export()
    return prepare_catalog_storage(members=members_from(artifacts), artifacts=artifacts)


def changed_row(plan, transform, kind="PRODUCT"):
    target = next(row for row in plan.rows if row.kind == kind)
    payload = json.loads(target.canonical_record)
    transform(payload)
    changed = replace(
        target, canonical_record=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    )
    return replace(plan, rows=tuple(changed if row == target else row for row in plan.rows))


@pytest.mark.parametrize("reverse", [False, True])
def test_restored_golden_artifacts_reach_public_candidate_without_typed_input_shortcut(reverse):
    plan = storage_plan()
    if reverse:
        plan = replace(
            plan,
            rows=plan.rows[::-1],
            identities=plan.identities[::-1],
            source_refs=plan.source_refs[::-1],
            hashes=plan.hashes[::-1],
        )
    artifacts = restore_catalog_storage(plan)
    assert artifacts.catalog_jsonl == (FIXTURE / "catalog.jsonl").read_bytes()
    assert artifacts.manifest_json == (FIXTURE / "manifest.json").read_bytes()
    expected = json.loads((FIXTURE / "expected.json").read_bytes())
    assert artifacts.catalog.catalog_manifest_hash == expected["catalog_manifest_hash"]
    result = candidate(artifacts)
    assert isinstance(result, CandidateIndexBuildSuccess)
    assert result.manifest.catalog_manifest_hash == expected["catalog_manifest_hash"]
    assert any(not member.display_text.isascii() for member in result.members)


def test_restores_ingredient_and_component_payloads_without_numeric_conversion():
    members = build_catalog_members(
        products=_product_inputs(), ingredients=_ingredient_inputs(), components=_component_inputs(), aliases=()
    )
    original = create_catalog_export(
        catalog_version="synthetic-components",
        members=members,
        source_refs=(CandidateCatalogSourceRef("synthetic-snapshot-001", "v1"),),
    )
    restored = restore_catalog_storage(prepare_catalog_storage(members=members, artifacts=original))
    assert restored.catalog_jsonl == original.catalog_jsonl
    assert {c.strength_value for c in restored.catalog.components} == {"010.00", "5.0", "2.50"}
    assert len(restored.catalog.ingredients) == 2
    assert isinstance(candidate(restored), CandidateIndexBuildFailure)


def test_unapproved_storage_is_not_promoted_during_restore():
    original = _export()
    restored = restore_catalog_storage(prepare_catalog_storage(members=members_from(original), artifacts=original))
    assert restored == original
    assert isinstance(candidate(restored), CandidateIndexBuildFailure)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-row",
        "duplicate-row",
        "missing-hash",
        "duplicate-hash",
        "missing-identity",
        "duplicate-identity",
        "missing-source",
        "duplicate-source",
    ],
)
def test_partial_or_duplicate_storage_never_returns_artifacts(mutation):
    plan = storage_plan()
    field = {"row": "rows", "hash": "hashes", "identity": "identities", "source": "source_refs"}[mutation.split("-")[1]]
    values = getattr(plan, field)
    changed = values[1:] if mutation.startswith("missing") else (*values, values[0])
    with pytest.raises(CatalogStorageRestoreError):
        restore_catalog_storage(replace(plan, **{field: changed}))


@pytest.mark.parametrize("mutation", ["digest", "kind", "spec", "target", "bytes"])
def test_hash_metadata_and_calculation_bytes_must_match(mutation):
    plan = storage_plan()
    item = plan.hashes[0]
    changes = {
        "digest": {"digest": "0" * 64},
        "kind": {"kind": "CATALOG_ENVELOPE"},
        "spec": {"contract_spec_version": "unconfirmed-v3"},
        "target": {"target": "envelope_payload"},
        "bytes": {"canonical_bytes": item.canonical_bytes[:-1]},
    }[mutation]
    with pytest.raises(CatalogStorageRestoreError):
        restore_catalog_storage(replace(plan, hashes=(replace(item, **changes), *plan.hashes[1:])))


@pytest.mark.parametrize("mutation", ["unknown", "missing", "bad-type", "bad-enum", "changed-name", "nested-unknown"])
def test_record_damage_is_not_fixed_by_defaults_or_type_coercion(mutation):
    def transform(payload):
        if mutation == "missing":
            del payload["manufacturer_name"]
        elif mutation == "nested-unknown":
            payload["identity"]["extra"] = "unexpected"
        else:
            key, value = {
                "unknown": ("extra", "unexpected"),
                "bad-type": ("product_name", 123),
                "bad-enum": ("status", "REVOKED"),
                "changed-name": ("product_name", "검증 오류에 포함하지 않을 원문"),
            }[mutation]
            payload[key] = value

    with pytest.raises(CatalogStorageRestoreError) as error:
        restore_catalog_storage(changed_row(storage_plan(), transform))
    assert str(error.value) == "Catalog storage restoration failed"
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


@pytest.mark.parametrize("mutation", ["source", "identity", "link"])
def test_relational_metadata_cannot_disagree_with_record_bytes(mutation):
    plan = storage_plan()
    target = next(row for row in plan.rows if row.kind == "SEARCH_ENTRY" and row.links[-1].kind == "ALIAS")
    changes = {
        "source": {"source_ref": CandidateCatalogSourceRef("unrelated", "v1")},
        "identity": {"identity": None},
        "link": {"links": ()},
    }[mutation]
    changed = replace(target, **changes)
    with pytest.raises(CatalogStorageRestoreError):
        restore_catalog_storage(replace(plan, rows=tuple(changed if row == target else row for row in plan.rows)))


@pytest.mark.parametrize("kind", ["manifest", "record"])
def test_duplicate_json_keys_are_rejected_on_restore(kind):
    plan = storage_plan()
    if kind == "manifest":
        plan = replace(plan, manifest_json=b'{"approval_receipt":null,' + plan.manifest_json[1:])
    else:
        first = plan.rows[0]
        plan = replace(
            plan,
            rows=(
                replace(first, canonical_record=b'{"status":"INACTIVE",' + first.canonical_record[1:]),
                *plan.rows[1:],
            ),
        )
    with pytest.raises(CatalogStorageRestoreError):
        restore_catalog_storage(plan)


@pytest.mark.parametrize("change", ["receipt-checksum", "receipt-missing-field", "gate", "schema", "counts"])
def test_manifest_cannot_disagree_with_receipt_or_actual_rows(change):
    plan = storage_plan()
    manifest = json.loads(plan.manifest_json)
    if change == "receipt-checksum":
        manifest["approval_receipt"]["export_checksum"] = "0" * 64
    elif change == "receipt-missing-field":
        del manifest["approval_receipt"]["is_complete"]
    elif change == "gate":
        manifest["approval_receipt"] = None
    elif change == "schema":
        manifest["schema_version"] = "medication-catalog-v1"
    else:
        manifest["declared_counts"]["alias_count"] = True
    with pytest.raises(CatalogStorageRestoreError):
        restore_catalog_storage(replace(plan, manifest_json=json.dumps(manifest).encode()))


def test_empty_unapproved_v2_can_be_restored_but_not_used_for_candidate():
    members = build_catalog_members(products=(), components=(), aliases=())
    original = create_catalog_export(
        catalog_version="synthetic-empty",
        members=members,
        source_refs=(CandidateCatalogSourceRef("snapshot-empty", "v1"),),
    )
    restored = restore_catalog_storage(prepare_catalog_storage(members=members, artifacts=original))
    assert restored.catalog_jsonl == b""
    assert isinstance(candidate(restored), CandidateIndexBuildFailure)


def test_missing_alias_source_is_not_replaced_with_dataclass_default():
    def remove_source(payload):
        del payload["alias_source"]

    with pytest.raises(CatalogStorageRestoreError):
        restore_catalog_storage(changed_row(storage_plan(), remove_source, kind="ALIAS"))
