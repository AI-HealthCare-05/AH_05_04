import importlib
import inspect


def test_knowledge_evidence_index_revision_contract() -> None:
    revision = importlib.import_module("backend.alembic.versions.178a1b2c3d4e_knowledge_evidence_index_foundation")
    source = inspect.getsource(revision)

    assert revision.down_revision == "e8c41a09d652"
    assert "CREATE EXTENSION IF NOT EXISTS vector" in source
    assert "vector_dims(embedding) BETWEEN 1 AND 2000" in source


def test_knowledge_evidence_index_downgrade_refuses_populated_state() -> None:
    revision = importlib.import_module("backend.alembic.versions.178a1b2c3d4e_knowledge_evidence_index_foundation")
    source = inspect.getsource(revision.downgrade)

    assert "rag_source_snapshot_member" in source
    assert "rag_knowledge_index" in source
    assert "rag_knowledge_index_member" in source
    assert "LOCK TABLE knowledge_document IN SHARE ROW EXCLUSIVE MODE" in source
    assert "LOCK TABLE knowledge_chunk IN SHARE ROW EXCLUSIVE MODE" in source
    assert "would lose Knowledge Evidence Index data" in source
