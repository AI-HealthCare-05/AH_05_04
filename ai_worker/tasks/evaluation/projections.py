from __future__ import annotations

from typing import cast

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes
from ai_worker.tasks.evaluation.schemas.artifacts import GateResult, RequiredGateMember


def _member_row(member: RequiredGateMember) -> str:
    decision = member.decision_status.value if member.decision_status is not None else "null"
    return (
        f"| `{member.member_type.value}` | `{member.member_id}@{member.member_version}` | "
        f"`{member.execution_status.value}` | `{decision}` | `{member.member_hash}` |"
    )


def render_release_gate(gate: GateResult) -> bytes:
    """Render a deterministic human projection from the validated gate payload."""

    decision = gate.aggregate_decision_status.value if gate.aggregate_decision_status is not None else "null"
    members = (*gate.required_metrics, *gate.required_suites, *gate.required_contract_receipts)
    lines = [
        "# RAG Evaluation Release Gate",
        "",
        "> Projection only — `release-gate.json` is the machine authority.",
        "",
        f"- Run ID: `{gate.run_id}`",
        f"- Execution Status: `{gate.aggregate_execution_status.value}`",
        f"- Decision Status: `{decision}`",
        f"- Required Scope Manifest: `{gate.required_scope_manifest_hash}`",
        "",
        "## Required Members",
        "",
        "| Type | Member | Execution | Decision | Artifact Hash |",
        "| --- | --- | --- | --- | --- |",
        *[_member_row(member) for member in members],
        "",
        "## Blocking Reasons",
        "",
        *([f"- `{code}`" for code in gate.blocking_reason_codes] or ["- None"]),
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def release_gate_json(gate: GateResult) -> bytes:
    """Serialize the machine authority with the repository canonical JSON rules."""

    return canonical_json_bytes(cast(JsonValue, gate.model_dump(mode="json")))
