from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from ai_worker.tasks.evaluation.schemas.authoring import DatasetManifest, EvidenceMappingManifest

jsonschema: Any = pytest.importorskip(
    "jsonschema",
    reason="external schema parity tests require the optional jsonschema validator",
)

EVALS_ROOT = Path(__file__).parents[3] / "evals"


def _json(relative_path: str) -> dict[str, Any]:
    return json.loads((EVALS_ROOT / relative_path).read_text(encoding="utf-8"))


def _assert_external_runtime_parity(
    *,
    schema_path: str,
    payload: dict[str, Any],
    runtime_model: type[BaseModel],
    expected_valid: bool,
) -> None:
    schema = _json(schema_path)
    external_errors = list(jsonschema.Draft202012Validator(schema).iter_errors(payload))
    if expected_valid:
        assert external_errors == []
        runtime_model.model_validate(payload)
        return

    assert external_errors
    with pytest.raises(ValidationError):
        runtime_model.model_validate(payload)


@pytest.mark.parametrize(
    ("fixture_git_commit_sha", "protected_artifact_receipt_ref", "expected_valid"),
    [
        ("a" * 40, None, True),
        (None, "fixture", True),
        ("a" * 40, "fixture", False),
        (None, None, False),
    ],
    ids=("git-only", "protected-receipt-only", "both", "neither"),
)
def test_external_dataset_schema_matches_runtime_provenance_exactly_one(
    fixture_git_commit_sha: str | None,
    protected_artifact_receipt_ref: str | None,
    expected_valid: bool,
) -> None:
    payload = _json("retrieval/manifests/dev-foundation-v1.dataset.json")
    existing_receipt = payload["protected_artifact_receipt_ref"]
    payload["fixture_git_commit_sha"] = fixture_git_commit_sha
    payload["protected_artifact_receipt_ref"] = (
        existing_receipt if protected_artifact_receipt_ref == "fixture" else None
    )

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/authoring/rag-eval.dataset-manifest.schema.json",
        payload=payload,
        runtime_model=DatasetManifest,
        expected_valid=expected_valid,
    )


@pytest.mark.parametrize(
    ("target_kind", "runtime_ref", "fixture_ref", "expected_valid"),
    [
        ("FIXTURE_RECORD", False, True, True),
        ("RUNTIME_TYPED_REF", True, False, True),
        ("FIXTURE_RECORD", True, False, False),
        ("RUNTIME_TYPED_REF", False, True, False),
        ("FIXTURE_RECORD", True, True, False),
        ("RUNTIME_TYPED_REF", True, True, False),
        ("FIXTURE_RECORD", False, False, False),
        ("RUNTIME_TYPED_REF", False, False, False),
    ],
    ids=(
        "fixture-kind-fixture-only",
        "runtime-kind-runtime-only",
        "fixture-kind-mismatch",
        "runtime-kind-mismatch",
        "fixture-kind-both",
        "runtime-kind-both",
        "fixture-kind-neither",
        "runtime-kind-neither",
    ),
)
def test_external_evidence_schema_matches_runtime_target_branch_selection(
    target_kind: str,
    runtime_ref: bool,
    fixture_ref: bool,
    expected_valid: bool,
) -> None:
    payload = _json("retrieval/evidence/dev-foundation-v1.evidence-mapping.json")
    entry = deepcopy(payload["entries"][0])
    existing_fixture_ref = entry["fixture_record_ref"]
    entry["target_kind"] = target_kind
    entry["runtime_typed_ref"] = (
        {"id": "SYNTHETIC_RUNTIME_EVIDENCE", "version": "1.0.0", "hash": "b" * 64} if runtime_ref else None
    )
    entry["fixture_record_ref"] = existing_fixture_ref if fixture_ref else None
    payload["entries"][0] = entry

    _assert_external_runtime_parity(
        schema_path="schemas/1.0.0/authoring/rag-eval.evidence-mapping-manifest.schema.json",
        payload=payload,
        runtime_model=EvidenceMappingManifest,
        expected_valid=expected_valid,
    )
