from dataclasses import replace
from uuid import UUID

import pytest

from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.rag.evidence_retrieval import ImmutableArtifactRef
from ai_worker.tasks.rag.evidence_search import (
    RetrievalExecutionMode,
    VersionedDenseSearchConfiguration,
    VersionedEvidenceRetrievalConfiguration,
    VersionedLexicalSearchConfiguration,
    project_versioned_evidence_retrieval_configuration,
)
from rag_runtime.guide_retrieval_binding import (
    GuideRetrievalArtifactRef,
    GuideRetrievalBindingValidationError,
    GuideRetrievalMemberBinding,
    GuideRetrievalSourceMember,
    build_guide_retrieval_binding_manifest,
    parse_member_bindings,
    validate_retrieval_configuration_projection,
)


def _artifact_ref(code: str, version: str, content_sha256: str) -> ImmutableArtifactRef:
    return ImmutableArtifactRef(
        artifact_code=code,
        version=version,
        content_sha256=content_sha256,
    )


def _retrieval_configuration() -> VersionedEvidenceRetrievalConfiguration:
    lexical = VersionedLexicalSearchConfiguration(
        artifact_ref=_artifact_ref("lexical-config", "1.0.0", "0" * 64),
    )
    lexical = replace(
        lexical,
        artifact_ref=replace(lexical.artifact_ref, content_sha256=lexical.compute_canonical_hash()),
    )
    dense = VersionedDenseSearchConfiguration(
        artifact_ref=_artifact_ref("dense-config", "1.0.0", "0" * 64),
    )
    dense = replace(
        dense,
        artifact_ref=replace(dense.artifact_ref, content_sha256=dense.compute_canonical_hash()),
    )
    configuration = VersionedEvidenceRetrievalConfiguration(
        artifact_ref=_artifact_ref("retrieval-config", "1.0.0", "0" * 64),
        lexical_config=lexical,
        dense_config=dense,
        expected_query_embedding_adapter_ref=_artifact_ref("embedding-adapter", "1.0.0", "a" * 64),
        execution_mode=RetrievalExecutionMode.HYBRID_RRF,
    )
    return replace(
        configuration,
        artifact_ref=replace(
            configuration.artifact_ref,
            content_sha256=configuration.compute_canonical_hash(),
        ),
    )


def _manifest():
    configuration = _retrieval_configuration()
    return build_guide_retrieval_binding_manifest(
        runtime_release_bundle_id=UUID("00000000-0000-0000-0000-000000000010"),
        runtime_release_bundle_manifest_hash="1" * 64,
        runtime_execution_manifest_id=UUID("00000000-0000-0000-0000-000000000020"),
        runtime_execution_manifest_hash="2" * 64,
        knowledge_index_id=UUID("00000000-0000-0000-0000-000000000030"),
        evidence_index_ref=GuideRetrievalArtifactRef("guide-index", "1.0.0", "3" * 64),
        member_bindings=(
            GuideRetrievalMemberBinding(
                source_snapshot_id=UUID("00000000-0000-0000-0000-000000000040"),
                source_snapshot_member_id=UUID("00000000-0000-0000-0000-000000000050"),
            ),
        ),
        retrieval_configuration=project_versioned_evidence_retrieval_configuration(configuration),
        source_members=(
            GuideRetrievalSourceMember(
                source_snapshot_id=UUID("00000000-0000-0000-0000-000000000040"),
                source_purpose="KNOWLEDGE",
                source_version="api:2026-09-21",
                canonical_checksum="4" * 64,
                approval_version="approval-v1",
                scope_policy_hash="5" * 64,
                freshness_policy_hash="6" * 64,
                required=True,
                selected_for_operation=True,
            ),
        ),
    )


def test_retrieval_configuration_projection_reuses_canonical_hash() -> None:
    configuration = _retrieval_configuration()
    projection = project_versioned_evidence_retrieval_configuration(configuration)

    artifact_ref = projection["artifact_ref"]
    assert isinstance(artifact_ref, dict)
    assert validate_retrieval_configuration_projection(projection) == configuration.compute_canonical_hash()
    assert artifact_ref["content_sha256"] == configuration.compute_canonical_hash()


def test_retrieval_configuration_projection_rejects_nested_hash_drift() -> None:
    projection = project_versioned_evidence_retrieval_configuration(_retrieval_configuration())
    lexical = dict(projection["lexical_config"])  # type: ignore[arg-type]
    lexical["trigram_threshold"] = "0.4"
    projection["lexical_config"] = lexical

    with pytest.raises(GuideRetrievalBindingValidationError, match="lexical_config artifact hash"):
        validate_retrieval_configuration_projection(projection)


def test_manifest_hash_binds_filter_source_and_retrieval_authority() -> None:
    manifest = _manifest()

    assert len(manifest.manifest_hash) == 64
    assert manifest.filter_snapshot_ref.content_sha256 != manifest.source_manifest_hash
    assert manifest.retrieval_configuration["artifact_ref"]["content_sha256"] != manifest.manifest_hash  # type: ignore[index]

    with pytest.raises(GuideRetrievalBindingValidationError, match="manifest_hash"):
        replace(manifest, source_manifest_hash="f" * 64)


@pytest.mark.parametrize(
    "value",
    [
        [{"source_snapshot_id": 1, "source_snapshot_member_id": "00000000-0000-0000-0000-000000000002"}],
        [
            {
                "source_snapshot_id": "00000000-0000-0000-0000-000000000002",
                "source_snapshot_member_id": "00000000-0000-0000-0000-000000000002",
            },
            {
                "source_snapshot_id": "00000000-0000-0000-0000-000000000001",
                "source_snapshot_member_id": "00000000-0000-0000-0000-000000000001",
            },
        ],
    ],
)
def test_member_binding_parser_rejects_coercion_and_noncanonical_order(value: object) -> None:
    with pytest.raises(GuideRetrievalBindingValidationError):
        parse_member_bindings(value)


def test_projected_configuration_hash_uses_existing_canonical_json() -> None:
    configuration = _retrieval_configuration()
    projection = project_versioned_evidence_retrieval_configuration(configuration)
    dense_config = projection["dense_config"]
    lexical_config = projection["lexical_config"]
    assert isinstance(dense_config, dict)
    assert isinstance(lexical_config, dict)
    dense_artifact_ref = dense_config["artifact_ref"]
    lexical_artifact_ref = lexical_config["artifact_ref"]
    assert isinstance(dense_artifact_ref, dict)
    assert isinstance(lexical_artifact_ref, dict)
    canonical_projection = {
        "algorithm_id": projection["algorithm_id"],
        "dense_config_hash": dense_artifact_ref["content_sha256"],
        "dense_limit": projection["dense_limit"],
        "exact_limit": projection["exact_limit"],
        "execution_mode": projection["execution_mode"],
        "expected_query_embedding_adapter_ref": projection["expected_query_embedding_adapter_ref"],
        "fts_limit": projection["fts_limit"],
        "future_reranker_input_limit": projection["future_reranker_input_limit"],
        "hybrid_limit": projection["hybrid_limit"],
        "lexical_config_hash": lexical_artifact_ref["content_sha256"],
        "lexical_limit": projection["lexical_limit"],
        "observed_score_projection": projection["observed_score_projection"],
        "observed_score_quantum": projection["observed_score_quantum"],
        "observed_score_rounding": projection["observed_score_rounding"],
        "rrf_k": projection["rrf_k"],
        "stable_coordinate_fields": projection["stable_coordinate_fields"],
        "tie_break": projection["tie_break"],
        "transaction_access": projection["transaction_access"],
        "transaction_isolation": projection["transaction_isolation"],
        "trigram_limit": projection["trigram_limit"],
    }

    assert canonical_sha256(canonical_projection) == configuration.compute_canonical_hash()
