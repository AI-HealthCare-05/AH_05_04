from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes
from ai_worker.tasks.evaluation.schema_exports import (
    normalize_schema_document,
    schema_documents,
    write_schema_documents,
)
from ai_worker.tasks.evaluation.schemas.artifacts import RESULT_ARTIFACT_MODELS


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*.json"))}


def test_schema_normalization_removes_only_non_contract_metadata_recursively() -> None:
    source = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:rag-eval:test:1.0.0",
        "title": "remove",
        "description": "remove",
        "type": "object",
        "$defs": {
            "Value": {
                "title": "remove nested",
                "description": "remove nested",
                "type": "string",
                "minLength": 1,
            }
        },
    }
    original = deepcopy(source)

    normalized = normalize_schema_document(source)

    assert source == original
    assert normalized == {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:rag-eval:test:1.0.0",
        "type": "object",
        "$defs": {"Value": {"type": "string", "minLength": 1}},
    }


def test_schema_normalization_preserves_contract_fields_named_title_or_description() -> None:
    source = {
        "type": "object",
        "properties": {
            "title": {"title": "metadata", "type": "string", "minLength": 1},
            "description": {"description": "metadata", "type": "string", "maxLength": 500},
        },
        "required": ["title", "description"],
        "additionalProperties": False,
    }

    normalized = normalize_schema_document(source)

    assert normalized["properties"] == {
        "title": {"type": "string", "minLength": 1},
        "description": {"type": "string", "maxLength": 500},
    }


def test_schema_documents_are_complete_strict_draft_2020_12_contracts() -> None:
    documents = schema_documents()

    assert len(documents) == 17
    assert len(RESULT_ARTIFACT_MODELS) == 8
    assert "operational/rag-eval.validation-receipt.schema.json" in documents
    for relative_path, document in documents.items():
        assert relative_path.endswith(".schema.json")
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert str(document["$id"]).startswith("urn:rag-eval:schema:")
        assert document["additionalProperties"] is False
        assert "title" not in canonical_json_bytes(document).decode()
        assert "description" not in canonical_json_bytes(document).decode()


def test_committed_schema_files_match_fresh_canonical_export_byte_for_byte(tmp_path: Path) -> None:
    write_schema_documents(tmp_path)

    committed_root = Path("evals/schemas/1.0.0")
    assert _files(tmp_path) == _files(committed_root)
    assert all(content == canonical_json_bytes(schema_documents()[path]) for path, content in _files(tmp_path).items())
