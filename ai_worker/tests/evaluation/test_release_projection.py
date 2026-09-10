from __future__ import annotations

from ai_worker.tasks.evaluation.projections import release_gate_json, render_release_gate
from ai_worker.tasks.evaluation.release_gate import GateEvidence, ReleaseGatePolicy, build_release_gate
from ai_worker.tasks.evaluation.schemas.artifacts import GateResult
from ai_worker.tasks.evaluation.schemas.common import ExperimentType, ImmutableReference, Partition


def _ref(identifier: str, value: str) -> ImmutableReference:
    return ImmutableReference(id=identifier, version="1.0.0", hash=value * 64)


def test_release_gate_markdown_is_a_deterministic_projection_of_gate_json() -> None:
    policy = ReleaseGatePolicy(
        evaluation_policy_ref=_ref("release-policy", "a"),
        evaluation_profile_ref=_ref("release-profile", "b"),
        comparison_policy_ref=_ref("release-comparison", "c"),
        runtime_eligible=False,
        required_experiment_types=(ExperimentType.END_TO_END_RAG,),
        required_partitions=(Partition.HOLDOUT, Partition.SAFETY_REGRESSION),
        required_metrics=(),
        required_suites=(),
        required_receipts=(),
        paired_comparison_receipt_id=None,
        required_case_ids=(),
        controlled_variable_keys=(),
        required_scope_manifest_hash="d" * 64,
    )
    evidence = GateEvidence(
        run_id="11111111-1111-4111-8111-111111111111",
        required_scope_manifest_hash="d" * 64,
        completed_experiment_types=(),
        completed_partitions=(),
        metrics=(),
        suites=(),
        receipts=(),
        paired_case_evidence=None,
    )
    gate = build_release_gate(policy, evidence)

    first = render_release_gate(gate)
    second = render_release_gate(gate)
    json_first = release_gate_json(gate)
    json_second = release_gate_json(gate)

    assert first == second
    assert json_first == json_second
    assert GateResult.model_validate_json(json_first) == gate
    assert b"# RAG Evaluation Release Gate" in first
    assert b"`NOT_EVALUATED`" in first
    assert b"`PROFILE_NOT_RUNTIME_ELIGIBLE`" in first
