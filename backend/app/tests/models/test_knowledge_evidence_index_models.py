from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql

from app.core.db.databases import Base
from app.models.knowledge import (
    KnowledgeDocumentContractVersion,
    RagKnowledgeDistanceMetric,
)
from app.models.rag_source import RagSourceSnapshotMemberKind


def test_knowledge_evidence_index_tables_are_registered() -> None:
    assert {
        "rag_source_snapshot_member",
        "rag_knowledge_index",
        "rag_knowledge_index_member",
    }.issubset(Base.metadata.tables)


def test_knowledge_document_and_chunk_expose_legacy_safe_contract_columns() -> None:
    document = Base.metadata.tables["knowledge_document"]
    chunk = Base.metadata.tables["knowledge_chunk"]

    assert {
        "record_contract_version",
        "source_snapshot_member_id",
        "external_document_id",
        "document_content_hash",
        "canonicalization_spec_version",
        "knowledge_index_lock_marker",
    }.issubset(document.c.keys())
    assert {"content_hash", "normalization_version", "knowledge_index_lock_marker"}.issubset(chunk.c.keys())
    assert document.c.source_url.nullable
    assert document.c.publisher.nullable
    assert document.c.document_version.nullable
    assert chunk.c.embedding_model.nullable
    assert chunk.c.vector_store_key.nullable
    assert {member.value for member in KnowledgeDocumentContractVersion} == {
        "LEGACY_V1",
        "KNOWLEDGE_EVIDENCE_V1",
    }


def test_source_snapshot_member_has_one_origin_shape() -> None:
    table = Base.metadata.tables["rag_source_snapshot_member"]

    assert set(table.c.keys()) == {
        "id",
        "source_snapshot_id",
        "member_kind",
        "endpoint_id",
        "operation_id",
        "ingestion_artifact_id",
        "locator",
        "content_sha256",
        "knowledge_index_lock_marker",
        "created_at",
    }
    assert {member.value for member in RagSourceSnapshotMemberKind} == {
        "ENDPOINT_OPERATION",
        "ARTIFACT",
    }
    assert "chk_rag_source_snapshot_member_origin" in {
        constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint)
    }


def test_index_and_member_columns_and_constraints_are_exact() -> None:
    index = Base.metadata.tables["rag_knowledge_index"]
    member = Base.metadata.tables["rag_knowledge_index_member"]

    assert set(index.c.keys()) == {
        "id",
        "index_code",
        "index_version",
        "corpus_manifest_hash",
        "embedding_manifest_hash",
        "index_configuration_hash",
        "embedding_model_ref",
        "embedding_model_version",
        "embedding_dimension",
        "distance_metric",
        "member_count",
        "knowledge_index_lock_marker",
        "created_at",
    }
    assert set(member.c.keys()) == {
        "id",
        "knowledge_index_id",
        "knowledge_chunk_id",
        "source_snapshot_id",
        "source_snapshot_member_id",
        "source_code",
        "source_version",
        "canonical_checksum",
        "external_document_id",
        "chunk_index",
        "content_hash",
        "embedding",
        "embedding_sha256",
        "member_order",
        "created_at",
    }
    assert str(member.c.embedding.type.compile(dialect=postgresql.dialect())) == "VECTOR"
    assert {metric.value for metric in RagKnowledgeDistanceMetric} == {"COSINE"}
    assert {
        "uq_rag_knowledge_index_version",
    } == {constraint.name for constraint in index.constraints if isinstance(constraint, UniqueConstraint)}
    assert {
        "uq_rag_knowledge_index_member_order",
        "uq_rag_knowledge_index_member_chunk",
        "uq_rag_knowledge_index_member_coordinate",
    }.issubset(constraint.name for constraint in member.constraints if isinstance(constraint, UniqueConstraint))
