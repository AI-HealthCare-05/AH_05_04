"""Fixed v2 bytes shared by Catalog production and the public Candidate handoff."""

import hashlib
import json
import unicodedata
from dataclasses import replace
from pathlib import Path

import pytest

from ai_worker.tasks.rag.candidate_index import (
    CandidateIndexBuildFailure,
    CandidateIndexBuildFailureReason,
    CandidateIndexBuildSuccess,
    build_candidate_index,
)
from ai_worker.tasks.rag.catalog import (
    CandidateCatalogSourceRef,
    CatalogApprovalReceipt,
    CatalogExportError,
    CatalogFreshnessStatus,
    CatalogSourceApproval,
    CatalogVerificationStatus,
    build_catalog_members,
    create_catalog_export,
)
from ai_worker.tasks.rag.catalog.export import verify_catalog_export
from ai_worker.tests.rag.catalog.test_export import _alias, _product
from ai_worker.tests.rag.test_candidate_index import lexical_config

FIXTURE = Path(__file__).resolve().parents[4] / "tests/fixtures/rag/catalog/hash-v2"


def approved_export(*, reverse=False, nfd=True):
    name = unicodedata.normalize("NFD" if nfd else "NFC", "합성 제품")
    products = (_product("P-001"), replace(_product("P-002"), product_name=name))
    aliases = (replace(_alias("P-001", "alias-cross", "합성 별칭"), source_snapshot_id="snapshot-002"),)
    refs = (CandidateCatalogSourceRef("snapshot-001", "v1"), CandidateCatalogSourceRef("snapshot-002", "v2"))
    members = build_catalog_members(products=products[::-1] if reverse else products, components=(), aliases=aliases)
    initial = create_catalog_export(catalog_version="synthetic-hash-v2", source_refs=refs, members=members)
    sources = tuple(
        CatalogSourceApproval(
            ref, f"synthetic-source-{i}", CatalogVerificationStatus.APPROVED, CatalogFreshnessStatus.CURRENT
        )
        for i, ref in enumerate(refs)
    )
    receipt = CatalogApprovalReceipt(
        "synthetic-approval-only",
        "synthetic-hash-v2",
        initial.export_checksum,
        CatalogVerificationStatus.APPROVED,
        True,
        sources[::-1] if reverse else sources,
    )
    return create_catalog_export(
        catalog_version="synthetic-hash-v2",
        source_refs=refs[::-1] if reverse else refs,
        members=members,
        approval_receipt=receipt,
    )


def candidate(artifacts):
    return build_candidate_index(
        artifacts, replace(lexical_config(), normalization_version=artifacts.catalog.normalization_version)
    )


def assert_rejected(artifacts):
    with pytest.raises(CatalogExportError) as error:
        verify_catalog_export(artifacts)
    assert error.value.code == "CATALOG_MANIFEST_BINDING_INVALID"
    result = candidate(artifacts)
    assert isinstance(result, CandidateIndexBuildFailure)
    assert result.reason is CandidateIndexBuildFailureReason.CATALOG_MANIFEST_INVALID


@pytest.mark.parametrize("reverse", [False, True])
def test_fixed_approved_bytes_reach_public_candidate(reverse):
    actual = approved_export(reverse=reverse)
    expected = json.loads((FIXTURE / "expected.json").read_bytes())
    jsonl = (FIXTURE / "catalog.jsonl").read_bytes()
    payload = (FIXTURE / "envelope-payload.json").read_bytes()
    manifest = (FIXTURE / "manifest.json").read_bytes()
    assert actual.catalog_jsonl == jsonl
    assert actual.manifest_json == manifest
    assert hashlib.sha256(jsonl).hexdigest() == actual.export_checksum == expected["export_checksum"]
    assert (
        hashlib.sha256(payload).hexdigest() == actual.catalog.catalog_manifest_hash == expected["catalog_manifest_hash"]
    )
    decoded = json.loads(manifest)
    del decoded["catalog_manifest_hash"]
    # Independent standard-library serialization of the frozen payload, not a production helper.
    assert (
        json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        == payload
    )
    assert not payload.endswith(b"\n")
    assert manifest.endswith(b"\n") and jsonl.endswith(b"\n")
    assert hashlib.sha256(manifest).hexdigest() == expected["manifest_file_checksum"]
    assert len(set(expected.values())) == 3
    result = candidate(replace(actual, catalog_jsonl=jsonl, manifest_json=manifest))
    assert isinstance(result, CandidateIndexBuildSuccess)
    assert result.manifest.catalog_manifest_hash == expected["catalog_manifest_hash"]


@pytest.mark.parametrize("mutation", ["crlf", "missing-lf", "bom", "extra-lf"])
@pytest.mark.parametrize("update_checksum", [False, True])
def test_noncanonical_export_bytes_rejected_even_with_updated_checksum(mutation, update_checksum):
    artifacts = approved_export()
    raw = artifacts.catalog_jsonl
    altered = {
        "crlf": raw.replace(b"\n", b"\r\n"),
        "missing-lf": raw[:-1],
        "bom": b"\xef\xbb\xbf" + raw,
        "extra-lf": raw + b"\n",
    }[mutation]
    changed = replace(artifacts, catalog_jsonl=altered)
    if update_checksum:
        manifest = json.loads(artifacts.manifest_json)
        checksum = hashlib.sha256(altered).hexdigest()
        manifest["export_checksum"] = checksum
        manifest["approval_receipt"]["export_checksum"] = checksum
        del manifest["catalog_manifest_hash"]
        digest = hashlib.sha256(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        manifest["catalog_manifest_hash"] = digest
        changed = replace(
            changed,
            export_checksum=checksum,
            catalog=replace(artifacts.catalog, catalog_manifest_hash=digest),
            manifest_json=json.dumps(manifest, ensure_ascii=False).encode(),
        )
    assert_rejected(changed)


@pytest.mark.parametrize("field", ["export_checksum", "manifest_file_checksum"])
def test_other_digest_cannot_replace_envelope_hash(field):
    artifacts = approved_export()
    digest = json.loads((FIXTURE / "expected.json").read_bytes())[field]
    manifest = json.loads(artifacts.manifest_json)
    manifest["catalog_manifest_hash"] = digest
    assert_rejected(
        replace(
            artifacts,
            catalog=replace(artifacts.catalog, catalog_manifest_hash=digest),
            manifest_json=json.dumps(manifest).encode(),
        )
    )


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema_version", "medication-catalog-v3"),
        ("canonicalization_spec_version", "catalog-projection-v1"),
        ("approval_receipt", None),
        ("source_refs", []),
        ("declared_counts", {}),
    ],
)
def test_envelope_fields_cannot_be_changed_without_binding(key, value):
    artifacts = approved_export()
    manifest = json.loads(artifacts.manifest_json)
    manifest[key] = value
    assert_rejected(replace(artifacts, manifest_json=json.dumps(manifest).encode()))


@pytest.mark.parametrize("prefix", [b'"export_checksum":"ignored",', b'"approval_receipt":null,'])
def test_duplicate_manifest_keys_are_rejected(prefix):
    artifacts = approved_export()
    # A last-value-wins reader currently hides the conflicting first value.
    assert_rejected(replace(artifacts, manifest_json=b"{" + prefix + artifacts.manifest_json[1:]))


def test_nfc_display_does_not_reuse_nfd_golden_hash():
    nfd, nfc = approved_export(), approved_export(nfd=False)
    assert nfd.export_checksum != nfc.export_checksum
    assert nfd.catalog.catalog_manifest_hash != nfc.catalog.catalog_manifest_hash
    assert isinstance(candidate(nfd), CandidateIndexBuildSuccess)
    assert isinstance(candidate(nfc), CandidateIndexBuildSuccess)


@pytest.mark.parametrize("duplicate", [b'"receipt_id":"ignored",', b'"receipt_id":"synthetic-approval-only",'])
def test_nested_duplicate_keys_are_rejected_even_when_equal(duplicate):
    artifacts = approved_export()
    changed = artifacts.manifest_json.replace(b'"approval_receipt":{', b'"approval_receipt":{' + duplicate, 1)
    assert changed != artifacts.manifest_json
    assert_rejected(replace(artifacts, manifest_json=changed))
