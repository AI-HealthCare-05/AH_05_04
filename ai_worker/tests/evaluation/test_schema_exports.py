from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from ai_worker.tasks.evaluation.canonical import canonical_json_bytes
from ai_worker.tasks.evaluation.schema_exports import (
    normalize_schema_document,
    schema_documents,
    write_schema_documents,
)
from ai_worker.tasks.evaluation.schemas.artifacts import RESULT_ARTIFACT_MODELS


def _files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*.json"))}


def _assert_no_schema_metadata(value: object, *, parent_key: str | None = None) -> None:
    map_key_namespaces = {"$defs", "definitions", "properties", "patternProperties", "dependentSchemas"}
    if isinstance(value, list):
        for item in value:
            _assert_no_schema_metadata(item)
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        if parent_key not in map_key_namespaces:
            assert key not in {"title", "description"}
        _assert_no_schema_metadata(item, parent_key=key)


def test_schema_normalization_removes_only_non_contract_metadata_recursively() -> None:
    source: dict[str, Any] = {
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
    source: dict[str, Any] = {
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
        if "oneOf" in document and "properties" not in document:
            assert document["unevaluatedProperties"] is False
        else:
            assert document["additionalProperties"] is False
        _assert_no_schema_metadata(document)


def test_union_schema_roots_do_not_forbid_all_branch_properties() -> None:
    documents = schema_documents()

    for relative_path in (
        "authoring/rag-eval.case.schema.json",
        "artifacts/rag-eval.case-result.schema.json",
    ):
        document: Any = documents[relative_path]
        assert "oneOf" in document
        assert "additionalProperties" not in document
        assert document["unevaluatedProperties"] is False
        referenced_defs = [str(branch["$ref"]).removeprefix("#/$defs/") for branch in document["oneOf"]]
        assert referenced_defs
        assert all(document["$defs"][name]["additionalProperties"] is False for name in referenced_defs)


def test_exported_schema_encodes_execution_decision_and_receipt_feasibility() -> None:
    documents = schema_documents()
    run_schema = documents["artifacts/rag-eval.run.schema.json"]
    receipt_schema = documents["operational/rag-eval.validation-receipt.schema.json"]

    assert run_schema["allOf"]
    assert receipt_schema["oneOf"] == [
        {
            "properties": {
                "execution_status": {"const": "COMPLETED"},
                "decision_status": {"const": "N/A"},
            },
            "required": ["execution_status", "decision_status"],
        },
        {
            "properties": {
                "execution_status": {"enum": ["INVALID", "ERROR"]},
                "decision_status": {"type": "null"},
            },
            "required": ["execution_status", "decision_status"],
        },
    ]


def test_schema_normalization_removes_metadata_only_from_schema_locations() -> None:
    source: dict[str, Any] = {
        "title": "root metadata",
        "description": "root metadata",
        "type": "object",
        "properties": {
            "title": {"title": "field metadata", "type": "string"},
            "description": {"description": "field metadata", "type": "string"},
        },
        "$defs": {"title": {"title": "definition metadata", "type": "string"}},
    }

    normalized = normalize_schema_document(source)

    assert normalized == {
        "type": "object",
        "properties": {"title": {"type": "string"}, "description": {"type": "string"}},
        "$defs": {"title": {"type": "string"}},
    }


def test_committed_schema_files_match_fresh_canonical_export_byte_for_byte(tmp_path: Path) -> None:
    write_schema_documents(tmp_path)

    committed_root = Path("evals/schemas/1.0.0")
    assert _files(tmp_path) == _files(committed_root)
    assert all(content == canonical_json_bytes(schema_documents()[path]) for path, content in _files(tmp_path).items())
