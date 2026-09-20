"""Existing MFDS product objects -> existing Source lifecycle, without acquisition."""

import hashlib
import json
import socket
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ai_worker.admin.mfds_product_rematerialization import (
    BLOCKED_BY_PRODUCT_ARTIFACT,
    BLOCKED_BY_PRODUCT_SOURCE_HIERARCHY_CONFLICT,
    ENDPOINT_VALUES,
    OPERATION_VALUES,
    SOURCE_VALUES,
    ExistingProductRawArtifactStore,
    MfdsProductRematerializationConfig,
    ProductRematerializationBlockedError,
    ensure_product_source_hierarchy,
    load_product_artifact_manifest,
    prepare_product_rematerialization,
)
from ai_worker.tasks.rag.source_ingestion.artifacts import RawArtifactMetadata
from ai_worker.tasks.rag.source_ingestion.checksums import product_canonical_checksum, raw_manifest_checksum
from ai_worker.tasks.rag.source_ingestion.mfds_product import build_product_snapshot_metadata
from ai_worker.tasks.rag.source_ingestion.receipt_validation import calculate_endpoint_receipt_hash

from .test_mfds_product import REPOSITORY_RECEIPT, envelope, product


class _Reader:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.requested: list[str] = []

    def read_verified(self, *, object_key, metadata):
        self.requested.append(object_key)
        content = self.objects[object_key]
        if hashlib.sha256(content).hexdigest() != metadata.raw_checksum or len(content) != metadata.byte_size:
            raise ValueError("artifact integrity")
        return content


class _Result:
    def __init__(self, *, row=None, scalar=None) -> None:
        self.row = row
        self.scalar = scalar

    def mappings(self):
        return self

    def one_or_none(self):
        return self.row

    def scalar_one_or_none(self):
        return self.scalar


def _receipt(tmp_path: Path, count: int) -> Path:
    payload = json.loads(REPOSITORY_RECEIPT.read_text())
    payload["validated_record_count"] = count
    fixtures = []
    for index, (scenario, content) in enumerate(
        (
            ("LIST_APPROVED_PRODUCTS_SUCCESS", envelope([product()])),
            ("SYNTHETIC_AUTH_FAILURE", envelope([], code="30")),
            ("SYNTHETIC_DAILY_LIMIT", envelope([], code="22")),
            ("SYNTHETIC_EMPTY", envelope([])),
            ("SYNTHETIC_SCHEMA_DRIFT", {"invalid": True}),
        )
    ):
        relative = f"tests/fixtures/rag/mfds/rematerialization-product-{index}.json"
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content))
        fixtures.append(
            {"scenario": scenario, "path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        )
    payload["fixture_evidence"] = fixtures
    payload["receipt_hash"] = calculate_endpoint_receipt_hash(payload)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(payload))
    return path


def _manifest(tmp_path: Path, *, rows=None):
    rows = rows or [product("P-001"), product("P-002")]
    pages = [envelope([row], page=index, total=len(rows)) for index, row in enumerate(rows, 1)]
    objects = {}
    artifacts = []
    metadata = []
    for page_number, page in enumerate(pages, 1):
        content = json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()
        checksum = hashlib.sha256(content).hexdigest()
        object_key = f"sha256/{checksum[:2]}/{checksum}.artifact"
        artifact_key = f"product-page-{page_number}.json"
        objects[object_key] = content
        object_path = tmp_path / "artifacts" / object_key
        object_path.parent.mkdir(parents=True, exist_ok=True)
        object_path.write_bytes(content)
        artifact = {
            "page_number": page_number,
            "artifact_key": artifact_key,
            "object_key": object_key,
            "raw_checksum": checksum,
            "byte_size": len(content),
            "content_type": "application/json",
        }
        artifacts.append(artifact)
        metadata.append(RawArtifactMetadata(artifact_key, checksum, len(content), "application/json"))
    canonical = product_canonical_checksum(rows)
    receipt = _receipt(tmp_path, len(rows))
    finished = "2026-09-19T03:34:30.340915Z"
    payload = {
        "schema": "mfds-product-rematerialization-manifest@1",
        "source_code": "MFDS_PRODUCT_APPROVAL",
        "endpoint_code": "MFDS_PRODUCT_APPROVAL_API",
        "operation_code": "LIST_APPROVED_PRODUCTS",
        "source_version": f"api:{finished}:{canonical}",
        "canonical_checksum": canonical,
        "raw_manifest_checksum": raw_manifest_checksum(metadata),
        "endpoint_receipt_hash": json.loads(receipt.read_text())["receipt_hash"],
        "record_count": len(rows),
        "started_at": "2026-09-19T03:31:03.856732Z",
        "finished_at": finished,
        "artifacts": artifacts,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload))
    return path, receipt, objects, payload


def test_config_uses_only_writer_and_read_only_artifact_root(tmp_path):
    env = {
        "SOURCE_WRITER_HOST": "db",
        "SOURCE_WRITER_PORT": "5432",
        "SOURCE_WRITER_NAME": "health",
        "SOURCE_WRITER_USER": "source_writer",
        "SOURCE_WRITER_PASSWORD": "synthetic",
        "SOURCE_WRITER_ACTOR": "issue-800",
        "SOURCE_ARTIFACT_READER_ROOT": str(tmp_path.resolve()),
    }
    config = MfdsProductRematerializationConfig.from_environment(env)
    assert config.artifact_reader_root == tmp_path.resolve()
    assert "RAG_MFDS_API_KEY" not in env

    with pytest.raises(ValueError, match="provider credential"):
        MfdsProductRematerializationConfig.from_environment({**env, "RAG_MFDS_API_KEY": "forbidden"})


def test_prepare_revalidates_existing_objects_without_scanning_or_rewriting(tmp_path):
    manifest, receipt, objects, payload = _manifest(tmp_path)
    reader = _Reader(objects)

    prepared = prepare_product_rematerialization(
        manifest_path=manifest,
        receipt_path=receipt,
        repository_root=tmp_path,
        artifact_root=tmp_path / "artifacts",
        reader=reader,
    )

    assert prepared.acquisition.result.record_count == payload["record_count"]
    assert prepared.acquisition.result.snapshot_candidate_allowed
    assert prepared.metadata == build_product_snapshot_metadata(
        acquisition=prepared.acquisition,
        run_group_key=prepared.metadata.run_group_key,
        verified_by=prepared.metadata.verified_by,
    )
    assert prepared.metadata.source_version == payload["source_version"]
    assert reader.requested == [entry["object_key"] for entry in payload["artifacts"]]
    assert isinstance(prepared.artifact_store, ExistingProductRawArtifactStore)
    stored = prepared.artifact_store.put_verified(
        page_number=1,
        file_path=prepared.acquisition.artifacts[0][1],
        metadata=prepared.acquisition.artifacts[0][2],
    )
    assert stored.object_key == payload["artifacts"][0]["object_key"]


def test_prepare_makes_zero_network_calls(tmp_path, monkeypatch):
    manifest, receipt, objects, _ = _manifest(tmp_path)

    def reject_network(*_args, **_kwargs):
        raise AssertionError("network call is forbidden")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    prepared = prepare_product_rematerialization(
        manifest_path=manifest,
        receipt_path=receipt,
        repository_root=tmp_path,
        artifact_root=tmp_path / "artifacts",
        reader=_Reader(objects),
    )
    assert prepared.acquisition.result.snapshot_candidate_allowed


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("record_count", 3),
        ("canonical_checksum", "a" * 64),
        ("raw_manifest_checksum", "b" * 64),
        ("source_version", "api:2026-09-19T03:34:30.340915Z:" + "c" * 64),
        ("endpoint_receipt_hash", "d" * 64),
    ],
)
def test_prepare_fails_closed_on_run_level_parity_mismatch(tmp_path, field, value):
    manifest, receipt, objects, payload = _manifest(tmp_path)
    payload[field] = value
    manifest.write_text(json.dumps(payload))

    with pytest.raises(ProductRematerializationBlockedError) as caught:
        prepare_product_rematerialization(
            manifest_path=manifest,
            receipt_path=receipt,
            repository_root=tmp_path,
            artifact_root=tmp_path / "artifacts",
            reader=_Reader(objects),
        )
    assert caught.value.code == BLOCKED_BY_PRODUCT_ARTIFACT


@pytest.mark.parametrize("rows", [[product("P-001"), {}], [product("P-001"), product("P-001")]])
def test_prepare_reuses_identity_classifier_and_rejects_null_or_duplicate_keys(tmp_path, rows):
    manifest, receipt, objects, payload = _manifest(tmp_path)
    metadata = []
    objects.clear()
    for artifact, row in zip(payload["artifacts"], rows, strict=True):
        page = artifact["page_number"]
        content = json.dumps(envelope([row], page=page, total=2), separators=(",", ":")).encode()
        checksum = hashlib.sha256(content).hexdigest()
        object_key = f"sha256/{checksum[:2]}/{checksum}.artifact"
        artifact.update(object_key=object_key, raw_checksum=checksum, byte_size=len(content))
        objects[object_key] = content
        path = tmp_path / "artifacts" / object_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        metadata.append(RawArtifactMetadata(artifact["artifact_key"], checksum, len(content), "application/json"))
    payload["raw_manifest_checksum"] = raw_manifest_checksum(metadata)
    manifest.write_text(json.dumps(payload))

    with pytest.raises(ProductRematerializationBlockedError):
        prepare_product_rematerialization(
            manifest_path=manifest,
            receipt_path=receipt,
            repository_root=tmp_path,
            artifact_root=tmp_path / "artifacts",
            reader=_Reader(objects),
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["artifacts"][0].update(object_key="../escape"),
        lambda payload: payload["artifacts"][1].update(page_number=3, artifact_key="product-page-3.json"),
        lambda payload: payload["artifacts"][1].update(page_number=1, artifact_key="product-page-1.json"),
        lambda payload: payload["artifacts"][0].update(content_type="text/plain"),
    ],
)
def test_manifest_rejects_unsafe_or_unbound_artifact_coordinates(tmp_path, mutate):
    manifest, _, _, payload = _manifest(tmp_path)
    mutate(payload)
    manifest.write_text(json.dumps(payload))

    with pytest.raises(ProductRematerializationBlockedError):
        load_product_artifact_manifest(manifest)


def test_byte_size_mismatch_and_decoded_page_mismatch_fail_closed(tmp_path):
    manifest, receipt, objects, payload = _manifest(tmp_path)
    payload["artifacts"][0]["byte_size"] += 1
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ProductRematerializationBlockedError) as caught:
        prepare_product_rematerialization(
            manifest_path=manifest,
            receipt_path=receipt,
            repository_root=tmp_path,
            artifact_root=tmp_path / "artifacts",
            reader=_Reader(objects),
        )
    assert caught.value.code == BLOCKED_BY_PRODUCT_ARTIFACT

    manifest, receipt, objects, payload = _manifest(tmp_path)
    entry = payload["artifacts"][0]
    content = json.dumps(envelope([product("P-001")], page=99, total=2), separators=(",", ":")).encode()
    checksum = hashlib.sha256(content).hexdigest()
    object_key = f"sha256/{checksum[:2]}/{checksum}.artifact"
    entry.update(object_key=object_key, raw_checksum=checksum, byte_size=len(content))
    objects[object_key] = content
    object_path = tmp_path / "artifacts" / object_key
    object_path.parent.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(content)
    payload["raw_manifest_checksum"] = raw_manifest_checksum(
        RawArtifactMetadata(item["artifact_key"], item["raw_checksum"], item["byte_size"], item["content_type"])
        for item in payload["artifacts"]
    )
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ProductRematerializationBlockedError) as caught:
        prepare_product_rematerialization(
            manifest_path=manifest,
            receipt_path=receipt,
            repository_root=tmp_path,
            artifact_root=tmp_path / "artifacts",
            reader=_Reader(objects),
        )
    assert caught.value.code == BLOCKED_BY_PRODUCT_ARTIFACT


def test_manifest_targets_430_without_scanning_486_object_root(tmp_path):
    rows = [product(f"P-{number:06d}") for number in range(1, 431)]
    manifest, receipt, objects, _ = _manifest(tmp_path, rows=rows)
    for number in range(56):
        objects[f"unrelated-{number}"] = b"not part of target run"
    reader = _Reader(objects)

    prepared = prepare_product_rematerialization(
        manifest_path=manifest,
        receipt_path=receipt,
        repository_root=tmp_path,
        artifact_root=tmp_path / "artifacts",
        reader=reader,
    )

    assert len(objects) == 486
    assert len(prepared.acquisition.artifacts) == 430
    assert len(reader.requested) == 430


async def test_hierarchy_absent_is_created_with_frozen_values():
    session = AsyncMock()
    session.execute.side_effect = [
        _Result(row=None),
        _Result(scalar="00000000-0000-4000-8000-000000000001"),
        _Result(row=None),
        _Result(scalar="00000000-0000-4000-8000-000000000002"),
        _Result(row=None),
        _Result(scalar="00000000-0000-4000-8000-000000000003"),
    ]

    hierarchy = await ensure_product_source_hierarchy(session)

    assert str(hierarchy.operation_id).endswith("3")
    assert session.execute.await_count == 6


async def test_hierarchy_exact_match_is_reused_without_insert():
    session = AsyncMock()
    session.execute.side_effect = [
        _Result(row={"id": "00000000-0000-4000-8000-000000000001", **SOURCE_VALUES}),
        _Result(
            row={
                "id": "00000000-0000-4000-8000-000000000002",
                "source_id": "00000000-0000-4000-8000-000000000001",
                **ENDPOINT_VALUES,
            }
        ),
        _Result(
            row={
                "id": "00000000-0000-4000-8000-000000000003",
                "endpoint_id": "00000000-0000-4000-8000-000000000002",
                **OPERATION_VALUES,
            }
        ),
    ]

    hierarchy = await ensure_product_source_hierarchy(session)

    assert str(hierarchy.source_id).endswith("1")
    assert session.execute.await_count == 3


async def test_hierarchy_mismatch_fails_without_update():
    session = AsyncMock()
    session.execute.return_value = _Result(
        row={"id": "00000000-0000-4000-8000-000000000001", **SOURCE_VALUES, "display_name": "wrong"}
    )

    with pytest.raises(ProductRematerializationBlockedError) as caught:
        await ensure_product_source_hierarchy(session)

    assert caught.value.code == BLOCKED_BY_PRODUCT_SOURCE_HIERARCHY_CONFLICT
    assert session.execute.await_count == 1
