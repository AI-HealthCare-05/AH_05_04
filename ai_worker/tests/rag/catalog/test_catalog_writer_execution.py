"""Catalog Writer composition contract: approval gate is reported, never bypassed."""

import json
from types import SimpleNamespace

import pytest

from ai_worker.admin.catalog_writer import main, summarize_catalog_execution
from ai_worker.tasks.rag.catalog.types import CatalogVerificationStatus


def _result(status, decision="ACTIVATION_CANDIDATE"):
    export = None if status is None else SimpleNamespace(catalog=SimpleNamespace(verification_status=status))
    return SimpleNamespace(export=export, decision=SimpleNamespace(value=decision))


@pytest.mark.parametrize(
    "status",
    [None, CatalogVerificationStatus.NOT_APPROVED],
)
def test_missing_approval_is_canonical_fail_closed(status):
    """No approval row -> CATALOG_NOT_APPROVED. This is the expected result, not a defect."""
    assert summarize_catalog_execution(_result(status)) == {
        "execution_status": "BLOCKED",
        "blocker_reason": "CATALOG_NOT_APPROVED",
    }


def test_approved_catalog_reports_verifier_decision():
    assert summarize_catalog_execution(_result(CatalogVerificationStatus.APPROVED)) == {
        "execution_status": "ACTIVATION_CANDIDATE"
    }


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--source-snapshot-id", "s"],
        ["--source-snapshot-id", "s", "--ingestion-run-id", "r"],
        ["--source-snapshot-id", "s", "--ingestion-run-id", "r", "--item-seq", "1"],
    ],
)
def test_incomplete_explicit_input_fails_closed(argv, capsys, monkeypatch):
    """The caller must name snapshot, run, item and version; nothing is inferred."""
    for forbidden in (
        "DB_PASSWORD",
        "DB_APP_PASSWORD",
        "DB_ADMIN_PASSWORD",
        "DB_MIGRATION_PASSWORD",
        "SOURCE_WRITER_PASSWORD",
        "SOURCE_MANAGEMENT_PASSWORD",
        "KNOWLEDGE_INDEX_BUILDER_PASSWORD",
        "CANDIDATE_INDEX_BUILDER_PASSWORD",
    ):
        monkeypatch.delenv(forbidden, raising=False)
    for key, value in (("HOST", "localhost"), ("PORT", "5432"), ("NAME", "db"), ("USER", "u"), ("PASSWORD", "p")):
        monkeypatch.setenv(f"CATALOG_WRITER_{key}", value)
    assert main(argv) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "execution_status": "BLOCKED",
        "blocker_reason": "BLOCKED_BY_PRODUCT_SOURCE_AUTHORITY",
    }
