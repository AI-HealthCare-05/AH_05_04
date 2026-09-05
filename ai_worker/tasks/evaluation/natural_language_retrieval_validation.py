from __future__ import annotations

from typing import Annotated, Literal, cast

from pydantic import BeforeValidator, Field, ValidationError, model_validator

from ai_worker.tasks.evaluation.canonical import JsonValue, canonical_sha256
from ai_worker.tasks.evaluation.errors import EvaluationErrorCode, EvaluationValidationError
from ai_worker.tasks.evaluation.privacy import validate_privacy_boundary
from ai_worker.tasks.evaluation.schemas.common import (
    NonEmptyString,
    Sha256Hex,
    StrictContractModel,
    UtcTimestamp,
)

_FORBIDDEN_KEY_FRAGMENTS = (
    "query",
    "evidence_body",
    "provider",
    "credential",
    "protected_path",
    "holdout_content",
    "fingerprint_value",
    "hmac_value",
)
_PHASE_0_BLOCKERS = (
    "BLOCKED_BY_EVAL_SCHEMA_EXTENSION",
    "BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER",
    "BLOCKED_BY_RAG_14_ADAPTER",
    "WAITING_FOR_HOLDOUT_FREEZE",
)
_DECISION_DOCS_PREFIX = "docs/"
_PHASE_0_CHECK_CATALOG = {
    "TASK_1_PROVENANCE_CONTRACTS": (
        "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest "
        "ai_worker/tests/evaluation/test_provenance_v1_schemas.py -q",
        "51 passed",
    ),
    "TASK_2_SCHEMA_SET_EXPORT": (
        "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run --with jsonschema pytest "
        "ai_worker/tests/evaluation/test_schema_exports.py::"
        "test_schema_set_1_3_review_provenance_v12_state_matrix_is_portable -q",
        "3 passed",
    ),
    "TASK_3_LOADER_BINDING": (
        "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest "
        "ai_worker/tests/evaluation/test_authoring_identity_loader.py "
        "ai_worker/tests/evaluation/test_loaders.py ai_worker/tests/evaluation/test_schema_exports.py -q",
        "151 passed, 3 skipped",
    ),
}
_PHASE_0_CHECK_IDS = tuple(_PHASE_0_CHECK_CATALOG)


def _tuple_from_wire(value: object) -> object:
    return tuple(value) if isinstance(value, list) else value


def _utf16_key(value: str) -> bytes:
    return value.encode("utf-16-be")


class PlannedCounts(StrictContractModel):
    dev_questions: Literal[60]
    holdout_questions: Literal[40]
    topics: Literal[5]
    expression_types: Literal[6]
    independent_groups: Literal[20]


class CreatedCounts(StrictContractModel):
    dev_questions: Literal[0]
    holdout_questions: Literal[0]
    gold_records: Literal[0]


class ValidationCheck(StrictContractModel):
    check_id: Literal[
        "TASK_1_PROVENANCE_CONTRACTS",
        "TASK_2_SCHEMA_SET_EXPORT",
        "TASK_3_LOADER_BINDING",
    ]
    command: NonEmptyString
    exit_code: Literal[0]
    result: NonEmptyString

    @model_validator(mode="after")
    def validate_catalog_entry(self) -> ValidationCheck:
        if (self.command, self.result) != _PHASE_0_CHECK_CATALOG[self.check_id]:
            raise ValueError("validation check must match the Phase 0 evidence catalog")
        return self


class CandidateSchemaSetRef(StrictContractModel):
    id: Literal["rag-eval.schema-set"]
    version: Literal["1.3.0"]
    hash: Literal["e9843e190fbfabc6305d709e04ea296aefd107e66739882471fa3aedee08092f"]


class Issue273ValidationStatus(StrictContractModel):
    schema_version: Literal["1.0.0"]
    issue: Literal["#273"]
    phase: Literal["PHASE_0_SCHEMA_CANDIDATE"]
    status_label: Literal["Candidate · Review Required"]
    schema_set_status: Literal["REVIEW_REQUIRED"]
    dataset_ref: Literal["rag-natural-language-retrieval-dev@1.0.0"]
    planned_counts: PlannedCounts
    created_counts: CreatedCounts
    schema_set_ref: CandidateSchemaSetRef
    schema_set_decision: Literal["docs/governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md"]
    responsible_reviewer: Literal["@hazelnutflavoured"]
    approval_transition: Literal["FUTURE_PULL_REQUEST_REVIEW_EVENT"]
    dataset_status: Literal["NOT_CREATED"]
    gold_review_status: Literal["NOT_STARTED"]
    holdout_freeze_status: Literal["NOT_STARTED"]
    adapter_status: Literal["NOT_IMPLEMENTED"]
    actual_run_ref: None
    release_eligible: Literal[False]
    blocking_codes: Annotated[
        tuple[
            Literal[
                "BLOCKED_BY_EVAL_SCHEMA_EXTENSION",
                "BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER",
                "BLOCKED_BY_RAG_14_ADAPTER",
                "WAITING_FOR_HOLDOUT_FREEZE",
            ],
            ...,
        ],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=4, max_length=4),
    ]
    checks: Annotated[
        tuple[ValidationCheck, ...],
        BeforeValidator(_tuple_from_wire),
        Field(min_length=1),
    ]
    updated_at: UtcTimestamp
    status_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_ordered_collections(self) -> Issue273ValidationStatus:
        if self.blocking_codes != _PHASE_0_BLOCKERS:
            raise ValueError("Phase 0 blockers must be the exact UTF-16-sorted set")
        check_ids = [check.check_id for check in self.checks]
        commands = [check.command for check in self.checks]
        if tuple(check_ids) != _PHASE_0_CHECK_IDS:
            raise ValueError("validation checks must contain the exact Phase 0 evidence catalog")
        if len(check_ids) != len(set(check_ids)) or len(commands) != len(set(commands)):
            raise ValueError("validation checks must be unique")
        if check_ids != sorted(check_ids, key=_utf16_key):
            raise ValueError("validation checks must be UTF-16 sorted by check ID")
        return self


def _reject_forbidden_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in _FORBIDDEN_KEY_FRAGMENTS):
                raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)
            _reject_forbidden_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden_keys(item)


def _reject_unverified_metric_fields(value: object) -> None:
    if not isinstance(value, dict) or value.get("actual_run_ref") is not None:
        return

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if "metric" in str(key).lower():
                    raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)


def _validate_status_payload(payload: dict[str, JsonValue]) -> Issue273ValidationStatus:
    validate_privacy_boundary(payload)
    _reject_forbidden_keys(payload)
    _reject_unverified_metric_fields(payload)
    try:
        status = Issue273ValidationStatus.model_validate(payload)
    except ValidationError:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None
    canonical_payload = cast(dict[str, JsonValue], status.model_dump(mode="json"))
    if canonical_sha256(
        canonical_payload,
        excluded_top_level_keys=frozenset({"status_sha256"}),
    ) != cast(str, canonical_payload["status_sha256"]):
        raise EvaluationValidationError(EvaluationErrorCode.HASH_MISMATCH)
    return status


def parse_status_bytes(raw_bytes: bytes) -> Issue273ValidationStatus:
    from ai_worker.tasks.evaluation.loaders import parse_json_object_bytes

    try:
        payload = parse_json_object_bytes(raw_bytes)
    except EvaluationValidationError:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID) from None
    return _validate_status_payload(payload)


def _decision_href(decision_path: str) -> str:
    if not decision_path.startswith(_DECISION_DOCS_PREFIX):
        raise ValueError("candidate Decision path must be under docs")
    return f"../../../{decision_path.removeprefix(_DECISION_DOCS_PREFIX)}"


def _markdown_table_cell(value: str | int) -> str:
    normalized = str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return normalized.replace("\\", "\\\\").replace("|", "\\|")


def render_report(raw_status: bytes) -> bytes:
    if type(raw_status) is not bytes:
        raise EvaluationValidationError(EvaluationErrorCode.SCHEMA_INVALID)
    status = parse_status_bytes(raw_status)
    schema_set = status.schema_set_ref
    decision_href = _decision_href(status.schema_set_decision)
    lines = [
        "# Issue #273 Phase 0 Validation Report",
        "",
        "> Candidate · Review Required — not approved, not frozen, and not a Release decision.",
        "",
        f"- Phase: `{status.phase}`",
        f"- Schema Set Status: `{status.schema_set_status}`",
        f"- Dataset: `{status.dataset_ref}` (`{status.dataset_status}`)",
        f"- Schema Set: `{schema_set.id}@{schema_set.version}` `{schema_set.hash}`",
        (f"- Candidate Decision: [`{status.schema_set_decision}`]({decision_href})"),
        (
            f"- Approval Transition: `{status.approval_transition}` by responsible reviewer "
            f"`{status.responsible_reviewer}`; this future PR event has not occurred."
        ),
        "- Release Eligible: `false`",
        "- Production remains closed.",
        "",
        "## Planned Scope and Current Artifacts",
        "",
        f"- Planned DEV questions: `{status.planned_counts.dev_questions}`; created: `{status.created_counts.dev_questions}`",
        (
            f"- Planned HOLDOUT questions: `{status.planned_counts.holdout_questions}`; "
            f"created: `{status.created_counts.holdout_questions}`"
        ),
        f"- Planned topics: `{status.planned_counts.topics}`",
        f"- Planned expression types: `{status.planned_counts.expression_types}`",
        f"- Planned independent groups: `{status.planned_counts.independent_groups}`",
        f"- Gold records created: `{status.created_counts.gold_records}`; review: `{status.gold_review_status}`",
        f"- HOLDOUT Freeze: `{status.holdout_freeze_status}`",
        f"- Actual Adapter: `{status.adapter_status}`",
        "- Actual Run Artifact: `NOT_CREATED`",
        "- Metric summary: `NOT_CREATED`",
        "",
        "No DEV or HOLDOUT question bodies, Gold artifacts, actual Run, or Metric values were created in Phase 0.",
        "",
        "## Blocking Codes",
        "",
        *(f"- `{code}`" for code in status.blocking_codes),
        "",
        "## Verification Evidence",
        "",
        "| Check | Command | Exit | Result |",
        "| --- | --- | ---: | --- |",
        *(
            f"| `{_markdown_table_cell(check.check_id)}` | `{_markdown_table_cell(check.command)}` | "
            f"`{_markdown_table_cell(check.exit_code)}` | {_markdown_table_cell(check.result)} |"
            for check in status.checks
        ),
        "",
        "## Boundaries",
        "",
        "- Issue [#278](https://github.com/AI-HealthCare-05/AH_05_04/issues/278) is separate and non-blocking for #273.",
        "- No approval, Contract Freeze, Dataset Freeze, HOLDOUT Freeze, actual baseline completion, or Production readiness is claimed.",
        "",
        f"Status updated at `{status.updated_at}`. Canonical status SHA-256: `{status.status_sha256}`.",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


__all__ = ["Issue273ValidationStatus", "parse_status_bytes", "render_report"]
