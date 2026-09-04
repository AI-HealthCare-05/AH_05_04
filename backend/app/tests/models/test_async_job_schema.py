from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import get_args
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import CheckConstraint, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_worker.core.retry import ALL_FAILURE_CODES
from ai_worker.schemas.messages import DomainType as WorkerDomainType
from app.core.db.databases import Base
from app.models.async_jobs import (
    _FAILURE_CODE_VALUES,
    AiJob,
    AiJobFailureCode,
    AiJobStatus,
    AiJobType,
    DomainType,
    MessageQuarantine,
    OutboxEvent,
    OutboxEventKind,
    OutboxEventStatus,
)
from app.models.users import Gender, User
from app.tests.conftest import test_engine


def test_failure_code_allowlist_matches_worker_retry_contract() -> None:
    """ai_worker/core/retry.py의 ALL_FAILURE_CODES와 DB CHECK 제약의 allowlist가 어긋나면
    Worker가 기록한 failure_code를 DB가 거부할 수 있으므로 두 목록을 동기화된 상태로 고정합니다."""
    assert set(_FAILURE_CODE_VALUES) == set(ALL_FAILURE_CODES)


def test_ai_job_failure_code_literal_matches_check_constraint_values() -> None:
    """`AiJobFailureCode`(dtos/jobs.py의 `JobErrorData.code` 타입)가 DB CHECK 제약의
    allowlist와 어긋나면, OpenAPI가 실제로는 나올 수 없는 값을 문서화하거나 실제 나올 수 있는
    값을 누락하게 되므로 두 목록을 동기화된 상태로 고정합니다."""
    assert set(get_args(AiJobFailureCode)) == set(_FAILURE_CODE_VALUES)


def test_domain_type_matches_worker_message_schema() -> None:
    """`ai_worker/schemas/messages.py`의 `DomainType`과 이 값이 어긋나면, Backend가 접수 시점에
    저장한 `outbox_event.domain_type`을 Publisher가 `WorkerMessage`로 조립할 때 검증에서
    거부될 수 있으므로 두 enum을 동기화된 상태로 고정합니다."""
    assert {member.value for member in DomainType} == {member.value for member in WorkerDomainType}


def test_track_a_async_tables_are_registered() -> None:
    expected_tables = {
        "ai_job",
        "ai_job_attempt",
        "outbox_event",
        "idempotency_record",
        "message_quarantine",
        "dlq_outbox_event",
    }

    assert expected_tables.issubset(Base.metadata.tables)


def test_ai_job_contains_worker_lifecycle_columns() -> None:
    ai_job = Base.metadata.tables["ai_job"]

    expected_columns = {
        "id",
        "user_id",
        "job_type",
        "status",
        "prescription_version_id",
        "expected_event_id",
        "last_consumed_event_id",
        "attempt_count",
        "max_attempts",
        "available_at",
        "lease_token",
        "lease_expires_at",
        "heartbeat_at",
        "failure_code",
        "failure_detail",
        "dead_lettered_at",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    }

    assert expected_columns.issubset(set(ai_job.c.keys()))
    assert {
        "chk_ai_job_status",
        "chk_ai_job_processing_lease",
        "chk_ai_job_failure_code_values",
        "chk_ai_job_terminal_completed_at",
    }.issubset(constraint.name for constraint in ai_job.constraints if isinstance(constraint, CheckConstraint))
    # Outbox는 30일, ai_job은 90일 보존이라 Outbox가 먼저 삭제될 수 있습니다. 기본값(NO ACTION)이면
    # Outbox 정리 배치가 FK 위반으로 실패하므로 두 FK는 ON DELETE SET NULL이어야 합니다.
    for column_name in ("expected_event_id", "last_consumed_event_id"):
        fk = next(iter(ai_job.c[column_name].foreign_keys))
        assert fk.ondelete == "SET NULL"


def test_ai_job_attempt_contains_execution_history_columns() -> None:
    ai_job_attempt = Base.metadata.tables["ai_job_attempt"]

    expected_columns = {
        "id",
        "ai_job_id",
        "attempt_no",
        "attempt_status",
        "runtime_metadata",
        "error_code",
        "error_message",
        "retryable",
        "timed_out",
        "started_at",
        "completed_at",
    }

    assert expected_columns.issubset(set(ai_job_attempt.c.keys()))
    assert "uq_ai_job_attempt_no" in {
        constraint.name for constraint in ai_job_attempt.constraints if isinstance(constraint, UniqueConstraint)
    }
    assert "chk_ai_job_attempt_error_code_values" in {
        constraint.name for constraint in ai_job_attempt.constraints if isinstance(constraint, CheckConstraint)
    }


def test_outbox_event_contains_publish_and_claim_columns() -> None:
    outbox_event = Base.metadata.tables["outbox_event"]

    expected_columns = {
        "event_id",
        "job_id",
        "attempt",
        "event_kind",
        "schema_version",
        "status",
        "available_at",
        "claim_token",
        "claim_expires_at",
        "published_at",
        "trace_id",
        "domain_type",
        "domain_id",
        "created_at",
        "updated_at",
    }

    assert expected_columns.issubset(set(outbox_event.c.keys()))
    assert "uq_outbox_event_job_attempt_kind" in {
        constraint.name for constraint in outbox_event.constraints if isinstance(constraint, UniqueConstraint)
    }


def test_idempotency_record_enforces_single_table_record_types() -> None:
    idempotency_record = Base.metadata.tables["idempotency_record"]

    expected_columns = {
        "id",
        "user_id",
        "operation_id",
        "key_hmac_version",
        "key_hmac",
        "request_hash",
        "record_type",
        "job_id",
        "parent_resource_id",
        "response_status",
        "response_body_snapshot",
        "encryption_key_version",
        "expires_at",
        "created_at",
        "updated_at",
    }

    assert expected_columns.issubset(set(idempotency_record.c.keys()))
    # BYTEA snapshot 계약(idempotency-v1.md)을 코드에서도 고정합니다.
    assert idempotency_record.c.response_body_snapshot.type.python_type is bytes
    # ASYNC_JOB의 parent_resource_id는 항상 NULL이라 하나의 UNIQUE 제약으로는 중복 접수를
    # 막지 못하므로, record_type별 scope를 가진 두 partial unique index로 나뉘어 있어야 합니다.
    assert {"uq_idempotency_async_scope", "uq_idempotency_sync_scope"}.issubset(
        index.name for index in idempotency_record.indexes if index.unique
    )
    assert "uq_idempotency_job" in {
        constraint.name for constraint in idempotency_record.constraints if isinstance(constraint, UniqueConstraint)
    }
    assert "chk_idempotency_record_payload" in {
        constraint.name for constraint in idempotency_record.constraints if isinstance(constraint, CheckConstraint)
    }


def test_quarantine_and_dlq_tables_keep_poison_message_metadata_only() -> None:
    message_quarantine = Base.metadata.tables["message_quarantine"]
    dlq_outbox_event = Base.metadata.tables["dlq_outbox_event"]

    assert set(message_quarantine.c.keys()) == {
        "id",
        "stream_name",
        "stream_entry_id",
        "message_digest",
        "job_id",
        "original_event_id",
        "failure_code",
        "failure_detail",
        "original_schema_version",
        "trace_id",
        "received_at",
        "created_at",
    }
    assert set(dlq_outbox_event.c.keys()) == {
        "event_id",
        "quarantine_id",
        "event_kind",
        "schema_version",
        "original_schema_version",
        "status",
        "attempt_count",
        "available_at",
        "claim_token",
        "claim_expires_at",
        "last_error_code",
        "published_at",
        "created_at",
        "updated_at",
    }
    assert not message_quarantine.c.job_id.foreign_keys


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()


async def test_deleting_outbox_event_nulls_out_ai_job_pointer_instead_of_failing(
    db_session: AsyncSession,
) -> None:
    """ai_job -> outbox_event FK가 실제로 ON DELETE SET NULL로 동작하는지 real Postgres에서 검증합니다.

    Outbox는 30일, ai_job은 90일 보존이라 Outbox가 먼저 삭제될 수 있습니다. 기본값(NO ACTION)이면
    이 삭제가 FK 위반으로 실패합니다.
    """
    token = uuid4().hex
    user = User(
        email=f"async-job-fk-{token[:12]}@example.com",
        hashed_password="hashed-password",
        name="합성 사용자",
        gender=Gender.MALE,
        birthday=datetime(1990, 1, 1, tzinfo=UTC).date(),
        phone_number=f"010{uuid4().int % 100_000_000:08d}",
    )
    db_session.add(user)
    await db_session.flush()

    job = AiJob(
        user_id=user.id,
        job_type=AiJobType.OCR,
        status=AiJobStatus.PENDING,
        max_attempts=3,
        available_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    event = OutboxEvent(
        job_id=job.id,
        attempt=1,
        event_kind=OutboxEventKind.JOB_EXECUTE,
        status=OutboxEventStatus.PUBLISHED,
        available_at=datetime.now(UTC),
        published_at=datetime.now(UTC) - timedelta(days=31),
    )
    db_session.add(event)
    await db_session.flush()

    job.expected_event_id = event.event_id
    job.last_consumed_event_id = event.event_id
    await db_session.flush()

    job_id = job.id

    # Outbox 30일 보존 정리 배치가 하는 것과 동일한 작업: 오래된 발행 완료 이벤트를 삭제합니다.
    await db_session.delete(event)
    await db_session.flush()

    # ON DELETE SET NULL은 Postgres가 직접 실행하므로, 세션이 이미 들고 있는 job 객체를
    # expire 없이 다시 읽으면 flush 전 Python 값이 그대로 남아 거짓 통과할 수 있습니다.
    db_session.expire(job)
    refreshed = await db_session.scalar(select(AiJob).where(AiJob.id == job_id))
    assert refreshed is not None
    assert refreshed.expected_event_id is None
    assert refreshed.last_consumed_event_id is None


async def test_message_quarantine_job_id_accepts_reference_to_missing_ai_job(
    db_session: AsyncSession,
) -> None:
    """outbox-stream-v1.md "재시도와 격리": job_id가 파싱되지만 DB에 그 Job이 없는 메시지도
    quarantine에 반드시 commit되어야 poison message가 무한 재전달되지 않습니다. `job_id`에 FK가
    걸리면 이 INSERT 자체가 FK 위반으로 실패하므로, 존재하지 않는 `ai_job.id`를 참조해도 실제로
    commit되는지 real Postgres에서 확인합니다.
    """
    quarantine = MessageQuarantine(
        stream_name="guide-jobs",
        stream_entry_id=f"{uuid4().hex}-0",
        message_digest=uuid4().hex,
        job_id=uuid4(),
        failure_code="UNSUPPORTED_SCHEMA",
    )
    db_session.add(quarantine)
    await db_session.flush()

    quarantine_id = quarantine.id
    expected_job_id = quarantine.job_id

    db_session.expire(quarantine)
    persisted = await db_session.scalar(select(MessageQuarantine).where(MessageQuarantine.id == quarantine_id))
    assert persisted is not None
    assert persisted.job_id == expected_job_id
