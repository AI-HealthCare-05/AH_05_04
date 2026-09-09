from __future__ import annotations

import re
import warnings
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from ai_worker.tasks.evaluation import natural_language_retrieval_validation as validation_module
from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_json_bytes, canonical_sha256, sha256_hex
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import load_dataset, parse_json_object_bytes
from ai_worker.tasks.evaluation.natural_language_retrieval_validation import (
    Issue273ValidationStatus,
    ValidationCheck,
    _reject_forbidden_keys,
    _reject_unverified_metric_fields,
    parse_status_bytes,
    render_report,
)

REPOSITORY_ROOT = Path(__file__).parents[3]
STATUS_PATH = REPOSITORY_ROOT / "docs/validation/rag/issue-273/status.json"
REPORT_PATH = REPOSITORY_ROOT / "docs/validation/rag/issue-273/report.md"
EVALS_ROOT = REPOSITORY_ROOT / "evals"
DATASET_MANIFEST_PATH = EVALS_ROOT / "retrieval/manifests/rag-natural-language-retrieval-dev-v1.dataset.json"
EVALS_README_PATH = EVALS_ROOT / "README.md"
DATASET_APPROVAL_EVIDENCE_PATH = EVALS_ROOT / "provenance/rag-natural-language-retrieval-dev-v1.approval-evidence.json"
HOLDOUT_PREPARATION_PATH = REPOSITORY_ROOT / "docs/validation/rag/issue-273/holdout-freeze-preparation.json"
PROTECTED_RUNNER_FOUNDATION_PATH = REPOSITORY_ROOT / "docs/validation/rag/issue-273/protected-runner-foundation.json"
SCHEMA_SET_HASH = "ca1f324c701dd5e86d811a4430ddbf2d394bd3aa0e7eb0e32dabcb8b63d1e325"
DATASET_MANIFEST_HASH = "b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2"
GOLD_REVIEW_EVIDENCE_HASH = "6dd83d9c258499fb0d543870e5a99a913abb0b2dcb3c11e4b72855e43c235776"
DATASET_APPROVAL_EVIDENCE_HASH = "3b1a90ba0f9a6c06162ce953bdb7e0d504f76074d415a807611812d16ac29896"
HOLDOUT_PREPARATION_HASH = "40ea344c378298d99c14c372c27296322854d8e9b055fa179592568ca88bc192"
HOLDOUT_PREPARATION_SELF_HASH = "b4a0a113d9efce867a434875f18ee259431d226a9cf1e4dcaed28152920600b6"
PROTECTED_RUNNER_FOUNDATION_HASH = "7311f9e934b5cda8a87f14ddf986057a54be79e0a215937ce2345af08b3593d5"
PROTECTED_RUNNER_FOUNDATION_SELF_HASH = "7c77b159df066b96aaf22c7ad431ab114dcf147fc6067e5b871bf8ea49e98d5c"


def _status_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.2.1",
        "issue": "#273",
        "phase": "PHASE_B3_PROTECTED_RUNNER_FOUNDATION",
        "status_label": "Phase B3 · Protected Runner Policy Foundation Implemented",
        "schema_set_status": "REVIEW_REQUIRED",
        "dataset_ref": "rag-natural-language-retrieval-dev@1.0.0",
        "planned_counts": {
            "dev_questions": 60,
            "holdout_questions": 40,
            "topics": 5,
            "expression_types": 6,
            "independent_groups": 20,
        },
        "created_counts": {
            "dev_questions": 60,
            "holdout_questions": 0,
            "gold_records": 20,
            "corpus_records": 100,
            "topics": 5,
            "expression_types": 6,
            "independent_groups": 20,
        },
        "dataset_manifest_sha256": DATASET_MANIFEST_HASH,
        "schema_set_ref": {
            "id": "rag-eval.schema-set",
            "version": "1.3.0",
            "hash": SCHEMA_SET_HASH,
        },
        "schema_set_decision": "docs/governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md",
        "responsible_reviewer": "@hazelnutflavoured",
        "approval_transition": "DEV_DATASET_CUSTODIAN_APPROVAL_RECORDED",
        "dataset_status": "DRAFT",
        "gold_review_status": "APPROVED",
        "gold_review_evidence_ref": {
            "id": "github-pr-341-review-5137833200",
            "version": "1.0.0",
            "hash": GOLD_REVIEW_EVIDENCE_HASH,
        },
        "dataset_approval_evidence_ref": {
            "id": "github-pr-354-review-5139907268",
            "version": "1.0.0",
            "hash": DATASET_APPROVAL_EVIDENCE_HASH,
        },
        "holdout_preparation_ref": {
            "id": "issue-273-holdout-freeze-preparation",
            "version": "1.0.0",
            "raw_sha256": HOLDOUT_PREPARATION_HASH,
            "self_sha256": HOLDOUT_PREPARATION_SELF_HASH,
        },
        "holdout_preparation_status": "PREPARATION_READY",
        "protected_runner_foundation_ref": {
            "id": "issue-273-protected-runner-foundation",
            "version": "1.0.0",
            "raw_sha256": PROTECTED_RUNNER_FOUNDATION_HASH,
            "self_sha256": PROTECTED_RUNNER_FOUNDATION_SELF_HASH,
        },
        "protected_runner_issue_status": "CREATED",
        "policy_foundation_status": "IMPLEMENTED",
        "effective_enforcement_status": "NOT_IMPLEMENTED",
        "infrastructure_adapter_status": "NOT_IMPLEMENTED",
        "reconciliation_adapter_status": "NOT_IMPLEMENTED",
        "holdout_freeze_status": "NOT_STARTED",
        "adapter_status": "NOT_IMPLEMENTED",
        "actual_run_ref": None,
        "release_eligible": False,
        "blocking_codes": [
            "BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER",
            "BLOCKED_BY_RAG_14_ADAPTER",
            "WAITING_FOR_HOLDOUT_ACCESS_AUTHORIZATION",
            "WAITING_FOR_HOLDOUT_FREEZE",
        ],
        "checks": [
            {
                "check_id": "PHASE_A_DEV_FIXTURE",
                "command": "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py -q",
                "exit_code": 0,
                "result": "26 passed",
            },
            {
                "check_id": "PHASE_A_LOADER",
                "command": "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_authoring_identity_loader.py ai_worker/tests/evaluation/test_loaders.py -q",
                "exit_code": 0,
                "result": "132 passed",
            },
            {
                "check_id": "PHASE_A_REPORT_PROJECTION",
                "command": "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_validation_report.py -q",
                "exit_code": 0,
                "result": "51 passed",
            },
            {
                "check_id": "PHASE_A_SCHEMA_EXPORT",
                "command": "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_schema_exports.py ai_worker/tests/evaluation/test_external_schema_parity.py ai_worker/tests/evaluation/test_provenance_v1_schemas.py -q",
                "exit_code": 0,
                "result": "94 passed, 7 skipped",
            },
            {
                "check_id": "PHASE_B_DATASET_APPROVAL_PROVENANCE",
                "command": "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py::test_issue_273_graph_records_the_actual_dataset_custodian_approval_event -q",
                "exit_code": 0,
                "result": "1 passed",
            },
            {
                "check_id": "PHASE_B_GOLD_REVIEW_PROVENANCE",
                "command": "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py::test_issue_273_graph_records_only_the_actual_gold_review_event -q",
                "exit_code": 0,
                "result": "1 passed",
            },
            {
                "check_id": "PHASE_B_HOLDOUT_FREEZE_PREPARATION",
                "command": "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_holdout_preparation.py -q",
                "exit_code": 0,
                "result": "27 passed",
            },
            {
                "check_id": "PHASE_B3_PROTECTED_RUNNER_FOUNDATION",
                "command": "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_natural_language_retrieval_protected_runner_foundation.py ai_worker/tests/evaluation/test_protected_retrieval.py -q",
                "exit_code": 0,
                "result": "59 passed",
            },
        ],
        "updated_at": "2026-09-09T00:00:00.000000Z",
        "status_sha256": "0" * 64,
    }
    payload["checks"].sort(key=lambda item: item["check_id"].encode("utf-16-be"))
    payload["status_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"status_sha256"}))
    return payload


def _status_bytes(payload: dict[str, Any]) -> bytes:
    return canonical_json_bytes(payload)


def _assert_status_matches_committed_dataset(status: Issue273ValidationStatus) -> None:
    manifest_payload: dict[str, Any] = parse_json_object_bytes(DATASET_MANIFEST_PATH.read_bytes())
    declared_manifest_hash = manifest_payload["manifest_sha256"]
    recomputed_manifest_hash = canonical_sha256(
        manifest_payload,
        excluded_top_level_keys=frozenset({"manifest_sha256"}),
    )
    assert recomputed_manifest_hash == declared_manifest_hash == status.dataset_manifest_sha256

    loaded = load_dataset(DATASET_MANIFEST_PATH, evals_root=EVALS_ROOT)
    loaded_manifest = loaded.manifest.model_dump(mode="json")
    assert status.dataset_ref == f"{loaded_manifest['dataset_code']}@{loaded_manifest['dataset_version']}"
    assert status.dataset_status == loaded_manifest["status"]
    approval_evidence_hash = sha256_hex(DATASET_APPROVAL_EVIDENCE_PATH.read_bytes())
    assert approval_evidence_hash == status.dataset_approval_evidence_ref.hash
    assert (
        status.dataset_approval_evidence_ref.model_dump(mode="json")
        in loaded_manifest["review_provenance"]["evidence_review_refs"]
    )
    assert sha256_hex(HOLDOUT_PREPARATION_PATH.read_bytes()) == status.holdout_preparation_ref.raw_sha256

    cases = [case.model_dump(mode="json") for case in loaded.cases]
    required_gold_ids = {evidence_id for case in cases for evidence_id in case["expected"]["required_evidence_refs"]}
    mapping = loaded.evidence_mapping.model_dump(mode="json")
    mapping_by_id = {entry["evidence_ref_id"]: entry for entry in mapping["entries"]}
    corpus_paths = {mapping_by_id[evidence_id]["fixture_record_ref"]["path"] for evidence_id in required_gold_ids}
    assert len(corpus_paths) == 1
    corpus = parse_json_object_bytes((EVALS_ROOT / corpus_paths.pop()).read_bytes())
    records = corpus["records"]
    assert isinstance(records, list)
    record_objects: list[dict[str, JsonValue]] = []
    for record in records:
        assert isinstance(record, dict)
        record_objects.append(record)

    # Gold/negative labels are deliberately absent from the retrieval index, so the Gold count comes
    # from the evaluation sidecar the index binds by hash.
    label_ref = corpus["evaluation_label_ref"]
    assert isinstance(label_ref, dict)
    label_path = label_ref["path"]
    assert isinstance(label_path, str)
    label_bytes = (EVALS_ROOT / label_path).read_bytes()
    assert sha256_hex(label_bytes) == label_ref["sha256"]
    labels = parse_json_object_bytes(label_bytes)["labels"]
    assert isinstance(labels, list)
    gold_count = sum(1 for item in labels if isinstance(item, dict) and item.get("record_kind") == "GOLD")

    topic_ids = {slice_id for case in cases for slice_id in case["slice_ids"] if slice_id.startswith("TOPIC_")}
    expression_ids = {
        slice_id for case in cases for slice_id in case["slice_ids"] if slice_id.startswith("EXPRESSION_")
    }
    transform_origins = {case["leakage_group_ids"]["transform_origin"] for case in cases}
    partition_counts = loaded_manifest["partition_counts"]
    assert status.created_counts.model_dump(mode="json") == {
        "dev_questions": partition_counts["DEV"],
        "holdout_questions": partition_counts["HOLDOUT"],
        "gold_records": gold_count,
        "corpus_records": len(record_objects),
        "topics": len(topic_ids),
        "expression_types": len(expression_ids),
        "independent_groups": len(transform_origins),
    }


def test_phase_b3_status_accepts_only_the_policy_foundation_without_effective_enforcement() -> None:
    status = parse_status_bytes(_status_bytes(_status_payload()))

    assert status.phase == "PHASE_B3_PROTECTED_RUNNER_FOUNDATION"
    assert status.dataset_status == "DRAFT"
    assert status.dataset_manifest_sha256 == DATASET_MANIFEST_HASH
    assert status.created_counts.model_dump() == {
        "dev_questions": 60,
        "holdout_questions": 0,
        "gold_records": 20,
        "corpus_records": 100,
        "topics": 5,
        "expression_types": 6,
        "independent_groups": 20,
    }
    assert status.schema_set_ref.hash == SCHEMA_SET_HASH
    assert status.gold_review_status == "APPROVED"
    assert status.gold_review_evidence_ref.hash == GOLD_REVIEW_EVIDENCE_HASH
    assert status.dataset_approval_evidence_ref.hash == DATASET_APPROVAL_EVIDENCE_HASH
    assert status.responsible_reviewer == "@hazelnutflavoured"
    assert status.approval_transition == "DEV_DATASET_CUSTODIAN_APPROVAL_RECORDED"
    assert status.holdout_preparation_ref.raw_sha256 == HOLDOUT_PREPARATION_HASH
    assert status.holdout_preparation_ref.self_sha256 == HOLDOUT_PREPARATION_SELF_HASH
    assert status.holdout_preparation_status == "PREPARATION_READY"
    assert status.protected_runner_foundation_ref.raw_sha256 == PROTECTED_RUNNER_FOUNDATION_HASH
    assert status.protected_runner_foundation_ref.self_sha256 == PROTECTED_RUNNER_FOUNDATION_SELF_HASH
    assert status.protected_runner_issue_status == "CREATED"
    assert status.policy_foundation_status == "IMPLEMENTED"
    assert status.effective_enforcement_status == "NOT_IMPLEMENTED"
    assert status.infrastructure_adapter_status == "NOT_IMPLEMENTED"
    assert status.reconciliation_adapter_status == "NOT_IMPLEMENTED"
    assert status.holdout_freeze_status == "NOT_STARTED"
    assert status.adapter_status == "NOT_IMPLEMENTED"
    assert status.actual_run_ref is None
    assert status.release_eligible is False


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda payload: payload.update({"unknown": True}), EvaluationErrorCode.SCHEMA_INVALID),
        (
            lambda payload: payload["blocking_codes"].reverse(),
            EvaluationErrorCode.SCHEMA_INVALID,
        ),
        (
            lambda payload: payload["blocking_codes"].append(payload["blocking_codes"][0]),
            EvaluationErrorCode.SCHEMA_INVALID,
        ),
        (lambda payload: payload["checks"].reverse(), EvaluationErrorCode.SCHEMA_INVALID),
        (lambda payload: payload["checks"].append(deepcopy(payload["checks"][0])), EvaluationErrorCode.SCHEMA_INVALID),
        (
            lambda payload: payload.update(
                {
                    "actual_run_ref": {
                        "run_id": "123e4567-e89b-42d3-a456-426614174000",
                        "semantic_hash": "1" * 64,
                        "result_content_manifest_sha256": "2" * 64,
                    }
                }
            ),
            EvaluationErrorCode.SCHEMA_INVALID,
        ),
        (lambda payload: payload.update({"metric_summary": []}), EvaluationErrorCode.SCHEMA_INVALID),
    ],
)
def test_phase_a_status_rejects_invalid_state(
    mutation: Any,
    expected_code: EvaluationErrorCode,
) -> None:
    payload = _status_payload()
    mutation(payload)
    payload["status_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"status_sha256"}))

    with pytest.raises(EvaluationValidationError) as raised:
        parse_status_bytes(_status_bytes(payload))

    assert raised.value.code is expected_code


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["checks"][0].update({"command": "python arbitrary.py"}),
        lambda payload: payload["checks"][0].update({"result": "arbitrary result"}),
        lambda payload: payload["checks"][0].update({"exit_code": 1}),
        lambda payload: payload["checks"].pop(),
        lambda payload: payload["checks"].append(
            {
                "check_id": "PHASE_A_UNDECLARED",
                "command": "uv run pytest undeclared.py -q",
                "exit_code": 0,
                "result": "1 passed",
            }
        ),
        lambda payload: payload["checks"].reverse(),
    ],
)
def test_phase_a_status_rejects_rehashed_check_catalog_mutation(mutation: Any) -> None:
    payload = _status_payload()
    mutation(payload)
    payload["status_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"status_sha256"}))

    with pytest.raises(EvaluationValidationError) as raised:
        parse_status_bytes(_status_bytes(payload))

    assert raised.value.code is EvaluationErrorCode.SCHEMA_INVALID


@pytest.mark.parametrize(
    ("sentinel", "expected_code"),
    [
        ("patient@example.com", EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN),
        ("010-1234-5678", EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN),
        ("Bearer abc.def.ghi", EvaluationErrorCode.PRIVACY_VALUE_FORBIDDEN),
        ("provider payload body", EvaluationErrorCode.SCHEMA_INVALID),
        ("/srv/protected/holdout/questions.json", EvaluationErrorCode.SCHEMA_INVALID),
        ("HOLDOUT raw content", EvaluationErrorCode.SCHEMA_INVALID),
        ("raw query text", EvaluationErrorCode.SCHEMA_INVALID),
    ],
)
def test_status_parser_rejects_rehashed_sensitive_check_values_without_echo(
    sentinel: str,
    expected_code: EvaluationErrorCode,
) -> None:
    payload = _status_payload()
    payload["checks"][0]["result"] = sentinel
    payload["status_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"status_sha256"}))

    with pytest.raises(EvaluationValidationError) as raised:
        parse_status_bytes(_status_bytes(payload))

    assert raised.value.code is expected_code
    assert sentinel not in str(raised.value)


def test_status_parser_rejects_duplicate_json_keys() -> None:
    with pytest.raises(EvaluationValidationError) as raised:
        parse_status_bytes(b'{"schema_version":"1.0.0","schema_version":"1.0.0"}')

    assert raised.value.code is EvaluationErrorCode.SCHEMA_INVALID


def test_status_parser_rejects_invalid_self_hash() -> None:
    payload = _status_payload()
    payload["status_sha256"] = "f" * 64

    with pytest.raises(EvaluationValidationError) as raised:
        parse_status_bytes(_status_bytes(payload))

    assert raised.value.code is EvaluationErrorCode.HASH_MISMATCH


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "rag-eval.schema-set-alternate"),
        ("version", "1.3.1"),
        ("hash", "a" * 64),
    ],
)
def test_status_parser_rejects_rehashed_non_candidate_schema_set(field: str, value: str) -> None:
    payload = _status_payload()
    payload["schema_set_ref"][field] = value
    payload["status_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"status_sha256"}))

    with pytest.raises(EvaluationValidationError) as raised:
        parse_status_bytes(_status_bytes(payload))

    assert raised.value.code is EvaluationErrorCode.SCHEMA_INVALID


def test_status_parser_rejects_rehashed_non_candidate_decision() -> None:
    payload = _status_payload()
    payload["schema_set_decision"] = "docs/governance/decisions/alternate.md"
    payload["status_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"status_sha256"}))

    with pytest.raises(EvaluationValidationError) as raised:
        parse_status_bytes(_status_bytes(payload))

    assert raised.value.code is EvaluationErrorCode.SCHEMA_INVALID


@pytest.mark.parametrize(
    ("forbidden_key", "expected_code"),
    [
        ("query", EvaluationErrorCode.SCHEMA_INVALID),
        ("nested_evidence_body_copy", EvaluationErrorCode.SCHEMA_INVALID),
        ("provider_payload", EvaluationErrorCode.PRIVACY_FIELD_FORBIDDEN),
        ("credential_ref", EvaluationErrorCode.SCHEMA_INVALID),
        ("protected_path_hint", EvaluationErrorCode.SCHEMA_INVALID),
        ("holdout_content_note", EvaluationErrorCode.SCHEMA_INVALID),
        ("fingerprint_value_copy", EvaluationErrorCode.SCHEMA_INVALID),
        ("hmac_value_copy", EvaluationErrorCode.SCHEMA_INVALID),
    ],
)
def test_status_parser_rejects_forbidden_key_fragments_recursively(
    forbidden_key: str,
    expected_code: EvaluationErrorCode,
) -> None:
    payload = _status_payload()
    payload["checks"][0]["details"] = {"nested": {forbidden_key: "redacted"}}
    payload["status_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"status_sha256"}))

    with pytest.raises(EvaluationValidationError) as raised:
        parse_status_bytes(_status_bytes(payload))

    assert raised.value.code is expected_code


@pytest.mark.parametrize(
    "forbidden_fragment",
    [
        "query",
        "evidence_body",
        "provider",
        "credential",
        "protected_path",
        "holdout_content",
        "fingerprint_value",
        "hmac_value",
    ],
)
def test_forbidden_key_guard_rejects_each_fragment_inside_nested_dict_and_list(forbidden_fragment: str) -> None:
    payload: dict[str, Any] = {"level_one": [{"level_two": {f"copied_{forbidden_fragment}_field": "redacted"}}]}

    with pytest.raises(EvaluationValidationError) as raised:
        _reject_forbidden_keys(payload)

    assert raised.value.code is EvaluationErrorCode.SCHEMA_INVALID


def test_unverified_metric_guard_rejects_nested_metric_key_when_actual_run_is_null() -> None:
    payload: dict[str, Any] = {"actual_run_ref": None, "level_one": [{"level_two": {"metric_summary": []}}]}

    with pytest.raises(EvaluationValidationError) as raised:
        _reject_unverified_metric_fields(payload)

    assert raised.value.code is EvaluationErrorCode.SCHEMA_INVALID


def test_report_decision_display_and_href_are_derived_from_the_validated_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_status = _status_bytes(_status_payload())
    status = parse_status_bytes(raw_status)
    observed_paths: list[str] = []

    def decision_href(decision_path: str) -> str:
        observed_paths.append(decision_path)
        return "../../../verified-decision.md"

    monkeypatch.setattr(validation_module, "_decision_href", decision_href)

    report = render_report(raw_status).decode("utf-8")

    assert observed_paths == [status.schema_set_decision]
    assert f"[`{status.schema_set_decision}`](../../../verified-decision.md)" in report


def test_markdown_table_cell_escapes_pipe_backslash_and_line_break_injection() -> None:
    injected = "safe\\value|cell\n\n## Injected heading\rafter"

    escaped = validation_module._markdown_table_cell(injected)

    assert escaped == "safe\\\\value\\|cell  ## Injected heading after"
    assert "\n" not in escaped
    assert "\r" not in escaped
    assert "|" not in escaped.replace("\\|", "")


def test_report_revalidates_model_copy_before_rendering_check_cells() -> None:
    payload = _status_payload()
    injected = "passed | forged\n\n## Follow-up heading"
    payload["checks"][0]["result"] = injected
    payload["status_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"status_sha256"}))

    with pytest.raises(EvaluationValidationError) as raised:
        render_report(_status_bytes(payload))

    assert raised.value.code is EvaluationErrorCode.SCHEMA_INVALID
    assert injected not in str(raised.value)


def test_report_rejects_untrusted_objects_without_serialization_warning_or_sentinel_leak() -> None:
    raw_status = _status_bytes(_status_payload())
    status = parse_status_bytes(raw_status)
    sentinel = "patient@example.com | raw HOLDOUT query\n## injected"
    base = status.model_dump(mode="json")
    raw_nested_model = Issue273ValidationStatus.model_construct(**{**base, "checks": [{"result": sentinel}]})
    nested_check = ValidationCheck.model_construct(
        check_id="TASK_1_PROVENANCE_CONTRACTS",
        command="unsafe",
        exit_code=0,
        result=sentinel,
    )
    nested_construct_model = Issue273ValidationStatus.model_construct(
        **{**base, "checks": (nested_check, *status.checks[1:])}
    )

    for probe in (raw_nested_model, nested_construct_model, {"nested": [sentinel]}, [sentinel]):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with pytest.raises(EvaluationValidationError) as raised:
                render_report(probe)  # type: ignore[arg-type]

        assert caught == []
        assert raised.value.code is EvaluationErrorCode.SCHEMA_INVALID
        assert sentinel not in str(raised.value)


def test_evals_readme_quotes_the_current_dataset_manifest_hash() -> None:
    """`evals/README.md` tells consumers how to verify this Dataset's integrity.

    A regeneration changes the manifest self-hash, so a stale README value makes a correct
    artifact look tampered with. Bind the documented value to the committed manifest.
    """
    manifest_payload: dict[str, Any] = parse_json_object_bytes(DATASET_MANIFEST_PATH.read_bytes())
    declared_manifest_hash = manifest_payload["manifest_sha256"]
    assert isinstance(declared_manifest_hash, str)
    readme = EVALS_README_PATH.read_text(encoding="utf-8")
    section = readme.split("### Issue #273 자연어 Retrieval DEV authoring", maxsplit=1)
    assert len(section) == 2, "evals/README.md must keep the Issue #273 section heading"
    issue_273_section = section[1].split("\n### ", maxsplit=1)[0]

    quoted_hashes = set(re.findall(r"\b[0-9a-f]{64}\b", issue_273_section))
    assert quoted_hashes == {declared_manifest_hash}, quoted_hashes


def test_evals_readme_describes_the_b3_foundation_without_claiming_enforcement() -> None:
    readme = EVALS_README_PATH.read_text(encoding="utf-8")
    issue_273_section = readme.split("### Issue #273 자연어 Retrieval DEV authoring", maxsplit=1)[1].split(
        "\n### ", maxsplit=1
    )[0]

    assert "아직 사람의 Gold 검토를 받지 않았" not in issue_273_section
    assert "모든 review provenance는 `DRAFT` 또는 `NOT_STARTED`" not in issue_273_section
    assert "HOLDOUT Freeze 준비는 `PREPARATION_READY`" in issue_273_section
    assert "접근 승인이나 Freeze 완료를 뜻하지 않는다" in issue_273_section
    assert "Issue #368은 `CREATED`" in issue_273_section
    assert "policy foundation은 `IMPLEMENTED`" in issue_273_section
    assert "독립 승인 reconciliation adapter와 실제 인프라 enforcement·adapter는" in issue_273_section
    assert "`NOT_IMPLEMENTED`" in issue_273_section
    assert "해당 Issue 상태는 `NOT_CREATED`" not in issue_273_section


def test_committed_status_is_canonical_and_report_is_exact_projection() -> None:
    raw_status = STATUS_PATH.read_bytes()
    status = parse_status_bytes(raw_status)

    _assert_status_matches_committed_dataset(status)
    stale_status = status.model_copy(update={"dataset_manifest_sha256": "0" * 64})
    with pytest.raises(AssertionError):
        _assert_status_matches_committed_dataset(stale_status)

    assert raw_status == canonical_json_bytes(parse_json_object_bytes(raw_status)) + b"\n"
    assert render_report(raw_status) == REPORT_PATH.read_bytes()
    assert b"Phase B3 \xc2\xb7 Protected Runner Policy Foundation Implemented" in REPORT_PATH.read_bytes()
    assert "한국어 자연어 합성 DEV 질문" in REPORT_PATH.read_text()
    assert b"DEV Dataset approval is recorded as APPROVED" in REPORT_PATH.read_bytes()
    assert b"Dataset remains DRAFT and unfrozen" in REPORT_PATH.read_bytes()
    assert b"Actual retrieval was not run" in REPORT_PATH.read_bytes()
    assert b"No baseline Metric exists" in REPORT_PATH.read_bytes()
    assert b"DEV cannot produce a Release PASS" in REPORT_PATH.read_bytes()
    assert b"Production remains closed" in REPORT_PATH.read_bytes()
    assert b"Access authorization is not recorded" in REPORT_PATH.read_bytes()
    assert b"HOLDOUT authoring has not started" in REPORT_PATH.read_bytes()
    assert HOLDOUT_PREPARATION_HASH.encode() in raw_status
    assert HOLDOUT_PREPARATION_HASH.encode() in REPORT_PATH.read_bytes()
    assert HOLDOUT_PREPARATION_SELF_HASH.encode() in raw_status
    assert HOLDOUT_PREPARATION_SELF_HASH.encode() in REPORT_PATH.read_bytes()
    assert PROTECTED_RUNNER_FOUNDATION_HASH.encode() in raw_status
    assert PROTECTED_RUNNER_FOUNDATION_HASH.encode() in REPORT_PATH.read_bytes()
    assert PROTECTED_RUNNER_FOUNDATION_SELF_HASH.encode() in raw_status
    assert PROTECTED_RUNNER_FOUNDATION_SELF_HASH.encode() in REPORT_PATH.read_bytes()
    assert sha256_hex(PROTECTED_RUNNER_FOUNDATION_PATH.read_bytes()) == PROTECTED_RUNNER_FOUNDATION_HASH
    for result in (
        b"1 passed",
        b"26 passed",
        b"27 passed",
        b"59 passed",
        b"51 passed",
        b"132 passed",
        b"94 passed, 7 skipped",
    ):
        assert result in raw_status
        assert result in REPORT_PATH.read_bytes()
    assert DATASET_MANIFEST_HASH.encode() in raw_status
    assert DATASET_MANIFEST_HASH.encode() in REPORT_PATH.read_bytes()
