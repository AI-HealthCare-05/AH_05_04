from collections.abc import Iterable
from datetime import datetime
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
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from app.core.db.databases import Base
from app.core.db.types import UUIDChar
from app.models.rag_evaluation import EvaluationDecisionStatus


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


def _sql_in_list(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class RagRuntimeBundleStatus(StrEnum):
    BUILDING = "BUILDING"
    READY = "READY"
    RETIRED = "RETIRED"
    FAILED = "FAILED"


class RagRuntimeEnvironmentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"


class RagRuntimeEnvironmentTransitionKind(StrEnum):
    PLANNED_ACTIVATION = "PLANNED_ACTIVATION"
    EMERGENCY_ROLLBACK = "EMERGENCY_ROLLBACK"
    RESUME = "RESUME"
    SUSPEND = "SUSPEND"


class RagRuntimeSourcePurpose(StrEnum):
    CATALOG = "CATALOG"
    KNOWLEDGE = "KNOWLEDGE"
    CANDIDATE_INDEX_INPUT = "CANDIDATE_INDEX_INPUT"
    RULE = "RULE"
    GUIDELINE = "GUIDELINE"
    SAFETY_POLICY = "SAFETY_POLICY"


class RagRuntimeApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RagRuntimeExecutionManifest(Base):
    __tablename__ = "rag_runtime_execution_manifest"
    __table_args__ = (
        UniqueConstraint("manifest_key", "manifest_version", name="uq_rag_runtime_manifest_key_version"),
        UniqueConstraint("manifest_hash", name="uq_rag_runtime_manifest_hash"),
        CheckConstraint("length(trim(manifest_key)) > 0", name="chk_rag_runtime_manifest_key_nonblank"),
        CheckConstraint("length(trim(manifest_version)) > 0", name="chk_rag_runtime_manifest_version_nonblank"),
        CheckConstraint("length(manifest_hash) = 64", name="chk_rag_runtime_manifest_hash_length"),
        CheckConstraint("length(trim(schema_version)) > 0", name="chk_rag_runtime_manifest_schema_version_nonblank"),
        CheckConstraint("length(git_commit_sha) BETWEEN 7 AND 40", name="chk_rag_runtime_manifest_git_sha_length"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    manifest_key: Mapped[str] = mapped_column(String(120), nullable=False)
    manifest_version: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    git_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    worker_artifact_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parser_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolver_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guard_policy_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    metadata_json: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    bundles: Mapped[list["RagRuntimeReleaseBundle"]] = relationship(back_populates="execution_manifest")


class RagRuntimeReleaseBundle(Base):
    __tablename__ = "rag_runtime_release_bundle"
    __table_args__ = (
        UniqueConstraint("bundle_key", "bundle_version", name="uq_rag_runtime_bundle_key_version"),
        UniqueConstraint("bundle_manifest_hash", name="uq_rag_runtime_bundle_manifest_hash"),
        UniqueConstraint("id", "bundle_manifest_hash", name="uq_rag_runtime_bundle_id_manifest_hash"),
        CheckConstraint("length(trim(bundle_key)) > 0", name="chk_rag_runtime_bundle_key_nonblank"),
        CheckConstraint("length(trim(bundle_version)) > 0", name="chk_rag_runtime_bundle_version_nonblank"),
        CheckConstraint("length(bundle_manifest_hash) = 64", name="chk_rag_runtime_bundle_manifest_hash_length"),
        CheckConstraint(
            f"bundle_status IN ({_sql_in_list(RagRuntimeBundleStatus)})",
            name="chk_rag_runtime_bundle_status",
        ),
        CheckConstraint(
            "candidate_index_manifest_hash IS NULL OR length(candidate_index_manifest_hash) = 64",
            name="chk_rag_runtime_bundle_candidate_index_hash_length",
        ),
        CheckConstraint(
            "knowledge_index_manifest_hash IS NULL OR length(knowledge_index_manifest_hash) = 64",
            name="chk_rag_runtime_bundle_knowledge_index_hash_length",
        ),
        CheckConstraint("length(trim(environment_code)) > 0", name="chk_rag_runtime_bundle_environment_code_nonblank"),
        CheckConstraint("length(trim(catalog_version)) > 0", name="chk_rag_runtime_bundle_catalog_version_nonblank"),
        CheckConstraint("length(catalog_manifest_hash) = 64", name="chk_rag_runtime_bundle_catalog_manifest_hash"),
        # An artifact member is identified by ref AND version together; neither alone is an
        # identity, so a half-populated pair must not be storable.
        *(
            CheckConstraint(
                f"({kind}_ref IS NULL) = ({kind}_version IS NULL)",
                name=f"chk_rag_runtime_bundle_{kind}_ref_version_pair",
            )
            for kind in ("candidate_index", "knowledge_index", "rule_set", "guideline_set", "safety_policy")
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    bundle_key: Mapped[str] = mapped_column(String(120), nullable=False)
    bundle_version: Mapped[str] = mapped_column(String(80), nullable=False)
    bundle_status: Mapped[RagRuntimeBundleStatus] = mapped_column(
        Enum(RagRuntimeBundleStatus, native_enum=False, length=20),
        nullable=False,
        default=RagRuntimeBundleStatus.BUILDING,
    )
    execution_manifest_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_runtime_execution_manifest.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bundle_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # rag_runtime_release_bundle has no environment FK: the environment row points at the bundle,
    # not the reverse.  environment_code is therefore the only place the environment that this
    # content was built for can be pinned, and it enters bundle_manifest_hash.
    environment_code: Mapped[str] = mapped_column(String(50), nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(80), nullable=False)
    catalog_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_index_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    candidate_index_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    candidate_index_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    knowledge_index_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    knowledge_index_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    knowledge_index_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_set_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rule_set_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    guideline_set_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    guideline_set_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    safety_policy_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    safety_policy_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    governance_revision_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    execution_manifest: Mapped[RagRuntimeExecutionManifest] = relationship(back_populates="bundles")
    source_snapshots: Mapped[list["RagRuntimeBundleSource"]] = relationship(back_populates="bundle")
    evaluation_approvals: Mapped[list["RagReleaseEvaluationApproval"]] = relationship(back_populates="bundle")


class RagRuntimeBundleSource(Base):
    """One pinned source member.

    Every field that enters ``bundle_manifest_hash`` is stored here, so the hash can be
    recomputed from persisted rows and compared with the stored value.  Without that the hash is
    an opaque token and the ``rag-runtime-v1.md`` requirement to re-verify the Bundle Manifest
    before a pointer change cannot be met.  ``source_version`` is validated against
    ``rag_source_snapshot`` inside the build transaction, not by a composite FK: depending on
    #369's ``uq_rag_source_snapshot_id_version`` made that constraint undroppable and broke #369's
    own downgrade tests.  #398 moved integrity enforcement into Python, and this follows it.
    """

    __tablename__ = "rag_runtime_bundle_source"
    __table_args__ = (
        UniqueConstraint(
            "bundle_id",
            "source_snapshot_id",
            "source_purpose",
            name="uq_rag_runtime_bundle_source_member",
        ),
        CheckConstraint(
            f"source_purpose IN ({_sql_in_list(RagRuntimeSourcePurpose)})",
            name="chk_rag_runtime_bundle_source_purpose",
        ),
        CheckConstraint("length(trim(source_version)) > 0", name="chk_rag_runtime_bundle_source_version_nonblank"),
        CheckConstraint("length(trim(approval_version)) > 0", name="chk_rag_runtime_bundle_source_approval_nonblank"),
        CheckConstraint("length(canonical_checksum) = 64", name="chk_rag_runtime_bundle_source_canonical_checksum"),
        CheckConstraint("length(scope_policy_hash) = 64", name="chk_rag_runtime_bundle_source_scope_policy_hash"),
        CheckConstraint(
            "length(freshness_policy_hash) = 64",
            name="chk_rag_runtime_bundle_source_freshness_policy_hash",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    bundle_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_runtime_release_bundle.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_snapshot_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_source_snapshot.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_version: Mapped[str] = mapped_column(String(255), nullable=False)
    canonical_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_version: Mapped[str] = mapped_column(String(80), nullable=False)
    scope_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    freshness_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_purpose: Mapped[RagRuntimeSourcePurpose] = mapped_column(
        Enum(RagRuntimeSourcePurpose, native_enum=False, length=30),
        nullable=False,
    )
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    selected_for_operation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    bundle: Mapped[RagRuntimeReleaseBundle] = relationship(back_populates="source_snapshots")


class RagRuntimeEnvironment(Base):
    __tablename__ = "rag_runtime_environment"
    __table_args__ = (
        UniqueConstraint("environment_code", name="uq_rag_runtime_environment_code"),
        ForeignKeyConstraint(
            ["active_bundle_id", "active_bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_rag_runtime_environment_active_bundle_manifest",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(trim(environment_code)) > 0", name="chk_rag_runtime_environment_code_nonblank"),
        CheckConstraint(
            f"environment_status IN ({_sql_in_list(RagRuntimeEnvironmentStatus)})",
            name="chk_rag_runtime_environment_status",
        ),
        CheckConstraint(
            "(active_bundle_id IS NULL AND active_bundle_manifest_hash IS NULL) OR "
            "(active_bundle_id IS NOT NULL AND active_bundle_manifest_hash IS NOT NULL)",
            name="chk_rag_runtime_environment_active_bundle_pair",
        ),
        CheckConstraint(
            "active_bundle_manifest_hash IS NULL OR length(active_bundle_manifest_hash) = 64",
            name="chk_rag_runtime_environment_active_bundle_hash_length",
        ),
        CheckConstraint("environment_revision >= 1", name="chk_rag_runtime_environment_revision_positive"),
        CheckConstraint("safety_epoch >= 1", name="chk_rag_runtime_environment_safety_epoch_positive"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    environment_code: Mapped[str] = mapped_column(String(50), nullable=False)
    environment_status: Mapped[RagRuntimeEnvironmentStatus] = mapped_column(
        Enum(RagRuntimeEnvironmentStatus, native_enum=False, length=20),
        nullable=False,
        default=RagRuntimeEnvironmentStatus.SUSPENDED,
    )
    active_bundle_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    active_bundle_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    environment_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    governance_revision_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    safety_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    transitions: Mapped[list["RagRuntimeEnvironmentTransition"]] = relationship(back_populates="environment")


class RagRuntimeEnvironmentTransition(Base):
    __tablename__ = "rag_runtime_environment_transition"
    __table_args__ = (
        UniqueConstraint(
            "environment_id",
            "environment_revision",
            name="uq_rag_runtime_transition_environment_revision",
        ),
        ForeignKeyConstraint(
            ["from_bundle_id", "from_bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_rag_runtime_transition_from_bundle_manifest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["to_bundle_id", "to_bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_rag_runtime_transition_to_bundle_manifest",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"transition_kind IN ({_sql_in_list(RagRuntimeEnvironmentTransitionKind)})",
            name="chk_rag_runtime_transition_kind",
        ),
        CheckConstraint(
            "(from_bundle_id IS NULL AND from_bundle_manifest_hash IS NULL) OR "
            "(from_bundle_id IS NOT NULL AND from_bundle_manifest_hash IS NOT NULL)",
            name="chk_rag_runtime_transition_from_bundle_pair",
        ),
        CheckConstraint(
            "(to_bundle_id IS NULL AND to_bundle_manifest_hash IS NULL) OR "
            "(to_bundle_id IS NOT NULL AND to_bundle_manifest_hash IS NOT NULL)",
            name="chk_rag_runtime_transition_to_bundle_pair",
        ),
        CheckConstraint(
            "from_bundle_manifest_hash IS NULL OR length(from_bundle_manifest_hash) = 64",
            name="chk_rag_runtime_transition_from_hash_length",
        ),
        CheckConstraint(
            "to_bundle_manifest_hash IS NULL OR length(to_bundle_manifest_hash) = 64",
            name="chk_rag_runtime_transition_to_hash_length",
        ),
        CheckConstraint("environment_revision >= 1", name="chk_rag_runtime_transition_revision_positive"),
        CheckConstraint("safety_epoch >= 1", name="chk_rag_runtime_transition_safety_epoch_positive"),
        CheckConstraint("length(trim(guard_decision_ref)) > 0", name="chk_rag_runtime_transition_guard_ref_nonblank"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    environment_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_runtime_environment.id", ondelete="RESTRICT"),
        nullable=False,
    )
    transition_kind: Mapped[RagRuntimeEnvironmentTransitionKind] = mapped_column(
        Enum(RagRuntimeEnvironmentTransitionKind, native_enum=False, length=30),
        nullable=False,
    )
    from_bundle_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    from_bundle_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_bundle_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    to_bundle_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    environment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    governance_revision_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    safety_epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    guard_decision_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    transition_reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    environment: Mapped[RagRuntimeEnvironment] = relationship(back_populates="transitions")


class RagReleaseEvaluationApproval(Base):
    __tablename__ = "rag_release_evaluation_approval"
    __table_args__ = (
        UniqueConstraint("bundle_id", "eval_run_id", "approval_scope", name="uq_rag_release_eval_approval_scope"),
        ForeignKeyConstraint(
            ["bundle_id", "bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_rag_release_eval_approval_bundle_manifest",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["eval_run_id", "eval_decision_status"],
            ["eval_run.id", "eval_run.decision_status"],
            name="fk_rag_release_eval_approval_run_decision",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(trim(approval_scope)) > 0", name="chk_rag_release_eval_approval_scope_nonblank"),
        CheckConstraint("length(bundle_manifest_hash) = 64", name="chk_rag_release_eval_approval_bundle_hash_length"),
        CheckConstraint(
            f"approval_status IN ({_sql_in_list(RagRuntimeApprovalStatus)})",
            name="chk_rag_release_eval_approval_status",
        ),
        CheckConstraint(
            f"eval_decision_status IN ({_sql_in_list(EvaluationDecisionStatus)})",
            name="chk_rag_release_eval_approval_decision_status",
        ),
        CheckConstraint(
            "approval_status <> 'APPROVED' OR "
            "(eval_decision_status = 'PASS' AND approved_by IS NOT NULL AND "
            "length(trim(approved_by)) > 0 AND approved_at IS NOT NULL)",
            name="chk_rag_release_eval_approval_requires_pass",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    bundle_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    bundle_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    eval_run_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    eval_decision_status: Mapped[EvaluationDecisionStatus] = mapped_column(
        Enum(EvaluationDecisionStatus, native_enum=False, length=20, values_callable=_enum_values),
        nullable=False,
    )
    approval_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    approval_status: Mapped[RagRuntimeApprovalStatus] = mapped_column(
        Enum(RagRuntimeApprovalStatus, native_enum=False, length=20),
        nullable=False,
        default=RagRuntimeApprovalStatus.PENDING,
    )
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    bundle: Mapped[RagRuntimeReleaseBundle] = relationship(back_populates="evaluation_approvals")


Index("idx_rag_runtime_bundle_status", RagRuntimeReleaseBundle.bundle_status)
Index("idx_rag_runtime_bundle_source_snapshot", RagRuntimeBundleSource.source_snapshot_id)
Index("idx_rag_runtime_environment_active_bundle", RagRuntimeEnvironment.active_bundle_id)
Index("idx_rag_runtime_transition_environment", RagRuntimeEnvironmentTransition.environment_id)
Index("idx_rag_runtime_transition_to_bundle", RagRuntimeEnvironmentTransition.to_bundle_id)
Index("idx_rag_release_eval_approval_eval_run", RagReleaseEvaluationApproval.eval_run_id)


class AiJobIntakeContext(Base):
    __tablename__ = "ai_job_intake_context"
    __table_args__ = (
        UniqueConstraint("ai_job_id", name="uq_ai_job_intake_context_job"),
        UniqueConstraint("chat_message_id", name="uq_ai_job_intake_context_chat_message"),
        ForeignKeyConstraint(
            ["runtime_release_bundle_id", "runtime_release_bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_ai_job_intake_context_bundle_manifest",
            ondelete="RESTRICT",
        ),
        CheckConstraint("runtime_environment_revision >= 1", name="chk_ai_job_intake_context_revision_positive"),
        CheckConstraint(
            "length(runtime_release_bundle_manifest_hash) = 64",
            name="chk_ai_job_intake_context_bundle_hash_length",
        ),
        CheckConstraint(
            "length(runtime_execution_manifest_hash) = 64",
            name="chk_ai_job_intake_context_manifest_hash_length",
        ),
        CheckConstraint(
            "question_digest IS NULL OR length(question_digest) = 64",
            name="chk_ai_job_intake_context_question_digest_length",
        ),
        CheckConstraint(
            "patient_context_digest IS NULL OR length(patient_context_digest) = 64",
            name="chk_ai_job_intake_context_patient_digest_length",
        ),
        CheckConstraint(
            "length(trim(runtime_guard_decision_ref)) > 0",
            name="chk_ai_job_intake_context_guard_ref_nonblank",
        ),
        CheckConstraint(
            "length(trim(context_schema_version)) > 0",
            name="chk_ai_job_intake_context_schema_nonblank",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    ai_job_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("ai_job.id", name="fk_ai_job_intake_context_job", ondelete="CASCADE"),
        nullable=False,
    )
    chat_message_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("chat_message.id", name="fk_ai_job_intake_context_chat_message", ondelete="RESTRICT"),
        nullable=False,
    )
    prescription_version_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey(
            "prescription_version.id",
            name="fk_ai_job_intake_context_prescription_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    runtime_environment_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_runtime_environment.id", name="fk_ai_job_intake_context_environment", ondelete="RESTRICT"),
        nullable=False,
    )
    runtime_environment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_release_bundle_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    runtime_release_bundle_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_execution_manifest_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey(
            "rag_runtime_execution_manifest.id",
            name="fk_ai_job_intake_context_execution_manifest",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    runtime_execution_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_guard_decision_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    question_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    patient_context_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_schema_version: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="ai-job-intake-context@1",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AiJobExecutionContext(Base):
    __tablename__ = "ai_job_execution_context"
    __table_args__ = (
        UniqueConstraint("ai_job_id", name="uq_ai_job_execution_context_job"),
        ForeignKeyConstraint(
            ["runtime_release_bundle_id", "runtime_release_bundle_manifest_hash"],
            ["rag_runtime_release_bundle.id", "rag_runtime_release_bundle.bundle_manifest_hash"],
            name="fk_ai_job_execution_context_bundle_manifest",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(guide_id IS NOT NULL AND chat_message_id IS NULL) OR (guide_id IS NULL AND chat_message_id IS NOT NULL)",
            name="chk_ai_job_execution_context_one_domain",
        ),
        CheckConstraint(
            "intake_context_id IS NULL OR chat_message_id IS NOT NULL",
            name="chk_ai_job_execution_context_intake_chat_only",
        ),
        CheckConstraint("runtime_environment_revision >= 1", name="chk_ai_job_execution_context_revision_positive"),
        CheckConstraint(
            "length(runtime_release_bundle_manifest_hash) = 64",
            name="chk_ai_job_execution_context_bundle_hash_length",
        ),
        CheckConstraint(
            "length(runtime_execution_manifest_hash) = 64",
            name="chk_ai_job_execution_context_manifest_hash_length",
        ),
        CheckConstraint(
            "patient_context_digest IS NULL OR length(patient_context_digest) = 64",
            name="chk_ai_job_execution_context_patient_digest_length",
        ),
        CheckConstraint(
            "source_scope_manifest_hash IS NULL OR length(source_scope_manifest_hash) = 64",
            name="chk_ai_job_execution_context_scope_hash_length",
        ),
        CheckConstraint(
            "length(trim(runtime_guard_decision_ref)) > 0",
            name="chk_ai_job_execution_context_guard_ref_nonblank",
        ),
        CheckConstraint(
            "length(trim(context_schema_version)) > 0",
            name="chk_ai_job_execution_context_schema_nonblank",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    ai_job_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("ai_job.id", name="fk_ai_job_execution_context_job", ondelete="CASCADE"),
        nullable=False,
    )
    intake_context_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("ai_job_intake_context.id", name="fk_ai_job_execution_context_intake", ondelete="RESTRICT"),
        nullable=True,
    )
    guide_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("guide.id", name="fk_ai_job_execution_context_guide", ondelete="RESTRICT"),
        nullable=True,
    )
    chat_message_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("chat_message.id", name="fk_ai_job_execution_context_chat_message", ondelete="RESTRICT"),
        nullable=True,
    )
    prescription_version_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey(
            "prescription_version.id",
            name="fk_ai_job_execution_context_prescription_version",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    runtime_environment_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey("rag_runtime_environment.id", name="fk_ai_job_execution_context_environment", ondelete="RESTRICT"),
        nullable=False,
    )
    runtime_environment_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime_release_bundle_id: Mapped[UUID] = mapped_column(UUIDChar(), nullable=False)
    runtime_release_bundle_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_execution_manifest_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey(
            "rag_runtime_execution_manifest.id",
            name="fk_ai_job_execution_context_execution_manifest",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    runtime_execution_manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    runtime_guard_decision_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    patient_context_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_scope_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_schema_version: Mapped[str] = mapped_column(
        String(80),
        nullable=False,
        default="ai-job-execution-context@1",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class AiJobExecutionIdentification(Base):
    __tablename__ = "ai_job_execution_identification"
    __table_args__ = (
        UniqueConstraint(
            "execution_context_id",
            "medication_identification_id",
            name="uq_ai_job_execution_identification_identification",
        ),
        UniqueConstraint(
            "execution_context_id",
            "prescription_version_medication_id",
            name="uq_ai_job_execution_identification_medication",
        ),
        ForeignKeyConstraint(
            ["medication_identification_id", "prescription_version_medication_id"],
            ["medication_identification.id", "medication_identification.prescription_version_medication_id"],
            name="fk_ai_job_execution_identification_matched_medication",
            ondelete="RESTRICT",
        ),
        Index("idx_ai_job_execution_identification_context", "execution_context_id"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    execution_context_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey(
            "ai_job_execution_context.id",
            name="fk_ai_job_execution_identification_context",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    medication_identification_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        nullable=False,
    )
    prescription_version_medication_id: Mapped[UUID] = mapped_column(
        UUIDChar(),
        ForeignKey(
            "prescription_version_medication.id",
            name="fk_ai_job_execution_identification_medication",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


Index("idx_ai_job_intake_context_job", AiJobIntakeContext.ai_job_id)
Index("idx_ai_job_intake_context_prescription_version", AiJobIntakeContext.prescription_version_id)
Index("idx_ai_job_execution_context_job", AiJobExecutionContext.ai_job_id)
Index("idx_ai_job_execution_context_prescription_version", AiJobExecutionContext.prescription_version_id)
