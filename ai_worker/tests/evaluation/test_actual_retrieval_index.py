from __future__ import annotations

import math
from pathlib import Path

import pytest

from ai_worker.tasks.evaluation.actual_retrieval_index import (
    DeterministicFakeEmbeddingAdapter,
    load_synthetic_knowledge_statements,
)
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError


@pytest.mark.asyncio
async def test_deterministic_fake_embedding_adapter_properties() -> None:
    adapter = DeterministicFakeEmbeddingAdapter()

    v1 = await adapter.embed_query("첫 번째 쿼리")
    v2 = await adapter.embed_query("첫 번째 쿼리")
    v3 = await adapter.embed_query("두 번째 쿼리")

    assert len(v1.reveal()) == 1536
    assert v1.reveal() == v2.reveal()
    assert v1.reveal() != v3.reveal()

    norm = math.sqrt(sum(x * x for x in v1.reveal()))
    assert math.isclose(norm, 1.0, rel_tol=1e-5)


def test_load_synthetic_knowledge_statements_success() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    index_path = (
        repo_root
        / "evals/retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json"
    )

    statements = load_synthetic_knowledge_statements(index_path)
    assert len(statements) == 100
    assert statements[0]["evidence_ref_id"].startswith("ev-nlr-")
    assert len(statements[0]["content_sha256"]) == 64


def test_load_synthetic_knowledge_statements_count_mismatch_fails(tmp_path: Path) -> None:
    tampered_file = tmp_path / "tampered_count.json"
    tampered_file.write_text(
        '{"records": [{"evidence_ref_id": "ev-01", "statement": "test", "content_sha256": "wrong_hash"}]}',
        encoding="utf-8",
    )

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_synthetic_knowledge_statements(tampered_file)
    assert exc_info.value.code == EvaluationErrorCode.RESOURCE_BYTES_INVALID


def test_load_synthetic_knowledge_statements_hash_mismatch_fails(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    index_path = (
        repo_root
        / "evals/retrieval/evidence/resources/rag-natural-language-retrieval-dev-v1/synthetic-knowledge-index.json"
    )
    import json

    data = json.loads(index_path.read_text(encoding="utf-8"))
    data["records"][0]["content_sha256"] = "0" * 64

    tampered_file = tmp_path / "tampered_hash.json"
    tampered_file.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(EvaluationValidationError) as exc_info:
        load_synthetic_knowledge_statements(tampered_file)
    assert exc_info.value.code == EvaluationErrorCode.HASH_MISMATCH
