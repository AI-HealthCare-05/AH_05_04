from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, TypeAdapter

from ai_worker.tasks.evaluation.schemas.artifacts import RESULT_ARTIFACT_MODELS, ValidationReceipt
from ai_worker.tasks.evaluation.schemas.authoring import (
    EVALUATION_CASE_ADAPTER,
    CriticalClaimRubric,
    DatasetManifest,
    EvidenceMappingManifest,
    ProtectedArtifactReceipt,
)
from ai_worker.tasks.evaluation.schemas.authoring_v1_1 import (
    EVALUATION_CASE_ADAPTER_V1_1,
    DatasetManifestV11,
)
from ai_worker.tasks.evaluation.schemas.policy import (
    ComparisonPolicy,
    EvaluationPolicy,
    EvaluationProfile,
    SuiteDefinition,
)

SCHEMA_VERSION = "1.0.0"
type SchemaSource = type[BaseModel] | TypeAdapter[Any]


@dataclass(frozen=True, slots=True)
class SchemaRegistryEntry:
    relative_path: str
    schema_id: str
    source: SchemaSource
    member_version: str = SCHEMA_VERSION

    @property
    def logical_name(self) -> str:
        return self.schema_id.removeprefix("rag-eval.")

    @property
    def urn(self) -> str:
        return f"urn:ah05:rag-eval:schema:{self.logical_name}:{self.member_version}"


SCHEMA_REGISTRY: tuple[SchemaRegistryEntry, ...] = (
    SchemaRegistryEntry("authoring/rag-eval.case.schema.json", "rag-eval.case", EVALUATION_CASE_ADAPTER),
    SchemaRegistryEntry(
        "authoring/rag-eval.dataset-manifest.schema.json", "rag-eval.dataset-manifest", DatasetManifest
    ),
    SchemaRegistryEntry(
        "authoring/rag-eval.evidence-mapping-manifest.schema.json",
        "rag-eval.evidence-mapping-manifest",
        EvidenceMappingManifest,
    ),
    SchemaRegistryEntry(
        "authoring/rag-eval.critical-claim-rubric.schema.json",
        "rag-eval.critical-claim-rubric",
        CriticalClaimRubric,
    ),
    SchemaRegistryEntry(
        "policy/rag-eval.evaluation-profile.schema.json", "rag-eval.evaluation-profile", EvaluationProfile
    ),
    SchemaRegistryEntry("policy/rag-eval.suite-definition.schema.json", "rag-eval.suite-definition", SuiteDefinition),
    SchemaRegistryEntry(
        "policy/rag-eval.comparison-policy.schema.json", "rag-eval.comparison-policy", ComparisonPolicy
    ),
    SchemaRegistryEntry(
        "policy/rag-eval.evaluation-policy.schema.json", "rag-eval.evaluation-policy", EvaluationPolicy
    ),
    *(
        SchemaRegistryEntry(f"artifacts/{schema_id}.schema.json", schema_id, model)
        for schema_id, model in RESULT_ARTIFACT_MODELS.items()
    ),
    SchemaRegistryEntry(
        "operational/rag-eval.validation-receipt.schema.json",
        "rag-eval.validation-receipt",
        ValidationReceipt,
    ),
    SchemaRegistryEntry(
        "operational/rag-eval.protected-artifact-receipt.schema.json",
        "rag-eval.protected-artifact-receipt",
        ProtectedArtifactReceipt,
    ),
)

if (
    len({entry.relative_path for entry in SCHEMA_REGISTRY}) != 18
    or len({entry.schema_id for entry in SCHEMA_REGISTRY}) != 18
):
    raise RuntimeError("RAG evaluation schema registry must contain exactly 18 unique entries")


SCHEMA_REGISTRY_V1_1: tuple[SchemaRegistryEntry, ...] = (
    SchemaRegistryEntry(
        "authoring/rag-eval.case.schema.json",
        "rag-eval.case",
        EVALUATION_CASE_ADAPTER_V1_1,
        "1.1.0",
    ),
    SchemaRegistryEntry(
        "authoring/rag-eval.dataset-manifest.schema.json",
        "rag-eval.dataset-manifest",
        DatasetManifestV11,
        "1.1.0",
    ),
    *SCHEMA_REGISTRY[2:],
)

SCHEMA_REGISTRIES = MappingProxyType(
    {
        "1.0.0": SCHEMA_REGISTRY,
        "1.1.0": SCHEMA_REGISTRY_V1_1,
    }
)

for schema_set_version, registry in SCHEMA_REGISTRIES.items():
    if len({entry.relative_path for entry in registry}) != 18 or len({entry.schema_id for entry in registry}) != 18:
        raise RuntimeError(f"RAG evaluation schema registry {schema_set_version} must contain 18 unique entries")
