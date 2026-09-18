"""Synthetic-only product HTTP → raw evidence → Snapshot boundary regression.

외부 MFDS actual API는 호출하지 않는다. 모든 응답은 합성 transport에서 나온다.
"""

import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from ai_worker.adapters.local_private_source_artifact_store import LocalPrivateSourceArtifactStore
from ai_worker.tasks.rag.source_client.contracts import SourceRunStatus
from ai_worker.tasks.rag.source_ingestion.mfds_product import (
    PRODUCT_IDENTITY,
    PRODUCT_NORMALIZATION_VERSION,
    acquire_mfds_product,
    build_product_snapshot_metadata,
    ingest_and_persist_mfds_product,
    load_product_receipt,
    observed_canonical_checksum,
    write_product_report,
)
from ai_worker.tasks.rag.source_ingestion.parse import PRODUCT_CANONICALIZATION_SPEC_VERSION
from ai_worker.tasks.rag.source_ingestion.receipt_validation import calculate_endpoint_receipt_hash
from ai_worker.tasks.rag.source_ingestion.reject_codes import (
    PRODUCT_REJECT_PARSER_VERSION,
    REJECT_CODE_CONTRACT_VERSION,
)
from ai_worker.tasks.rag.source_ingestion.snapshot_policy import SourceSnapshotPolicy

ROOT = Path(__file__).resolve().parents[4]
REPOSITORY_RECEIPT = ROOT / "docs/validation/rag/endpoints/LIST_APPROVED_PRODUCTS.json"
SECRET = "synthetic-service-key"
PRODUCTION_SOURCES = (
    ROOT / "ai_worker/tasks/rag/source_ingestion/mfds_product.py",
    ROOT / "ai_worker/admin/mfds_product_ingestion.py",
)
# 현재 실측 probe 값. production 코드가 이 값을 상수로 굳히지 않았는지만 확인한다.
ACTUAL_PROBE_RECORD_COUNT = 42_992
ACTUAL_PROBE_PAGE_COUNT = 430


def product(item_seq="synthetic-000001", **changes):
    return dict(ITEM_SEQ=item_seq, ITEM_NAME="합성 제품", ENTP_NAME="합성 제조사", **changes)


def envelope(records, page=1, total=None, code="00"):
    return {
        "header": {"resultCode": code},
        "body": {
            "pageNo": page,
            "numOfRows": 100,
            "totalCount": len(records) if total is None else total,
            "items": {"item": records},
        },
    }


async def resolver(_host):
    return ("8.8.8.8",)


async def acquire(tmp_path, pages, gate=None):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=pages[int(request.url.params["pageNo"]) - 1])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        acquisition = await acquire_mfds_product(
            gate=gate or AsyncMock(),
            client=client,
            secret_value=SECRET,
            spool_parent=tmp_path,
            resolver=resolver,
        )
    return acquisition, requests


def receipt_file(tmp_path, *, validated_record_count=1):
    """Test-only receipt evidence; never written into production endpoint validation docs."""
    payload = json.loads(REPOSITORY_RECEIPT.read_text())
    payload["validated_record_count"] = validated_record_count
    fixtures = []
    scenarios = (
        ("LIST_APPROVED_PRODUCTS_SUCCESS", envelope([product()])),
        ("SYNTHETIC_AUTH_FAILURE", envelope([], code="30")),
        ("SYNTHETIC_DAILY_LIMIT", envelope([], code="22")),
        ("SYNTHETIC_EMPTY", envelope([])),
        ("SYNTHETIC_SCHEMA_DRIFT", {"invalid": True}),
    )
    for index, (scenario, content) in enumerate(scenarios):
        relative = f"tests/fixtures/rag/mfds/product-{index}.json"
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content))
        fixtures.append(dict(scenario=scenario, path=relative, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    payload["fixture_evidence"] = fixtures
    payload["receipt_hash"] = calculate_endpoint_receipt_hash(payload)
    path = tmp_path / f"product-receipt-{validated_record_count}.json"
    path.write_text(json.dumps(payload))
    return path


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


async def persist(tmp_path, acquisition, *, receipt_path, repo=None, metadata=None):
    repo = repo or repository()
    return (
        await ingest_and_persist_mfds_product(
            acquisition=acquisition,
            repository=repo,
            artifact_store=store(tmp_path),
            metadata=metadata
            or build_product_snapshot_metadata(acquisition=acquisition, run_group_key="synthetic-product"),
            receipt_path=receipt_path,
            repository_root=tmp_path,
        ),
        repo,
    )


async def test_every_page_is_fetched_without_item_or_page_filters(tmp_path):
    rows = [product("P-001"), product("P-002")]
    acquisition, requests = await acquire(
        tmp_path, [envelope([rows[0]], total=2), envelope([rows[1]], page=2, total=2)]
    )

    assert acquisition.result.operation == PRODUCT_IDENTITY
    assert acquisition.result.status is SourceRunStatus.SUCCEEDED
    assert acquisition.result.snapshot_candidate_allowed
    assert [dict(request.url.params) for request in requests] == [
        {"type": "json", "serviceKey": SECRET, "pageNo": str(number), "numOfRows": "100"} for number in (1, 2)
    ]
    # ITEM_SEQ provider filter가 요청에 섞이지 않는다.
    assert all("ITEM_SEQ" not in dict(request.url.params) for request in requests)
    assert len(acquisition.artifacts) == 2
    assert (acquisition.directory.stat().st_mode & 0o777) == 0o700


async def test_report_records_observations_without_leaking_the_service_key(tmp_path):
    rows = [product("P-001"), product("P-002")]
    acquisition, _ = await acquire(tmp_path, [envelope(rows)])
    receipt = load_product_receipt(
        receipt_path=receipt_file(tmp_path, validated_record_count=2), repository_root=tmp_path
    )

    report_path = write_product_report(acquisition, receipt=receipt)
    report = json.loads(report_path.read_text())

    assert SECRET not in report_path.read_text()
    assert report["record_count"] == 2
    assert report["pages_cover_advertised_count"] is True
    assert report["primary_key_null_count"] == 0
    assert report["primary_key_duplicate_count"] == 0
    assert report["whole_record_duplicate_count"] == 0
    assert report["canonicalization_spec_version"] == PRODUCT_CANONICALIZATION_SPEC_VERSION
    assert report["receipt_validated_record_count"] == 2
    assert report["matches_receipt_record_count"] is True
    assert report["receipt_issued"] is False
    assert report["canonical_checksum"] == observed_canonical_checksum(acquisition)


async def test_stale_receipt_count_fails_closed_before_any_persistence(tmp_path):
    """과거 건수로 발급된 Receipt는 Snapshot을 만들지 못한다. 보고에 그치지 않고 차단한다."""
    acquisition, _ = await acquire(tmp_path, [envelope([product("P-001")])])
    stale = receipt_file(tmp_path, validated_record_count=ACTUAL_PROBE_RECORD_COUNT - 3)
    receipt = load_product_receipt(receipt_path=stale, repository_root=tmp_path)

    report = json.loads(write_product_report(acquisition, receipt=receipt).read_text())
    assert report["matches_receipt_record_count"] is False

    repo = repository()
    with pytest.raises(ValueError, match="Receipt record count"):
        await persist(tmp_path, acquisition, receipt_path=stale, repo=repo)

    repo.create_snapshot.assert_not_awaited()
    repo.create_run.assert_not_awaited()


async def test_matching_receipt_count_is_required_exactly(tmp_path):
    """한 건만 어긋나도 통과하지 않는다."""
    acquisition, _ = await acquire(tmp_path, [envelope([product("P-001"), product("P-002")])])

    for count in (1, 3):
        repo = repository()
        with pytest.raises(ValueError, match="Receipt record count"):
            await persist(
                tmp_path, acquisition, receipt_path=receipt_file(tmp_path, validated_record_count=count), repo=repo
            )
        repo.create_run.assert_not_awaited()


async def test_metadata_binds_an_api_source_version_to_the_observed_checksum(tmp_path):
    acquisition, _ = await acquire(tmp_path, [envelope([product("P-001")])])

    metadata = build_product_snapshot_metadata(
        acquisition=acquisition, run_group_key="synthetic-product", verified_by="synthetic-actor"
    )

    checksum = observed_canonical_checksum(acquisition)
    assert checksum is not None
    assert metadata.source_version.startswith("api:")
    assert metadata.source_version.endswith(f":{checksum}")
    assert metadata.external_version is None
    assert metadata.parser_version == PRODUCT_REJECT_PARSER_VERSION
    assert metadata.reject_code_contract_version == REJECT_CODE_CONTRACT_VERSION
    assert metadata.normalization_version == PRODUCT_NORMALIZATION_VERSION == "mfds-product-normalization@1"
    # normalization 단계 식별자와 canonicalization 규격은 의미가 다르므로 같은 값일 수 없다.
    assert metadata.normalization_version != PRODUCT_CANONICALIZATION_SPEC_VERSION
    assert metadata.schema_version == "mfds-product-response@1"
    assert metadata.rejected_record_count == 0
    assert metadata.collected_at == acquisition.finished_at


async def test_verified_run_reaches_a_snapshot_with_every_raw_page(tmp_path):
    rows = [product("P-001"), product("P-002")]
    acquisition, _ = await acquire(tmp_path, [envelope([rows[0]], total=2), envelope([rows[1]], page=2, total=2)])

    result, repo = await persist(tmp_path, acquisition, receipt_path=receipt_file(tmp_path, validated_record_count=2))

    assert result.snapshot_id == repo.create_snapshot.return_value
    assert result.failure_code is None
    assert len(repo.create_artifacts.call_args.kwargs["artifacts"]) == 2


@pytest.mark.parametrize(
    "rows",
    [
        [product("P-001"), {}],
        [product("P-001"), product("")],
        [product("P-001"), product("P-001")],
    ],
)
async def test_identity_rejections_keep_raw_evidence_without_a_partial_snapshot(tmp_path, rows):
    acquisition, _ = await acquire(tmp_path, [envelope(rows)])

    assert observed_canonical_checksum(acquisition) is None
    result, repo = await persist(tmp_path, acquisition, receipt_path=receipt_file(tmp_path, validated_record_count=2))

    assert result.snapshot_id is None
    assert result.failure_code == "PARSER_VALIDATION_FAILED"
    repo.create_snapshot.assert_not_awaited()


async def test_missing_receipt_fails_closed_instead_of_a_silent_repository_default(tmp_path):
    acquisition, _ = await acquire(tmp_path, [envelope([product("P-001")])])
    repo = repository()

    with pytest.raises(ValueError, match="Endpoint receipt"):
        await persist(tmp_path, acquisition, receipt_path=tmp_path / "absent-receipt.json", repo=repo)

    repo.create_snapshot.assert_not_awaited()
    repo.create_run.assert_not_awaited()


async def test_contract_mismatched_metadata_is_refused_before_any_persistence(tmp_path):
    acquisition, _ = await acquire(tmp_path, [envelope([product("P-001")])])
    metadata = build_product_snapshot_metadata(acquisition=acquisition, run_group_key="synthetic-product")

    for change in ({"parser_version": "other@1"}, {"reject_code_contract_version": None}, {"external_version": "x"}):
        repo = repository()
        with pytest.raises(ValueError, match="contract mismatch"):
            await persist(
                tmp_path,
                acquisition,
                receipt_path=receipt_file(tmp_path),
                repo=repo,
                metadata=replace(metadata, **change),
            )
        repo.create_run.assert_not_awaited()


async def test_acquisition_requires_the_source_lock_before_any_provider_call(tmp_path):
    gate = AsyncMock()

    await acquire(tmp_path, [envelope([product("P-001")])], gate=gate)

    gate.try_lock_acquisition.assert_awaited_once_with(PRODUCT_IDENTITY)


@pytest.mark.parametrize("path", PRODUCTION_SOURCES, ids=lambda path: path.name)
def test_production_sources_never_hard_code_the_actual_run_size(path):
    """실측 42,992건·430페이지를 영구 invariant로 굳히지 않는다."""
    module = ast.parse(path.read_text(encoding="utf-8"))
    literals = {
        node.value for node in ast.walk(module) if isinstance(node, ast.Constant) and isinstance(node.value, int)
    }

    assert ACTUAL_PROBE_RECORD_COUNT not in literals
    assert ACTUAL_PROBE_PAGE_COUNT not in literals


@pytest.mark.parametrize("path", PRODUCTION_SOURCES, ids=lambda path: path.name)
def test_production_sources_never_default_to_the_repository_receipt(path):
    """Receipt 경로는 호출자가 지정한다. 저장소 Receipt를 기본값으로 참조하지 않는다."""
    module = ast.parse(path.read_text(encoding="utf-8"))
    strings = {
        node.value for node in ast.walk(module) if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert not any("docs/validation/rag/endpoints" in value for value in strings)
    assert not any("LIST_APPROVED_PRODUCTS.json" in value for value in strings)


def test_the_repository_receipt_is_stale_for_the_current_endpoint():
    """저장소 Receipt는 과거 42,989건 기준이다. 이 사실이 바뀌면 이 테스트가 알려준다."""
    payload = json.loads(REPOSITORY_RECEIPT.read_text())

    assert payload["validated_record_count"] == 42_989
    assert payload["validated_record_count"] != ACTUAL_PROBE_RECORD_COUNT
