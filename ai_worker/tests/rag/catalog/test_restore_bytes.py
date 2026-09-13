"""The byte reader keeps the already confirmed v2 fixture unchanged."""

import json

import pytest

from ai_worker.tasks.rag.catalog.restore import CatalogStorageRestoreError, restore_catalog_export_bytes
from ai_worker.tests.rag.catalog.test_hash_contract_v2 import FIXTURE, approved_export


def test_original_v2_golden_bytes_are_restored_exactly():
    restored = restore_catalog_export_bytes(
        catalog_jsonl=(FIXTURE / "catalog.jsonl").read_bytes(),
        manifest_json=(FIXTURE / "manifest.json").read_bytes(),
    )
    assert restored == approved_export()


@pytest.mark.parametrize("damage", ["crlf", "missing-lf", "unknown-field", "duplicate-key", "missing-receipt-field"])
def test_byte_reader_does_not_normalize_or_fill_damaged_data(damage):
    jsonl = (FIXTURE / "catalog.jsonl").read_bytes()
    manifest = (FIXTURE / "manifest.json").read_bytes()
    if damage == "crlf":
        jsonl = jsonl.replace(b"\n", b"\r\n")
    elif damage == "missing-lf":
        jsonl = jsonl[:-1]
    elif damage == "unknown-field":
        jsonl = jsonl.replace(b"{", b'{"unknown":null,', 1)
    elif damage == "duplicate-key":
        jsonl = jsonl.replace(b"{", b'{"record_type":"PRODUCT",', 1)
    else:
        data = json.loads(manifest)
        del data["approval_receipt"]["is_complete"]
        manifest = json.dumps(data).encode()
    with pytest.raises(CatalogStorageRestoreError):
        restore_catalog_export_bytes(catalog_jsonl=jsonl, manifest_json=manifest)
