"""Unit tests for PostgresqlEvidenceSearchAdapter artifact identity and candidate projection."""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock

from ai_worker.adapters.postgresql_evidence_search import (
    POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_ARTIFACT_CODE,
    POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_HASH,
    POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION,
    POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION_VERSION,
    POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF,
    POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_VERSION,
    PostgresqlEvidenceSearchAdapter,
)
from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef, QueryFingerprint
from ai_worker.tasks.rag.evidence_search import EvidenceSearchSuccess
from ai_worker.tasks.rag.retrieval_runtime import (
    RetrievalExecutionStatus,
    compute_production_search_receipt,
)

EXPECTED_CANDIDATE_HASH = "77945a6a1a72eba682a13a85b895fd289ab7bce5b63498f4727621180fbad073"


def test_postgresql_evidence_search_adapter_canonical_projection_and_candidate_hash() -> None:
    expected_projection: dict[str, Any] = {
        "projection_version": "postgresql-evidence-search-adapter@1",
        "artifact_code": "postgresql-evidence-search-adapter",
        "artifact_version": "1.0.0",
        "database_engine": "postgresql",
        "contract": "knowledge-evidence-search-rrf-v1",
        "subsearches": ["dense", "exact", "fts", "trigram"],
        "fusion_algorithm": "rrf-rank-fusion@1",
        "runtime_module": "ai_worker.adapters.postgresql_evidence_search",
    }
    assert POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION == expected_projection
    assert POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_ARTIFACT_CODE == "postgresql-evidence-search-adapter"
    assert POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_VERSION == "1.0.0"
    assert POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION_VERSION == "postgresql-evidence-search-adapter@1"

    recomputed = canonical_sha256(POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION)
    assert recomputed == EXPECTED_CANDIDATE_HASH
    assert POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_HASH == EXPECTED_CANDIDATE_HASH

    assert POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF == ImmutableArtifactRef(
        artifact_code="postgresql-evidence-search-adapter",
        version="1.0.0",
        content_sha256=EXPECTED_CANDIDATE_HASH,
    )


def test_postgresql_evidence_search_adapter_same_projection_same_ref() -> None:
    proj_copy = dict(POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION)
    hash_copy = canonical_sha256(proj_copy)
    ref_copy = ImmutableArtifactRef(
        artifact_code=str(proj_copy["artifact_code"]),
        version=str(proj_copy["artifact_version"]),
        content_sha256=hash_copy,
    )
    assert ref_copy == POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF


def test_postgresql_evidence_search_adapter_modified_semantics_produces_different_ref() -> None:
    mutations: list[dict[str, Any]] = [
        {**POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION, "database_engine": "mysql"},
        {**POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION, "contract": "knowledge-evidence-search-rrf-v2"},
        {**POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION, "subsearches": ["dense", "exact"]},
        {**POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION, "fusion_algorithm": "borda-count@1"},
        {**POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION, "runtime_module": "ai_worker.adapters.custom_search"},
        {**POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION, "artifact_version": "1.0.1"},
    ]
    for mutated in mutations:
        mutated_hash = canonical_sha256(mutated)
        assert mutated_hash != EXPECTED_CANDIDATE_HASH


def test_postgresql_evidence_search_adapter_projection_excludes_runtime_and_config_state() -> None:
    forbidden_keys = {
        "rrf_k",
        "exact_limit",
        "trigram_limit",
        "fts_limit",
        "dense_limit",
        "trigram_similarity_threshold",
        "stable_coordinate_fields",
        "tie_break",
        "database_url",
        "password",
        "secret",
        "raw_query",
        "embedding",
        "timestamp",
        "git_commit",
    }
    present_keys = set(POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_PROJECTION.keys())
    assert forbidden_keys.isdisjoint(present_keys)


def test_postgresql_evidence_search_adapter_constructor_requires_explicit_ref() -> None:
    sig = inspect.signature(PostgresqlEvidenceSearchAdapter.__init__)
    param = sig.parameters.get("adapter_artifact_ref")
    assert param is not None
    assert param.default is inspect.Parameter.empty


def test_postgresql_evidence_search_adapter_ref_propagation_in_evidence_search_success() -> None:
    fake_session_factory = MagicMock()
    adapter = PostgresqlEvidenceSearchAdapter(
        session_factory=fake_session_factory,
        adapter_artifact_ref=POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF,
    )
    assert adapter._adapter_artifact_ref == POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF

    fake_request = MagicMock()
    success = EvidenceSearchSuccess(
        request=fake_request,
        adapter_artifact_ref=adapter._adapter_artifact_ref,
        lexical_hits=(),
        dense_hits=(),
        hybrid_hits=(),
        signals=(),
    )
    assert success.adapter_artifact_ref == POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF


def test_postgresql_evidence_search_adapter_ref_propagation_to_production_search_receipt() -> None:
    filter_ref = ImmutableArtifactRef("filter-snapshot", "1.0.0", "f" * 64)
    index_ref = ImmutableArtifactRef("synthetic-index", "1.0.0", "i" * 64)
    ret_cfg_ref = ImmutableArtifactRef("retrieval_config", "1.0", "c" * 64)
    qfp = QueryFingerprint("sha256", "v1", "q" * 64)

    receipt = compute_production_search_receipt(
        variant="RET-H",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code="ELIGIBILITY_CONFIRMED",
        query_fingerprint=qfp,
        filter_snapshot_ref=filter_ref,
        evidence_index_ref=index_ref,
        retrieval_config_ref=ret_cfg_ref,
        adapter_artifact_ref=POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF,
        query_embedding_sha256="e" * 64,
        signal_manifest_sha256="s" * 64,
        hit_manifest_sha256="h" * 64,
        selection_manifest_sha256="m" * 64,
    )

    assert receipt.adapter_artifact_ref == POSTGRESQL_EVIDENCE_SEARCH_ADAPTER_REF

    # Changing adapter_artifact_ref modifies the receipt content_sha256
    altered_receipt = compute_production_search_receipt(
        variant="RET-H",
        status=RetrievalExecutionStatus.SUCCEEDED,
        diagnostic_code="ELIGIBILITY_CONFIRMED",
        query_fingerprint=qfp,
        filter_snapshot_ref=filter_ref,
        evidence_index_ref=index_ref,
        retrieval_config_ref=ret_cfg_ref,
        adapter_artifact_ref=ImmutableArtifactRef("postgresql-evidence-search-adapter", "1.0.0", "0" * 64),
        query_embedding_sha256="e" * 64,
        signal_manifest_sha256="s" * 64,
        hit_manifest_sha256="h" * 64,
        selection_manifest_sha256="m" * 64,
    )
    assert altered_receipt.artifact_ref.content_sha256 != receipt.artifact_ref.content_sha256
