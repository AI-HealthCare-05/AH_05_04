"""Synthetic resources and schema loader for evaluation and smoke tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_OID, UUID, uuid5

from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError

DEFAULT_SMOKE_FIXTURE_PATH = Path(__file__).resolve().parent / "ret_h_smoke_synthetic_fixture.json"


@dataclass(frozen=True, slots=True)
class SmokeSyntheticRecord:
    evidence_ref_id: str
    statement: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class SmokeSyntheticFixtureInput:
    source_code: str
    endpoint_code: str
    operation_code: str
    index_code: str
    index_version: str
    query_sentinel: str
    source_sentinel: str
    synthetic_query: str
    synthetic_query_sha256: str
    records: tuple[SmokeSyntheticRecord, ...]
    file_sha256: str


def _validate_fixture_codes(data: dict[str, Any]) -> tuple[str, str, str, str, str]:
    source_code = str(data.get("source_code") or "").strip()
    endpoint_code = str(data.get("endpoint_code") or "").strip()
    operation_code = str(data.get("operation_code") or "").strip()
    index_code = str(data.get("index_code") or "").strip()
    index_version = str(data.get("index_version") or "").strip()

    if not source_code or len(source_code) > 100:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="source_code")
    if not endpoint_code or len(endpoint_code) > 100:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="endpoint_code")
    if not operation_code or len(operation_code) > 100:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="operation_code")
    if not index_code or len(index_code) > 120:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="index_code")
    if not index_version or len(index_version) > 64:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="index_version")
    return source_code, endpoint_code, operation_code, index_code, index_version


def _validate_fixture_sentinels_and_query(data: dict[str, Any]) -> tuple[str, str, str, str]:
    query_sentinel = str(data.get("query_sentinel") or "").strip()
    source_sentinel = str(data.get("source_sentinel") or "").strip()

    if not query_sentinel:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="query_sentinel")
    if not source_sentinel:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="source_sentinel")
    if query_sentinel == source_sentinel:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="sentinels_distinct")

    synthetic_query = str(data.get("synthetic_query") or "").strip()
    synthetic_query_sha256 = str(data.get("synthetic_query_sha256") or "").strip()

    if query_sentinel not in synthetic_query:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="synthetic_query_sentinel")
    if source_sentinel in synthetic_query:
        raise EvaluationValidationError(
            EvaluationErrorCode.SCHEMA_INVALID, safe_path="synthetic_query_forbidden_sentinel"
        )

    actual_query_hash = hashlib.sha256(synthetic_query.encode()).hexdigest()
    if actual_query_hash != synthetic_query_sha256:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH, safe_path="synthetic_query_sha256")
    return query_sentinel, source_sentinel, synthetic_query, synthetic_query_sha256


def _validate_fixture_records(
    records_raw: Any, query_sentinel: str, source_sentinel: str
) -> tuple[SmokeSyntheticRecord, ...]:
    if not isinstance(records_raw, list) or len(records_raw) != 1:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="records_count")

    rec = records_raw[0]
    if not isinstance(rec, dict):
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="record_item")

    evidence_ref_id = str(rec.get("evidence_ref_id") or "").strip()
    statement = str(rec.get("statement") or "").strip()
    content_sha256 = str(rec.get("content_sha256") or "").strip()

    if not evidence_ref_id or not statement or not content_sha256:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="record_fields")

    if source_sentinel not in statement:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="statement_source_sentinel")
    if query_sentinel in statement:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID, safe_path="statement_forbidden_sentinel")

    actual_statement_hash = hashlib.sha256(statement.encode()).hexdigest()
    if actual_statement_hash != content_sha256:
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH, safe_path="statement_content_sha256")

    return (SmokeSyntheticRecord(evidence_ref_id=evidence_ref_id, statement=statement, content_sha256=content_sha256),)


def load_ret_h_smoke_synthetic_fixture(
    path: Path | str | None = None,
) -> SmokeSyntheticFixtureInput:
    target_path = Path(path) if path is not None else DEFAULT_SMOKE_FIXTURE_PATH
    if not target_path.is_file():
        raise EvaluationValidationError(
            EvaluationErrorCode.RESOURCE_NOT_FOUND,
            safe_path=str(target_path),
        )

    raw_text = target_path.read_text(encoding="utf-8")
    file_sha256 = hashlib.sha256(raw_text.encode()).hexdigest()

    try:
        data = json.loads(raw_text)
    except Exception as exc:
        raise EvaluationValidationError(
            EvaluationErrorCode.JSON_INVALID,
            safe_path=str(target_path),
        ) from exc

    if not isinstance(data, dict):
        raise EvaluationValidationError(
            EvaluationErrorCode.SCHEMA_INVALID,
            safe_path="root",
        )

    source_code, endpoint_code, operation_code, index_code, index_version = _validate_fixture_codes(data)
    (
        query_sentinel,
        source_sentinel,
        synthetic_query,
        synthetic_query_sha256,
    ) = _validate_fixture_sentinels_and_query(data)
    records = _validate_fixture_records(data.get("records"), query_sentinel, source_sentinel)

    return SmokeSyntheticFixtureInput(
        source_code=source_code,
        endpoint_code=endpoint_code,
        operation_code=operation_code,
        index_code=index_code,
        index_version=index_version,
        query_sentinel=query_sentinel,
        source_sentinel=source_sentinel,
        synthetic_query=synthetic_query,
        synthetic_query_sha256=synthetic_query_sha256,
        records=records,
        file_sha256=file_sha256,
    )


def build_ret_h_smoke_fixture_manifest(
    *,
    fixture_input: SmokeSyntheticFixtureInput,
    stage1_receipt: Any,
    stage2_receipt: Any,
    job_id: UUID,
    execution_context_id: UUID,
    prescription_version_id: UUID,
    runtime_release_bundle_id: UUID | None = None,
    runtime_execution_manifest_id: UUID | None = None,
) -> dict[str, Any]:
    bundle_id = runtime_release_bundle_id or uuid5(NAMESPACE_OID, f"bundle:{job_id}")
    manifest_id = runtime_execution_manifest_id or uuid5(NAMESPACE_OID, f"manifest:{job_id}")

    return {
        "knowledge_index_id": str(stage2_receipt.knowledge_index_id),
        "knowledge_index_ref": f"{fixture_input.index_code}:{fixture_input.index_version}",
        "allowed_source_snapshot_ids": [str(stage1_receipt.snapshot_id)],
        "allowed_source_snapshot_member_ids": [str(m) for m in stage1_receipt.member_ids],
        "lexical_config_ref": {
            "artifact_code": "lexical-search-config",
            "version": "1.0.0",
            "content_sha256": "0" * 64,
        },
        "dense_config_ref": {
            "artifact_code": "dense-search-config",
            "version": "1.0.0",
            "content_sha256": "0" * 64,
        },
        "retrieval_config_ref": {
            "artifact_code": "retrieval-config",
            "version": "1.0.0",
            "content_sha256": "0" * 64,
        },
        "filter_snapshot_ref": {
            "artifact_code": "filter-snapshot",
            "version": "1.0.0",
            "content_sha256": "0" * 64,
        },
        "evidence_index_ref": {
            "artifact_code": fixture_input.index_code,
            "version": fixture_input.index_version,
            "content_sha256": stage2_receipt.index_configuration_hash,
        },
        "search_adapter_ref": {
            "artifact_code": "postgresql-evidence-search-adapter",
            "version": "1.0.0",
            "content_sha256": "a" * 64,
        },
        "embedding_adapter_ref": {
            "artifact_code": "openai-text-embedding-adapter",
            "version": "1.0.0",
            "content_sha256": "e" * 64,
        },
        "synthetic_query": fixture_input.synthetic_query,
        "synthetic_query_sha256": fixture_input.synthetic_query_sha256,
        "query_sentinel": fixture_input.query_sentinel,
        "source_sentinel": fixture_input.source_sentinel,
        "job_id": str(job_id),
        "execution_context_id": str(execution_context_id),
        "prescription_version_id": str(prescription_version_id),
        "runtime_release_bundle_id": str(bundle_id),
        "runtime_release_bundle_manifest_hash": "0" * 64,
        "runtime_execution_manifest_id": str(manifest_id),
        "runtime_execution_manifest_hash": "0" * 64,
        "runtime_guard_decision_ref": "ALLOW",
        "source_manifest_hash": fixture_input.file_sha256,
    }
