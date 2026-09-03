from ai_worker.tasks.evaluation.canonical import canonical_sha256
from ai_worker.tasks.evaluation.manifest import CaseInputBinding, case_input_sha256


def _case_binding(*, resolved_hash: str) -> CaseInputBinding:
    return CaseInputBinding(
        case_id="case-001",
        task_type="RETRIEVAL",
        partition="DEV",
        case_resource_sha256="1" * 64,
        dataset_manifest_sha256="2" * 64,
        evidence_mapping_manifest_sha256="3" * 64,
        critical_claim_rubric_hash="4" * 64,
        resolved_evaluation_config_hash=resolved_hash,
    )


def test_case_input_hash_binds_case_task_dataset_evidence_rubric_and_config() -> None:
    first = case_input_sha256(_case_binding(resolved_hash="a" * 64))
    changed = case_input_sha256(_case_binding(resolved_hash="b" * 64))

    assert first != changed
    assert first == canonical_sha256(
        {
            "case_id": "case-001",
            "task_type": "RETRIEVAL",
            "partition": "DEV",
            "case_resource_sha256": "1" * 64,
            "dataset_manifest_sha256": "2" * 64,
            "evidence_mapping_manifest_sha256": "3" * 64,
            "critical_claim_rubric_hash": "4" * 64,
            "resolved_evaluation_config_hash": "a" * 64,
        }
    )
