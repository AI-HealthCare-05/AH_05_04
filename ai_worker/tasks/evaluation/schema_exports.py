from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes
from ai_worker.tasks.evaluation.schemas.artifacts import RESULT_ARTIFACT_MODELS, ValidationReceipt
from ai_worker.tasks.evaluation.schemas.authoring import (
    EVALUATION_CASE_ADAPTER,
    CriticalClaimRubric,
    DatasetManifest,
    EvidenceMappingManifest,
)
from ai_worker.tasks.evaluation.schemas.policy import (
    ComparisonPolicy,
    EvaluationPolicy,
    EvaluationProfile,
    SuiteDefinition,
)

_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
_SCHEMA_VERSION = "1.0.0"

type SchemaSource = type[BaseModel] | TypeAdapter[Any]

_AUTHORING_MODELS: dict[str, SchemaSource] = {
    "rag-eval.case": EVALUATION_CASE_ADAPTER,
    "rag-eval.dataset-manifest": DatasetManifest,
    "rag-eval.evidence-mapping-manifest": EvidenceMappingManifest,
    "rag-eval.critical-claim-rubric": CriticalClaimRubric,
}
_POLICY_MODELS: dict[str, SchemaSource] = {
    "rag-eval.evaluation-profile": EvaluationProfile,
    "rag-eval.suite-definition": SuiteDefinition,
    "rag-eval.comparison-policy": ComparisonPolicy,
    "rag-eval.evaluation-policy": EvaluationPolicy,
}


def normalize_schema_document(document: dict[str, JsonValue]) -> dict[str, JsonValue]:
    map_key_namespaces = {"$defs", "definitions", "properties", "patternProperties", "dependentSchemas"}

    def normalize(value: JsonValue, *, parent_key: str | None = None) -> JsonValue:
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            return {
                key: normalize(item, parent_key=key)
                for key, item in value.items()
                if parent_key in map_key_namespaces or key not in {"title", "description"}
            }
        return value

    return cast(dict[str, JsonValue], normalize(document))


def _model_schema(model: SchemaSource) -> dict[str, JsonValue]:
    if isinstance(model, TypeAdapter):
        return cast(dict[str, JsonValue], model.json_schema(mode="validation"))
    return cast(dict[str, JsonValue], model.model_json_schema(mode="validation"))


def _schema_document(schema_id: str, model: SchemaSource) -> dict[str, JsonValue]:
    document = _model_schema(model)
    document["$schema"] = _DRAFT_2020_12
    document["$id"] = f"urn:rag-eval:schema:{schema_id}:{_SCHEMA_VERSION}"
    document["additionalProperties"] = False
    return normalize_schema_document(document)


def schema_documents() -> dict[str, dict[str, JsonValue]]:
    documents: dict[str, dict[str, JsonValue]] = {}
    for schema_id, model in _AUTHORING_MODELS.items():
        documents[f"authoring/{schema_id}.schema.json"] = _schema_document(schema_id, model)
    for schema_id, model in _POLICY_MODELS.items():
        documents[f"policy/{schema_id}.schema.json"] = _schema_document(schema_id, model)
    for schema_id, model in RESULT_ARTIFACT_MODELS.items():
        documents[f"artifacts/{schema_id}.schema.json"] = _schema_document(schema_id, model)
    documents["operational/rag-eval.validation-receipt.schema.json"] = _schema_document(
        "rag-eval.validation-receipt", ValidationReceipt
    )
    return dict(sorted(documents.items()))


def write_schema_documents(root: Path) -> None:
    for relative_path, document in schema_documents().items():
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(canonical_json_bytes(document))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export deterministic RAG evaluation JSON Schemas")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    write_schema_documents(args.output)


if __name__ == "__main__":
    main()
