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
_PHASE_B2_BLOCKERS = (
    "BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER",
    "BLOCKED_BY_RAG_14_ADAPTER",
    "WAITING_FOR_HOLDOUT_ACCESS_AUTHORIZATION",
    "WAITING_FOR_HOLDOUT_FREEZE",
)
_DECISION_DOCS_PREFIX = "docs/"
_VALIDATION_CHECK_CATALOG = {
    "PHASE_A_DEV_FIXTURE": (
        "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest "
        "ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py -q",
        "26 passed",
    ),
    "PHASE_A_LOADER": (
        "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest "
        "ai_worker/tests/evaluation/test_authoring_identity_loader.py "
        "ai_worker/tests/evaluation/test_loaders.py -q",
        "132 passed",
    ),
    "PHASE_A_REPORT_PROJECTION": (
        "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest "
        "ai_worker/tests/evaluation/test_natural_language_retrieval_validation_report.py -q",
        "51 passed",
    ),
    "PHASE_A_SCHEMA_EXPORT": (
        "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest "
        "ai_worker/tests/evaluation/test_schema_exports.py "
        "ai_worker/tests/evaluation/test_external_schema_parity.py "
        "ai_worker/tests/evaluation/test_provenance_v1_schemas.py -q",
        "94 passed, 7 skipped",
    ),
    "PHASE_B3_PROTECTED_RUNNER_FOUNDATION": (
        "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest "
        "ai_worker/tests/evaluation/test_natural_language_retrieval_protected_runner_foundation.py "
        "ai_worker/tests/evaluation/test_protected_retrieval.py -q",
        "58 passed",
    ),
    "PHASE_B_DATASET_APPROVAL_PROVENANCE": (
        "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest "
        "ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py::"
        "test_issue_273_graph_records_the_actual_dataset_custodian_approval_event -q",
        "1 passed",
    ),
    "PHASE_B_GOLD_REVIEW_PROVENANCE": (
        "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest "
        "ai_worker/tests/evaluation/test_natural_language_retrieval_dev_fixture.py::"
        "test_issue_273_graph_records_only_the_actual_gold_review_event -q",
        "1 passed",
    ),
    "PHASE_B_HOLDOUT_FREEZE_PREPARATION": (
        "UV_CACHE_DIR=/private/tmp/ah_issue273_uv_cache uv run pytest "
        "ai_worker/tests/evaluation/test_natural_language_retrieval_holdout_preparation.py -q",
        "27 passed",
    ),
}
_VALIDATION_CHECK_IDS = tuple(_VALIDATION_CHECK_CATALOG)


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
    dev_questions: Literal[60]
    holdout_questions: Literal[0]
    gold_records: Literal[20]
    corpus_records: Literal[100]
    topics: Literal[5]
    expression_types: Literal[6]
    independent_groups: Literal[20]


class ValidationCheck(StrictContractModel):
    check_id: Literal[
        "PHASE_A_DEV_FIXTURE",
        "PHASE_A_LOADER",
        "PHASE_A_REPORT_PROJECTION",
        "PHASE_A_SCHEMA_EXPORT",
        "PHASE_B3_PROTECTED_RUNNER_FOUNDATION",
        "PHASE_B_DATASET_APPROVAL_PROVENANCE",
        "PHASE_B_GOLD_REVIEW_PROVENANCE",
        "PHASE_B_HOLDOUT_FREEZE_PREPARATION",
    ]
    command: NonEmptyString
    exit_code: Literal[0]
    result: NonEmptyString

    @model_validator(mode="after")
    def validate_catalog_entry(self) -> ValidationCheck:
        if (self.command, self.result) != _VALIDATION_CHECK_CATALOG[self.check_id]:
            raise ValueError("validation check must match the Issue 273 evidence catalog")
        return self


class CandidateSchemaSetRef(StrictContractModel):
    id: Literal["rag-eval.schema-set"]
    version: Literal["1.3.0"]
    hash: Literal["ca1f324c701dd5e86d811a4430ddbf2d394bd3aa0e7eb0e32dabcb8b63d1e325"]


class GoldReviewEvidenceRef(StrictContractModel):
    id: Literal["github-pr-341-review-5137833200"]
    version: Literal["1.0.0"]
    hash: Literal["6dd83d9c258499fb0d543870e5a99a913abb0b2dcb3c11e4b72855e43c235776"]


class DatasetApprovalEvidenceRef(StrictContractModel):
    id: Literal["github-pr-354-review-5139907268"]
    version: Literal["1.0.0"]
    hash: Literal["3b1a90ba0f9a6c06162ce953bdb7e0d504f76074d415a807611812d16ac29896"]


class HoldoutPreparationRef(StrictContractModel):
    id: Literal["issue-273-holdout-freeze-preparation"]
    version: Literal["1.0.0"]
    raw_sha256: Literal["40ea344c378298d99c14c372c27296322854d8e9b055fa179592568ca88bc192"]
    self_sha256: Literal["b4a0a113d9efce867a434875f18ee259431d226a9cf1e4dcaed28152920600b6"]


class ProtectedRunnerFoundationRef(StrictContractModel):
    id: Literal["issue-273-protected-runner-foundation"]
    version: Literal["1.0.0"]
    raw_sha256: Literal["72306db8414f81cd3f724177885d24c69d87ea56745b4094f4eb29dfb9920de0"]
    self_sha256: Literal["f2bb8e8ccb5e9d85470c0ebc69574196865c91ef3504162859660c530990dbe0"]


class Issue273ValidationStatus(StrictContractModel):
    schema_version: Literal["1.2.1"]
    issue: Literal["#273"]
    phase: Literal["PHASE_B3_PROTECTED_RUNNER_FOUNDATION"]
    status_label: Literal["Phase B3 · Protected Runner Policy Foundation Implemented"]
    schema_set_status: Literal["REVIEW_REQUIRED"]
    dataset_ref: Literal["rag-natural-language-retrieval-dev@1.0.0"]
    planned_counts: PlannedCounts
    created_counts: CreatedCounts
    dataset_manifest_sha256: Literal["b8c7a1a2b529b73ce1a275e9b0210794de3dcbab72d1b50dec4def15166aada2"]
    schema_set_ref: CandidateSchemaSetRef
    schema_set_decision: Literal["docs/governance/decisions/2026-09-05-rag-evaluation-schema-set-1-3-candidate.md"]
    responsible_reviewer: Literal["@hazelnutflavoured"]
    approval_transition: Literal["DEV_DATASET_CUSTODIAN_APPROVAL_RECORDED"]
    dataset_status: Literal["DRAFT"]
    gold_review_status: Literal["APPROVED"]
    gold_review_evidence_ref: GoldReviewEvidenceRef
    dataset_approval_evidence_ref: DatasetApprovalEvidenceRef
    holdout_preparation_ref: HoldoutPreparationRef
    holdout_preparation_status: Literal["PREPARATION_READY"]
    protected_runner_foundation_ref: ProtectedRunnerFoundationRef
    protected_runner_issue_status: Literal["CREATED"]
    policy_foundation_status: Literal["IMPLEMENTED"]
    effective_enforcement_status: Literal["NOT_IMPLEMENTED"]
    infrastructure_adapter_status: Literal["NOT_IMPLEMENTED"]
    reconciliation_adapter_status: Literal["NOT_IMPLEMENTED"]
    holdout_freeze_status: Literal["NOT_STARTED"]
    adapter_status: Literal["NOT_IMPLEMENTED"]
    actual_run_ref: None
    release_eligible: Literal[False]
    blocking_codes: Annotated[
        tuple[
            Literal[
                "BLOCKED_BY_PROTECTED_RETRIEVAL_RUNNER",
                "BLOCKED_BY_RAG_14_ADAPTER",
                "WAITING_FOR_HOLDOUT_ACCESS_AUTHORIZATION",
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
        if self.blocking_codes != _PHASE_B2_BLOCKERS:
            raise ValueError("Issue 273 blockers must be the exact UTF-16-sorted set")
        check_ids = [check.check_id for check in self.checks]
        commands = [check.command for check in self.checks]
        if tuple(check_ids) != _VALIDATION_CHECK_IDS:
            raise ValueError("validation checks must contain the exact Issue 273 evidence catalog")
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
        "# Issue #273 Phase B3 Protected Runner Policy Foundation Validation Report",
        "",
        "> Phase B3 · Protected Runner Policy Foundation Implemented — executable policy tests exist, but effective",
        "> infrastructure enforcement, authorization, Freeze, actual run, and Release remain incomplete.",
        "",
        f"- Phase: `{status.phase}`",
        f"- Schema Set Status: `{status.schema_set_status}`",
        f"- Dataset: `{status.dataset_ref}` (`{status.dataset_status}`)",
        f"- Dataset Manifest SHA-256: `{status.dataset_manifest_sha256}`",
        f"- Schema Set: `{schema_set.id}@{schema_set.version}` `{schema_set.hash}`",
        (f"- Candidate Decision: [`{status.schema_set_decision}`]({decision_href})"),
        (
            f"- Gold Review Evidence: `{status.gold_review_evidence_ref.id}@"
            f"{status.gold_review_evidence_ref.version}` `{status.gold_review_evidence_ref.hash}`"
        ),
        (
            f"- Dataset Approval Evidence: `{status.dataset_approval_evidence_ref.id}@"
            f"{status.dataset_approval_evidence_ref.version}` `{status.dataset_approval_evidence_ref.hash}`"
        ),
        (f"- HOLDOUT Preparation: `{status.holdout_preparation_ref.id}@{status.holdout_preparation_ref.version}`"),
        f"- Preparation raw SHA-256: `{status.holdout_preparation_ref.raw_sha256}`",
        f"- Preparation self SHA-256: `{status.holdout_preparation_ref.self_sha256}`",
        (
            f"- Protected Runner Foundation: `{status.protected_runner_foundation_ref.id}@"
            f"{status.protected_runner_foundation_ref.version}`"
        ),
        f"- Foundation raw SHA-256: `{status.protected_runner_foundation_ref.raw_sha256}`",
        f"- Foundation self SHA-256: `{status.protected_runner_foundation_ref.self_sha256}`",
        f"- Phase B3 Product·Privacy·Safety·Evaluation Reviewer: `{status.responsible_reviewer}`",
        "- Phase B3 Dataset Custodian·Backend·Security Reviewer: `@phina-io`",
        f"- Prior DEV Approval Transition: `{status.approval_transition}`; the verified actor was `@phina-io` "
        "(`DATASET_CUSTODIAN`). This is not HOLDOUT access authorization.",
        "- Release Eligible: `false`",
        "- Production remains closed.",
        "",
        "## Authored DEV Scope",
        "",
        f"- Planned DEV questions: `{status.planned_counts.dev_questions}`; created: `{status.created_counts.dev_questions}`",
        (
            f"- Planned HOLDOUT questions: `{status.planned_counts.holdout_questions}`; "
            f"created: `{status.created_counts.holdout_questions}`"
        ),
        f"- Topics: planned `{status.planned_counts.topics}`; created `{status.created_counts.topics}`",
        (
            f"- Expression types: planned `{status.planned_counts.expression_types}`; "
            f"created `{status.created_counts.expression_types}`"
        ),
        (
            f"- Independent transform-origin groups: planned `{status.planned_counts.independent_groups}`; "
            f"created `{status.created_counts.independent_groups}`"
        ),
        f"- Gold records created: `{status.created_counts.gold_records}`; review: `{status.gold_review_status}`",
        f"- Study-wide synthetic corpus records created: `{status.created_counts.corpus_records}`",
        f"- HOLDOUT Preparation: `{status.holdout_preparation_status}`",
        f"- Protected Runner Issue: `{status.protected_runner_issue_status}`",
        f"- Policy Foundation: `{status.policy_foundation_status}`",
        f"- Effective Enforcement: `{status.effective_enforcement_status}`",
        f"- Infrastructure Adapter: `{status.infrastructure_adapter_status}`",
        f"- Reconciliation Adapter: `{status.reconciliation_adapter_status}`",
        f"- HOLDOUT Freeze: `{status.holdout_freeze_status}`",
        f"- Actual Adapter: `{status.adapter_status}`",
        "- Actual Run Artifact: `NOT_CREATED`",
        "",
        (
            "한국어 자연어 합성 DEV 질문 60개와 합성 Gold/corpus authoring graph가 저장소에 존재하며, "
            "실제 환자 발화나 실제 제품 데이터가 아니다."
        ),
        "DEV Dataset approval is recorded as APPROVED for the 60 Cases, Evidence Mapping, and Dataset Manifest.",
        "Dataset remains DRAFT and unfrozen; preparation does not create or Freeze HOLDOUT content.",
        "The protected Runner policy foundation is implemented and verified with synthetic adapters only.",
        "Access authorization is not recorded, and HOLDOUT authoring has not started.",
        "Actual retrieval was not run because the actual Adapter is NOT_IMPLEMENTED.",
        "No baseline Metric exists, and no Metric fields are recorded in the machine status.",
        "DEV cannot produce a Release PASS; Production remains closed.",
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
        "- No Dataset Freeze, HOLDOUT Freeze, actual baseline completion, Release PASS, or Production readiness is claimed.",
        "- HOLDOUT question content is absent from the repository and remains future protected work.",
        "- Actual protected infrastructure, access authorization, actual Adapter, and HOLDOUT Freeze remain blockers.",
        "- `@phina-io` must approve the database/schema, roles, protected credential environment, audit retention,",
        "  backup, revoke, and incident-response design before an infrastructure adapter is implemented.",
        "- HOLDOUT authoring may start only after an independent Dataset Custodian authorization event is recorded.",
        "- The #158 replay uses a different Dataset and is `NOT_COMPARABLE_DIFFERENT_DATASET`.",
        "",
        f"Status updated at `{status.updated_at}`. Canonical status SHA-256: `{status.status_sha256}`.",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


__all__ = ["Issue273ValidationStatus", "parse_status_bytes", "render_report"]
