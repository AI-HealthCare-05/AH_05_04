from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from app.core.db.databases import Base
from app.core.db.types import UUIDChar


def _sql_in_list(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class EvaluationExperimentType(StrEnum):
    KNOWLEDGE_RETRIEVAL = "KNOWLEDGE_RETRIEVAL"
    ANSWER_GROUNDING_SAFETY = "ANSWER_GROUNDING_SAFETY"
    END_TO_END_RAG = "END_TO_END_RAG"


class EvaluationDatasetPartition(StrEnum):
    AUTHORING = "AUTHORING"
    DEV = "DEV"
    HOLDOUT = "HOLDOUT"
    SAFETY_REGRESSION = "SAFETY_REGRESSION"


class EvaluationDatasetStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    FROZEN = "FROZEN"
    RETIRED = "RETIRED"


class EvaluationVariantRole(StrEnum):
    BASELINE = "BASELINE"
    CANDIDATE = "CANDIDATE"
    DIAGNOSTIC = "DIAGNOSTIC"


class EvaluationExecutionStatus(StrEnum):
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    NOT_EVALUATED = "NOT_EVALUATED"
    INVALID = "INVALID"
    ERROR = "ERROR"
    COMPLETED = "COMPLETED"


class EvaluationDecisionStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_APPLICABLE = "N/A"


class EvaluationMetricScope(StrEnum):
    RUN = "RUN"
    CASE = "CASE"


class EvaluationFailureScope(StrEnum):
    RUN = "RUN"
    CASE = "CASE"


class EvalDataset(Base):
    __tablename__ = "eval_dataset"
    __table_args__ = (
        UniqueConstraint("dataset_key", "dataset_version", name="uq_eval_dataset_key_version"),
        UniqueConstraint("manifest_hash", name="uq_eval_dataset_manifest_hash"),
        CheckConstraint("length(trim(dataset_key)) > 0", name="chk_eval_dataset_key_nonblank"),
        CheckConstraint("length(trim(dataset_version)) > 0", name="chk_eval_dataset_version_nonblank"),
        CheckConstraint("length(trim(display_name)) > 0", name="chk_eval_dataset_display_name_nonblank"),
        CheckConstraint("length(schema_set_sha256) = 64", name="chk_eval_dataset_schema_sha256_length"),
        CheckConstraint("length(manifest_hash) = 64", name="chk_eval_dataset_manifest_hash_length"),
        CheckConstraint("case_count >= 0", name="chk_eval_dataset_case_count"),
        CheckConstraint(
            f"dataset_status IN ({_sql_in_list(EvaluationDatasetStatus)})",
            name="chk_eval_dataset_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    dataset_key: Mapped[str] = mapped_column(String(120), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dataset_status: Mapped[EvaluationDatasetStatus] = mapped_column(
        Enum(EvaluationDatasetStatus, native_enum=False, length=20),
        nullable=False,
        default=EvaluationDatasetStatus.DRAFT,
    )
    schema_set_id: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_set_version: Mapped[str] = mapped_column(String(80), nullable=False)
    schema_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_classification: Mapped[str] = mapped_column(String(80), nullable=False)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    cases: Mapped[list["EvalCase"]] = relationship(back_populates="dataset", foreign_keys="EvalCase.dataset_id")
    experiments: Mapped[list["EvalExperiment"]] = relationship(
        back_populates="dataset", foreign_keys="EvalExperiment.dataset_id"
    )


class EvalCase(Base):
    __tablename__ = "eval_case"
    __table_args__ = (
        Index("idx_eval_case_dataset_partition", "dataset_id", "partition"),
        CheckConstraint("length(trim(case_key)) > 0", name="chk_eval_case_key_nonblank"),
        CheckConstraint("length(trim(case_version)) > 0", name="chk_eval_case_version_nonblank"),
        CheckConstraint("length(input_hash) = 64", name="chk_eval_case_input_hash_length"),
        CheckConstraint("gold_hash IS NULL OR length(gold_hash) = 64", name="chk_eval_case_gold_hash_length"),
        CheckConstraint(
            f"partition IN ({_sql_in_list(EvaluationDatasetPartition)})",
            name="chk_eval_case_partition",
        ),
        CheckConstraint(
            f"experiment_type IN ({_sql_in_list(EvaluationExperimentType)})",
            name="chk_eval_case_experiment_type",
        ),
        CheckConstraint(
            "experiment_type != 'END_TO_END_RAG' OR "
            "(expected_scope_codes IS NOT NULL AND json_typeof(expected_scope_codes) = 'array' "
            "AND json_array_length(expected_scope_codes) > 0)",
            name="chk_eval_case_end_to_end_scope_codes_nonempty",
        ),
        UniqueConstraint("dataset_id", "case_key", name="uq_eval_case_dataset_key"),
        UniqueConstraint("id", "dataset_id", name="uq_eval_case_id_dataset"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("eval_dataset.id"), nullable=False)
    case_key: Mapped[str] = mapped_column(String(120), nullable=False)
    case_version: Mapped[str] = mapped_column(String(80), nullable=False)
    partition: Mapped[EvaluationDatasetPartition] = mapped_column(
        Enum(EvaluationDatasetPartition, native_enum=False, length=30),
        nullable=False,
    )
    experiment_type: Mapped[EvaluationExperimentType] = mapped_column(
        Enum(EvaluationExperimentType, native_enum=False, length=40),
        nullable=False,
    )
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    gold_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    question_template: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_segment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    medication_family: Mapped[str | None] = mapped_column(String(120), nullable=True)
    transform_origin: Mapped[str | None] = mapped_column(String(120), nullable=True)
    expected_scope_codes: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    expected_outcome_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    dataset: Mapped[EvalDataset] = relationship(back_populates="cases", foreign_keys=[dataset_id])
    results: Mapped[list["EvalCaseResult"]] = relationship(back_populates="case", foreign_keys="EvalCaseResult.case_id")


class EvalExperiment(Base):
    __tablename__ = "eval_experiment"
    __table_args__ = (
        Index("idx_eval_experiment_dataset", "dataset_id"),
        UniqueConstraint("experiment_key", "experiment_version", name="uq_eval_experiment_key_version"),
        CheckConstraint("length(trim(experiment_key)) > 0", name="chk_eval_experiment_key_nonblank"),
        CheckConstraint("length(trim(experiment_version)) > 0", name="chk_eval_experiment_version_nonblank"),
        CheckConstraint("length(trim(policy_ref)) > 0", name="chk_eval_experiment_policy_ref_nonblank"),
        CheckConstraint("length(policy_hash) = 64", name="chk_eval_experiment_policy_hash_length"),
        CheckConstraint(
            f"experiment_type IN ({_sql_in_list(EvaluationExperimentType)})",
            name="chk_eval_experiment_type",
        ),
        UniqueConstraint("id", "dataset_id", name="uq_eval_experiment_id_dataset"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    dataset_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("eval_dataset.id"), nullable=False)
    experiment_key: Mapped[str] = mapped_column(String(120), nullable=False)
    experiment_version: Mapped[str] = mapped_column(String(80), nullable=False)
    experiment_type: Mapped[EvaluationExperimentType] = mapped_column(
        Enum(EvaluationExperimentType, native_enum=False, length=40),
        nullable=False,
    )
    policy_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_set_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rubric_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_release_gate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    dataset: Mapped[EvalDataset] = relationship(back_populates="experiments", foreign_keys=[dataset_id])
    variants: Mapped[list["EvalVariant"]] = relationship(
        back_populates="experiment", foreign_keys="EvalVariant.experiment_id"
    )
    runs: Mapped[list["EvalRun"]] = relationship(back_populates="experiment", foreign_keys="EvalRun.experiment_id")


class EvalVariant(Base):
    __tablename__ = "eval_variant"
    __table_args__ = (
        Index("idx_eval_variant_experiment", "experiment_id"),
        UniqueConstraint("experiment_id", "variant_key", name="uq_eval_variant_experiment_key"),
        UniqueConstraint("experiment_id", "config_hash", name="uq_eval_variant_experiment_config"),
        UniqueConstraint("id", "experiment_id", name="uq_eval_variant_id_experiment"),
        CheckConstraint("length(trim(variant_key)) > 0", name="chk_eval_variant_key_nonblank"),
        CheckConstraint("length(config_hash) = 64", name="chk_eval_variant_config_hash_length"),
        CheckConstraint(
            f"variant_role IN ({_sql_in_list(EvaluationVariantRole)})",
            name="chk_eval_variant_role",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    experiment_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("eval_experiment.id"), nullable=False)
    variant_key: Mapped[str] = mapped_column(String(120), nullable=False)
    variant_role: Mapped[EvaluationVariantRole] = mapped_column(
        Enum(EvaluationVariantRole, native_enum=False, length=20),
        nullable=False,
    )
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_bundle_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_snapshot_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    candidate_index_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    knowledge_index_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parser_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    experiment: Mapped[EvalExperiment] = relationship(back_populates="variants", foreign_keys=[experiment_id])
    runs: Mapped[list["EvalRun"]] = relationship(back_populates="variant", foreign_keys="EvalRun.variant_id")


class EvalRun(Base):
    __tablename__ = "eval_run"
    __table_args__ = (
        Index("idx_eval_run_dataset", "dataset_id"),
        Index("idx_eval_run_experiment", "experiment_id"),
        Index("idx_eval_run_variant", "variant_id"),
        UniqueConstraint("run_key", name="uq_eval_run_key"),
        UniqueConstraint("id", "dataset_id", name="uq_eval_run_id_dataset"),
        ForeignKeyConstraint(
            ["experiment_id", "dataset_id"],
            ["eval_experiment.id", "eval_experiment.dataset_id"],
            name="fk_eval_run_experiment_dataset",
        ),
        ForeignKeyConstraint(
            ["variant_id", "experiment_id"],
            ["eval_variant.id", "eval_variant.experiment_id"],
            name="fk_eval_run_variant_experiment",
        ),
        CheckConstraint("length(trim(run_key)) > 0", name="chk_eval_run_key_nonblank"),
        CheckConstraint("length(git_commit_sha) BETWEEN 7 AND 40", name="chk_eval_run_git_commit_sha_length"),
        CheckConstraint("length(dataset_manifest_hash) = 64", name="chk_eval_run_dataset_hash_length"),
        CheckConstraint(
            "execution_manifest_hash IS NULL OR length(execution_manifest_hash) = 64",
            name="chk_eval_run_execution_manifest_hash_length",
        ),
        CheckConstraint(
            f"execution_status IN ({_sql_in_list(EvaluationExecutionStatus)})",
            name="chk_eval_run_execution_status",
        ),
        CheckConstraint(
            f"decision_status IS NULL OR decision_status IN ({_sql_in_list(EvaluationDecisionStatus)})",
            name="chk_eval_run_decision_status",
        ),
        CheckConstraint(
            "execution_status = 'COMPLETED' OR decision_status IS NULL",
            name="chk_eval_run_incomplete_decision_null",
        ),
        CheckConstraint(
            "completed_at IS NULL OR started_at IS NULL OR completed_at >= started_at",
            name="chk_eval_run_completed_after_started",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    run_key: Mapped[str] = mapped_column(String(120), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("eval_dataset.id"), nullable=False)
    experiment_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("eval_experiment.id"), nullable=False)
    variant_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("eval_variant.id"), nullable=False)
    execution_status: Mapped[EvaluationExecutionStatus] = mapped_column(
        Enum(EvaluationExecutionStatus, native_enum=False, length=30),
        nullable=False,
        default=EvaluationExecutionStatus.NOT_EVALUATED,
    )
    decision_status: Mapped[EvaluationDecisionStatus | None] = mapped_column(
        Enum(EvaluationDecisionStatus, native_enum=False, length=20, values_callable=_enum_values),
        nullable=True,
    )
    git_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    dataset_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate_guard_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    blocking_execution_statuses: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    experiment: Mapped[EvalExperiment] = relationship(back_populates="runs", foreign_keys=[experiment_id])
    variant: Mapped[EvalVariant] = relationship(back_populates="runs", foreign_keys=[variant_id])
    case_results: Mapped[list["EvalCaseResult"]] = relationship(
        back_populates="run", foreign_keys="EvalCaseResult.run_id"
    )
    metrics: Mapped[list["EvalMetric"]] = relationship(back_populates="run")
    failures: Mapped[list["EvalFailure"]] = relationship(back_populates="run")


class EvalCaseResult(Base):
    __tablename__ = "eval_case_result"
    __table_args__ = (
        Index("idx_eval_case_result_case", "case_id"),
        Index("idx_eval_case_result_dataset", "dataset_id"),
        Index("idx_eval_case_result_run", "run_id"),
        UniqueConstraint("run_id", "case_id", name="uq_eval_case_result_run_case"),
        ForeignKeyConstraint(
            ["case_id", "dataset_id"],
            ["eval_case.id", "eval_case.dataset_id"],
            name="fk_eval_case_result_case_dataset",
        ),
        ForeignKeyConstraint(
            ["run_id", "dataset_id"],
            ["eval_run.id", "eval_run.dataset_id"],
            name="fk_eval_case_result_run_dataset",
        ),
        CheckConstraint(
            f"execution_status IN ({_sql_in_list(EvaluationExecutionStatus)})",
            name="chk_eval_case_result_execution_status",
        ),
        CheckConstraint(
            f"decision_status IS NULL OR decision_status IN ({_sql_in_list(EvaluationDecisionStatus)})",
            name="chk_eval_case_result_decision_status",
        ),
        CheckConstraint(
            "execution_status = 'COMPLETED' OR decision_status IS NULL",
            name="chk_eval_case_result_incomplete_decision_null",
        ),
        CheckConstraint(
            "result_summary_hash IS NULL OR length(result_summary_hash) = 64",
            name="chk_eval_case_result_summary_hash_length",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("eval_run.id"), nullable=False)
    case_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("eval_case.id"), nullable=False)
    dataset_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("eval_dataset.id"), nullable=False)
    execution_status: Mapped[EvaluationExecutionStatus] = mapped_column(
        Enum(EvaluationExecutionStatus, native_enum=False, length=30),
        nullable=False,
        default=EvaluationExecutionStatus.NOT_EVALUATED,
    )
    decision_status: Mapped[EvaluationDecisionStatus | None] = mapped_column(
        Enum(EvaluationDecisionStatus, native_enum=False, length=20, values_callable=_enum_values),
        nullable=True,
    )
    request_guard_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    result_summary_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    non_sensitive_summary: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    run: Mapped[EvalRun] = relationship(back_populates="case_results", foreign_keys=[run_id])
    case: Mapped[EvalCase] = relationship(back_populates="results", foreign_keys=[case_id])
    metrics: Mapped[list["EvalMetric"]] = relationship(back_populates="case_result")
    failures: Mapped[list["EvalFailure"]] = relationship(back_populates="case_result")


class EvalMetric(Base):
    __tablename__ = "eval_metric"
    __table_args__ = (
        Index(
            "uq_eval_metric_run_metric",
            "run_id",
            "metric_key",
            "metric_version",
            unique=True,
            postgresql_where=text("run_id IS NOT NULL AND case_result_id IS NULL"),
        ),
        Index(
            "uq_eval_metric_case_metric",
            "case_result_id",
            "metric_key",
            "metric_version",
            unique=True,
            postgresql_where=text("case_result_id IS NOT NULL AND run_id IS NULL"),
        ),
        CheckConstraint("length(trim(metric_key)) > 0", name="chk_eval_metric_key_nonblank"),
        CheckConstraint("length(trim(metric_version)) > 0", name="chk_eval_metric_version_nonblank"),
        CheckConstraint("length(trim(metric_area)) > 0", name="chk_eval_metric_area_nonblank"),
        CheckConstraint(
            f"metric_scope IN ({_sql_in_list(EvaluationMetricScope)})",
            name="chk_eval_metric_scope",
        ),
        CheckConstraint(
            "(metric_scope = 'RUN' AND run_id IS NOT NULL AND case_result_id IS NULL) OR "
            "(metric_scope = 'CASE' AND run_id IS NULL AND case_result_id IS NOT NULL)",
            name="chk_eval_metric_single_owner",
        ),
        CheckConstraint("numerator IS NULL OR numerator >= 0", name="chk_eval_metric_numerator_nonnegative"),
        CheckConstraint("denominator IS NULL OR denominator >= 0", name="chk_eval_metric_denominator_nonnegative"),
        CheckConstraint(
            "numerator IS NULL OR denominator IS NULL OR numerator <= denominator",
            name="chk_eval_metric_numerator_lte_denominator",
        ),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="chk_eval_metric_score_range"),
        CheckConstraint(
            "(confidence_lower IS NULL AND confidence_upper IS NULL) OR "
            "(confidence_lower IS NOT NULL AND confidence_upper IS NOT NULL AND "
            "confidence_lower >= 0 AND confidence_upper <= 1 AND confidence_lower <= confidence_upper)",
            name="chk_eval_metric_confidence_range",
        ),
        CheckConstraint(
            "NOT (is_release_blocking AND is_diagnostic)",
            name="chk_eval_metric_diagnostic_not_release_blocking",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    run_id: Mapped[UUID | None] = mapped_column(UUIDChar(), ForeignKey("eval_run.id"), nullable=True)
    case_result_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("eval_case_result.id"),
        nullable=True,
    )
    metric_scope: Mapped[EvaluationMetricScope] = mapped_column(
        Enum(EvaluationMetricScope, native_enum=False, length=10),
        nullable=False,
    )
    metric_key: Mapped[str] = mapped_column(String(120), nullable=False)
    metric_version: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_area: Mapped[str] = mapped_column(String(80), nullable=False)
    numerator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    denominator: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)
    confidence_lower: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)
    confidence_upper: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)
    is_release_blocking: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_diagnostic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    run: Mapped[EvalRun | None] = relationship(back_populates="metrics")
    case_result: Mapped[EvalCaseResult | None] = relationship(back_populates="metrics")


class EvalFailure(Base):
    __tablename__ = "eval_failure"
    __table_args__ = (
        Index(
            "uq_eval_failure_run_code",
            "run_id",
            "failure_code",
            "failure_area",
            unique=True,
            postgresql_where=text("run_id IS NOT NULL AND case_result_id IS NULL"),
        ),
        Index(
            "uq_eval_failure_case_code",
            "case_result_id",
            "failure_code",
            "failure_area",
            unique=True,
            postgresql_where=text("case_result_id IS NOT NULL AND run_id IS NULL"),
        ),
        CheckConstraint("length(trim(failure_code)) > 0", name="chk_eval_failure_code_nonblank"),
        CheckConstraint("length(trim(failure_area)) > 0", name="chk_eval_failure_area_nonblank"),
        CheckConstraint(
            f"failure_scope IN ({_sql_in_list(EvaluationFailureScope)})",
            name="chk_eval_failure_scope",
        ),
        CheckConstraint(
            "(failure_scope = 'RUN' AND run_id IS NOT NULL AND case_result_id IS NULL) OR "
            "(failure_scope = 'CASE' AND run_id IS NULL AND case_result_id IS NOT NULL)",
            name="chk_eval_failure_single_owner",
        ),
        CheckConstraint(
            "context_hash IS NULL OR length(context_hash) = 64",
            name="chk_eval_failure_context_hash_length",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    run_id: Mapped[UUID | None] = mapped_column(UUIDChar(), ForeignKey("eval_run.id"), nullable=True)
    case_result_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("eval_case_result.id"),
        nullable=True,
    )
    failure_scope: Mapped[EvaluationFailureScope] = mapped_column(
        Enum(EvaluationFailureScope, native_enum=False, length=10),
        nullable=False,
    )
    failure_code: Mapped[str] = mapped_column(String(120), nullable=False)
    failure_area: Mapped[str] = mapped_column(String(80), nullable=False)
    context_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    non_sensitive_context: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    run: Mapped[EvalRun | None] = relationship(back_populates="failures")
    case_result: Mapped[EvalCaseResult | None] = relationship(back_populates="failures")
