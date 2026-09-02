from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.db.databases import Base
from app.core.db.types import UUIDChar

if TYPE_CHECKING:
    from app.models.users import User


def _sql_in_list(values: Iterable[str]) -> str:
    """`CheckConstraint`의 `IN (...)` 목록을 만듭니다. enum과 문자열 값을 모두 받습니다."""
    return ", ".join(f"'{value}'" for value in values)


def _now_timestamp_column() -> Mapped[datetime]:
    """DB 서버 시각(`func.now()`)을 기본값으로 쓰는 timezone-aware timestamp 컬럼입니다."""
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class _CreatedUpdatedColumns:
    """생성·수정 시각을 함께 기록하는 테이블(`AiJob`, `OutboxEvent`, `IdempotencyRecord`,
    `DlqOutboxEvent`)이 공유하는 컬럼 mixin입니다."""

    created_at: Mapped[datetime] = _now_timestamp_column()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


_FAILURE_CODE_VALUES = (
    "TIMEOUT",
    "DEPENDENCY_UNAVAILABLE",
    "INVALID_INPUT",
    "UNSUPPORTED_SCHEMA",
    "SAFETY_VALIDATION_FAILED",
    "RETRY_EXHAUSTED",
    "INTERNAL_ERROR",
)
_FAILURE_CODE_LIST_SQL = _sql_in_list(_FAILURE_CODE_VALUES)


class AiJobType(StrEnum):
    OCR = "OCR"
    GUIDE = "GUIDE"
    CHAT = "CHAT"


class DomainType(StrEnum):
    OCR_JOB = "OCR_JOB"
    GUIDE = "GUIDE"
    CHAT_MESSAGE = "CHAT_MESSAGE"


DOMAIN_TYPE_BY_JOB_TYPE: dict[AiJobType, DomainType] = {
    AiJobType.OCR: DomainType.OCR_JOB,
    AiJobType.GUIDE: DomainType.GUIDE,
    AiJobType.CHAT: DomainType.CHAT_MESSAGE,
}


class AiJobStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    RETRY_WAIT = "RETRY_WAIT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    STALE = "STALE"


class AiJobAttemptStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class OutboxEventKind(StrEnum):
    JOB_EXECUTE = "JOB_EXECUTE"


class OutboxEventStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"


class IdempotencyRecordType(StrEnum):
    ASYNC_JOB = "ASYNC_JOB"
    SYNC_MUTATION = "SYNC_MUTATION"


class DlqOutboxEventKind(StrEnum):
    QUARANTINE_RECORDED = "QUARANTINE_RECORDED"


class DlqOutboxEventStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    PUBLISHED = "PUBLISHED"


class AiJob(_CreatedUpdatedColumns, Base):
    __tablename__ = "ai_job"
    __table_args__ = (
        Index("idx_ai_job_user_status_updated", "user_id", "status", "updated_at", "id"),
        Index("idx_ai_job_status_available", "status", "available_at", "id"),
        Index("idx_ai_job_expected_event", "expected_event_id"),
        # outbox_event/dlq_outbox_event의 claim_expires_at처럼 만료 lease reclaim 조회용입니다.
        # 90일 보존으로 셋 중 가장 크게 자라는 테이블이라 인덱스 없이는 이 조회가 full scan이 됩니다.
        Index("idx_ai_job_lease_expires", "status", "lease_expires_at", "id"),
        CheckConstraint(f"job_type IN ({_sql_in_list(AiJobType)})", name="chk_ai_job_type"),
        CheckConstraint(
            f"status IN ({_sql_in_list(AiJobStatus)})",
            name="chk_ai_job_status",
        ),
        CheckConstraint("attempt_count >= 0", name="chk_ai_job_attempt_count"),
        CheckConstraint("max_attempts > 0", name="chk_ai_job_max_attempts"),
        CheckConstraint("attempt_count <= max_attempts", name="chk_ai_job_attempt_limit"),
        CheckConstraint("status <> 'FAILED' OR failure_code IS NOT NULL", name="chk_ai_job_failed_code"),
        CheckConstraint(
            f"failure_code IS NULL OR failure_code IN ({_FAILURE_CODE_LIST_SQL})",
            name="chk_ai_job_failure_code_values",
        ),
        CheckConstraint(
            "status NOT IN ('COMPLETED', 'FAILED', 'STALE') OR completed_at IS NOT NULL",
            name="chk_ai_job_terminal_completed_at",
        ),
        CheckConstraint(
            "status <> 'PROCESSING' OR (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="chk_ai_job_processing_lease",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id"), nullable=False)
    job_type: Mapped[AiJobType] = mapped_column(Enum(AiJobType, native_enum=False, length=20), nullable=False)
    status: Mapped[AiJobStatus] = mapped_column(
        Enum(AiJobStatus, native_enum=False, length=20),
        nullable=False,
        default=AiJobStatus.PENDING,
    )
    prescription_version_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    # ai_job -> outbox_event -> ai_job은 순환 참조라 use_alter로 걸어야
    # Base.metadata.create_all()/drop_all()이 두 테이블 순서를 정할 수 있습니다.
    # Outbox는 30일, Job은 90일 보존이라 Outbox가 먼저 삭제될 수 있으므로 ON DELETE SET NULL로
    # 둡니다 — 기본값(NO ACTION)이면 Outbox 정리 배치가 FK 위반으로 실패합니다.
    expected_event_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("outbox_event.event_id", use_alter=True, name="fk_ai_job_expected_event", ondelete="SET NULL"),
        nullable=True,
    )
    last_consumed_event_id: Mapped[UUID | None] = mapped_column(
        UUIDChar(),
        ForeignKey("outbox_event.event_id", use_alter=True, name="fk_ai_job_last_consumed_event", ondelete="SET NULL"),
        nullable=True,
    )
    # 0은 컬럼 default이며, 접수 시점 값과 증가 시점·Worker fencing 검증 방식(outbox_event.attempt=1과의
    # 관계 포함)은 outbox-stream-v1.md "소비와 fencing" 기준으로 #141에서 계약과 함께 확정합니다.
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    available_at: Mapped[datetime] = _now_timestamp_column()
    lease_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship()
    outbox_events: Mapped[list["OutboxEvent"]] = relationship(
        back_populates="job",
        foreign_keys="OutboxEvent.job_id",
    )
    idempotency_records: Mapped[list["IdempotencyRecord"]] = relationship(back_populates="job")
    attempts: Mapped[list["AiJobAttempt"]] = relationship(back_populates="job")


class AiJobAttempt(Base):
    __tablename__ = "ai_job_attempt"
    __table_args__ = (
        UniqueConstraint("ai_job_id", "attempt_no", name="uq_ai_job_attempt_no"),
        Index("idx_ai_job_attempt_status_started", "attempt_status", "started_at"),
        CheckConstraint("attempt_no >= 1", name="chk_ai_job_attempt_attempt_no"),
        CheckConstraint(
            f"attempt_status IN ({_sql_in_list(AiJobAttemptStatus)})",
            name="chk_ai_job_attempt_status",
        ),
        CheckConstraint(
            "attempt_status NOT IN ('COMPLETED', 'BLOCKED', 'FAILED') OR completed_at IS NOT NULL",
            name="chk_ai_job_attempt_terminal_completed_at",
        ),
        CheckConstraint("attempt_status <> 'FAILED' OR error_code IS NOT NULL", name="chk_ai_job_attempt_failed_code"),
        CheckConstraint(
            f"error_code IS NULL OR error_code IN ({_FAILURE_CODE_LIST_SQL})",
            name="chk_ai_job_attempt_error_code_values",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    ai_job_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("ai_job.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_status: Mapped[AiJobAttemptStatus] = mapped_column(
        Enum(AiJobAttemptStatus, native_enum=False, length=30),
        nullable=False,
        default=AiJobAttemptStatus.PROCESSING,
    )
    runtime_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    timed_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = _now_timestamp_column()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped["AiJob"] = relationship(back_populates="attempts")


class OutboxEvent(_CreatedUpdatedColumns, Base):
    __tablename__ = "outbox_event"
    __table_args__ = (
        UniqueConstraint("job_id", "attempt", "event_kind", name="uq_outbox_event_job_attempt_kind"),
        Index("idx_outbox_status_available", "status", "available_at", "event_id"),
        Index("idx_outbox_claim_expires", "claim_expires_at", "event_id"),
        CheckConstraint(f"event_kind IN ({_sql_in_list(OutboxEventKind)})", name="chk_outbox_event_kind"),
        CheckConstraint(f"status IN ({_sql_in_list(OutboxEventStatus)})", name="chk_outbox_status"),
        CheckConstraint("attempt > 0", name="chk_outbox_attempt"),
        CheckConstraint(
            f"domain_type IS NULL OR domain_type IN ({_sql_in_list(DomainType)})", name="chk_outbox_domain_type"
        ),
    )

    event_id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("ai_job.id"), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    event_kind: Mapped[OutboxEventKind] = mapped_column(
        Enum(OutboxEventKind, native_enum=False, length=30),
        nullable=False,
        default=OutboxEventKind.JOB_EXECUTE,
    )
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    status: Mapped[OutboxEventStatus] = mapped_column(
        Enum(OutboxEventStatus, native_enum=False, length=20),
        nullable=False,
        default=OutboxEventStatus.PENDING,
    )
    available_at: Mapped[datetime] = _now_timestamp_column()
    claim_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # outbox-stream-v1.md Stream envelope의 required trace_id입니다. 접수 시점의 request.state.trace_id를
    # 저장해두지 않으면, 나중에 실제 발행(XADD) 시점에는 원래 HTTP 요청이 이미 끝나 그 값을 잃어버려
    # live-provider-call-evidence.md가 세우는 runner→Backend 로그→provider 로그 상관관계가 접수 경로에서
    # 끊깁니다. WorkerMessage.trace_id가 32자리 hex required라 NULL이면 #219가 발행 시 예외 분기를
    # 둬야 하므로, JobIntakeService.accept_job()/AsyncJobRepository.create_outbox_event()는 이 값을
    # 필수 인자로 받습니다. 컬럼 자체는 과거 row나 이 서비스 밖의 다른 경로를 위해 nullable로 둡니다
    # (값 형식 검증은 컬럼 레벨에 없고 #219에서 확인).
    trace_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Stream envelope의 required domain_type/domain_id입니다. ai_job은 domain_type/domain_id 물리
    # 컬럼을 두지 않기로 확정했고(PR #184) 도메인 테이블에도 ai_job으로부터의 역참조 컬럼이 아직
    # 없어서, 나중에 역조회할 방법이 없습니다. 대신 create_domain_placeholder가 도메인 row를 막 만든
    # 접수 시점에 이미 알고 있는 값을 여기 그대로 저장해 역조회 자체를 필요 없게 만듭니다.
    # #142(Pending Reclaim·재시도)가 새 attempt의 retry Outbox event를 만들 때는, 같은 Job의 직전
    # Outbox event에서 이 domain_type/domain_id를 그대로 복사해야 합니다 — 재시도는 같은 도메인
    # 결과를 다시 만드는 게 아니라 같은 결과를 향한 재시도이므로 참조가 바뀌면 안 됩니다.
    domain_type: Mapped[DomainType | None] = mapped_column(
        Enum(DomainType, native_enum=False, length=20), nullable=True
    )
    domain_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)

    job: Mapped["AiJob"] = relationship(back_populates="outbox_events", foreign_keys=[job_id])


class IdempotencyRecord(_CreatedUpdatedColumns, Base):
    __tablename__ = "idempotency_record"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_idempotency_job"),
        # 비동기 접수(ASYNC_JOB)는 parent_resource_id가 항상 NULL이라, 일반 UNIQUE 제약에 포함하면
        # PostgreSQL이 NULL끼리는 다르다고 취급해 중복 접수를 막지 못합니다. record_type별로
        # scope가 다른 두 partial unique index로 나눠서 걸어야 합니다.
        Index(
            "uq_idempotency_async_scope",
            "record_type",
            "user_id",
            "operation_id",
            "key_hmac",
            unique=True,
            postgresql_where=text("record_type = 'ASYNC_JOB'"),
        ),
        Index(
            "uq_idempotency_sync_scope",
            "record_type",
            "user_id",
            "operation_id",
            "parent_resource_id",
            "key_hmac",
            unique=True,
            postgresql_where=text("record_type = 'SYNC_MUTATION'"),
        ),
        Index("idx_idempotency_user_operation", "user_id", "operation_id", "created_at"),
        Index("idx_idempotency_expires", "expires_at", "id"),
        CheckConstraint(f"record_type IN ({_sql_in_list(IdempotencyRecordType)})", name="chk_idempotency_record_type"),
        CheckConstraint(
            "("
            "record_type = 'ASYNC_JOB' "
            "AND job_id IS NOT NULL "
            "AND parent_resource_id IS NULL "
            "AND response_status IS NULL "
            "AND response_body_snapshot IS NULL "
            "AND encryption_key_version IS NULL"
            ") OR ("
            "record_type = 'SYNC_MUTATION' "
            "AND job_id IS NULL "
            "AND parent_resource_id IS NOT NULL "
            "AND response_status IS NOT NULL "
            "AND response_body_snapshot IS NOT NULL "
            "AND encryption_key_version IS NOT NULL"
            ")",
            name="chk_idempotency_record_payload",
        ),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("user.id"), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hmac_version: Mapped[str] = mapped_column(String(20), nullable=False)
    key_hmac: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    record_type: Mapped[IdempotencyRecordType] = mapped_column(
        Enum(IdempotencyRecordType, native_enum=False, length=30),
        nullable=False,
    )
    job_id: Mapped[UUID | None] = mapped_column(UUIDChar(), ForeignKey("ai_job.id"), nullable=True)
    parent_resource_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # idempotency-v1.md: SYNC_MUTATION snapshot은 암호화 후 PostgreSQL BYTEA로 저장합니다.
    response_body_snapshot: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    encryption_key_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    user: Mapped["User"] = relationship()
    job: Mapped["AiJob | None"] = relationship(back_populates="idempotency_records")


class MessageQuarantine(Base):
    __tablename__ = "message_quarantine"
    __table_args__ = (
        UniqueConstraint("stream_name", "stream_entry_id", name="uq_message_quarantine_stream_entry"),
        Index("idx_message_quarantine_received", "received_at", "id"),
        Index("idx_message_quarantine_failure", "failure_code", "received_at"),
        Index("idx_message_quarantine_job", "job_id"),
    )

    id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    stream_name: Mapped[str] = mapped_column(String(100), nullable=False)
    stream_entry_id: Mapped[str] = mapped_column(String(100), nullable=False)
    message_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    # job_id를 메시지에서 파싱할 수 있었을 때만 채워집니다. quarantine이 필요한 대표 케이스가
    # "job_id는 파싱되는데 DB에 그 Job이 없는 메시지"(90일 보존 만료, 손상 메시지, dead-letter 재생 등)라
    # FK를 걸면 그 격리 자체가 INSERT 실패로 막힙니다. 그래서 FK 없는 nullable UUID + 인덱스로 둡니다.
    job_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    original_event_id: Mapped[UUID | None] = mapped_column(UUIDChar(), nullable=True)
    # ai_job.failure_code/ai_job_attempt.error_code와 의도적으로 다른 도메인입니다 — 이건 Job 실행 실패가
    # 아니라 메시지 자체(파싱 불가, Stream 손상 등)의 실패라 _FAILURE_CODE_VALUES allowlist로 제한하지
    # 않습니다. 원문 예외 문구를 그대로 넣지 않는 책임은 여전히 이 컬럼을 채우는 코드에 있습니다.
    failure_code: Mapped[str] = mapped_column(String(100), nullable=False)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_schema_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    received_at: Mapped[datetime] = _now_timestamp_column()
    created_at: Mapped[datetime] = _now_timestamp_column()

    dlq_outbox_event: Mapped["DlqOutboxEvent | None"] = relationship(back_populates="quarantine")


class DlqOutboxEvent(_CreatedUpdatedColumns, Base):
    __tablename__ = "dlq_outbox_event"
    __table_args__ = (
        UniqueConstraint("quarantine_id", name="uq_dlq_outbox_quarantine"),
        Index("idx_dlq_outbox_status_available", "status", "available_at", "event_id"),
        Index("idx_dlq_outbox_claim_expires", "claim_expires_at", "event_id"),
        CheckConstraint(f"event_kind IN ({_sql_in_list(DlqOutboxEventKind)})", name="chk_dlq_outbox_event_kind"),
        CheckConstraint(f"status IN ({_sql_in_list(DlqOutboxEventStatus)})", name="chk_dlq_outbox_status"),
        CheckConstraint("attempt_count >= 0", name="chk_dlq_outbox_attempt_count"),
    )

    event_id: Mapped[UUID] = mapped_column(UUIDChar(), primary_key=True, default=uuid4)
    quarantine_id: Mapped[UUID] = mapped_column(UUIDChar(), ForeignKey("message_quarantine.id"), nullable=False)
    event_kind: Mapped[DlqOutboxEventKind] = mapped_column(
        Enum(DlqOutboxEventKind, native_enum=False, length=30),
        nullable=False,
        default=DlqOutboxEventKind.QUARANTINE_RECORDED,
    )
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False, default="1.0")
    original_schema_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[DlqOutboxEventStatus] = mapped_column(
        Enum(DlqOutboxEventStatus, native_enum=False, length=20),
        nullable=False,
        default=DlqOutboxEventStatus.PENDING,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = _now_timestamp_column()
    claim_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # message_quarantine.failure_code와 같은 이유로 _FAILURE_CODE_VALUES allowlist를 적용하지 않습니다 —
    # DLQ 발행 실패는 Job 실행 실패와 다른 도메인(발행·claim 자체의 실패)입니다.
    last_error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    quarantine: Mapped["MessageQuarantine"] = relationship(back_populates="dlq_outbox_event")
