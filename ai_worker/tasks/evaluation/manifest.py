from __future__ import annotations

from dataclasses import asdict, dataclass

from ai_worker.tasks.evaluation.canonical import canonical_sha256


@dataclass(frozen=True, slots=True)
class CaseInputBinding:
    case_id: str
    task_type: str
    partition: str
    case_resource_sha256: str
    dataset_manifest_sha256: str
    evidence_mapping_manifest_sha256: str
    critical_claim_rubric_hash: str
    resolved_evaluation_config_hash: str


def case_input_sha256(binding: CaseInputBinding) -> str:
    return canonical_sha256(asdict(binding))
