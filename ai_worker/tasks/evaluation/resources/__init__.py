"""Synthetic resources and schema loader for evaluation and smoke tests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.evidence_search import VersionedEvidenceRetrievalConfiguration

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


@dataclass(frozen=True, slots=True)
class RetHSmokeRuntimeProvenance:
    lexical_config_ref: ImmutableArtifactRef | Mapping[str, Any]
    dense_config_ref: ImmutableArtifactRef | Mapping[str, Any]
    retrieval_config_ref: ImmutableArtifactRef | Mapping[str, Any]
    filter_snapshot_ref: ImmutableArtifactRef | Mapping[str, Any]
    search_adapter_ref: ImmutableArtifactRef | Mapping[str, Any]
    embedding_adapter_ref: ImmutableArtifactRef | Mapping[str, Any]
    runtime_release_bundle_id: UUID | str
    runtime_release_bundle_manifest_hash: str
    runtime_execution_manifest_id: UUID | str
    runtime_execution_manifest_hash: str
    runtime_guard_decision_ref: str

    @classmethod
    def from_retrieval_config(
        cls,
        *,
        retrieval_config: VersionedEvidenceRetrievalConfiguration,
        filter_snapshot_ref: ImmutableArtifactRef | Mapping[str, Any],
        search_adapter_ref: ImmutableArtifactRef | Mapping[str, Any],
        runtime_release_bundle_id: UUID | str,
        runtime_release_bundle_manifest_hash: str,
        runtime_execution_manifest_id: UUID | str,
        runtime_execution_manifest_hash: str,
        runtime_guard_decision_ref: str,
    ) -> RetHSmokeRuntimeProvenance:
        if retrieval_config.dense_config is None:
            raise ValueError("retrieval_config.dense_config is required for RET-H hybrid retrieval")
        if retrieval_config.expected_query_embedding_adapter_ref is None:
            raise ValueError("retrieval_config.expected_query_embedding_adapter_ref is required")
        return cls(
            lexical_config_ref=retrieval_config.lexical_config.artifact_ref,
            dense_config_ref=retrieval_config.dense_config.artifact_ref,
            retrieval_config_ref=retrieval_config.artifact_ref,
            filter_snapshot_ref=filter_snapshot_ref,
            search_adapter_ref=search_adapter_ref,
            embedding_adapter_ref=retrieval_config.expected_query_embedding_adapter_ref,
            runtime_release_bundle_id=runtime_release_bundle_id,
            runtime_release_bundle_manifest_hash=runtime_release_bundle_manifest_hash,
            runtime_execution_manifest_id=runtime_execution_manifest_id,
            runtime_execution_manifest_hash=runtime_execution_manifest_hash,
            runtime_guard_decision_ref=runtime_guard_decision_ref,
        )


def _validate_hash_not_placeholder(value: Any, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
    cleaned = value.strip().lower()
    if len(cleaned) != 64 or not all(c in "0123456789abcdef" for c in cleaned):
        raise ValueError(f"{field_name} must be a 64-character lowercase hex string, got {value!r}")
    if cleaned == "0" * 64 or set(cleaned) == {"0"}:
        raise ValueError(f"{field_name} cannot be placeholder zero hash")
    return cleaned


def _validate_uuid_field(value: Any, field_name: str) -> UUID:
    if value is None:
        raise ValueError(f"{field_name} is required")
    if isinstance(value, UUID):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return UUID(value.strip())
        except ValueError as err:
            raise ValueError(f"{field_name} must be a valid UUID hex string") from err
    raise TypeError(f"{field_name} must be a valid UUID, got {value!r}")


def _validate_artifact_ref(ref: Any, field_name: str) -> dict[str, str]:
    if ref is None:
        raise ValueError(f"{field_name} is required")
    if isinstance(ref, ImmutableArtifactRef):
        code, version, sha = ref.artifact_code, ref.version, ref.content_sha256
    elif isinstance(ref, Mapping):
        code = str(ref.get("artifact_code") or "")
        version = str(ref.get("version") or "")
        sha = str(ref.get("content_sha256") or "")
    else:
        raise TypeError(f"{field_name} must be ImmutableArtifactRef or Mapping, got {type(ref).__name__}")

    if not code.strip():
        raise ValueError(f"{field_name}.artifact_code is required")
    if not version.strip():
        raise ValueError(f"{field_name}.version is required")
    clean_sha = _validate_hash_not_placeholder(sha, f"{field_name}.content_sha256")
    return {
        "artifact_code": code.strip(),
        "version": version.strip(),
        "content_sha256": clean_sha,
    }


def _validate_guard_decision_ref(value: Any, field_name: str) -> str:
    if not value or not isinstance(value, str):
        raise ValueError(f"{field_name} is required and must be a non-empty string")
    cleaned = value.strip()
    if not cleaned or cleaned.upper() == "ALLOW":
        raise ValueError(f"{field_name} cannot be empty or placeholder 'ALLOW'")
    return cleaned


def _extract_provenance_refs(
    *,
    provenance: RetHSmokeRuntimeProvenance | None,
    retrieval_config: VersionedEvidenceRetrievalConfiguration | None,
    lexical_config_ref: Any,
    dense_config_ref: Any,
    retrieval_config_ref: Any,
    filter_snapshot_ref: Any,
    search_adapter_ref: Any,
    embedding_adapter_ref: Any,
) -> tuple[Any, Any, Any, Any, Any, Any]:
    if provenance is not None:
        lexical_config_ref = lexical_config_ref or provenance.lexical_config_ref
        dense_config_ref = dense_config_ref or provenance.dense_config_ref
        retrieval_config_ref = retrieval_config_ref or provenance.retrieval_config_ref
        filter_snapshot_ref = filter_snapshot_ref or provenance.filter_snapshot_ref
        search_adapter_ref = search_adapter_ref or provenance.search_adapter_ref
        embedding_adapter_ref = embedding_adapter_ref or provenance.embedding_adapter_ref

    if retrieval_config is not None:
        lexical_config_ref = lexical_config_ref or retrieval_config.lexical_config.artifact_ref
        if retrieval_config.dense_config is not None:
            dense_config_ref = dense_config_ref or retrieval_config.dense_config.artifact_ref
        retrieval_config_ref = retrieval_config_ref or retrieval_config.artifact_ref
        if retrieval_config.expected_query_embedding_adapter_ref is not None:
            embedding_adapter_ref = embedding_adapter_ref or retrieval_config.expected_query_embedding_adapter_ref

    return (
        lexical_config_ref,
        dense_config_ref,
        retrieval_config_ref,
        filter_snapshot_ref,
        search_adapter_ref,
        embedding_adapter_ref,
    )


def _extract_provenance_identities(
    *,
    provenance: RetHSmokeRuntimeProvenance | None,
    bundle_id: Any,
    bundle_hash: Any,
    manifest_id: Any,
    manifest_hash: Any,
    guard_ref: Any,
) -> tuple[Any, Any, Any, Any, Any]:
    if provenance is not None:
        bundle_id = bundle_id or provenance.runtime_release_bundle_id
        bundle_hash = bundle_hash or provenance.runtime_release_bundle_manifest_hash
        manifest_id = manifest_id or provenance.runtime_execution_manifest_id
        manifest_hash = manifest_hash or provenance.runtime_execution_manifest_hash
        guard_ref = guard_ref or provenance.runtime_guard_decision_ref

    return bundle_id, bundle_hash, manifest_id, manifest_hash, guard_ref


def build_ret_h_smoke_fixture_manifest(
    *,
    fixture_input: SmokeSyntheticFixtureInput,
    stage1_receipt: Any,
    stage2_receipt: Any,
    job_id: UUID,
    execution_context_id: UUID,
    prescription_version_id: UUID,
    provenance: RetHSmokeRuntimeProvenance | None = None,
    retrieval_config: VersionedEvidenceRetrievalConfiguration | None = None,
    lexical_config_ref: ImmutableArtifactRef | Mapping[str, Any] | None = None,
    dense_config_ref: ImmutableArtifactRef | Mapping[str, Any] | None = None,
    retrieval_config_ref: ImmutableArtifactRef | Mapping[str, Any] | None = None,
    filter_snapshot_ref: ImmutableArtifactRef | Mapping[str, Any] | None = None,
    search_adapter_ref: ImmutableArtifactRef | Mapping[str, Any] | None = None,
    embedding_adapter_ref: ImmutableArtifactRef | Mapping[str, Any] | None = None,
    runtime_release_bundle_id: UUID | str | None = None,
    runtime_release_bundle_manifest_hash: str | None = None,
    runtime_execution_manifest_id: UUID | str | None = None,
    runtime_execution_manifest_hash: str | None = None,
    runtime_guard_decision_ref: str | None = None,
) -> dict[str, Any]:
    """Build the approved fixture manifest for the #683 RET-H AWS synthetic smoke runner.

    All 11 provenance, configuration, and runtime identity references must be supplied
    from pinned, genuine artifacts. Silent defaults, synthesized UUIDs, or placeholder
    zero hashes are strictly rejected fail-closed.
    """
    raw_lex, raw_dense, raw_ret, raw_filter, raw_search, raw_emb = _extract_provenance_refs(
        provenance=provenance,
        retrieval_config=retrieval_config,
        lexical_config_ref=lexical_config_ref,
        dense_config_ref=dense_config_ref,
        retrieval_config_ref=retrieval_config_ref,
        filter_snapshot_ref=filter_snapshot_ref,
        search_adapter_ref=search_adapter_ref,
        embedding_adapter_ref=embedding_adapter_ref,
    )
    raw_b_id, raw_b_hash, raw_m_id, raw_m_hash, raw_guard = _extract_provenance_identities(
        provenance=provenance,
        bundle_id=runtime_release_bundle_id,
        bundle_hash=runtime_release_bundle_manifest_hash,
        manifest_id=runtime_execution_manifest_id,
        manifest_hash=runtime_execution_manifest_hash,
        guard_ref=runtime_guard_decision_ref,
    )

    valid_lex = _validate_artifact_ref(raw_lex, "lexical_config_ref")
    valid_dense = _validate_artifact_ref(raw_dense, "dense_config_ref")
    valid_ret = _validate_artifact_ref(raw_ret, "retrieval_config_ref")
    valid_filter = _validate_artifact_ref(raw_filter, "filter_snapshot_ref")
    valid_search = _validate_artifact_ref(raw_search, "search_adapter_ref")
    valid_emb = _validate_artifact_ref(raw_emb, "embedding_adapter_ref")

    valid_b_id = _validate_uuid_field(raw_b_id, "runtime_release_bundle_id")
    valid_b_hash = _validate_hash_not_placeholder(raw_b_hash, "runtime_release_bundle_manifest_hash")
    valid_m_id = _validate_uuid_field(raw_m_id, "runtime_execution_manifest_id")
    valid_m_hash = _validate_hash_not_placeholder(raw_m_hash, "runtime_execution_manifest_hash")
    valid_guard = _validate_guard_decision_ref(raw_guard, "runtime_guard_decision_ref")

    valid_idx_conf_hash = _validate_hash_not_placeholder(
        stage2_receipt.index_configuration_hash, "stage2_receipt.index_configuration_hash"
    )

    return {
        "knowledge_index_id": str(stage2_receipt.knowledge_index_id),
        "knowledge_index_ref": f"{fixture_input.index_code}:{fixture_input.index_version}",
        "allowed_source_snapshot_ids": [str(stage1_receipt.snapshot_id)],
        "allowed_source_snapshot_member_ids": [str(m) for m in stage1_receipt.member_ids],
        "lexical_config_ref": valid_lex,
        "dense_config_ref": valid_dense,
        "retrieval_config_ref": valid_ret,
        "filter_snapshot_ref": valid_filter,
        "evidence_index_ref": {
            "artifact_code": fixture_input.index_code,
            "version": fixture_input.index_version,
            "content_sha256": valid_idx_conf_hash,
        },
        "search_adapter_ref": valid_search,
        "embedding_adapter_ref": valid_emb,
        "synthetic_query": fixture_input.synthetic_query,
        "synthetic_query_sha256": fixture_input.synthetic_query_sha256,
        "query_sentinel": fixture_input.query_sentinel,
        "source_sentinel": fixture_input.source_sentinel,
        "job_id": str(job_id),
        "execution_context_id": str(execution_context_id),
        "prescription_version_id": str(prescription_version_id),
        "runtime_release_bundle_id": str(valid_b_id),
        "runtime_release_bundle_manifest_hash": valid_b_hash,
        "runtime_execution_manifest_id": str(valid_m_id),
        "runtime_execution_manifest_hash": valid_m_hash,
        "runtime_guard_decision_ref": valid_guard,
        "source_manifest_hash": fixture_input.file_sha256,
    }
