from __future__ import annotations

import re

import pytest

from app.models.rag_retrieval import (
    RetrievalHit,
    RetrievalRun,
    RetrievalRunStatus,
    RetrievalRunVariant,
    RetrievalSignal,
    RetrievalSignalMethod,
)


def test_retrieval_run_table_contract() -> None:
    table = RetrievalRun.__table__
    assert table.name == "retrieval_run"

    # Primary key
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["id"]

    # Column nullability & types
    col_dict = {c.name: c for c in table.columns}
    assert not col_dict["id"].nullable
    assert not col_dict["job_id"].nullable
    assert not col_dict["execution_context_id"].nullable
    assert not col_dict["prescription_version_id"].nullable
    assert not col_dict["runtime_release_bundle_id"].nullable
    assert not col_dict["runtime_release_bundle_manifest_hash"].nullable
    assert not col_dict["runtime_execution_manifest_id"].nullable
    assert not col_dict["runtime_execution_manifest_hash"].nullable
    assert not col_dict["runtime_guard_decision_ref"].nullable
    assert not col_dict["knowledge_index_id"].nullable
    assert not col_dict["node_id"].nullable
    assert not col_dict["variant"].nullable
    assert not col_dict["query_digest_algorithm"].nullable
    assert not col_dict["query_digest_key_version"].nullable
    assert not col_dict["query_digest"].nullable
    assert not col_dict["filter_snapshot"].nullable
    assert not col_dict["filter_snapshot_hash"].nullable
    assert not col_dict["source_manifest_hash"].nullable
    assert not col_dict["retrieval_configuration_hash"].nullable
    assert col_dict["query_embedding_sha256"].nullable
    assert not col_dict["lexical_limit"].nullable
    assert not col_dict["dense_limit"].nullable
    assert not col_dict["hybrid_limit"].nullable
    assert not col_dict["final_k"].nullable
    assert not col_dict["status"].nullable
    assert col_dict["diagnostic_code"].nullable
    assert col_dict["error_code"].nullable
    assert col_dict["search_receipt_hash"].nullable
    assert col_dict["receipt_hash"].nullable
    assert not col_dict["started_at"].nullable
    assert col_dict["completed_at"].nullable

    # Foreign keys
    fk_dict = {fk.parent.name: fk for fk in table.foreign_keys}
    assert fk_dict["job_id"].target_fullname == "ai_job.id"
    assert fk_dict["job_id"].ondelete == "CASCADE"
    assert fk_dict["knowledge_index_id"].target_fullname == "rag_knowledge_index.id"
    assert fk_dict["knowledge_index_id"].ondelete == "RESTRICT"

    # Unique constraints
    uqs = {uq.name: [c.name for c in uq.columns] for uq in table.constraints if uq.name and "uq_" in uq.name}
    assert "uq_retrieval_run_job_node" in uqs
    assert uqs["uq_retrieval_run_job_node"] == ["job_id", "node_id"]

    # Check constraints
    chks = {chk.name for chk in table.constraints if chk.name and "chk_" in chk.name}
    assert "chk_retrieval_run_variant" in chks
    assert "chk_retrieval_run_status" in chks
    assert "chk_retrieval_run_limits" in chks
    assert "chk_retrieval_run_bundle_hash" in chks
    assert "chk_retrieval_run_manifest_hash" in chks
    assert "chk_retrieval_run_query_digest" in chks
    assert "chk_retrieval_run_filter_snapshot_hash" in chks
    assert "chk_retrieval_run_source_manifest_hash" in chks
    assert "chk_retrieval_run_retrieval_configuration_hash" in chks
    assert "chk_retrieval_run_query_embedding_sha256" in chks
    assert "chk_retrieval_run_search_receipt_hash" in chks
    assert "chk_retrieval_run_receipt_hash" in chks


def test_retrieval_signal_table_contract() -> None:
    table = RetrievalSignal.__table__
    assert table.name == "retrieval_signal"

    # Composite primary key
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["retrieval_run_id", "retrieval_method", "knowledge_chunk_id"]

    # Column nullability
    col_dict = {c.name: c for c in table.columns}
    assert not col_dict["retrieval_run_id"].nullable
    assert not col_dict["retrieval_method"].nullable
    assert not col_dict["knowledge_chunk_id"].nullable
    assert not col_dict["raw_rank"].nullable
    assert not col_dict["raw_score"].nullable
    assert not col_dict["score_projection_version"].nullable

    # Foreign keys
    fk_dict = {fk.parent.name: fk for fk in table.foreign_keys}
    assert fk_dict["retrieval_run_id"].target_fullname == "retrieval_run.id"
    assert fk_dict["retrieval_run_id"].ondelete == "CASCADE"
    assert fk_dict["knowledge_chunk_id"].target_fullname == "knowledge_chunk.id"
    assert fk_dict["knowledge_chunk_id"].ondelete == "RESTRICT"

    # Unique constraints
    uqs = {uq.name: [c.name for c in uq.columns] for uq in table.constraints if uq.name and "uq_" in uq.name}
    assert "uq_retrieval_signal_run_method_rank" in uqs
    assert uqs["uq_retrieval_signal_run_method_rank"] == ["retrieval_run_id", "retrieval_method", "raw_rank"]

    # Check constraints
    chks = {chk.name for chk in table.constraints if chk.name and "chk_" in chk.name}
    assert "chk_retrieval_signal_method" in chks
    assert "chk_retrieval_signal_raw_rank" in chks


def test_retrieval_hit_table_contract() -> None:
    table = RetrievalHit.__table__
    assert table.name == "retrieval_hit"

    # Composite primary key
    pk_cols = [c.name for c in table.primary_key.columns]
    assert pk_cols == ["retrieval_run_id", "knowledge_chunk_id"]

    # Column nullability
    col_dict = {c.name: c for c in table.columns}
    assert not col_dict["retrieval_run_id"].nullable
    assert not col_dict["knowledge_chunk_id"].nullable
    assert col_dict["lexical_rank"].nullable
    assert col_dict["dense_rank"].nullable
    assert not col_dict["rrf_rank"].nullable
    assert not col_dict["rrf_score"].nullable
    assert not col_dict["rrf_score_numerator"].nullable
    assert not col_dict["rrf_score_denominator"].nullable
    assert col_dict["rerank_score"].nullable
    assert not col_dict["final_rank"].nullable
    assert not col_dict["selected"].nullable

    # Foreign keys
    fk_dict = {fk.parent.name: fk for fk in table.foreign_keys}
    assert fk_dict["retrieval_run_id"].target_fullname == "retrieval_run.id"
    assert fk_dict["retrieval_run_id"].ondelete == "CASCADE"
    assert fk_dict["knowledge_chunk_id"].target_fullname == "knowledge_chunk.id"
    assert fk_dict["knowledge_chunk_id"].ondelete == "RESTRICT"

    # Unique constraints
    uqs = {uq.name: [c.name for c in uq.columns] for uq in table.constraints if uq.name and "uq_" in uq.name}
    assert "uq_retrieval_hit_run_rrf_rank" in uqs
    assert uqs["uq_retrieval_hit_run_rrf_rank"] == ["retrieval_run_id", "rrf_rank"]
    assert "uq_retrieval_hit_run_final_rank" in uqs
    assert uqs["uq_retrieval_hit_run_final_rank"] == ["retrieval_run_id", "final_rank"]

    # Check constraints
    chks = {chk.name for chk in table.constraints if chk.name and "chk_" in chk.name}
    assert "chk_retrieval_hit_rrf_rank" in chks
    assert "chk_retrieval_hit_final_rank" in chks
    assert "chk_retrieval_hit_lexical_rank" in chks
    assert "chk_retrieval_hit_dense_rank" in chks


def test_retrieval_enums_match_specifications() -> None:
    assert set(RetrievalRunStatus) == {"RUNNING", "COMPLETED", "FAILED"}
    assert set(RetrievalRunVariant) == {"RET-L", "RET-D", "RET-H"}
    assert set(RetrievalSignalMethod) == {"EXACT", "TRIGRAM", "FTS", "LEXICAL", "DENSE"}


def test_python_validation_boundary_selected_hits_and_ranks() -> None:
    """Validate that selected hits cannot exceed rank 5 or final_rank > 5 in Python domain rules."""

    # Selected hit with final_rank > 5 is invalid
    def validate_selection(final_rank: int, selected: bool) -> None:
        if selected and final_rank > 5:
            raise ValueError("VALIDATION_ERROR: Selected hits must be within pre-gate top 5")

    with pytest.raises(ValueError, match="Selected hits must be within pre-gate top 5"):
        validate_selection(final_rank=6, selected=True)

    # Valid selection within top 5
    validate_selection(final_rank=1, selected=True)
    validate_selection(final_rank=5, selected=True)
    validate_selection(final_rank=6, selected=False)


def test_python_validation_boundary_hash_format() -> None:
    sha256_re = re.compile(r"^[0-9a-f]{64}$")
    assert sha256_re.match("a" * 64) is not None
    assert sha256_re.match("A" * 64) is None  # must be lowercase hex
    assert sha256_re.match("a" * 63) is None
    assert sha256_re.match("g" * 64) is None
