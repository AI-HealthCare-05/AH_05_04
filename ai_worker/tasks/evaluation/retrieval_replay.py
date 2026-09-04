from __future__ import annotations

import json
from pathlib import Path


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
