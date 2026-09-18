"""Unit and contract tests for RET-H AWS synthetic smoke bootstrap stages (#684)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ai_worker.tasks.evaluation.actual_retrieval_index import SYNTHETIC_INDEX_CODE
from ai_worker.tasks.evaluation.errors import EvaluationValidationError
from ai_worker.tasks.evaluation.resources import (
    DEFAULT_SMOKE_FIXTURE_PATH,
    RetHSmokeRuntimeProvenance,
    build_ret_h_smoke_fixture_manifest,
    load_ret_h_smoke_synthetic_fixture,
)
from ai_worker.tasks.evaluation.ret_h_bootstrap_stages import (
    RET_H_SMOKE_ENDPOINT_CODE,
    RET_H_SMOKE_INDEX_CODE,
    RET_H_SMOKE_INDEX_VERSION,
    RET_H_SMOKE_OPERATION_CODE,
    RET_H_SMOKE_SOURCE_CODE,
    Stage1SourceReceipt,
    Stage2IndexReceipt,
    bootstrap_ret_h_smoke_stage2_knowledge_index,
    generate_ret_h_smoke_fixture_manifest,
    stage1_receipt_to_dict,
    stage2_receipt_to_dict,
)
from ai_worker.tasks.evaluation.ret_h_smoke import (
    APPROVED_SYNTHETIC_INDEX_CODES,
    verify_fixture_is_synthetic,
)
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from backend.app.release_validation.ret_h_synthetic_smoke import (
    sentinels_from_fixture,
    verify_query_sentinel_binding,
)
from scripts.ret_h_aws_synthetic_smoke import _build_hybrid_retrieve_request, _load_fixture


def test_default_smoke_fixture_file_exists_and_loads() -> None:
    assert DEFAULT_SMOKE_FIXTURE_PATH.is_file(), f"Fixture file not found: {DEFAULT_SMOKE_FIXTURE_PATH}"
    fixture = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)

    assert fixture.source_code == RET_H_SMOKE_SOURCE_CODE
    assert fixture.endpoint_code == RET_H_SMOKE_ENDPOINT_CODE
    assert fixture.operation_code == RET_H_SMOKE_OPERATION_CODE
    assert fixture.index_code == RET_H_SMOKE_INDEX_CODE
    assert fixture.index_version == RET_H_SMOKE_INDEX_VERSION

    assert len(fixture.query_sentinel) >= 16
    assert len(fixture.source_sentinel) >= 16
    assert fixture.query_sentinel != fixture.source_sentinel
    assert fixture.query_sentinel in fixture.synthetic_query
    assert fixture.source_sentinel in fixture.records[0].statement

    assert len(fixture.records) == 1
    statement_hash = hashlib.sha256(fixture.records[0].statement.encode("utf-8")).hexdigest()
    assert fixture.records[0].content_sha256 == statement_hash

    query_hash = hashlib.sha256(fixture.synthetic_query.encode("utf-8")).hexdigest()
    assert fixture.synthetic_query_sha256 == query_hash


def test_fixture_schema_rejects_missing_sentinels(tmp_path: Path) -> None:
    bad_fixture = {
        "source_code": RET_H_SMOKE_SOURCE_CODE,
        "endpoint_code": RET_H_SMOKE_ENDPOINT_CODE,
        "operation_code": RET_H_SMOKE_OPERATION_CODE,
        "index_code": RET_H_SMOKE_INDEX_CODE,
        "index_version": RET_H_SMOKE_INDEX_VERSION,
        "query_sentinel": "",
        "source_sentinel": "RET_H_SMOKE_S_valid12345678",
        "synthetic_query": "query",
        "synthetic_query_sha256": hashlib.sha256(b"query").hexdigest(),
        "records": [
            {
                "evidence_ref_id": "SYN-1",
                "statement": "RET_H_SMOKE_S_valid12345678 statement",
                "content_sha256": hashlib.sha256(b"RET_H_SMOKE_S_valid12345678 statement").hexdigest(),
            }
        ],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad_fixture), encoding="utf-8")
    with pytest.raises(EvaluationValidationError, match="query_sentinel"):
        load_ret_h_smoke_synthetic_fixture(p)


def test_fixture_schema_rejects_identical_sentinels(tmp_path: Path) -> None:
    shared_sentinel = "RET_H_SMOKE_SHARED_12345678"
    bad_fixture = {
        "source_code": RET_H_SMOKE_SOURCE_CODE,
        "endpoint_code": RET_H_SMOKE_ENDPOINT_CODE,
        "operation_code": RET_H_SMOKE_OPERATION_CODE,
        "index_code": RET_H_SMOKE_INDEX_CODE,
        "index_version": RET_H_SMOKE_INDEX_VERSION,
        "query_sentinel": shared_sentinel,
        "source_sentinel": shared_sentinel,
        "synthetic_query": f"{shared_sentinel} query",
        "synthetic_query_sha256": hashlib.sha256(f"{shared_sentinel} query".encode()).hexdigest(),
        "records": [
            {
                "evidence_ref_id": "SYN-1",
                "statement": f"{shared_sentinel} statement",
                "content_sha256": hashlib.sha256(f"{shared_sentinel} statement".encode()).hexdigest(),
            }
        ],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad_fixture), encoding="utf-8")
    with pytest.raises(EvaluationValidationError, match="sentinels_distinct"):
        load_ret_h_smoke_synthetic_fixture(p)


def test_fixture_schema_rejects_multiple_records(tmp_path: Path) -> None:
    q_sent = "RET_H_SMOKE_Q_12345678"
    s_sent = "RET_H_SMOKE_S_12345678"
    bad_fixture = {
        "source_code": RET_H_SMOKE_SOURCE_CODE,
        "endpoint_code": RET_H_SMOKE_ENDPOINT_CODE,
        "operation_code": RET_H_SMOKE_OPERATION_CODE,
        "index_code": RET_H_SMOKE_INDEX_CODE,
        "index_version": RET_H_SMOKE_INDEX_VERSION,
        "query_sentinel": q_sent,
        "source_sentinel": s_sent,
        "synthetic_query": f"{q_sent} query",
        "synthetic_query_sha256": hashlib.sha256(f"{q_sent} query".encode()).hexdigest(),
        "records": [
            {
                "evidence_ref_id": "SYN-1",
                "statement": f"{s_sent} stmt 1",
                "content_sha256": hashlib.sha256(f"{s_sent} stmt 1".encode()).hexdigest(),
            },
            {
                "evidence_ref_id": "SYN-2",
                "statement": f"{s_sent} stmt 2",
                "content_sha256": hashlib.sha256(f"{s_sent} stmt 2".encode()).hexdigest(),
            },
        ],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad_fixture), encoding="utf-8")
    with pytest.raises(EvaluationValidationError, match="records_count"):
        load_ret_h_smoke_synthetic_fixture(p)


def test_fixture_schema_rejects_unembedded_sentinels(tmp_path: Path) -> None:
    q_sent = "RET_H_SMOKE_Q_12345678"
    s_sent = "RET_H_SMOKE_S_12345678"
    bad_fixture = {
        "source_code": RET_H_SMOKE_SOURCE_CODE,
        "endpoint_code": RET_H_SMOKE_ENDPOINT_CODE,
        "operation_code": RET_H_SMOKE_OPERATION_CODE,
        "index_code": RET_H_SMOKE_INDEX_CODE,
        "index_version": RET_H_SMOKE_INDEX_VERSION,
        "query_sentinel": q_sent,
        "source_sentinel": s_sent,
        "synthetic_query": "query without sentinel",
        "synthetic_query_sha256": hashlib.sha256(b"query without sentinel").hexdigest(),
        "records": [
            {
                "evidence_ref_id": "SYN-1",
                "statement": f"{s_sent} stmt 1",
                "content_sha256": hashlib.sha256(f"{s_sent} stmt 1".encode()).hexdigest(),
            }
        ],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad_fixture), encoding="utf-8")
    with pytest.raises(EvaluationValidationError, match="synthetic_query_sentinel"):
        load_ret_h_smoke_synthetic_fixture(p)


def test_fixture_schema_rejects_length_violation(tmp_path: Path) -> None:
    q_sent = "RET_H_SMOKE_Q_12345678"
    s_sent = "RET_H_SMOKE_S_12345678"
    bad_fixture = {
        "source_code": "X" * 101,  # limit is 100
        "endpoint_code": RET_H_SMOKE_ENDPOINT_CODE,
        "operation_code": RET_H_SMOKE_OPERATION_CODE,
        "index_code": RET_H_SMOKE_INDEX_CODE,
        "index_version": RET_H_SMOKE_INDEX_VERSION,
        "query_sentinel": q_sent,
        "source_sentinel": s_sent,
        "synthetic_query": f"{q_sent} query",
        "synthetic_query_sha256": hashlib.sha256(f"{q_sent} query".encode()).hexdigest(),
        "records": [
            {
                "evidence_ref_id": "SYN-1",
                "statement": f"{s_sent} stmt 1",
                "content_sha256": hashlib.sha256(f"{s_sent} stmt 1".encode()).hexdigest(),
            }
        ],
    }
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad_fixture), encoding="utf-8")
    with pytest.raises(EvaluationValidationError, match="source_code"):
        load_ret_h_smoke_synthetic_fixture(p)


def test_synthetic_allowlist_contains_ret_h_smoke_index() -> None:
    assert RET_H_SMOKE_INDEX_CODE in APPROVED_SYNTHETIC_INDEX_CODES
    assert SYNTHETIC_INDEX_CODE in APPROVED_SYNTHETIC_INDEX_CODES
    assert len(APPROVED_SYNTHETIC_INDEX_CODES) == 2


@pytest.mark.asyncio
async def test_verify_fixture_is_synthetic_exact_matching() -> None:
    session = AsyncMock()
    # Mocking query result for non-approved index
    row_mock = MagicMock()
    row_mock.index_code = "rag-ret-h-aws-smoke-synthetic-index-malicious"
    row_mock.index_version = "1.0.0"

    result_mock = MagicMock()
    result_mock.one_or_none.return_value = row_mock
    session.execute.return_value = result_mock

    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session

    ok, msg = await verify_fixture_is_synthetic(
        session_factory=session_factory,
        knowledge_index_id=uuid4(),
        allowed_source_snapshot_ids=(uuid4(),),
    )
    assert not ok
    assert "not an approved synthetic index" in msg


@pytest.mark.asyncio
async def test_stage2_preflight_fails_closed_when_snapshot_unsealed() -> None:
    fixture = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    embedding_port = AsyncMock()

    session = AsyncMock()
    # Snapshot query returns verification_status != 'CURRENT'
    snap_result = MagicMock()
    snap_result.mappings.return_value.one_or_none.return_value = {
        "id": str(uuid4()),
        "verification_status": "PENDING",  # NOT CURRENT!
        "verification_seal_id": None,
        "canonical_checksum": fixture.file_sha256,
    }
    session.execute.return_value = snap_result

    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session

    with pytest.raises(EvaluationValidationError, match="unsealed"):
        await bootstrap_ret_h_smoke_stage2_knowledge_index(
            session_factory=session_factory,
            embedding_port=embedding_port,
            fixture=fixture,
            stage1_snapshot_id=uuid4(),
        )

    # Invariant: 0 embedding calls
    assert embedding_port.embed_text.call_count == 0


@pytest.mark.asyncio
async def test_stage2_preflight_fails_closed_when_checksum_mismatch() -> None:
    fixture = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    embedding_port = AsyncMock()

    session = AsyncMock()
    snap_result = MagicMock()
    snap_result.mappings.return_value.one_or_none.return_value = {
        "id": str(uuid4()),
        "verification_status": "CURRENT",
        "verification_seal_id": str(uuid4()),
        "canonical_checksum": "0" * 64,  # MISMATCH!
    }
    session.execute.return_value = snap_result

    session_factory = MagicMock()
    session_factory.return_value.__aenter__.return_value = session

    with pytest.raises(EvaluationValidationError, match="canonical_checksum"):
        await bootstrap_ret_h_smoke_stage2_knowledge_index(
            session_factory=session_factory,
            embedding_port=embedding_port,
            fixture=fixture,
            stage1_snapshot_id=uuid4(),
        )

    assert embedding_port.embed_text.call_count == 0


def _make_sample_receipts(fixture_input: Any) -> tuple[Stage1SourceReceipt, Stage2IndexReceipt, dict[str, Any]]:
    source_id = uuid4()
    endpoint_id = uuid4()
    operation_id = uuid4()
    snapshot_id = uuid4()
    member_id = uuid4()
    seal_id = uuid4()
    index_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()
    index_member_id = uuid4()
    job_id = uuid4()
    ctx_id = uuid4()
    prescription_id = uuid4()

    stage1 = Stage1SourceReceipt(
        source_id=source_id,
        endpoint_id=endpoint_id,
        operation_id=operation_id,
        snapshot_id=snapshot_id,
        member_ids=(member_id,),
        canonical_checksum=fixture_input.file_sha256,
        verification_seal_id=seal_id,
        reused=False,
    )
    stage2 = Stage2IndexReceipt(
        knowledge_index_id=index_id,
        index_code=RET_H_SMOKE_INDEX_CODE,
        index_version=RET_H_SMOKE_INDEX_VERSION,
        index_configuration_hash="a" * 64,
        document_id=doc_id,
        chunk_id=chunk_id,
        member_id=index_member_id,
        reused=False,
    )
    ids = {
        "job_id": job_id,
        "ctx_id": ctx_id,
        "prescription_id": prescription_id,
        "snapshot_id": snapshot_id,
        "member_id": member_id,
        "index_id": index_id,
    }
    return stage1, stage2, ids


def _valid_pinned_provenance() -> dict[str, Any]:
    return {
        "lexical_config_ref": ImmutableArtifactRef("lexical-search-config", "1.0.0", "1" * 64),
        "dense_config_ref": ImmutableArtifactRef("dense-search-config", "1.0.0", "2" * 64),
        "retrieval_config_ref": ImmutableArtifactRef("retrieval-config", "1.0.0", "3" * 64),
        "filter_snapshot_ref": ImmutableArtifactRef("filter-snapshot", "1.0.0", "4" * 64),
        "search_adapter_ref": ImmutableArtifactRef("postgresql-evidence-search-adapter", "1.0.0", "5" * 64),
        "embedding_adapter_ref": ImmutableArtifactRef("openai-text-embedding-adapter", "1.0.0", "6" * 64),
        "runtime_release_bundle_id": uuid4(),
        "runtime_release_bundle_manifest_hash": "7" * 64,
        "runtime_execution_manifest_id": uuid4(),
        "runtime_execution_manifest_hash": "8" * 64,
        "runtime_guard_decision_ref": "ret-h-aws-synthetic-smoke",
    }


def test_manifest_fails_when_required_provenance_missing() -> None:
    """1. required provenance 누락 -> manifest 생성 실패 (fail-closed)."""
    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    stage1, stage2, ids = _make_sample_receipts(fixture_input)

    # Calling with no provenance arguments at all must fail closed
    with pytest.raises(ValueError, match="lexical_config_ref is required"):
        build_ret_h_smoke_fixture_manifest(
            fixture_input=fixture_input,
            stage1_receipt=stage1,
            stage2_receipt=stage2,
            job_id=ids["job_id"],
            execution_context_id=ids["ctx_id"],
            prescription_version_id=ids["prescription_id"],
        )

    # Calling with any one required field missing/None must fail closed
    base_prov = _valid_pinned_provenance()
    for key in base_prov:
        prov_copy = dict(base_prov)
        prov_copy[key] = None
        with pytest.raises((ValueError, TypeError), match=key):
            build_ret_h_smoke_fixture_manifest(
                fixture_input=fixture_input,
                stage1_receipt=stage1,
                stage2_receipt=stage2,
                job_id=ids["job_id"],
                execution_context_id=ids["ctx_id"],
                prescription_version_id=ids["prescription_id"],
                **prov_copy,
            )


def test_manifest_rejects_placeholder_zero_hash_and_dummy_guard() -> None:
    """2. placeholder zero hash 및 dummy guard decision ref -> 거부."""
    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    stage1, stage2, ids = _make_sample_receipts(fixture_input)
    base_prov = _valid_pinned_provenance()

    # Placeholder "0"*64 in artifact ref sha
    p1 = dict(base_prov)
    p1["lexical_config_ref"] = ImmutableArtifactRef("lexical-search-config", "1.0.0", "0" * 64)
    with pytest.raises(ValueError, match="placeholder zero hash"):
        build_ret_h_smoke_fixture_manifest(
            fixture_input=fixture_input,
            stage1_receipt=stage1,
            stage2_receipt=stage2,
            job_id=ids["job_id"],
            execution_context_id=ids["ctx_id"],
            prescription_version_id=ids["prescription_id"],
            **p1,
        )

    # Placeholder "0"*64 in bundle manifest hash
    p2 = dict(base_prov)
    p2["runtime_release_bundle_manifest_hash"] = "0" * 64
    with pytest.raises(ValueError, match="placeholder zero hash"):
        build_ret_h_smoke_fixture_manifest(
            fixture_input=fixture_input,
            stage1_receipt=stage1,
            stage2_receipt=stage2,
            job_id=ids["job_id"],
            execution_context_id=ids["ctx_id"],
            prescription_version_id=ids["prescription_id"],
            **p2,
        )

    # Placeholder "0"*64 in execution manifest hash
    p3 = dict(base_prov)
    p3["runtime_execution_manifest_hash"] = "0" * 64
    with pytest.raises(ValueError, match="placeholder zero hash"):
        build_ret_h_smoke_fixture_manifest(
            fixture_input=fixture_input,
            stage1_receipt=stage1,
            stage2_receipt=stage2,
            job_id=ids["job_id"],
            execution_context_id=ids["ctx_id"],
            prescription_version_id=ids["prescription_id"],
            **p3,
        )

    # Dummy placeholder guard decision ref "ALLOW"
    p4 = dict(base_prov)
    p4["runtime_guard_decision_ref"] = "ALLOW"
    with pytest.raises(ValueError, match="placeholder 'ALLOW'"):
        build_ret_h_smoke_fixture_manifest(
            fixture_input=fixture_input,
            stage1_receipt=stage1,
            stage2_receipt=stage2,
            job_id=ids["job_id"],
            execution_context_id=ids["ctx_id"],
            prescription_version_id=ids["prescription_id"],
            **p4,
        )

    # Empty guard decision ref
    p5 = dict(base_prov)
    p5["runtime_guard_decision_ref"] = ""
    with pytest.raises(ValueError, match="runtime_guard_decision_ref is required"):
        build_ret_h_smoke_fixture_manifest(
            fixture_input=fixture_input,
            stage1_receipt=stage1,
            stage2_receipt=stage2,
            job_id=ids["job_id"],
            execution_context_id=ids["ctx_id"],
            prescription_version_id=ids["prescription_id"],
            **p5,
        )


def test_manifest_with_pinned_refs_round_trips_fixture_loader() -> None:
    """3. 실제 pinned refs -> #683 fixture loader round-trip PASS."""
    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    stage1, stage2, ids = _make_sample_receipts(fixture_input)
    provenance = RetHSmokeRuntimeProvenance(**_valid_pinned_provenance())

    manifest = build_ret_h_smoke_fixture_manifest(
        fixture_input=fixture_input,
        stage1_receipt=stage1,
        stage2_receipt=stage2,
        job_id=ids["job_id"],
        execution_context_id=ids["ctx_id"],
        prescription_version_id=ids["prescription_id"],
        provenance=provenance,
    )

    # 1. JSON serializable round-trip
    serialized = json.dumps(manifest)
    deserialized = json.loads(serialized)

    # 2. Sentinels extraction succeeds
    sentinels = sentinels_from_fixture(deserialized)
    assert sentinels is not None
    assert sentinels.query_sentinel == fixture_input.query_sentinel
    assert sentinels.source_sentinel == fixture_input.source_sentinel

    # 3. Query sentinel binding succeeds
    check_result = verify_query_sentinel_binding(
        synthetic_query=deserialized["synthetic_query"],
        sentinels=sentinels,
        approved_query_sha256=deserialized["synthetic_query_sha256"],
    )
    assert check_result.passed

    # 4. _build_hybrid_retrieve_request from scripts/ret_h_aws_synthetic_smoke.py succeeds
    req = _build_hybrid_retrieve_request(deserialized)
    assert req.job_id == ids["job_id"]
    assert req.execution_context_id == ids["ctx_id"]
    assert req.prescription_version_id == ids["prescription_id"]
    assert req.search_request.execution_binding.knowledge_index_id == ids["index_id"]
    assert req.search_request.execution_binding.allowed_source_snapshot_ids == (ids["snapshot_id"],)
    assert req.search_request.execution_binding.allowed_source_snapshot_member_ids == (ids["member_id"],)


def test_execution_request_refs_exact_match_manifest() -> None:
    """4. execution request의 config/adaptor refs와 manifest refs exact-match."""
    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    stage1, stage2, ids = _make_sample_receipts(fixture_input)
    prov_kwargs = _valid_pinned_provenance()

    manifest = build_ret_h_smoke_fixture_manifest(
        fixture_input=fixture_input,
        stage1_receipt=stage1,
        stage2_receipt=stage2,
        job_id=ids["job_id"],
        execution_context_id=ids["ctx_id"],
        prescription_version_id=ids["prescription_id"],
        **prov_kwargs,
    )

    req = _build_hybrid_retrieve_request(manifest)
    binding = req.search_request.execution_binding
    ret_config = binding.retrieval_config

    # Exact matching of all config and adapter references
    assert ret_config.lexical_config.artifact_ref.artifact_code == manifest["lexical_config_ref"]["artifact_code"]
    assert ret_config.lexical_config.artifact_ref.version == manifest["lexical_config_ref"]["version"]
    assert ret_config.lexical_config.artifact_ref.content_sha256 == manifest["lexical_config_ref"]["content_sha256"]

    assert ret_config.dense_config is not None
    assert ret_config.dense_config.artifact_ref.artifact_code == manifest["dense_config_ref"]["artifact_code"]
    assert ret_config.dense_config.artifact_ref.version == manifest["dense_config_ref"]["version"]
    assert ret_config.dense_config.artifact_ref.content_sha256 == manifest["dense_config_ref"]["content_sha256"]

    assert ret_config.artifact_ref.artifact_code == manifest["retrieval_config_ref"]["artifact_code"]
    assert ret_config.artifact_ref.version == manifest["retrieval_config_ref"]["version"]
    assert ret_config.artifact_ref.content_sha256 == manifest["retrieval_config_ref"]["content_sha256"]

    assert ret_config.expected_query_embedding_adapter_ref is not None
    assert (
        ret_config.expected_query_embedding_adapter_ref.artifact_code
        == manifest["embedding_adapter_ref"]["artifact_code"]
    )
    assert ret_config.expected_query_embedding_adapter_ref.version == manifest["embedding_adapter_ref"]["version"]
    assert (
        ret_config.expected_query_embedding_adapter_ref.content_sha256
        == manifest["embedding_adapter_ref"]["content_sha256"]
    )

    assert binding.filter_snapshot_ref.artifact_code == manifest["filter_snapshot_ref"]["artifact_code"]
    assert binding.filter_snapshot_ref.version == manifest["filter_snapshot_ref"]["version"]
    assert binding.filter_snapshot_ref.content_sha256 == manifest["filter_snapshot_ref"]["content_sha256"]

    assert binding.evidence_index_ref.artifact_code == manifest["evidence_index_ref"]["artifact_code"]
    assert binding.evidence_index_ref.version == manifest["evidence_index_ref"]["version"]
    assert binding.evidence_index_ref.content_sha256 == manifest["evidence_index_ref"]["content_sha256"]

    # Runtime execution manifest identity exact matching
    assert str(req.runtime_release_bundle_id) == manifest["runtime_release_bundle_id"]
    assert req.runtime_release_bundle_manifest_hash == manifest["runtime_release_bundle_manifest_hash"]
    assert str(req.runtime_execution_manifest_id) == manifest["runtime_execution_manifest_id"]
    assert req.runtime_execution_manifest_hash == manifest["runtime_execution_manifest_hash"]
    assert req.runtime_guard_decision_ref == manifest["runtime_guard_decision_ref"]
    assert req.source_manifest_hash == manifest["source_manifest_hash"]


def test_generate_manifest_command_invokes_builder_and_produces_output(tmp_path: Path) -> None:
    """1. production-facing manifest command가 builder를 실제 호출하고 2. output manifest file이 생성된다."""
    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    stage1, stage2, ids = _make_sample_receipts(fixture_input)
    provenance = RetHSmokeRuntimeProvenance(**_valid_pinned_provenance())

    out_file = tmp_path / "smoke_manifest.json"
    written_path = generate_ret_h_smoke_fixture_manifest(
        stage1_receipt=stage1,
        stage2_receipt=stage2,
        job_id=ids["job_id"],
        execution_context_id=ids["ctx_id"],
        prescription_version_id=ids["prescription_id"],
        provenance=provenance,
        output_manifest_path=out_file,
        fixture_input=fixture_input,
    )

    assert written_path == out_file
    assert out_file.is_file()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["knowledge_index_id"] == str(ids["index_id"])
    assert data["allowed_source_snapshot_ids"] == [str(ids["snapshot_id"])]
    assert data["allowed_source_snapshot_member_ids"] == [str(ids["member_id"])]


def test_generate_manifest_command_fails_closed_without_writing_on_missing_provenance(tmp_path: Path) -> None:
    """3. missing provenance는 file을 생성하지 않고 실패한다."""
    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    stage1, stage2, ids = _make_sample_receipts(fixture_input)
    out_file = tmp_path / "should_not_exist.json"

    with pytest.raises((EvaluationValidationError, ValueError)):
        generate_ret_h_smoke_fixture_manifest(
            stage1_receipt=stage1,
            stage2_receipt=stage2,
            job_id=ids["job_id"],
            execution_context_id=ids["ctx_id"],
            prescription_version_id=ids["prescription_id"],
            provenance=None,  # type: ignore[arg-type]
            output_manifest_path=out_file,
            fixture_input=fixture_input,
        )

    assert not out_file.exists(), "Manifest file must not be created on missing provenance"


def test_generate_manifest_command_roundtrips_through_683_fixture_loader(tmp_path: Path) -> None:
    """4. 생성 파일을 #683 fixture loader가 읽는다."""
    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    stage1, stage2, ids = _make_sample_receipts(fixture_input)
    provenance = RetHSmokeRuntimeProvenance(**_valid_pinned_provenance())

    out_file = tmp_path / "smoke_manifest.json"
    generate_ret_h_smoke_fixture_manifest(
        stage1_receipt=stage1,
        stage2_receipt=stage2,
        job_id=ids["job_id"],
        execution_context_id=ids["ctx_id"],
        prescription_version_id=ids["prescription_id"],
        provenance=provenance,
        output_manifest_path=out_file,
        fixture_input=fixture_input,
    )

    loaded = _load_fixture(out_file)
    assert loaded is not None
    assert isinstance(loaded, dict)
    assert loaded["knowledge_index_id"] == str(ids["index_id"])
    assert loaded["source_manifest_hash"] == fixture_input.file_sha256


def test_generate_manifest_command_passes_683_preflight_and_request_reconstruction(tmp_path: Path) -> None:
    """5. 생성 manifest로 #683 synthetic preflight/request reconstruction이 통과한다."""
    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    stage1, stage2, ids = _make_sample_receipts(fixture_input)
    provenance = RetHSmokeRuntimeProvenance(**_valid_pinned_provenance())

    out_file = tmp_path / "smoke_manifest.json"
    generate_ret_h_smoke_fixture_manifest(
        stage1_receipt=stage1,
        stage2_receipt=stage2,
        job_id=ids["job_id"],
        execution_context_id=ids["ctx_id"],
        prescription_version_id=ids["prescription_id"],
        provenance=provenance,
        output_manifest_path=out_file,
        fixture_input=fixture_input,
    )

    loaded = _load_fixture(out_file)
    assert loaded is not None

    # Preflight sentinel extraction and verification
    sentinels = sentinels_from_fixture(loaded)
    assert sentinels is not None
    assert sentinels.query_sentinel == fixture_input.query_sentinel
    assert sentinels.source_sentinel == fixture_input.source_sentinel

    binding_check = verify_query_sentinel_binding(
        synthetic_query=loaded["synthetic_query"],
        sentinels=sentinels,
        approved_query_sha256=loaded["synthetic_query_sha256"],
    )
    assert binding_check.passed

    # Request reconstruction
    req = _build_hybrid_retrieve_request(loaded)
    assert req.job_id == ids["job_id"]
    assert req.execution_context_id == ids["ctx_id"]
    assert req.prescription_version_id == ids["prescription_id"]
    assert req.search_request.execution_binding.knowledge_index_id == ids["index_id"]


def test_generate_manifest_command_rejects_zero_hash_and_allow_placeholder(tmp_path: Path) -> None:
    """6. zero hash / ALLOW placeholder는 계속 거부된다."""
    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    stage1, stage2, ids = _make_sample_receipts(fixture_input)
    out_file = tmp_path / "placeholder_should_not_exist.json"

    # Zero hash
    bad_prov_zero = _valid_pinned_provenance()
    bad_prov_zero["runtime_release_bundle_manifest_hash"] = "0" * 64
    prov_zero = RetHSmokeRuntimeProvenance(**bad_prov_zero)

    with pytest.raises((EvaluationValidationError, ValueError)):
        generate_ret_h_smoke_fixture_manifest(
            stage1_receipt=stage1,
            stage2_receipt=stage2,
            job_id=ids["job_id"],
            execution_context_id=ids["ctx_id"],
            prescription_version_id=ids["prescription_id"],
            provenance=prov_zero,
            output_manifest_path=out_file,
            fixture_input=fixture_input,
        )
    assert not out_file.exists()

    # ALLOW placeholder
    bad_prov_allow = _valid_pinned_provenance()
    bad_prov_allow["runtime_guard_decision_ref"] = "ALLOW"
    prov_allow = RetHSmokeRuntimeProvenance(**bad_prov_allow)

    with pytest.raises((EvaluationValidationError, ValueError)):
        generate_ret_h_smoke_fixture_manifest(
            stage1_receipt=stage1,
            stage2_receipt=stage2,
            job_id=ids["job_id"],
            execution_context_id=ids["ctx_id"],
            prescription_version_id=ids["prescription_id"],
            provenance=prov_allow,
            output_manifest_path=out_file,
            fixture_input=fixture_input,
        )
    assert not out_file.exists()


def test_generate_manifest_cli_execution(tmp_path: Path) -> None:
    """CLI subcommand manifest round-trip execution test."""
    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    stage1, stage2, ids = _make_sample_receipts(fixture_input)
    prov_dict = _valid_pinned_provenance()
    # Serialize UUIDs and ImmutableArtifactRefs to dict for JSON
    prov_json = {
        "lexical_config_ref": {
            "artifact_code": prov_dict["lexical_config_ref"].artifact_code,
            "version": prov_dict["lexical_config_ref"].version,
            "content_sha256": prov_dict["lexical_config_ref"].content_sha256,
        },
        "dense_config_ref": {
            "artifact_code": prov_dict["dense_config_ref"].artifact_code,
            "version": prov_dict["dense_config_ref"].version,
            "content_sha256": prov_dict["dense_config_ref"].content_sha256,
        },
        "retrieval_config_ref": {
            "artifact_code": prov_dict["retrieval_config_ref"].artifact_code,
            "version": prov_dict["retrieval_config_ref"].version,
            "content_sha256": prov_dict["retrieval_config_ref"].content_sha256,
        },
        "filter_snapshot_ref": {
            "artifact_code": prov_dict["filter_snapshot_ref"].artifact_code,
            "version": prov_dict["filter_snapshot_ref"].version,
            "content_sha256": prov_dict["filter_snapshot_ref"].content_sha256,
        },
        "search_adapter_ref": {
            "artifact_code": prov_dict["search_adapter_ref"].artifact_code,
            "version": prov_dict["search_adapter_ref"].version,
            "content_sha256": prov_dict["search_adapter_ref"].content_sha256,
        },
        "embedding_adapter_ref": {
            "artifact_code": prov_dict["embedding_adapter_ref"].artifact_code,
            "version": prov_dict["embedding_adapter_ref"].version,
            "content_sha256": prov_dict["embedding_adapter_ref"].content_sha256,
        },
        "runtime_release_bundle_id": str(prov_dict["runtime_release_bundle_id"]),
        "runtime_release_bundle_manifest_hash": prov_dict["runtime_release_bundle_manifest_hash"],
        "runtime_execution_manifest_id": str(prov_dict["runtime_execution_manifest_id"]),
        "runtime_execution_manifest_hash": prov_dict["runtime_execution_manifest_hash"],
        "runtime_guard_decision_ref": prov_dict["runtime_guard_decision_ref"],
    }

    s1_path = tmp_path / "s1.json"
    s2_path = tmp_path / "s2.json"
    prov_path = tmp_path / "prov.json"
    out_manifest = tmp_path / "cli_manifest.json"

    s1_path.write_text(json.dumps(stage1_receipt_to_dict(stage1)), encoding="utf-8")
    s2_path.write_text(json.dumps(stage2_receipt_to_dict(stage2)), encoding="utf-8")
    prov_path.write_text(json.dumps(prov_json), encoding="utf-8")

    import sys

    from ai_worker.tasks.evaluation.ret_h_bootstrap_stages import main

    test_args = [
        "ret_h_bootstrap_stages",
        "manifest",
        "--stage1-receipt",
        str(s1_path),
        "--stage2-receipt",
        str(s2_path),
        "--job-id",
        str(ids["job_id"]),
        "--execution-context-id",
        str(ids["ctx_id"]),
        "--prescription-version-id",
        str(ids["prescription_id"]),
        "--provenance-file",
        str(prov_path),
        "--output-manifest",
        str(out_manifest),
    ]
    orig_argv = sys.argv
    try:
        sys.argv = test_args
        main()
    finally:
        sys.argv = orig_argv

    assert out_manifest.is_file()
    loaded = _load_fixture(out_manifest)
    assert loaded is not None
    assert loaded["knowledge_index_id"] == str(ids["index_id"])


def test_stage2_uses_canonical_openai_embedding_adapter_ref() -> None:
    import inspect

    from ai_worker.adapters.openai_text_embedding import (
        OPENAI_TEXT_EMBEDDING_ADAPTER_HASH,
        OPENAI_TEXT_EMBEDDING_ADAPTER_PROJECTION,
        OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        OpenAITextEmbeddingAdapter,
    )
    from ai_worker.tasks.evaluation.canonical import canonical_sha256
    from ai_worker.tasks.evaluation.ret_h_bootstrap_stages import _run_cli_stage2

    expected_hash = "608364dae260bed7d053c6ce4736fd83ab20621e701c29c7b0e6c1fbef10a102"
    assert canonical_sha256(OPENAI_TEXT_EMBEDDING_ADAPTER_PROJECTION) == expected_hash
    assert OPENAI_TEXT_EMBEDDING_ADAPTER_HASH == expected_hash
    assert OPENAI_TEXT_EMBEDDING_ADAPTER_REF == ImmutableArtifactRef(
        artifact_code="openai-text-embedding-adapter",
        version="1.0.0",
        content_sha256=expected_hash,
    )

    sig = inspect.signature(_run_cli_stage2)
    param = sig.parameters.get("expected_embedding_adapter_ref")
    assert param is not None
    assert param.default == OPENAI_TEXT_EMBEDDING_ADAPTER_REF

    # OpenAITextEmbeddingAdapter constructor requires explicit adapter_artifact_ref
    adapter_sig = inspect.signature(OpenAITextEmbeddingAdapter.__init__)
    adapter_param = adapter_sig.parameters.get("adapter_artifact_ref")
    assert adapter_param is not None
    assert adapter_param.default is inspect.Parameter.empty


def test_stage2_cli_assembly_without_raw_sha_env_or_argument() -> None:
    from ai_worker.tasks.evaluation.ret_h_bootstrap_stages import main

    # CLI parser for stage2 must NOT have --embedding-adapter-sha256 argument
    with pytest.raises(SystemExit):
        main(["stage2", "--embedding-adapter-sha256", "invalid"])

    # Help output must succeed without error
    with pytest.raises(SystemExit) as exc_info:
        main(["stage2", "--help"])
    assert exc_info.value.code == 0


def test_corpus_query_and_smoke_stage2_all_use_identical_canonical_ref() -> None:
    from ai_worker.adapters.openai_text_embedding import OPENAI_TEXT_EMBEDDING_ADAPTER_REF
    from ai_worker.admin.knowledge_evidence_index import (
        OPENAI_TEXT_EMBEDDING_ADAPTER_REF as CORPUS_ADAPTER_REF,
    )
    from ai_worker.tasks.evaluation.actual_retrieval import (
        build_actual_adapter_registry,
    )
    from ai_worker.tasks.evaluation.ret_h_bootstrap_stages import (
        OPENAI_TEXT_EMBEDDING_ADAPTER_REF as STAGE2_ADAPTER_REF,
    )

    # All three must be the exact same canonical reference
    assert CORPUS_ADAPTER_REF == OPENAI_TEXT_EMBEDDING_ADAPTER_REF
    assert STAGE2_ADAPTER_REF == OPENAI_TEXT_EMBEDDING_ADAPTER_REF

    assert OPENAI_TEXT_EMBEDDING_ADAPTER_REF.artifact_code == "openai-text-embedding-adapter"
    assert OPENAI_TEXT_EMBEDDING_ADAPTER_REF.version == "1.0.0"
    assert (
        OPENAI_TEXT_EMBEDDING_ADAPTER_REF.content_sha256
        == "608364dae260bed7d053c6ce4736fd83ab20621e701c29c7b0e6c1fbef10a102"
    )

    # Actual retrieval registry builds retrieval_config with OPENAI_TEXT_EMBEDDING_ADAPTER_REF
    from dataclasses import dataclass
    from pathlib import Path

    @dataclass
    class _DummyResolved:
        repository_root: Path = Path(".")

    class _DummySearchPort:
        pass

    class _DummyVerifier:
        pass

    reg = build_actual_adapter_registry(
        _DummyResolved(),
        search_port=_DummySearchPort(),  # type: ignore[arg-type]
        eligibility_verifier=_DummyVerifier(),  # type: ignore[arg-type]
    )
    adapter = reg.resolve("actual-retrieval.v1")
    assert adapter is not None
    assert adapter._retrieval_config.expected_query_embedding_adapter_ref == OPENAI_TEXT_EMBEDDING_ADAPTER_REF


def test_manifest_embedding_adapter_ref_matches_canonical_ref_and_query_retrieval(tmp_path: Path) -> None:
    from ai_worker.adapters.openai_text_embedding import OPENAI_TEXT_EMBEDDING_ADAPTER_REF
    from ai_worker.tasks.rag.evidence_search import (
        RetrievalExecutionMode,
        VersionedDenseSearchConfiguration,
        VersionedEvidenceRetrievalConfiguration,
        VersionedLexicalSearchConfiguration,
    )

    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    stage1, stage2, ids = _make_sample_receipts(fixture_input)

    lexical_config = VersionedLexicalSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("lexical-search-config", "1.0.0", "1" * 64)
    )
    dense_config = VersionedDenseSearchConfiguration(
        artifact_ref=ImmutableArtifactRef("dense-search-config", "1.0.0", "2" * 64)
    )
    ret_config = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=ImmutableArtifactRef("retrieval-config", "1.0.0", "3" * 64),
        lexical_config=lexical_config,
        dense_config=dense_config,
        expected_query_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
    )

    provenance = RetHSmokeRuntimeProvenance.from_retrieval_config(
        retrieval_config=ret_config,
        filter_snapshot_ref=ImmutableArtifactRef("filter-snapshot", "1.0.0", "4" * 64),
        search_adapter_ref=ImmutableArtifactRef("postgresql-evidence-search-adapter", "1.0.0", "5" * 64),
        runtime_release_bundle_id=uuid4(),
        runtime_release_bundle_manifest_hash="7" * 64,
        runtime_execution_manifest_id=uuid4(),
        runtime_execution_manifest_hash="8" * 64,
        runtime_guard_decision_ref="ret-h-aws-synthetic-smoke",
    )

    assert provenance.embedding_adapter_ref == OPENAI_TEXT_EMBEDDING_ADAPTER_REF

    manifest = build_ret_h_smoke_fixture_manifest(
        fixture_input=fixture_input,
        stage1_receipt=stage1,
        stage2_receipt=stage2,
        job_id=ids["job_id"],
        execution_context_id=ids["ctx_id"],
        prescription_version_id=ids["prescription_id"],
        provenance=provenance,
    )

    assert manifest["embedding_adapter_ref"]["artifact_code"] == OPENAI_TEXT_EMBEDDING_ADAPTER_REF.artifact_code
    assert manifest["embedding_adapter_ref"]["version"] == OPENAI_TEXT_EMBEDDING_ADAPTER_REF.version
    assert manifest["embedding_adapter_ref"]["content_sha256"] == OPENAI_TEXT_EMBEDDING_ADAPTER_REF.content_sha256


@pytest.mark.asyncio
async def test_stage2_mismatched_adapter_ref_fails_closed() -> None:
    from ai_worker.adapters.openai_text_embedding import OPENAI_TEXT_EMBEDDING_ADAPTER_REF
    from ai_worker.tasks.evaluation.ret_h_bootstrap_stages import (
        _compute_stage2_embedding,
        bootstrap_ret_h_smoke_stage2_knowledge_index,
    )
    from ai_worker.tasks.rag.evidence_search import SensitiveVector
    from ai_worker.tasks.rag.text_embedding import TextEmbeddingPort, TextEmbeddingSuccess

    fixture = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
    mismatched_ref = ImmutableArtifactRef("wrong-adapter", "1.0.0", "0" * 64)

    class MismatchedPort(TextEmbeddingPort):
        def __init__(self) -> None:
            self._adapter_artifact_ref = mismatched_ref

        async def embed(self, text, **kwargs):  # type: ignore[no-untyped-def]
            return TextEmbeddingSuccess(
                embedding=SensitiveVector([0.1] * 1536),
                adapter_artifact_ref=mismatched_ref,
            )

    # 1. Direct _compute_stage2_embedding check fails closed on mismatch
    with pytest.raises(EvaluationValidationError) as exc_info:
        await _compute_stage2_embedding(
            MismatchedPort(),
            "test statement",
            expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        )
    assert exc_info.value.safe_path == "embedding_adapter_artifact_identity"

    # 2. bootstrap_ret_h_smoke_stage2_knowledge_index pre-check fails closed
    session_factory = MagicMock()
    with pytest.raises(EvaluationValidationError) as exc_info2:
        await bootstrap_ret_h_smoke_stage2_knowledge_index(
            session_factory=session_factory,
            embedding_port=MismatchedPort(),
            fixture=fixture,
            stage1_snapshot_id=uuid4(),
            expected_embedding_adapter_ref=OPENAI_TEXT_EMBEDDING_ADAPTER_REF,
        )
    assert exc_info2.value.safe_path == "embedding_adapter_artifact_identity"


def test_postgresql_evidence_search_adapter_canonical_candidate_identity_wiring() -> None:
    from ai_worker.adapters.postgresql_evidence_search import (
        POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_HASH,
        POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION,
        POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF,
    )
    from ai_worker.tasks.evaluation.canonical import canonical_sha256

    assert POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF.artifact_code == "postgresql-evidence-search-adapter"
    assert POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF.version == "1.0.0"
    assert (
        POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF.content_sha256
        == "77945a6a1a72eba682a13a85b895fd289ab7bce5b63498f4727621180fbad073"
    )
    assert canonical_sha256(POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION) == POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_HASH


def test_scripts_ret_h_aws_synthetic_smoke_rejects_fixture_search_adapter_mismatch() -> None:
    from unittest.mock import MagicMock, patch

    from scripts.ret_h_aws_synthetic_smoke import build_live_dependencies

    fake_factory = MagicMock()
    with patch(
        "ai_worker.tasks.evaluation.ret_h_smoke.build_session_factory",
        return_value=(MagicMock(), fake_factory),
    ):
        with patch("ai_worker.core.get_config"):
            fixture = {
                "search_adapter_ref": {
                    "artifact_code": "postgresql-evidence-search-adapter",
                    "version": "1.0.0",
                    "content_sha256": "5" * 64,
                }
            }
            with pytest.raises(ValueError, match="fixture search_adapter_ref mismatch"):
                build_live_dependencies(
                    fixture,
                    environment={},
                    redis_stream="test",
                    redis_dlq_stream="test-dlq",
                    sentinels=["sentinel"],
                )


def test_scripts_ret_h_aws_synthetic_smoke_accepts_canonical_candidate_ref() -> None:
    from unittest.mock import MagicMock, patch

    from ai_worker.adapters.postgresql_evidence_search import POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF
    from scripts.ret_h_aws_synthetic_smoke import build_live_dependencies

    fake_factory = MagicMock()
    with patch(
        "ai_worker.tasks.evaluation.ret_h_smoke.build_session_factory",
        return_value=(MagicMock(), fake_factory),
    ):
        with patch("ai_worker.core.get_config"):
            with patch("scripts.ret_h_aws_synthetic_smoke._build_hybrid_retrieve_request"):
                fixture = {
                    "search_adapter_ref": {
                        "artifact_code": POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF.artifact_code,
                        "version": POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF.version,
                        "content_sha256": POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF.content_sha256,
                    },
                    "embedding_adapter_ref": {
                        "content_sha256": "6" * 64,
                    },
                    "knowledge_index_id": "17810000-0000-4000-8000-000000000000",
                    "allowed_source_snapshot_ids": ["17810000-0000-4000-8000-000000000001"],
                    "allowed_source_snapshot_member_ids": ["17810000-0000-4000-8000-000000000002"],
                    "source_sentinel": "sentinel",
                }
                deps = build_live_dependencies(
                    fixture,
                    environment={},
                    redis_stream="test",
                    redis_dlq_stream="test-dlq",
                    sentinels=["sentinel"],
                )
                assert deps.execution_fn is not None
