import importlib
import inspect


def test_knowledge_evidence_index_revision_contract() -> None:
    revision = importlib.import_module("backend.alembic.versions.178a1b2c3d4e_knowledge_evidence_index_foundation")
    source = inspect.getsource(revision)

    assert revision.down_revision == "166f30415263"
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


def test_retrieval_run_revision_contract() -> None:
    revision = importlib.import_module("backend.alembic.versions.178c2d3e4f50_create_retrieval_run_tables")
    source = inspect.getsource(revision)

    assert isinstance(revision.down_revision, str) and revision.down_revision
    assert (
        'op.create_table(\n        "retrieval_run"' in source or 'op.create_table(\n        "retrieval_run"' in source
    )
    assert "retrieval_signal" in source
    assert "retrieval_hit" in source
    assert "uq_retrieval_run_job_node" in source
    assert "uq_retrieval_signal_run_method_rank" in source
    assert "uq_retrieval_hit_run_rrf_rank" in source
    assert "uq_retrieval_hit_run_final_rank" in source


def test_retrieval_run_downgrade_contract() -> None:
    revision = importlib.import_module("backend.alembic.versions.178c2d3e4f50_create_retrieval_run_tables")
    source = inspect.getsource(revision.downgrade)

    # Children must be dropped before parent
    hit_idx = source.index('op.drop_table("retrieval_hit")')
    sig_idx = source.index('op.drop_table("retrieval_signal")')
    run_idx = source.index('op.drop_table("retrieval_run")')
    assert hit_idx < run_idx
    assert sig_idx < run_idx
