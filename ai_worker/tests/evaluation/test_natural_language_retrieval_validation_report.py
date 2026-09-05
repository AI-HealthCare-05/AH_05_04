from __future__ import annotations

import warnings
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from ai_worker.tasks.evaluation import natural_language_retrieval_validation as validation_module
from ai_worker.tasks.evaluation.canonical import canonical_json_bytes, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.loaders import parse_json_object_bytes
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
SCHEMA_SET_HASH = "611738652c2f7cb8b79b091669212a257474c4d3d0aa81a829a4f534bb6a3158"


def _status_payload() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0.0",
        "issue": "#273",
        "phase": "PHASE_0_SCHEMA_CANDIDATE",
        "status_label": "Candidate · Review Required",
        "schema_set_status": "REVIEW_REQUIRED",
        "dataset_ref": "rag-natural-language-retrieval-dev@1.0.0",
        "planned_counts": {
            "dev_questions": 60,
            "holdout_questions": 40,
            "topics": 5,
            "expression_types": 6,
            "independent_groups": 20,
        },
        "created_counts": {"dev_questions": 0, "holdout_questions": 0, "gold_records": 0},
        "schema_set_ref": {
            "id": "rag-eval.schema-set",
            "version": "1.3.0",
            "hash": SCHEMA_SET_HASH,
        },
        "schema_set_decision": "docs/governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md",
        "responsible_reviewer": "@hazelnutflavoured",
        "approval_transition": "FUTURE_PULL_REQUEST_REVIEW_EVENT",
        "dataset_status": "NOT_CREATED",
        "gold_review_status": "NOT_STARTED",
        "holdout_freeze_status": "NOT_STARTED",
        "adapter_status": "NOT_IMPLEMENTED",
        "actual_run_ref": None,
        "release_eligible": False,
        "blocking_codes": [
            "BLOCKED_BY_EVAL_SCHEMA_EXTENSION",
            "BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER",
            "BLOCKED_BY_RAG_14_ADAPTER",
            "WAITING_FOR_HOLDOUT_FREEZE",
        ],
        "checks": [
            {
                "check_id": "TASK_1_PROVENANCE_CONTRACTS",
                "command": "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_provenance_v1_schemas.py -q",
                "exit_code": 0,
                "result": "57 passed",
            },
            {
                "check_id": "TASK_2_SCHEMA_SET_EXPORT",
                "command": "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run --with jsonschema pytest ai_worker/tests/evaluation/test_schema_exports.py::test_schema_set_1_3_review_provenance_v12_state_matrix_is_portable ai_worker/tests/evaluation/test_schema_exports.py::test_schema_set_1_3_positive_integers_match_the_canonical_safe_integer_boundary -q",
                "exit_code": 0,
                "result": "5 passed",
            },
            {
                "check_id": "TASK_3_LOADER_BINDING",
                "command": "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest ai_worker/tests/evaluation/test_authoring_identity_loader.py ai_worker/tests/evaluation/test_loaders.py ai_worker/tests/evaluation/test_schema_exports.py -q",
                "exit_code": 0,
                "result": "151 passed, 5 skipped",
            },
        ],
        "updated_at": "2026-09-05T15:28:41.000000Z",
        "status_sha256": "0" * 64,
    }
    payload["status_sha256"] = canonical_sha256(payload, excluded_top_level_keys=frozenset({"status_sha256"}))
    return payload


def _status_bytes(payload: dict[str, Any]) -> bytes:
    return canonical_json_bytes(payload)


def test_phase_0_status_accepts_only_the_candidate_state() -> None:
    status = parse_status_bytes(_status_bytes(_status_payload()))

    assert status.phase == "PHASE_0_SCHEMA_CANDIDATE"
    assert status.schema_set_ref.hash == SCHEMA_SET_HASH
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
def test_phase_0_status_rejects_invalid_state(
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
                "check_id": "TASK_4_UNDECLARED",
                "command": "uv run pytest undeclared.py -q",
                "exit_code": 0,
                "result": "1 passed",
            }
        ),
        lambda payload: payload["checks"].reverse(),
    ],
)
def test_phase_0_status_rejects_rehashed_check_catalog_mutation(mutation: Any) -> None:
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


def test_committed_status_is_canonical_and_report_is_exact_projection() -> None:
    raw_status = STATUS_PATH.read_bytes()
    parse_status_bytes(raw_status)

    assert raw_status == canonical_json_bytes(parse_json_object_bytes(raw_status)) + b"\n"
    assert render_report(raw_status) == REPORT_PATH.read_bytes()
    assert b"Candidate \xc2\xb7 Review Required" in REPORT_PATH.read_bytes()
    assert b"Production remains closed" in REPORT_PATH.read_bytes()
    assert b"57 passed" in raw_status and b"151 passed, 5 skipped" in raw_status
    assert b"57 passed" in REPORT_PATH.read_bytes() and b"151 passed, 5 skipped" in REPORT_PATH.read_bytes()
    assert b"47 passed" not in raw_status and b"154 passed" not in raw_status
    assert b"47 passed" not in REPORT_PATH.read_bytes() and b"154 passed" not in REPORT_PATH.read_bytes()
    assert b"5 passed" in raw_status and b"5 passed" in REPORT_PATH.read_bytes()
    assert b"2026-09-05T15:28:41.000000Z" in raw_status
    assert b"2026-09-05T15:28:41.000000Z" in REPORT_PATH.read_bytes()
    assert b"2026-09-05T13:06:16.000000Z" not in raw_status
    assert b"2026-09-05T13:06:16.000000Z" not in REPORT_PATH.read_bytes()
    assert b"7f5921c9bc34b071407cbaae318f975c264d740cc8d2fdff77bab007622bd886" not in raw_status
    assert b"7f5921c9bc34b071407cbaae318f975c264d740cc8d2fdff77bab007622bd886" not in REPORT_PATH.read_bytes()
