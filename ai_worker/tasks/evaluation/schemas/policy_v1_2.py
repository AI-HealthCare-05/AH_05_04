from __future__ import annotations

from typing import Literal

from ai_worker.tasks.evaluation.schemas.common_v1_2 import ReviewProvenanceV12
from ai_worker.tasks.evaluation.schemas.policy import (
    EvaluationPolicy,
    EvaluationProfile,
    SuiteDefinition,
)


class EvaluationProfileV12(EvaluationProfile):
    schema_version: Literal["1.2.0"]  # type: ignore[assignment]  # Pydantic versioned contract override.
    review_provenance: ReviewProvenanceV12  # type: ignore[assignment]  # Pydantic versioned contract override.


class SuiteDefinitionV12(SuiteDefinition):
    schema_version: Literal["1.2.0"]  # type: ignore[assignment]  # Pydantic versioned contract override.
    review_provenance: ReviewProvenanceV12  # type: ignore[assignment]  # Pydantic versioned contract override.


class EvaluationPolicyV12(EvaluationPolicy):
    schema_version: Literal["1.2.0"]  # type: ignore[assignment]  # Pydantic versioned contract override.
    review_provenance: ReviewProvenanceV12  # type: ignore[assignment]  # Pydantic versioned contract override.
