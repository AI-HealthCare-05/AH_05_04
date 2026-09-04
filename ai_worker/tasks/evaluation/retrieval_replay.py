from __future__ import annotations

import json
from pathlib import Path

from ai_worker.tasks.evaluation.runner import AdapterRequest
from ai_worker.tasks.evaluation.schemas.artifacts import CASE_RESULT_ADAPTER, CaseResult


def load_replay(path: Path) -> dict[str, tuple[str, ...]]:
    """Load a non-sensitive deterministic retrieval replay keyed by case id."""

    payload = json.loads(path.read_bytes())
    records = payload.get("case_results")
    if not isinstance(records, list):
        raise ValueError("replay case results are required")
    replay: dict[str, tuple[str, ...]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("replay case result must be an object")
        case_id = record.get("case_id")
        ranked = record.get("ranked_evidence_ids")
        if not isinstance(case_id, str) or not isinstance(ranked, list) or not all(isinstance(item, str) for item in ranked):
            raise ValueError("replay case result is invalid")
        ranked_ids = tuple(ranked)
        if len(ranked_ids) != len(set(ranked_ids)):
            raise ValueError("ranked evidence ids must be unique")
        if case_id in replay:
            raise ValueError("replay case ids must be unique")
        replay[case_id] = ranked_ids
    return replay


class ReplayRetrievalAdapter:
    """Adapts a deterministic replay into the existing retrieval result contract."""

    def __init__(self, replay: dict[str, tuple[str, ...]]) -> None:
        self._replay = replay

    def execute(self, request: AdapterRequest) -> CaseResult:
        ranked = self._replay[request.case.case_id]
        return CASE_RESULT_ADAPTER.validate_python(
            {
                "schema_id": "rag-eval.case-result",
                "schema_version": "1.0.0",
                "run_id": request.run_id,
                "case_id": request.case.case_id,
                "dataset_code": request.case.dataset_code,
                "dataset_version": request.case.dataset_version,
                "task_type": "RETRIEVAL",
                "partition": request.case.partition.value,
                "input_sha256": request.input_sha256,
                "execution_status": "COMPLETED",
                "decision_status": "N/A",
                "failure_codes": [],
                "retrieved_evidence_ids": list(ranked),
                "selected_evidence_ids": list(ranked),
                "actual_claim_ids": None,
                "actual_citation_evidence_ids": None,
                "actual_rule_ids": None,
                "actual_scope_codes": None,
                "actual_response_level": None,
                "actual_safety_disposition": None,
                "actual_execution_status": None,
                "actual_release_decision": None,
                "actual_fallback_code": None,
                "actual_provider_invocation": None,
                "actual_retrieval_invocation": True,
                "actual_publication_allowed": None,
                "actual_sections": None,
                "omitted_sections": None,
                "risk_level": None,
                "answer_sha256": None,
                "latency_ms": 0,
                "input_token_count": None,
                "output_token_count": None,
                "estimated_cost": None,
            }
        )
