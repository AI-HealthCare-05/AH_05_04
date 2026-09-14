"""Synthetic-only detail HTTP → raw evidence → Snapshot boundary regression."""

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.tasks.rag.source_client.contracts import SourceRunStatus
from ai_worker.tasks.rag.source_client.endpoints import MFDS_DETAIL_CANDIDATE, MFDS_DETAIL_IDENTITY
from ai_worker.tasks.rag.source_ingestion.mfds_detail import (
    DETAIL_PARSER_VERSION,
    acquire_mfds_detail,
    build_detail_ingestion_result,
    ingest_and_persist_mfds_detail,
)
from ai_worker.tasks.rag.source_ingestion.receipt_validation import (
    calculate_endpoint_receipt_hash,
    load_detail_endpoint_receipt,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_lifecycle import SnapshotIngestionMetadata
from ai_worker.tasks.rag.source_ingestion.snapshot_policy import SourceSnapshotPolicy

ROOT = Path(__file__).resolve().parents[4]
NOW = datetime(2026, 9, 13, tzinfo=UTC)


def row(serial="001", **changes):
    return dict(
        ITEM_SEQ="synthetic-product",
        TAMT_SEQ="01",
        MTRAL_SN=serial,
        MTRAL_CODE="synthetic-material",
        QNT="1.0",
        INGD_UNIT_CD="mg",
        **changes,
    )


def envelope(records, page=1, total=None, code="00"):
    return {
        "header": {"resultCode": code},
        "body": {
            "pageNo": page,
            "numOfRows": 100,
            "totalCount": len(records) if total is None else total,
            "items": records,
        },
    }


async def resolver(_host):
    return ("8.8.8.8",)


async def acquire(tmp_path, pages, gate=None):
    requests = []

    def handler(request):
        requests.append(request)
        payload = pages[int(request.url.params["pageNo"]) - 1]
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await acquire_mfds_detail(
            gate=gate or AsyncMock(),
            client=client,
            secret_value="synthetic-secret",
            spool_parent=tmp_path,
            resolver=resolver,
        )
    return result, requests


def receipt_file(tmp_path):
    # Test-only receipt evidence; never written into production endpoint validation docs.
    payload = json.loads((ROOT / "docs/validation/rag/endpoints/LIST_APPROVED_PRODUCTS.json").read_text())
    contract = MFDS_DETAIL_CANDIDATE.contract
    payload.update(
        identity=asdict(MFDS_DETAIL_IDENTITY),
        primary_key_fields=list(contract.primary_key_fields),
        verified_path_template=contract.path_template,
        limits=asdict(contract.limits),
        validated_record_count=2,
    )
    scenarios = [
        "LIST_PRODUCT_COMPONENT_DETAILS_SUCCESS",
        "SYNTHETIC_AUTH_FAILURE",
        "SYNTHETIC_DAILY_LIMIT",
        "SYNTHETIC_EMPTY",
        "SYNTHETIC_SCHEMA_DRIFT",
        "SYNTHETIC_DETAIL_BLANK",
        "SYNTHETIC_DETAIL_DUPLICATE",
    ]
    contents = [
        envelope([row(), row("002")]),
        envelope([], code="30"),
        envelope([], code="22"),
        envelope([]),
        {"invalid": True},
        envelope([{"ITEM_SEQ": "synthetic-blank"}]),
        envelope([row(), row()]),
    ]
    fixtures = []
    for index, (scenario, content) in enumerate(zip(scenarios, contents, strict=True)):
        relative = f"tests/fixtures/rag/mfds/detail-{index}.json"
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content))
        fixtures.append(dict(scenario=scenario, path=relative, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    payload["fixture_evidence"] = fixtures
    payload["receipt_hash"] = calculate_endpoint_receipt_hash(payload)
    path = tmp_path / "detail-receipt.json"
    path.write_text(json.dumps(payload))
    return path


def metadata(checksum="a" * 64):
    return SnapshotIngestionMetadata(
        source_version=f"internal:commit-{'a' * 40}-manifest-{'b' * 64}:{checksum}",
        schema_version="mfds-detail@1",
        parser_version=DETAIL_PARSER_VERSION,
        normalization_version="identity@1",
        rejected_record_count=0,
        run_group_key="synthetic-detail",
        attempt_number=1,
        started_at=NOW,
        finished_at=NOW,
        collected_at=NOW,
    )


def repository():
    repo = AsyncMock()
    repo.lock_operation.return_value = uuid4()
    repo.get_source_policy.return_value = SourceSnapshotPolicy()
    repo.get_snapshot_by_version.return_value = None
    repo.get_latest_snapshot.return_value = None
    repo.has_attempt_version_conflict.return_value = False
    repo.create_snapshot.return_value = uuid4()
    repo.create_run.return_value = uuid4()
    return repo


def store(tmp_path):
    return LocalPrivateSourceArtifactStore(root=tmp_path / "objects")


async def test_whole_pages_preserve_repeated_material_and_raw_bytes(tmp_path):
    acquisition, requests = await acquire(
        tmp_path, [envelope([row()], total=2), envelope([row("002")], page=2, total=2)]
    )
    evidence = receipt_file(tmp_path)
    ingestion, canonical = build_detail_ingestion_result(
        acquisition=acquisition, receipt_path=evidence, repository_root=tmp_path
    )
    assert json.loads(canonical) == [row(), row("002")]
    assert ingestion.record_count == 2
    assert ingestion.canonical_checksum == hashlib.sha256(canonical).hexdigest()
    assert [dict(request.url.params) for request in requests] == [
        {"type": "json", "serviceKey": "synthetic-secret", "pageNo": str(i), "numOfRows": "100"} for i in (1, 2)
    ]
    report = (acquisition.directory / "inspection.json").read_text()
    assert "synthetic-secret" not in report
    assert json.loads(report)["catalog_input_eligible"] is True
    assert (acquisition.directory.stat().st_mode & 0o777) == 0o700
    repo = repository()
    result = await ingest_and_persist_mfds_detail(
        acquisition=acquisition,
        repository=repo,
        artifact_store=store(tmp_path),
        metadata=metadata(ingestion.canonical_checksum),
        receipt_path=evidence,
        repository_root=tmp_path,
    )
    assert result.snapshot_id == repo.create_snapshot.return_value
    assert len(repo.create_artifacts.call_args.kwargs["artifacts"]) == 2


@pytest.mark.parametrize(
    "records,reason",
    [
        ([row(), {"ITEM_SEQ": "synthetic-blank"}], "EMPTY_COMPONENT_FIELDS"),
        ([row(), dict(row("002"), QNT=None)], "INVALID_COMPONENT_FIELDS"),
        ([row(), dict(row(), QNT="2")], "CONFLICTING_OBSERVATION"),
        ([row(), row()], None),
    ],
)
async def test_ineligible_rows_kept_without_partial_snapshot(tmp_path, records, reason):
    acquisition, _ = await acquire(tmp_path, [envelope(records)])
    report = json.loads((acquisition.directory / "inspection.json").read_text())
    assert json.loads((acquisition.directory / "observations.json").read_bytes()) == records
    assert report["input_count"] == 2
    assert report["observation_count"] + report["duplicate_count"] + report["exclusion_count"] == 2
    assert not report["catalog_input_eligible"]
    if reason:
        assert report["exclusion_counts"][reason] == 1
        assert report["exclusions"][0]["record_index"] == 1
    repo = repository()
    result = await ingest_and_persist_mfds_detail(
        acquisition=acquisition,
        repository=repo,
        artifact_store=store(tmp_path),
        metadata=metadata(),
        receipt_path=receipt_file(tmp_path),
        repository_root=tmp_path,
    )
    assert result.failure_code
    repo.create_snapshot.assert_not_awaited()
    assert len(repo.create_artifacts.call_args.kwargs["artifacts"]) == 1


@pytest.mark.parametrize(
    "damage", ["subset", "missing-page", "wrong-total", "bytes", "product-receipt", "wrong-operation", "fixture"]
)
async def test_scope_and_evidence_damage_rejected(tmp_path, damage):
    acquisition, _ = await acquire(tmp_path, [envelope([row()], total=2), envelope([row("002")], page=2, total=2)])
    evidence = receipt_file(tmp_path)
    if damage == "subset":
        acquisition = replace(acquisition, result=replace(acquisition.result, pages=acquisition.result.pages[:1]))
    elif damage == "missing-page":
        acquisition = replace(acquisition, artifacts=acquisition.artifacts[:1])
    elif damage == "wrong-total":
        pages = (replace(acquisition.result.pages[0], total_count=1), *acquisition.result.pages[1:])
        acquisition = replace(acquisition, result=replace(acquisition.result, pages=pages))
    elif damage == "bytes":
        acquisition.artifacts[0][1].write_bytes(b"{}")
    elif damage == "product-receipt":
        evidence = ROOT / "docs/validation/rag/endpoints/LIST_APPROVED_PRODUCTS.json"
    elif damage == "fixture":
        (tmp_path / "tests/fixtures/rag/mfds/detail-0.json").write_text("{}")
    else:
        acquisition = replace(
            acquisition,
            result=replace(acquisition.result, operation=replace(MFDS_DETAIL_IDENTITY, operation_code="OTHER")),
        )
    with pytest.raises(ValueError):
        build_detail_ingestion_result(acquisition=acquisition, receipt_path=evidence, repository_root=tmp_path)


@pytest.mark.parametrize(
    "pages",
    [
        [envelope([row()], total=3), envelope([row("002")], page=2, total=2)],
        [envelope([row()], total=2), envelope([], page=2, total=2)],
        [envelope([row()], page=2)],
        [envelope([], code="30")],
        [envelope([], code="22")],
        [{"invalid": True}],
    ],
)
async def test_failed_acquisition_never_produces_snapshot(tmp_path, pages):
    acquisition, _ = await acquire(tmp_path, pages)
    assert acquisition.result.status is not SourceRunStatus.SUCCEEDED
    repo = repository()
    await ingest_and_persist_mfds_detail(
        acquisition=acquisition,
        repository=repo,
        artifact_store=store(tmp_path),
        metadata=metadata(),
        receipt_path=receipt_file(tmp_path),
        repository_root=tmp_path,
    )
    repo.create_snapshot.assert_not_awaited()


async def test_lock_conflict_makes_no_http_call(tmp_path):
    gate = AsyncMock()
    gate.try_lock_acquisition.side_effect = RuntimeError("busy")
    with pytest.raises(RuntimeError, match="busy"):
        await acquire(tmp_path, [], gate)
    assert not list(tmp_path.iterdir())


async def test_storage_error_propagates_for_caller_rollback(tmp_path):
    acquisition, _ = await acquire(tmp_path, [envelope([row()])])
    evidence = receipt_file(tmp_path)
    ingestion, _ = build_detail_ingestion_result(
        acquisition=acquisition, receipt_path=evidence, repository_root=tmp_path
    )
    repo = repository()
    repo.create_artifacts.side_effect = RuntimeError("synthetic storage failure")
    with pytest.raises(RuntimeError, match="synthetic storage failure"):
        await ingest_and_persist_mfds_detail(
            acquisition=acquisition,
            repository=repo,
            artifact_store=store(tmp_path),
            metadata=metadata(ingestion.canonical_checksum),
            receipt_path=evidence,
            repository_root=tmp_path,
        )


def test_real_receipt_absence_and_product_receipt_are_not_approval(tmp_path):
    with pytest.raises(ValueError):
        load_detail_endpoint_receipt(tmp_path / "missing.json")
    with pytest.raises(ValueError):
        load_detail_endpoint_receipt(ROOT / "docs/validation/rag/endpoints/LIST_APPROVED_PRODUCTS.json")


async def test_version_checksum_mismatch_is_failed_audit_not_snapshot(tmp_path):
    acquisition, _ = await acquire(tmp_path, [envelope([row()])])
    repo = repository()
    result = await ingest_and_persist_mfds_detail(
        acquisition=acquisition,
        repository=repo,
        artifact_store=store(tmp_path),
        metadata=metadata(),
        receipt_path=receipt_file(tmp_path),
        repository_root=tmp_path,
    )
    assert result.failure_code == "SOURCE_VERSION_BINDING_MISMATCH"
    repo.create_snapshot.assert_not_awaited()
    assert len(repo.create_artifacts.call_args.kwargs["artifacts"]) == 1


async def test_page_limit_preserves_observed_raw_but_no_canonical_candidate(tmp_path, monkeypatch):
    import ai_worker.tasks.rag.source_ingestion.mfds_detail as module

    monkeypatch.setattr(
        module, "_CONTRACT", replace(module._CONTRACT, limits=replace(module._CONTRACT.limits, max_pages=1))
    )
    acquisition, requests = await acquire(tmp_path, [envelope([row()], total=2)])
    assert len(requests) == 1
    assert acquisition.result.failure.code.value == "PAGE_LIMIT_EXCEEDED"
    assert len(acquisition.artifacts) == 1
    assert not (acquisition.directory / "observations.json").exists()
    assert json.loads((acquisition.directory / "inspection.json").read_text())["catalog_input_eligible"] is False


async def test_raw_preservation_failure_cannot_be_reported_as_success(tmp_path, monkeypatch):
    import ai_worker.tasks.rag.source_ingestion.mfds_detail as module

    def fail_write(*_args):
        raise OSError("synthetic-private-path")

    monkeypatch.setattr(module, "_write_private", fail_write)
    with pytest.raises(RuntimeError, match="caller must roll back") as error:
        await acquire(tmp_path, [envelope([row()])])
    assert "synthetic-private-path" not in str(error.value)
