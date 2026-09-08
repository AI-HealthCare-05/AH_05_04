"""복원 자료의 당시 승인과 현재 승인 포트의 결과를 구분합니다."""

import asyncio
import hashlib
import json
import traceback
import unicodedata
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from pydantic import TypeAdapter

from ai_worker.tasks.rag.candidate_index import CandidateIndexBuildSuccess
from ai_worker.tasks.rag.catalog.approval import CatalogApprovalReceipt, CatalogApprovalVerifier
from ai_worker.tasks.rag.catalog.restore import CatalogStorageRestoreError, restore_current_catalog_storage
from ai_worker.tasks.rag.catalog.storage import prepare_catalog_storage
from ai_worker.tasks.rag.catalog.types import CatalogFreshnessStatus, CatalogVerificationStatus
from ai_worker.tests.rag.catalog.test_export import _export
from ai_worker.tests.rag.catalog.test_hash_contract_v2 import FIXTURE, approved_export, candidate
from ai_worker.tests.rag.catalog.test_restore import storage_plan
from ai_worker.tests.rag.catalog.test_storage import members_from


def verifier_and_receipt():
    data = json.loads(approved_export().manifest_json)["approval_receipt"]
    receipt = TypeAdapter(CatalogApprovalReceipt).validate_json(json.dumps(data), strict=True)
    verifier = AsyncMock(spec=CatalogApprovalVerifier)
    verifier.verify.return_value = receipt
    return verifier, receipt


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_current_receipt_preserves_frozen_bytes_and_public_handoff(reverse):
    plan = storage_plan()
    verifier, receipt = verifier_and_receipt()
    if reverse:
        verifier.verify.return_value = replace(receipt, sources=receipt.sources[::-1])
        plan = replace(plan, rows=plan.rows[::-1], source_refs=plan.source_refs[::-1])
    restored = await restore_current_catalog_storage(plan, approval_verifier=verifier)
    expected = approved_export()
    assert restored == expected
    verifier.verify.assert_awaited_once_with(
        catalog_version=expected.catalog.catalog_version,
        export_checksum=expected.export_checksum,
        source_refs=expected.catalog.source_refs,
    )
    frozen = json.loads((FIXTURE / "expected.json").read_bytes())
    assert restored.catalog_jsonl == (FIXTURE / "catalog.jsonl").read_bytes()
    assert restored.manifest_json == (FIXTURE / "manifest.json").read_bytes()
    assert hashlib.sha256(restored.catalog_jsonl).hexdigest() == frozen["export_checksum"]
    assert hashlib.sha256(restored.manifest_json).hexdigest() == frozen["manifest_file_checksum"]
    result = candidate(restored)
    assert isinstance(result, CandidateIndexBuildSuccess)
    assert result.manifest.catalog_manifest_hash == frozen["catalog_manifest_hash"]
    assert result.manifest.product_identity_count == 2
    assert result.manifest.product_name_count == 2
    assert result.manifest.approved_alias_count == 1
    assert any(unicodedata.normalize("NFC", member.display_text) != member.display_text for member in result.members)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        "missing-verifier",
        "revoked",
        "version",
        "checksum",
        "receipt-id",
        "source-receipt",
        "source-subset",
        "duplicate-source",
        "not-approved",
        "source-not-approved",
        "stale",
        "incomplete",
    ],
)
async def test_past_approval_never_overrides_current_evidence(change):
    plan = storage_plan()
    verifier, receipt = verifier_and_receipt()
    if change == "missing-verifier":
        verifier = None
    elif change == "revoked":
        verifier.verify.return_value = None
    else:
        first, *others = receipt.sources
        changes = {
            "version": replace(receipt, catalog_version="different"),
            "checksum": replace(receipt, export_checksum="0" * 64),
            "receipt-id": replace(receipt, receipt_id="new-approved-receipt"),
            "source-receipt": replace(receipt, sources=(replace(first, receipt_id="new-source-receipt"), *others)),
            "source-subset": replace(receipt, sources=(first,)),
            "duplicate-source": replace(receipt, sources=(*receipt.sources, first)),
            "not-approved": replace(receipt, verification_status=CatalogVerificationStatus.NOT_APPROVED),
            "source-not-approved": replace(
                receipt, sources=(replace(first, verification_status=CatalogVerificationStatus.NOT_APPROVED), *others)
            ),
            "stale": replace(receipt, sources=(replace(first, freshness_status=CatalogFreshnessStatus.STALE), *others)),
            "incomplete": replace(receipt, is_complete=False),
        }
        verifier.verify.return_value = changes[change]
    with pytest.raises(CatalogStorageRestoreError):
        await restore_current_catalog_storage(plan, approval_verifier=verifier)
    assert plan == storage_plan()


@pytest.mark.asyncio
async def test_rechecks_each_call_and_revocation_blocks_second_handoff():
    verifier, receipt = verifier_and_receipt()
    verifier.verify.side_effect = [receipt, None]
    plan = storage_plan()
    await restore_current_catalog_storage(plan, approval_verifier=verifier)
    with pytest.raises(CatalogStorageRestoreError):
        await restore_current_catalog_storage(plan, approval_verifier=verifier)
    assert verifier.verify.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["unapproved", "corrupt"])
async def test_bad_stored_material_never_calls_approval_service(invalid):
    verifier, _ = verifier_and_receipt()
    if invalid == "corrupt":
        plan = replace(storage_plan(), rows=())
    else:
        artifacts = _export()
        plan = prepare_catalog_storage(members=members_from(artifacts), artifacts=artifacts)
    with pytest.raises(CatalogStorageRestoreError):
        await restore_current_catalog_storage(plan, approval_verifier=verifier)
    verifier.verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_outage_is_redacted_and_cancellation_propagates():
    verifier, _ = verifier_and_receipt()
    verifier.verify.side_effect = RuntimeError("private-approval-endpoint-secret")
    with pytest.raises(CatalogStorageRestoreError) as error:
        await restore_current_catalog_storage(storage_plan(), approval_verifier=verifier)
    assert "private-approval" not in "".join(traceback.format_exception(error.value))
    verifier.verify.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await restore_current_catalog_storage(storage_plan(), approval_verifier=verifier)
