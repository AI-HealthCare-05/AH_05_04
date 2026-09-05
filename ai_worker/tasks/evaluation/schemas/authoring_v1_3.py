from __future__ import annotations

from typing import Literal

from ai_worker.tasks.evaluation.schemas.authoring import ResourceReference
from ai_worker.tasks.evaluation.schemas.authoring_v1_2 import DatasetManifestV12


class DatasetManifestV13(DatasetManifestV12):
    schema_version: Literal["1.3.0"]  # type: ignore[assignment]  # Pydantic versioned contract override.
    authoring_identity_manifest_ref: ResourceReference
