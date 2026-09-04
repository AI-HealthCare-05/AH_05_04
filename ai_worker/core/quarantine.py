"""원문을 저장하지 않는 메시지 격리·DLQ 계약입니다."""

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from ai_worker.core.consumer_execution import Transaction
from ai_worker.core.stream import StreamAcknowledger

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TRACE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STREAM_ENTRY_ID_PATTERN = re.compile(r"^[0-9]+-[0-9]+$")


class QuarantineFailureCode(StrEnum):
    """메시지 자체의 검증 실패 코드입니다."""

    INVALID_MESSAGE_SCHEMA = "INVALID_MESSAGE_SCHEMA"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    EVENT_MISMATCH = "EVENT_MISMATCH"
    ATTEMPT_MISMATCH = "ATTEMPT_MISMATCH"


@dataclass(frozen=True, slots=True)
class QuarantineRequest:
    """DB 격리에 허용되는 최소 비민감 메타데이터입니다."""

    stream_name: str
    stream_entry_id: str
    message_digest: str
    failure_code: QuarantineFailureCode
    job_id: UUID | None
    original_event_id: UUID | None
    original_schema_version: str | None
    trace_id: str | None
    received_at: datetime

    def __post_init__(self) -> None:
        _validate_stream_name(self.stream_name)
        _validate_stream_entry_id(self.stream_entry_id)
        _validate_message_digest(self.message_digest)
        _validate_failure_code(self.failure_code)
        _validate_schema_version(self.original_schema_version)
        _validate_trace_id(self.trace_id)

        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("received_at은 timezone-aware datetime이어야 합니다.")


@dataclass(frozen=True, slots=True)
class RejectedWorkerDelivery:
    """역직렬화할 수 없는 Stream entry의 안전한 메타데이터입니다."""

    stream_name: str
    stream_entry_id: str
    message_digest: str
    failure_code: QuarantineFailureCode
    job_id: UUID | None
    original_event_id: UUID | None
    original_schema_version: str | None
    trace_id: str | None

    def __post_init__(self) -> None:
        _validate_stream_name(self.stream_name)
        _validate_stream_entry_id(self.stream_entry_id)
        _validate_message_digest(self.message_digest)
        _validate_failure_code(self.failure_code)
        _validate_schema_version(self.original_schema_version)
        _validate_trace_id(self.trace_id)

    def to_quarantine_request(
        self,
        *,
        received_at: datetime,
    ) -> QuarantineRequest:
        return QuarantineRequest(
            stream_name=self.stream_name,
            stream_entry_id=self.stream_entry_id,
            message_digest=self.message_digest,
            failure_code=self.failure_code,
            job_id=self.job_id,
            original_event_id=self.original_event_id,
            original_schema_version=self.original_schema_version,
            trace_id=self.trace_id,
            received_at=received_at,
        )


@dataclass(frozen=True, slots=True)
class QuarantineReceipt:
    """격리 row와 DLQ Outbox의 식별자입니다."""

    quarantine_id: UUID
    dlq_event_id: UUID


@dataclass(frozen=True, slots=True)
class DeadLetterEnvelope:
    """Dead-letter Stream에 발행할 비민감 envelope입니다."""

    event_id: UUID
    quarantine_id: UUID
    stream_entry_id: str
    message_digest: str
    failure_code: QuarantineFailureCode
    original_schema_version: str | None
    trace_id: str | None

    def __post_init__(self) -> None:
        _validate_stream_entry_id(self.stream_entry_id)
        _validate_message_digest(self.message_digest)
        _validate_failure_code(self.failure_code)
        _validate_schema_version(self.original_schema_version)
        _validate_trace_id(self.trace_id)


class QuarantineRepository(Protocol):
    async def record(
        self,
        request: QuarantineRequest,
    ) -> QuarantineReceipt:
        """격리 row와 DLQ Outbox를 같은 transaction에 저장합니다."""
        ...


class QuarantineExecution:
    """Quarantine과 DLQ Outbox를 commit한 뒤 원본 메시지를 ACK합니다."""

    def __init__(
        self,
        *,
        repository: QuarantineRepository,
        transaction: Transaction,
        acknowledger: StreamAcknowledger,
    ) -> None:
        self._repository = repository
        self._transaction = transaction
        self._acknowledger = acknowledger

    async def execute(
        self,
        request: QuarantineRequest,
    ) -> QuarantineReceipt:
        """Durable quarantine 저장이 완료된 경우에만 원본을 ACK합니다."""

        try:
            receipt = await self._repository.record(request)
            await self._transaction.commit()
        except BaseException:
            await self._rollback_safely()
            raise

        # commit 이후 ACK가 실패하면 DB 기록은 유지합니다.
        # 원본 메시지가 재전달될 때 repository의 unique 경계가
        # 같은 quarantine과 DLQ Outbox를 재사용합니다.
        await self._acknowledger.acknowledge(request.stream_entry_id)

        return receipt

    async def _rollback_safely(self) -> None:
        """rollback 실패가 원래 저장·commit 오류를 덮어쓰지 않게 합니다."""

        try:
            await self._transaction.rollback()
        except Exception:
            return


def _validate_stream_name(stream_name: str) -> None:
    if not stream_name.strip():
        raise ValueError("stream_name은 비어 있을 수 없습니다.")


def _validate_stream_entry_id(stream_entry_id: str) -> None:
    if _STREAM_ENTRY_ID_PATTERN.fullmatch(stream_entry_id) is None:
        raise ValueError("stream_entry_id 형식이 올바르지 않습니다.")


def _validate_message_digest(message_digest: str) -> None:
    if _SHA256_PATTERN.fullmatch(message_digest) is None:
        raise ValueError("message_digest는 64자리 lowercase SHA-256이어야 합니다.")


def _validate_failure_code(
    failure_code: QuarantineFailureCode,
) -> None:
    if not isinstance(failure_code, QuarantineFailureCode):
        raise TypeError("failure_code는 QuarantineFailureCode여야 합니다.")


def _validate_schema_version(
    original_schema_version: str | None,
) -> None:
    if original_schema_version is None:
        return

    if not original_schema_version.strip() or len(original_schema_version) > 20:
        raise ValueError("original_schema_version 형식이 올바르지 않습니다.")


def _validate_trace_id(trace_id: str | None) -> None:
    if trace_id is None:
        return

    if _TRACE_ID_PATTERN.fullmatch(trace_id) is None:
        raise ValueError("trace_id는 32자리 lowercase hexadecimal이어야 합니다.")
