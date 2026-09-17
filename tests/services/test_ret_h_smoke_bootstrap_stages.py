"""Unit and contract tests for RET-H AWS synthetic smoke bootstrap stages (#684)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ai_worker.tasks.evaluation.actual_retrieval_index import SYNTHETIC_INDEX_CODE
from ai_worker.tasks.evaluation.errors import EvaluationValidationError
from ai_worker.tasks.evaluation.resources import (
    DEFAULT_SMOKE_FIXTURE_PATH,
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
)
from ai_worker.tasks.evaluation.ret_h_smoke import (
    APPROVED_SYNTHETIC_INDEX_CODES,
    verify_fixture_is_synthetic,
)
from backend.app.release_validation.ret_h_synthetic_smoke import (
    sentinels_from_fixture,
    verify_query_sentinel_binding,
)
from scripts.ret_h_aws_synthetic_smoke import _build_hybrid_retrieve_request


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


def test_build_ret_h_smoke_fixture_manifest_and_round_trip() -> None:
    fixture_input = load_ret_h_smoke_synthetic_fixture(DEFAULT_SMOKE_FIXTURE_PATH)
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

    stage1_receipt = Stage1SourceReceipt(
        source_id=source_id,
        endpoint_id=endpoint_id,
        operation_id=operation_id,
        snapshot_id=snapshot_id,
        member_ids=(member_id,),
        canonical_checksum=fixture_input.file_sha256,
        verification_seal_id=seal_id,
        reused=False,
    )

    stage2_receipt = Stage2IndexReceipt(
        knowledge_index_id=index_id,
        index_code=RET_H_SMOKE_INDEX_CODE,
        index_version=RET_H_SMOKE_INDEX_VERSION,
        index_configuration_hash="a" * 64,
        document_id=doc_id,
        chunk_id=chunk_id,
        member_id=index_member_id,
        reused=False,
    )

    manifest = build_ret_h_smoke_fixture_manifest(
        fixture_input=fixture_input,
        stage1_receipt=stage1_receipt,
        stage2_receipt=stage2_receipt,
        job_id=job_id,
        execution_context_id=ctx_id,
        prescription_version_id=prescription_id,
    )

    # 1. JSON serializable
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
    assert req.job_id == job_id
    assert req.execution_context_id == ctx_id
    assert req.prescription_version_id == prescription_id
    assert req.search_request.execution_binding.knowledge_index_id == index_id
    assert req.search_request.execution_binding.allowed_source_snapshot_ids == (snapshot_id,)
    assert req.search_request.execution_binding.allowed_source_snapshot_member_ids == (member_id,)
